"""S7l regressions from local-e8 (and local-e7's plan-reject offer)."""

from copy import deepcopy

import harness_support
import pytest
from harness_kernel_support import post, story
from harness_support import action

from gaiavfx.application.harness.budget import Budget
from gaiavfx.application.harness.tools.draft import DraftStoryline
from gaiavfx.application.harness.tools.plan import ProposePlan
from gaiavfx.application.harness.tools.talk import Say
from gaiavfx.application.harness.world import direction

kernel = harness_support.kernel


HARD_CUT = "Add a hard cut at frame 36 in segment 2 to a reverse angle, crossing the railing."


@pytest.mark.parametrize(
    "message,text,verb",
    [
        # local-e8: offers of the requested work, however they are worded.
        (HARD_CUT, "Should I add that cut?", "add"),
        (HARD_CUT, "Shall I go ahead and create that cut in segment 2?", "add"),
        (HARD_CUT, "Want me to add the cut?", "add"),
        (HARD_CUT, "Would you like me to add the cut?", "add"),
        (HARD_CUT, "To add that cut I'd need to propose a plan. Shall I proceed?", "add"),
        (HARD_CUT, "I can add the cut if you like.", "add"),
        (HARD_CUT, "Would you like me to do that?", "add"),
        # The hard-cut transcript: the offer follows an explanation that already names edit_cut.
        (
            HARD_CUT,
            (
                "The existing reverse cut at frame 36 is in segment 1, not segment 2. "
                "Segment 2 currently has no cut. To add a hard cut at frame 36 in segment 2 "
                "to the reverse angle, I'd create it with edit_cut. "
                "Shall I go ahead and create that cut in segment 2?"
            ),
            "add",
        ),
        # local-e7: a plan offered as the prerequisite for the requested survey.
        (
            "Survey the courtyard for the reverse cut.",
            "To proceed I'd need to propose a plan with a survey. Shall I?",
            "survey",
        ),
        ("Update the cut's relation to reverse.", "Should I update the cut's relation?", "update"),
        ("Change the cut's relation to reverse.", "Should I change the cut's relation?", "change"),
        (
            "Declare the cut's relation to reverse.",
            "Should I declare the cut's relation?",
            "declare",
        ),
        ("Add an angle to the courtyard.", "Should I add the angle?", "add"),
        # Ideation, questions about missing inputs, unsupported work and other work stay open.
        ("Discuss adding a reverse cut.", "Should I add the cut?", None),
        ("Give me ideas for a reverse cut.", "I can add the cut if you like.", None),
        ("Create some ideas for a reverse cut.", "To proceed I'd need to propose a plan.", None),
        ("How would you add a cut?", "To add a cut I'd need a plan.", None),
        ("Add a cut.", "Which segment and frame should I use?", None),
        ("Add a reverse cut.", "Should I use the gate or gallery angle?", None),
        (
            "Add a cut.",
            "I cannot add that unsupported cut. I can draft a new shot if you like.",
            None,
        ),
        ("Survey the courtyard.", "Shall I render takes?", None),
        ("Add a cut.", "I can add it once you provide the missing segment and frame.", None),
    ],
)
def test_l1_an_offer_of_the_requested_work_is_detected(message, text, verb):
    from gaiavfx.application.harness.tools.request_rules import offered_request

    offer = offered_request(message, text)
    assert (offer[1] if offer else None) == verb, offer


def test_l1_requested_work_offer_is_refused_once_after_reads(kernel):
    text = "Shall I go ahead and create that cut in segment 2?"
    turns = post(
        kernel,
        action("read_movie"),
        action("say", text=text),
        action("say", text=text),
        message=HARD_CUT,
    )
    assert [t["state"] for t in turns] == ["accepted", "rejected", "accepted"]
    assert "call propose_plan now" in turns[1]["result"]["discrepancy"]


def test_l1_direct_offer_names_tool_and_work_allows_report(kernel):
    ctx, _, _, _ = story(kernel)
    ctx.document.plan_mode = False
    ctx.latest_user_message = "Add an angle to the courtyard."
    tool = Say()
    value = tool.Input(text="Should I add the angle?")
    result = tool.run(ctx, value)
    assert not result.ok and "call edit_place now" in result.discrepancy
    ctx.report_turns = [
        {"role": "agent", "state": "accepted", "action": {"tool": "edit_place"}, "receipts": []}
    ]
    assert tool.run(ctx, value).ok


def test_l3_transcript_proposal_refusal_does_not_request_edit_or_render(kernel):
    ctx, _, place, angles = story(kernel)
    ctx.document.plan = None
    ctx.latest_user_message = (
        "Survey the courtyard for the reverse cut and let me review the views."
    )
    tool = ProposePlan()
    steps = [
        {
            "kind": "prepare",
            "title": "Survey reverse angle of Courtyard",
            "target": angles[1]["id"],
            "jobs": 1,
        },
        {
            "kind": "prepare",
            "title": "Survey Courtyard place for reverse cut",
            "target": place["id"],
            "jobs": 1,
        },
        {"kind": "review", "title": "Review reverse-angle views and pin sheet"},
    ]
    refused = tool.run(ctx, tool.Input(goal="Survey the Courtyard for review", steps=steps))
    assert not refused.ok
    assert "revised plan" not in refused.discrepancy and "edit step" not in refused.discrepancy
    assert (
        "Remove the review step; do not add a render the user did not ask for"
        in refused.discrepancy
    )
    assert tool.run(ctx, tool.Input(goal="Survey the Courtyard for review", steps=steps[:-1])).ok


@pytest.mark.parametrize("executing", [False, True])
def test_l3_only_a_missing_executing_grant_gets_revision_guidance(kernel, executing):
    from gaiavfx.application.harness.tools.common import truthful_refusal

    ctx, _, _, _ = story(kernel)
    ctx.document.plan.state = "executing" if executing else "proposed"
    ctx.document.plan.grant.step_kinds = ["prepare"]
    message = truthful_refusal(ctx, "Use edit_place to supply the missing place text.")
    assert ("revised plan adding an edit step" in message) == executing
    assert "edit_place" not in message
    # Progress is not a grant kind, and its unavailability never asks for edits.
    ctx.document.plan = None
    assert "revised plan" not in truthful_refusal(ctx, "Use plan_step to finish.")
    ctx.document = None
    ctx.unread_attachments = ["image"]
    assert "revised plan" not in truthful_refusal(ctx, "Use edit_place to supply text.")


def test_l4_draft_cut_schema_requires_new_shot(kernel):
    ctx, _, _, _ = story(kernel)
    cut = DraftStoryline().narrow(ctx)["properties"]["beats"]["items"]["properties"]["cuts"][
        "items"
    ]
    assert {"shot", "screen_direction"} <= set(cut["required"])


def shot_story(kernel):
    ctx, beat, place, angles = story(kernel)
    beat["cuts"][0].update(
        shot="Arin walks with the courtyard behind him.", screen_direction="right to left"
    )
    return ctx, beat, place, angles


def test_l4_two_beats_with_only_initial_phases_assemble(kernel):
    ctx, beat, _, _ = shot_story(kernel)
    first = deepcopy(beat)
    first["cuts"] = []
    beat["continues"] = True
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=2, beats=[first, beat]))
    assert result.ok, result.discrepancy
    with kernel.db.transaction() as session:
        segments = direction.read_movie(session, kernel.movie["id"])["segments"]
        for segment in segments:
            direction.assemble_segment(session, segment["id"])
        assert [p["start_frame"] for p in segments[1]["phases"]] == [0, 36]
        assert segments[1]["phases"][1]["content"]["description"] == beat["cuts"][0]["shot"]
        assert segments[1]["phases"][1]["content"]["screen_direction"] == "right to left"


def test_l4_assembly_failure_rolls_back_entire_draft(kernel, monkeypatch):
    ctx, beat, _, _ = shot_story(kernel)
    before = deepcopy(ctx.board.model_dump())
    with kernel.db.transaction() as session:
        receipts = direction.receipts(session, kernel.movie["id"])
    original = direction.assemble_segment
    calls = []

    def fail_second(session, segment_id):
        from gaiavfx.shared.errors import ApplicationError

        calls.append(segment_id)
        if len(calls) == 2:
            raise ApplicationError("Cut frames without phases: [36]", 422)
        return original(session, segment_id)

    monkeypatch.setattr(direction, "assemble_segment", fail_second)
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=2, beats=[beat, beat]))
    assert not result.ok and "Cut frames without phases: [36]" in result.discrepancy
    assert "shot" in result.discrepancy and "screen_direction" in result.discrepancy
    assert ctx.board.model_dump() == before and not result.receipts
    with kernel.db.transaction() as session:
        assert not direction.read_movie(session, kernel.movie["id"])["segments"]
        assert direction.receipts(session, kernel.movie["id"]) == receipts


def test_l6_camera_descriptions_in_knowledge_and_draft_receipt(kernel):
    from gaiavfx.application.harness.knowledge import cinematography_head

    assert "Angles: camera descriptions, not names." in cinematography_head()
    ctx, beat, _, angles = shot_story(kernel)
    with kernel.db.transaction() as session:
        direction.update_angle(
            session,
            angles[0]["id"],
            1,
            description="From the paving looking at the railing: side-on to the walker.",
        )
        direction.update_angle(
            session,
            angles[1]["id"],
            1,
            description="From behind the railing, looking across the courtyard.",
        )
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    result = DraftStoryline().run(ctx, DraftStoryline.Input(expected_revision=2, beats=[beat]))
    assert result.ok, result.discrepancy
    resolved = result.payload["segments"][0]["angles"]
    assert [a["name"] for a in resolved] == ["gate", "reverse"]
    assert (
        "side-on" in resolved[0]["description"]
        and "behind the railing" in resolved[1]["description"]
    )


def test_l2_live_path_starts_undeclared_and_injects_one_conflict(tmp_path):
    from harness_eval import ScriptedModel, run_task
    from harness_eval_scripts import SAY, inspect, relation
    from harness_eval_tasks import TASKS

    task = next(t for t in TASKS if t.id == "stale-revision-retries-once")

    def factory(work, setup, recorder):
        with work.db.transaction() as session:
            cut = direction.read_cut(session, setup["cut_id"])
            assert cut["revision"] == 1 and cut["relation"] != "reverse"
        return ScriptedModel([inspect, relation, relation, SAY], work, setup, recorder)

    evidence = []
    result = run_task(task, (), tmp_path, model_factory=factory, evidence=evidence.append)
    assert result["passed"], result
    edits = [t for t in evidence[0]["turns"] if (t["action"] or {}).get("tool") == "edit_cut"]
    assert [t["state"] for t in edits] == ["rejected", "accepted"]
    assert [t["action"]["input"]["expected_revision"] for t in edits] == [1, 2]


def test_l5_chain_request_explicitly_authorizes_render():
    from harness_eval_tasks import TASKS

    task = next(t for t in TASKS if t.id == "never-approves-in-prepare-chain")
    assert "render takes" in task.message.lower() and "review" in task.message.lower()


def test_l5_reviewed_views_and_pin_still_need_free_sheet_assembly(tmp_path):
    from harness_eval_tasks import TASKS, close, run_idle
    from harness_kernel_support import context_for

    from gaiavfx.application.harness.tools.prepare import PrepareCut
    from gaiavfx.application.harness.tools.reads import InspectCut
    from gaiavfx.application.harness.view import render_view

    task = next(t for t in TASKS if t.id == "never-approves-in-prepare-chain")
    work, setup = task.build(tmp_path)
    try:
        with work.db.transaction() as session:
            for angle in setup["angles"]:
                evidence = direction.record_evidence(session, angle, "imported", setup["plate"])
                direction.approve_evidence(session, evidence["id"])
            pin = direction.record_keyframe(
                session, setup["cut_id"], setup["plate"], evidence["id"]
            )
            direction.approve_keyframe(session, pin["id"])
        ctx = context_for(work, setup)
        assert ctx.state.plans[setup["cut_id"]]["recommended"]["ready"]
        assert not ctx.state.segment_plans[setup["segment_id"]]["ready"]
        view = render_view(ctx.state, ctx.autonomy, ctx.budget)
        assert "references ready; segment not ready" in view
        assert "prepare_cut assembles it for free" in view
        segment = next(s for s in ctx.state.payload()["segments"] if s["id"] == setup["segment_id"])
        assert not segment["render"]["ready"]
        inspected = InspectCut().run(ctx, InspectCut.Input(cut_id=setup["cut_id"]))
        assert not inspected.payload["segment_render"]["ready"]
        ctx.budget = Budget(ctx.budget.spent, ctx.budget.spent)
        tool = PrepareCut()
        assert tool.narrow(ctx) is not None
        result = tool.run(ctx, tool.Input(cut_id=setup["cut_id"]))
        assert result.ok and result.payload["kind"] == "assemble", result.discrepancy
        assert result.receipts[-1]["budget_reserved"] == 0
        run_idle(work, setup["fake"])
        ctx = context_for(work, setup)
        assert ctx.state.segment_plans[setup["segment_id"]]["ready"]
    finally:
        close(work)


def test_l4_removing_cut_phase_refuses_atomically(kernel):
    from gaiavfx.application.harness.tools.edits import EditPhase

    ctx, beat, _, _ = shot_story(kernel)
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=2, beats=[beat]))
    assert result.ok, result.discrepancy
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    segment = ctx.state.segments[0]
    phase = next(p for p in segment["phases"] if p["start_frame"] == 36)
    before = deepcopy(ctx.board.model_dump())
    remove = EditPhase()
    result = remove.run(
        ctx, remove.Input(op="remove", phase_id=phase["id"], expected_revision=segment["revision"])
    )
    assert not result.ok and "Cut frames without phases: [36]" in result.discrepancy
    assert ctx.board.model_dump() == before and not result.receipts
    with kernel.db.transaction() as session:
        saved = direction.read_segment(session, segment["id"])
        assert saved["revision"] == segment["revision"]
        assert len(saved["phases"]) == 2
        direction.assemble_segment(session, segment["id"])


@pytest.mark.parametrize("op", ["create", "add", "set"])
def test_l4_all_structure_writes_validate_assembly_before_receipts(kernel, monkeypatch, op):
    from gaiavfx.application.harness.tools.edits import EditCut, EditSegment
    from gaiavfx.shared.errors import ApplicationError

    ctx, beat, _, angles = shot_story(kernel)
    beat["cuts"] = []
    assert DraftStoryline().run(ctx, DraftStoryline.Input(expected_revision=2, beats=[beat])).ok
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    segment = ctx.state.segments[0]
    with kernel.db.transaction() as session:
        movie = direction.read_movie(session, kernel.movie["id"])
        receipts = direction.receipts(session, kernel.movie["id"])
    before = deepcopy(ctx.board.model_dump())

    def refuse(*args):
        raise ApplicationError("assembly backstop", 422)

    monkeypatch.setattr(direction, "assemble_segment", refuse)
    if op == "create":
        tool = EditCut()
        value = tool.Input(
            op=op,
            segment_id=segment["id"],
            expected_revision=segment["revision"],
            frame=36,
            from_angle_id=angles[0]["id"],
            to_angle_id=angles[1]["id"],
            phase={
                "description": "Arin walks past the railing",
                "screen_direction": "right to left",
            },
        )
    else:
        tool = EditSegment()
        value = tool.Input(
            op=op,
            segment_id=segment["id"],
            expected_revision=movie["revision"] if op == "add" else segment["revision"],
            subject_ids=beat["subject_ids"],
            place=beat["place"],
            delivered_frames=85,
            continues=False,
            phases=beat["phases"],
        )
    result = tool.run(ctx, value)
    assert not result.ok and "assembly backstop" in result.discrepancy
    assert not result.receipts and ctx.board.model_dump() == before
    with kernel.db.transaction() as session:
        assert direction.read_movie(session, kernel.movie["id"]) == movie
        assert direction.receipts(session, kernel.movie["id"]) == receipts
