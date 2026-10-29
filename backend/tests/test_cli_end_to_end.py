"""The cut slice runs through the public CLI against in-process inference."""

import io
import json
import struct
import subprocess

from fake_comfy import FakeComfy
from PIL import Image

from gaiavfx.direction_cli import main
from gaiavfx.infrastructure.database import Database
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.direction.persistence import EvidenceRecord


def png(color):
    data = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(data, "PNG")
    return data.getvalue()


def context():
    header = json.dumps(
        {
            "__metadata__": {"format": "h3_motion_context_av_v1"},
            "video": {"shape": [1, 1, 1, 1, 1]},
            "audio": {"shape": [1, 1, 1, 1]},
        }
    ).encode()
    return struct.pack("<Q", len(header)) + header


def test_cut_cli_on_fake(tmp_path, capsys):
    media = MediaTools()
    video = tmp_path / "survey.mp4"
    subprocess.run(
        [
            str(media.ffmpeg),
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=32x32:rate=24:duration=5.2",
            "-c:v",
            "libx264",
            str(video),
        ],
        check=True,
    )

    def outputs(graph):
        classes = {node["class_type"] for node in graph.values()}
        if "TextEncodeQwenImageEditPlus" in classes:
            seed = graph["sample"]["inputs"]["seed"]
            return {"save": {"images": [{"filename": "edit.png", "type": "output"}]}}, {
                "save:images:0": png((seed, 40, 60))
            }
        # The shape ComfyUI 0.35's SaveVideo reports: the mp4 under `images`, marked animated.
        saved = {"images": [{"filename": "take.mp4", "type": "output"}], "animated": [True]}
        return {"save_video": saved}, {
            "save_video:images:0": video.read_bytes(),
            "context": context(),
        }

    fake = FakeComfy(outputs, polls_to_complete=1)

    def cli(*args, ok=True):
        code = main(["--library", str(tmp_path / "library"), *map(str, args)], comfy=fake)
        out = capsys.readouterr()
        assert (code == 0) is ok, out.err
        return json.loads(out.out if ok else out.err)

    cli("library", "create", "--name", "Slice")
    movie = cli("movie", "create", "--title", "Courtyard")
    assets = []
    for index in range(3):
        source = tmp_path / f"source{index}.png"
        source.write_bytes(png((index * 40, 20, 100)))
        assets.append(cli("asset", "import", source)["id"])
    latent = tmp_path / "context.safetensors"
    latent.write_bytes(context())
    artifact = cli("artifact", "import", latent)["id"]
    place = cli("place", "add", "--name", "Courtyard")["id"]
    gate, along, reverse = [
        cli("angle", "add", "--place-id", place, "--name", name)["id"]
        for name in ("Gate", "Along", "Reverse")
    ]
    early_segment = tmp_path / "early-segment.json"
    early_segment.write_text(
        json.dumps(
            {
                "subjects": [
                    {
                        "id": "arin",
                        "name": "Arin",
                        "description": "a walker",
                        "keeps": ["grey coat"],
                        "identity_asset": assets[0],
                    }
                ],
                "place": {
                    "noun": "courtyard",
                    "description": "A quiet courtyard.",
                    "landmarks": "rail",
                },
                "shots": [
                    {"description": "Walk.", "screen_direction": "forward", "sound": "steps"},
                    {"description": "Turn.", "screen_direction": "left", "sound": "steps"},
                ],
                "cut_frames": [36],
                "delivered_frames": 102,
                "continues": True,
            }
        )
    )
    stored_segment = cli(
        "segment",
        "add",
        "--movie-id",
        movie["id"],
        "--expected-revision",
        movie["revision"],
        "--file",
        early_segment,
    )
    cut = cli(
        "cut",
        "add",
        "--segment-id",
        stored_segment["id"],
        "--expected-revision",
        stored_segment["revision"],
        "--frame",
        36,
        "--from-angle-id",
        gate,
        "--to-angle-id",
        reverse,
        "--relation",
        "reverse",
        "--crosses-barrier",
        "--receives-predecessor",
    )["id"]
    assert (
        "cut_not_ready: Cut "
        in cli(
            "render",
            "start",
            "--cut-id",
            cut,
            "--seeds",
            "1",
            "--context-artifact",
            artifact,
            ok=False,
        )["detail"]
    )
    assert [item["action"] for item in cli("cut", "inspect", cut)["recommended"]["actions"]] == [
        "view_angle",
        "insert_subject",
        "view_place",
    ]
    plan = tmp_path / "survey.json"
    plan.write_text(
        json.dumps(
            {
                "what": "an empty courtyard",
                "sound": "wind",
                "first_view": "gate",
                "first_angle_id": gate,
                "first_frame_asset": assets[1],
                "views": [[along, "along the wall"], [reverse, "reverse view"]],
                "overhead": "straight down",
            }
        )
    )
    survey = cli("survey", "start", "--place-id", place, "--plan", plan, "--seed", 1)
    cli("run", "--endpoint", "http://fake", "--until-idle", "--poll-seconds", 0)
    saved = cli("operation", "show", survey["id"])
    assert saved["state"] == "succeeded" and len(saved["candidates"]) == 6
    assert cli("cut", "inspect", cut)["recommended"]["actions"][0]["action"] == "view_angle"
    review = cli("survey", "review", survey["id"])
    assert len(review["members"]) == 3
    assert cli("survey", "review", survey["id"])["members"] == review["members"]
    assert cli("cut", "inspect", cut)["recommended"]["actions"][0]["action"] == "review_view"
    extracted = {item["output_key"]: item for item in saved["candidates"]}
    assert "detail" in cli("views", "approve", "--candidate", extracted["overhead"]["id"], ok=False)
    along_evidence = cli("views", "approve", "--candidate", extracted[along]["id"])
    target = cli("views", "approve", "--candidate", extracted[reverse]["id"])
    assert cli("cut", "inspect", cut)["recommended"]["actions"][0]["action"] == "insert_subject"
    request = tmp_path / "insert.json"
    request.write_text(
        json.dumps(
            {
                "who": "Arin",
                "looks": "grey coat",
                "pronouns": "he",
                "facing": "screen_left",
                "plate": "empty courtyard",
                "keep": "walls",
                "where": "by rail",
                "size": "one third",
                "distance_description": "mid-distance",
                "along": "the rail",
                "barrier": "the rail",
                "feet_placement": "Both feet on stone.",
                "occlude": "Do not occlude.",
            }
        )
    )
    assert "detail" in cli(
        "insert",
        "start",
        "--cut-id",
        cut,
        "--evidence-id",
        along_evidence["id"],
        "--subject-asset",
        assets[0],
        "--request",
        request,
        "--seeds",
        "2",
        ok=False,
    )
    insertion = cli(
        "insert",
        "start",
        "--cut-id",
        cut,
        "--evidence-id",
        target["id"],
        "--subject-asset",
        assets[0],
        "--request",
        request,
        "--seeds",
        "2,3",
    )
    cli("run", "--endpoint", "http://fake", "--until-idle", "--poll-seconds", 0)
    pins = cli("insert", "review", insertion["id"])
    assert len(pins["members"]) == 2
    first = pins["members"][0]["candidate_id"]
    cli("keyframe", "approve-candidate", "--candidate", first)
    assert (
        "already approved"
        in cli("keyframe", "approve-candidate", "--candidate", first, ok=False)["detail"]
    )
    assert "detail" in cli(
        "keyframe", "approve-candidate", "--candidate", pins["members"][1]["candidate_id"], ok=False
    )
    assert cli("cut", "inspect", cut)["recommended"]["ready"]
    sheet = cli(
        "sheet",
        "assemble",
        "--place-id",
        place,
        "--tiles",
        along_evidence["id"] + "," + target["id"],
        "--blank",
        "2:32x32",
        "--origin",
        "survey",
    )
    before = len(fake.posts)
    cli("run", "--endpoint", "http://fake", "--until-idle", "--poll-seconds", 0)
    assert len(fake.posts) == before
    sheet_asset = cli("operation", "show", sheet["id"])["candidates"][0]["asset_id"]
    segment = tmp_path / "segment.json"
    segment.write_text(
        json.dumps(
            {
                "subjects": [
                    {
                        "id": "arin",
                        "name": "Arin",
                        "description": "a walker",
                        "keeps": ["grey coat"],
                        "identity_asset": assets[0],
                    }
                ],
                "place": {
                    "noun": "courtyard",
                    "description": "A quiet courtyard.",
                    "landmarks": "rail and gate",
                },
                "shots": [
                    {
                        "description": "Walk forward.",
                        "screen_direction": "forward",
                        "sound": "steps",
                    },
                    {"description": "Turn around.", "screen_direction": "left", "sound": "steps"},
                ],
                "cut_frames": [36],
                "delivered_frames": 102,
                "continues": True,
            }
        )
    )
    assert "detail" in cli(
        "render",
        "start",
        "--cut-id",
        cut,
        "--seeds",
        "1",
        "--sheet",
        sheet_asset,
        ok=False,
    )
    db = Database(tmp_path / "library")
    try:
        with db.transaction() as session:
            session.get(EvidenceRecord, along_evidence["id"]).approved = False
        assert (
            "stale"
            in cli(
                "render",
                "start",
                "--cut-id",
                cut,
                "--seeds",
                "1",
                "--sheet",
                sheet_asset,
                "--context-artifact",
                artifact,
                ok=False,
            )["detail"]
        )
        with db.transaction() as session:
            session.get(EvidenceRecord, along_evidence["id"]).approved = True
    finally:
        db.close()
    implicit_sheet = cli(
        "render",
        "start",
        "--cut-id",
        cut,
        "--seeds",
        "1",
        "--context-artifact",
        artifact,
    )
    assert implicit_sheet["request"]["sheet_asset"] == sheet_asset
    text_segment = cli(
        "segment",
        "add",
        "--movie-id",
        movie["id"],
        "--expected-revision",
        cli("movie", "show", movie["id"])["revision"],
        "--file",
        segment,
    )
    assert text_segment["subject_receipts"] == []
    assert len(cli("subject", "list", movie["id"])) == 1
    text_cut = cli(
        "cut",
        "add",
        "--segment-id",
        text_segment["id"],
        "--expected-revision",
        text_segment["revision"],
        "--frame",
        36,
        "--from-angle-id",
        gate,
        "--to-angle-id",
        along,
        "--relation",
        "same_direction",
        "--no-crosses-barrier",
        "--receives-predecessor",
    )["id"]
    assert cli("render", "start", "--cut-id", text_cut, "--seeds", 4, ok=False)["detail"] == (
        "continuation_source: Select a take of segment 1 first."
    )
    wrong = tmp_path / "wrong-cut.json"
    wrong.write_text(segment.read_text().replace('"cut_frames": [36]', '"cut_frames": [48]'))
    wrong_segment = cli(
        "segment",
        "add",
        "--movie-id",
        movie["id"],
        "--expected-revision",
        cli("movie", "show", movie["id"])["revision"],
        "--file",
        wrong,
    )
    wrong_cut = cli(
        "cut",
        "add",
        "--segment-id",
        wrong_segment["id"],
        "--expected-revision",
        1,
        "--frame",
        36,
        "--from-angle-id",
        gate,
        "--to-angle-id",
        along,
        "--relation",
        "same_direction",
        "--no-crosses-barrier",
        "--receives-predecessor",
    )
    error = cli(
        "render",
        "start",
        "--cut-id",
        wrong_cut["id"],
        "--seeds",
        1,
        "--context-artifact",
        artifact,
        ok=False,
    )["detail"]
    assert error == "structure: Cut frames without phases: [36]"
    assert "detail" in cli(
        "render",
        "start",
        "--cut-id",
        cut,
        "--seeds",
        "1,1",
        "--sheet",
        sheet_asset,
        "--context-artifact",
        artifact,
        ok=False,
    )
    render = cli(
        "render",
        "start",
        "--cut-id",
        cut,
        "--seeds",
        "1,2,3",
        "--sheet",
        sheet_asset,
        "--context-artifact",
        artifact,
    )
    assert len(render["jobs"]) == 3
    assert {job["seed"] for job in render["jobs"]} == {1, 2, 3}
    for job in render["jobs"]:
        graph = job["graph"]
        assert {"picture_0", "picture_1", "keyframe_0", "save_context"} <= graph.keys()
        assert graph["keyframe_0"]["inputs"]["frame_idx"] == 36 + 22 + 12
        prefix = job["snapshot"]["output_prefix"]
        assert graph["load_context"]["inputs"]["latent_path"] == f"{prefix}/context.safetensors"
        assert graph["save_context"]["inputs"]["filename_prefix"] == f"{prefix}/context"
    cli("run", "--endpoint", "http://fake", "--until-idle", "--poll-seconds", 0)
    saved = cli("operation", "show", render["id"])
    assert saved["state"] == "succeeded" and all(
        item["artifact_id"] for item in saved["candidates"]
    )
    takes = cli("render", "review", render["id"])
    assert len(takes["members"]) == 3
    assert "detail" in cli("review", "reveal", takes["id"], ok=False)
    cli("review", "rank", takes["id"], "--tiers", "B|A,C", "--usable", 1, "--expected-revision", 0)
    assert len(cli("review", "reveal", takes["id"])["members"]) == 3
    selected = cli(
        "take", "select", "--review-set", takes["id"], "--letter", "B", "--expected-revision", 0
    )
    assert selected["authority"] == "user"

    plan = cli("cut", "inspect", text_cut)
    assert plan["recommended"]["technique_id"] == "text" and plan["recommended"]["ready"]
    assert "detail" in cli(
        "render",
        "start",
        "--cut-id",
        text_cut,
        "--seeds",
        "4",
        "--sheet",
        sheet_asset,
        "--context-artifact",
        artifact,
        ok=False,
    )
    text_render = cli(
        "render",
        "start",
        "--cut-id",
        text_cut,
        "--seeds",
        "4",
    )
    graph = text_render["jobs"][0]["graph"]
    assert "picture_0" in graph and "picture_1" not in graph and "keyframe_0" not in graph
    assert "load_context" in graph

    assert text_render["request"]["context_source"] == {
        "kind": "selection",
        "segment_id": stored_segment["id"],
        "selection_id": selected["id"],
        "candidate_id": selected["candidate_id"],
        "authority": "user",
        "basis": None,  # a user's own selection has no provisional basis (S7b)
    }
    selected_candidate = next(c for c in saved["candidates"] if c["id"] == selected["candidate_id"])
    assert text_render["request"]["context_artifact"] == selected_candidate["artifact_id"]
    changed = cli("subject", "set", "arin", "--expected-revision", 1, "--keeps", "blue coat")
    for row in (stored_segment, text_segment):
        assert cli("segment", "assembled", row["id"])["subjects"][0]["keeps"] == ["blue coat"]
    assert (
        "appears in segment"
        in cli(
            "subject",
            "remove",
            "arin",
            "--expected-revision",
            cli("movie", "show", movie["id"])["revision"],
            ok=False,
        )["detail"]
    )
    cli("undo", changed["receipt"]["id"])
    assert cli("segment", "assembled", text_segment["id"])["subjects"][0]["keeps"] == ["grey coat"]
    phase_file = tmp_path / "phase.json"
    phase_file.write_text(json.dumps({"description": "Stop.", "screen_direction": "", "sound": ""}))
    more = cli(
        "phase",
        "add",
        "--segment-id",
        text_segment["id"],
        "--expected-revision",
        2,
        "--start-frame",
        72,
        "--content",
        phase_file,
    )
    cli(
        "cut",
        "add",
        "--segment-id",
        text_segment["id"],
        "--expected-revision",
        more["receipt"]["after"]["revision"],
        "--frame",
        72,
        "--from-angle-id",
        along,
        "--to-angle-id",
        gate,
        "--relation",
        "same_direction",
        "--no-crosses-barrier",
        "--override-technique",
        "text",
    )
    multi = cli("render", "start", "--cut-id", text_cut, "--seeds", 4)
    assert multi["request"]["segment_id"] == text_segment["id"]
    assert len(multi["request"]["cut_ids"]) == 2
    assert len(multi["jobs"]) == 1
