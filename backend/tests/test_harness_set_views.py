"""The agent prepares a cut's views with its place's producer (ADR 0019 §3, §7): a place built as a set gets them
rendered from the set, with no plate and no render budget, and its reviews and retries are those of a survey's views."""

from itertools import count

from test_set_build import drain
from test_set_build import room as room  # noqa: PLC0414 - pytest fixture export
from test_set_views import aim, plain, runner
from test_set_views import shack as shack  # noqa: PLC0414 - pytest fixture export

from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.session import AgentSession
from gaiavfx.application.harness.shots import shots
from gaiavfx.application.harness.tools.plan_costs import preparation_needs
from gaiavfx.application.harness.tools.prepare import PrepareCut, needed_jobs
from gaiavfx.application.harness.tools.producers import EmptyPlate
from gaiavfx.application.prepare_references import open_views_review
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService


def cut_into_the_set(s):
    """A cut from the scene's Door to a new Stove angle of the place built as the set, its camera aimed."""
    with s.workspace.db.transaction() as session:
        stove = DIRECTION.add_angle(session, s.place["id"], "Stove")
        segment = DIRECTION.read_segment(session, s.segment["id"])
        cut = DIRECTION.create_cut(
            session,
            segment["id"],
            segment["revision"],
            frame=48,
            from_angle_id=s.door["id"],
            to_angle_id=stove["id"],
            relation="same_direction",
            crosses_barrier=False,
            relation_source="authored",
        )
    aim(s.workspace, s.set_id, stove["id"], heading=1.2)
    return stove, cut


TURNS = count(1)  # each call is a turn of its own, as a request key needs


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


def test_a_cut_into_a_set_gets_its_views_rendered_from_the_set(shack):
    s = shack
    stove, cut = cut_into_the_set(s)
    ctx = context(s)
    row = next(s for s in shots(ctx.state) if s["id"] == cut["id"])
    assert next(p for p in ctx.state.places if p["id"] == s.place["id"])["set_id"] == s.set_id
    # Its views cost nothing and need no plate: the plan's view actions name the set's renders.
    needs = preparation_needs(ctx, row)
    assert needs[("views", s.place["id"])] == 0
    assert not {kind for kind, _ in needs} & {"plate", "survey"}
    assert needed_jobs(ctx.state, row) == 0
    schema = PrepareCut().narrow(ctx)
    assert "plate" not in (schema if "properties" in schema else schema["oneOf"][0])["properties"]
    refused = PrepareCut().run(
        ctx, PrepareCut.Input(cut_id=cut["id"], plate=EmptyPlate(source_asset="x", empty=True))
    )
    assert not refused.ok and "plate_not_used" in refused.discrepancy

    started = PrepareCut().run(ctx, PrepareCut.Input(cut_id=cut["id"]))
    assert started.ok, started
    assert started.payload["kind"] == "set_views"
    assert started.receipts[-1]["budget_reserved"] == 0
    assert started.payload["inputs_used"] == "Rendered from the stage's accepted version."
    operation = plain(s.workspace, OperationService().operation, started.payload["operation_id"])
    assert [v["angle_id"] for v in operation["request"]["views"]] == [stove["id"]]
    drain(runner(s.workspace))

    # The renders wait for the user's review, which prepare_cut opens as it opens a survey's.
    ctx = context(s)
    listed = next(o for o in ctx.state.operations if o["id"] == operation["id"])
    assert listed["review_pending"]
    review = PrepareCut().run(ctx, PrepareCut.Input(cut_id=cut["id"]))
    assert review.ok and review.payload["review"]["kind"] == "angle_views"


def test_a_rejected_set_render_is_not_rendered_again_unchanged(shack):
    s = shack
    stove, cut = cut_into_the_set(s)
    started = PrepareCut().run(context(s), PrepareCut.Input(cut_id=cut["id"]))
    drain(runner(s.workspace))
    assert PrepareCut().run(context(s), PrepareCut.Input(cut_id=cut["id"])).ok
    with s.workspace.db.transaction() as session:
        review = open_views_review(session, started.payload["operation_id"])
        ReviewService().reject_references(
            session,
            review["id"],
            expected_revision=review["revision"],
            note="The stove is cut off at the left.",
            rejected_by="user",
        )
    again = PrepareCut().run(context(s), PrepareCut.Input(cut_id=cut["id"]))
    assert not again.ok
    assert f"Rejected set_views {started.payload['operation_id']}" in again.discrepancy
    assert "camera" in again.discrepancy and "stage workbench" in again.discrepancy
    # Re-aimed, it renders again.
    aim(s.workspace, s.set_id, stove["id"], heading=0.8)
    renewed = PrepareCut().run(context(s), PrepareCut.Input(cut_id=cut["id"]))
    assert renewed.ok and renewed.payload["kind"] == "set_views"
    assert "camera" in renewed.payload["revised_inputs"]
    assert plain(s.workspace, DIRECTION.read_place, s.place["id"])["set_id"] == s.set_id
