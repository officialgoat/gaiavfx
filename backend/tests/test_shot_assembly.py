import hashlib
import json
import sys
import types

import numpy as np
import pytest
from test_fractional_fps import make_work
from test_replacement_runner import drain, read

from gaiavfx.application.replacement_steps import iter_rgb
from gaiavfx.application.shot_assembly import (
    aggregate_checks,
    assemble_frames,
    crossfade,
    shot_versions,
    start_shot_assembly,
)
from gaiavfx.application.shot_replacement import (
    start_shot_replacement,
    start_window_analyses,
)
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.direction.replacement import CheckResult
from gaiavfx.modules.direction.windows import shot_windows
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError


def test_crossfade_has_exact_endpoints_weights_and_preserves_equal_pixels():
    left = np.full((22, 4, 6, 3), 21, np.uint8)
    right = np.full_like(left, 231)
    left[:, 0] = right[:, 0] = 193
    result = crossfade(left, right)
    assert np.array_equal(result[0], left[0])
    assert np.array_equal(result[-1], right[-1])
    assert (result[:, 0] == 193).all()
    assert np.array_equal(result[:, 1, 0, 0], 21 + np.arange(22) * 10)


def test_aggregate_checks_are_blind_safe_and_worst_verdict_wins():
    def check(verdict):
        return {
            "check": "seam",
            "verdict": verdict,
            "advice": "none",
            "measured": 1,
            "reason": "secret seed 83 and treatment inverted",
        }

    assert (
        aggregate_checks([[check("pass")], [check("unsupported")]])[0]["verdict"] == "unsupported"
    )
    result = aggregate_checks([[check("fail")], [check("unsupported")]])
    assert result[0]["verdict"] == "fail"
    assert result[0]["calibrated"] is False
    assert "secret" not in json.dumps(result) and "inverted" not in json.dumps(result)


def test_stream_encoder_failure_closes_the_frame_iterator_and_process(tmp_path):
    from gaiavfx.application.replacement_steps import lossless_video
    from gaiavfx.infrastructure.media import MediaTools

    closed = []

    def frames():
        try:
            for _ in range(73):
                yield np.zeros((32, 32, 4), np.uint8)
        finally:
            closed.append(True)

    media = MediaTools()
    with pytest.raises(ApplicationError):
        lossless_video(
            media,
            frames(),
            tmp_path / "broken.mkv",
            24,
            size=(32, 32),
            frame_count=73,
            alpha=True,
            attachment=tmp_path / "missing.json",
        )
    assert closed == [True]
    assert not media._processes


@pytest.mark.parametrize("state", ["partial", "awaiting_review"])
def test_assembly_accepts_partial_windows_only_after_the_loop_settles(monkeypatch, state):
    from gaiavfx.application import shot_assembly as assembly

    windows = {}
    for index, (start, end, _) in enumerate(shot_windows(0, 160)):
        key = str(index)
        windows[key] = {
            "id": key,
            "kind": "character_replacement",
            "state": state,
            "request": {
                "facts": {"source_asset": "source", "shot_start": start, "shot_end": end},
                "seed": 7,
                "policy": replacement_policy_text(),
            },
            "candidates": [{"id": key, "job_id": key, "asset_id": key}],
            "jobs": [
                {
                    "id": key,
                    "step": "replacement_composite",
                    "state": "succeeded",
                    "seed": 7,
                    "snapshot": {
                        "step_spec": {"arm": {"treatment": "rgb", "framing": "crop", "round": 0}}
                    },
                    "outputs": [{"output_key": "checks", "artifact_id": key}],
                }
            ],
        }
    monkeypatch.setattr(assembly.operations, "operation", lambda session, key: windows[key])
    monkeypatch.setattr(assembly.operations, "create", lambda session, **kwargs: kwargs)
    monkeypatch.setattr(
        assembly,
        "take_checks",
        lambda *args, **kwargs: (CheckResult("residual_source", "unsupported", "none", None),),
    )
    library = types.SimpleNamespace(
        asset_info=lambda *args: {"sha256": "frozen", "info": {"frame_rate": "24"}},
        read_blob=lambda *args: b"{}",
    )
    args = {
        "source_asset": "source",
        "shot_start": 0,
        "shot_end": 160,
        "window_operations": list(windows),
        "request_key": "assembly",
    }
    if state == "awaiting_review":
        with pytest.raises(ApplicationError, match="must settle"):
            start_shot_assembly(None, library, **args)
    else:
        result = start_shot_assembly(None, library, **args)
        assert len(result["request"]["versions"]) == 1 and result["budget"] == 0
        assert result["request"]["versions"][0]["checks"][0]["verdict"] == "unsupported"


def test_completed_versions_survive_another_assembly_job_failure(monkeypatch):
    from gaiavfx.application import shot_assembly as assembly

    operation = {
        "kind": "shot_assembly",
        "state": "partial",
        "request": {"versions": [{"take": 0}, {"take": 1}]},
        "jobs": [
            {
                "id": "complete",
                "state": "succeeded",
                "outputs": [
                    {"output_key": "preview", "asset_id": "preview"},
                    {"output_key": "patch", "artifact_id": "patch"},
                ],
            },
            {"id": "failed", "state": "failed", "outputs": []},
        ],
    }
    monkeypatch.setattr(assembly.operations, "operation", lambda *args: operation)
    assert shot_versions(None, None, "assembly") == [
        {
            "take": 0,
            "id": "complete",
            "preview_asset": "preview",
            "patch_artifact": "patch",
            "layer": None,
            "parent": None,
        }
    ]


def film_fixture(db, library, runner, assets, tmp_path, monkeypatch):
    count = 190
    faces = tmp_path / "fixture-faces.npz"
    embeddings = np.zeros((count, 512), np.float16)
    embeddings[:, 0] = 1
    np.savez(
        faces,
        frame=np.arange(count, dtype=np.int32),
        box=np.tile([39, 50, 49, 60], (count, 1)).astype(np.float32),
        embedding=embeddings,
        score=np.ones(count, np.float32),
        kps=np.zeros((count, 5, 2), np.float32),
    )

    report = {
        "schema": 1,
        "source_asset": assets[0],
        "fps": [24, 1],
        "frame_count": count,
        "tracks": [{"id": "film-person", "faces": list(range(count))}],
        "clusters": [{"id": "c01", "track_ids": ["film-person"]}],
    }

    def chunk(context):
        # A per-chunk face report, as the real chunked scan emits: never the faces source.
        context.staging.mkdir(parents=True, exist_ok=True)
        partial = context.staging / "chunk-faces.npz"
        np.savez(partial, frame=np.zeros(1, np.int32))
        return [("faces:reports:0", partial, "analysis_report")]

    def step(context):
        context.staging.mkdir(parents=True, exist_ok=True)
        target = context.staging / "faces.npz"
        target.write_bytes(faces.read_bytes())
        cast = context.staging / "cast.json"
        cast.write_text(json.dumps(report), "utf-8")
        return [("faces", target, "analysis_report"), ("cast", cast, "analysis_report")]

    runner.steps["fixture_chunk"] = chunk
    runner.steps["film_cast"] = step
    with db.transaction() as session:
        operation = OperationService().create(
            session,
            kind="film_analysis",
            request={},
            jobs=[
                JobSpec("analysis", "local", "fixture", step="fixture_chunk"),
                JobSpec("analysis", "local", "fixture", step="film_cast", depends_on=0),
            ],
            budget_limit=None,
            budget=0,
        )
    drain(runner)
    module = types.ModuleType("gaiavfx.application.film_analysis")
    module.film_analysis_report = lambda session, library, operation_id: report
    monkeypatch.setitem(sys.modules, module.__name__, module)
    return operation


@pytest.mark.parametrize("mode,count", [("draft", 1), ("final", 2)])
def test_three_window_operations_assembly_and_lossless_patch(tmp_path, monkeypatch, mode, count):
    from test_replacement_runner import natural_colours

    from gaiavfx.application import replace_character as replacement
    from gaiavfx.application import replacement_steps

    monkeypatch.setattr(replacement_steps, "inversion_score", natural_colours)
    db, library, runner, assets, frames = make_work(tmp_path)
    try:
        film = film_fixture(db, library, runner, assets, tmp_path, monkeypatch)
        # Force a likeness retry in the first final window. It must keep RGB,
        # even though the ordinary policy would switch this arm to inversion.
        if mode == "final":
            from gaiavfx.application import replace_character as replacement

            policy = replacement_policy_text()
            monkeypatch.setattr(replacement, "replacement_policy_text", lambda: policy)
            original_checks = replacement.take_checks

            def checks(session, library, operation, job, report, policy, **kwargs):
                if (
                    operation["request"]["facts"]["shot_start"] == 13
                    and job["snapshot"]["step_spec"]["arm"]["round"] == 0
                ):
                    return (CheckResult("residual_source", "fail", "none", 0.8),)
                return original_checks(session, library, operation, job, report, policy, **kwargs)

            monkeypatch.setattr(replacement, "take_checks", checks)
        with db.transaction() as session:
            analyses = start_window_analyses(
                session,
                library,
                source_asset=assets[0],
                shot_start=13,
                shot_end=173,
                faces_operation=film["id"],
                request_key="shot",
            )
        assert len(analyses) == 3
        assert all(a["budget_reserved"] == 0 for a in analyses)
        assert all("film_report" not in json.dumps(a) for a in analyses)
        assert all(len(json.dumps(a["request"])) < 1500 for a in analyses)
        drain(runner)
        performer = {"tracks": {}, "target_asset": assets[1]}
        with db.transaction() as session:
            for analysis in analyses:
                operation = read(db, analysis["id"])
                assert operation["state"] == "succeeded", operation
                artifact = operation["jobs"][-1]["outputs"][0]["artifact_id"]
                report = json.loads(library.read_blob(session, artifact))
                track = report["tracks"][0]
                assert track["identity"]["cluster"] == "c01"
                assert track["identity"]["film_tracks"] == {
                    "film-person": report["shot_end"] - report["shot_start"]
                }
                assert track["identity_margin"] == 1.0
                performer["tracks"][analysis["id"]] = track["id"]
            kwargs = {
                "source_asset": assets[0],
                "window_analyses": [a["id"] for a in analyses],
                "performers": [performer],
                "mode": mode,
                "seed": 43,
                "request_key": "shot",
            }
            with pytest.raises(ApplicationError, match="budget"):
                start_shot_replacement(
                    session, library, **{**kwargs, "request_key": "over-budget"}, budget_limit=0
                )
            assert OperationService().recent(session, kind="character_replacement") == []
            windows = start_shot_replacement(session, library, **kwargs)
            assert [w["id"] for w in start_shot_replacement(session, library, **kwargs)] == [
                w["id"] for w in windows
            ]
            source = library.path(session, assets[0])
            source_before = library.get(session, assets[0])
        assert len(windows) == 3
        assert all(
            not w["request"].get("chain") and w["request"]["facts"]["context_frames"] == 0
            for w in windows
        )
        digest = hashlib.sha256(source.read_bytes()).hexdigest()
        assert all(w["budget_reserved"] == (2 if mode == "draft" else 4) for w in windows)
        assert all(w["request"]["first_seeds"] == list(range(43, 43 + count)) for w in windows)
        assert len({w["request"]["first_plan"]["arm"]["treatment"] for w in windows}) == 1
        drain(runner)
        assert all(read(db, w["id"])["state"] == "succeeded" for w in windows)
        treatments = {
            j["snapshot"]["step_spec"]["arm"]["treatment"]
            for w in windows
            for j in read(db, w["id"])["jobs"]
            if j["step"] == "replacement_composite"
        }
        assert len(treatments) == 1
        if mode == "final":
            assert read(db, windows[0]["id"])["review"][0]["arm"]["treatment"] == "rgb"
        from gaiavfx.application.shot_assembly import chain_take, window_takes
        from gaiavfx.application.shot_replacement import start_shot_bridge
        from gaiavfx.integrations.minimax_h3 import capabilities
        from gaiavfx.modules.direction.replacement import (
            ReplacementFacts,
            from_record,
            parse_replacement_policy,
            to_record,
            union_crop,
        )

        bridge_ops, joins = [], []
        with db.transaction() as session:
            neighbours = [read(db, w["id"]) for w in windows]
            selected = [chain_take(session, library, o) for o in neighbours]
            for join in range(1, len(windows)):
                shared = union_crop(
                    [
                        from_record(ReplacementFacts, neighbours[i]["request"]["facts"])
                        for i in (join - 1, join)
                    ],
                    parse_replacement_policy(neighbours[join]["request"]["policy"]),
                    capabilities().replacement,
                )
                joins.append({"join": join, "kind": "bridge", "crop": to_record(shared)})
                successors = (
                    window_takes(session, library, neighbours[join]).values()
                    if join == len(windows) - 1
                    else [selected[join]]
                )
                for index, successor in enumerate(successors):
                    bridge = start_shot_bridge(
                        session,
                        library,
                        source_asset=assets[0],
                        predecessor=selected[join - 1],
                        successor=successor,
                        join=join,
                        crop=to_record(shared),
                        seed=43,
                        request_key=f"bridge-{join}-{index}",
                    )
                    assert bridge["budget_reserved"] == 1
                    assert bridge["request"]["facts"]["handoff"] == "bridge"
                    assert bridge["jobs"][1]["graph"]["conditioning"]["inputs"]["length"] == 73
                    bridge_ops.append(bridge)
        drain(runner)
        assert all(read(db, b["id"])["state"] == "succeeded" for b in bridge_ops), [
            read(db, b["id"]) for b in bridge_ops
        ]
        with db.transaction() as session:
            kwargs = {
                "source_asset": assets[0],
                "shot_start": 13,
                "shot_end": 173,
                "window_operations": [w["id"] for w in windows],
                "request_key": "assembly",
                "bridge_operations": [b["id"] for b in bridge_ops],
                "handoff": {"kind": "bridge", "joins": joins},
            }
            assembly = start_shot_assembly(session, library, **kwargs)
            assert start_shot_assembly(session, library, **kwargs)["id"] == assembly["id"]
            assert shot_versions(session, library, assembly["id"]) == []
        assert assembly["budget_reserved"] == 0
        posts = len(runner.comfy.posts)
        drain(runner)
        assert len(runner.comfy.posts) == posts
        assembly = read(db, assembly["id"])
        assert assembly["state"] == "succeeded", assembly
        with db.transaction() as session:
            versions = shot_versions(session, library, assembly["id"])
            assert len(versions) == count
            for version, job in zip(versions, assembly["jobs"], strict=True):
                assert len({w["treatment"] for w in version["windows"]}) == 1
                assert len(version["bridges"]) == 2
                assert all(j["handoff"] == "bridge" for j in version["joins"])
                artifact = library.artifact(session, version["patch_artifact"])
                assert artifact["kind"] == "replacement_patch"
                preview = library.get(session, version["preview_asset"])
                assert preview["info"]["has_audio"]
                assert any(p["type"] == "shot_assembly" for p in preview["provenance"])
                spec = job["snapshot"]["step_spec"]
                paths = {
                    w["asset"]: library.path(session, w["asset"])
                    for w in [*spec["windows"], *spec["bridges"]]
                }
                expected = np.stack(
                    list(assemble_frames(library.media, paths, spec["windows"], spec["bridges"]))
                )
                restored = frames[13:173].copy()
                patch = library.artifact_path(session, artifact["id"])
                manifest_path = tmp_path / f"patch-{version['take']}.json"
                library.media.run(
                    library.media.ffmpeg,
                    [
                        "-y",
                        "-dump_attachment:t:0",
                        str(manifest_path),
                        "-i",
                        str(patch),
                        "-t",
                        "0",
                        "-f",
                        "null",
                        "-",
                    ],
                )
                manifest = json.loads(manifest_path.read_text("utf-8"))
                assert (
                    manifest["start"] == 13
                    and manifest["end"] == 173
                    and manifest["fps"] == [24, 1]
                )
                x0, y0, x1, y1 = manifest["rect"]
                assert all(v % 2 == 0 for v in manifest["rect"])
                for index, rgba in enumerate(iter_rgb(library.media, patch, alpha=True)):
                    rgb, alpha = rgba[:, :, :3], rgba[:, :, 3] > 0
                    restored[index, y0:y1, x0:x1][alpha] = rgb[alpha]
                assert np.array_equal(restored, expected)
                outside = np.ones(restored.shape[1:3], bool)
                outside[y0:y1, x0:x1] = False
                assert outside.any(), "fixture must exercise pixels outside the patch"
                assert np.array_equal(expected[:, outside], frames[13:173, outside])
            assert library.get(session, assets[0]) == source_before
        assert hashlib.sha256(source.read_bytes()).hexdigest() == digest
    finally:
        db.close()


def test_469_frame_1080p_assembly_has_bounded_peak_memory(tmp_path, monkeypatch):
    import tracemalloc
    from contextlib import contextmanager

    from gaiavfx.application import shot_assembly as assembly

    windows = [
        {"start": a, "end": b, "asset": str(i)} for i, (a, b, _) in enumerate(shot_windows(0, 469))
    ]

    def decode(media, path, start=0, end=None, **kwargs):
        for _ in range(end - start):
            frame = np.zeros((1080, 1920, 3), np.uint8)
            if path != "source":
                frame[400:600, 700:900] = int(path) + 1
            yield frame

    class Sink:
        frames = 0

        def write(self, data):
            self.frames += 1

    sink = Sink()

    @contextmanager
    def writer(*args):
        yield sink

    def encode(media, frames, target, fps, **kwargs):
        assert kwargs["alpha"] and kwargs["frame_count"] == 469
        assert sum(1 for _ in frames) == 469

    monkeypatch.setattr(assembly, "iter_rgb", decode)
    monkeypatch.setattr(assembly, "video_writer", writer)
    monkeypatch.setattr(assembly, "lossless_video", encode)
    media = types.SimpleNamespace(
        probe=lambda path: {"width": 1920, "height": 1080, "has_audio": False}
    )
    context = types.SimpleNamespace(
        library=types.SimpleNamespace(media=media),
        staging=tmp_path,
        paths={"source": "source", **{w["asset"]: w["asset"] for w in windows}},
        snapshot={
            "step_spec": {
                "source_asset": "source",
                "start": 0,
                "end": 469,
                "fps": "24",
                "windows": windows,
            }
        },
    )
    tracemalloc.start()
    try:
        assembly.shot_assembly(context)
        peak = tracemalloc.get_traced_memory()[1]
    finally:
        tracemalloc.stop()
    assert sink.frames == 469
    assert peak < 384 * 1024 * 1024, peak


@pytest.mark.parametrize(
    "selected_by,margin", [("identity", None), ("identity", 0.7), ("user", 0.7)]
)
def test_shot_user_confirmation_and_typed_gap_rollback(monkeypatch, selected_by, margin):
    from contextlib import nullcontext

    from gaiavfx.application import shot_replacement as shot
    from gaiavfx.application.replace_character import ReplacementPlanningError
    from gaiavfx.modules.direction.replacement import Gap

    reports = {
        str(i): {"source_asset": "source", "shot_start": a, "shot_end": b}
        for i, (a, b, _) in enumerate(shot_windows(0, 160))
    }
    monkeypatch.setattr(shot.operations, "operation", lambda session, key: reports[key])
    monkeypatch.setattr(shot, "_report", lambda session, report, library: report)
    seen = []

    def initial(session, request, library, renders=None, **kwargs):
        if (
            request.performers[0].selected_by == "identity"
            and request.performers[0].identity_margin is None
        ):
            return (
                None,
                None,
                None,
                types.SimpleNamespace(
                    arm=None, gaps=(Gap("identity_conflict", "Confirm this appearance."),)
                ),
            )
        return (
            types.SimpleNamespace(close_up=False),
            None,
            replacement_policy_text(),
            types.SimpleNamespace(arm=types.SimpleNamespace(treatment="rgb", faceswap=False)),
        )

    def start(session, request, *args, **kwargs):
        seen.append(request)
        operation = {
            "id": f"window{len(seen) - 1}",
            "request": {
                "policy": "frozen-policy",
                "knowledge": {"render.toml": "frozen-render"},
                "first_plan": {"arm": {"treatment": request.treatment_override}},
            },
        }
        reports[operation["id"]] = operation
        assert request.handoff is None
        return operation

    monkeypatch.setattr(shot, "_initial", initial)
    monkeypatch.setattr(shot, "start_replacement", start)
    args = {
        "source_asset": "source",
        "window_analyses": list(reports),
        "performers": [
            {
                "tracks": dict.fromkeys(reports, "sam"),
                "target_asset": "target",
                "selected_by": selected_by,
                "margins": dict.fromkeys(reports, margin),
            }
        ],
        "mode": "final",
        "seed": 9,
        "request_key": "same",
    }
    session = types.SimpleNamespace(begin_nested=nullcontext)
    if selected_by == "identity" and margin is None:
        with pytest.raises(ReplacementPlanningError) as error:
            shot.start_shot_replacement(session, None, **args)
        assert error.value.gaps[0].code == "identity_conflict" and error.value.status == 409
        assert not seen
    else:
        windows = shot.start_shot_replacement(session, None, **args)
        assert len(windows) == 3
        assert len(seen) == 3
        assert all(
            r.performers[0].selected_by == selected_by
            and r.performers[0].identity_margin == margin
            and r.treatment_override == "rgb"
            for r in seen
        )
    assert shot._key("same", 0, "window-analysis") != shot._key("same", 0, "window-replacement")


@pytest.mark.parametrize("explicit_take", [False, True])
@pytest.mark.parametrize("confirmed_window", [False, True])
def test_latent_continuation_reuses_frozen_crop_policy_and_selection(
    monkeypatch, explicit_take, confirmed_window
):
    from test_replacement_contract import mug_facts, plan

    from gaiavfx.application import shot_replacement as shot
    from gaiavfx.modules.direction.replacement import to_record

    crop = plan(mug_facts()).arm.crop
    first = {
        "id": "w0",
        "request": {
            "save_latent": True,
            "crop_override": to_record(crop),
            "policy": "frozen-policy",
            "knowledge": {"render.toml": "frozen-render"},
            "first_plan": {"arm": {"treatment": "rgb"}},
        },
    }
    take, choices = {"window": {"candidate_id": "chosen"}}, []
    monkeypatch.setattr(shot.operations, "operation", lambda *args: first)
    monkeypatch.setattr(shot, "chain_take", lambda *args: choices.append(True) or take)
    monkeypatch.setattr(
        shot, "start_replacement", lambda session, request, *args, **kwargs: (request, kwargs)
    )
    request, settings = shot.continue_shot_replacement(
        None,
        None,
        source_asset="film",
        window_analyses=["a0", "a1"],
        window_operations=["w0"],
        performers=[
            {
                "tracks": {"a0": "p0", "a1": "p1"},
                "target_asset": "target",
                "selected_by": "identity",
                "margins": {"a1": 0.7},
                **({"selections": {"a1": "user"}} if confirmed_window else {}),
            }
        ],
        mode="final",
        seed=9,
        request_key="same",
        **({"predecessor_take": take} if explicit_take else {}),
    )
    assert len(choices) == (0 if explicit_take else 1)
    assert request.crop_override == crop and request.treatment_override == "rgb"
    assert request.handoff == shot.HandoffChoice("w0", "chosen")
    assert request.performers[0].track_id == "p1"
    assert request.performers[0].identity_margin == 0.7
    assert request.performers[0].selected_by == ("user" if confirmed_window else "identity")
    assert settings["continuation_snapshot"] is first["request"]


@pytest.mark.parametrize(
    "verdicts,expected",
    [
        ([["fail"], ["pass"], ["pass"]], 1),
        ([["fail", "fail"], ["fail"]], 1),
        ([["unsupported"], ["unsupported"]], 0),
        ([["unsupported"], ["pass"]], 1),
        ([], None),
    ],
)
def test_chain_take_uses_calibrated_checks_only(monkeypatch, verdicts, expected):
    from gaiavfx.application import shot_assembly as assembly

    takes = {
        i: {
            "checks": [{"verdict": v, "advice": "retry"} for v in row],
            "window": {"candidate_id": str(i)},
        }
        for i, row in enumerate(verdicts)
    }
    monkeypatch.setattr(assembly, "window_takes", lambda *args: takes)
    result = assembly.chain_take(None, None, {})
    assert (result["take"] if result else None) == expected


@pytest.mark.parametrize(
    "stopped,mismatch,unchained",
    [
        (False, None, False),
        (False, "predecessor", False),
        (False, "sha256", False),
        (True, None, False),
        (False, None, True),
    ],
)
def test_chained_versions_follow_candidate_identity(monkeypatch, stopped, mismatch, unchained):
    from gaiavfx.application import shot_assembly as assembly

    ops, tables = {}, {}
    for i, (start, end, _) in enumerate(shot_windows(0, 175)):
        key = f"op{i}"
        ops[key] = {
            "id": key,
            "kind": "character_replacement",
            "state": "succeeded",
            "request": {
                "facts": {"source_asset": "source", "shot_start": start, "shot_end": end},
                **(
                    {
                        "chain": {
                            "predecessor_operation": f"op{i - 1}",
                            "candidate_id": f"c{i - 1}-1",
                            "sha256": "frozen",
                        }
                    }
                    if i
                    else {}
                ),
            },
        }
        tables[key] = {
            t: {
                "asset": f"asset{i}-{t}",
                "checks": [],
                "window": {
                    "operation_id": key,
                    "candidate_id": f"c{i}-{t}",
                    "seed": 7 + t,
                },
            }
            for t in (0, 1)
        }
    if unchained:
        for op in ops.values():
            op["request"].pop("chain", None)
        del tables["op1"][0]
    if mismatch == "predecessor":
        ops["op2"]["request"]["chain"]["predecessor_operation"] = "op0"
    elif mismatch == "sha256":
        ops["op2"]["request"]["chain"]["sha256"] = "changed"
    monkeypatch.setattr(assembly.operations, "operation", lambda session, key: ops[key])
    monkeypatch.setattr(assembly.operations, "create", lambda session, **kwargs: kwargs)
    monkeypatch.setattr(assembly, "window_takes", lambda session, library, op: tables[op["id"]])
    library = types.SimpleNamespace(
        asset_info=lambda *args: {"sha256": "frozen", "info": {"frame_rate": "24"}}
    )
    args = {
        "source_asset": "source",
        "shot_start": 0,
        "shot_end": 175,
        "window_operations": list(ops)[:2] if stopped else list(ops),
        "request_key": "assembly",
    }
    if mismatch:
        with pytest.raises(ApplicationError) as exc:
            assembly.start_shot_assembly(None, library, **args)
        assert exc.value.status == 422
        return
    stop = {"window": 1, "reason": "no_usable_take"}
    result = assembly.start_shot_assembly(
        None, library, **args, **({"stopped": stop} if stopped else {})
    )
    if unchained:
        assert [v["take"] for v in result["request"]["versions"]] == [1]
        assert [w["candidate_id"] for w in result["request"]["versions"][0]["windows"]] == [
            "c0-1",
            "c1-1",
            "c2-1",
        ]
        assert all("chained_from" not in w for w in result["request"]["versions"][0]["windows"])
    elif stopped:
        assert result["request"]["versions"] == []
        assert result["request"]["stopped"] == stop
        assert [j.step for j in result["jobs"]] == ["shot_assembly_empty"]
    else:
        assert [v["take"] for v in result["request"]["versions"]] == [0, 1]
        for t, version in enumerate(result["request"]["versions"]):
            assert [w["candidate_id"] for w in version["windows"]] == ["c0-1", "c1-1", f"c2-{t}"]
            assert [w["chained_from"] for w in version["windows"]] == [None, "c0-1", "c1-1"]


@pytest.mark.parametrize("kind", ["none", "latent", "crossfade"])
def test_assembly_preserves_plain_record_shape_and_handoff_provenance(monkeypatch, kind):
    from gaiavfx.application import shot_assembly as assembly

    end = 73 if kind == "none" else 124
    ops, tables = {}, {}
    for i, (start, stop, _) in enumerate(shot_windows(0, end)):
        key = f"op{i}"
        request = {"facts": {"source_asset": "film", "shot_start": start, "shot_end": stop}}
        if kind == "latent":
            request.update(
                {"handoff": {"predecessor_operation": "op0", "candidate_id": "c0"}}
                if i
                else {"save_latent": True}
            )
        ops[key] = {
            "id": key,
            "kind": "character_replacement",
            "state": "succeeded",
            "request": request,
        }
        tables[key] = {
            0: {
                "asset": f"asset{i}",
                "checks": [],
                "window": {"operation_id": key, "candidate_id": f"c{i}", "seed": 7},
            }
        }
    monkeypatch.setattr(assembly.operations, "operation", lambda session, key: ops[key])
    monkeypatch.setattr(assembly.operations, "create", lambda session, **kwargs: kwargs)
    monkeypatch.setattr(assembly, "window_takes", lambda session, library, op: tables[op["id"]])
    library = types.SimpleNamespace(
        asset_info=lambda *args: {"sha256": "frozen", "info": {"frame_rate": "24"}}
    )
    result = assembly.start_shot_assembly(
        None,
        library,
        source_asset="film",
        shot_start=0,
        shot_end=end,
        window_operations=list(ops),
        request_key="assembly-shape",
        **(
            {
                "bridge_operations": [],
                "handoff": {
                    "kind": kind,
                    "joins": [],
                    **({"reason": "no_shared_crop"} if kind == "crossfade" else {}),
                },
            }
            if kind != "none"
            else {}
        ),
    )
    version = result["request"]["versions"][0]
    if kind == "none":
        # The complete pre-handoff request and job snapshot shape, including absent keys.
        assert result["request"] == {
            "source_asset": "film",
            "shot_start": 0,
            "shot_end": 73,
            "window_operations": ["op0"],
            "versions": [{"take": 0, "checks": [], "windows": [tables["op0"][0]["window"]]}],
        }
        assert result["jobs"][0].snapshot == {
            "intermediate": True,
            "replacement_source": "film",
            "step_spec": {
                "source_asset": "film",
                "start": 0,
                "end": 73,
                "fps": "24",
                "take": 0,
                "windows": [{"start": 0, "end": 73, "asset": "asset0"}],
            },
        }
    else:
        assert version["joins"] == [
            {
                "join": 1,
                "handoff": kind,
                **({"reason": "no_shared_crop"} if kind == "crossfade" else {}),
            }
        ]
        assert version["bridges"] == []
        assert version["windows"][1]["handoff"] == ("latent" if kind == "latent" else None)


def test_bridge_assembly_streams_delivery_plan_with_two_decoders(monkeypatch):
    from gaiavfx.application import shot_assembly as assembly
    from gaiavfx.modules.direction.windows import bridge_layout, delivery_plan

    windows = [
        {"start": a, "end": b, "asset": str(i)} for i, (a, b, _) in enumerate(shot_windows(0, 175))
    ]
    bridges = [{**b, "asset": str(10 + b["join"])} for b in bridge_layout(windows, 175)]
    sources = {("window", i): w for i, w in enumerate(windows)}
    sources.update({("bridge", b["join"]): b for b in bridges})
    active, peak, starts = set(), [], []

    def decode(media, path, start=0, end=None):
        starts.append((path, start))
        active.add(path)
        peak.append(len(active))
        try:
            for _ in range(start, end):
                yield np.full((2, 2, 3), int(path) * 10, np.uint8)
        finally:
            active.remove(path)

    monkeypatch.setattr(assembly, "iter_rgb", decode)
    paths = {s["asset"]: s["asset"] for s in sources.values()}
    actual = list(assembly.assemble_frames(None, paths, windows, bridges))
    expected = []
    for segment in delivery_plan(windows, bridges):
        left = np.full((2, 2, 3), int(sources[segment["source"]]["asset"]) * 10, np.uint8)
        for frame in range(segment["start"], segment["end"]):
            if segment["right"]:
                right = np.full_like(left, int(sources[segment["right"]]["asset"]) * 10)
                expected.append(
                    assembly._blend_frame(
                        left,
                        right,
                        frame - segment["start"] + segment["weight_start"],
                        segment["denominator"],
                    )
                )
            else:
                expected.append(left)
    assert np.array_equal(actual, expected) and len(actual) == 175
    assert max(peak) == 2 and not active
    assert any(start > 0 for _, start in starts)


def test_head_pass_creates_windows_and_bridge_parents(monkeypatch):
    from contextlib import nullcontext

    from gaiavfx.application import shot_replacement as shot

    ops, candidates, reports = {}, {}, {}
    ranges = shot_windows(0, 175)
    for i, (start, end, _) in enumerate(ranges):
        ops[f"w{i}"] = {
            "id": f"w{i}",
            "request": {
                "analysis_operation": f"a{i}",
                "facts": {"source_asset": "film", "shot_start": start, "shot_end": end},
            },
        }
        ops[f"a{i}"] = {"id": f"a{i}"}
        candidates[f"c{i}"] = {"operation_id": f"w{i}", "asset_id": f"parent{i}"}
        reports[f"a{i}"] = {"source_asset": "film", "shot_start": start, "shot_end": end}
    for j in (1, 2):
        ops[f"b{j}"] = {
            "id": f"b{j}",
            "request": {
                "analysis_operation": f"a{j}",
                "bridge": {
                    "join": j,
                    "windows": [
                        {"operation": f"w{i}", "candidate_id": f"c{i}"} for i in (j - 1, j)
                    ],
                },
            },
        }
        candidates[f"bc{j}"] = {"operation_id": f"b{j}", "asset_id": f"bridge-parent{j}"}
    monkeypatch.setattr(shot.operations, "operation", lambda session, key: ops[key])
    monkeypatch.setattr(shot.operations, "candidate", lambda session, key: candidates[key])
    monkeypatch.setattr(shot, "_report", lambda session, op, library: reports[op["id"]])
    settings = []

    def start(session, request, *args, **kwargs):
        settings.append(kwargs)
        return request

    monkeypatch.setattr(shot, "start_replacement", start)
    parent = {
        "windows": [{"operation_id": f"w{i}", "candidate_id": f"c{i}"} for i in range(3)],
        "bridges": [{"operation_id": f"b{j}", "candidate_id": f"bc{j}"} for j in (1, 2)],
    }
    requests = shot.start_shot_head_pass(
        types.SimpleNamespace(begin_nested=nullcontext),
        None,
        source_asset="film",
        window_analyses=[f"a{i}" for i in range(3)],
        performer={"tracks": {f"a{i}": "person" for i in range(3)}, "target_asset": "target"},
        parent_version=parent,
        seed=7,
        request_key="heads",
    )
    assert len(requests) == 5
    assert [r.head.parent_asset for r in requests] == [
        "parent0",
        "parent1",
        "parent2",
        "bridge-parent1",
        "bridge-parent2",
    ]
    assert [r.bridge.join for r in requests[3:]] == [1, 2]
    assert all("continuation_snapshot" not in s for s in settings)
    assert all(s["renders"] == 1 and not s.get("draft", False) for s in settings)


@pytest.mark.parametrize(
    "failed,head,draft",
    [
        (False, False, False),
        (True, False, False),
        (True, True, False),
        (False, False, True),
        (True, False, True),
    ],
)
def test_versions_bind_each_last_take_to_its_bridge(monkeypatch, failed, head, draft):
    from gaiavfx.application import shot_assembly as assembly
    from gaiavfx.modules.direction.windows import bridge_layout

    windows = shot_windows(0, 175)
    ops, tables = {}, {}

    def add(key, facts, candidates, bridge=None):
        ops[key] = {
            "id": key,
            "kind": "character_replacement",
            "state": "succeeded",
            "request": {"facts": facts, **({"bridge": bridge} if bridge else {})},
        }
        tables[key] = {
            take: {
                "asset": candidate,
                "checks": [{"check": "identity", "verdict": "pass", "advice": "none"}],
                "window": {"operation_id": key, "candidate_id": candidate, "seed": take},
            }
            for take, candidate in enumerate(candidates)
        }

    for i, (start, end, _) in enumerate(windows):
        add(
            f"w{i}",
            {"source_asset": "film", "shot_start": start, "shot_end": end},
            [f"c{i}-0", f"c{i}-1"],
        )
        if draft:
            ops[f"w{i}"]["request"]["draft"] = True
    if draft:
        tables["w2"][0]["checks"][0]["verdict"] = "fail"
    bridge_ids = []
    for layout in bridge_layout(windows, 175):
        join = layout["join"]
        for take in [1] if draft and join == 2 else range(2 if join == 2 and not head else 1):
            key = f"b{join}-{take}"
            bridge_ids.append(key)
            add(
                key,
                {},
                [key],
                {
                    **layout,
                    "windows": [
                        {"candidate_id": f"c{join - 1}-0"},
                        {"candidate_id": f"c{join}-{take}"},
                    ],
                },
            )
    if failed:
        tables[bridge_ids[-1]] = {}
        ops[bridge_ids[-1]]["state"] = "failed"
    monkeypatch.setattr(assembly.operations, "operation", lambda session, key: ops[key])
    monkeypatch.setattr(assembly.operations, "create", lambda session, **kwargs: kwargs)
    monkeypatch.setattr(assembly, "window_takes", lambda session, library, op: tables[op["id"]])
    library = types.SimpleNamespace(
        asset_info=lambda *args: {"sha256": "frozen", "info": {"frame_rate": "24"}}
    )
    result = assembly.start_shot_assembly(
        None,
        library,
        source_asset="film",
        shot_start=0,
        shot_end=175,
        window_operations=[f"w{i}" for i in range(3)],
        bridge_operations=bridge_ids,
        handoff={"kind": "bridge", "joins": [{"join": j, "kind": "bridge"} for j in (1, 2)]},
        layer="head" if head else None,
        request_key="bridged-versions",
    )
    versions = result["request"]["versions"]
    if head:
        assert versions == []  # A head failure must not replace the parent's body bridge.
        assert [j.step for j in result["jobs"]] == ["shot_assembly_empty"]
        return
    assert [v["take"] for v in versions] == ([1] if draft else [0, 1])
    assert len(result["jobs"]) == len(versions)
    for version in versions:
        take = version["take"]
        assert [w["candidate_id"] for w in version["windows"]] == ["c0-0", "c1-0", f"c2-{take}"]
        expected = ["b1-0"] if failed and take == 1 else ["b1-0", f"b2-{take}"]
        assert [b["candidate_id"] for b in version["bridges"]] == expected
        assert version["joins"][-1] == (
            {"join": 2, "handoff": "crossfade", "reason": "bridge_failed"}
            if failed and take == 1
            else {"join": 2, "handoff": "bridge"}
        )
