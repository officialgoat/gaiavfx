"""Target evidence, CPU preparation and read-side classification; no inference."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from PIL import Image
from test_recast_service import edit
from test_recast_service import recast_template as recast_template  # noqa: PLC0414
from test_recast_service import world as world  # noqa: PLC0414

from gaiavfx.application import recast
from gaiavfx.application import recast_projection as p
from gaiavfx.application.operation_steps import LocalContext
from gaiavfx.application.target_faces import (
    SCAN_VERSION,
    start_target_report,
    target_normalize,
    target_report,
)
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.direction.replacement import parse_replacement_policy
from gaiavfx.modules.direction.target_face import classify_target_faces
from gaiavfx.shared.errors import ApplicationError

EVIDENCE = "backend/tests/fixtures/replacement/target-faces-9oct.json"


def evidence():
    return json.loads((Path(__file__).parents[2] / EVIDENCE).read_text())["targets"]


def test_policy_reproduces_four_picture_evidence():
    policy = parse_replacement_policy(replacement_policy_text()).target_face
    assert policy.evidence == EVIDENCE
    for target in evidence().values():
        rows = [(score, (0, 0, height, height)) for score, height in target["faces"]]
        result = classify_target_faces(reversed(rows), policy)
        assert result["state"] == ("face" if target["label"].startswith("human") else "no_face")
        assert result["score"] == target["faces"][0][0]
        assert result["height"] == target["faces"][0][1]
    assert classify_target_faces([(0.7, (0, 0, 5, 5))], policy)["state"] == "face"


@pytest.mark.parametrize("score", [0, -1, 1.1, float("nan"), float("inf"), True])
def test_policy_rejects_invalid_threshold(score):
    with pytest.raises(ValueError):
        replace(p.policy().target_face, min_det_score=score)


@pytest.mark.parametrize(
    "change",
    [
        'evidence = ""',
        'evidence = "../outside.json"',
        "min_det_score = 0",
        "min_det_score = 1.1",
        "extra = 1",
    ],
)
def test_policy_parser_rejects_invalid_target_table(change):
    text = replacement_policy_text().split("[target_face]")[0]
    fields = {"min_det_score": "0.7", "evidence": f'"{EVIDENCE}"', "note": '"measured"'}
    key, value = change.split("=", 1)
    fields[key.strip()] = value.strip()
    with pytest.raises(ValueError):
        parse_replacement_policy(
            text + "[target_face]\n" + "\n".join(f"{k} = {v}" for k, v in fields.items())
        )
    del fields["evidence"]
    with pytest.raises(ValueError):
        parse_replacement_policy(
            text + "[target_face]\n" + "\n".join(f"{k} = {v}" for k, v in fields.items())
        )


def test_report_operation_is_idempotent_and_unreserved(world):
    assert world.invoke(target_report, "picture") is None
    op = world.invoke(start_target_report, "picture")
    assert op["kind"] == "target_face" and op["budget_reserved"] == 0
    assert op["awaits_review"] is False
    assert op["request"] == {"target_asset": "picture", "scan_version": SCAN_VERSION, "attempt": 1}
    assert len(op["request_key"]) == 12
    assert len(op["jobs"]) == 2
    local, comfy = op["jobs"]
    assert local["step"] == "target_normalize"
    assert local["inputs"] == {"picture": {"sha256": "picture"}}
    assert comfy["executor"] == "comfy" and comfy["kind"] == "analysis"
    assert comfy["depends_on"] == 0
    node = comfy["graph"]["faces"]
    assert node["class_type"] == "GaiaFaceScan" and node["inputs"]["frames_json"] == "[0]"
    assert comfy["inputs"]["target"] == {
        "source": "job_artifact",
        "output_key": "normalized",
        "remote_name": node["inputs"]["video"],
    }
    assert world.invoke(start_target_report, "picture")["id"] == op["id"]
    assert world.invoke(start_target_report, "picture2")["id"] != op["id"]
    assert world.invoke(target_report, "picture") == {"state": "checking"}


def test_normalize_exif_rotation_aligns_boxes(world, tmp_path):
    original = tmp_path / "source.png"
    image = Image.new("RGBA", (1024, 768))
    exif = Image.Exif()
    exif[274] = 6
    image.save(original, exif=exif)
    outputs = target_normalize(
        LocalContext(None, tmp_path / "staging", {}, {"picture": original}, {})
    )
    key, path, kind = outputs[0]
    assert (key, kind) == ("normalized", "visual_evidence")
    with Image.open(path) as normalized:
        assert normalized.format == "PNG" and normalized.mode == "RGB"
        assert normalized.size == (768, 1024)
        assert normalized.getexif().get(274) is None
        for target in evidence().values():
            width, height = target["size"]
            for _, face_height in target["faces"]:
                box = (
                    0,
                    0,
                    face_height * normalized.width / width,
                    face_height * normalized.height / height,
                )
                assert 0 <= box[0] < box[2] <= normalized.width
                assert 0 <= box[1] < box[3] <= normalized.height
    target = evidence()["CS010.png"]
    score, height = target["faces"][0]
    box = [0, 0, height * 768 / target["size"][0], height * 1024 / target["size"][1]]
    world.settle_target(scores=(score,), boxes=[box])
    with world.db.transaction() as session:
        operation = world.ops.recent(session, kind="target_face")[0]
    world.blobs[operation["jobs"][0]["id"]] = path.read_bytes()
    result = world.invoke(target_report, "picture")
    assert result["size"] == [768, 1024] and result["box"] == box
    assert result["height"] == box[3] and result["state"] == "face"


@pytest.mark.parametrize(
    "scores,state", [((0.881, 0.8), "face"), ((0.525, 0.331), "no_face"), ((), "no_face")]
)
def test_report_reads_best_face_and_caches(world, monkeypatch, scores, state):
    world.settle_target(scores=scores)
    report = world.invoke(target_report, "picture")
    assert report["state"] == state and report["size"] == [768, 1024]
    assert report["box"] == ([0, 0, 97, 97] if scores else None)
    assert report["score"] == (max(scores) if scores else None)
    monkeypatch.setattr(
        world.library, "read_blob", lambda *args: pytest.fail("cached report reread")
    )
    report["size"][0] = 1
    assert world.invoke(target_report, "picture")["size"] == [768, 1024]


@pytest.mark.parametrize(
    "options",
    [
        {"state": "failed"},
        {"state": "cancelled"},
        {"raw": b"invalid npz"},
        {"scores": (float("nan"),)},
        {"boxes": [[0, 0, -1, 97]]},
    ],
)
def test_report_failures(world, options):
    world.settle_target(**options)
    assert world.invoke(target_report, "picture")["state"] == "failed"


def test_mapping_proposal_confirmation_and_other_recasts_share_report(world):
    first = world.create()
    cid = first["characters"][0]["id"]
    first = edit(world, first, recast.propose_target, cid, asset_id="picture", label="Proposal")
    assert first["characters"][0]["target"]["face"] == "checking"
    first = edit(world, first, recast.confirm_target, cid)
    first = edit(world, first, recast.set_target, cid, asset_id="picture", label="Renamed")
    second = world.create("second")
    edit(
        world,
        second,
        recast.set_target,
        second["characters"][0]["id"],
        asset_id="picture",
        label="Shared",
    )
    with world.db.transaction() as session:
        assert len(world.ops.recent(session, kind="target_face")) == 1
        stored = p.store.load(session, first["id"])
        assert "face" not in stored["characters"][0]
    world.settle_target()
    assert (
        world.invoke(recast.recast_state, first["id"])["characters"][0]["target"]["face"] == "face"
    )


def test_old_policy_without_target_face_is_safe(world, monkeypatch):
    from gaiavfx.application import target_faces

    text = replacement_policy_text().split("[target_face]")[0]
    assert parse_replacement_policy(text).target_face is None
    monkeypatch.setattr(target_faces, "replacement_policy_text", lambda: text)
    with pytest.raises(ApplicationError, match="requires a target_face policy"):
        world.invoke(start_target_report, "picture")
    assert world.invoke(target_report, "picture")["state"] == "failed"
    with world.db.transaction() as session:
        assert world.ops.recent(session, kind="target_face") == []


@pytest.mark.parametrize(
    "error", [OSError("temporarily unreadable"), ApplicationError("temporarily missing", 404)]
)
def test_transient_read_failure_is_not_cached(world, monkeypatch, error):
    from gaiavfx.application import target_faces

    world.settle_target()
    original = world.library.read_blob

    def unavailable(*args):
        raise error

    monkeypatch.setattr(world.library, "read_blob", unavailable)
    assert world.invoke(target_report, "picture")["state"] == "failed"
    assert not target_faces._CACHES[world.library]
    monkeypatch.setattr(world.library, "read_blob", original)
    assert world.invoke(target_report, "picture")["state"] == "face"


def test_invalid_report_is_cached_but_can_be_retried_explicitly(world, monkeypatch):
    world.settle_target(raw=b"broken npz")
    assert world.invoke(target_report, "picture")["state"] == "failed"
    with monkeypatch.context() as patch:
        patch.setattr(
            world.library, "read_blob", lambda *args: pytest.fail("invalid report reparsed")
        )
        assert world.invoke(target_report, "picture")["state"] == "failed"
    original = world.invoke(start_target_report, "picture")
    retry = world.invoke(start_target_report, "picture", retry_failed=True)
    assert retry["id"] != original["id"] and retry["request"]["attempt"] == 2
    assert world.invoke(target_report, "picture")["state"] == "checking"
    assert world.invoke(start_target_report, "picture", retry_failed=True)["id"] == retry["id"]
    world.settle_target()
    assert world.invoke(target_report, "picture")["state"] == "face"


@pytest.mark.parametrize("command", [recast.set_target, recast.confirm_target])
def test_user_mapping_retries_failed_report(world, command):
    state = world.create()
    cid = state["characters"][0]["id"]
    state = edit(world, state, recast.propose_target, cid, asset_id="picture", label="Proposal")
    world.settle_target(state="failed")
    kwargs = {"asset_id": "picture", "label": "Target"} if command == recast.set_target else {}
    state = edit(world, state, command, cid, **kwargs)
    assert state["characters"][0]["target"]["face"] == "checking"
    assert world.invoke(start_target_report, "picture")["request"]["attempt"] == 2


def test_agent_proposal_never_retries_failed_report(world):
    state = world.create()
    cid = state["characters"][0]["id"]
    state = edit(world, state, recast.propose_target, cid, asset_id="picture", label="Proposal")
    world.settle_target(state="failed")
    state = edit(world, state, recast.propose_target, cid, asset_id="picture", label="Renamed")
    assert state["characters"][0]["target"]["face"] == "failed"
    assert world.invoke(start_target_report, "picture")["request"]["attempt"] == 1


def test_portrait_crop_and_sheet_pixels(tmp_path):
    from gaiavfx.application.operation_steps import portrait_crop, portrait_sheet
    from gaiavfx.integrations.minimax_h3.sheet import portrait_sheet_layout

    source = tmp_path / "target.png"
    image = Image.new("RGB", (1024, 768), "red")
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    crop = portrait_crop(
        LocalContext(
            None,
            tmp_path / "crop",
            {},
            {"target": source},
            {"step_spec": {"target": "target", "box": [0, 0, 200, 300]}},
        )
    )
    assert crop[0][::2] == ("crop", "visual_evidence")
    with Image.open(crop[0][1]) as result:
        assert result.size == (512, 512) and result.getpixel((0, 0)) == (196, 196, 196)
    paths = {"target": source}
    colors = {"front": "green", "three_quarter": "blue", "profile": "yellow"}
    for view, color in colors.items():
        paths[view] = tmp_path / f"{view}.png"
        Image.new("RGB", (1024, 1024), color).save(paths[view])
    spec = dict(zip(paths, paths)) | {"width": 768, "height": 1024, "gap": 6, "gap_rgb": [196] * 3}
    output = portrait_sheet(LocalContext(None, tmp_path / "sheet", {}, paths, {"step_spec": spec}))
    assert output[0][::2] == ("sheet", "image")
    with Image.open(output[0][1]) as result:
        assert result.size == (768, 1024)
        for box, color in zip(
            portrait_sheet_layout((768, 1024), 768, 1024, 6), ("red", *colors.values()), strict=True
        ):
            x, y, w, h = box
            assert result.getpixel((x + w // 2, y + h // 2)) == Image.new(
                "RGB", (1, 1), color
            ).getpixel((0, 0))
        assert result.getpixel((258, 0)) == (196, 196, 196)
    assert image.size == (1024, 768)  # original retained


def test_portrait_operation_contract(world):
    from gaiavfx.application.target_faces import start_face_portraits
    from gaiavfx.integrations.qwen_image_edit.prompt import compile_portrait
    from gaiavfx.modules.direction.portrait import VIEWS, Portrait

    operation = world.invoke(
        start_face_portraits, "picture", [0, 0, 97, 97], seed=271828, request_key="portraits"
    )
    assert operation["kind"] == "face_portraits"
    assert operation["budget_reserved"] == 3 and not operation["awaits_review"]
    crop, *jobs = operation["jobs"]
    assert crop["step"] == "portrait_crop" and crop["executor"] == "local"
    assert [j["seed"] for j in jobs] == [271828, 271829, 271830]
    for job, view in zip(jobs, VIEWS, strict=True):
        assert job["depends_on"] == 0 and job["executor"] == "comfy"
        assert job["snapshot"]["expect"] == {"kind": "image", "width": 1024, "height": 1024}
        assert "wording.toml" in job["snapshot"]["knowledge"]
        assert job["snapshot"]["request"] == {
            "target_asset": "picture",
            "face_asset": "crop",
            "view": view,
        }
        assert job["graph"]["positive"]["inputs"]["prompt"] == compile_portrait(
            Portrait("picture", "crop", view)
        )
        assert job["inputs"]["crop"]["source"] == "job_artifact"
        assert job["inputs"]["crop"]["output_key"] == "crop"
        assert job["inputs"]["picture"]["source"] == "job_artifact"
        assert job["inputs"]["picture"]["output_key"] == "normalized"
        loads = [
            n["inputs"]["image"] for n in job["graph"].values() if n["class_type"] == "LoadImage"
        ]
        assert loads == [job["inputs"][k]["remote_name"] for k in ("crop", "picture")]
    assert (
        world.invoke(
            start_face_portraits, "picture", [0, 0, 97, 97], seed=271828, request_key="portraits"
        )["id"]
        == operation["id"]
    )


def test_face_reference_operations_collect_real_assets_with_mocked_inference(tmp_path):
    import io

    from fake_comfy import FakeComfy
    from test_production_api import run_idle, setup, source_assets

    from gaiavfx.application.target_faces import start_face_portraits, start_face_sheet
    from gaiavfx.modules.generation.operations.service import OperationService

    app = setup(tmp_path)
    workspace = app.state.workspace
    ops = OperationService()
    assets, _ = source_assets(app, tmp_path)

    def portrait_output(graph):
        stream = io.BytesIO()
        Image.new("RGB", (1024, 1024), (graph["sample"]["inputs"]["seed"], 90, 130)).save(
            stream, "PNG"
        )
        return (
            {"save": {"images": [{"filename": "portrait.png", "type": "output"}]}},
            {"save:images:0": stream.getvalue()},
        )

    comfy = FakeComfy(portrait_output, polls_to_complete=1)
    app.state.operations.comfy = comfy
    with workspace.db.transaction() as session:
        portraits = start_face_portraits(
            session,
            workspace.library,
            assets[0],
            [1, 1, 12, 12],
            seed=10,
            request_key="mock-portraits",
        )
    run_idle(app)
    with workspace.db.transaction() as session:
        portraits = ops.operation(session, portraits["id"])
        assert portraits["state"] == "succeeded"
        assert len(portraits["candidates"]) == 3
        crop = portraits["jobs"][0]
        assert crop["outputs"][0]["artifact_id"]
        assert not any(c["job_id"] == crop["id"] for c in portraits["candidates"])
        sheet = start_face_sheet(
            session, workspace.library, assets[0], portraits["id"], request_key="mock-sheet"
        )
    run_idle(app)
    with workspace.db.transaction() as session:
        sheet = ops.operation(session, sheet["id"])
        assert sheet["state"] == "succeeded" and len(sheet["candidates"]) == 1
        asset = workspace.library.get(session, sheet["candidates"][0]["asset_id"])
        assert (asset["info"]["width"], asset["info"]["height"]) == (768, 1024)
        provenance = asset["provenance"][-1]
        assert provenance["type"] == "generation_candidate"
        assert provenance["operation_id"] == sheet["id"]
        assert set(provenance["inputs"]) == {
            assets[0],
            *(c["asset_id"] for c in portraits["candidates"]),
        }
        assert workspace.library.get(session, assets[0])["provenance"] == [{"type": "test"}]
    assert len(comfy.posts) == 3


def test_approved_target_lowers_to_h3_and_face_check():
    from test_replacement_contract import mug_facts, plan

    from gaiavfx.application import replace_character
    from gaiavfx.application.operations import knowledge_snapshot
    from gaiavfx.application.recast_planning import effective_target
    from gaiavfx.integrations import minimax_h3

    target = effective_target(
        {"target_asset_id": "raw", "reference_asset_id": "sheet", "reference_state": "approved"}
    )
    facts = mug_facts()
    facts = replace(facts, performers=(replace(facts.performers[0], target_asset=target),))
    request = {
        "facts": replace_character.to_record(facts),
        "tracks": [],
        "policy": replacement_policy_text(),
        "knowledge": knowledge_snapshot(minimax_h3),
        "sources": {asset: {"sha256": asset} for asset in (facts.source_asset, target)},
        "seed": 123,
        "has_audio": False,
    }
    jobs = replace_character._round_jobs(request, plan(facts).arm, 0)
    for job in jobs:
        if job.executor != "comfy":
            continue
        assert "sheet" in job.inputs and "raw" not in job.inputs
        remote = job.inputs["sheet"]["remote_name"]
        if job.integration == "minimax_h3":
            assert any(
                n["class_type"] == "LoadImage" and n["inputs"]["image"] == remote
                for n in job.graph.values()
            )
        else:
            assert any(
                n["class_type"] == "GaiaFaceCheck"
                and job.graph[n["inputs"]["target"][0]]["inputs"]["image"] == remote
                for n in job.graph.values()
            )


@pytest.mark.parametrize("box", [(120, 100, 160, 180), (0, 0, 40, 80)])
def test_portrait_crop_preserves_face_scale_and_centre_at_edges(tmp_path, box):
    import numpy as np

    from gaiavfx.application.operation_steps import portrait_crop

    image = np.full((400, 400, 3), 255, dtype=np.uint8)
    x1, y1, x2, y2 = box
    image[y1:y2, x1:x2] = (255, 0, 0)
    source = tmp_path / "face.png"
    Image.fromarray(image).save(source)
    outputs = portrait_crop(
        LocalContext(
            None,
            tmp_path / "crop",
            {},
            {"target": source},
            {"step_spec": {"target": "target", "box": box}},
        )
    )
    with Image.open(outputs[0][1]) as crop:
        pixels = np.asarray(crop)
        ys, xs = np.where((pixels[:, :, 0] > 220) & (pixels[:, :, 1] < 40))
        scale_x = (xs.max() - xs.min() + 1) / (x2 - x1)
        scale_y = (ys.max() - ys.min() + 1) / (y2 - y1)
        assert abs(scale_x - scale_y) < 0.04
        assert abs(scale_x - 512 / (2.5 * max(x2 - x1, y2 - y1))) < 0.04
        assert abs((xs.max() + xs.min()) / 2 - 255.5) < 2
        assert abs((ys.max() + ys.min()) / 2 - 255.5) < 2
        if x1 == 0:
            assert crop.getpixel((0, 0)) == (196, 196, 196)


def test_portrait_crop_publishes_exif_normalized_target(tmp_path):
    from gaiavfx.application.operation_steps import portrait_crop

    source = tmp_path / "rotated.png"
    image = Image.new("RGB", (100, 60), "red")
    image.paste("blue", (50, 0, 100, 60))
    exif = Image.Exif()
    exif[274] = 6
    image.save(source, exif=exif)
    outputs = portrait_crop(
        LocalContext(
            None,
            tmp_path / "crop",
            {},
            {"target": source},
            {"step_spec": {"target": "target", "box": [10, 10, 30, 40]}},
        )
    )
    assert outputs[1][::2] == ("normalized", "visual_evidence")
    with Image.open(outputs[1][1]) as normalized:
        assert normalized.size == (60, 100) and not normalized.getexif().get(274)
        assert normalized.getpixel((30, 10)) == (255, 0, 0)
        assert normalized.getpixel((30, 90)) == (0, 0, 255)
    with Image.open(source) as unchanged:
        assert unchanged.size == (100, 60) and unchanged.getexif()[274] == 6


def test_portrait_sheet_keeps_small_target_size_and_composites_alpha(tmp_path):
    from gaiavfx.application.operation_steps import portrait_sheet
    from gaiavfx.integrations.minimax_h3.sheet import portrait_sheet_layout

    target = tmp_path / "transparent.png"
    image = Image.new("RGBA", (32, 48), (255, 0, 0, 128))
    image.putpixel((0, 0), (255, 0, 0, 0))
    image.save(target)
    paths = {key: target for key in ("target", "front", "three_quarter", "profile")}
    spec = {key: key for key in paths} | {
        "width": 768,
        "height": 1024,
        "gap": 6,
        "gap_rgb": [196] * 3,
    }
    outputs = portrait_sheet(LocalContext(None, tmp_path / "sheet", {}, paths, {"step_spec": spec}))
    assert portrait_sheet_layout((32, 48), 768, 1024, 6)[0] == (112, 488, 32, 48)
    with Image.open(outputs[0][1]) as sheet:
        assert sheet.getpixel((112, 488)) == (196, 196, 196)
        assert sheet.getpixel((128, 512)) == (226, 98, 98)
        assert sheet.getpixel((111, 512)) == (196, 196, 196)
        assert sheet.getpixel((144, 512)) == (196, 196, 196)
