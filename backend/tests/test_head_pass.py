"""Real media/SQLite with fake ComfyUI: derived head windows preserve parent pixels."""

import json

import numpy as np
import pytest
from test_replacement_runner import analysed, drain, pcm, read
from test_replacement_runner import work as work  # noqa: PLC0414

from gaiavfx.application import replace_character as replacement
from gaiavfx.application import replacement_steps as pixels
from gaiavfx.application.shot_assembly import shot_versions, start_shot_assembly
from gaiavfx.application.shot_replacement import start_shot_head_pass
from gaiavfx.modules.direction.head import head_track
from gaiavfx.modules.direction.replacement import CropPlan, from_record, parse_replacement_policy
from gaiavfx.shared.errors import ApplicationError


def test_parent_guide_head_composite_checks_and_derived_assembly(work, monkeypatch):
    from gaiavfx.application import operations as operation_app
    from gaiavfx.integrations import minimax_h3

    db, library, runner, assets, film_frames = work
    runner.comfy.window_face = ((100, 110, 136, 215), 0.0)
    analysis, report, request = analysed(work)
    current_policy = replacement.replacement_policy_text()
    start = current_policy.index("[head]")
    old_policy = current_policy[:start] + current_policy[current_policy.index("\n[", start + 1) :]
    current_knowledge = operation_app.knowledge_snapshot(minimax_h3)
    old_knowledge = {k: v for k, v in current_knowledge.items() if k != "head.toml"}
    # A settled body from before head-pass support must still accept a new head pass.
    with monkeypatch.context() as frozen:
        frozen.setattr(replacement, "replacement_policy_text", lambda: old_policy)
        frozen.setattr(operation_app, "knowledge_snapshot", lambda integration: old_knowledge)
        with db.transaction() as session:
            body = replacement.start_replacement(session, request, library=library, renders=1)
    assert parse_replacement_policy(body["request"]["policy"]).head is None
    assert "head.toml" not in body["request"]["knowledge"]
    drain(runner)
    body = read(db, body["id"])
    assert body["state"] == "succeeded"
    with db.transaction() as session:
        assembly = start_shot_assembly(
            session,
            library,
            source_asset=assets[0],
            shot_start=1,
            shot_end=52,
            window_operations=[body["id"]],
            request_key="body-assembly",
        )
    drain(runner)
    with db.transaction() as session:
        parent = shot_versions(session, library, assembly["id"])[0]
        parent_candidate = replacement.operations.candidate(
            session, parent["windows"][0]["candidate_id"]
        )
        parent_path = library.path(session, parent_candidate["asset_id"])
        head_request = {**request, "head": {"parent_asset": parent_candidate["asset_id"]}}
        for renders in (None, 2):
            with pytest.raises(ApplicationError) as exc:
                replacement.start_replacement(
                    session, head_request, library=library, renders=renders
                )
            assert exc.value.status == 422
        operations = start_shot_head_pass(
            session,
            library,
            source_asset=assets[0],
            window_analyses=[analysis["id"]],
            performer={
                "tracks": {analysis["id"]: request["performers"][0]["track_id"]},
                "target_asset": assets[1],
                "selected_by": "user",
            },
            parent_version=parent,
            seed=1007,
            request_key="head-window",
        )
        op = operations[0]
        assert op["request"]["policy"] == current_policy
        assert op["request"]["knowledge"] == current_knowledge
        assert op["request"]["first_plan"]["arm"]["framing"] == "head"
        assert (
            op["request"]["head"]["parent_sha256"]
            == library.asset_info(session, parent_candidate["asset_id"])["sha256"]
        )
        assert (
            op["request"]["sources"][parent_candidate["asset_id"]]["sha256"]
            == op["request"]["head"]["parent_sha256"]
        )
    assert op["budget_reserved"] == 1
    assert sum(j["executor"] == "comfy" and j["kind"] == "video" for j in op["jobs"]) == 1
    drain(runner)
    op = read(db, op["id"])
    assert op["state"] == "succeeded", op
    assert len(op["candidates"]) == len(op["review"]) == 1
    assert op["review"][0]["decided_by"] == "advancer" and op["review"][0]["arm"] is None
    assert {c["check"] for c in op["review"][0]["attempts"][0]["takes"][0]} >= {
        "residual_source",
        "arcface_target",
        "mouth_aperture",
    }
    prepare = next(j for j in op["jobs"] if j["step"] == "replacement_prepare")
    composite = next(j for j in op["jobs"] if j["step"] == "replacement_composite")
    plan = from_record(CropPlan, composite["snapshot"]["step_spec"]["crop"])
    head_policy = parse_replacement_policy(op["request"]["policy"]).head
    with db.transaction() as session:
        guide_path = library.path(session, prepare["outputs"][0]["asset_id"])
        result_path = library.path(session, op["candidates"][0]["asset_id"])
        source_path = library.path(session, assets[0])
        evidence = {
            o["output_key"]: library.artifact_path(session, o["artifact_id"])
            for o in composite["outputs"]
            if o.get("artifact_id")
        }
    parent_frames = pixels.decode_rgb(library.media, parent_path)
    guide = pixels.decode_rgb(library.media, guide_path)
    assert np.array_equal(guide[0], pixels.crop(parent_frames[0], plan))
    assert not np.array_equal(guide[0], pixels.crop(film_frames[1], plan))
    result = pixels.decode_rgb(library.media, result_path)
    track = report["tracks"][0]
    masks = pixels._mask_data(library, track["mask_artifact"])
    squares = head_track(track["mouth"]["box"], track["presence"], head_policy)
    support = np.stack(
        [
            pixels.head_alpha(m, square, plan, head_policy) > 0
            for m, square in zip(masks, squares, strict=True)
        ]
    )
    assert np.array_equal(result[~support], parent_frames[~support])
    assert np.any(result[support] != parent_frames[support])
    assert np.array_equal(result[:, :20], parent_frames[:, :20])
    assert np.array_equal(result[:, -20:], parent_frames[:, -20:])
    assert pcm(library.media, result_path) == pcm(library.media, source_path, 1, 52)
    for key in ("candidate_pixels", "source_pixels", "performer_mask_0"):
        assert pixels.decode_rgb(library.media, evidence[key]).shape == (51, 512, 512, 3)
    assert np.array_equal(
        pixels.decode_rgb(library.media, evidence["source_pixels"])[0],
        pixels.crop(film_frames[1], plan),
    )
    checks = json.loads(evidence["checks"].read_text())
    assert checks["head_mask"] == {"mask_dilate_px": 12, "mask_feather_px": 16}
    assert checks["letterbox_difference"]["changed_channels"] == 0
    provenance = {
        "assembly_op": assembly["id"],
        "version_id": parent["id"],
        "take": parent["take"],
        "track_id": track["id"],
    }
    with db.transaction() as session:
        derived = start_shot_assembly(
            session,
            library,
            source_asset=assets[0],
            shot_start=1,
            shot_end=52,
            window_operations=[op["id"]],
            request_key="head-assembly",
            layer="head",
            parent=provenance,
        )
    drain(runner)
    with db.transaction() as session:
        version = shot_versions(session, library, derived["id"])[0]
        assert version["layer"] == "head" and version["parent"] == provenance
        assert shot_versions(session, library, assembly["id"])[0] == parent
        parent_patch = library.artifact_path(session, parent["patch_artifact"])
        derived_patch = library.artifact_path(session, version["patch_artifact"])
    # Patches are tight rectangles; reconstruct against film as conform does.

    def reconstruct(path):
        manifest = path.with_name(path.stem + "-test.json")
        library.media.run(
            library.media.ffmpeg,
            [
                "-y",
                "-dump_attachment:t:0",
                str(manifest),
                "-i",
                str(path),
                "-t",
                "0",
                "-f",
                "null",
                "-",
            ],
        )
        meta = json.loads(manifest.read_text("utf-8"))
        frames = film_frames[1:52].copy()
        x0, y0, x1, y1 = meta["rect"]
        for index, patch in enumerate(pixels.iter_rgb(library.media, path, alpha=True)):
            region = frames[index, y0:y1, x0:x1]
            region[patch[..., 3] > 0] = patch[..., :3][patch[..., 3] > 0]
        return frames

    assembled_parent, assembled_head = reconstruct(parent_patch), reconstruct(derived_patch)
    assert np.array_equal(assembled_parent, parent_frames)
    assert np.array_equal(assembled_head[~support], assembled_parent[~support])
    assert np.any(assembled_head[support] != assembled_parent[support])


def test_old_analysis_has_typed_gap_without_creating_operation(work):
    db, library, _, _, _ = work
    _, _, request = analysed(work)
    request["head"] = {"parent_asset": "not-read-before-planning"}
    with db.transaction() as session:
        before = replacement.operations.recent(session, kind="character_replacement")
        with pytest.raises(replacement.ReplacementPlanningError) as exc:
            replacement.start_replacement(session, request, library=library, renders=1)
        assert exc.value.gaps[0].code == "no_face_track"
        assert replacement.operations.recent(session, kind="character_replacement") == before


def test_third_window_gap_rolls_back_first_two_operations(work, monkeypatch):
    from gaiavfx.application import shot_replacement as shot
    from gaiavfx.modules.direction.replacement import Gap
    from gaiavfx.modules.direction.windows import shot_windows
    from gaiavfx.modules.generation.operations.lifecycle import JobSpec
    from gaiavfx.modules.generation.operations.service import OperationService

    db, library = work[:2]
    reports = {
        str(i): {"source_asset": "source", "shot_start": a, "shot_end": b}
        for i, (a, b, _) in enumerate(shot_windows(0, 160))
    }
    parents = {
        "parent" + i: {"id": "parent" + i, "request": {"analysis_operation": i, "facts": r}}
        for i, r in reports.items()
    }
    monkeypatch.setattr(shot, "_report", lambda session, op, library: reports[op["id"]])
    monkeypatch.setattr(
        shot.operations, "operation", lambda session, key: parents.get(key, {"id": key})
    )
    monkeypatch.setattr(
        shot.operations, "candidate", lambda session, key: {"asset_id": key, "operation_id": key}
    )
    ops = OperationService()
    seen = []

    def start(session, request, budget, request_key, *, library, renders):
        assert renders == 1 and request.head is not None
        seen.append(request_key)
        if request.analysis_operation == "2":
            raise replacement.ReplacementPlanningError(
                (Gap("no_face_track", "No usable face track."),)
            )
        return ops.create(
            session,
            kind="character_replacement",
            request={},
            request_key=request_key,
            jobs=[JobSpec("video", "local", "test", step="test")],
            budget=1,
            budget_limit=None,
        )

    monkeypatch.setattr(shot, "start_replacement", start)
    with db.transaction() as session:
        with pytest.raises(replacement.ReplacementPlanningError):
            start_shot_head_pass(
                session,
                library,
                source_asset="source",
                window_analyses=list(reports),
                performer={"tracks": dict.fromkeys(reports, "person"), "target_asset": "target"},
                parent_version={
                    "windows": [{"operation_id": p, "candidate_id": p} for p in parents]
                },
                seed=1000,
                request_key="atomic-head",
            )
        assert len(seen) == 3
        assert ops.recent(session, kind="character_replacement") == []
        assert seen[0] == shot._key("atomic-head", 0, "window-head")
        assert seen[0] != shot._key("atomic-head", 0, "window-replacement")


def test_head_canvas_checks_keep_calibration_and_blind_reasons():
    from types import SimpleNamespace

    from test_replacement_checks import report

    from gaiavfx.application.replacement_checks import take_checks
    from gaiavfx.modules.direction.application import default_replacement_policy

    composite = {"snapshot": {"step_spec": {"face_measurement": True}}}
    check_job = {
        "depends_on": 0,
        "snapshot": {"face_check": {"performer_0": "one"}},
        "state": "succeeded",
        "outputs": [{"output_key": "analysis", "artifact_id": "check"}],
    }
    operation = {
        "request": {
            "head": {"parent_asset": "parent"},
            "facts": {"performers": [{"track_id": "one"}]},
        },
        "jobs": [composite, check_job],
    }
    library = SimpleNamespace(
        read_blob=lambda *args: json.dumps({"performers": {"one": report(0.34)}})
    )
    for blind in (False, True):
        checks = {
            c.check: c
            for c in take_checks(
                None,
                library,
                operation,
                composite,
                {
                    "checks": [
                        {
                            "check": "inverted_copy",
                            "verdict": "unsupported",
                            "advice": "none",
                            "measured": -0.99,
                            "reason": "measured on the head canvas; uncalibrated for head passes",
                        }
                    ]
                },
                default_replacement_policy(),
                blind=blind,
            )
        }
        assert checks["residual_source"].verdict == "fail"
        assert checks["residual_source"].measured == 0.34
        assert ("measured on the head canvas" in checks["residual_source"].reason) is not blind
        for name, value in (("arcface_target", 0.2), ("mouth_aperture", 0.8)):
            assert checks[name].verdict == "unsupported" and checks[name].measured == value
            assert "performer 1" in checks[name].reason
        assert checks["inverted_copy"].measured == -0.99
        assert checks["inverted_copy"].verdict == "unsupported"
        assert ("head canvas" in checks["inverted_copy"].reason) is not blind
