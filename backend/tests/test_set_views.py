"""Set views (ADR 0019 §3): a place built as a set gets its angles' pictures rendered from the set, reviewed like a
survey's views, and approved as `set_view` evidence that follows its own inputs."""

import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest
from fake_comfy import FakeComfy
from PIL import Image
from test_set_build import FAKE, call, drain
from test_set_build import room as room  # noqa: PLC0414 - pytest fixture export

from gaiavfx.application import (
    direction_plans,
    prepare_references,
    set_scenes,
    set_views,
    sets,
)
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.engine_jobs import EngineCommand
from gaiavfx.application.operations import OperationRunner
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

WORKERS = Path(__file__).parents[2] / "engine_workers"


def runner(workspace):
    return OperationRunner(
        workspace.db,
        FakeComfy(),
        workspace.library,
        "http://fake",
        poll_seconds=0,
        sweep_seconds=0,
        engines={set_views.STEP: EngineCommand((sys.executable, str(FAKE)))},
    )


def plain(workspace, function, *args, **kwargs):
    with workspace.db.transaction() as session:
        return function(session, *args, **kwargs)


@pytest.fixture
def shack(room, tmp_path, request):
    """The room set accepted, a place built as it with a Door angle, and a scene of one subject opening there.

    Parametrized indirectly with False, the scene has no cast: a stage needs no actors."""
    cast = getattr(request, "param", True)
    workspace, set_id = room
    state = call(workspace, sets.set_state, set_id)
    call(workspace, sets.accept_version, set_id, state["version"]["id"], expected_current_id=None)
    picture = tmp_path / "ivo.png"
    Image.new("RGB", (64, 96), "brown").save(picture)
    prepared = workspace.library.prepare(picture, "ivo.png")
    with workspace.db.transaction() as session:
        identity = workspace.library.register(session, prepared, {"type": "import"})
        place = DIRECTION.create_place(session, "Shack")
        door = DIRECTION.add_angle(session, place["id"], "Door")
        sets.link_place(session, place["id"], expected_revision=place["revision"], set_id=set_id)
        movie = DIRECTION.create_movie(session, "Shack scenes")
        subject = DIRECTION.add_subject(
            session,
            movie["id"],
            movie["revision"],
            name="Ivo",
            description="A fisher",
            keeps="sweater",
            identity_asset_id=identity["id"],
        )
        action = "Ivo feeds the stove." if cast else "The stove glows in the empty shack."
        segment = DIRECTION.add_segment(
            session,
            movie["id"],
            subject["receipt"]["after"]["revision"],
            content={
                "subjects": [subject["id"]] if cast else [],
                "place": {"noun": "shack", "description": "A fishing shack.", "landmarks": ""},
            },
            delivered_frames=97,
            continues=False,
            phases=[(0, {"description": action, "screen_direction": "", "sound": ""})],
        )
        DIRECTION.update_segment(
            session,
            segment["id"],
            segment["revision"],
            opening_angle_id=door["id"],
            opening_technique="opening_views",
        )
    view = aim(workspace, set_id, door["id"])
    return SimpleNamespace(
        workspace=workspace, set_id=set_id, place=place, door=door, segment=segment, view=view
    )


def aim(workspace, set_id, angle_id, heading=0.0):
    state = call(workspace, sets.set_state, set_id)
    viewmat = np.eye(4)
    viewmat[:3, :3] = np.array(
        [[np.cos(heading), 0, -np.sin(heading)], [0, 1, 0], [np.sin(heading), 0, np.cos(heading)]]
    )
    saved = call(
        workspace,
        sets.save_view,
        set_id,
        version_id=state["version"]["id"],
        name="Door",
        viewmat=viewmat.tolist(),
        fov=60.0,
        width=320,
        height=180,
    )["views"][-1]
    call(
        workspace,
        sets.aim_view,
        set_id,
        saved["id"],
        angle_id=angle_id,
        version_id=state["version"]["id"],
    )
    return saved


def render(workspace, place, door):
    operation = call(
        workspace, set_views.start_set_views, place_id=place["id"], angle_ids=[door["id"]]
    )
    drain(runner(workspace))
    return plain(workspace, OperationService().operation, operation["id"])


def opening_plan(workspace, segment):
    return plain(workspace, direction_plans.plan_for_segment, segment["id"])


@pytest.mark.parametrize("shack", [True, False], indirect=True, ids=["one subject", "no cast"])
def test_a_set_view_is_rendered_reviewed_and_bound_as_the_opening_sheet(shack):
    s = shack
    workspace, place, door = s.workspace, s.place, s.door
    operation = render(workspace, place, door)
    assert operation["state"] == "succeeded"
    assert operation["request"]["views"][0]["view_id"] == s.view["id"]
    (candidate,) = operation["candidates"]
    assert candidate["output_key"] == door["id"]
    plain(workspace, prepare_references.open_views_review, operation["id"])
    waiting = opening_plan(workspace, s.segment)
    assert not waiting.ready
    assert "views of the opening angle await review" in waiting.refusals[0].message
    evidence = plain(workspace, prepare_references.approve_view, candidate_id=candidate["id"])
    assert (evidence["kind"], evidence["approved"]) == ("set_view", True)
    assert evidence["inputs"]["view_id"] == s.view["id"]
    sheet = plain(
        workspace,
        prepare_references.assemble_sheet,
        place_id=place["id"],
        tiles=[evidence["id"]],
        origin="survey",
    )
    assert sheet["request"]["origin"] == "set"
    drain(runner(workspace))
    with workspace.db.transaction() as session:
        plan, _, pictures, *_ = direction_plans.segment_render_inputs(session, s.segment["id"])
    assert plan.ready, plan.refusals
    assert plan.technique_id == "opening_views"
    (views,) = [p for p in pictures if p.role.value == "place_views"]
    assert (views.first_shot, views.origin) == (1, "set")


def test_a_set_view_goes_stale_with_its_camera_or_what_the_set_hides_not_with_a_new_version(shack):
    s = shack
    workspace, set_id, place, door = s.workspace, s.set_id, s.place, s.door
    operation = render(workspace, place, door)
    plain(workspace, prepare_references.open_views_review, operation["id"])
    evidence = plain(
        workspace, prepare_references.approve_view, candidate_id=operation["candidates"][0]["id"]
    )

    def binding():
        return plain(workspace, DIRECTION.reference_binding, "view", evidence["id"])

    assert not binding().stale
    version_id = call(workspace, sets.set_state, set_id)["version"]["id"]
    region = call(
        workspace,
        sets.hide_region,
        set_id,
        version_id=version_id,
        shape="sphere",
        params={"centre": [0, 0, 1], "radius": 0.2},
        label="Smear",
    )["hide_regions"][-1]
    assert binding().reasons == ("what the stage hides changed since approval",)
    call(
        workspace,
        sets.set_hide_region_removed,
        set_id,
        region["id"],
        removed=True,
        version_id=version_id,
    )
    assert not binding().stale
    aim(workspace, set_id, door["id"], heading=0.5)
    assert binding().reasons == ("the angle's camera in the stage changed since approval",)
    # A place no longer built as any stage says so; the live trial's unbuilt place read "built as another stage now".
    with workspace.db.transaction() as session:
        current = DIRECTION.read_place(session, place["id"])
        DIRECTION.set_place_set(session, place["id"], current["revision"], set_id=None)
    assert binding().reasons == ("the place is no longer built as a stage",)


def test_a_view_is_not_approved_once_its_camera_moved_after_the_render(shack):
    s = shack
    workspace, set_id, place, door = s.workspace, s.set_id, s.place, s.door
    operation = render(workspace, place, door)
    plain(workspace, prepare_references.open_views_review, operation["id"])
    aim(workspace, set_id, door["id"], heading=1.0)
    with pytest.raises(ApplicationError, match="render the view again"):
        plain(
            workspace,
            prepare_references.approve_view,
            candidate_id=operation["candidates"][0]["id"],
        )


def test_set_views_say_what_is_missing_before_anything_runs(shack):
    s = shack
    workspace, place, door = s.workspace, s.place, s.door
    with workspace.db.transaction() as session:
        stove = DIRECTION.add_angle(session, place["id"], "Stove")
        other = DIRECTION.create_place(session, "Elsewhere")
    with pytest.raises(ApplicationError, match="Aim a camera in the stage for Stove"):
        call(workspace, set_views.start_set_views, place_id=place["id"], angle_ids=[stove["id"]])
    with pytest.raises(ApplicationError, match="not built as a stage"):
        call(workspace, set_views.start_set_views, place_id=other["id"], angle_ids=[door["id"]])
    with pytest.raises(ApplicationError, match="only angles of this place"):
        call(workspace, set_views.start_set_views, place_id=place["id"], angle_ids=["nope"])


def test_a_set_view_is_never_judged_by_a_survey_inputs():
    from gaiavfx.modules.direction.service import DirectionService

    class Row:
        kind = "set_view"

    with pytest.raises(ApplicationError, match="view reader"):
        DirectionService()._view_inputs(None, Row())


def test_the_render_hides_what_the_workbench_hides():
    sys.path.insert(0, str(WORKERS))
    from worldbuilder import hidden

    means = np.array([[0, 0, 1], [0.15, 0, 1], [1, 1, 1], [2, 2, 2]], dtype=np.float32)
    regions = [
        {"shape": "sphere", "params": {"centre": [0, 0, 1], "radius": 0.2}},
        {"shape": "box", "params": {"centre": [2, 2, 2], "size": [0.4, 0.4, 0.4]}},
    ]
    assert hidden(means, regions).tolist() == [True, True, False, True]


def test_the_first_shot_says_what_it_takes_from_its_set(shack):
    s = shack
    workspace, set_id, door = s.workspace, s.set_id, s.door

    def blocker():
        """The first shot's next step, read again after each one."""
        return plain(workspace, direction_plans.plan_for_segment, s.segment["id"]).place.blocker

    assert blocker() == "Render views from the stage"
    operation = render(workspace, s.place, door)
    plain(workspace, prepare_references.open_views_review, operation["id"])
    assert blocker() == "Views to review"
    evidence = plain(
        workspace, prepare_references.approve_view, candidate_id=operation["candidates"][0]["id"]
    )
    assert blocker() == "Assemble the sheet"
    plain(
        workspace,
        prepare_references.assemble_sheet,
        place_id=s.place["id"],
        tiles=[evidence["id"]],
        origin="set",
    )
    drain(runner(workspace))
    place = plain(workspace, direction_plans.plan_for_segment, s.segment["id"]).place
    assert (place.blocker, place.set_version, place.rendered_version) == (None, 1, 1)
    assert place.uses_views and not place.update_available
    with workspace.db.transaction() as session:
        segment = DIRECTION.read_segment(session, s.segment["id"])
        DIRECTION.update_segment(
            session, segment["id"], segment["revision"], opening_technique=None
        )
    text = plain(workspace, direction_plans.plan_for_segment, s.segment["id"]).place
    assert (text.uses_views, text.blocker, text.set_id) == (False, None, set_id)


def test_use_set_says_what_it_changes_then_does_it(shack):
    s = shack
    workspace, set_id = s.workspace, s.set_id
    with workspace.db.transaction() as session:
        first = DIRECTION.read_segment(session, s.segment["id"])
        movie = DIRECTION.read_movie(session, first["movie_id"])
        other = DIRECTION.add_segment(
            session,
            movie["id"],
            movie["revision"],
            content=first["content"],
            delivered_frames=97,
            continues=False,
            phases=[(0, {"description": "Ivo sits.", "screen_direction": "", "sound": ""})],
        )
        porch = DIRECTION.create_place(session, "Porch")
    preview = plain(
        workspace,
        set_scenes.use_set_preview,
        other["id"],
        set_id=set_id,
        place_id=porch["id"],
        angle_name="Porch, wide",
    )
    assert (preview["place_name"], preview["angle_name"], preview["new_angle"]) == (
        "Porch",
        "Porch, wide",
        True,
    )
    assert preview["relinks_from"] is None and preview["camera"] is None
    with pytest.raises(ApplicationError, match="Choose the angle"):
        plain(workspace, set_scenes.use_set_preview, other["id"], set_id=set_id)
    used = plain(
        workspace,
        set_scenes.use_set,
        other["id"],
        expected_revision=other["revision"],
        set_id=set_id,
        place_id=porch["id"],
        angle_name="Porch, wide",
    )
    assert used["opening_technique"] == "opening_views"
    with workspace.db.transaction() as session:
        assert DIRECTION.read_place(session, porch["id"])["set_id"] == set_id
        angle = DIRECTION.read_angle(session, used["opening_angle_id"])
    assert angle["name"] == "Porch, wide"
    # The shack scene previews no other scene in its place, and the camera its angle already has in the set.
    shack_preview = plain(workspace, set_scenes.use_set_preview, s.segment["id"], set_id=set_id)
    assert shack_preview["other_scenes"] == [] and shack_preview["camera"] == "Door"
    # Undo restores the segment's first shot; the place stays built as the set.
    with workspace.db.transaction() as session:
        DIRECTION.undo(session, used["receipt"]["id"])
        assert DIRECTION.read_segment(session, other["id"])["opening_technique"] is None
        assert DIRECTION.read_place(session, porch["id"])["set_id"] == set_id


def test_a_new_accepted_version_offers_an_update_without_staling_frozen_views(shack):
    from test_set_build import exclude
    from test_set_build import runner as repair_runner

    from gaiavfx.application import set_build

    s = shack
    workspace = s.workspace
    base = call(workspace, sets.set_state, s.set_id)
    # Freeze v1, then accept v2 before reviewing the rendered view.
    started = call(
        workspace, set_views.start_set_views, place_id=s.place["id"], angle_ids=[s.door["id"]]
    )
    exclude(workspace, s.set_id, base, "K2")
    call(workspace, set_build.run_queued_fixes, s.set_id, version_id=base["version"]["id"])
    # The repair runner may also encounter the view job, so give it the same existing fake for that step.
    run = repair_runner(workspace)
    run.engines[set_views.STEP] = EngineCommand((sys.executable, str(FAKE)))
    drain(run)
    built = call(workspace, sets.set_state, s.set_id)
    call(
        workspace,
        sets.accept_version,
        s.set_id,
        built["build"]["version_id"],
        expected_current_id=base["version"]["id"],
    )
    rendered = plain(workspace, OperationService().operation, started["id"])
    assert rendered["request"]["version_id"] == base["version"]["id"]
    plain(workspace, prepare_references.open_views_review, started["id"])
    evidence = plain(
        workspace, prepare_references.approve_view, candidate_id=rendered["candidates"][0]["id"]
    )
    assert not plain(workspace, DIRECTION.reference_binding, "view", evidence["id"]).stale
    plain(
        workspace,
        prepare_references.assemble_sheet,
        place_id=s.place["id"],
        tiles=[evidence["id"]],
        origin="set",
    )
    drain(runner(workspace))
    plan = opening_plan(workspace, s.segment)
    assert plan.ready, plan.refusals
    assert (plan.place.rendered_version, plan.place.set_version, plan.place.update_available) == (
        1,
        2,
        True,
    )
    assert plan.place.blocker is None
