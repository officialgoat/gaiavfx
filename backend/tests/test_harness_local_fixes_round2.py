"""CPU transcript regressions from local-2/local-e2 and their thinking repeats.

Scripted guidance probes establish the contract shown to the model, not live
model compliance. Recovery probes use the actual grammar, refusals and stores.
"""

import json
from copy import deepcopy
from types import SimpleNamespace

import harness_support
import pytest
from harness_eval import ScriptedModel, run_task
from harness_eval_live import RecordedAssistant
from harness_eval_outcomes import evaluate
from harness_eval_scripts import GOOD, SAY, action, edit_plan
from harness_eval_tasks import QUALITY_TASKS, TASKS


def task(name):
    return next(t for t in (*TASKS, *QUALITY_TASKS) if t.id == name)


CPU_SETTINGS = {
    "assistant_agent_connection": "cpu",
    "assistant_connections": [
        {"id": "cpu", "name": "CPU", "model": "stub", "base_url": "http://unused.invalid"}
    ],
}


def live_factory(sequence):
    def factory(work, setup, recorder):
        replies = iter(sequence)

        def run(*args, **kwargs):
            entry = next(replies, SAY)
            return entry(work, setup) if callable(entry) else entry

        return RecordedAssistant(SimpleNamespace(run=run), None, recorder)

    return factory


@pytest.mark.parametrize(
    "name", ["P1", "draft-storyline-under-plan", "drafts-structure-from-brief"]
)
def test_b1_story_creation_is_action_guidance(tmp_path, monkeypatch, name):
    original = ScriptedModel.call

    def guided(self, settings, messages, schema, cancelled, log_path):
        text = json.dumps(messages).lower()
        assert "say ends the turn; if you intend to act, call the tool now" in text
        if isinstance(messages[-1].get("content"), list):
            # A reference-notes turn has no workflow actions or plan-mode rule.
            assert "propose_plan" not in text
        elif name != "drafts-structure-from-brief":
            assert (
                "create, draft, edit, prepare and render need propose_plan and user approval"
                in text
            )
        else:
            assert "act directly with the tools; reply with say when done" in text
            assert "propose_plan" not in text
        return original(self, settings, messages, schema, cancelled, log_path)

    monkeypatch.setattr(ScriptedModel, "call", guided)
    result = run_task(task(name), GOOD[name], tmp_path)
    assert result["passed"], result


@pytest.mark.parametrize("name", ["declare-relation", "stale-revision-retries-once"])
def test_b2_relation_result_stops_scope_creep(tmp_path, monkeypatch, name):
    original = ScriptedModel.call
    seen = []

    def guided(self, settings, messages, schema, cancelled, log_path):
        text = json.dumps(messages)
        assert "Producers need a preparation/render request" in text
        with self.work.db.transaction() as session:
            from gaiavfx.modules.agent.service import AgentStore

            turns = AgentStore().turns(session, self.setup["session_id"])
        edits = [
            t
            for t in turns
            if t["state"] == "accepted" and (t["action"] or {}).get("tool") == "edit_cut"
        ]
        if edits:
            assert "Declared. Offer the next step; do not prepare unless asked." in text
            seen.append(True)
        return original(self, settings, messages, schema, cancelled, log_path)

    monkeypatch.setattr(ScriptedModel, "call", guided)
    result = run_task(task(name), GOOD[name], tmp_path)
    assert result["passed"] and seen, result


def test_b3_render_target_required_at_live_grammar(tmp_path, monkeypatch):
    original = RecordedAssistant.call
    checked = []

    def checked_call(self, settings, messages, schema, cancelled, log_path):
        from harness_eval import missing_required

        for branch in schema.get("oneOf", [schema]):
            if branch["properties"]["tool"].get("const") == "render_candidates":
                value = branch["properties"]["input"]
                assert "segment_id" in value["required"]
                assert "cut_id" not in value["properties"]
                assert "input.segment_id" in missing_required(
                    action("render_candidates", count=1), branch
                )
                checked.append(True)
        return original(self, settings, messages, schema, cancelled, log_path)

    monkeypatch.setattr(RecordedAssistant, "call", checked_call)
    spec = task("plan-revise-and-render")
    result = run_task(
        spec, (), tmp_path, model_factory=live_factory(GOOD[spec.id]), settings=CPU_SETTINGS
    )
    assert result["passed"] and checked, result


def test_b4_relation_prepare_plan_refused_then_corrected(tmp_path):
    def wrong_kind(work, setup):
        proposal = edit_plan(work, setup)
        proposal["input"]["steps"][0].update(
            kind="prepare", title="Declare reverse relation", jobs=1
        )
        return proposal

    spec = task("plan-deviation-refused")
    observed = []
    result = run_task(
        spec,
        (),
        tmp_path,
        model_factory=live_factory([wrong_kind, *GOOD[spec.id]]),
        settings=CPU_SETTINGS,
        evidence=observed.append,
    )
    assert result["passed"], result
    rejected = [t for t in observed[0]["turns"] if t["state"] == "rejected"]
    assert len(rejected) == 1
    assert "edit" in rejected[0]["result"]["discrepancy"]
    assert "kind" in rejected[0]["result"]["discrepancy"]


def test_b5_plate_retry_uses_concrete_refusal_values(tmp_path):
    def retry(work, setup):
        from gaiavfx.modules.agent.service import AgentStore

        with work.db.transaction() as session:
            turns = AgentStore().turns(session, setup["session_id"])
        refusal = next(
            t["result"]["discrepancy"] for t in reversed(turns) if t["state"] == "rejected"
        )
        assert "plate is required" in refusal
        # The grammar now requires the plate before execution. The direct-call
        # backstop still supplies a concrete, schema-valid recovery example.
        from gaiavfx.application.harness.loop import Harness
        from gaiavfx.application.harness.session import AgentSession
        from gaiavfx.application.harness.tools.prepare import PrepareCut

        harness = Harness(work.db, AgentStore(), None, work.settings)
        row = harness.read(setup["session_id"])
        context = harness.context(row, AgentSession.model_validate(row["document"]), turns)
        tool = PrepareCut()
        refusal = tool.run(context, tool.Input(cut_id=setup["cut_id"])).discrepancy
        hint = json.loads(refusal.split("Example input: ", 1)[1])
        assert hint["plate"]["source_asset"] == setup["plate"]
        assert "frame" not in hint["plate"]
        assert hint["plate"]["angle_id"] in setup["angles"]
        assert "Arin" in hint["plate"]["whom"]
        return action("prepare_cut", **hint)

    spec = task("plate-from-removal")
    sequence = list(GOOD[spec.id])
    sequence[2] = retry
    result = run_task(
        spec, (), tmp_path, model_factory=live_factory(sequence), settings=CPU_SETTINGS
    )
    assert result["passed"], result


@pytest.mark.parametrize("name", ["plan-propose-approve-execute", "plan-revise-and-render"])
def test_w4_live_stub_reviews_and_resumes_with_render_read(tmp_path, name):
    from harness_eval import summarise
    from harness_eval_scripts import candidates

    observed = []
    # W3 exempts surveys. S7g requires reading a render before its report.
    sequence = [
        entry for entry in GOOD[name] if name == "plan-revise-and-render" or entry is not candidates
    ]
    result = run_task(
        task(name),
        (),
        tmp_path,
        model_factory=live_factory(sequence),
        settings=CPU_SETTINGS,
        evidence=observed.append,
    )
    assert result["passed"], result
    assert result["outcome_rubric"]["plan_completed"]
    assert result["outcome_rubric"]["plan_review_resume"]
    assert result["human_actions"]
    assert all(a["actor"] == "eval_user" for a in result["human_actions"])
    assert summarise([result])["results"][0]["human_actions"] == result["human_actions"]
    assert observed[0]["row"]["state"] == "idle"
    for corruption, criterion in [
        ("unfinished", "plan_completed"),
        ("early_resume", "plan_review_resume"),
    ]:
        data = deepcopy(observed[0])
        if corruption == "unfinished":
            data["row"]["document"]["plan"]["state"] = "executing"
        else:
            data["setup"]["no_false_resume"] = False
        assert not evaluate(task(name), data)[0][criterion]


@pytest.mark.parametrize(
    "name", ["missing-coverage-prepares", "interrupted-wait-resumes", "plan-mode-off-direct"]
)
def test_w3_survey_review_stop_without_read_has_real_controls(tmp_path, name):
    from harness_eval_scripts import candidates

    observed = []
    sequence = [entry for entry in GOOD[name] if entry is not candidates]
    result = run_task(
        task(name),
        (),
        tmp_path,
        model_factory=live_factory(sequence),
        settings=CPU_SETTINGS,
        evidence=observed.append,
    )
    assert result["passed"], result
    for corruption in ("missing_survey", "approval", "missing_review", "failed_survey"):
        data = deepcopy(observed[0])
        if corruption == "missing_survey":
            data["final"]["operations"] = []
        elif corruption == "approval":
            data["final"]["pending"] = []
        elif corruption == "failed_survey":
            data["final"]["operations"][0]["state"] = "failed"
        else:
            data["turns"][-1]["action"]["input"]["text"] = "Done."
        assert not evaluate(task(name), data)[0]["survey_review_stop"], corruption


@pytest.mark.parametrize(
    "question,ok",
    [
        ("Which person, 1 or 2, on the numbered contact sheet?", True),
        ("Which person should I use?", False),
        ("Replacement is unsupported; shall I draft a new shot?", False),
    ],
)
def test_p3_requires_numbered_disambiguation(tmp_path, question, ok):
    result = run_task(
        task("P3"), [*GOOD["P3"][:-1], action("ask_human", question=question)], tmp_path
    )
    assert result["rubric"]["replacement_outcome"] is ok, result
    assert result["passed"] is ok, result


def test_p3_correct_words_do_not_excuse_changes(tmp_path):
    from harness_eval_replacement import rubric
    from harness_outcome_controls import corrupt_outcome

    captured = []
    result = run_task(task("P3"), GOOD["P3"], tmp_path, evidence=captured.append)
    assert result["passed"], result
    for violation in ("plan", "edit", "operation", "selection"):
        changed, criterion = corrupt_outcome("P3", captured[0], violation)
        assert not rubric(task("P3"), changed)[criterion], violation


world = harness_support.world


def test_recovery_examples_are_scoped_executable_and_rollback_safe(world):
    from test_harness_tools import context_for

    from gaiavfx.application.harness.actions import parse_action
    from gaiavfx.application.harness.shots import shots
    from gaiavfx.application.harness.tools.edits import EditCut
    from gaiavfx.application.harness.tools.input_hints import plate_input
    from gaiavfx.application.harness.tools.places import EditPlace
    from gaiavfx.application.harness.tools.prepare import PrepareCut
    from gaiavfx.application.harness.tools.reads import InspectCut
    from gaiavfx.modules.direction.service import DirectionService

    work, setup = world
    direction = DirectionService()
    with work.db.transaction() as session:
        place = direction.read_place(session, setup["place_id"])
        direction.update_place(session, place["id"], place["revision"], description="")
    ctx = context_for(work, setup)
    tool = PrepareCut()
    refused = tool.run(ctx, tool.Input(cut_id=setup["cut_id"]))
    assert not refused.ok and "place_text_missing" in refused.discrepancy
    recovery = json.loads(refused.discrepancy.split("Example input: ")[1])
    assert recovery["place_id"] == setup["place_id"]
    assert "sound" not in recovery and "overhead" not in recovery  # preserve authored text
    editor = EditPlace()
    _, value = parse_action(action("edit_place", **recovery), [(editor, editor.narrow(ctx))])
    assert editor.run(ctx, value).ok
    ctx = context_for(work, setup)
    with work.db.transaction() as session:
        before = direction.read_place(session, setup["place_id"])
    refused = editor.run(
        ctx,
        editor.Input(
            op="set",
            place_id=before["id"],
            expected_revision=before["revision"],
            sound="must roll back",
            angles=[{}],
        ),
    )
    assert not refused.ok
    recovery = json.loads(refused.discrepancy.split("Example input: ")[1])
    assert recovery["angles"][0]["angle_id"] in setup["angles"]
    assert recovery["expected_revision"] == before["revision"]
    with work.db.transaction() as session:
        assert direction.read_place(session, before["id"]) == before

    # H8: a place image comes before footage. Without one, preserve the selected-take fallback.
    ctx.state.segments[0]["selection"] = {"asset_id": setup["take"]}
    hint = json.loads(
        plate_input(ctx, next(s for s in shots(ctx.state) if s["kind"] == "cut")).split(
            "Example input: "
        )[1]
    )
    assert hint["plate"]["source_asset"] == setup["plate"] and "frame" not in hint["plate"]
    ctx.state.assets = [a for a in ctx.state.assets if a["kind"] == "video"]
    hint = json.loads(
        plate_input(ctx, next(s for s in shots(ctx.state) if s["kind"] == "cut")).split(
            "Example input: "
        )[1]
    )
    assert hint["plate"]["source_asset"] == setup["take"] and hint["plate"]["frame"] == 0

    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
        direction.update_cut(
            session,
            cut["id"],
            cut["revision"],
            relation=None,
            crosses_barrier=None,
            relation_source="unknown",
        )
    ctx = context_for(work, setup)
    inspected = InspectCut().run(ctx, InspectCut.Input(cut_id=setup["cut_id"]))
    assert inspected.payload["plan_step_kinds"]["declare_relation"] == "edit"
    assert EditCut().narrow(ctx)  # hint maps to the existing edit tool


@pytest.mark.parametrize(
    "title,wrong",
    [
        ("declare_relation", True),
        ("Declare the reverse across the railing", True),
        ("Draft three new segments", True),
        ("Edit cast", True),
        ("Add a place", True),
        ("Survey the new place", False),
        ("Prepare reverse-angle views", False),
        ("Insert cast into the reviewed view", False),
        ("Remove cast from the source frame", False),
        ("Add cast into a reviewed view", False),
        ("Create a place view", False),
        ("Assemble the sheet", False),
    ],
)
def test_step_kind_validation_keeps_real_preparation_legal(title, wrong):
    from gaiavfx.application.harness.tools.plan import edit_title

    assert edit_title(title) is wrong
