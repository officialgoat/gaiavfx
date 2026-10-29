"""S7l regressions from local-e8 (and local-e7's plan-reject offer)."""

from copy import deepcopy

import harness_support
import pytest
from harness_support import action
from test_harness_follow_through import post, story

from gaiavfx.application.harness.tools.draft import DraftStoryline
from gaiavfx.application.harness.tools.plan import ProposePlan
from gaiavfx.application.harness.tools.talk import Say
from gaiavfx.application.harness.world import direction

kernel = harness_support.kernel


@pytest.mark.parametrize(
    "text",
    [
        "Should I add that cut?",
        "Shall I go ahead and create that cut in segment 2?",
        "Want me to add the cut?",
        "Would you like me to add the cut?",
        "To add that cut I'd need to propose a plan. Shall I proceed?",
        "I can add the cut if you like.",
        "Would you like me to do that?",
    ],
)
def test_l1_requested_work_offer_is_refused_once_after_reads(kernel, text):
    turns = post(
        kernel,
        action("read_movie"),
        action("say", text=text),
        action("say", text=text),
        message="Add a hard cut at frame 36 in segment 2 to a reverse angle, crossing the railing.",
    )
    assert [t["state"] for t in turns] == ["accepted", "rejected", "accepted"]
    assert "call propose_plan now" in turns[1]["result"]["discrepancy"]


def test_l1_plan_reject_offer(kernel):
    turns = post(
        kernel,
        action("say", text="To proceed I'd need to propose a plan with a survey. Shall I?"),
        action("propose_plan", goal="Survey", steps=[{"kind": "prepare", "title": "Survey"}]),
    )
    assert [t["state"] for t in turns] == ["rejected", "accepted"]


@pytest.mark.parametrize("verb", ["update", "change", "declare"])
def test_l1_other_explicit_edit_verbs(kernel, verb):
    turns = post(
        kernel,
        action("say", text=f"Should I {verb} the cut's relation?"),
        action("say", text="I need the intended relation."),
        message=f"{verb.title()} the cut's relation to reverse.",
    )
    assert [t["state"] for t in turns] == ["rejected", "accepted"]


def test_l1_hard_cut_transcript_offer_recovers_to_edit(tmp_path):
    from harness_eval import run_task
    from harness_eval_scripts import GOOD
    from harness_eval_tasks import TASKS

    task = next(t for t in TASKS if t.id == "hard-cut-at-frame-36")
    offer = action(
        "say",
        text="The existing reverse cut at frame 36 is in segment 1, not segment 2. "
        "Segment 2 currently has no cut. To add a hard cut at frame 36 in segment 2 "
        "to the reverse angle, I'd create it with edit_cut. "
        "Shall I go ahead and create that cut in segment 2?",
    )
    observed = []
    sequence = [GOOD[task.id][0], offer, *GOOD[task.id][1:]]
    result = run_task(task, sequence, tmp_path, outcome=True, evidence=observed.append)
    assert result["passed"], result
    agents = [t for t in observed[0]["turns"] if t["role"] == "agent"]
    assert [t["state"] for t in agents] == ["accepted", "rejected", "accepted", "accepted"]
    assert "call edit_cut now" in agents[1]["result"]["discrepancy"]


@pytest.mark.parametrize(
    "message,text",
    [
        ("Discuss adding a reverse cut.", "Should I add the cut?"),
        ("Give me ideas for a reverse cut.", "I can add the cut if you like."),
        ("Create some ideas for a reverse cut.", "To proceed I'd need to propose a plan."),
        ("How would you add a cut?", "To add a cut I'd need a plan."),
        ("Add a cut.", "Which segment and frame should I use?"),
        ("Add a reverse cut.", "Should I use the gate or gallery angle?"),
        ("Add a cut.", "I cannot add that unsupported cut. I can draft a new shot if you like."),
        ("Survey the courtyard.", "Shall I render takes?"),
        ("Add a cut.", "I can add it once you provide the missing segment and frame."),
    ],
)
def test_l1_negative_controls(kernel, message, text):
    turns = post(kernel, action("say", text=text), message=message)
    assert len(turns) == 1 and turns[0]["state"] == "accepted"


def test_l1_direct_offer_names_tool_and_work_allows_report(kernel):
    ctx, _, _, _ = story(kernel)
    ctx.document.plan_mode = False
    ctx.latest_user_message = "Add an angle to the courtyard."
    tool = Say()
    value = tool.Input(text="Should I add the angle?")
    result = tool.run(ctx, value)
    assert not result.ok and "call edit_place now" in result.discrepancy
    ctx.say_attempted, ctx.work_performed = False, True
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
    from test_harness_tools import context_for

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
        view = render_view(ctx.state, ctx.autonomy, ctx.jobs_reserved, ctx.jobs_limit)
        assert "references ready; segment not ready" in view
        assert "prepare_cut assembles it for free" in view
        segment = next(s for s in ctx.state.payload()["segments"] if s["id"] == setup["segment_id"])
        assert not segment["render"]["ready"]
        inspected = InspectCut().run(ctx, InspectCut.Input(cut_id=setup["cut_id"]))
        assert not inspected.payload["segment_render"]["ready"]
        ctx.jobs_limit = ctx.jobs_reserved
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
            phase={"description": "Arin walks", "screen_direction": "right to left"},
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
