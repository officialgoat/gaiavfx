"""S7i regressions from local-e5 and the second courtyard e2e conversation."""

import json
from copy import deepcopy

import harness_support
import pytest
from harness_support import action
from test_harness_tools import context_for

from gaiavfx.application.harness.actions import ToolResult, action_schema, inlined, parse_action
from gaiavfx.application.harness.context import assemble
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.prompt import system_prompt
from gaiavfx.application.harness.session import AgentSession, Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.application.harness.tools.draft import DraftStoryline
from gaiavfx.application.harness.tools.plan import PlanStepTool, ProposePlan
from gaiavfx.application.harness.tools.prepare import PrepareCut
from gaiavfx.application.harness.world import direction, operations, review_pending
from gaiavfx.application.prepare_references import (
    approve_plate,
    open_plate_review,
    start_removal,
)
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.shared.errors import ApplicationError

world = harness_support.world
kernel = harness_support.kernel


def executing(*steps):
    return Plan(
        id="plan",
        revision=1,
        state="executing",
        goal="Prepare then render Segment 2",
        proposed_turn=0,
        steps=list(steps),
        budget_jobs=6,
        grant=PlanGrant(
            budget_jobs=6,
            step_kinds=list(dict.fromkeys(s.kind for s in steps)),
            approved_turn=1,
            reserved_at_approval=0,
        ),
    )


def test_wrong_plate_retry_uses_original_source_one_job_and_waits_for_its_approval(world):
    from harness_eval_tasks import run_idle

    work, setup = world
    tool = PrepareCut()
    invalid = tool.Input(
        cut_id=setup["cut_id"],
        plate={
            "source_asset": setup["plate"],
            "angle_id": setup["angles"][2],
            "whom": "Arin",
            "object_pronoun": "him",
            "behind": "the courtyard",
        },
    )
    # The Python backstop applies even when the existing plate is already correct.
    assert "plate_angle_mismatch" in tool.run(context_for(work, setup), invalid).discrepancy
    with work.db.transaction() as session:
        wrong = start_removal(
            session,
            place_id=setup["place_id"],
            angle_id=setup["angles"][2],
            source_asset=setup["plate"],
            wording={"whom": "Arin", "object_pronoun": "him", "behind": "the courtyard"},
            seeds=[71],
        )
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        review = open_plate_review(session, wrong["id"])
        approve_plate(session, candidate_id=review["members"][0]["candidate_id"])
    ctx, tool = context_for(work, setup), PrepareCut()
    assert "seeds" not in json.dumps(ctx.state.payload())
    refusal = tool.run(ctx, tool.Input(cut_id=setup["cut_id"]))
    assert not refusal.ok and "plate_angle_mismatch" in refusal.discrepancy
    assert setup["angles"][0] in refusal.discrepancy and "one removal job" in refusal.discrepancy
    value = json.loads(refusal.discrepancy.split("Example input: ")[1])
    assert value["plate"]["source_asset"] == setup["plate"]
    bad = deepcopy(value)
    bad["plate"]["angle_id"] = setup["angles"][2]
    with pytest.raises(ApplicationError, match=setup["angles"][0]):
        parse_action(action(tool.name, **bad), [(tool, tool.narrow(ctx))])
    assert "plate_angle_mismatch" in tool.run(ctx, tool.Input(**bad)).discrepancy
    ctx.jobs_limit = 0
    # S7j offers the zero-inference empty-image fit; removal still needs a job.
    assert tool.narrow(ctx) is not None
    assert "budget_exhausted" in tool.run(ctx, tool.Input(**value)).discrepancy
    ctx.jobs_limit = 1
    # Omission defaults to from_angle; this is also valid in the grammar.
    value["plate"].pop("angle_id")
    _, parsed = parse_action(action(tool.name, **value), [(tool, tool.narrow(ctx))])
    result = tool.run(ctx, parsed)
    assert result.ok and result.receipts[0]["budget_reserved"] == 1
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        op = operations.operation(session, result.waits_on)
        assert op["request"]["angle_id"] == setup["angles"][0]
        assert op["request"]["source_asset"] == setup["plate"]
        assert review_pending(session, op)
        assert not review_pending(session, operations.operation(session, wrong["id"]))
    ctx = context_for(work, setup)
    pending = tool.run(ctx, tool.Input(cut_id=setup["cut_id"]))
    assert pending.ok and pending.payload["review"] and not pending.waits_on
    with work.db.transaction() as session:
        review = open_plate_review(session, op["id"])
        approved = approve_plate(session, candidate_id=review["members"][0]["candidate_id"])
    ctx = context_for(work, setup)
    ctx.turn_id = "survey-after-replacement"
    survey = tool.run(ctx, tool.Input(cut_id=setup["cut_id"]))
    assert survey.ok and survey.payload["kind"] == "survey", survey
    with work.db.transaction() as session:
        assert (
            operations.operation(session, survey.waits_on)["request"]["plan"]["first_frame_asset"]
            == approved["plate_asset_id"]
        )


@pytest.mark.parametrize(
    "bad", ["other_place", "unknown_place_alias", "missing_source", "order", "bounds"]
)
def test_draft_cuts_rollback_all_beats_and_receipts(kernel, bad):
    from test_harness_drafting import draft_context

    ctx, subject = draft_context(kernel)
    with kernel.db.transaction() as session:
        place = direction.create_place(session, "Courtyard")
        angles = [direction.add_angle(session, place["id"], name) for name in ("side", "reverse")]
        other = direction.create_place(session, "Elsewhere")
        foreign = direction.add_angle(session, other["id"], "wrong")
        receipts_before = direction.receipts(session, kernel.movie["id"])
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    beat = {
        "title": "Walk",
        "place": {"noun": "courtyard", "description": "Railing"},
        "subject_ids": [subject["id"]],
        "continues": False,
        "phases": [{"start_frame": 0, "description": "Walk", "screen_direction": "left to right"}],
        "place_id": place["id"],
        "from_angle": angles[0]["id"],
        "cuts": [
            {
                "frame": 36,
                "to_angle": angles[1]["id"],
                "relation": "reverse",
                "crosses_barrier": True,
                "shot": "Arin walks past the railing from the reverse side.",
                "screen_direction": "right to left",
            }
        ],
    }
    beats = [deepcopy(beat), deepcopy(beat)]
    if bad == "other_place":
        beats[1]["cuts"][0]["to_angle"] = foreign["id"]
    elif bad == "unknown_place_alias":
        # An omitted place now resolves from cut angles; an unknown legacy alias
        # must still roll back every earlier beat and receipt.
        beats[1]["place_id"] = "unknown-place"
    elif bad == "missing_source":
        beats[1].pop("from_angle")
    elif bad == "order":
        beats[1]["cuts"].append(deepcopy(beats[1]["cuts"][0]))
    else:
        beats[1]["cuts"][0]["frame"] = 999
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=2, beats=beats))
    assert not result.ok and "beat 2" in result.discrepancy and not ctx.board.decisions
    with kernel.db.transaction() as session:
        assert not direction.read_movie(session, kernel.movie["id"])["segments"]
        assert direction.receipts(session, kernel.movie["id"]) == receipts_before


@pytest.mark.parametrize("kind", ["survey", "removal"])
def test_prepare_reviews_pause_own_step_then_resume_and_leave_render_review_pending(world, kind):
    from harness_eval_quality import complete_review
    from harness_eval_tasks import run_idle

    work, setup = world
    harness = Harness(work.db, AgentStore(), None, work.settings)
    row = harness.open(setup["movie_id"])
    doc = AgentSession.model_validate(row["document"])
    doc.plan = executing(
        PlanStep(kind="prepare", title="Prepare cut", target=setup["cut_id"], jobs=3),
        PlanStep(kind="render", title="Render segment", jobs=2),
        PlanStep(kind="review", title="Review takes of Segment 2"),
    )
    ctx = harness.context(row, doc, [], "prepare-plan")
    tool = PrepareCut()
    value = tool.Input(cut_id=setup["cut_id"])
    if kind == "removal":
        with work.db.transaction() as session:
            removal = start_removal(
                session,
                place_id=setup["place_id"],
                angle_id=setup["angles"][0],
                source_asset=setup["plate"],
                wording={"whom": "Arin", "object_pronoun": "him", "behind": "the courtyard"},
                seeds=[74],
            )
        result = ToolResult(waits_on=removal["id"])
    else:
        result = tool.run(ctx, value)
    assert result.ok
    harness._link_step(doc, result, tool, value)
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        op = operations.operation(session, result.waits_on)
        harness._link_review(doc, op, session)
    assert [s.status for s in doc.plan.steps] == ["awaiting_user", "pending", "pending"]
    assert doc.plan.steps[0].review_of == op["id"] and not doc.plan.steps[2].review_of
    step = PlanStepTool()
    assert not step.run(ctx, step.Input(step=0, status="done")).ok
    with work.db.transaction() as session:
        assert not harness._complete_reviews(session, row, doc)
    complete_review(work, op["id"])
    with work.db.transaction() as session:
        assert harness._complete_reviews(session, row, doc)
    assert [s.status for s in doc.plan.steps] == ["pending", "pending", "pending"]
    assert doc.plan.steps[0].review_of == op["id"]
    with work.db.transaction() as session:
        harness._link_review(doc, op, session)
    assert doc.plan.steps[0].status == "pending"


@pytest.mark.parametrize("kinds", [("review",), ("edit", "review"), ("prepare", "review")])
def test_orphan_or_reference_only_review_plan_refuses(kernel, kinds):
    ctx = kernel.harness.context(kernel.row, AgentSession(), [])
    tool = ProposePlan()
    result = tool.run(
        ctx, tool.Input(goal="Draft storyline", steps=[{"kind": k, "title": k} for k in kinds])
    )
    assert (
        not result.ok
        and "review_without_render" in result.discrepancy
        and ctx.document.plan is None
    )


def test_rank_resume_is_snapshot_and_next_model_call_sees_later_selection(tmp_path):
    from harness_eval_tasks import TASKS, close

    from gaiavfx.application.candidate_review import select_take
    from gaiavfx.application.harness.driver import AgentRunner
    from gaiavfx.application.harness.plans import review_outcome
    from gaiavfx.application.harness.world import reviews

    task = next(t for t in TASKS if t.id == "provisional-pick-unblocks")
    work, setup = task.build(tmp_path)
    try:
        with work.db.transaction() as session:
            op = operations.operation(session, setup["fixture_render"])
            review = reviews.blind(session, op["id"])
            a, b = review["members"][:2]
            reviews.save_ranking(
                session,
                review["id"],
                expected_revision=review["ranking"]["revision"],
                tiers=[[a["candidate_id"]], [b["candidate_id"]]],
                usable_tiers=1,
                notes={},
                tags={},
                general="",
            )
            text = review_outcome(session, setup["movie_id"], op)
        assert "ranked A > B" in text and "selection pending" in text
        harness = Harness(work.db, AgentStore(), None, work.settings)
        row = harness.open(setup["movie_id"])
        harness.set_plan_mode(row["id"], row["revision"], False)
        harness.post_message(row["id"], "Report the selection", "report")
        current = harness.read(row["id"])
        ctx = harness.context(current, AgentSession.model_validate(current["document"]), [])
        assert "Never pick without that request" in ctx.view
        assert "pick_provisional (unjudged)" in system_prompt(ctx, applicable_tools(ctx))
        # This is the race: ranking is already committed; selection arrives before the next call.
        with work.db.transaction() as session:
            select_take(
                session,
                review_set_id=review["id"],
                candidate_id=a["candidate_id"],
                authority="user",
                expected_revision=0,
            )
        seen = []
        from harness_support import ScriptedClient

        def report(messages, schema):
            seen.append(messages[-1]["content"])
            return action("say", text="A is user-selected.")

        harness.model = ScriptedClient([report])
        AgentRunner(work.db, harness).run_once()
        assert seen and f"user-selected: {a['asset_id']}" in seen[0]
        assert "Continuation hint:" not in seen[0]
        assert harness.read(row["id"])["state"] == "idle"
    finally:
        close(work)


def test_provisional_hint_is_conditional_and_good_sequence_uses_unjudged(tmp_path):
    from harness_eval import run_task
    from harness_eval_scripts import GOOD
    from harness_eval_tasks import TASKS

    task = next(t for t in TASKS if t.id == "provisional-pick-unblocks")
    captured = []
    result = run_task(task, GOOD[task.id], tmp_path, evidence=captured.append)
    assert result["passed"], result
    picks = [
        t for t in captured[0]["turns"] if (t["action"] or {}).get("tool") == "pick_provisional"
    ]
    assert picks[0]["result"]["payload"]["basis"] == "unjudged"


def test_e24_starts_without_cut_and_good_creates_it(tmp_path):
    from harness_eval import run_task
    from harness_eval_scripts import GOOD
    from harness_eval_tasks import TASKS

    task = next(t for t in TASKS if t.id == "hard-cut-at-frame-36")
    data = []
    result = run_task(task, GOOD[task.id], tmp_path, evidence=data.append)
    assert result["passed"], result
    assert not data[0]["initial"]["movie"]["segments"][1]["cuts"]
    cuts = data[0]["final"]["movie"]["segments"][1]["cuts"]
    assert len(cuts) == 1 and cuts[0]["frame"] == 36


def test_live_e7_waiting_human_completes_removal_review_before_continue(tmp_path):
    from harness_eval import run_task, scripted_model
    from harness_eval_scripts import GOOD
    from harness_eval_tasks import TASKS

    task = next(t for t in TASKS if t.id == "plate-from-removal")
    sequence = list(GOOD[task.id])
    sequence[4] = action("ask_human", question="Please review the plate in the Review workspace.")
    result = run_task(
        task,
        (),
        tmp_path,
        model_factory=lambda work, setup, recorder: scripted_model(sequence, work, setup, recorder),
    )
    assert result["passed"], result
    assert [a["action"] for a in result["human_actions"]] == ["approve_plate", "post_message"]
    assert result["human_actions"][1]["message"] == "Continue."


def test_large_library_200_assets_30_segments_fits_and_unknown_ids_refuse(world, tmp_path):
    from harness_eval_tasks import png

    from gaiavfx.application.harness.knowledge import cinematography_head, unsupported_head
    from gaiavfx.application.harness.tools.reads import ReadSegment

    work, setup = world
    for i in range(197):
        path = tmp_path / f"source-{i}.png"
        path.write_bytes(png((i, 2, 3)))
        prepared = work.library.prepare(path, path.name)
        with work.db.transaction() as session:
            work.library.register(session, prepared, {"type": "import"})
    with work.db.transaction() as session:
        movie = direction.read_movie(session, setup["movie_id"])
        content = movie["segments"][0]["content"]
        for i in range(29):
            row = direction.add_segment(
                session,
                movie["id"],
                direction.read_movie(session, movie["id"])["revision"],
                content=content,
                delivered_frames=102,
                continues=False,
                phases=[(0, {"description": "Walk", "screen_direction": "", "sound": ""})],
            )
            direction.create_cut(
                session,
                row["id"],
                row["revision"],
                frame=36,
                from_angle_id=setup["angles"][0],
                to_angle_id=setup["angles"][2],
                relation="reverse",
                crosses_barrier=True,
                relation_source="authored",
            )
        assert len(direction.read_movie(session, movie["id"])["segments"]) == 30
    harness = Harness(work.db, AgentStore(), None, work.settings)
    opened = harness.open(setup["movie_id"])
    for plan_mode in (False, True):
        ctx = harness.context(opened, AgentSession(plan_mode=plan_mode), [])
        applicable = applicable_tools(ctx)
        schema = action_schema(applicable)
        report = assemble(
            system_prompt(ctx, applicable),
            "Continue the movie",
            "\n".join(f"{t.name}: {t.description}" for t, _ in applicable),
            schema,
            ctx.board,
            ctx.view,
            [],
            32768,
            output=2400,
            schema_billed=True,
            knowledge=unsupported_head() + "\n" + cinematography_head(),
        ).report
        assert report["schema"] <= 4500 and report["view"] <= 2000
        assert report["total_reserved"] <= 32768
        shape = next(
            b
            for b in inlined(schema)["oneOf"]
            if b["properties"]["tool"]["const"] == "read_segment"
        )
        assert shape["properties"]["input"]["properties"]["segment_id"] == {"type": "string"}
        # The wire's string fallback does not remove validation or lose a legal id.
        tool, value = parse_action(
            action("read_segment", segment_id=ctx.state.segments[-1]["id"]), applicable
        )
        assert tool.run(ctx, value).ok
        with pytest.raises(ApplicationError, match=ctx.state.segments[0]["id"]):
            parse_action(action("read_segment", segment_id="invented"), applicable)
        refused = ReadSegment().run(ctx, ReadSegment.Input(segment_id="invented"))
        assert not refused.ok and "Candidates:" in refused.discrepancy
    # A scope on the last segment is retained even beyond the movie overview cap.
    scoped = {**opened, "scope_kind": "segment", "scope_id": row["id"]}
    ctx = harness.context(scoped, AgentSession(plan_mode=False), [])
    assert [s["id"] for s in ctx.state.segments] == [row["id"]]
    assert row["id"] in json.dumps(action_schema(applicable_tools(ctx)))
