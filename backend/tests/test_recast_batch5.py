"""Batch 5 policy opt-ins, frozen compatibility and shot-wide decisions (no inference)."""

import json
from contextlib import nullcontext
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from replacement_plan_support import (
    CAP,
    attempt,
    first_round,
    joint_facts,
    mug_facts,
    result,
    round_plan,
)

from gaiavfx.application import recast_feed, recast_projection, recast_run, shot_assembly
from gaiavfx.application import replace_character as app
from gaiavfx.application import shot_replacement as shot
from gaiavfx.application.operation_start import knowledge_snapshot
from gaiavfx.integrations import minimax_h3
from gaiavfx.modules.recast.application import replacement_policy_text
from gaiavfx.modules.recast.replacement import (
    Arm,
    ReplacementFacts,
    from_record,
    parse_replacement_policy,
    plan_replacement,
    to_record,
)
from gaiavfx.modules.recast.replacement_plan import HandoffChoice, effective_policy

OLD_TEXT = (Path(__file__).parent / "fixtures/replacement/policy-before-batch5.toml").read_text()
TEXT = replacement_policy_text()
POLICY = parse_replacement_policy(TEXT)


def plan(facts, text=TEXT, prior=(), support=CAP.replacement):
    return plan_replacement(facts, support, parse_replacement_policy(text), CAP.frame_grid, prior)


def test_frozen_old_policy_keeps_inversion_escalation_joint_lock_and_draft():
    facts = replace(mug_facts(), close_up=True)
    first = plan(facts, OLD_TEXT).arm
    assert first.treatment == "inverted" and not first.faceswap
    second = plan(facts, OLD_TEXT, (attempt(first, (result("residual_source"),)),)).arm
    assert second.faceswap and second.seed_offset == 2
    joint = replace(joint_facts(), quiet=False, speaking=True, lips_unmeasured=True)
    assert not plan(joint, OLD_TEXT).arm.audio_lock
    policy = effective_policy(OLD_TEXT, draft=True)
    assert (policy.renders, policy.max_rounds, policy.renders_per_round) == (1, 1, (1,))
    # The old request shape has neither a draft flag nor a FaceSwap pin.
    policy = effective_policy(OLD_TEXT)
    assert not policy.first_arm_faceswap_configured
    assert (policy.renders, policy.renders_per_round) == (4, (2, 1, 1))
    arm_record = to_record(first)
    del arm_record["faceswap"]
    del arm_record["audio_lock"]
    assert from_record(Arm, arm_record) == first
    record = to_record(mug_facts())
    del record["lips_unmeasured"]
    assert from_record(ReplacementFacts, record) == mug_facts()


def test_rgb_close_up_starts_faceswap_and_recast_retries_seed():
    facts = replace(mug_facts(), close_up=True)
    first = round_plan(facts, text=TEXT, treatment_override="rgb", faceswap_override=True)
    assert first.arm.treatment == "rgb" and first.arm.faceswap
    assert any("FaceSwap from round 0" in r for r in first.reasons)
    second = round_plan(
        facts,
        (attempt(first.arm, (result("arcface_target"),)),),
        text=TEXT,
        treatment_override="rgb",
        faceswap_override=True,
    )
    assert second.arm == replace(first.arm, round=1, seed_offset=2, seed_count=1)
    assert "likeness: seed" in second.reasons
    # The single-shot tool still owns its treatment escalation.
    assert (
        plan(facts, prior=(attempt(first.arm, (result("arcface_target"),)),)).arm.treatment
        == "inverted"
    )


@pytest.mark.parametrize("facts", [mug_facts(), joint_facts()])
def test_medium_and_joint_start_rgb_without_faceswap(facts):
    first = plan(facts)
    assert first.arm.treatment == "rgb" and not first.arm.faceswap
    assert "First arm: rgb guide." in first.reasons


@pytest.mark.parametrize("treatment", ["rgb", "inverted"])
def test_pinned_treatment_reason_only_names_the_pin_on_round_zero(treatment):
    facts = mug_facts()
    args = {"treatment_override": treatment, "faceswap_override": False}
    pinned = round_plan(facts, text=TEXT, **args)
    assert pinned.arm.treatment == treatment
    assert f"First arm: {treatment} guide." in pinned.reasons
    assert f"Treatment pinned per shot: {treatment}." in pinned.reasons
    assert not any("policy default" in reason for reason in pinned.reasons)
    retry = round_plan(
        facts, (attempt(pinned.arm, (result("arcface_target"),)),), text=TEXT, **args
    )
    assert retry.arm.round == 1
    assert "likeness: seed" in retry.reasons
    stopped = round_plan(
        facts, (attempt(pinned.arm, (result("arcface_target", "pass"),)),), text=TEXT, **args
    )
    assert stopped.arm is None and stopped.stop
    for planned in (retry, stopped):
        assert not any("Treatment pinned per shot" in reason for reason in planned.reasons)


@pytest.mark.parametrize("speaking", [False, True])
@pytest.mark.parametrize("treatment", [None, "rgb", "inverted"])
def test_frozen_old_policy_preserves_exact_first_retry_stop_and_bridge_reasons(speaking, treatment):
    facts = replace(mug_facts(), quiet=False, speaking=speaking)
    args = {"treatment_override": treatment}
    first = round_plan(facts, text=OLD_TEXT, **args)
    assert first.reasons == ()
    assert first.arm.audio_lock is speaking
    retry = round_plan(
        facts, (attempt(first.arm, (result("arcface_target"),)),), text=OLD_TEXT, **args
    )
    assert retry.reasons == ("likeness: seed",)
    stopped = round_plan(
        facts, (attempt(first.arm, (result("arcface_target", "pass"),)),), text=OLD_TEXT, **args
    )
    assert stopped.reasons == ("all calibrated checks passed",)
    bridge = round_plan(
        replace(facts, handoff="bridge"),
        text=OLD_TEXT,
        renders=1,
        bridge_arm=first.arm,
        crop_override=first.arm.crop,
        **args,
    )
    assert bridge.reasons == ()
    assert bridge.arm.audio_lock is speaking


def test_treatment_only_request_does_not_gain_new_pin_reason():
    pinned = round_plan(mug_facts(), text=TEXT, treatment_override="inverted")
    assert pinned.arm.treatment == "inverted"
    assert pinned.reasons == ("First arm: inverted guide.",)


def test_close_up_without_backend_faceswap_falls_back_with_reason():
    first = plan(
        replace(mug_facts(), close_up=True), support=replace(CAP.replacement, faceswap=False)
    )
    assert first.arm.treatment == "inverted" and not first.arm.faceswap
    assert any("without FaceSwap support" in r for r in first.reasons)
    pinned = round_plan(
        replace(mug_facts(), close_up=True),
        text=TEXT,
        treatment_override=first.arm.treatment,
        faceswap_override=first.arm.faceswap,
        support=replace(CAP.replacement, faceswap=False),
    )
    assert pinned.arm.treatment == "inverted" and not pinned.arm.faceswap
    assert any("without FaceSwap support" in r for r in pinned.reasons)


@pytest.mark.parametrize(
    "check,verdict,advice,retry",
    [
        ("arcface_target", "fail", "none", True),
        ("residual_source", "fail", "none", True),
        ("arcface_target", "unsupported", "retry", False),
        ("background_geometry", "unsupported", "retry", False),
        ("background_geometry", "fail", "none", False),
        ("mouth_aperture", "fail", "none", False),
    ],
)
def test_draft_only_retries_calibrated_likeness_once(check, verdict, advice, retry):
    facts = replace(mug_facts(), close_up=True)
    args = {"draft": True, "treatment_override": "rgb", "faceswap_override": True}
    first = round_plan(facts, text=TEXT, **args)
    assert first.arm.seed_count == 1
    checks = (result(check, verdict, advice),)
    prior = (attempt(first.arm, checks),)
    second = round_plan(facts, prior, text=TEXT, **args)
    assert (second.arm is not None) is retry
    assert effective_policy(TEXT, draft=True).renders == 2
    if retry:
        assert second.arm == replace(first.arm, round=1, seed_offset=1)
        assert round_plan(facts, (*prior, attempt(second.arm, checks)), text=TEXT, **args).stop
        assert any("Draft: one likeness retry" in r for r in second.reasons)


@pytest.mark.parametrize(
    "old,new",
    [
        ('faceswap = "close_up"', 'faceswap = "always"'),
        ('audio_lock_groupings = ["single", "joint"]', 'audio_lock_groupings = ["head"]'),
        ('audio_lock_unmeasured = ["joint"]', 'audio_lock_unmeasured = "joint"'),
        ("renders_per_round = [1, 1]", "renders_per_round = [true, 1]"),
        ('retry_on = ["likeness"]', 'retry_on = ["mouth"]'),
        ("[loop]", '[loop]\nretry_on = ["likeness"]'),
        ("min_window_agreeing_fraction = 0.05", "min_window_agreeing_fraction = nan"),
    ],
)
def test_new_policy_keys_are_closed_and_validated(old, new):
    assert old in TEXT
    with pytest.raises((TypeError, ValueError)):
        parse_replacement_policy(TEXT.replace(old, new))


def test_faceless_target_close_up_pins_faceswap_off_in_every_window(monkeypatch):
    """A robot target close-up (the source face is large, the target has none) never stacks
    FaceSwap: in Batch 6 it painted the actress's face through the robot's face plate."""
    reports = {
        str(i): {"source_asset": "source", "shot_start": a, "shot_end": b}
        for i, (a, b, _) in enumerate(shot.shot_windows(0, 160))
    }
    monkeypatch.setattr(shot.operations, "operation", lambda session, key: reports[key])
    monkeypatch.setattr(shot, "analysis_report", lambda session, report, library: report)
    first = replace(mug_facts(), close_up=True)
    initial = []

    def fake_initial(session, request, library, **kwargs):
        initial.append(request)
        planned = first_round(
            first, faceswap_override=request.faceswap_override, close_up=first.close_up
        )
        return planned, TEXT

    monkeypatch.setattr(shot, "plan_request", fake_initial)
    seen = []

    def start(session, request, *args, **kwargs):
        seen.append(request)
        return {"id": str(len(seen))}

    monkeypatch.setattr(shot, "start_replacement", start)
    shot.start_shot_replacement(
        SimpleNamespace(begin_nested=nullcontext),
        None,
        source_asset="source",
        window_analyses=list(reports),
        performers=[{"tracks": {k: "actor" for k in reports}, "target_asset": "target"}],
        mode="draft",
        seed=7,
        request_key="test",
        faceswap_allowed=False,
    )
    assert initial[0].faceswap_override is False
    assert len(seen) == 3
    assert all(r.faceswap_override is False and r.close_up is True for r in seen)


@pytest.mark.parametrize("pin", [None, False, True])
def test_retried_chain_take_and_frozen_settings_reach_window_two_and_bridge(monkeypatch, pin):
    facts = replace(mug_facts(), close_up=pin is True)
    arm = plan(facts, OLD_TEXT if pin is None else TEXT).arm
    frozen = {
        "policy": OLD_TEXT if pin is None else TEXT,
        "knowledge": knowledge_snapshot(minimax_h3),
        "facts": to_record(facts),
        "seed": 7,
        "analysis_operation": "a0",
        "save_latent": True,
        "crop_override": to_record(arm.crop),
        "first_plan": {"arm": to_record(arm)},
    }
    if pin is not None:
        frozen["faceswap_override"] = pin
    operation = {"id": "w0", "request": frozen}
    monkeypatch.setattr(shot.operations, "operation", lambda *args: operation)
    # Keep the real chain_take chooser: take 0 fails, retry take 1 passes.
    takes = {
        i: {
            "checks": [{"verdict": verdict}],
            "window": {"operation_id": "w0", "candidate_id": f"c{i}"},
        }
        for i, verdict in enumerate(("fail", "pass"))
    }
    monkeypatch.setattr(shot_assembly, "window_takes", lambda *args: takes)
    captured = []

    def start(session, request, *args, **kwargs):
        captured.append((request, kwargs))
        return {"id": "w1"}

    monkeypatch.setattr(shot, "start_replacement", start)
    shot.continue_shot_replacement(
        None,
        None,
        source_asset="source",
        window_analyses=["a0", "a1"],
        window_operations=["w0"],
        performers=[{"tracks": {"a0": "actor", "a1": "actor"}, "target_asset": "target"}],
        mode="draft",
        seed=7,
        request_key="test",
    )
    request, kwargs = captured[-1]
    assert request.handoff.candidate_id == "c1"
    assert request.faceswap_override is pin
    assert request.close_up is (None if pin is None else pin)
    assert kwargs["draft"] and kwargs["continuation_snapshot"] is frozen
    shot.start_shot_bridge(
        None,
        None,
        source_asset="source",
        predecessor=takes[1],
        successor=takes[1],
        join=1,
        crop=to_record(arm.crop),
        seed=7,
        request_key="bridge",
    )
    request, kwargs = captured[-1]
    assert request.faceswap_override is pin
    assert kwargs["renders"] == 1 and kwargs["continuation_snapshot"] is frozen
    assert not kwargs.get("draft", False)


@pytest.mark.parametrize("decision", [None, "neutral", "fitted", "inherited"])
def test_handoff_freezes_chosen_tone_report_and_passes_it_to_composite(monkeypatch, decision):
    facts = replace(mug_facts(), shot_start=51, shot_end=124, handoff="latent")
    arm = plan(facts).arm
    tone = {"applied": True}
    if decision:
        tone.update(decision=decision, final_gain=[1.08, 1.1, 1.04])
    predecessor = {"id": "w0", "request": {"facts": to_record(mug_facts()), "seed": 7}}
    composite = {
        "seed": 8,
        "snapshot": {"step_spec": {"crop": to_record(arm.crop)}},
        "outputs": [{"output_key": "checks", "artifact_id": "selected-checks"}],
    }
    render = {"outputs": [{"context_artifact_id": "latent", "context_sha256": "sha"}]}
    monkeypatch.setattr(app, "_chosen_window", lambda *args: (predecessor, composite, render))
    monkeypatch.setattr(
        app.MediaLibrary, "artifact", lambda *args: {"id": "latent", "sha256": "sha"}
    )
    reads = []

    def blob(session, artifact):
        reads.append(artifact)
        return json.dumps({"tone_anchor": tone})

    handoff = app._freeze_handoff(
        None, HandoffChoice("w0", "retry"), facts, arm.crop, SimpleNamespace(read_blob=blob)
    )
    assert reads == ["selected-checks"] and handoff["take"] == 1
    if decision:
        assert handoff["tone"] == {"decision": decision, "final_gain": tone["final_gain"]}
    else:
        assert "tone" not in handoff
    frozen = {
        "facts": to_record(facts),
        "policy": TEXT,
        "knowledge": knowledge_snapshot(minimax_h3),
        "tracks": [],
        "handoff": handoff,
        "seed": 7,
        "has_audio": False,
        "sources": {key: {"sha256": key} for key in ("source", "target")},
    }
    composite_job = next(
        j for j in app._round_jobs(frozen, arm, 0) if j.step == "replacement_composite"
    )
    assert composite_job.snapshot["step_spec"]["handoff"] == handoff


@pytest.mark.parametrize("mode,reserved", [("draft", 2), ("final", 4)])
def test_settled_windows_return_unused_reservations_and_pending_windows_stay_held(
    monkeypatch, mode, reserved
):
    render = {"executor": "comfy", "kind": "video"}
    operation = {
        "state": "awaiting_review",
        "budget_reserved": reserved,
        "cancel_requested": False,
        "jobs": [
            render,
            {"executor": "comfy", "kind": "analysis"},
            {"executor": "local", "kind": "video"},
        ],
    }
    shot = {
        "plan": {"renders_per_window": reserved},
        "mode": mode,
        "assembly_op": None,
        "window_replacement_ops": ["w0"],
        "window_analysis_ops": ["a0", "a1"],
    }
    monkeypatch.setattr(recast_projection.operations, "operation", lambda *args: operation)
    assert recast_feed.used(None, {"shots": [shot]}) == 2 * reserved
    operation["state"] = "succeeded"
    assert recast_projection.render_cost(operation) == 1
    assert recast_feed.used(None, {"shots": [shot]}) == 1 + reserved
    shot["assembly_op"] = "assembled"
    assert recast_feed.used(None, {"shots": [shot]}) == 1
    assert recast_projection.window_renders(mode) == reserved


def test_draft_bridge_reservation_is_one_per_join_even_with_retry():
    bridge_plan = {
        "windows": [{}, {}, {}],
        "_handoff": {"joins": [{"join": 1, "kind": "bridge"}, {"join": 2, "kind": "bridge"}]},
    }
    assert recast_run.bridge_reservation(bridge_plan, 2, mode="draft") == 2
    assert recast_run.bridge_reservation(bridge_plan, 4, mode="final") == 5
