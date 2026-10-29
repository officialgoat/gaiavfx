"""Close-up identity escalation, frozen knowledge and legacy compatibility."""

import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_head_plan import planned as head_plan
from test_replacement_checks import mug_analysis as mug_analysis  # noqa: PLC0414
from test_replacement_contract import CAP, attempt, joint_facts, mug_facts, result
from test_replacement_runner import work as work  # noqa: PLC0414

from gaiavfx.application import replace_character as app
from gaiavfx.application.operations import knowledge_snapshot
from gaiavfx.integrations import minimax_h3
from gaiavfx.integrations.minimax_h3 import graph as h3_graph
from gaiavfx.integrations.minimax_h3.graph import parse_render, render_presets
from gaiavfx.integrations.minimax_h3.replacement import (
    build_replacement_graph,
    compile_replacement,
)
from gaiavfx.modules.direction.application import (
    replacement_policy_text as shipped_policy_text,
)
from gaiavfx.modules.direction.capabilities import CapabilityGap
from gaiavfx.modules.direction.replacement import (
    Arm,
    ReplacementFacts,
    ReplacementPlan,
    from_record,
    parse_replacement_policy,
    plan_replacement,
    to_record,
)


def replacement_policy_text():
    # Keep the escalation ladder tests on the pre-round-0 FaceSwap arm.
    return (
        shipped_policy_text()
        .replace('treatment = "rgb"', 'treatment = "inverted"', 1)
        .replace('faceswap = "close_up"', 'faceswap = "never"')
    )


POLICY = parse_replacement_policy(replacement_policy_text())


def without_table(text, name):
    return re.sub(rf"(?m)^\[{name}\]\n[^\[]*", "", text)


def plan(facts, prior=(), policy=POLICY, support=CAP.replacement):
    return plan_replacement(facts, support, policy, CAP.frame_grid, prior)


def request(**kwargs):
    return app.ReplacementRequest(
        "source", "analysis", (app.PerformerChoice("actor", "target"),), **kwargs
    )


@pytest.mark.parametrize(
    "heights,expected",
    [
        ([None, 699, 699, 999], False),
        ([650, None, 700, 750], True),
        ([None, None], False),
        ([], False),
        ([0, -1, float("nan")], False),
    ],
)
def test_median_measured_native_face_height(heights, expected):
    people = {"actor": {"mouth": {"face_height": heights}, "presence": ["present"] * len(heights)}}
    actual, reasons = app._close_up_facts(request(), people, (1920, 1000), POLICY)
    assert actual is expected
    assert bool(reasons) is expected or reasons == ("Close-up unmeasured",)


def test_close_up_derivation_is_optional_single_and_overridable():
    people = {"actor": {"mouth": {"face_height": [740] * 73}, "presence": ["present"] * 73}}
    assert app._close_up_facts(request(), people, (1920, 1000), POLICY) == (
        True,
        ("Close-up: median face 74% of the frame height.",),
    )
    assert not app._close_up_facts(request(), {"actor": {}}, (1920, 1000), POLICY)[0]
    assert not app._close_up_facts(request(), people, (1920, 1000), replace(POLICY, faceswap=None))[
        0
    ]
    joint = replace(request(), performers=(*request().performers, app.PerformerChoice("two", "t")))
    assert app._close_up_facts(joint, people, (1920, 1000), POLICY) == (False, ())
    for explicit in (True, False):
        assert (
            app._close_up_facts(request(close_up=explicit), {"actor": {}}, (1920, 1000), POLICY)[0]
            is explicit
        )
    assert not app._close_up_facts(request(close_up=False), people, (1920, 1000), POLICY)[0]
    threshold = replace(POLICY, faceswap=replace(POLICY.faceswap, close_up_min_face_fraction=0.8))
    assert not app._close_up_facts(request(), people, (1920, 1000), threshold)[0]


def test_application_preview_freezes_fact_and_reason(mug_analysis):
    document, choice = mug_analysis
    document["tracks"][0]["mouth"] = {"aperture": [0.02] * 73, "face_height": [379] * 73}
    preview = app.replacement_plan(None, choice, library=None)
    assert preview["facts"]["close_up"] is True
    assert "Close-up: median face 74% of the frame height." in preview["reasons"]
    assert preview["arm"]["faceswap"] is True


def test_close_up_derivation_matches_native_trial_series():
    from test_mouth_facts import EVIDENCE, trial

    assert POLICY.faceswap.close_up_min_face_fraction == 0.7
    seen = set()
    for window in EVIDENCE["windows"]:
        shot = window["app_shot"]
        if shot not in {"058", "061", "056", "059", "006"}:
            continue
        series, presence = trial(shot, window["window"], side="native")
        people = {
            "actor": {"mouth": {"face_height": series[0].face_height}, "presence": presence[0]}
        }
        close_up, _ = app._close_up_facts(request(), people, (1920, 800), POLICY)
        assert close_up is (shot in {"058", "061"}), (shot, window["window"])
        seen.add(shot)
    assert seen == {"058", "061", "056", "059", "006"}


@pytest.mark.parametrize(
    "measured,present,expected", [(1, 8, False), (2, 8, True), (2, 9, False), (0, 0, False)]
)
def test_close_up_requires_measurements_on_a_quarter_of_present_frames(measured, present, expected):
    people = {
        "actor": {
            "mouth": {"face_height": [740] * measured + [None] * (present - measured) + [999] * 12},
            "presence": ["present"] * present + ["absent"] * 12,
        }
    }
    actual, reasons = app._close_up_facts(request(), people, (1920, 1000), POLICY)
    assert actual is expected
    assert reasons == (
        ("Close-up: median face 74% of the frame height.",)
        if expected
        else ("Close-up unmeasured",)
    )
    if present:
        stricter = replace(POLICY, min_face_frames_fraction=0.5)
        assert app._close_up_facts(request(), people, (1920, 1000), stricter) == (
            False,
            ("Close-up unmeasured",),
        )


def test_joint_explicit_close_up_is_refused_before_analysis():
    from gaiavfx.shared.errors import ApplicationError

    joint = replace(
        request(close_up=True),
        performers=(app.PerformerChoice("one", "t"), app.PerformerChoice("two", "t")),
    )
    with pytest.raises(ApplicationError, match="Close-up applies to a single performer") as error:
        app.replacement_plan(None, joint, library=None)
    assert error.value.status == 422


@pytest.mark.parametrize("knowledge_kind", ["missing", "legacy", "current"])
@pytest.mark.parametrize("context_frames", [0, 22])
def test_advancer_gates_faceswap_from_frozen_knowledge(knowledge_kind, context_frames, monkeypatch):
    facts = replace(mug_facts(), close_up=True, context_frames=context_frames)
    first = plan(facts).arm
    stored = {
        "facts": to_record(facts),
        "policy": replacement_policy_text(),
        "policy_limits": {"max_rounds": 3},
        "seed": 7,
    }
    if knowledge_kind != "missing":
        stored["knowledge"] = knowledge_snapshot(minimax_h3)
        if knowledge_kind == "legacy":
            stored["knowledge"]["render.toml"] = without_table(
                stored["knowledge"]["render.toml"], "faceswap"
            )
    if context_frames:
        stored["chain"] = {"predecessor_operation": "window0", "candidate_id": "chosen"}
    # Current defaults disagree with the frozen snapshot in both directions.
    current = render_presets()
    if knowledge_kind == "current":
        current = replace(current, faceswap=None)
    monkeypatch.setattr(h3_graph, "render_presets", lambda: current)
    operation = {
        "id": "op",
        "state": "awaiting_review",
        "cancel_requested": False,
        "review": [],
        "budget_reserved": 4,
        "request": stored,
        "jobs": [
            {
                "step": "replacement_composite",
                "executor": "local",
                "kind": "video",
                "state": "succeeded",
                "snapshot": {"step_spec": {"arm": to_record(first)}},
                "outputs": [{"output_key": "checks", "artifact_id": "checks"}],
            }
        ]
        * 2
        + [{"step": None, "snapshot": {}, "executor": "comfy", "kind": "video"}] * 2,
    }
    monkeypatch.setattr(app.operations, "operation", lambda *args: operation)
    monkeypatch.setattr(app, "_round_jobs", lambda *args: [])
    monkeypatch.setattr(
        app.operations, "resume", lambda session, op_id, decision, **kwargs: decision
    )
    library = SimpleNamespace(
        read_blob=lambda *args: json.dumps({"checks": [to_record(result("residual_source"))]})
    )
    decision = app.advance_replacement(None, operation, library=library)
    assert decision["arm"]["faceswap"] is (knowledge_kind == "current")
    assert decision["reasons"] == [
        "likeness: faceswap" if knowledge_kind == "current" else "likeness: seed"
    ]
    assert decision["seeds"] == [9]


@pytest.mark.parametrize("supported", [False, True])
def test_continuation_initial_planning_uses_window_zero_faceswap_capability(monkeypatch, supported):
    facts = replace(mug_facts(), close_up=True, handoff="latent")
    window_zero = {"policy": replacement_policy_text(), "knowledge": knowledge_snapshot(minimax_h3)}
    if not supported:
        window_zero["knowledge"]["render.toml"] = without_table(
            window_zero["knowledge"]["render.toml"], "faceswap"
        )
    current = render_presets()
    if supported:
        current = replace(current, faceswap=None)
    monkeypatch.setattr(h3_graph, "render_presets", lambda: current)
    monkeypatch.setattr(
        app, "_facts", lambda *args: (facts, {"contacts": {}, "concepts": ["person"]}, ())
    )
    original_plan, seen = app._plan, []

    def planning(*args, **kwargs):
        seen.append(kwargs["faceswap_supported"])
        assert args[1] == window_zero["policy"]
        return original_plan(*args, **kwargs)

    monkeypatch.setattr(app, "_plan", planning)
    choice = request(
        handoff=app.HandoffChoice("window0", "chosen"), crop_override=plan(facts).arm.crop
    )
    first = app._initial(None, choice, None, continuation_snapshot=window_zero)[3]
    assert seen == [supported] and not first.arm.faceswap
    second = original_plan(
        facts,
        window_zero["policy"],
        (attempt(first.arm, (result("residual_source"),)),),
        faceswap_supported=seen[0],
    )
    assert second.arm.faceswap is supported
    assert second.reasons == ("likeness: faceswap" if supported else "likeness: seed",)


def test_faceswap_hash_is_optional_but_validated():
    text = knowledge_snapshot(minimax_h3)["render.toml"]
    pin = render_presets().faceswap["sha256"]
    assert "sha256" not in parse_render(text.replace(f'sha256 = "{pin}"\n', "")).faceswap
    for invalid in ('"bad"', "true", '""'):
        with pytest.raises(ValueError, match="faceswap sha256"):
            parse_render(text.replace(f'sha256 = "{pin}"', f"sha256 = {invalid}"))


@pytest.mark.parametrize("audio_lock", [False, True])
def test_likeness_failure_stacks_then_retries_with_fresh_seeds(audio_lock):
    facts = replace(mug_facts(), close_up=True, speaking=audio_lock)
    first = plan(facts).arm
    assert not first.faceswap and first.audio_lock is audio_lock
    checks = (result("residual_source"),)
    prior = (attempt(first, checks),)
    second = plan(facts, prior)
    assert second.reasons == ("likeness: faceswap",)
    assert second.arm.faceswap and second.arm.seed_offset == 2
    assert second.arm.audio_lock is audio_lock
    third = plan(facts, (*prior, attempt(second.arm, checks)))
    assert third.reasons == ("likeness: seed",)
    assert third.arm.faceswap and third.arm.seed_offset == 3
    assert third.arm.audio_lock is audio_lock
    assert plan(facts, (*prior, attempt(second.arm, checks), attempt(third.arm, checks))).stop


@pytest.mark.parametrize("treatment", ["inverted", "rgb"])
def test_pinned_treatment_allows_faceswap_before_seed_retries(treatment):
    facts = replace(mug_facts(), close_up=True, speaking=True)
    text = replacement_policy_text()
    first = app._plan(facts, text, treatment_override=treatment).arm
    checks = (result("residual_source"),)
    prior = (attempt(first, checks),)
    second = app._plan(facts, text, prior, treatment_override=treatment)
    assert second.reasons == ("likeness: faceswap",)
    assert second.arm == replace(first, faceswap=True, seed_count=1, round=1, seed_offset=2)
    third = app._plan(
        facts, text, (*prior, attempt(second.arm, checks)), treatment_override=treatment
    )
    assert third.reasons == ("likeness: seed",)
    assert third.arm == replace(second.arm, round=2, seed_offset=3)


@pytest.mark.parametrize("treatment", ["inverted", "rgb"])
@pytest.mark.parametrize("joint", [False, True])
@pytest.mark.parametrize(
    "checks",
    [
        (result("residual_source"),),
        (result("background_geometry"),),
        (result("residual_source", "pass", "none"),),
        (result("residual_source", "unsupported", "none"),),
        (result("residual_source", "unsupported", "retry"),),
    ],
)
def test_pinned_non_close_up_plans_and_stop_reasons_match_previous_behavior(
    treatment, joint, checks
):
    facts = joint_facts() if joint else mug_facts()
    text = replacement_policy_text()
    first = app._plan(facts, text, treatment_override=treatment).arm
    prior = (attempt(first, checks),)
    # Reproduce the policy transformation before FaceSwap was introduced.
    previous = replace(
        POLICY,
        treatment=treatment,
        inverted_when_similarity_at_least=None,
        faceswap=None,
        escalation=tuple(
            replace(row, to="seed") if row.to == "treatment" else row
            for row in POLICY.escalation
            if row.to != "faceswap"
        ),
    )
    expected = plan(facts, prior, previous)
    actual = app._plan(facts, text, prior, treatment_override=treatment)
    assert actual == expected
    if actual.arm and checks[0].check == "residual_source":
        assert actual.reasons == ("likeness: seed",)
        assert actual.arm == replace(first, seed_count=1, round=1, seed_offset=2)


@pytest.mark.parametrize("mode", ["wide", "joint", "head", "backend", "policy"])
def test_ineligible_windows_skip_faceswap(mode):
    facts = replace(joint_facts() if mode == "joint" else mug_facts(), close_up=mode != "wide")
    first = replace(head_plan().arm, treatment="inverted") if mode == "head" else plan(facts).arm
    support = replace(CAP.replacement, faceswap=False) if mode == "backend" else CAP.replacement
    policy = replace(POLICY, faceswap=None) if mode == "policy" else POLICY
    next_plan = plan(facts, (attempt(first, (result("residual_source"),)),), policy, support)
    assert next_plan.reasons == ("likeness: seed",)
    assert not next_plan.arm.faceswap


@pytest.mark.parametrize(
    "verdict,advice", [("pass", "none"), ("unsupported", "none"), ("unsupported", "retry")]
)
def test_only_failed_likeness_enables_faceswap(verdict, advice):
    facts = replace(mug_facts(), close_up=True)
    assert plan(
        facts, (attempt(plan(facts).arm, (result("residual_source", verdict, advice),)),)
    ).stop


def test_geometry_escalation_keeps_faceswap():
    facts = replace(mug_facts(), close_up=True)
    first = plan(facts).arm
    prior = (attempt(first, (result("residual_source"),)),)
    second = plan(facts, prior).arm
    third = plan(facts, (*prior, attempt(second, (result("background_geometry"),))))
    assert third.arm.faceswap and third.arm.framing == "active" and third.arm.seed_offset == 3


def test_optional_tables_gate_capability_and_planning(monkeypatch):
    old_policy = parse_replacement_policy(without_table(replacement_policy_text(), "faceswap"))
    old_render = parse_render(
        without_table(knowledge_snapshot(minimax_h3)["render.toml"], "faceswap")
    )
    assert old_policy.faceswap is None and old_render.faceswap is None
    facts = replace(mug_facts(), close_up=True)
    prior = (attempt(plan(facts).arm, (result("residual_source"),)),)
    assert not plan(facts, prior, old_policy).arm.faceswap
    assert not app._plan(
        facts,
        replacement_policy_text(),
        prior,
        faceswap_supported=minimax_h3.capabilities(old_render).replacement.faceswap,
    ).arm.faceswap
    monkeypatch.setattr(h3_graph, "render_presets", lambda: old_render)
    assert minimax_h3.capabilities().replacement.faceswap is False
    assert not plan(facts, prior, support=minimax_h3.capabilities().replacement).arm.faceswap


def test_frozen_knowledge_drives_prompt_graph_and_job_snapshot():
    facts = replace(mug_facts(), close_up=True)
    arm = replace(plan(facts).arm, faceswap=True, audio_lock=True)
    knowledge = knowledge_snapshot(minimax_h3)
    knowledge["render.toml"] = (
        knowledge["render.toml"]
        .replace('trigger = "Faceswap"', 'trigger = "FrozenTrigger"')
        .replace("strength = 1.0", "strength = 0.75")
        .replace("SS_FaceSwap_MiniMax_H3_REF2VA.safetensors", "frozen-faceswap.safetensors")
    )
    stored = {
        "facts": to_record(facts),
        "knowledge": knowledge,
        "policy": replacement_policy_text(),
        "tracks": [],
        "sources": {k: {"sha256": k} for k in ("source", "target")},
        "seed": 7,
        "has_audio": True,
    }
    jobs = app._round_jobs(stored, arm, 0)
    render = next(j for j in jobs if j.kind == "video" and j.executor == "comfy")
    assert render.snapshot["knowledge"] == knowledge
    downloads = json.loads(
        (Path(__file__).parent / "fixtures/downloads-2026-10-09.json").read_text()
    )
    pin = next(row["sha256"] for row in downloads["items"] if row["name"].startswith("H3 FaceSwap"))
    assert parse_render(render.snapshot["knowledge"]["render.toml"]).faceswap["sha256"] == pin
    graph = render.graph
    assert graph["conditioning"]["inputs"]["prompt"].startswith("FrozenTrigger. ")
    assert graph["faceswap"]["inputs"] == {
        "model": ["adapter", 0],
        "lora_name": "frozen-faceswap.safetensors",
        "strength_model": 0.75,
    }
    assert graph["audio_lock"]["inputs"]["model"] == ["faceswap", 0]


def test_legacy_facts_plans_and_arms_advance_and_lower_identically():
    facts = mug_facts()
    first = plan(facts)
    record = to_record(first)
    for key in ("audio_lock", "faceswap"):
        del record["arm"][key]
    fact_record = to_record(facts)
    for key in ("speaking", "close_up", "context_frames"):
        del fact_record[key]
    loaded = from_record(ReplacementPlan, record)
    loaded_facts = from_record(ReplacementFacts, fact_record)
    assert loaded == first and loaded_facts == facts
    policy_text = without_table(replacement_policy_text(), "faceswap")
    policy_text = re.sub(
        r'\n\[\[escalation\]\]\non = "likeness"\nchecks = [^\n]+\nfires_on = \["fail"\]\nto = "faceswap"\n',
        "",
        policy_text,
    )
    knowledge = knowledge_snapshot(minimax_h3)
    knowledge["render.toml"] = without_table(knowledge["render.toml"], "faceswap")
    stored = {"facts": fact_record, "policy": policy_text, "knowledge": knowledge}
    prior = (attempt(loaded.arm, (result("residual_source"),)),)
    next_plan = app._plan(loaded_facts, policy_text, prior)
    assert next_plan.reasons == ("likeness: seed",) and not next_plan.arm.faceswap
    for arm in (loaded.arm, next_plan.arm):
        _, presets, compiled, _ = app.round_render(stored, arm)
        args = {
            "guide_file": "guide.mkv",
            "picture_files": {"target": "picture.png"},
            "seed": 7,
            "output_prefix": "legacy",
            "presets": presets,
        }
        assert compiled.text == compiled.prompt.render()
        assert build_replacement_graph(compiled, **args) == build_replacement_graph(
            compile_replacement(facts, arm), **args
        )
    assert (
        app._request({"source_asset": "s", "analysis_operation": "a", "performers": []}).close_up
        is None
    )


@pytest.mark.parametrize("value", [True, 0, -0.1, 1.1, float("nan")])
def test_invalid_policy_threshold(value):
    with pytest.raises(ValueError):
        replace(POLICY.faceswap, close_up_min_face_fraction=value)


def test_invalid_arms_and_missing_lowering_knowledge():
    with pytest.raises(ValueError, match="faceswap"):
        replace(head_plan().arm, faceswap=True)
    with pytest.raises(ValueError, match="faceswap"):
        replace(plan(joint_facts()).arm, faceswap=True)
    with pytest.raises(ValueError, match="close_up"):
        replace(mug_facts(), close_up=1)
    with pytest.raises(ValueError, match="faceswap"):
        Arm("inverted", "single", "active", None, 1, 0, faceswap=1)
    facts = mug_facts()
    arm = replace(plan(facts).arm, faceswap=True)
    absent = replace(render_presets(), faceswap=None)
    with pytest.raises(CapabilityGap, match="FaceSwap"):
        compile_replacement(facts, arm, presets=absent)
    with pytest.raises(CapabilityGap, match="FaceSwap"):
        build_replacement_graph(
            compile_replacement(facts, arm),
            presets=absent,
            guide_file="guide.mkv",
            picture_files={"target": "picture.png"},
            seed=7,
            output_prefix="test",
        )


def test_durable_loop_freezes_close_up_and_stacks_only_after_failed_checks(work, monkeypatch):
    from test_replacement_checks import report
    from test_replacement_runner import analysed, drain, read

    db, library, runner, _, _ = work
    monkeypatch.setattr(app, "replacement_policy_text", replacement_policy_text)
    runner.comfy.window_face = ((100, 25, 136, 275), 7.0)
    _, _, choice = analysed(work)
    choice["treatment_override"] = "inverted"  # Recast freezes treatment for every window.
    runner.comfy.face_reports = {"performer_0": report(0.55)}
    with db.transaction() as session:
        operation = app.start_replacement(session, choice, 4, library=library)
    assert operation["request"]["facts"]["close_up"] is True
    # Later rounds use frozen source facts, even when new measurements would disagree.
    runner.comfy.window_face = None
    drain(runner)
    finished = read(db, operation["id"])
    assert finished["state"] == "succeeded"
    renders = [j for j in finished["jobs"] if j["kind"] == "video" and j["executor"] == "comfy"]
    assert [j["seed"] for j in renders] == [7, 8, 9, 10]
    assert ["faceswap" in j["graph"] for j in renders] == [False, False, True, True]
    assert all("audio_lock" in j["graph"] for j in renders)
    assert finished["review"][0]["reasons"] == ["likeness: faceswap"]
    assert finished["review"][1]["reasons"] == ["likeness: seed"]
    assert len(finished["candidates"]) == 4


def test_close_up_target_likeness_failure_escalates_to_faceswap():
    """B2: a calibrated close-up likeness failure alone reaches the FaceSwap row."""
    facts = replace(mug_facts(), close_up=True, speaking=True)
    first = plan(facts).arm
    second = plan(facts, (attempt(first, (result("arcface_target"),)),))
    assert second.reasons == ("likeness: faceswap",)
    assert second.arm.faceswap
