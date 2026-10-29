"""Fake-inference end-to-end replacement invariants and durable loop recovery."""

import hashlib
from pathlib import Path

import numpy as np
import pytest
from fake_comfy import ReplacementComfy
from PIL import Image

from gaiavfx.application import replacement_steps
from gaiavfx.application.operations import OperationRunner
from gaiavfx.application.replace_character import (
    approve_replacement,
    open_replacement_review,
    people_for_asset,
    replacement_plan,
    start_people_analysis,
    start_replacement,
)
from gaiavfx.application.replacement_steps import _pipe, decode_rgb, lossless_video
from gaiavfx.infrastructure.database import Database
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.editing.selections.service import SelectionService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService, MediaLibrary


def natural_colours(*_args, **_kwargs):
    """Stand in a natural-colour score for plumbing tests, including inverted retries.
    test_replacement_checks exercises the real measure on explicit inverted guides."""
    return 0.5


def test_bridge_facts_rebases_presence_and_quiet_intervals():
    from dataclasses import replace

    from test_replacement_contract import mug_facts

    from gaiavfx.application.replace_character import bridge_facts
    from gaiavfx.modules.direction.replacement import to_record

    left = replace(mug_facts(), silent_intervals=((20, 60),))
    right = replace(
        mug_facts(),
        shot_start=51,
        shot_end=124,
        silent_intervals=((0, 15), (20, 30)),
        speaking=True,
    )
    layout = {"start": 26, "end": 99}
    merged = bridge_facts({"facts": to_record(left)}, {"facts": to_record(right)}, layout)
    assert merged.silent_intervals == ((0, 40), (45, 55))
    assert merged.speaking and not merged.quiet and merged.handoff == "bridge"
    assert merged.shot_start == 26 and merged.shot_end == 99
    assert all(len(t.presence) == 73 for t in merged.tracks)
    moved_bystander = replace(
        left,
        tracks=(
            left.tracks[0],
            replace(left.tracks[1], box=replace(left.tracks[1].box, x0=0)),
        ),
    )
    merged = bridge_facts(
        {"facts": to_record(moved_bystander)}, {"facts": to_record(right)}, layout
    )
    assert merged.tracks[1].box == right.tracks[1].box


@pytest.mark.parametrize("presence", ["partial", "absent", "unresolved"])
def test_bridge_facts_accepts_partial_presence_and_refuses_only_missing_or_unresolved(presence):
    from dataclasses import replace

    from test_replacement_contract import CAP, POLICY, mug_facts

    from gaiavfx.application.replace_character import bridge_facts
    from gaiavfx.modules.direction.replacement import scope_gaps, to_record

    left, right = mug_facts(), replace(mug_facts(), shot_start=51, shot_end=124)
    left_presence = ("present",) * 40 + ("absent",) * 33
    right_presence = ("present",) * 73
    if presence == "absent":
        left_presence = right_presence = ("absent",) * 73
    elif presence == "unresolved":
        left_presence = ("present",) * 40 + ("unresolved",) + ("absent",) * 32
    left = replace(left, tracks=(replace(left.tracks[0], presence=left_presence), *left.tracks[1:]))
    right = replace(
        right, tracks=(replace(right.tracks[0], presence=right_presence), *right.tracks[1:])
    )
    args = ({"facts": to_record(left)}, {"facts": to_record(right)}, {"start": 26, "end": 99})
    if presence != "partial":
        with pytest.raises(ValueError, match="presence"):
            bridge_facts(*args)
        return
    merged = bridge_facts(*args)
    assert merged.tracks[0].presence == ("present",) * 14 + ("absent",) * 11 + ("present",) * 48
    assert not scope_gaps(merged, CAP.replacement, POLICY, CAP.frame_grid)


@pytest.mark.parametrize("count", [2, 3])
def test_latent_shot_collects_context_and_uploads_it_for_second_window(
    tmp_path, monkeypatch, count
):
    import json
    import struct

    from test_fractional_fps import make_work

    from gaiavfx.application.shot_replacement import (
        continue_shot_replacement,
        shared_crop_for_windows,
        start_shot_replacement,
    )

    monkeypatch.setattr(replacement_steps, "inversion_score", natural_colours)
    db, library, runner, assets, _ = make_work(tmp_path, count=73 + 51 * (count - 1))
    monkeypatch.setattr(runner.comfy, "people", 1)
    with db.transaction() as session:
        analyses = [
            start_people_analysis(session, assets[0], start, end, request_key=f"analysis-{i}")
            for i in range(count)
            for start, end in [(i * 51, i * 51 + 73)]
        ]
    drain(runner)
    performer = {"tracks": {}, "target_asset": assets[1], "selected_by": "user"}
    with db.transaction() as session:
        for analysis in analyses:
            operation = OperationService().operation(session, analysis["id"])
            assert operation["state"] == "succeeded", operation
            artifact = operation["jobs"][-1]["outputs"][0]["artifact_id"]
            report = json.loads(library.read_blob(session, artifact))
            performer["tracks"][analysis["id"]] = report["tracks"][0]["id"]
        kwargs = {
            "source_asset": assets[0],
            "window_analyses": [a["id"] for a in analyses],
            "performers": [performer],
            "mode": "draft",
            "seed": 7,
            "request_key": "latent-shot",
        }
        if count == 3:
            crop = shared_crop_for_windows(
                session,
                library,
                requests=[
                    {
                        "source_asset": assets[0],
                        "analysis_operation": a["id"],
                        "performers": [
                            {
                                "track_id": performer["tracks"][a["id"]],
                                "target_asset": assets[1],
                                "selected_by": "user",
                            }
                        ],
                    }
                    for a in analyses
                ],
            )
            kwargs["handoff"] = {"kind": "latent", "reason": "close_up_chain", "crop": crop}
        windows = start_shot_replacement(session, library, **kwargs)
        assert len(windows) == 1 and windows[0]["request"]["save_latent"]
        operation = windows[0]
    download = runner.comfy.download
    header = json.dumps(
        {
            "__metadata__": {"format": "h3_motion_context_av_v1"},
            "video": {"shape": [1, 1, 1, 1, 1]},
            "audio": {"shape": [1, 1, 1, 1]},
        }
    ).encode()

    def capture(endpoint, descriptor, path):
        if descriptor.get("output_key") == "context":
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(struct.pack("<Q", len(header)) + header)
        else:
            download(endpoint, descriptor, path)

    monkeypatch.setattr(runner.comfy, "download", capture)
    drain(runner)
    operation = read(db, operation["id"])
    assert operation["state"] == "succeeded", operation
    render = operation["jobs"][1]
    saved = render["snapshot"]["capture_context"]
    assert (
        render["graph"]["save_latent"]["inputs"]["filename_prefix"]
        == saved["subfolder"] + "/context"
    )
    assert saved["subfolder"] == render["inputs"]["guide"]["remote_name"].rsplit("/", 1)[0]
    output = render["outputs"][0]
    with db.transaction() as session:
        artifact = library.artifact(session, output["context_artifact_id"])
        assert artifact["sha256"] == output["context_sha256"] and artifact["kind"] == "h3_context"
        successor = continue_shot_replacement(
            session, library, **kwargs, window_operations=[operation["id"]]
        )
    assert all(c["job_id"] != render["id"] for c in operation["candidates"])
    handoff = successor["request"]["handoff"]
    assert handoff["predecessor_operation"] == operation["id"]
    assert handoff["candidate_id"] in {c["id"] for c in operation["candidates"]}
    assert handoff["latent_artifact"] == artifact["id"]
    assert handoff["sha256"] == artifact["sha256"]
    assert successor["request"]["crop_override"] == operation["request"]["crop_override"]
    second_render = successor["jobs"][1]
    binding = second_render["inputs"]["latent"]
    assert binding == {
        "source": "artifact",
        "artifact_id": artifact["id"],
        "sha256": artifact["sha256"],
        "directory": "output",
        "remote_name": second_render["inputs"]["guide"]["remote_name"].rsplit("/", 1)[0]
        + "/context.safetensors",
    }
    drain(runner)
    successor = read(db, successor["id"])
    assert successor["state"] == "succeeded", successor
    uploaded = dict(zip(runner.comfy.uploads, runner.comfy.upload_directories, strict=True))
    assert uploaded[(artifact["sha256"], binding["remote_name"])] == "output"
    assert (
        successor["jobs"][1]["graph"]["load_prev"]["inputs"]["latent_path"]
        == binding["remote_name"]
    )
    assert successor["request"].get("save_latent", False) is (count == 3)
    if count == 3:
        middle = successor["jobs"][1]
        assert {"load_prev", "save_latent"} <= middle["graph"].keys()
        with db.transaction() as session:
            last = continue_shot_replacement(
                session,
                library,
                **kwargs,
                window_operations=[operation["id"], successor["id"]],
            )
        assert last["request"]["handoff"]["predecessor_operation"] == successor["id"]
        assert (
            last["request"]["handoff"]["latent_artifact"]
            == middle["outputs"][0]["context_artifact_id"]
        )
        assert not last["request"].get("save_latent", False)
        drain(runner)
        assert read(db, last["id"])["state"] == "succeeded"
    db.close()


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setattr(replacement_steps, "inversion_score", natural_colours)
    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Replacement")
    db = Database(tmp_path)
    library = MediaLibrary(db, MediaTools())
    rng = np.random.default_rng(4)
    image = rng.integers(30, 220, (320, 512, 3), dtype=np.uint8)
    image[:20] = image[-20:] = 0
    frames = np.repeat(image[None], 53, axis=0)
    audio = tmp_path / "sound.wav"
    library.media.run(
        library.media.ffmpeg,
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:sample_rate=48000:duration=3",
            "-c:a",
            "pcm_f32le",
            str(audio),
        ],
    )
    source = tmp_path / "source.mkv"
    lossless_video(library.media, frames, source, 24, audio)
    target = tmp_path / "target.png"
    Image.new("RGB", (32, 48), (200, 40, 60)).save(target)
    assets = []
    for path in (source, target):
        prepared = library.prepare(path, path.name)
        with db.transaction() as session:
            assets.append(library.register(session, prepared, {"type": "import"})["id"])
    fake = ReplacementComfy(tmp_path, library.media)
    runner = OperationRunner(db, fake, library, "http://fake", poll_seconds=0)
    yield db, library, runner, assets, frames
    db.close()


def read(db, operation_id):
    with db.transaction() as session:
        return OperationService().operation(session, operation_id)


def drain(runner):
    for _ in range(30):
        if not runner.run_once():
            return
    raise AssertionError("Replacement queue did not settle")


def analysed(work, concepts=("person",)):
    db, _, runner, assets, _ = work
    with db.transaction() as session:
        operation = start_people_analysis(
            session, assets[0], 1, 52, concepts=concepts, request_key="analysis"
        )
    drain(runner)
    operation = read(db, operation["id"])
    assert operation["state"] == "succeeded", operation
    with db.transaction() as session:
        report = people_for_asset(session, assets[0], library=work[1])[0]["report"]
    request = {
        "source_asset": assets[0],
        "analysis_operation": operation["id"],
        "performers": [{"track_id": report["tracks"][0]["id"], "target_asset": assets[1]}],
        "seed": 7,
    }
    return operation, report, request


def pcm(media, path, start=0, end=None):
    args = [
        "-i",
        str(path),
        "-vn",
        "-af",
        f"atrim=start={start / 24:.12f}" + (f":end={end / 24:.12f}" if end else ""),
        "-f",
        "f32le",
        "-acodec",
        "pcm_f32le",
        "pipe:1",
    ]
    return hashlib.sha256(_pipe(media, args)).hexdigest()


def test_analysis_to_composite_review_and_derivative_preserves_source(work):
    db, library, runner, assets, frames = work
    analysis, report, request = analysed(work, ("person", "yellow mug"))
    assert analysis["budget_reserved"] == 0 and analysis["candidates"] == []
    assert len([t for t in report["tracks"] if t["concept"] == "person"]) == 2
    assert report["contacts"][report["tracks"][0]["id"]]
    with db.transaction() as session:
        selection = SelectionService().select(
            session,
            movie_id="movie",
            segment_id="segment",
            candidate_id="existing-take",
            asset_id=assets[0],
            authority="user",
            expected_revision=0,
        )
        source_before = library.get(session, assets[0])
        source_path = library.path(session, assets[0])
        source_hash = hashlib.sha256(source_path.read_bytes()).hexdigest()
        preview = replacement_plan(session, request, library=work[1])
        assert preview["arm"] and preview["crop_preview"]
        assert preview["facts"]["required_context"]
        assert preview["facts"]["performers"][0]["target_text"] is None
        op = start_replacement(session, request, 4, "replacement", library=work[1])
        assert (
            start_replacement(session, request, 4, "replacement", library=work[1])["id"] == op["id"]
        )
    drain(runner)
    op = read(db, op["id"])
    assert op["state"] == "succeeded", op
    assert len(op["candidates"]) == 2
    with db.transaction() as session:
        for job in op["jobs"]:
            if job["snapshot"].get("intermediate"):
                for output in job["outputs"]:
                    if output.get("asset_id"):
                        provenance = library.get(session, output["asset_id"])["provenance"]
                        assert any(p["type"] == "replacement_intermediate" for p in provenance)
                        assert not any(p["type"] == "generation_candidate" for p in provenance)
    assert len(op["review"]) == 1 and op["review"][0]["arm"] is None
    assert op["request"]["first_seeds"] == [7, 8]
    box = preview["crop_preview"]["box"]
    yy, xx = np.mgrid[:320, :512]
    outside = (
        (xx + 0.5 <= box["x0"])
        | (xx + 0.5 >= box["x1"])
        | (yy + 0.5 <= box["y0"])
        | (yy + 0.5 >= box["y1"])
    )
    expected_audio = pcm(library.media, source_path, 1, 52)
    for candidate in op["candidates"]:
        with db.transaction() as session:
            path = library.path(session, candidate["asset_id"])
        decoded = decode_rgb(library.media, path)
        assert decoded.shape == frames[1:52].shape
        assert np.array_equal(decoded[:, outside], frames[1:52, outside])
        assert np.array_equal(decoded[:, :20], frames[1:52, :20])
        assert np.array_equal(decoded[:, -20:], frames[1:52, -20:])
        assert pcm(library.media, path) == expected_audio
        assert candidate["prescreen"]
    with db.transaction() as session:
        review = open_replacement_review(session, op["id"])
        assert open_replacement_review(session, op["id"])["id"] == review["id"]
        assert len(review["members"]) == 2
        approved = approve_replacement(session, op["candidates"][0]["id"])
        assert approved["asset_id"] != assets[0]
        assert set(approved["provenance"]) == {
            "type",
            "source_asset",
            "shot",
            "operation",
            "round",
            "arm",
        }
        assert library.get(session, assets[0]) == source_before
        assert SelectionService().current(session, "movie", "segment") == selection
    assert hashlib.sha256(source_path.read_bytes()).hexdigest() == source_hash


def test_geometry_advice_advances_once_and_recovery_keeps_all_candidates(work):
    db, _, runner, _, _ = work
    _, _, request = analysed(work)
    runner.comfy.shift = 12
    with db.transaction() as session:
        op = start_replacement(session, request, 4, library=work[1])
    # Preparation, two renders/composites and two measurements: then append round 2.
    for _ in range(7):
        assert runner.run_once()
    op = read(db, op["id"])
    assert op["state"] == "queued", op
    assert len(op["jobs"]) == 11
    assert op["review"][0]["arm"]["framing"] == "active"
    with db.transaction() as session:
        early = open_replacement_review(session, op["id"])
        assert len(early["members"]) == 2
    runner.recover()
    runner.recover()
    assert len(read(db, op["id"])["jobs"]) == 11
    drain(runner)
    op = read(db, op["id"])
    assert op["state"] == "succeeded", op
    assert len(op["candidates"]) == 3
    with db.transaction() as session:
        assert len(open_replacement_review(session, op["id"])["members"]) == 3


def test_cancellation_prevents_advancer_and_further_submission(work):
    db, _, runner, _, _ = work
    _, _, request = analysed(work)
    with db.transaction() as session:
        op = start_replacement(session, request, 4, library=work[1])
    runner.run_once()
    with db.transaction() as session:
        OperationService().request_cancel(session, op["id"])
    submitted = len(runner.comfy.posts)
    runner.recover()
    drain(runner)
    op = read(db, op["id"])
    assert op["state"] == "cancelled" and not op["review"]
    assert len(runner.comfy.posts) == submitted


def test_no_advisory_stops_after_first_round_even_when_background_shifts(work, monkeypatch):
    import gaiavfx.application.replace_character as replacement

    db, _, runner, _, _ = work
    _, _, request = analysed(work)
    text = replacement.replacement_policy_text().replace(
        'fires_on = ["fail", "retry"]', 'fires_on = ["fail"]'
    )
    monkeypatch.setattr(replacement, "replacement_policy_text", lambda: text)
    runner.comfy.shift = 12
    with db.transaction() as session:
        op = start_replacement(session, request, 4, library=work[1])
    drain(runner)
    op = read(db, op["id"])
    assert op["state"] == "succeeded"
    assert len(op["candidates"]) == 2 and len(op["jobs"]) == 7
    assert op["review"][0]["arm"] is None


def test_failed_advance_rolls_back_resume_and_recovery_retries_once(work, monkeypatch):
    from gaiavfx.application.operations import ADVANCERS

    db, _, runner, _, _ = work
    _, _, request = analysed(work)
    runner.comfy.shift = 12
    advance = ADVANCERS["character_replacement"]

    def fail_after_resume(session, operation, *, library):
        advance(session, operation, library=library)
        raise RuntimeError("injected after resume")

    monkeypatch.setitem(ADVANCERS, "character_replacement", fail_after_resume)
    with db.transaction() as session:
        op = start_replacement(session, request, 4, library=work[1])
    for _ in range(7):
        runner.run_once()
    op = read(db, op["id"])
    assert op["state"] == "awaiting_review"
    assert len(op["jobs"]) == 7
    assert op["review"] == [{"decided_by": "advancer_error", "error": "injected after resume"}]
    monkeypatch.setitem(ADVANCERS, "character_replacement", advance)
    runner.recover()
    assert len(read(db, op["id"])["jobs"]) == 11
    runner.recover()
    assert len(read(db, op["id"])["jobs"]) == 11
    with db.transaction() as session:
        OperationService().request_cancel(session, op["id"])
    runner.recover()
    assert read(db, op["id"])["state"] == "cancelled"


def test_analysis_recovery_and_job_artifact_hash_guard(work):
    from fake_comfy import Crash

    from gaiavfx.shared.errors import ApplicationError

    db, library, runner, assets, _ = work
    runner.comfy.crash_after_accept = Crash
    with db.transaction() as session:
        op = start_people_analysis(session, assets[0], 1, 52)
    runner.run_once()  # local shot extraction precedes SAM
    with pytest.raises(Crash):
        runner.run_once()
    runner.recover()
    runner.run_once()
    op = read(db, op["id"])
    assert len(runner.comfy.posts) == 1
    assert op["jobs"][1]["state"] == "succeeded"
    descriptor = next(d for d in op["jobs"][1]["outputs"] if d["output_key"] == "analysis")
    with db.transaction() as session:
        path = library.artifact_path(session, descriptor["artifact_id"])
    path.write_bytes(path.read_bytes() + b" ")
    with pytest.raises(ApplicationError, match="artifact changed"):
        runner._sources(op["jobs"][2])
    assert op["candidates"] == []


def test_resume_rejects_fourth_round_over_reservation_and_bad_decisions(tmp_path):
    from test_operations_store import run_to_success

    from gaiavfx.modules.generation.operations.lifecycle import JobSpec
    from gaiavfx.shared.errors import ApplicationError

    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Round limits")
    db, service = Database(tmp_path), OperationService()
    job = JobSpec(
        "video", "comfy", "fake", graph={"node": {}}, snapshot={"knowledge": {"test": "frozen"}}
    )

    def finish(session):
        while (child := service.claim_next(session)) is not None:
            run_to_success(session, service, child)

    def decision(round_index, count=1):
        return {
            "decided_by": "advancer",
            "round": round_index,
            "arm": {"round": round_index, "seed_count": count, "seed_offset": round_index + 1},
            "gaps": [],
            "reasons": ["test retry"],
            "seeds": list(range(10 + round_index + 1, 10 + round_index + 1 + count)),
        }

    try:
        with db.transaction() as session:
            op = service.create(
                session,
                kind="character_replacement",
                request={"seed": 10, "policy_limits": {"max_rounds": 3}},
                jobs=[job, job],
                budget_limit=4,
                budget=4,
                awaits_review=True,
            )
            finish(session)
            with pytest.raises(ApplicationError, match="Invalid advancer"):
                service.resume(session, op["id"], {"decided_by": "advancer"})
            with pytest.raises(ApplicationError, match="reservation"):
                service.resume(
                    session, op["id"], decision(1, 3), jobs=[job, job, job], awaits_review=True
                )
            service.resume(session, op["id"], decision(1), jobs=[job], awaits_review=True)
            finish(session)
            service.resume(session, op["id"], decision(2), jobs=[job], awaits_review=True)
            finish(session)
            with pytest.raises(ApplicationError, match="bounds"):
                service.resume(session, op["id"], decision(3), jobs=[job], awaits_review=True)
            closed = service.resume(session, op["id"], {**decision(3), "arm": None, "seeds": []})
            assert closed["state"] == "succeeded"
            assert len(closed["jobs"]) == 4
    finally:
        db.close()


@pytest.mark.parametrize("failed", [(1,), (2,), (1, 2)])
def test_failed_renders_close_from_surviving_composites_on_idle_tick(work, failed):
    db, library, runner, _, _ = work
    _, _, request = analysed(work)
    rendered = 0

    def fail(graph):
        nonlocal rendered
        if any(node["class_type"] == "GaiaFaceCheck" for node in graph.values()):
            return False
        rendered += 1
        return rendered in failed

    runner.comfy.fail_graph = fail
    with db.transaction() as session:
        op = start_replacement(session, request, 4, library=library)
    drain(runner)  # Last dependent settles in claim_next when the final render failed.
    result = read(db, op["id"])
    assert result["state"] == "partial", result
    assert len(result["candidates"]) == 2 - len(failed)
    assert len(result["review"]) == 1
    decision = result["review"][0]
    assert decision["decided_by"] == "advancer" and decision["arm"] is None
    assert any("successful composites" in r for r in decision["reasons"])
    if result["candidates"]:
        with db.transaction() as session:
            assert len(open_replacement_review(session, op["id"])["members"]) == 1
    before = result["updated_at"]
    runner._advance(op["id"])
    runner._advance()
    assert read(db, op["id"])["updated_at"] == before


def test_policy_round_limit_larger_than_reservation_closes_without_error(work, monkeypatch):
    import gaiavfx.application.replace_character as replacement

    db, library, runner, _, _ = work
    _, _, request = analysed(work)
    text = replacement.replacement_policy_text().replace("renders = 4", "renders = 2")
    monkeypatch.setattr(replacement, "replacement_policy_text", lambda: text)
    runner.comfy.shift = 12
    with db.transaction() as session:
        op = start_replacement(session, request, 2, library=library)
    drain(runner)
    result = read(db, op["id"])
    assert result["state"] == "succeeded", result
    assert len(result["jobs"]) == 7 and result["budget_reserved"] == 2
    assert result["review"][0]["arm"] is None
    assert any("reservation exhausted" in r for r in result["review"][0]["reasons"])


def test_advance_contains_discovery_and_error_persistence_failures(monkeypatch, caplog):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from gaiavfx.application.operations import ADVANCERS

    @contextmanager
    def broken():
        raise RuntimeError("database unavailable")
        yield

    runner = OperationRunner(SimpleNamespace(transaction=broken), None, None, "http://fake")
    runner._advance()
    assert "advance pass failed" in caplog.text

    @contextmanager
    def transaction():
        yield object()

    runner.db.transaction = transaction
    monkeypatch.setattr(
        runner.service,
        "operation",
        lambda *args: {
            "kind": "character_replacement",
            "state": "awaiting_review",
            "cancel_requested": False,
        },
    )
    monkeypatch.setattr(runner.service, "lock_for_advance", lambda *args: None)

    def fail(*args, **kwargs):
        raise RuntimeError("callback unavailable")

    monkeypatch.setitem(ADVANCERS, "character_replacement", fail)
    monkeypatch.setattr(runner.service, "record_advance_error", fail)
    runner._advance("op")
    assert "Could not record advance error" in caplog.text


@pytest.mark.parametrize("failed_state", ["failed", "cancelled"])
def test_advancer_uses_successful_composites_without_refunding_failed_take(
    monkeypatch, failed_state
):
    import json
    from types import SimpleNamespace

    from test_replacement_contract import mug_facts

    import gaiavfx.application.replace_character as replacement
    from gaiavfx.modules.direction.replacement import to_record

    facts = mug_facts()
    text = replacement.replacement_policy_text()
    arm = replacement._plan(facts, text).arm
    composites = [
        {
            "step": "replacement_composite",
            "executor": "local",
            "kind": "video",
            "state": state,
            "snapshot": {"step_spec": {"arm": to_record(arm)}},
            "outputs": [{"output_key": "checks", "artifact_id": "checks"}],
        }
        for state in ("succeeded", failed_state)
    ]
    op = {
        "id": "op",
        "state": "awaiting_review",
        "cancel_requested": False,
        "review": [],
        "budget_reserved": 4,
        "request": {
            "facts": to_record(facts),
            "policy": text,
            "policy_limits": {"max_rounds": 3},
            "seed": 7,
        },
        "jobs": composites
        + [{"snapshot": {}, "step": None, "executor": "comfy", "kind": "video"}] * 2,
    }
    monkeypatch.setattr(replacement.operations, "operation", lambda *args: op)
    seen = []
    original = replacement._plan

    def plan(facts, text, attempts, renders=None, **kwargs):
        seen.extend(attempts)
        return original(facts, text, attempts, renders=renders, **kwargs)

    monkeypatch.setattr(replacement, "_plan", plan)
    monkeypatch.setattr(replacement, "_round_jobs", lambda *args: [])
    monkeypatch.setattr(
        replacement.operations, "resume", lambda session, op_id, decision, **kwargs: decision
    )
    library = SimpleNamespace(
        read_blob=lambda *args: json.dumps(
            {
                "checks": [
                    {
                        "check": "background_geometry",
                        "verdict": "unsupported",
                        "advice": "retry",
                        "measured": 2.0,
                    }
                ]
            }
        )
    )
    decision = replacement.advance_replacement(None, op, library=library)
    assert len(seen[0].takes) == 1 and seen[0].arm.seed_count == 2
    assert decision["arm"]["framing"] == "active" and decision["arm"]["seed_offset"] == 2
    assert decision["seeds"] == [9]


@pytest.mark.parametrize("limit,reserved,expected", [(1, 4, 0), (3, 2, 0), (3, 3, 1)])
def test_advancer_clamps_before_resume(monkeypatch, limit, reserved, expected):
    import json
    from types import SimpleNamespace

    from test_replacement_contract import mug_facts

    import gaiavfx.application.replace_character as replacement
    from gaiavfx.modules.direction.replacement import to_record

    facts = mug_facts()
    text = replacement.replacement_policy_text().replace(
        "renders_per_round = [2, 1, 1]", "renders_per_round = [2, 2, 2]"
    )
    arm = replacement._plan(facts, text).arm
    op = {
        "id": "op",
        "state": "awaiting_review",
        "cancel_requested": False,
        "review": [],
        "budget_reserved": reserved,
        "request": {
            "facts": to_record(facts),
            "policy": text,
            "policy_limits": {"max_rounds": limit},
            "seed": 7,
        },
        "jobs": [
            {
                "step": "replacement_composite",
                "executor": "local",
                "kind": "video",
                "state": "succeeded",
                "snapshot": {"step_spec": {"arm": to_record(arm)}},
                "outputs": [{"output_key": "checks", "artifact_id": "checks"}],
            }
        ]
        * 2
        + [{"step": None, "snapshot": {}, "executor": "comfy", "kind": "video"}] * 2,
    }
    monkeypatch.setattr(replacement.operations, "operation", lambda *args: op)
    monkeypatch.setattr(
        replacement, "_round_jobs", lambda request, arm, start: [object()] * arm.seed_count
    )

    def resume(session, op_id, decision, *, jobs, awaits_review):
        assert len(jobs) == expected and awaits_review == bool(expected)
        assert len(jobs) + 2 <= reserved
        assert not jobs or decision["round"] < limit
        return decision

    monkeypatch.setattr(replacement.operations, "resume", resume)
    library = SimpleNamespace(
        read_blob=lambda *args: json.dumps(
            {
                "checks": [
                    {
                        "check": "background_geometry",
                        "verdict": "unsupported",
                        "advice": "retry",
                        "measured": 2.0,
                    }
                ]
            }
        )
    )
    decision = replacement.advance_replacement(None, op, library=library)
    assert len(decision["seeds"]) == expected


def test_read_blob_honours_library_instance_root(tmp_path):
    from types import SimpleNamespace

    root = tmp_path / "media-root"
    root.mkdir()
    payload = b"managed artifact in a separately configured root"
    (root / "report.json").write_bytes(payload)
    row = SimpleNamespace(path="report.json", sha256=hashlib.sha256(payload).hexdigest())
    library = MediaLibrary(SimpleNamespace(root=root), None)
    session = SimpleNamespace(get=lambda *args: row)
    assert library.read_blob(session, "report") == payload


def test_idle_tick_continues_when_advancer_appends_a_round(monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from gaiavfx.application.operations import ADVANCERS

    @contextmanager
    def transaction():
        yield object()

    runner = OperationRunner(SimpleNamespace(transaction=transaction), None, None, "http://fake")
    monkeypatch.setattr(runner.service, "claim_next", lambda *args: None)
    monkeypatch.setattr(runner.service, "ready_to_advance", lambda *args: ["op"])
    monkeypatch.setattr(runner.service, "lock_for_advance", lambda *args: None)
    monkeypatch.setattr(
        runner.service,
        "operation",
        lambda *args: {
            "kind": "character_replacement",
            "state": "awaiting_review",
            "cancel_requested": False,
        },
    )
    called = []

    def advance(session, operation, **kwargs):
        called.append(operation)
        return {"state": "queued"}

    monkeypatch.setitem(ADVANCERS, "character_replacement", advance)
    assert runner.run_once() is True
    assert len(called) == 1


def test_idle_polls_sweep_for_advances_once_after_work_then_rarely(work, monkeypatch):
    _, _, runner, _, _ = work
    sweeps = []
    original = runner.service.ready_to_advance
    monkeypatch.setattr(
        runner.service,
        "ready_to_advance",
        lambda session, *args: sweeps.append(1) or original(session, *args),
    )
    clock = [100.0]
    monkeypatch.setattr("gaiavfx.application.operations.time.monotonic", lambda: clock[0])
    assert not runner.run_once() and len(sweeps) == 1
    for _ in range(20):
        # A 10 ms poll must not add a read transaction to every tick.
        assert not runner.run_once()
    assert len(sweeps) == 1
    clock[0] += runner.sweep_seconds
    assert not runner.run_once() and len(sweeps) == 2
    sweeps.clear()
    analysed(work)  # The drain's first idle tick after the jobs sweeps with the clock frozen.
    assert len(sweeps) == 1
    assert not runner.run_once() and len(sweeps) == 1


@pytest.mark.parametrize(
    "invalid,status",
    [
        (None, None),
        ("unsettled", 409),
        ("kind", 409),
        ("source", 422),
        ("overlap", 422),
        ("foreign", 422),
        ("failed", 422),
        ("size", 422),
        ("fps", 422),
        ("length", 422),
        ("image", 422),
    ],
)
def test_handoff_freezes_native_candidate_and_round_job_bindings(monkeypatch, invalid, status):
    from dataclasses import replace
    from types import SimpleNamespace

    from test_replacement_contract import mug_facts, plan

    from gaiavfx.application import operations as app_operations
    from gaiavfx.application import replace_character as replacement
    from gaiavfx.modules.direction.application import replacement_policy_text
    from gaiavfx.modules.direction.replacement import to_record
    from gaiavfx.shared.errors import ApplicationError

    facts = replace(mug_facts(), shot_start=51, shot_end=124, handoff="latent")
    arm = plan(facts)
    previous = {
        "id": "previous",
        "kind": "character_replacement",
        "state": "succeeded",
        "request": {
            "seed": 9,
            "facts": to_record(mug_facts()),
        },
        "candidates": [{"id": "chosen", "job_id": "composite", "asset_id": "delivery"}],
        "jobs": [
            {
                "id": "composite",
                "ordinal": 2,
                "step": "replacement_composite",
                "state": "succeeded",
                "seed": 10,
                "depends_on": 1,
                "snapshot": {"step_spec": {"crop": to_record(arm.arm.crop)}},
                "outputs": [],
            },
            {
                "id": "render",
                "ordinal": 1,
                "state": "succeeded",
                "outputs": [{"context_artifact_id": "latent", "context_sha256": "latent-sha"}],
            },
        ],
    }
    asset = {
        "kind": "video",
        "sha256": "delivery-sha",
        "info": {
            "width": facts.native[0],
            "height": facts.native[1],
            "frame_rate": "24",
            "duration": 73 / 24,
        },
    }
    if invalid == "unsettled":
        previous["state"] = "awaiting_review"
    if invalid == "kind":
        previous["kind"] = "person_analysis"
    if invalid == "source":
        previous["request"]["facts"]["source_asset"] = "other"
    if invalid == "overlap":
        previous["request"]["facts"] = to_record(mug_facts(51))
    if invalid == "foreign":
        previous["candidates"] = []
    if invalid == "failed":
        previous["jobs"][0]["state"] = "failed"
    if invalid == "size":
        asset["info"]["width"] = 2
    if invalid == "fps":
        asset["info"]["frame_rate"] = "25"
    if invalid == "length":
        asset["info"]["duration"] = 1
    if invalid == "image":
        asset["kind"] = "image"
    monkeypatch.setattr(replacement.operations, "operation", lambda *args: previous)
    monkeypatch.setattr(
        replacement,
        "_initial",
        lambda *args, **kwargs: (
            facts,
            {"tracks": [{"id": t.id, "concept": "person"} for t in facts.tracks]},
            replacement_policy_text(),
            arm,
        ),
    )
    monkeypatch.setattr(
        replacement.MediaLibrary,
        "asset_info",
        lambda session, key: (
            asset if key == "delivery" else {"sha256": key + "-sha", "info": {"has_audio": False}}
        ),
    )
    monkeypatch.setattr(app_operations, "create_operation", lambda *args, **kwargs: kwargs)
    monkeypatch.setattr(
        replacement.MediaLibrary, "artifact", lambda *args: {"id": "latent", "sha256": "latent-sha"}
    )
    request = replacement.ReplacementRequest(
        facts.source_asset,
        "analysis",
        (),
        seed=9,
        handoff=replacement.HandoffChoice("previous", "chosen"),
        crop_override=arm.arm.crop,
    )
    if status:
        with pytest.raises(ApplicationError) as exc:
            replacement.start_replacement(None, request, library=SimpleNamespace())
        assert exc.value.status == status
        return
    result = replacement.start_replacement(None, request, library=SimpleNamespace())
    handoff = result["request"]["handoff"]
    assert handoff == {
        "kind": "latent",
        "predecessor_operation": "previous",
        "candidate_id": "chosen",
        "take": 1,
        "latent_artifact": "latent",
        "sha256": "latent-sha",
        "crop": to_record(arm.arm.crop),
    }
    prepare, comfy, composite = result["jobs"][:3]
    assert "delivery" not in prepare.inputs and "chain" not in prepare.snapshot["step_spec"]
    binding = comfy.inputs["latent"]
    assert binding["source"] == "artifact" and binding["directory"] == "output"
    assert binding["artifact_id"] == "latent" and binding["sha256"] == "latent-sha"
    assert comfy.graph["load_prev"]["inputs"]["latent_path"] == binding["remote_name"]
    assert "motion_context" not in comfy.graph and "context" not in comfy.inputs
    assert composite.snapshot["expect"]["frames"] == 73
    # Every retry holds the same canvas; the predecessor is never re-cropped.
    jobs = replacement._round_jobs(result["request"], replace(arm.arm, round=1), 9)
    assert jobs[0].snapshot["step_spec"]["crop"] == to_record(arm.arm.crop)
    assert jobs[1].inputs["latent"]["artifact_id"] == "latent"


def test_head_bridge_merges_face_boxes_and_stays_rgb_unlocked(monkeypatch):
    from dataclasses import replace
    from types import SimpleNamespace

    from test_replacement_contract import mug_facts, plan

    from gaiavfx.application import replace_character as replacement
    from gaiavfx.application.operations import knowledge_snapshot
    from gaiavfx.integrations import minimax_h3
    from gaiavfx.modules.direction.replacement import to_record

    left = mug_facts()
    right = replace(left, shot_start=51, shot_end=124)
    facts = replacement.bridge_facts(
        {"facts": to_record(left)}, {"facts": to_record(right)}, {"start": 26, "end": 99}
    )
    windows = []
    for i, original in enumerate((left, right)):
        windows.append(
            {
                "operation": f"op{i}",
                "candidate_id": f"c{i}",
                "start": original.shot_start,
                "end": original.shot_end,
                "selected_tracks": ["actor"],
                "tracks": [
                    {
                        "id": "actor",
                        "concept": "person",
                        "presence": ["present"] * 73,
                        "mouth": {"box": [[840 + i * 10, 90, 930 + i * 10, 200]] * 73},
                    }
                ],
                "arm": to_record(plan(original).arm),
            }
        )
    bridge = {"join": 1, "start": 26, "end": 99, "generated": [48, 77], "windows": windows}
    successor = {"analysis_operation": "a1", "tracks": windows[1]["tracks"]}
    monkeypatch.setattr(replacement, "_bridge_data", lambda *args: (facts, bridge, successor))
    request = replacement.ReplacementRequest(
        "source",
        "a1",
        (replacement.PerformerChoice("actor", "target"),),
        head=replacement.HeadPassChoice("parent"),
        bridge=replacement.BridgeChoice(1, ("op0", "c0"), ("op1", "c1")),
    )
    replacement._request(request)
    merged, report, policy, planned = replacement._initial(None, request, SimpleNamespace(), 1)
    assert merged.handoff == "none" and planned.arm.framing == "head"
    assert planned.arm.treatment == "rgb" and not planned.arm.audio_lock
    assert (
        report["tracks"][0]["mouth"]["box"]
        == [[840, 90, 930, 200]] * 25 + [[850, 90, 940, 200]] * 48
    )
    frozen = {
        "facts": to_record(merged),
        "tracks": report["tracks"],
        "policy": policy,
        "seed": 10,
        "knowledge": knowledge_snapshot(minimax_h3),
        "sources": {"source": {"sha256": "s"}, "target": {"sha256": "t"}},
        "head": {"parent_asset": "parent", "parent_sha256": "p"},
        "bridge": bridge,
        "has_audio": False,
    }
    prepare, render = replacement._round_jobs(frozen, planned.arm, 0)[:2]
    assert prepare.snapshot["step_spec"]["head_bridge_performer"] == 0
    assert "span" not in render.graph and "audio_lock" not in render.graph
    assert "pixels" not in render.inputs


@pytest.mark.parametrize("invalid", [None, "fps", "sha256", "canvas", "length"])
def test_bridge_freeze_validates_raw_renders_and_retains_neighbour_crops(monkeypatch, invalid):
    from dataclasses import replace

    from test_replacement_contract import mug_facts, plan

    from gaiavfx.application import replace_character as replacement
    from gaiavfx.modules.direction.replacement import to_record

    left = mug_facts()
    right = replace(left, shot_start=51, shot_end=124)
    arm = plan(left).arm
    neighbours, assets = {}, {}
    for index, facts in enumerate((left, right)):
        key = f"w{index}"
        # An escalated active/full neighbour must keep its own native-to-canvas map.
        chosen_arm = replace(arm, framing="full", crop=None) if index else arm
        crop = replacement._crop(facts, chosen_arm, (1024, 1024))
        neighbours[key] = (
            {
                "id": key,
                "request": {
                    "facts": to_record(facts),
                    "seed": 7,
                    "analysis_operation": f"a{index}",
                    "tracks": [],
                },
            },
            {
                "seed": 8,
                "snapshot": {
                    "step_spec": {
                        "crop": to_record(crop),
                        "arm": to_record(chosen_arm),
                        "selected_tracks": ["actor"],
                        "window_frames": 73,
                    }
                },
            },
            {"outputs": [{"asset_id": key, "sha256": key}]},
        )
        assets[key] = {
            "id": key,
            "sha256": key,
            "kind": "video",
            "info": {
                "width": crop.canvas[0],
                "height": crop.canvas[1],
                "duration": 73 / 24,
                "frame_rate": "24",
            },
        }
    if invalid == "fps":
        assets["w1"]["info"].update(frame_rate="25", duration=73 / 25)
    elif invalid == "sha256":
        assets["w1"]["sha256"] = "changed"
    elif invalid == "canvas":
        assets["w1"]["info"]["width"] = 2
    elif invalid == "length":
        assets["w1"]["info"]["duration"] = 1
    monkeypatch.setattr(replacement, "_chosen_window", lambda session, key, *args: neighbours[key])
    monkeypatch.setattr(replacement.MediaLibrary, "asset_info", lambda session, key: assets[key])
    choice = replacement.BridgeChoice(2, ("w0", "c0"), ("w1", "c1"))
    if invalid:
        with pytest.raises(replacement.ApplicationError, match="raw render differs"):
            replacement._bridge_data(None, choice, "source")
        return
    facts, bridge, _ = replacement._bridge_data(None, choice, "source")
    assert (facts.shot_start, facts.shot_end, facts.handoff) == (26, 99, "bridge")
    assert bridge["join"] == 2 and bridge["generated"] == [48, 77]
    assert [w["candidate_id"] for w in bridge["windows"]] == ["c0", "c1"]
    assert bridge["windows"][1]["arm"]["framing"] == "full"
    assert bridge["windows"][0]["crop"] != bridge["windows"][1]["crop"]
