"""A set seeded from a take (ADR 0019 §4), through the durable runner: a fake ComfyUI answers the take-geometry
graph with a synthetic room's exact geometry (`synthetic_room.py`), the real worldbuilder worker judges it and lays
out the atlas, and a fake splat fit stands in for gsplat. A seed that fills its unseen space (ADR 0020) runs its
rounds through the same fakes (`fill_answers.py`). No model, no GPU."""

import json

import pytest
from set_scene import build_scene

from gaiavfx.application import set_build, set_seed, sets
from gaiavfx.application.direction_plans import plan_for_segment
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.infrastructure import engines
from gaiavfx.modules.editing.selections.service import SelectionService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

OPERATIONS = OperationService()


@pytest.fixture
def scene(tmp_path, monkeypatch):
    """A movie with one segment (a cut at f12) whose current take is a video, opening in a place not built as a
    set (`set_scene.build_scene`)."""
    monkeypatch.setenv("GAIAVFX_GPU_LOCK", str(tmp_path / "gpu.lock"))
    monkeypatch.setattr(engines, "free_vram_gb", lambda: None)
    built = build_scene(tmp_path)
    yield built
    built.workspace.db.close()


def drain(run):
    for _ in range(60):
        if not run.run_once():
            break


def transact(workspace, function, *args, **kwargs):
    with workspace.db.transaction() as session:
        return function(session, *args, **kwargs)


def operation(workspace, operation_id):
    return transact(workspace, OPERATIONS.operation, operation_id)


def test_the_preview_says_what_it_builds_before_it_does(scene):
    s = scene
    preview = transact(
        s.workspace,
        set_seed.seed_preview,
        s.workspace.library,
        segment_id=s.segment["id"],
        fill_unseen=False,
    )
    assert [(x["label"], x["start"], x["end"]) for x in preview["shots"]] == [
        ("First shot", 0, 12),
        ("Cut 1 (f12)", 12, 24),
    ]
    assert preview["frames"] == 12 and preview["refusal"] is None
    assert preview["place"]["name"] == "Box room" and preview["can_use_for_place"]
    assert preview["name"] == "Box room from a take"
    assert preview["candidate_id"] == s.candidate["id"] and preview["seed"] is None


def test_a_moving_take_becomes_a_set_whose_first_version_waits(scene):
    s = scene
    started = transact(
        s.workspace,
        set_seed.start_seed,
        s.workspace.library,
        segment_id=s.segment["id"],
        shot=0,
        name="Box room from a take",
        use_for_place=True,
        fill_unseen=False,
    )
    request = started["request"]
    assert (request["mode"], request["start"], request["end"]) == ("seed", 0, 12)
    assert request["asset_sha256"] == s.asset["sha256"] and request["place_id"] == s.place["id"]
    with s.workspace.db.transaction() as session:
        state, refusal = set_seed.segment_seed(
            session, DIRECTION.read_segment(session, s.segment["id"])
        )
    assert state["state"] == "building" and refusal is None
    drain(s.run)
    done = operation(s.workspace, started["id"])
    assert [j["state"] for j in done["jobs"]] == ["succeeded"] * 3, [
        j["error"] for j in done["jobs"]
    ]
    decision = done["review"][-1]
    assert decision["stage"] == "done"
    graph = s.comfy.graphs[0]
    assert (graph["shot"]["inputs"]["batch_index"], graph["shot"]["inputs"]["length"]) == (0, 12)
    set_id = decision["set_id"]
    with s.workspace.db.transaction() as session:
        row = sets.STORE.get(session, set_id)
        version = row["versions"][0]
        assert (version["origin"], version["state"]) == ("seed", "proposed")
        assert (
            row["provenance"]["source"] == "take"
            and row["provenance"]["candidate_id"] == s.candidate["id"]
        )
        assert row["provenance"]["gate"]["passed"]
        keyframes = sets.STORE.keyframes(session, version["id"])
        assert [k["label"] for k in keyframes] == [
            f"K{n}" for n in range(1, 9)
        ]  # the fake answers 8 frames
        assert all(
            s.workspace.library.get(session, k["image_asset_id"])["kind"] == "image"
            for k in keyframes
        )
        # The place is built as the new set; its scenes read "accept a version" until the human does.
        assert DIRECTION.read_place(session, s.place["id"])["set_id"] == set_id
        state, _ = set_seed.segment_seed(session, DIRECTION.read_segment(session, s.segment["id"]))
        assert state["state"] == "done" and state["set_id"] == set_id
        plan = plan_for_segment(session, s.segment["id"])
        assert plan.seed == state and plan.place.set_id == set_id
    accepted = transact(
        s.workspace,
        sets.accept_version,
        s.workspace.library,
        set_id,
        version["id"],
        expected_current_id=None,
    )
    assert accepted
    with s.workspace.db.transaction() as session:
        state, _ = set_seed.segment_seed(session, DIRECTION.read_segment(session, s.segment["id"]))
        assert state is None  # decided: the set and the place say the rest
    # The dialog still tells what became of the shot's last build.
    preview = transact(
        s.workspace,
        set_seed.seed_preview,
        s.workspace.library,
        segment_id=s.segment["id"],
        fill_unseen=False,
    )
    assert preview["seed"]["message"] == "Box room from a take is built: v1 accepted."
    assert preview["seed"]["label"] == "v1 accepted"
    # Two sets never read alike: the next default name is told apart, and the cost is said before a build.
    assert preview["name"] == "Box room from a take 2"
    assert (
        preview["estimate"].startswith("About 3 minutes of GPU work")
        and "Jobs" in preview["estimate"]
    )
    assert not preview["seed"]["waiting"]


def test_a_still_camera_is_refused_in_words_and_the_refusal_holds_for_that_shot(scene):
    s = scene
    started = transact(
        s.workspace,
        set_seed.start_seed,
        s.workspace.library,
        segment_id=s.segment["id"],
        shot=1,
        name="Corner",
        use_for_place=False,
        fill_unseen=False,
    )
    drain(s.run)
    refused = operation(s.workspace, started["id"])
    assert [j["state"] for j in refused["jobs"]] == [
        "succeeded",
        "succeeded",
    ]  # no splat was fitted
    assert refused["review"][-1]["stage"] == "refused"
    with s.workspace.db.transaction() as session:
        state = set_seed.seed_state(session, refused)
    assert state == {
        "state": "refused",
        "message": "The camera stands still in this take: without filling its unseen space, building a stage "
        "needs the camera to move through the place.",
        "operation_id": refused["id"],
        "set_id": None,
        "waiting": False,
        "name": "Corner",
        "label": "Refused: the camera stands still",
        "shot": 1,
        "can_retry": False,
    }
    with s.workspace.db.transaction() as session:
        assert sets.list_sets(session, s.workspace.library) == []
    # The gate measured what the take shows: the same shot, take and settings would measure it again.
    with pytest.raises(ApplicationError, match="Only a build that stopped is tried again"):
        transact(s.workspace, set_seed.retry_seed, s.workspace.library, started["id"])
    preview = transact(
        s.workspace,
        set_seed.seed_preview,
        s.workspace.library,
        segment_id=s.segment["id"],
        shot=1,
        fill_unseen=False,
    )
    assert preview["seed"] == state
    assert preview["refusal"] == (
        "Building this shot again would give the same answer. Fill in its unseen space, choose a shot whose "
        "camera moves through the place, or choose another take."
    )
    with pytest.raises(ApplicationError, match="would give the same answer"):
        transact(
            s.workspace,
            set_seed.start_seed,
            s.workspace.library,
            segment_id=s.segment["id"],
            shot=1,
            name="Corner",
            use_for_place=False,
            fill_unseen=False,
        )
    assert len(s.comfy.posts) == 1
    # The scene's References row and its Build dialog name the refused shot; the first shot can still be built.
    assert transact(s.workspace, plan_for_segment, s.segment["id"]).seed == state
    first = transact(
        s.workspace,
        set_seed.seed_preview,
        s.workspace.library,
        segment_id=s.segment["id"],
        shot=0,
        fill_unseen=False,
    )
    assert first["refusal"] is None and first["seed"] is None


def test_a_seed_needs_a_take_and_never_takes_a_set_from_a_place(scene):
    s = scene
    with s.workspace.db.transaction() as session:
        place = DIRECTION.read_place(session, s.place["id"])
        DIRECTION.set_place_set(session, place["id"], place["revision"], set_id="another-set")
    preview = transact(
        s.workspace,
        set_seed.seed_preview,
        s.workspace.library,
        segment_id=s.segment["id"],
        fill_unseen=False,
    )
    assert not preview["can_use_for_place"] and preview["refusal"] is None
    with pytest.raises(ApplicationError, match="already built as a stage"):
        transact(
            s.workspace,
            set_seed.start_seed,
            s.workspace.library,
            segment_id=s.segment["id"],
            shot=0,
            name="Again",
            use_for_place=True,
            fill_unseen=False,
        )
    with s.workspace.db.transaction() as session:
        SelectionService().clear(
            session,
            movie_id=s.segment["movie_id"],
            segment_id=s.segment["id"],
            authority="user",
            expected_revision=1,
        )
    preview = transact(
        s.workspace,
        set_seed.seed_preview,
        s.workspace.library,
        segment_id=s.segment["id"],
        fill_unseen=False,
    )
    assert preview["refusal"] == "Select a take of this scene first."
    with pytest.raises(ApplicationError, match="Select a take"):
        transact(
            s.workspace,
            set_seed.start_seed,
            s.workspace.library,
            segment_id=s.segment["id"],
            shot=0,
            name="Again",
            use_for_place=False,
            fill_unseen=False,
        )


def test_a_take_made_before_the_scene_changed_is_refused(scene):
    s = scene
    with s.workspace.db.transaction() as session:
        segment = DIRECTION.read_segment(session, s.segment["id"])
        DIRECTION.update_segment(session, segment["id"], segment["revision"], delivered_frames=30)
    preview = transact(
        s.workspace,
        set_seed.seed_preview,
        s.workspace.library,
        segment_id=s.segment["id"],
        fill_unseen=False,
    )
    assert preview["refusal"].startswith("The selected take was made before this scene changed")
    # The Build command reads the same refusal from the segment's plan.
    plan = transact(s.workspace, plan_for_segment, s.segment["id"])
    assert plan.build_refusal == preview["refusal"]
    with pytest.raises(ApplicationError, match="made before this scene changed"):
        transact(
            s.workspace,
            set_seed.start_seed,
            s.workspace.library,
            segment_id=s.segment["id"],
            shot=1,
            name="Late",
            use_for_place=False,
            fill_unseen=False,
        )


def start(scene, *, shot=0, use_for_place=True):
    return transact(
        scene.workspace,
        set_seed.start_seed,
        scene.workspace.library,
        segment_id=scene.segment["id"],
        shot=shot,
        name="Room from the selected take",
        use_for_place=use_for_place,
        fill_unseen=False,
    )


def preview(scene, shot=0):
    return transact(
        scene.workspace,
        set_seed.seed_preview,
        scene.workspace.library,
        segment_id=scene.segment["id"],
        shot=shot,
        fill_unseen=False,
    )


def test_geometry_failure_is_stopped_in_plan_and_dialog_and_retry_recovers(scene):
    s = scene
    s.comfy.execution_error = True
    started = start(s)
    drain(s.run)
    failed = operation(s.workspace, started["id"])
    assert failed["jobs"][0]["state"] == "failed"
    state = preview(s)["seed"]
    assert state["state"] == "failed"
    assert state["message"] == (
        "Building Room from the selected take stopped before it finished; Jobs says why."
    )
    assert state["can_retry"]
    assert transact(s.workspace, plan_for_segment, s.segment["id"]).seed == state
    assert transact(s.workspace, sets.list_sets, s.workspace.library) == []
    assert transact(s.workspace, DIRECTION.read_place, s.place["id"])["set_id"] is None

    s.comfy.execution_error = False
    again = transact(s.workspace, set_seed.retry_seed, s.workspace.library, started["id"])
    assert again["request"] == started["request"] | {"attempt": 2}
    drain(s.run)
    assert preview(s)["seed"]["state"] == "done"
    assert len(transact(s.workspace, sets.list_sets, s.workspace.library)) == 1


def test_retry_is_refused_while_running_and_start_is_idempotent(scene):
    started = start(scene)
    assert start(scene)["id"] == started["id"]
    with pytest.raises(ApplicationError, match="still running"):
        transact(scene.workspace, set_seed.retry_seed, scene.workspace.library, started["id"])
    assert len(scene.comfy.posts) == 0  # a request guard, before any remote work


def test_retry_refuses_a_changed_take_asset(scene):
    from gaiavfx.modules.library.assets import AssetRecord

    s = scene
    s.comfy.execution_error = True
    started = start(s)
    drain(s.run)
    # Simulate a changed Library identity at the persistence boundary; no media files are altered.
    with s.workspace.db.transaction() as session:
        session.get(AssetRecord, s.asset["id"]).sha256 = "0" * 64
    with pytest.raises(ApplicationError, match="take changed since this seed"):
        transact(s.workspace, set_seed.retry_seed, s.workspace.library, started["id"])
    assert len(s.comfy.posts) == 1


def test_cut_moved_during_a_seed_keeps_the_frozen_shot_and_retry(scene):
    s = scene
    started = start(s)
    with s.workspace.db.transaction() as session:
        cut = DIRECTION.read_cut(session, s.segment["cuts"][0]["id"])
        DIRECTION.update_cut(session, cut["id"], cut["revision"], frame=8)
    s.comfy.execution_error = True
    drain(s.run)
    again = transact(s.workspace, set_seed.retry_seed, s.workspace.library, started["id"])
    assert again["request"] == started["request"] | {"attempt": 2}
    s.comfy.execution_error = False
    drain(s.run)
    graph = s.comfy.graphs[-1]["shot"]["inputs"]
    assert (graph["batch_index"], graph["length"]) == (0, 12)
    done = operation(s.workspace, again["id"])
    row = transact(s.workspace, sets.STORE.get, done["review"][-1]["set_id"])
    assert row["provenance"]["frames"] == [0, 12]


def test_a_place_claimed_during_a_seed_keeps_its_other_set(scene):
    s = scene
    started = start(s)
    with s.workspace.db.transaction() as session:
        place = DIRECTION.read_place(session, s.place["id"])
        DIRECTION.set_place_set(session, place["id"], place["revision"], set_id="another-set")
    drain(s.run)
    assert operation(s.workspace, started["id"])["review"][-1]["stage"] == "done"
    assert transact(s.workspace, DIRECTION.read_place, s.place["id"])["set_id"] == "another-set"
    assert not preview(s)["can_use_for_place"]


def test_a_segment_without_an_opening_angle_can_preview_without_a_place(scene):
    s = scene
    with s.workspace.db.transaction() as session:
        movie = DIRECTION.read_movie(session, s.segment["movie_id"])
        segment = DIRECTION.add_segment(
            session,
            movie["id"],
            movie["revision"],
            content=s.segment["content"],
            delivered_frames=24,
            continues=False,
            phases=[(0, {"description": "Ivo waits.", "screen_direction": "", "sound": ""})],
        )
    result = transact(
        s.workspace,
        set_seed.seed_preview,
        s.workspace.library,
        segment_id=segment["id"],
        fill_unseen=False,
    )
    assert result["shots"] == [
        {"index": 0, "label": "First shot", "start": 0, "end": 24, "times": "0:00–0:01"}
    ]
    assert result["place"] is None and not result["can_use_for_place"]
    assert result["name"] == "Stage from a take"
    assert result["refusal"] == "Select a take of this scene first."


def test_a_refused_seed_is_hidden_when_another_take_is_selected(scene):
    s = scene
    start(s, shot=1, use_for_place=False)
    drain(s.run)
    assert transact(s.workspace, plan_for_segment, s.segment["id"]).seed["state"] == "refused"
    with s.workspace.db.transaction() as session:
        candidate = OPERATIONS.add_candidate(
            session, s.candidate["job_id"], s.asset["id"], "other-take", "video"
        )
        SelectionService().select(
            session,
            movie_id=s.segment["movie_id"],
            segment_id=s.segment["id"],
            candidate_id=candidate["id"],
            asset_id=s.asset["id"],
            authority="user",
            expected_revision=1,
        )
    assert transact(s.workspace, plan_for_segment, s.segment["id"]).seed is None
    assert preview(s, shot=1)["seed"] is None


def test_a_frames_seed_can_run_queued_fixes_without_a_panorama(scene):
    from test_set_build import call, exclude, runner

    s = scene
    started = start(s)
    drain(s.run)
    set_id = operation(s.workspace, started["id"])["review"][-1]["set_id"]
    before = call(s.workspace, sets.set_state, set_id)
    base = before["version"]
    exclude(s.workspace, set_id, before, "K2")
    queued = call(s.workspace, set_build.run_queued_fixes, set_id, version_id=base["id"])
    drain(runner(s.workspace))
    built = call(s.workspace, sets.set_state, set_id)
    assert built["build"]["state"] == "succeeded", built["build"]
    repaired = call(s.workspace, sets.set_state, set_id, built["build"]["version_id"])["version"]
    assert (repaired["parent_id"], repaired["state"], repaired["origin"]) == (
        base["id"],
        "proposed",
        "repair",
    )
    assert repaired["map_meta"]["grid"] == base["map_meta"]["grid"]
    assert "K2" not in [c["label"] for c in repaired["map_meta"]["cameras"]]
    assert len(repaired["map_meta"]["cameras"]) == len(base["map_meta"]["cameras"]) - 1
    assert any(k["was"] == "K2" and k["role"] == "appearance-only" for k in repaired["keyframes"])
    jobs = operation(s.workspace, queued["build"]["operation_id"])["jobs"]
    with s.workspace.db.transaction() as session:
        summary = next(o for o in jobs[-1]["outputs"] if o["output_key"] == "summary")
        fit = json.loads(s.workspace.library.read_blob(session, summary["artifact_id"]))
    assert fit["command"] == "init-frames"  # the fake reports which real worker branch called it


def test_retry_keeps_settings_when_integration_defaults_change(scene, monkeypatch):
    s = scene
    s.comfy.execution_error = True
    started = start(s)
    drain(s.run)
    original = set_seed.settings()
    monkeypatch.setattr(set_seed, "settings", lambda: original | {"frames": original["frames"] + 1})
    again = transact(s.workspace, set_seed.retry_seed, s.workspace.library, started["id"])
    assert again["request"] == started["request"] | {"attempt": 2}
