"""Calibration, per-performer aggregation, and durable measurement jobs."""

import json
from dataclasses import replace
from pathlib import Path

import pytest
from test_replacement_contract import CAP, mug_facts
from test_replacement_runner import analysed, drain, read
from test_replacement_runner import work as replacement_work

import gaiavfx.application.replace_character as replacement
from gaiavfx.application.replacement_checks import face_checks
from gaiavfx.application.replacement_steps import inversion_score as REAL_INVERSION_SCORE
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.direction.replacement import (
    Attempt,
    Box,
    Calibration,
    CheckResult,
    Track,
    calibrated_check,
    minor_bystander_reason,
    parse_replacement_policy,
    plan_replacement,
    to_record,
)

work = replacement_work


@pytest.fixture
def mug_analysis(monkeypatch):
    def track(name, box, present, concept="person"):
        return {
            "id": name,
            "concept": concept,
            "box": to_record(Box(*box)),
            "presence": ["present"] * present + ["absent"] * (73 - present),
            "mask_artifact": name,
        }

    document = {
        "source_asset": "shot",
        "shot_start": 0,
        "shot_end": 73,
        "native": [896, 512],
        "fps": 24,
        "letterbox": None,
        "concepts": ["person", "mug"],
        "tracks": [
            track("performer", (587, 4, 864, 508), 73),
            track("bystander", (50, 173, 470, 508), 73),
            track("minor", (854, 57, 896, 95), 9),
            track("mug", (717, 132, 772, 180), 73, "mug"),
        ],
        "contacts": {"performer": ["mug"], "bystander": [], "minor": []},
    }
    monkeypatch.setattr(replacement.operations, "operation", lambda *args: {})
    monkeypatch.setattr(replacement, "_report", lambda *args: document)
    monkeypatch.setattr(
        replacement.MediaLibrary,
        "asset_info",
        lambda _session, asset: (
            {"kind": "video", "info": {"has_audio": False}}
            if asset == "shot"
            else {"kind": "image"}
        ),
    )
    request = replacement.ReplacementRequest(
        "shot", "analysis", (replacement.PerformerChoice("performer", "picture"),)
    )
    return document, request


@pytest.mark.parametrize("has_audio", [False, True])
def test_speaking_uses_source_library_audio_metadata(mug_analysis, monkeypatch, has_audio):
    document, request = mug_analysis
    document["tracks"][0]["mouth"] = {"aperture": [0.02] * 73, "face_height": [300] * 73}
    monkeypatch.setattr(
        replacement.MediaLibrary,
        "asset_info",
        lambda _session, asset: (
            {"kind": "video", "info": {"has_audio": has_audio}}
            if asset == "shot"
            else {"kind": "image"}
        ),
    )
    facts, _, _, plan = replacement._initial(None, request, None)
    assert facts.speaking is has_audio and plan.arm.audio_lock is has_audio


def test_minor_mug_track_keeps_crop_report_and_inversion_consistent(mug_analysis, monkeypatch):
    from types import SimpleNamespace

    import numpy as np

    from gaiavfx.application import replacement_steps as steps
    from gaiavfx.application.operations import knowledge_snapshot

    document, request = mug_analysis
    before = json.dumps(document)
    facts, report_document, policy, plan = replacement._initial(None, request, None)
    assert plan.arm.framing == "crop" and not plan.gaps
    assert plan.arm.crop.box.x0 >= 473  # Keeps the real bystander plus dilation outside.
    assert plan.arm.crop.box.x1 >= 864 and plan.arm.crop.box.y1 >= 508
    assert {t.id for t in facts.tracks} == {"performer", "bystander"}
    assert {b.track_id for b in facts.bystanders} == {"bystander"}
    assert facts.required_context == (Box(717, 132, 772, 180),)
    assert facts.avoided_regions == (Box(50, 173, 470, 508),)
    assert any(
        "minor" in r and "area=0.003479" in r and "presence=0.123288" in r and "dropped" in r
        for r in plan.reasons
    )
    assert json.dumps(document) == before and report_document is document
    frozen = {
        "facts": to_record(facts),
        "tracks": document["tracks"],
        "policy": policy,
        "knowledge": knowledge_snapshot(replacement.minimax_h3),
        "sources": {key: {"sha256": key} for key in ("shot", "picture")},
        "seed": 0,
        "has_audio": False,
    }
    jobs = replacement._round_jobs(frozen, plan.arm, 0)
    spec = jobs[0].snapshot["step_spec"]
    assert spec["ignored_tracks"] == ["minor"]
    assert len(spec["tracks"]) == 4
    frames = np.full((73, 4, 4, 3), 80, np.uint8)
    masks = {
        key: np.zeros(frames.shape[:3], bool) for key in ("performer", "bystander", "minor", "mug")
    }
    masks["performer"][:] = True
    for index, key in enumerate(("minor", "bystander", "mug")):
        masks[key][:, 0, index] = True
    monkeypatch.setattr(steps, "_mask_data", lambda library, key: masks[key])
    performer, protected = steps._selected_masks(SimpleNamespace(library=None), spec, frames)
    result = steps.invert_performer(frames, performer, protected)
    assert np.all(result[:, 0, 0] == 175)  # Minor does not carve a hole in inversion.
    assert np.all(result[:, 0, 1:3] == 80)  # Real bystander and mug remain protected.


def test_real_bystander_margin_overlap_still_has_typed_gap(mug_analysis):
    document, request = mug_analysis
    # Disjoint raw boxes overlap once both receive the policy's three-pixel dilation.
    document["tracks"][2]["box"] = to_record(Box(867, 57, 896, 160))
    document["tracks"][2]["presence"] = ["present"] * 73
    facts, _, _, plan = replacement._initial(None, request, None)
    assert {b.track_id for b in facts.bystanders} == {"bystander", "minor"}
    assert plan.arm.framing == "active"
    assert [g.code for g in plan.gaps] == ["no_crop_fits"]
    assert any("falling back to active" in r for r in plan.reasons)
    assert not any("Ignored minor" in r for r in plan.reasons)


def test_selected_minor_track_and_explicit_avoidance_are_not_silently_dropped(mug_analysis):
    _, request = mug_analysis
    policy = parse_replacement_policy(replacement_policy_text())
    selected = replace(request, performers=(replacement.PerformerChoice("minor", "picture"),))
    facts, _, reasons = replacement._facts(None, selected, None, policy)
    assert "minor" in {t.id for t in facts.tracks}
    assert not any("Ignored minor" in r for r in reasons)
    explicit = replace(request, avoided_regions=(Box(854, 57, 896, 95),))
    _, _, _, plan = replacement._initial(None, explicit, None)
    assert plan.arm.framing == "active"
    assert [g.code for g in plan.gaps] == ["no_crop_fits"]


@pytest.mark.parametrize(
    "area_height,present,minor", [(50, 1, False), (49, 4, True), (100, 0, True)]
)
def test_minor_thresholds_are_independent_and_inclusive(area_height, present, minor):
    policy = parse_replacement_policy(replacement_policy_text())
    track = Track(
        "person",
        Box(0, 0, 100, area_height),
        ("present",) * present + ("absent",) * (4 - present),
        None,
        "user",
    )
    assert bool(minor_bystander_reason(track, (1000, 1000), policy)) is minor


@pytest.mark.parametrize("value", ["-0.1", "1.1", "nan", "inf", "true", '"0.25"'])
@pytest.mark.parametrize(
    "key",
    [
        "min_bystander_area_fraction",
        "min_bystander_presence",
        "min_face_height_fraction",
        "min_face_frames_fraction",
    ],
)
def test_analysis_thresholds_reject_invalid_fractions(key, value):
    text = replacement_policy_text()
    original = {"min_bystander_area_fraction": "0.005", "min_face_height_fraction": "0.06"}.get(
        key, "0.25"
    )
    with pytest.raises((ValueError, TypeError)):
        parse_replacement_policy(text.replace(f"{key} = {original}", f"{key} = {value}"))


def test_analysis_policy_is_strict_and_optional():
    text = replacement_policy_text()
    with pytest.raises(ValueError, match="analysis keys"):
        parse_replacement_policy(text.replace("[analysis]", "[analysis]\nunknown = 0.1"))
    start, end = text.index("[analysis]"), text.index("[first_arm]")
    policy = parse_replacement_policy(text[:start] + text[end:])
    assert policy.min_bystander_area_fraction == policy.min_bystander_presence == 0


def report(source=0.1, target=0.2, mouth=0.8, ratio=1.4):
    return {
        "schema": 1,
        "frames": [None] * 56,
        "summary": {
            "candidate": {
                "to_source": {"median": source},
                "to_target": {"median": target},
                "frames_with_face": 56 if source is not None else 0,
            },
            "source": {"frames_with_face": 56},
        },
        "mouth": {"pearson_r": mouth, "max_ratio": ratio},
    }


@pytest.mark.parametrize(
    "value,verdict",
    [
        (0.1, "pass"),
        (0.30, "pass"),
        (0.32, "unsupported"),
        (0.34, "fail"),
        (0.55, "fail"),
        (None, "unsupported"),
    ],
)
def test_calibrated_residual_and_uncalibrated_measurements(value, verdict):
    policy = parse_replacement_policy(replacement_policy_text())
    checks = {c.check: c for c in face_checks(policy, ["actor"], {"actor": report(value)})}
    assert checks["residual_source"].verdict == verdict
    assert checks["residual_source"].measured == value
    assert checks["arcface_target"].verdict == checks["mouth_aperture"].verdict == "unsupported"
    assert checks["arcface_target"].measured == (0.2 if value is not None else None)
    assert checks["mouth_aperture"].measured == (0.8 if value is not None else None)
    assert "1.4" in checks["mouth_aperture"].reason
    if value is None:
        assert "no usable face" in checks["residual_source"].reason


@pytest.mark.parametrize(
    "values,verdict,measured",
    [
        ((0.1, 0.2), "pass", 0.2),
        ((0.1, 0.55), "fail", 0.55),
        ((0.1, 0.32), "unsupported", 0.32),
        ((None, 0.55), "fail", 0.55),
        ((None, 0.1), "unsupported", 0.1),
        ((None, None), "unsupported", None),
    ],
)
def test_joint_aggregation_retains_each_performer(values, verdict, measured):
    policy = parse_replacement_policy(replacement_policy_text())
    checks = {
        c.check: c
        for c in face_checks(
            policy,
            ["left", "right"],
            {"left": report(values[0], 0.1, 0.4), "right": report(values[1], 0.3, 0.9)},
        )
    }
    assert (checks["residual_source"].verdict, checks["residual_source"].measured) == (
        verdict,
        measured,
    )
    assert checks["arcface_target"].measured == (
        0.1 if values[0] is not None else 0.3 if values[1] is not None else None
    )
    assert checks["mouth_aperture"].measured == (
        0.4 if values[0] is not None else 0.9 if values[1] is not None else None
    )
    assert all("performer 1:" in c.reason and "performer 2:" in c.reason for c in checks.values())


@pytest.mark.parametrize("side", ["candidate", "source"])
@pytest.mark.parametrize("count,verdict", [(0, "unsupported"), (13, "unsupported"), (14, "fail")])
def test_sparse_face_frames_cannot_fail_a_performer(side, count, verdict):
    policy = parse_replacement_policy(replacement_policy_text())
    evidence = report(0.55)
    evidence["summary"][side]["frames_with_face"] = count
    checks = {c.check: c for c in face_checks(policy, ["actor"], {"actor": evidence})}
    assert checks["residual_source"].verdict == verdict
    if count < 14:
        assert all(c.measured is None for c in checks.values())
        reason = "no usable face" if count == 0 else "too few usable face frames"
        assert all(reason in c.reason for c in checks.values())


def test_mouth_null_reason_and_comfy_error_text_are_preserved_and_bounded():
    policy = parse_replacement_policy(replacement_policy_text())
    evidence = report(mouth=None, ratio=None)
    evidence["mouth"]["reason"] = "too few paired mouth frames"
    checks = {c.check: c for c in face_checks(policy, ["actor"], {"actor": evidence})}
    assert checks["mouth_aperture"].measured is None
    assert "too few paired mouth frames" in checks["mouth_aperture"].reason
    checks = face_checks(policy, ["actor"], {}, error="X" * 500 + "SECRET-TAIL" * 1000)
    assert all(c.verdict == "unsupported" and c.measured is None for c in checks)
    assert all(c.reason.count("X") == 500 and "SECRET-TAIL" not in c.reason for c in checks)


@pytest.mark.parametrize(
    "scale,shift,verdict,advice",
    [
        (1.0, 7, "pass", "none"),
        (1.02, 8, "pass", "none"),
        (1.03, 9, "unsupported", "retry"),
        (0.64, 183, "fail", "none"),
        (0.84, 77, "fail", "none"),
        (1.05, 0, "fail", "none"),
        (1, 24, "fail", "none"),
        (None, None, "unsupported", "retry"),
    ],
)
def test_geometry_calibration(scale, shift, verdict, advice):
    policy = parse_replacement_policy(replacement_policy_text())
    result = calibrated_check(
        policy,
        "background_geometry",
        shift,
        metrics={
            "scale_delta": abs(scale - 1) if scale is not None else None,
            "shift": shift,
        },
    )
    assert (result.verdict, result.advice) == (verdict, advice)


@pytest.mark.parametrize(
    "change",
    [
        "unknown = 1",
        'check = "invented"',
        "pass_at_most = 0.5\nfail_at_least = 0.4",
        "pass_at_most = 0.4\nfail_at_least = 0.4",
        "pass_at_most = 0.3\nfail_at_most = 0.2",
        "pass_at_most = nan",
        "pass_at_most = true",
        "pass_at_most = 0.1\npass_at_least = 0.2",
        "pass_scale_delta_at_most = 0.05\nfail_scale_delta_at_least = 0.02",
        "pass_shift_at_most = -1",
        'evidence = "../outside.json"',
        "pass_at_most = 0.3\npass_shift_at_most = 8",
        "pass_scale_delta_at_most = 0.02\nfail_shift_at_least = 24",
        "",
    ],
)
def test_calibration_parser_is_strict(change):
    text = (
        replacement_policy_text().split("[[calibration]]")[0]
        + replacement_policy_text()[replacement_policy_text().index("[[escalation]]") :]
    )
    row = {
        "check": '"residual_source"',
        "evidence": '"backend/tests/fixtures/replacement/calibration-6oct.json"',
        "note": '"user verdicts"',
    }
    for line in change.splitlines():
        key, value = line.split("=", 1)
        row[key.strip()] = value.strip()
    with pytest.raises((ValueError, TypeError)):
        parse_replacement_policy(
            text + "\n[[calibration]]\n" + "\n".join(f"{k} = {v}" for k, v in row.items())
        )


def test_calibration_evidence_and_first_arm_default_and_repeat():
    text = replacement_policy_text().replace('treatment = "rgb"', 'treatment = "inverted"', 1)
    policy = parse_replacement_policy(text)
    for row in policy.calibration:
        assert json.loads((Path(__file__).parents[2] / row.evidence).read_text())["schema"] == 1
    facts = mug_facts()
    first = plan_replacement(facts, CAP.replacement, policy, CAP.frame_grid).arm
    assert first.treatment == "inverted"
    checks = (
        CheckResult("residual_source", "fail", "none", 0.55),
        CheckResult("background_geometry", "unsupported", "retry", 15),
    )
    second = plan_replacement(
        facts, CAP.replacement, policy, CAP.frame_grid, (Attempt(first, (checks, checks)),)
    )
    assert second.arm.treatment == "inverted" and second.arm.crop == first.crop
    assert second.arm.seed_offset == 2 and second.arm.seed_count == 1
    assert second.reasons == ("likeness: seed",)
    third = plan_replacement(
        facts,
        CAP.replacement,
        policy,
        CAP.frame_grid,
        (Attempt(first, (checks, checks)), Attempt(second.arm, (checks,))),
    )
    assert third.arm.crop == first.crop and third.arm.seed_offset == 3
    assert parse_replacement_policy(text.replace('treatment = "inverted"', "")).treatment == "rgb"
    with pytest.raises(ValueError):
        parse_replacement_policy(text.replace('treatment = "inverted"', 'treatment = "unknown"'))


def test_generic_calibration_supports_higher_and_banded_values():
    policy = parse_replacement_policy(replacement_policy_text())
    for row, values in (
        (
            Calibration(
                "arcface_target", "evidence.json", "test", pass_at_least=0.6, fail_at_most=0.3
            ),
            [(0.7, "pass"), (0.2, "fail"), (0.4, "unsupported")],
        ),
        (
            Calibration(
                "seam",
                "evidence.json",
                "test",
                pass_at_least=0.4,
                pass_at_most=0.6,
                fail_at_most=0.2,
                fail_at_least=0.8,
            ),
            [(0.5, "pass"), (0.1, "fail"), (0.9, "fail"), (0.7, "unsupported")],
        ),
    ):
        for value, expected in values:
            assert (
                calibrated_check(replace(policy, calibration=(row,)), row.check, value).verdict
                == expected
            )
    with pytest.raises(ValueError, match="duplicate"):
        replace(policy, calibration=(policy.calibration[0], policy.calibration[0]))


def _rgb_policy(monkeypatch):
    text = replacement_policy_text()
    monkeypatch.setattr(replacement, "replacement_policy_text", lambda: text)


def _checks(operation):
    return [
        {c["check"]: c for c in take} for take in operation["review"][0]["attempts"][0]["takes"]
    ]


def test_two_composites_measure_wait_escalate_once_and_keep_budget(work, monkeypatch):
    db, library, runner, _, _ = work
    _, _, request = analysed(work)
    _rgb_policy(monkeypatch)
    runner.comfy.face_reports = {"performer_0": report(0.55)}
    with db.transaction() as session:
        op = replacement.start_replacement(session, request, 4, library=library)
    measurement = [j for j in op["jobs"] if j["kind"] == "analysis"]
    assert len(measurement) == 2
    assert all(j["snapshot"]["face_check"] for j in measurement)
    assert all(
        j["graph"]["performer_0"]["inputs"]["min_face_height_fraction"] == 0.06
        and j["graph"]["performer_0"]["inputs"]["min_face_frames_fraction"] == 0.25
        for j in measurement
    )
    assert all(
        v["source"] == "job_artifact"
        for j in measurement
        for k, v in j["inputs"].items()
        if k in {"candidate", "source", "mask_0"}
    )
    for _ in range(6):
        assert runner.run_once()
    waiting = read(db, op["id"])
    assert len(waiting["candidates"]) == 2 and not waiting["review"]
    runner.recover()
    assert len(read(db, op["id"])["jobs"]) == 7
    assert runner.run_once()
    advanced = read(db, op["id"])
    _assert_fresh_upload_folders(advanced["jobs"])
    assert advanced["review"][0]["arm"]["treatment"] == "inverted"
    assert all(t["residual_source"]["verdict"] == "fail" for t in _checks(advanced))
    assert advanced["budget_reserved"] == 4
    assert sum(j["kind"] == "video" and j["executor"] == "comfy" for j in advanced["jobs"]) == 3
    runner.recover()
    assert len(read(db, op["id"])["jobs"]) == len(advanced["jobs"])
    drain(runner)
    finished = read(db, op["id"])
    assert finished["state"] == "succeeded"
    assert len(finished["candidates"]) == 4
    assert all(
        next(j for j in finished["jobs"] if j["id"] == c["job_id"])["step"]
        == "replacement_composite"
        for c in finished["candidates"]
    )


@pytest.mark.parametrize("failure", [False, True])
def test_measurement_reports_and_failures_close_cleanly(work, failure):
    db, library, runner, _, _ = work
    _, _, request = analysed(work)
    runner.comfy.face_reports = {"performer_0": report(0.1)}
    if failure:
        runner.comfy.fail_graph = lambda graph: any(
            n["class_type"] == "GaiaFaceCheck" for n in graph.values()
        )
        original_request = runner.comfy.request

        def verbose_error(url, method, route, **kwargs):
            response = original_request(url, method, route, **kwargs)
            if route.startswith("/history/"):
                for entry in response.values():
                    if entry["status"]["status_str"] == "error":
                        entry["status"]["messages"] = [
                            ["execution_error", "fake error " + "X" * 1000 + "omitted-tail"]
                        ]
            return response

        runner.comfy.request = verbose_error
    with db.transaction() as session:
        op = replacement.start_replacement(session, request, 4, library=library)
    drain(runner)
    op = read(db, op["id"])
    assert op["state"] == ("partial" if failure else "succeeded")
    assert len(op["candidates"]) == 2 and op["budget_reserved"] == 4
    assert op["review"][0]["arm"] is None
    if failure:
        assert "omitted-tail" in json.dumps(op["jobs"])
        assert "omitted-tail" not in json.dumps(op["review"])
    for take in _checks(op):
        assert take["residual_source"]["verdict"] == ("unsupported" if failure else "pass")
        if failure:
            assert all(
                "fake error" in take[c]["reason"]
                for c in ("residual_source", "arcface_target", "mouth_aperture")
            )
    runner.recover()
    assert len(read(db, op["id"])["review"]) == 1


def _assert_fresh_upload_folders(jobs):
    folders = set()
    for job in jobs:
        names = [v["remote_name"] for v in job["inputs"].values() if "remote_name" in v]
        if job["executor"] != "comfy":
            assert not names
            continue
        assert names and len(names) == len(set(names))
        job_folders = set()
        for remote_name in names:
            root, folder, name = remote_name.split("/")
            assert root == "gaiavfx" and folder and name
            job_folders.add(folder)
            assert any(
                remote_name == item
                for node in job["graph"].values()
                for item in node["inputs"].values()
            )
        assert len(job_folders) == 1 and not job_folders & folders
        folders.update(job_folders)


def test_repeated_operations_use_fresh_upload_folders_and_replay_frozen_jobs(work):
    db, library, _, assets, _ = work
    analysis, _, request = analysed(work)
    with db.transaction() as session:
        repeated_analysis = replacement.start_people_analysis(
            session, assets[0], 1, 52, request_key="second-analysis"
        )
        assert (
            replacement.start_people_analysis(
                session, assets[0], 1, 52, request_key="second-analysis"
            )["jobs"]
            == repeated_analysis["jobs"]
        )
        first = replacement.start_replacement(
            session, request, request_key="first-replacement", library=library
        )
        second = replacement.start_replacement(
            session, request, request_key="second-replacement", library=library
        )
        assert (
            replacement.start_replacement(
                session, request, request_key="first-replacement", library=library
            )["jobs"]
            == first["jobs"]
        )
    assert first["request"] == second["request"]
    _assert_fresh_upload_folders(
        analysis["jobs"] + repeated_analysis["jobs"] + first["jobs"] + second["jobs"]
    )


def test_joint_job_binds_distinct_performer_masks_targets_and_keeps_reports(work):
    import numpy as np

    from gaiavfx.application.replacement_steps import _mask_data, decode_rgb

    db, library, runner, _, _ = work
    original_outputs = runner.comfy.outputs_for

    def outputs_with_diagnostic(graph):
        outputs, payloads = original_outputs(graph)
        if "concept_0_track" in graph:
            outputs["sam_diagnostic"] = {"text": ["tracking complete"]}
        return outputs, payloads

    runner.comfy.outputs_for = outputs_with_diagnostic
    analysis, people, request = analysed(work)
    # Import a second reference so identity bindings cannot accidentally alias.
    from PIL import Image

    target = db.root / "other-target.png"
    Image.new("RGB", (32, 48), (20, 100, 200)).save(target)
    prepared = library.prepare(target, target.name)
    with db.transaction() as session:
        second = library.register(session, prepared, {"type": "import"})["id"]
    request["performers"].append({"track_id": people["tracks"][1]["id"], "target_asset": second})
    runner.comfy.face_reports = {"performer_0": report(0.1), "performer_1": report(0.55)}
    with db.transaction() as session:
        op = replacement.start_replacement(session, request, 4, library=library)
    for job in [j for j in op["jobs"] if j["kind"] == "analysis"]:
        graph = job["graph"]
        assert graph["performer_0_target"]["inputs"] != graph["performer_1_target"]["inputs"]
        assert (
            graph["performer_0"]["inputs"]["performer"]
            != graph["performer_1"]["inputs"]["performer"]
        )
    _assert_fresh_upload_folders(analysis["jobs"] + op["jobs"])
    drain(runner)
    op = read(db, op["id"])
    assert all(t["residual_source"]["measured"] == 0.55 for t in _checks(op))
    measured = next(j for j in op["jobs"] if j["kind"] == "analysis")
    source = runner.comfy.files[measured["inputs"]["source"]["remote_name"]]
    np.testing.assert_array_equal(decode_rgb(library.media, source), work[4][1:52])
    tracks = {t["id"]: t for t in people["tracks"]}
    for n, performer in enumerate(request["performers"]):
        mask = runner.comfy.files[measured["inputs"][f"mask_{n}"]["remote_name"]]
        expected = _mask_data(library, tracks[performer["track_id"]]["mask_artifact"])
        np.testing.assert_array_equal(decode_rgb(library.media, mask)[..., 0] > 127, expected)
    with db.transaction() as session:
        for job in [j for j in op["jobs"] if j["kind"] == "analysis"]:
            document = json.loads(library.read_blob(session, job["outputs"][0]["artifact_id"]))
            assert set(document["performers"]) == {p["track_id"] for p in request["performers"]}


def test_fake_rejects_unqualified_upload_name(tmp_path):
    from fake_comfy import FakeComfy

    with pytest.raises(ValueError):
        FakeComfy().upload("http://fake", tmp_path / "file", "bare-name.png")


@pytest.mark.parametrize(
    "broken", [None, {}, {"actor": {}}, {"actor": report(float("nan"))}, {"actor": report(True)}]
)
def test_incomplete_and_nonfinite_reports_are_unsupported(broken):
    policy = parse_replacement_policy(replacement_policy_text())
    residual = next(
        c for c in face_checks(policy, ["actor"], broken) if c.check == "residual_source"
    )
    assert residual.verdict == "unsupported" and residual.measured is None
    assert "invalid face report" in residual.reason


def test_text_report_collection_recovers_without_resubmission(tmp_path, monkeypatch):
    from fake_comfy import Crash, FakeComfy

    from gaiavfx.application.operations import OperationRunner
    from gaiavfx.infrastructure.database import Database
    from gaiavfx.infrastructure.media import MediaTools
    from gaiavfx.modules.generation.operations.lifecycle import JobSpec
    from gaiavfx.modules.generation.operations.service import OperationService
    from gaiavfx.modules.library.application import LibraryService, MediaLibrary

    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Reports")
    db = Database(tmp_path)
    try:
        library = MediaLibrary(db, MediaTools())
        fake = FakeComfy(
            lambda graph: ({"face": {"text": [json.dumps(report())]}}, {}), polls_to_complete=1
        )
        runner = OperationRunner(db, fake, library, "http://fake", poll_seconds=0)
        with db.transaction() as session:
            op = OperationService().create(
                session,
                kind="person_analysis",
                request={},
                budget_limit=0,
                jobs=[
                    JobSpec(
                        "analysis",
                        "comfy",
                        "person_analysis",
                        graph={"face": {"class_type": "GaiaFaceCheck", "inputs": {}}},
                        snapshot={
                            "knowledge": {"policy": replacement_policy_text()},
                            "face_check": {"face": "actor"},
                        },
                    )
                ],
            )
        original = runner._transition

        def crash(job, state, **kwargs):
            if state == "succeeded":
                raise Crash("after report checkpoint")
            return original(job, state, **kwargs)

        monkeypatch.setattr(runner, "_transition", crash)
        with pytest.raises(Crash):
            runner.run_once()
        before = read(db, op["id"])["jobs"][0]
        assert before["state"] == "collecting"
        assert before["outputs"][0]["artifact_id"]
        monkeypatch.setattr(runner, "_transition", original)
        runner.recover()
        drain(runner)
        after = read(db, op["id"])
        assert after["state"] == "succeeded"
        assert after["jobs"][0]["outputs"] == before["outputs"]
        assert len(fake.posts) == 1 and after["budget_reserved"] == 0 and not after["candidates"]
    finally:
        db.close()


def test_inversion_score_separates_negative_copies():
    import numpy as np

    from gaiavfx.application.replacement_steps import inversion_score

    rng = np.random.default_rng(3)
    frames = rng.integers(0, 255, (6, 80, 100, 3), dtype=np.uint8)
    mask = np.zeros((6, 80, 100), bool)
    mask[:, 10:70, 15:85] = True
    relit = np.clip(frames.astype(int) * 0.8 + 20, 0, 255).astype(np.uint8)
    assert inversion_score(frames, relit, mask) > 0.9
    assert inversion_score(frames, 255 - frames, mask) < -0.9
    assert inversion_score(frames, frames, np.zeros_like(mask)) is None
    policy = parse_replacement_policy(replacement_policy_text())
    assert calibrated_check(policy, "inverted_copy", -0.97).verdict == "fail"
    assert calibrated_check(policy, "inverted_copy", 0.45).verdict == "pass"
    assert calibrated_check(policy, "inverted_copy", -0.2).verdict == "unsupported"
    likeness = [row for row in policy.escalation if row.on == "likeness"]
    assert all("inverted_copy" in row.checks for row in likeness)


def test_fake_negative_copy_fails_the_inversion_check_and_escalates(work, monkeypatch):
    """The fake renders the inverted guide back, a negative copy: the real measure fails it."""
    from gaiavfx.application import replacement_steps

    monkeypatch.setattr(replacement_steps, "inversion_score", REAL_INVERSION_SCORE)
    text = replacement_policy_text().replace('treatment = "rgb"', 'treatment = "inverted"', 1)
    monkeypatch.setattr(replacement, "replacement_policy_text", lambda: text)
    db, library, runner, _, _ = work
    _, _, request = analysed(work)
    runner.comfy.face_reports = {"performer_0": report(0.1)}
    with db.transaction() as session:
        op = replacement.start_replacement(session, request, 4, library=library)
    drain(runner)
    finished = read(db, op["id"])
    first = _checks(finished)
    assert all(take["inverted_copy"]["verdict"] == "fail" for take in first)
    assert all(take["inverted_copy"]["measured"] < -0.5 for take in first)
    assert len(finished["review"]) >= 2


@pytest.mark.parametrize(
    "close_up,target,verdict",
    [
        (True, 0.13, "fail"),
        (True, 0.17, "fail"),
        (True, 0.19, "unsupported"),
        (True, 0.30, "unsupported"),
        (False, 0.13, "unsupported"),
        (True, None, "unsupported"),
    ],
)
def test_close_up_likeness_bound_fails_only_close_ups(close_up, target, verdict):
    policy = parse_replacement_policy(replacement_policy_text())
    evidence = report(0.1, target if target is not None else 0.2)
    if target is None:
        evidence["summary"]["candidate"]["to_target"]["median"] = None
    checks = {
        c.check: c for c in face_checks(policy, ["actor"], {"actor": evidence}, close_up=close_up)
    }
    assert checks["arcface_target"].verdict == verdict
    if verdict == "fail":
        assert "likeness-12oct.json" in checks["arcface_target"].reason
    # The residual check is unaffected.
    assert checks["residual_source"].verdict == "pass"


def test_faceswap_likeness_keys_are_optional_and_paired():
    text = replacement_policy_text()
    assert parse_replacement_policy(text).faceswap.close_up_likeness_fail_at_most == 0.17
    without = "\n".join(
        line
        for line in text.splitlines()
        if not line.startswith(("close_up_likeness_fail_at_most", "likeness_evidence"))
    )
    assert parse_replacement_policy(without).faceswap.close_up_likeness_fail_at_most is None
    half = "\n".join(line for line in text.splitlines() if not line.startswith("likeness_evidence"))
    with pytest.raises(ValueError):
        parse_replacement_policy(half)


def test_close_up_likeness_bound_separates_frozen_user_evidence():
    policy = parse_replacement_policy(replacement_policy_text())
    fixture = json.loads(
        (Path(__file__).parent / "fixtures/replacement/likeness-12oct.json").read_text()
    )
    bound = policy.faceswap.close_up_likeness_fail_at_most
    rejected = [
        take["arcface_target"]
        for take in fixture["takes"]
        if "identity flagged" in take["user_verdict"]
    ]
    accepted = [
        take["arcface_target"]
        for take in fixture["takes"]
        if "identity flagged" not in take["user_verdict"]
    ]
    assert max(rejected) <= bound < min(accepted)
    assert bound == fixture["bound"] == 0.17


def test_old_frozen_close_up_policy_keeps_035_bound():
    text = (Path(__file__).parent / "fixtures/replacement/policy-before-batch5.toml").read_text()
    policy = parse_replacement_policy(text)
    assert policy.faceswap.close_up_likeness_fail_at_most == 0.35
    checks = {
        c.check: c
        for c in face_checks(policy, ["actor"], {"actor": report(0.1, 0.35)}, close_up=True)
    }
    assert checks["arcface_target"].verdict == "fail"
    assert "likeness-11oct.json" in checks["arcface_target"].reason
    assert checks["residual_source"].verdict == "pass"
