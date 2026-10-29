"""link_set (ADR 0019 §7): the agent uses a set for a scene as the user's Use set does, and its result says what
changed; sets enter the agent's context whenever a scoped place is built as one."""

from itertools import count

from test_set_build import room as room  # noqa: PLC0414 - pytest fixture export
from test_set_views import plain
from test_set_views import shack as shack  # noqa: PLC0414 - pytest fixture export

from gaiavfx.application import set_agent, sets
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.session import AgentSession
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.application.harness.tools.prepare import PrepareCut
from gaiavfx.application.harness.tools.sets import LinkSet, SaveSetView
from gaiavfx.application.harness.view import render_view
from gaiavfx.modules.agent.service import AgentStore

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


def porch_scene(s):
    """A second scene of the movie, opening on the Steps of a Porch that is not built as a set."""
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
        segment = DIRECTION.update_segment(
            session, segment["id"], segment["revision"], opening_angle_id=steps["id"]
        )
    return porch, steps, segment


def test_the_agent_uses_a_set_for_a_scene_and_says_what_changed(shack):
    s = shack
    porch, steps, segment = porch_scene(s)
    ctx = context(s)
    # The shack's place is built as the set, so the sets are in the agent's context without being named.
    assert [row["id"] for row in ctx.state.sets] == [s.set_id]
    assert "link_set" in {tool.name for tool, _ in applicable_tools(ctx)}
    view = render_view(ctx.state, ctx.autonomy, 0, 6, document=ctx.document)
    assert f"built as stage {s.set_id}" in view

    linked = LinkSet().run(
        ctx,
        LinkSet.Input(
            segment_id=segment["id"], expected_revision=segment["revision"], set_id=s.set_id
        ),
    )
    assert linked.ok, linked
    message = linked.payload["message"]
    assert message.startswith("Porch is built as the stage")
    assert "this scene opens on Steps and asks for the stage's views" in message
    assert (
        "Steps has no camera in the stage yet: save_set_view with its angle_id aims one, or the user aims one in "
        "the stage workbench" in message
    )
    assert message.endswith("the place stays built as the stage.")
    assert plain(s.workspace, DIRECTION.read_place, porch["id"])["set_id"] == s.set_id
    after = plain(s.workspace, DIRECTION.read_segment, segment["id"])
    assert after["opening_technique"] == "opening_views"
    # The agent now sees the first shot it must prepare, and why it cannot yet.
    view = render_view(context(s).state, "prepare", 0, 6)
    assert f"First shot (prepare_cut cut_id {segment['id']}): opening_views not ready" in view
    # and what stands in the way of its stage, as the References row words it, with the angle to aim
    assert (
        f"  First shot's stage Box room (angle_id {steps['id']}): No camera for the first shot in the stage; "
        "save_set_view with this angle_id aims one"
    ) in view


def test_a_scene_in_a_stage_s_place_that_opens_on_text_is_told_how_to_use_the_stage(shack):
    # The live eval (stage-local-a3): asked to use a stage for a scene whose place was already built as it, the model
    # saw no first-shot line, aimed a view with no angle and never asked for the stage's views.
    s = shack
    porch, steps, _ = porch_scene(s)
    with s.workspace.db.transaction() as session:
        DIRECTION.set_place_set(session, porch["id"], porch["revision"], set_id=s.set_id)
    view = render_view(context(s).state, "prepare", 0, 6)
    assert (
        f"  First shot (angle_id {steps['id']}) is in stage Box room but uses text only; link_set asks it for the "
        "stage's views"
    ) in view
    # An edit step on that angle points at the stage's tools, not at rewriting the angle's words (stage-local-a4:
    # the model followed "edit_place op=set (angle_id)" and called the camera aimed).
    from gaiavfx.application.harness.session import PlanStep
    from gaiavfx.application.harness.view import step_target

    step = PlanStep(kind="edit", title="Aim the steps", target=steps["id"])
    assert step_target(step, context(s).state) == (
        f"target exists: {steps['id']}; follow-up ops: save_set_view with this angle_id aims it in stage "
        f"{s.set_id}; link_set uses the stage for a scene opening on it; edit_place op=set only rewrites its words"
    )


def test_aiming_or_rewording_a_stage_s_angle_says_what_the_scene_still_needs(shack):
    # stage-local-a5: one model aimed the camera and never called link_set; the other rewrote the angle's words with
    # edit_place and called the camera aimed.
    from gaiavfx.application.harness.tools.places import EditPlace

    s = shack
    porch, steps, segment = porch_scene(s)
    with s.workspace.db.transaction() as session:
        porch = DIRECTION.set_place_set(session, porch["id"], porch["revision"], set_id=s.set_id)
    camera = plain(s.workspace, set_agent.agent_read_set, s.workspace.library, s.set_id)
    aimed = SaveSetView().run(
        context(s),
        SaveSetView.Input(
            set_id=s.set_id,
            name="Steps",
            camera=camera["cameras"][0]["label"],
            heading=180.0,
            angle_id=steps["id"],
        ),
    )
    assert aimed.ok, aimed
    assert aimed.payload["next"] == (
        f"Segment 2 ({segment['id']}) opens on this angle with text only: link_set asks its first shot for the "
        "stage's views."
    )
    reworded = EditPlace().run(
        context(s),
        EditPlace.Input(
            op="set",
            place_id=porch["id"],
            expected_revision=porch["revision"],
            angles=[
                {
                    "angle_id": steps["id"],
                    "expected_revision": steps["revision"],
                    "description": "From K0, looking the other way.",
                }
            ],
        ),
    )
    assert reworded.ok, reworded
    assert reworded.payload["stage"] == (
        f"Only the angles' words changed; their cameras in stage {s.set_id} did not: save_set_view with an "
        "angle_id aims one."
    )


def test_a_prepare_step_on_a_scene_s_angle_reads_its_first_shot_s_plan(shack):
    # The live trial: approved, a prepare step targeting a scene's opening angle stopped the conversation with
    # "Context assembly failed" (a KeyError on the segment's id): a first shot's plan is its segment's opening.
    from gaiavfx.application.harness.session import Plan, PlanGrant, PlanStep

    s = shack
    _, steps, segment = porch_scene(s)
    linked = LinkSet().run(
        context(s),
        LinkSet.Input(
            segment_id=segment["id"], expected_revision=segment["revision"], set_id=s.set_id
        ),
    )
    assert linked.ok, linked
    ctx = context(s)
    document = ctx.document.model_copy(deep=True)
    document.plan_mode = True
    document.plan = Plan(
        id="plan",
        revision=1,
        state="executing",
        goal="Aim the steps",
        proposed_turn=0,
        steps=[PlanStep(kind="prepare", title="Prepare the steps", target=steps["id"])],
        budget_jobs=0,
        grant=PlanGrant(
            budget_jobs=0, step_kinds=["prepare"], approved_turn=1, reserved_at_approval=0
        ),
    )
    view = render_view(ctx.state, "prepare", 0, 6, document=document)
    # The step's problem is the first shot's: no camera aimed in the stage yet.
    assert "No camera for the first shot in the stage" in view


def test_aiming_a_camera_proposed_as_preparation_is_refused_as_an_edit(shack):
    # The live trial: the model planned "Aim a camera at the Stove angle (segment 1)" as a prepare step, which grants
    # no save_set_view.
    from gaiavfx.application.harness.tools.plan import ProposePlan, aim_title

    s = shack
    _, steps, _ = porch_scene(s)
    ctx = context(s)
    ctx.document.plan_mode = True
    refused = ProposePlan().run(
        ctx,
        ProposePlan.Input(
            goal="Aim the steps",
            steps=[
                {
                    "kind": "prepare",
                    "title": "Aim a camera at the Steps angle",
                    "target": steps["id"],
                }
            ],
        ),
    )
    assert not refused.ok
    assert refused.discrepancy.startswith(
        "wrong_step_kind: Aim a camera at the Steps angle aims a camera in a stage; use kind edit"
    )
    assert aim_title("Save a set view for each segment's first-shot angle")
    assert not aim_title("Render views from the stage")
    assert not aim_title("Prepare the first shot from the stage's views")


def test_link_set_refuses_a_stale_revision(shack):
    s = shack
    _, _, segment = porch_scene(s)
    refused = LinkSet().run(
        context(s),
        LinkSet.Input(
            segment_id=segment["id"], expected_revision=segment["revision"] - 1, set_id=s.set_id
        ),
    )
    assert not refused.ok and "stale" in refused.discrepancy.lower()


def test_the_agent_aims_the_scene_angle_s_camera_and_then_prepares_its_first_shot(shack):
    s = shack
    _, steps, segment = porch_scene(s)
    linked = LinkSet().run(
        context(s),
        LinkSet.Input(
            segment_id=segment["id"], expected_revision=segment["revision"], set_id=s.set_id
        ),
    )
    assert linked.ok, linked
    camera = plain(s.workspace, set_agent.agent_read_set, s.workspace.library, s.set_id)
    key = camera["cameras"][0]["label"]
    aimed = SaveSetView().run(
        context(s),
        SaveSetView.Input(
            set_id=s.set_id, name="Steps", camera=key, heading=90.0, angle_id=steps["id"]
        ),
    )
    assert aimed.ok, aimed
    with s.workspace.db.transaction() as session:
        view = sets.STORE.view_for_angle(session, s.set_id, steps["id"])
    assert view and view["name"] == "Steps"
    started = PrepareCut().run(context(s), PrepareCut.Input(cut_id=segment["id"]))
    assert started.ok and started.payload["kind"] == "set_views", started


def test_the_agent_cannot_aim_a_camera_for_a_place_built_as_another_set(shack):
    s = shack
    _, steps, _ = porch_scene(s)
    camera = plain(s.workspace, set_agent.agent_read_set, s.workspace.library, s.set_id)
    refused = SaveSetView().run(
        context(s),
        SaveSetView.Input(
            set_id=s.set_id,
            name="Steps",
            camera=camera["cameras"][0]["label"],
            heading=0.0,
            angle_id=steps["id"],
        ),
    )
    assert not refused.ok and "Porch is not built as this stage" in refused.discrepancy
