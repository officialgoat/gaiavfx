"""Scripted regressions from local-1b/local-e1, plus direct dispatch backstops."""

from copy import deepcopy
from dataclasses import replace
from types import SimpleNamespace

import harness_support
import pytest
from harness_eval import ScriptedModel, run_task
from harness_eval_scripts import GOOD, SAY, STEP_DONE, STORY_PLAN, action, prepare, storyline
from harness_eval_tasks import TASKS

from gaiavfx.application.harness.actions import parse_action
from gaiavfx.application.harness.session import AgentSession, Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.application.harness.tools.render import RenderCandidates, render_seeds
from gaiavfx.application.harness.tools.talk import Say
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.shared.errors import ApplicationError

kernel = harness_support.kernel


def task(name):
    return next(t for t in TASKS if t.id == name)


@pytest.mark.parametrize("case", ["approved-survey", "drafted-storyline"])
def test_f1_mid_plan_narration_is_rejected_and_execution_continues(tmp_path, case):
    if case == "approved-survey":
        spec = task("plan-propose-approve-execute")
        sequence = list(GOOD[spec.id])
        sequence.insert(
            sequence.index(prepare),
            action("say", text="Plan approved. I'll now survey the reverse angle."),
        )
    else:
        # E21 drafted all three parts then narrated the still-pending preparation.
        # The fixture has no cuts/jobs to prepare: explicitly skip it with a note.
        spec = replace(
            task("draft-storyline-under-plan"),
            # S7j refuses producer steps on setup-only requests. Keep this older
            # mid-plan narration regression under an explicit prepare request.
            message="Create a three-part storyline and prepare any missing references.",
            expected_tools=(
                "propose_plan",
                "draft_storyline",
                "plan_step",
                "plan_step",
                "say",
            ),
        )
        plan = deepcopy(STORY_PLAN)
        plan["input"]["steps"].append({"kind": "prepare", "title": "Prepare Part 1 cuts"})
        sequence = [
            plan,
            storyline,
            STEP_DONE,
            action("say", text="All three parts are drafted. Now I'll prepare Part 1."),
            action(
                "plan_step",
                step=1,
                status="skipped",
                note="The drafted storyline has no cuts or missing references.",
            ),
            SAY,
        ]
    observed = []
    result = run_task(spec, sequence, tmp_path, evidence=observed.append)
    assert result["passed"], result
    narrations = [t for t in observed[0]["turns"] if t["state"] == "rejected"]
    if case == "approved-survey":
        assert len(narrations) == 2
        assert "call propose_plan now" in narrations[0]["result"]["discrepancy"]
        narrations = narrations[1:]
    assert len(narrations) == 1
    assert narrations[0]["action"]["tool"] == "say"
    assert "say" not in narrations[0]["provenance"]["tools"]
    assert observed[0]["row"]["document"]["plan"]["state"] == "done"


@pytest.mark.parametrize("name", ["draft-storyline-under-plan", "plan-reject"])
def test_f2_action_requests_get_the_rule_at_the_decision_point(tmp_path, monkeypatch, name):
    # This stub reproduces the prose-only answer unless the *view's second line*
    # carries the action rule. It tests model-facing guidance, not model quality.
    original = ScriptedModel.call
    captured = []

    def call(self, settings, messages, schema, cancelled, log_path):
        captured.append(messages)
        view = next(
            m["content"]
            for m in messages
            if isinstance(m.get("content"), str) and "Plan mode on; no plan yet." in m["content"]
        )
        assert "call propose_plan" in view and "ideas the user asked to discuss" in view
        assert "propose_plan:" in str(messages) and "Storyline edits use draft_storyline" in str(
            messages
        )
        return original(self, settings, messages, schema, cancelled, log_path)

    # Inspect just the initial decision; after proposal the view rightly changes.
    def first_call(self, *args):
        monkeypatch.setattr(ScriptedModel, "call", original)
        return call(self, *args)

    monkeypatch.setattr(ScriptedModel, "call", first_call)
    spec = task(name)
    result = run_task(spec, GOOD[name], tmp_path)
    assert result["passed"] and captured, result


def test_f3_review_result_drives_a_workspace_report_not_a_chat_ranking(tmp_path):
    def report(work, setup):
        from gaiavfx.application.harness.loop import Harness
        from gaiavfx.application.harness.prompt import ASK_HUMAN, SYSTEM
        from gaiavfx.application.harness.tools.prepare import PrepareCut

        with work.db.transaction() as session:
            turns = AgentStore().turns(session, setup["session_id"])
        harness = Harness(work.db, AgentStore(), None, work.settings)
        row = harness.read(setup["session_id"])
        context = harness.context(row, AgentSession.model_validate(row["document"]), turns)
        prepared = PrepareCut().run(context, PrepareCut.Input(cut_id=setup["cut_id"]))
        assert prepared.ok and "Review workspace" in prepared.payload["message"]
        assert "Review workspace" in ASK_HUMAN and "Review workspace" in SYSTEM
        result = next(
            t["result"]
            for t in reversed(turns)
            if (t["action"] or {}).get("tool") == "read_candidates"
        )
        if "Review workspace" not in result["payload"].get("message", ""):
            return action("ask_human", question="Could you rank A, B and C here?")
        return SAY

    spec = task("missing-coverage-prepares")
    result = run_task(spec, [*GOOD[spec.id][:-1], report], tmp_path, outcome=True)
    assert result["passed"], result


@pytest.mark.parametrize("explicit", [False, True])
def test_f4_count_without_seeds_renders_two_and_remains_blind(tmp_path, explicit):
    def render(work, setup):
        return action(
            "render_candidates",
            segment_id=setup["segment_id"],
            **({"count": 2} if explicit else {}),
        )

    spec = task("text-renders-never-selects")
    sequence = list(GOOD[spec.id])
    sequence[1] = render
    observed = []
    result = run_task(spec, sequence, tmp_path, evidence=observed.append)
    assert result["passed"], result
    turns = observed[0]["turns"]
    created = next(t for t in turns if (t["action"] or {}).get("tool") == "render_candidates")
    assert created["state"] == "accepted"
    payload = created["result"]["payload"]
    assert payload["count"] == len(set(payload["seeds"])) == 2
    operation = next(
        o for o in observed[0]["final"]["operations"] if o["id"] == payload["operation_id"]
    )
    assert [j["seed"] for j in operation["jobs"] if j["seed"] is not None] == payload["seeds"]
    read = next(t for t in turns if (t["action"] or {}).get("tool") == "read_candidates")
    assert "seeds" not in str(read["result"]) and "seed" not in str(read["result"])


def test_say_backstop_steps_reviews_and_refusal_escape(kernel):
    k = kernel
    doc = AgentSession(
        plan=Plan(
            id="p",
            revision=1,
            state="executing",
            goal="Work",
            proposed_turn=0,
            budget_jobs=0,
            steps=[PlanStep(kind="edit", title="Draft"), PlanStep(kind="prepare", title="Prepare")],
            grant=PlanGrant(
                budget_jobs=0,
                step_kinds=["edit", "prepare"],
                approved_turn=1,
                reserved_at_approval=0,
            ),
        )
    )
    ctx = k.harness.context(k.row, doc, [])
    say = Say()
    assert not say.run(ctx, say.Input(text="I'll start now.")).ok
    names = {t.name for t, _ in applicable_tools(ctx)}
    assert "say" not in names and "prepare_cut" not in names and "board_write" not in names
    assert {"ask_human", "plan_step", "read_movie", "edit_cast"} <= names
    doc.previous_discrepancy = "Invalid field; correct it"
    assert say.narrow(ctx) is None  # edit tools still legal
    doc.plan.steps[0].status = "done"
    assert say.narrow(ctx) is not None  # no cuts or jobs, after a refusal
    doc.previous_discrepancy = ""
    assert say.narrow(ctx) is None
    doc.plan.steps[1].status = "awaiting_user"
    assert say.narrow(ctx) is not None
    doc.plan.steps[1].status = "skipped"
    assert say.run(ctx, say.Input(text="Done.")).ok


def test_count_is_deterministic_bounded_and_explicit_seeds_are_reproducible():
    context = SimpleNamespace(
        session_id="session", turn=5, jobs_limit=4, jobs_reserved=0, document=None
    )
    Input = RenderCandidates.Input
    default = Input(cut_id="cut")
    first = render_seeds(context, default)
    assert first == render_seeds(context, default) and len(set(first)) == 2
    context.turn += 1
    assert first != render_seeds(context, default)
    context.jobs_reserved = 3
    assert len(render_seeds(context, default)) == 1
    assert render_seeds(context, Input(cut_id="cut", seeds=[123])) == [123]
    with pytest.raises(ApplicationError, match="count must match"):
        render_seeds(context, Input(cut_id="cut", count=2, seeds=[123]))
    context.jobs_reserved = 4
    with pytest.raises(ApplicationError, match="budget_exhausted"):
        render_seeds(context, default)


def test_render_count_narrowing_and_backstop(tmp_path):
    from harness_eval_tasks import close

    from gaiavfx.application.harness.loop import Harness

    work, setup = task("text-renders-never-selects").build(tmp_path)
    try:
        h = Harness(work.db, AgentStore(), None, work.settings)
        row = h.open(setup["movie_id"])
        doc = AgentSession.model_validate(row["document"])
        doc.plan_mode = False
        ctx = h.context(row, doc, [])
        ctx.jobs_limit, ctx.jobs_reserved = 2, 1
        tool = RenderCandidates()
        assert tool.narrow(ctx)["properties"]["count"]["maximum"] == 1
        result = tool.run(ctx, tool.Input(cut_id=setup["cut_id"], count=2))
        assert not result.ok and "budget_exhausted" in result.discrepancy
        with pytest.raises(ApplicationError):
            parse_action(
                action("render_candidates", cut_id=setup["cut_id"], seeds=[]), applicable_tools(ctx)
            )
    finally:
        close(work)
