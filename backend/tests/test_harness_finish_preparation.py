"""S7q: run 8 turns 51/56 and local-e12's repeated approved-plate review."""

import json
from copy import deepcopy
from pathlib import Path

import harness_support
import pytest
import test_harness_e2e_tools
from harness_eval_tasks import run_idle
from harness_support import action
from test_harness_round5 import executing
from test_harness_round6 import drafted
from test_harness_tools import context_for

from gaiavfx.application.harness.actions import action_schema, parse_action
from gaiavfx.application.harness.context import assemble
from gaiavfx.application.harness.knowledge import cinematography_head, unsupported_head
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.plans import review_outcome
from gaiavfx.application.harness.prompt import system_prompt
from gaiavfx.application.harness.session import PlanStep
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.application.harness.tools.candidates import ReadCandidates
from gaiavfx.application.harness.tools.insertion import resolve_insertion
from gaiavfx.application.harness.tools.plan import PlanStepTool
from gaiavfx.application.harness.tools.prepare import PrepareCut
from gaiavfx.application.harness.tools.render import RenderCandidates
from gaiavfx.application.harness.tools.talk import AskHuman, Say
from gaiavfx.application.harness.view import render_view
from gaiavfx.application.harness.world import direction, operations, reviews
from gaiavfx.application.prepare_references import (
    approve_pin,
    approve_plate,
    open_insertion_review,
    open_plate_review,
)
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.shared.errors import ApplicationError

world = harness_support.world
kernel = harness_support.kernel
unplated = test_harness_e2e_tools.unplated
RECORD = json.loads(
    (Path(__file__).parent / "fixtures/harness/run8_preparation.json").read_text("utf-8")
)


def approved_references(work, setup, *, continues=False):
    with work.db.transaction() as session:
        segment = direction.read_segment(session, setup["segment_id"])
        direction.update_segment(session, segment["id"], segment["revision"], continues=continues)
        for angle in setup["angles"]:
            evidence = direction.record_evidence(session, angle, "imported", setup["plate"])
            direction.approve_evidence(session, evidence["id"])
        pin = direction.record_keyframe(session, setup["cut_id"], setup["plate"], evidence["id"])
        direction.approve_keyframe(session, pin["id"])


def plan_context(work, setup, *, render=False):
    ctx = context_for(work, setup)
    ctx.document.plan_mode = True
    ctx.document.plan = executing(
        PlanStep(kind="prepare", title="Survey", target=setup["cut_id"], status="done", jobs=1),
        PlanStep(
            kind="prepare",
            title="Insert Arin",
            target=setup["cut_id"],
            status="done" if render else "pending",
            jobs=2,
        ),
        PlanStep(kind="render", title="Render two takes", target=setup["segment_id"], jobs=2),
        PlanStep(kind="review", title="Review takes"),
    )
    return ctx


def check_budget(ctx):
    tools = applicable_tools(ctx)
    report = assemble(
        system_prompt(ctx, tools),
        ctx.board.brief,
        "\n".join(f"{tool.name}: {tool.description}" for tool, _ in tools),
        action_schema(tools),
        ctx.board,
        render_view(
            ctx.state, ctx.autonomy, ctx.jobs_reserved, ctx.jobs_limit, document=ctx.document
        ),
        [],
        32768,
        output=2400,
        schema_billed=True,
        knowledge=unsupported_head() + "\n" + cinematography_head(),
    ).report
    assert report["total_reserved"] <= 32768
    print("S7q budget:", {k: report[k] for k in ("head", "schema", "view", "total_reserved")})


def test_q1_run8_last_prepare_cannot_finish_before_free_sheet(world):
    work, setup = world
    approved_references(work, setup)
    ctx = plan_context(work, setup)
    check_budget(ctx)
    grant = deepcopy(ctx.document.plan.grant)
    tool = PlanStepTool()
    result = tool.run(ctx, tool.Input(**RECORD["finish"]))
    assert not result.ok
    assert "sheet (free): call prepare_cut once more" in result.discrepancy
    assert "costs no jobs" in result.discrepancy
    assert ctx.document.plan.steps[1].status == "pending"
    assembly = PrepareCut().run(ctx, PrepareCut.Input(cut_id=setup["cut_id"]))
    assert assembly.ok, assembly.discrepancy
    assert assembly.receipts[-1]["budget_reserved"] == 0
    run_idle(work, setup["fake"])
    ctx.state = context_for(work, setup).state
    assert tool.run(ctx, tool.Input(**RECORD["finish"])).ok
    assert ctx.document.plan.grant == grant


def test_q1_earlier_prepare_can_finish_but_unrelated_later_step_cannot_hide_needs(world):
    work, setup = world
    ctx = plan_context(work, setup)
    ctx.document.plan.steps[0].status = "pending"
    tool = PlanStepTool()
    assert tool.run(ctx, tool.Input(step=0, status="done")).ok
    ctx.document.plan.steps.insert(2, PlanStep(kind="prepare", title="Elsewhere", target="other"))
    result = tool.run(ctx, tool.Input(step=1, status="done"))
    assert not result.ok and "survey" in result.discrepancy and "pin" in result.discrepancy
    # Explicitly stopping limited work remains possible without claiming cut readiness.
    assert tool.run(
        ctx, tool.Input(step=1, status="skipped", note="Remaining work not requested")
    ).ok


def test_q1_render_focus_allows_only_zero_job_sheet_with_existing_prepare_grant(world):
    from harness_eval_tasks import TASKS, prepare_fixture

    work, setup = world
    prepare_fixture(
        next(t for t in TASKS if t.id == "never-approves-in-prepare-chain"), work, setup
    )
    approved_references(work, setup, continues=True)
    ctx = plan_context(work, setup, render=True)
    check_budget(ctx)
    grant = deepcopy(ctx.document.plan.grant)
    tools = applicable_tools(ctx)
    assert "prepare_cut" in {tool.name for tool, _ in tools}
    tool, value = parse_action(action("prepare_cut", cut_id=setup["cut_id"]), tools)
    assert "plate" not in tool.narrow(ctx)["properties"]
    assert "insertion" not in tool.narrow(ctx)["properties"]
    result = tool.run(ctx, value)
    assert result.ok, result.discrepancy
    assert result.payload["kind"] == "assemble"
    assert result.receipts[-1]["budget_reserved"] == 0
    assert ctx.document.plan.grant == grant
    run_idle(work, setup["fake"])
    ctx.state = context_for(work, setup).state
    view = render_view(ctx.state, ctx.autonomy, 0, 6, document=ctx.document)
    assert "outside this scope" not in view and "no preparable cut" not in view
    assert "prepare_cut" not in {tool.name for tool, _ in applicable_tools(ctx)}
    ctx.turn_id = "render-after-sheet"
    rendered = RenderCandidates().run(
        ctx, RenderCandidates.Input(segment_id=setup["segment_id"], count=2)
    )
    assert rendered.ok, rendered.discrepancy
    assert rendered.receipts[-1]["budget_reserved"] == 2
    assert ctx.document.plan.grant == grant


def test_q1_render_does_not_gain_jobs_or_prepare_authority(world):
    work, setup = world
    ctx = plan_context(work, setup, render=True)
    prepare = PrepareCut()
    assert "prepare_cut" not in {t.name for t, _ in applicable_tools(ctx)}
    assert not prepare.run(ctx, prepare.Input(cut_id=setup["cut_id"])).ok
    approved_references(work, setup)
    ctx.state = context_for(work, setup).state
    ctx.document.plan.grant.step_kinds = ["render", "review"]
    assert "prepare_cut" not in {t.name for t, _ in applicable_tools(ctx)}
    assert not prepare.run(ctx, prepare.Input(cut_id=setup["cut_id"])).ok
    ctx.document.plan.grant.step_kinds.append("prepare")
    ctx.document.plan.steps[2].target = "another-segment"
    assert "prepare_cut" not in {t.name for t, _ in applicable_tools(ctx)}
    assert not prepare.run(ctx, prepare.Input(cut_id=setup["cut_id"])).ok


def test_q1_run8_pin_resume_does_not_instruct_premature_completion(world):
    from test_harness_honest_retries import approved_views, prepare

    work, setup = world
    approved_views(work, setup)
    pin = prepare(work, setup, seed=42)
    assert pin.ok, pin.discrepancy
    run_idle(work, setup["fake"])
    ctx = plan_context(work, setup)
    ctx.document.plan.steps[1].status = "awaiting_user"
    ctx.document.plan.steps[1].review_of = pin.waits_on
    harness = Harness(work.db, AgentStore(), None, work.settings)
    with work.db.transaction() as session:
        review = open_insertion_review(session, pin.waits_on)
        approve_pin(session, candidate_id=review["members"][0]["candidate_id"])
        row = harness.store.read_session(session, ctx.session_id)
        assert harness._complete_reviews(session, row, ctx.document)
        message = harness.store.turns(session, ctx.session_id)[-1]["action"]["message"]
    assert "sheet (free)" in message and "mark it done with plan_step" not in message
    assert ctx.document.plan.steps[1].status == "pending"


@pytest.mark.parametrize("field", ["where", "distance_description"])
@pytest.mark.parametrize(
    "text",
    [
        RECORD["insertion"]["where"],
        "standing beyond the railing",
        "pauses on paving",
        "waits in the courtyard",
        "at the railing",
        "one arm's length from the iron bars",
        "an arm’s length from the railing",
        "next to the railing",
        "against the railing",
        "a short step from the bars",
        "facing the camera",
        "facing the courtyard",
    ],
)
def test_q2_run8_walking_pin_refuses_static_close_or_facing_prose(kernel, field, text):
    ctx, segment = drafted(kernel)
    cut = segment["cuts"][0]
    # Run 8 described a position after the cut; its screen direction carries the walk.
    with kernel.db.transaction() as session:
        phase = segment["phases"][-1]
        direction.update_phase(
            session,
            phase["id"],
            phase["revision"],
            content={
                **phase["content"],
                "description": "Arin beyond the railing with the sunlit courtyard behind him",
                "screen_direction": "right to left",
            },
        )
    wording = {
        **RECORD["insertion"],
        "where": "on the open paving beyond the railing",
        "distance_description": "distant, small",
        "size": "one fifth",
        field: text,
    }
    with kernel.db.transaction() as session, pytest.raises(ApplicationError) as error:
        resolve_insertion(session, ctx, cut, wording)
    message = str(error.value)
    assert field in message
    assert "on the open paving beyond the railing" in message
    assert "distant, small" in message
    assert "8 of 8" in message


def test_q2_position_for_walk_passes_and_nonmoving_nonreverse_controls(kernel):
    ctx, segment = drafted(kernel)
    cut = segment["cuts"][0]
    wording = {
        **RECORD["insertion"],
        "where": "on the open paving beyond the railing",
        "distance_description": "distant, small",
        "size": "one fifth",
    }
    with kernel.db.transaction() as session:
        result = resolve_insertion(session, ctx, cut, wording)
        assert result["facing"] == "screen_left"
        for changed in ({"crosses_barrier": False}, {"relation": "side"}):
            assert resolve_insertion(
                session, ctx, {**cut, **changed}, {**wording, "where": "standing at the railing"}
            )
        phase = segment["phases"][-1]
        direction.update_phase(
            session,
            phase["id"],
            phase["revision"],
            content={**phase["content"], "description": "Arin rests", "screen_direction": ""},
        )
        assert resolve_insertion(session, ctx, cut, {**wording, "where": "standing at the railing"})


def plate_review(work, setup):
    prepare = PrepareCut()
    result = prepare.run(
        context_for(work, setup),
        prepare.Input(
            cut_id=setup["cut_id"],
            plate={
                "source_asset": setup["identity"],
                "whom": "Arin",
                "object_pronoun": "him",
                "behind": "the paving",
            },
        ),
    )
    assert result.ok, result.discrepancy
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        return open_plate_review(session, result.waits_on)


def test_q3_completed_plate_review_is_explicit_and_repeat_request_refused_once(unplated):
    work, setup = unplated
    review = plate_review(work, setup)
    with work.db.transaction() as session:
        approve_plate(session, candidate_id=review["members"][0]["candidate_id"])
        message = review_outcome(
            session, setup["movie_id"], operations.operation(session, review["id"])
        )
    assert "plate is approved" in message and "survey" in message
    ctx = context_for(work, setup)
    assert "plate is approved" in ctx.view and "survey" in ctx.view
    check_budget(ctx)
    read = ReadCandidates().run(ctx, ReadCandidates.Input(operation_id=review["id"]))
    assert "plate is approved" in read.payload["message"]
    harness = Harness(work.db, AgentStore(), None, work.settings)
    row = harness.post_message(ctx.session_id, "Continue.", "q3-continue")
    assert "plate is approved" in row["document"]["summary"]
    assert "survey" in row["document"]["summary"]
    assert "plate is approved" in harness.turns(ctx.session_id)[-1]["action"]["message"]
    for text in (
        "The plate is approved. Please review the survey views when they are ready.",
        "You already approved the plate; no further plate review is needed.",
        "Please choose the lighting for the next shot.",
    ):
        assert Say().run(ctx, Say.Input(text=text)).ok
    for tool, value in (
        (Say(), Say.Input(text=RECORD["repeat_plate_report"])),
        (AskHuman(), AskHuman.Input(question="Please review the plate candidates.")),
    ):
        ctx = context_for(work, setup)
        result = tool.run(ctx, value)
        assert (
            not result.ok
            and "the plate is approved; continue with the survey" in result.discrepancy
        )
        ctx.report_turns.append(
            {"role": "agent", "state": "rejected", "result": result.model_dump()}
        )
        assert tool.run(ctx, value).ok
    ctx = context_for(work, setup)
    ctx.turn_id = "survey-after-plate"
    result = PrepareCut().run(ctx, PrepareCut.Input(cut_id=setup["cut_id"]))
    assert result.ok and result.payload["kind"] == "survey"


def test_q3_pending_or_rejected_plate_and_other_reviews_are_not_claimed_approved(unplated):
    work, setup = unplated
    review = plate_review(work, setup)
    tool = Say()
    value = tool.Input(text="Please review the plate candidates in the Review workspace.")
    assert tool.run(context_for(work, setup), value).ok

    with work.db.transaction() as session:
        reviews.reject_references(
            session,
            review["id"],
            expected_revision=review["revision"],
            note="Railing was erased",
            rejected_by="user",
        )
        message = review_outcome(
            session, setup["movie_id"], operations.operation(session, review["id"])
        )
    assert "rejected" in message and "approved" not in message
    assert "plate is approved" not in context_for(work, setup).view
    assert tool.run(context_for(work, setup), value).ok


def test_q3_local_e12_repeat_report_recovers_and_consumes_the_approved_plate(tmp_path):
    from harness_eval import run_task
    from harness_eval_scripts import GOOD, candidates
    from harness_eval_tasks import TASKS

    task = next(t for t in TASKS if t.id == "plate-from-removal")
    sequence = GOOD[task.id]
    observed = []
    result = run_task(
        task,
        [
            *sequence[:5],
            candidates,
            action("say", text=RECORD["repeat_plate_report"]),
            *sequence[5:],
        ],
        tmp_path,
        outcome=True,
        evidence=observed.append,
    )
    assert result["passed"], result
    assert any(
        (t.get("result") or {}).get("discrepancy", "").startswith("plate_already_approved:")
        for t in observed[0]["turns"]
    )


def test_live14_survey_only_request_is_told_to_skip_the_remaining_preparation(world):
    """local-e14 plan-propose: after an approved survey the user's request ("Survey … let
    me review the views") is done; the pin and sheet are outside it."""
    from gaiavfx.application.harness.tools.preparation_progress import completion_refusal

    work, setup = world
    approved_references(work, setup)
    ctx = plan_context(work, setup)
    ctx.latest_user_message = (
        "Survey the courtyard for the reverse cut and let me review the views."
    )
    message = completion_refusal(ctx, 1)
    assert (
        message.startswith("preparation_outside_request:")
        or "preparation_outside_request" in message
    )
    assert "skipped" in message and "Do not prepare more" in message
    ctx.latest_user_message = "Prepare the reverse and render two takes."
    broad = completion_refusal(ctx, 1)
    assert "preparation_outside_request" not in broad
