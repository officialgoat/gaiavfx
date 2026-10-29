"""The agent prepares a segment's first shot (ADR 0019 §1, §7; Fable and Astra, round 6): prepare_cut takes the
segment's id for it, its costs, schema, view and completion include it, and its views come from its place's producer.
A scene that opens in a set is prepared to ready with no render budget; one whose place is not a set still needs its
plate and survey."""

from itertools import count

from test_set_build import drain
from test_set_build import room as room  # noqa: PLC0414 - pytest fixture export
from test_set_views import plain, runner
from test_set_views import shack as shack  # noqa: PLC0414 - pytest fixture export

from gaiavfx.application import direction_plans, prepare_references
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.session import AgentSession, PlanStep
from gaiavfx.application.harness.shots import shots
from gaiavfx.application.harness.tools.plan_costs import check_plan_costs, preparation_needs
from gaiavfx.application.harness.tools.prepare import PrepareCut
from gaiavfx.application.harness.view import render_view
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.modules.generation.operations.service import OperationService

TURNS = count(1)


def context(s):
    s.workspace.save_settings(s.workspace.settings() | {"agent_autonomy": "prepare"})
    harness = Harness(
        s.workspace.db, AgentStore(), None, s.workspace.settings, library=s.workspace.library
    )
    row = harness.open(s.segment["movie_id"])
    row = harness.set_plan_mode(row["id"], row["revision"], False)
    return harness.context(
        row, AgentSession.model_validate(row["document"]), [], f"turn-{next(TURNS)}"
    )


def prepare(s, shot_id):
    return PrepareCut().run(context(s), PrepareCut.Input(cut_id=shot_id))


def test_a_first_shot_in_a_set_is_prepared_to_ready_by_the_agent(shack):
    s = shack
    ctx = context(s)
    first = next(shot for shot in shots(ctx.state) if shot["kind"] == "opening")
    assert (first["id"], first["angle"]) == (s.segment["id"], "Door")
    # It needs the set's views and a sheet, both free, and the agent is told how to prepare it.
    assert preparation_needs(ctx, first) == {
        ("views", s.place["id"]): 0,
        ("sheet", s.place["id"]): 0,
    }
    assert s.segment["id"] in str(PrepareCut().narrow(ctx))
    view = render_view(ctx.state, ctx.autonomy, 0, 6, document=ctx.document)
    assert (
        f"First shot (prepare_cut cut_id {s.segment['id']}): opening_views not ready: "
        "view_angle, view_place"
    ) in view
    step = PlanStep(kind="prepare", title="Prepare the first shot", target=s.segment["id"], jobs=0)
    assert check_plan_costs(ctx, [step]) == ""
    render = PlanStep(kind="render", title="Render", target=s.segment["id"], jobs=1)
    assert "the first shot of segment" in check_plan_costs(ctx, [render])

    started = prepare(s, s.segment["id"])
    assert started.ok, started
    assert (started.payload["kind"], started.receipts[-1]["budget_reserved"]) == ("set_views", 0)
    drain(runner(s.workspace))
    opened = prepare(s, s.segment["id"])
    assert opened.ok and opened.payload["review"]["kind"] == "angle_views"
    # The user approves the render; the agent then assembles the sheet, again for free.
    operation = plain(s.workspace, OperationService().operation, started.payload["operation_id"])
    plain(
        s.workspace,
        prepare_references.approve_view,
        candidate_id=operation["candidates"][0]["id"],
    )
    sheet = prepare(s, s.segment["id"])
    assert sheet.ok and sheet.payload["kind"] == "assemble", sheet
    drain(runner(s.workspace))
    assert plain(s.workspace, direction_plans.plan_for_segment, s.segment["id"]).ready
    done = prepare(s, s.segment["id"])
    assert not done.ok and "the segment is ready to render" in done.discrepancy


def test_a_first_shot_whose_place_is_not_a_set_still_needs_its_plate_and_survey(shack):
    s = shack
    with s.workspace.db.transaction() as session:
        porch = DIRECTION.create_place(session, "Porch")
        steps = DIRECTION.add_angle(session, porch["id"], "Steps")
        movie = DIRECTION.read_movie(session, s.segment["movie_id"])
        segment = DIRECTION.add_segment(
            session,
            movie["id"],
            movie["revision"],
            content=s.segment["content"],
            delivered_frames=48,
            continues=False,
            phases=[(0, {"description": "Ivo waits.", "screen_direction": "", "sound": ""})],
        )
        DIRECTION.update_segment(
            session,
            segment["id"],
            segment["revision"],
            opening_angle_id=steps["id"],
            opening_technique="opening_views",
        )
    ctx = context(s)
    first = next(shot for shot in shots(ctx.state) if shot["id"] == segment["id"])
    needs = preparation_needs(ctx, first)
    assert {kind for kind, _ in needs} >= {"plate", "survey"}
    assert needs[("plate", porch["id"])] == 2 and needs[("survey", porch["id"])] == 1
    refused = prepare(s, segment["id"])
    assert not refused.ok and "plate" in refused.discrepancy
