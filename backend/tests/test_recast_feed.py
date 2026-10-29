"""The feed reserves renders once, assembles settled windows and never chooses takes."""

import json

import pytest
from test_recast_service import edit
from test_recast_service import recast_template as recast_template  # noqa: PLC0414
from test_recast_service import world as world  # noqa: PLC0414

from gaiavfx.application import recast
from gaiavfx.application import recast_projection as p
from gaiavfx.application.operations import OperationRunner
from gaiavfx.shared.errors import ApplicationError


def run(world, state, budget=20, mode="draft", frame_range=None):
    return edit(world, state, recast.start_run, mode=mode, range=frame_range, render_budget=budget)


def settle_all(world, kind):
    with world.db.transaction() as session:
        for operation in world.ops.recent(session, kind=kind):
            world.settle(session, operation["id"])


def drain(world, recast_id):
    for _ in range(12):
        world.invoke(recast.feed)
        settle_all(world, "character_replacement")
        world.invoke(recast.feed)
        settle_all(world, "shot_assembly")
        world.invoke(recast.feed)
        state = world.invoke(recast.recast_state, recast_id)
        if state["run"]["state"] == "finished" and all(
            s["status"] not in {"rendering", "queued"} for s in state["shots"]
        ):
            return state
    raise AssertionError("Run did not finish")


def test_feed_order_idempotence_budget_resume_finish(world):
    state = run(world, world.ready(), budget=4)
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 2
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 2
    with world.db.transaction() as session:
        operations = [world.ops.operation(session, x) for x in world.calls["replacement"]]
    assert [o["request"]["analysis"] for o in operations] == world.calls["analysis"][:2]
    assert sum(o["budget_reserved"] for o in operations) == 4
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    assert len(world.calls["assembly"]) == 2
    # Two one-take windows return their spare reservation; a third can start.
    assert len(world.calls["replacement"]) == 3
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    settle_all(world, "shot_assembly")
    world.invoke(recast.feed)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["run"]["state"] == "paused"
    assert state["run"]["renders_used"] == 3
    state = run(world, state, budget=5)
    state = drain(world, state["id"])
    assert state["run"]["state"] == "finished"
    assert state["run"]["renders_used"] == 4
    assert state["run"]["shots_done"] == 4
    assert all(s["status"] == "needs_review" and s["choice"] is None for s in state["shots"])
    with world.db.transaction() as session:
        assert p.store.load(session, state["id"])["choices"] == []


def test_head_layer_reserves_feeds_reveals_chooses_and_chains(world):
    state = drain(world, run(world, world.ready())["id"])
    shot = state["shots"][0]
    character = shot["plan"]["performers"][0]
    parent = shot["versions"][0]
    before = state["run"]["renders_used"]
    estimate = state["estimate"]
    state = edit(
        world,
        state,
        recast.start_head_pass,
        shot["id"],
        version=parent["id"],
        character_id=character,
    )
    assert state["run"]["renders_used"] == before + 1
    assert state["estimate"] == estimate
    assert state["shots"][0]["status"] == "rendering"
    assert state["shots"][0]["choice"] is None
    progress = state["shots"][0]["layers"][0]
    assert progress == {
        "character_id": character,
        "state": "rendering",
        "windows_settled": 0,
        "windows_total": 1,
        "renders_reserved": 1,
    }
    assert world.invoke(recast.recast_state, state["id"])["shots"][0]["layers"] == [progress]
    assert "parent" not in json.dumps(state) and "candidate_id" not in json.dumps(state)
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])["shots"][0]
        layer = stored["layers"][0]
        head_op = world.ops.operation(session, layer["operations"][0])
        from gaiavfx.application.recast_planning import head_seed

        assert head_op["request"]["seed"] == head_seed(
            {"id": state["id"]}, world.report["shots"][0], 0
        )
    with pytest.raises(ApplicationError, match="already exists"):
        edit(
            world,
            state,
            recast.start_head_pass,
            shot["id"],
            version=parent["id"],
            character_id=character,
        )
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])["shots"][0]
        assembly = world.ops.operation(session, stored["layers"][0]["assembly_op"])
        assert assembly["request"]["layer"] == "head"
        assert assembly["request"]["parent"] == {
            "assembly_op": layer["parent_assembly"],
            "version_id": parent["id"],
            "take": 0,
            "track_id": "person0",
            "track_ids": ["person0"],
        }
    settle_all(world, "shot_assembly")
    state = world.invoke(recast.recast_state, state["id"])
    assert [v["letter"] for v in state["shots"][0]["versions"]] == ["A", "B"]
    derived = state["shots"][0]["versions"][1]
    assert set(derived) == {"id", "letter", "asset_id", "checks"}
    assert state["shots"][0]["versions"][0] == parent
    state = edit(world, state, recast.choose_version, shot["id"], version=derived["id"])
    assert state["shots"][0]["choice"]["version"] == derived["id"]
    revealed = world.invoke(recast.reveal_shot, state["id"], shot["id"])["versions"][1]
    assert revealed["layer"] == "head" and revealed["parent"]["version_id"] == parent["id"]
    # A head version can be a parent; changing the mapping does not change its target.
    state = edit(
        world, state, recast.set_target, character, asset_id="picture2", label="New target"
    )
    state = edit(
        world,
        state,
        recast.start_head_pass,
        shot["id"],
        version=derived["id"],
        character_id=character,
    )
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])["shots"][0]
        second = world.ops.operation(session, stored["layers"][1]["operations"][0])
        assert second["request"]["facts"]["performers"][0]["target_asset"] == "picture"
    # Retaking the body remains possible with an older layer and an active head layer.
    state = edit(world, state, recast.retake_shot, shot["id"], mode="draft")
    state = edit(world, state, recast.cancel_run)
    assert state["shots"][0]["layers"][1]["state"] == "cancelled"
    with world.db.transaction() as session:
        assert world.ops.operation(session, second["id"])["cancel_requested"]
    assert state["shots"][0]["choice"]["version"] == derived["id"]


def test_head_layer_budget_bad_inputs_and_revision(world):
    state = drain(world, run(world, world.ready(), budget=5)["id"])
    state = run(world, state, budget=4)
    shot = state["shots"][0]
    character, parent = shot["plan"]["performers"][0], shot["versions"][0]["id"]
    for version, cid, status in (
        ("unknown", character, 422),
        (parent, "unknown", 422),
        (parent, character, 409),
    ):
        with pytest.raises(ApplicationError) as exc:
            edit(
                world, state, recast.start_head_pass, shot["id"], version=version, character_id=cid
            )
        assert exc.value.status == status
    unchanged = world.invoke(recast.recast_state, state["id"])
    assert unchanged["revision"] == state["revision"]
    assert unchanged["shots"][0]["layers"] == []
    with pytest.raises(ApplicationError, match="reload"):
        world.invoke(
            recast.start_head_pass,
            state["id"],
            shot["id"],
            expected_revision=0,
            version=parent,
            character_id=character,
        )


def test_feed_propagates_head_window_cancellation(world):
    state = drain(world, run(world, world.ready())["id"])
    shot = state["shots"][0]
    state = edit(
        world,
        state,
        recast.start_head_pass,
        shot["id"],
        version=shot["versions"][0]["id"],
        character_id=shot["plan"]["performers"][0],
    )
    with world.db.transaction() as session:
        layer = p.store.load(session, state["id"])["shots"][0]["layers"][0]
        world.ops.request_cancel(session, layer["operations"][0])
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        layer = p.store.load(session, state["id"])["shots"][0]["layers"][0]
        assert layer["cancelled"] and layer["assembly_op"] is None


def test_head_on_a_settled_retake_is_not_reported_as_a_body_retake(world):
    state = drain(world, run(world, world.ready(), frame_range={"start": 0, "end": 73})["id"])
    state = edit(world, state, recast.retake_shot, "s0001", mode="draft")
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    settle_all(world, "shot_assembly")
    state = world.invoke(recast.recast_state, state["id"])
    shot = state["shots"][0]
    state = edit(
        world,
        state,
        recast.start_head_pass,
        "s0001",
        version=shot["versions"][1]["id"],
        character_id=shot["plan"]["performers"][0],
    )
    assert state["shots"][0]["status"] == "rendering"
    assert state["shots"][0]["retaking"] is False


def test_pause_finishes_inflight_cancel_queued_priority_and_range(world):
    state = world.ready()
    state = edit(world, state, recast.patch_shot, "s0004", included=True, priority=0)
    state = run(world, state, frame_range={"start": 73, "end": 292})
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        first = world.ops.operation(session, world.calls["replacement"][0])
    assert first["request"]["analysis"] == world.calls["analysis"][3]
    state = edit(world, state, recast.pause_run)
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    settle_all(world, "shot_assembly")
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 2
    state = world.invoke(recast.recast_state, state["id"])
    assert state["run"]["state"] == "paused"
    state = run(world, state, frame_range={"start": 73, "end": 292})
    world.invoke(recast.feed)
    state = edit(world, state, recast.cancel_run)
    assert state["run"]["state"] == "idle"
    with world.db.transaction() as session:
        last = world.ops.operation(session, world.calls["replacement"][-1])
    assert last["cancel_requested"] and last["state"] == "cancelled"
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 3


def test_versions_blind_choice_retake_accumulates_and_reveal(world):
    state = drain(world, run(world, world.ready())["id"])
    first = state["shots"][0]["versions"][0]
    assert first["letter"] == "A"
    assert set(first) == {"id", "letter", "asset_id", "checks"}
    assert "candidate_id" not in json.dumps(state) and "treatment" not in json.dumps(state)
    state = edit(world, state, recast.choose_version, "s0001", version=first["id"])
    assert state["shots"][0]["status"] == "approved"
    chosen_revision = state["revision"]
    state = edit(world, state, recast.choose_version, "s0001", version="unchosen")
    assert state["revision"] == chosen_revision + 1
    assert state["shots"][0]["choice"] is None
    assert state["shots"][0]["status"] == "needs_review"
    with pytest.raises(ApplicationError) as stale:
        world.invoke(
            recast.choose_version,
            state["id"],
            "s0001",
            expected_revision=chosen_revision,
            version="unchosen",
        )
    assert stale.value.status == 409
    state = edit(world, state, recast.choose_version, "s0001", version=first["id"])
    state = edit(world, state, recast.retake_shot, "s0001", mode="draft")
    assert state["shots"][0]["status"] == "queued"
    assert state["shots"][0]["retaking"] is True
    assert world.invoke(recast.recast_state, state["id"])["shots"][0]["retaking"] is True
    assert state["run"]["shots_done"] == 3
    assert state["shots"][0]["versions"] == [first]
    with pytest.raises(ApplicationError):
        edit(world, state, recast.retake_shot, "s0001", mode="draft")
    # Fed but not yet claimed (ComfyUI busy or down), the retake waits rather than renders.
    world.invoke(recast.feed)
    assert world.invoke(recast.recast_state, state["id"])["shots"][0]["status"] == "queued"
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    settle_all(world, "shot_assembly")
    state = world.invoke(recast.recast_state, state["id"])
    assert [v["letter"] for v in state["shots"][0]["versions"]] == ["A", "B"]
    assert state["shots"][0]["retaking"] is False
    assert state["shots"][0]["choice"]["version"] == first["id"]
    reveal = world.invoke(recast.reveal_shot, state["id"], "s0001")
    assert len(reveal["versions"]) == 2 and reveal["versions"][0]["windows"][0]["seed"] == 123
    state = edit(world, state, recast.choose_version, "s0001", version="original")
    assert state["shots"][0]["status"] == "kept_original"
    state = edit(world, state, recast.choose_version, "s0001", version="unchosen")
    state = world.invoke(recast.recast_state, state["id"])
    assert state["shots"][0]["choice"] is None
    assert state["shots"][0]["status"] == "needs_review"
    with world.db.transaction() as session:
        choices = p.store.load(session, state["id"])["choices"]
    assert [c["version"] for c in choices] == [
        first["id"],
        "unchosen",
        first["id"],
        "original",
        "unchosen",
    ]
    assert all(c["authority"] == "user" for c in choices)


def test_final_budget_and_retake_exhaustion_roll_back(world):
    state = run(world, world.ready(), budget=3, mode="final")
    world.invoke(recast.feed)
    assert not world.calls["replacement"]
    state = world.invoke(recast.recast_state, state["id"])
    assert state["run"]["state"] == "paused"
    state = drain(world, run(world, state, budget=5, mode="draft")["id"])
    with pytest.raises(ApplicationError):
        edit(world, state, recast.retake_shot, "s0001", mode="draft")
    after = world.invoke(recast.recast_state, state["id"])
    assert after["shots"][0]["versions"] == state["shots"][0]["versions"]
    assert after["revision"] == state["revision"]


def test_window_failure_still_assembles_and_finishes(world):
    state = run(world, world.ready(), frame_range={"start": 0, "end": 73})
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        world.settle(session, world.calls["replacement"][0], "failed")
    world.invoke(recast.feed)
    settle_all(world, "shot_assembly")
    world.invoke(recast.feed)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["shots"][0]["status"] == "failed"
    assert state["run"]["state"] == "finished"
    assert state["run"]["shots_done"] == 0
    assert state["run"]["shots_total"] == 1


def test_render_progress_excludes_gaps_and_unplanned_shots(world):
    state = world.ready()
    world.window_reports[world.calls["analysis"][0]]["gaps"] = [
        {"code": "presence_gap", "message": "Performer leaves the window."}
    ]
    world.touch_window(world.calls["analysis"][0])
    state = world.invoke(recast.recast_state, state["id"])
    assert state["shots"][0]["status"] == "gap"
    assert state["run"]["shots_done"] == 0
    assert state["run"]["shots_total"] == 3
    state = drain(world, run(world, state)["id"])
    assert state["run"]["shots_done"] == state["run"]["shots_total"] == 3


def test_sweeps_cadence_failure_containment_and_recording(world, monkeypatch):
    from gaiavfx.application import operations

    events = []

    def bad(session, *, library):
        events.append("bad")
        raise RuntimeError("fixture failure")

    def good(session, *, library):
        events.append("good")
        return False

    monkeypatch.setattr(operations, "SWEEPS", [bad, good])
    runner = OperationRunner(world.db, None, world.library, "unused", sweep_seconds=60)
    assert runner.run_once() is False
    assert events == ["bad", "good"]
    assert runner.run_once() is False
    assert events == ["bad", "good"]
    logged = json.loads((world.db.root / "logs/operation-sweep-errors.jsonl").read_text())
    assert logged["sweep"] == "bad" and logged["error"] == "fixture failure"


@pytest.mark.parametrize("long", [False, True])
def test_over_reservation_rolls_back_windows_and_feed_state(world, monkeypatch, long):
    state = run(world, long_shots(world) if long else world.ready(), budget=8 if long else 2)
    original = world.window_api.start_shot_replacement

    def overspend(session, library, **kwargs):
        return original(session, library, **{**kwargs, "mode": "final"})

    monkeypatch.setattr(world.window_api, "start_shot_replacement", overspend)
    world.invoke(recast.feed)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["run"]["state"] == "paused"
    assert "exceeded" in state["run"]["reason"]
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == (3 if long else 1)
    with world.db.transaction() as session:
        assert world.ops.recent(session, kind="character_replacement") == []
        assert all(
            not s["window_replacement_ops"] for s in p.store.load(session, state["id"])["shots"]
        )


def test_measured_estimate_excludes_queue_wait_and_unused_reservations(world, monkeypatch):
    state = drain(world, run(world, world.ready())["id"])
    original = p.operations.operation

    def operation(session, operation_id):
        value = original(session, operation_id)
        if operation_id in world.calls["replacement"]:
            # A long queue wait is not execution; two renders used out of four reserved.
            value["budget_reserved"] = 4
            value["created_at"] = "2026-10-01T00:00:00+00:00"
            value["jobs"] = [
                {
                    "executor": "comfy",
                    "kind": "video",
                    "state": "succeeded",
                    "updated_at": "2026-10-08T00:03:00+00:00",
                    "state_history": [
                        {"state": "queued", "at": "2026-10-01T00:00:00+00:00"},
                        {"state": "running", "at": "2026-10-08T00:01:00+00:00"},
                        {"state": "succeeded", "at": "2026-10-08T00:03:00+00:00"},
                    ],
                }
            ] * 2
        return value

    monkeypatch.setattr(p.operations, "operation", operation)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["estimate"]["basis"] == "measured"
    assert state["estimate"]["gpu_seconds_low"] == 672
    assert state["estimate"]["gpu_seconds_high"] == 1248
    assert state["run"]["renders_used"] == 8  # Actual renders, not the 16 reserved.


@pytest.mark.parametrize("failure", ["cancel", "failed_window", "empty_assembly"])
def test_head_pass_retry_after_cancel_or_failure(world, failure):
    state = drain(world, run(world, world.ready())["id"])
    shot = state["shots"][0]
    args = {"version": shot["versions"][0]["id"], "character_id": shot["plan"]["performers"][0]}
    state = edit(world, state, recast.start_head_pass, "s0001", **args)
    if failure == "cancel":
        state = edit(world, state, recast.cancel_run)
    else:
        with world.db.transaction() as session:
            layer = p.store.load(session, state["id"])["shots"][0]["layers"][0]
            world.settle(
                session,
                layer["operations"][0],
                "failed" if failure == "failed_window" else "succeeded",
            )
        world.invoke(recast.feed)
        with world.db.transaction() as session:
            layer = p.store.load(session, state["id"])["shots"][0]["layers"][0]
            world.versions[layer["assembly_op"]] = []
        settle_all(world, "shot_assembly")
        state = world.invoke(recast.recast_state, state["id"])
    assert state["shots"][0]["layers"][0]["state"] == (
        "cancelled" if failure == "cancel" else "failed"
    )
    before = state["run"]["renders_used"]
    state = edit(world, state, recast.start_head_pass, "s0001", **args)
    assert len(state["shots"][0]["layers"]) == 2
    assert state["shots"][0]["layers"][1]["state"] == "rendering"
    assert state["run"]["renders_used"] == before + 1


def test_head_pass_uses_root_body_tracks_and_targets_in_each_window_after_retake(world):
    world.report["shots"] = [{"index": 1, "start": 0, "end": 124, "poster_artifact": None}]
    world.report["frame_count"] = 124
    world.report["tracks"] = world.report["tracks"][:3]
    for track in world.report["tracks"]:
        track["shot"] = 1
    world.report["clusters"][0]["track_ids"] = ["t1", "t2"]
    world.report["unknown_track_ids"] = ["t3"]
    state = world.ready()
    first, second = world.calls["analysis"]
    world.window_reports[first]["tracks"] = world.window_reports[first]["tracks"][:1]
    world.window_reports[second]["tracks"] = world.window_reports[second]["tracks"][1:]
    for analysis in (first, second):
        world.touch_window(analysis)
    state = drain(world, run(world, world.invoke(recast.recast_state, state["id"]))["id"])
    shot = state["shots"][0]
    original = shot["versions"][0]["id"]
    character = shot["plan"]["performers"][0]
    state = edit(world, state, recast.move_tracks, track_ids=["t2"], to="unknown")
    state = edit(world, state, recast.move_tracks, track_ids=["t3"], to=character)
    state = edit(
        world, state, recast.set_target, character, asset_id="picture2", label="Retake target"
    )
    state = edit(world, state, recast.retake_shot, "s0001", mode="draft")
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    settle_all(world, "shot_assembly")
    state = world.invoke(recast.recast_state, state["id"])
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])["shots"][0]
        body = [
            world.ops.operation(session, op)["request"]["facts"]["performers"][0]
            for op in stored["window_replacement_ops"]
        ]
        assert [b["track_id"] for b in body] == ["person0", "person2"]
        assert [b["target_asset"] for b in body] == ["picture2", "picture2"]
    parent = original
    for index in range(2):
        state = edit(
            world, state, recast.start_head_pass, "s0001", version=parent, character_id=character
        )
        with world.db.transaction() as session:
            layer = p.store.load(session, state["id"])["shots"][0]["layers"][index]
            heads = [
                world.ops.operation(session, op)["request"]["facts"]["performers"][0]
                for op in layer["operations"]
            ]
            assert [h["track_id"] for h in heads] == ["person0", "person1"]
            assert [h["target_asset"] for h in heads] == ["picture", "picture"]
        settle_all(world, "character_replacement")
        world.invoke(recast.feed)
        settle_all(world, "shot_assembly")
        state = world.invoke(recast.recast_state, state["id"])
        derived = world.invoke(recast.reveal_shot, state["id"], "s0001")["versions"][-1]
        assert derived["parent"]["track_ids"] == ["person0", "person1"]
        parent = derived["id"]


@pytest.mark.parametrize(
    "face_state,status,message",
    [
        ("no_face", 422, "Head pass needs a target with a human face"),
        ("missing", 409, "Checking the target picture; try again in a moment"),
        ("checking", 409, "Checking the target picture; try again in a moment"),
        ("failed", 409, "Checking the target picture; try again in a moment"),
    ],
)
def test_head_target_refusal_uses_frozen_picture_before_reservation(
    world, monkeypatch, face_state, status, message
):
    from gaiavfx.application import target_faces

    state = drain(world, run(world, world.ready())["id"])
    shot = state["shots"][0]
    cid = shot["plan"]["performers"][0]
    # Invalidate the old scan; today's mapping is human but the frozen body target is checked.
    monkeypatch.setattr(target_faces, "SCAN_VERSION", 2)
    state = edit(world, state, recast.set_target, cid, asset_id="picture2", label="New human")
    world.settle_target("picture2")
    if face_state == "no_face":
        world.settle_target(scores=(0.525,))
    elif face_state == "failed":
        world.settle_target(state="failed")
    elif face_state == "checking":
        world.invoke(target_faces.start_target_report, "picture")
    with world.db.transaction() as session:
        before = world.ops.recent(session)
        with pytest.raises(ApplicationError) as exc:
            recast.start_head_pass(
                session,
                world.library,
                state["id"],
                shot["id"],
                expected_revision=state["revision"],
                version=shot["versions"][0]["id"],
                character_id=cid,
            )
        assert exc.value.status == status and str(exc.value) == message
        # Pending callers retain the report, without committing a layer or a reservation.
        assert target_faces.target_report(session, world.library, "picture") is not None
        after = world.ops.recent(session)
        assert sum(o["budget_reserved"] for o in after) == sum(o["budget_reserved"] for o in before)
        assert len(after) == len(before) + (face_state in {"missing", "failed"})
    current = world.invoke(recast.recast_state, state["id"])
    assert current["revision"] == state["revision"]
    assert current["shots"][0]["layers"] == []
    assert current["run"]["renders_used"] == state["run"]["renders_used"]
    assert current["estimate"] == state["estimate"]


def test_feed_backfills_old_confirmed_targets_once_without_read_writes(world, monkeypatch):
    from gaiavfx.application import target_faces

    state = world.ready()
    monkeypatch.setattr(target_faces, "SCAN_VERSION", 2)
    current = world.invoke(recast.recast_state, state["id"])
    assert current["characters"][0]["target"]["face"] == "checking"
    assert world.invoke(target_faces.target_report, "picture") is None
    assert world.invoke(recast.feed)
    operation = world.invoke(target_faces.start_target_report, "picture")
    assert not operation["_created"] and operation["budget_reserved"] == 0
    assert not world.invoke(recast.feed)
    current = world.invoke(recast.recast_state, state["id"])
    assert current["run"]["renders_used"] == 0 and current["estimate"] == state["estimate"]


def test_failed_backfill_does_not_pause_rendering(world, monkeypatch, caplog):
    from gaiavfx.application import recast_feed, target_faces

    state = run(world, world.ready())
    monkeypatch.setattr(target_faces, "SCAN_VERSION", 2)

    def unavailable(*args, **kwargs):
        raise ApplicationError("Cannot start face report", 503)

    monkeypatch.setattr(recast_feed, "start_target_report", unavailable)
    world.invoke(recast.feed)
    current = world.invoke(recast.recast_state, state["id"])
    assert current["run"]["state"] == state["run"]["state"] == "running"
    assert len(world.calls["replacement"]) == 2
    assert "Target face backfill failed" in caplog.text


def test_error_paused_recast_is_not_backfilled_or_saved(world, monkeypatch):
    from gaiavfx.application import recast_feed

    state = world.ready()
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        stored.update(run_state="paused", run_reason="Run error: existing failure")
        p.store.save(session, stored)

    def unexpected(*args, **kwargs):
        pytest.fail("Error-paused recast must not be backfilled or saved")

    monkeypatch.setattr(recast_feed, "start_target_report", unexpected)
    monkeypatch.setattr(p.store, "save", unexpected)
    assert not world.invoke(recast.feed)


def test_joint_body_allows_human_head_pass_when_other_target_has_no_face(world):
    world.report["tracks"][1].update(shot=1, frames=world.report["tracks"][0]["frames"])
    world.report["clusters"] = [
        {**world.report["clusters"][0], "id": "c01", "track_ids": ["t1", "t3", "t4"]},
        {**world.report["clusters"][0], "id": "c02", "track_ids": ["t2"]},
    ]
    state = world.create()
    human, robot = [c["id"] for c in state["characters"]]
    state = edit(world, state, recast.set_target, human, asset_id="picture", label="Human")
    state = edit(world, state, recast.set_target, robot, asset_id="picture2", label="Robot")
    world.settle_target()
    world.settle_target("picture2", scores=(0.525,))
    state = edit(world, state, recast.plan_recast, range=None)
    state = drain(world, run(world, state)["id"])
    shot = state["shots"][0]
    assert set(shot["plan"]["performers"]) == {human, robot}
    state = edit(
        world,
        state,
        recast.start_head_pass,
        shot["id"],
        version=shot["versions"][0]["id"],
        character_id=human,
    )
    assert state["shots"][0]["layers"][0]["character_id"] == human


def test_faceless_target_shots_start_without_faceswap(world):
    """Batch 6: FaceSwap on a robot target painted the source actor's face onto it."""
    world.report["tracks"][1].update(shot=1, frames=world.report["tracks"][0]["frames"])
    world.report["clusters"] = [
        {**world.report["clusters"][0], "id": "c01", "track_ids": ["t1", "t3", "t4"]},
        {**world.report["clusters"][0], "id": "c02", "track_ids": ["t2"]},
    ]
    state = world.create()
    human, robot = [c["id"] for c in state["characters"]]
    state = edit(world, state, recast.set_target, human, asset_id="picture", label="Human")
    state = edit(world, state, recast.set_target, robot, asset_id="picture2", label="Robot")
    world.settle_target()
    world.settle_target("picture2", scores=(0.525,))
    state = edit(world, state, recast.plan_recast, range=None)
    state = drain(world, run(world, state)["id"])
    shot = state["shots"][0]
    assert set(shot["plan"]["performers"]) == {human, robot}
    with world.db.transaction() as session:
        windows = [
            world.ops.operation(session, op_id)
            for op_id in p.store.load(session, state["id"])["shots"][0]["window_replacement_ops"]
        ]
    assert windows and all(w["request"]["faceswap_allowed"] is False for w in windows)


def test_human_targets_keep_faceswap_to_the_policy(world):
    state = drain(world, run(world, world.ready())["id"])
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])["shots"]
        windows = [
            world.ops.operation(session, op_id)
            for s in stored
            for op_id in s["window_replacement_ops"]
        ]
    assert windows and all(w["request"]["faceswap_allowed"] is True for w in windows)


@pytest.mark.parametrize("definitive", [None, "no_face", "failed"])
def test_head_pass_starts_all_missing_window_targets_and_prefers_refusal(
    world, monkeypatch, definitive
):
    from copy import deepcopy

    from gaiavfx.application import target_faces
    from gaiavfx.modules.generation.operations.persistence import OperationRecord

    world.report["shots"] = [{"index": 1, "start": 0, "end": 124, "poster_artifact": None}]
    world.report["frame_count"] = 124
    world.report["tracks"] = world.report["tracks"][:1]
    world.report["clusters"][0]["track_ids"] = ["t1"]
    state = drain(world, run(world, world.ready())["id"])
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])["shots"][0]
        assert len(stored["window_replacement_ops"]) == 2
        body = session.get(OperationRecord, stored["window_replacement_ops"][1])
        request = deepcopy(body.request)
        request["facts"]["performers"][0]["target_asset"] = "picture2"
        body.request = request
    monkeypatch.setattr(target_faces, "SCAN_VERSION", 2)
    if definitive == "no_face":
        world.settle_target("picture2", scores=(0.525,))
    elif definitive == "failed":
        world.settle_target("picture2", state="failed")
        original = recast.start_target_report

        def refuse_retry(session, library, asset_id, **kwargs):
            if asset_id == "picture2":
                raise ApplicationError("Retry unavailable", 422)
            return original(session, library, asset_id, **kwargs)

        monkeypatch.setattr(recast, "start_target_report", refuse_retry)
    shot = state["shots"][0]
    with world.db.transaction() as session:
        with pytest.raises(ApplicationError) as error:
            recast.start_head_pass(
                session,
                world.library,
                state["id"],
                shot["id"],
                expected_revision=state["revision"],
                version=shot["versions"][0]["id"],
                character_id=shot["plan"]["performers"][0],
            )
        assert error.value.status == (422 if definitive else 409)
        assert target_faces.target_report(session, world.library, "picture")["state"] == "checking"
        assert target_faces.target_report(session, world.library, "picture2")["state"] == (
            definitive or "checking"
        )
        assert p.store.load(session, state["id"])["shots"][0]["layers"] == []


def long_shots(world, count=1, length=175):
    world.report["shots"] = [
        {"index": i + 1, "start": i * length, "end": (i + 1) * length, "poster_artifact": None}
        for i in range(count)
    ]
    world.report["frame_count"] = count * length
    world.report["tracks"] = world.report["tracks"][:count]
    for i, track in enumerate(world.report["tracks"]):
        track["frames"] = list(range(i * length, (i + 1) * length, 5))
    world.report["clusters"][0]["track_ids"] = [t["id"] for t in world.report["tracks"]]
    return world.ready()


def test_latent_feed_waits_reserves_replays_and_retakes(world):
    from gaiavfx.application.recast_feed import used

    state = run(world, long_shots(world, length=124), budget=4)
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 1
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        assert used(session, stored) == 4
        first = world.ops.operation(session, world.calls["replacement"][0])
        assert first["request"]["save_latent"] and first["request"]["crop_override"]
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 1
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 2 and not world.calls["assembly"]
    with world.db.transaction() as session:
        second = world.ops.operation(session, world.calls["replacement"][1])
        assert second["request"]["handoff"]["predecessor_operation"] == first["id"]
        assert "chain" not in second["request"]
    state = drain(world, state["id"])
    assert state["run"]["renders_used"] == 2
    state = run(world, state, budget=6)
    state = edit(world, state, recast.retake_shot, state["shots"][0]["id"], mode="draft")
    assert len(world.calls["replacement"]) == 3
    assert drain(world, state["id"])["run"]["renders_used"] == 4


def test_fresh_windows_wait_for_both_neighbours_before_bridges(world):
    from gaiavfx.application.recast_feed import used

    state = run(world, long_shots(world), budget=8)
    assert state["estimate"]["renders"] == 8
    assert state["estimate"]["gpu_seconds_low"] == int(8 * 280 * 0.7)
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 3 and not world.calls.get("bridge")
    with world.db.transaction() as session:
        assert used(session, p.store.load(session, state["id"])) == 8
        world.settle(session, world.calls["replacement"][0])
    world.invoke(recast.feed)
    assert not world.calls.get("bridge")
    with world.db.transaction() as session:
        world.settle(session, world.calls["replacement"][1])
    world.invoke(recast.feed)
    assert len(world.calls["bridge"]) == 1 and not world.calls["assembly"]
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    assert len(world.calls["bridge"]) == 2 and not world.calls["assembly"]
    world.invoke(recast.feed)
    assert len(world.calls["bridge"]) == 2
    state = drain(world, state["id"])
    assert state["run"]["renders_used"] == 5 and state["shots"][0]["status"] == "needs_review"


def test_pending_bridges_reserve_before_second_shot_and_cancel(world):
    from gaiavfx.application.recast_feed import in_flight, used

    state = run(world, long_shots(world, 2), budget=8)
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 3
    settle_all(world, "character_replacement")
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        assert used(session, stored) == 5 and in_flight(session, stored["shots"][0])
    world.invoke(recast.feed)
    assert len(world.calls["bridge"]) == 2
    state = edit(world, world.invoke(recast.recast_state, state["id"]), recast.cancel_run)
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        assert not stored["shots"][0]["bridge_ops"]
        assert len(stored["shots"][0]["plan"]["_history"][-1]["bridge_ops"]) == 2
        assert all(
            world.ops.operation(session, b)["cancel_requested"] for b in world.calls["bridge"]
        )


def test_failed_window_stops_and_retake_renders_windows_and_bridges(world):
    state = run(world, long_shots(world), budget=11)
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        for i, operation in enumerate(world.calls["replacement"]):
            world.settle(session, operation, "failed" if i == 1 else "succeeded")
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        assembly = world.ops.operation(session, world.calls["assembly"][-1])
        assert assembly["request"]["stopped"] == {"window": 1, "reason": "no_usable_take"}
    state = drain(world, state["id"])
    assert state["shots"][0]["status"] == "failed"
    state = edit(world, state, recast.retake_shot, state["shots"][0]["id"], mode="draft")
    assert len(world.calls["replacement"]) == 6
    state = drain(world, state["id"])
    assert len(world.calls["bridge"]) == 2 and state["shots"][0]["status"] == "needs_review"


def test_legacy_inflight_chain_is_superseded_without_a_successor(world):
    state = run(world, long_shots(world, length=124), budget=4)
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        stored["shots"][0]["plan"].pop("_handoff")
        p.store.save(session, stored)
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 1
    with world.db.transaction() as session:
        assembly = world.ops.operation(session, world.calls["assembly"][-1])
        assert assembly["request"]["stopped"] == {"window": 1, "reason": "handoff_superseded"}
    state = drain(world, state["id"])
    assert state["shots"][0]["status"] == "failed"


def test_stored_chained_operation_is_superseded_even_with_a_current_handoff_plan(world):
    from gaiavfx.modules.generation.operations.lifecycle import JobSpec

    state = run(world, long_shots(world), budget=5)
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        shot = stored["shots"][0]
        assert shot["plan"]["_handoff"]["kind"] == "bridge"
        legacy = []
        for index, analysis in enumerate(shot["window_analysis_ops"][:2]):
            request = {
                "source_asset": "film",
                "analysis_operation": analysis,
                "seed": 7,
                "facts": {"context_frames": 22 if index else 0},
            }
            if index:
                request["chain"] = {
                    "predecessor_operation": legacy[0],
                    "candidate_id": legacy[0] + "-candidate",
                    "asset_id": "previous-composite",
                    "sha256": "frozen",
                }
            operation = world.ops.create(
                session,
                kind="character_replacement",
                request=request,
                request_key=f"stored-chain-{index}",
                jobs=[JobSpec("video", "local", "fixture", step="replacement_composite")],
                budget_limit=None,
                budget=1,
            )
            legacy.append(operation["id"])
            world.settle(session, operation["id"])
        shot["window_replacement_ops"] = legacy
        p.store.save(session, stored)
    world.invoke(recast.feed)
    assert not world.calls["replacement"] and not world.calls.get("bridge")
    with world.db.transaction() as session:
        assembly = world.ops.operation(session, world.calls["assembly"][-1])
        assert assembly["request"]["window_operations"] == legacy
        assert assembly["request"]["stopped"] == {"window": 2, "reason": "handoff_superseded"}
    assert drain(world, state["id"])["shots"][0]["status"] == "failed"


def test_single_window_feed_omits_handoff_arguments(world, monkeypatch):
    original = world.window_api.start_shot_assembly
    calls = []

    def assembly(*args, **kwargs):
        calls.append(kwargs)
        return original(*args, **kwargs)

    monkeypatch.setattr(world.window_api, "start_shot_assembly", assembly)
    state = run(world, long_shots(world, length=73), budget=2)
    assert drain(world, state["id"])["shots"][0]["status"] == "needs_review"
    assert len(calls) == 1
    assert "handoff" not in calls[0] and "bridge_operations" not in calls[0]


def test_no_shared_crop_uses_two_fresh_windows(world, monkeypatch):
    def no_crop(*args, **kwargs):
        raise ValueError("overlapping avoided region")

    monkeypatch.setattr(world.window_api, "shared_crop_for_windows", no_crop)
    state = run(world, long_shots(world, length=124), budget=4)
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 2
    with world.db.transaction() as session:
        plan = p.store.load(session, state["id"])["shots"][0]["plan"]
        assert plan["_handoff"] == {"kind": "crossfade", "joins": [], "reason": "no_shared_crop"}
    assert drain(world, state["id"])["run"]["renders_used"] == 2


@pytest.mark.parametrize("mode", ["draft", "final"])
def test_last_join_uses_chain_take_in_draft_and_each_succeeded_take_in_final(
    world, monkeypatch, mode
):
    original = world.window_api.window_takes

    def multiple(session, library, operation):
        take = original(session, library, operation)[0]
        return {i: {"window": {**take["window"], "candidate_id": f"last-{i}"}} for i in range(3)}

    monkeypatch.setattr(world.window_api, "window_takes", multiple)
    state = run(world, long_shots(world), budget=17, mode=mode)
    assert state["estimate"]["renders"] == (8 if mode == "draft" else 17)
    world.invoke(recast.feed)
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    assert len(world.calls["bridge"]) == (2 if mode == "draft" else 4)
    with world.db.transaction() as session:
        ops = [world.ops.operation(session, key) for key in world.calls["bridge"]]
        assert [o["request"]["bridge"]["join"] for o in ops] == (
            [1, 2] if mode == "draft" else [1, 2, 2, 2]
        )
        assert all(o["budget_reserved"] == 1 for o in ops)
    state = drain(world, state["id"])
    assert state["run"]["renders_used"] == (5 if mode == "draft" else 16)


def settle_reference(world, kind, state="succeeded"):
    """Settle fixture jobs through Generation; retain image assets/candidates as collection would."""
    from gaiavfx.modules.library.assets import AssetRecord

    with world.db.transaction() as session:
        operation = world.ops.recent(session, kind=kind)[0]
        for job in operation["jobs"]:
            if state == "succeeded" and job["step"] != "portrait_crop":
                asset_id = job["id"]
                session.add(
                    AssetRecord(
                        id=asset_id,
                        sha256=asset_id,
                        name=asset_id,
                        kind="image",
                        path=asset_id,
                        mime="image/png",
                        info={"width": 768, "height": 1024},
                        provenance=[
                            {"type": "generation_candidate", "operation_id": operation["id"]}
                        ],
                    )
                )
                world.ops.add_candidate(session, job["id"], asset_id, "image", "image")
        world.settle(session, operation["id"], state)
        return world.ops.operation(session, operation["id"])


def waiting_reference(world, state):
    world.invoke(recast.feed)
    portraits = settle_reference(world, "face_portraits")
    world.invoke(recast.feed)
    sheet = settle_reference(world, "face_sheet")
    world.invoke(recast.feed)
    return world.invoke(recast.recast_state, state["id"]), portraits, sheet


def test_face_references_advance_alongside_latent_and_preserve_its_target(world):
    state = run(world, long_shots(world, length=124), budget=4)
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 1
    settle_reference(world, "face_portraits")
    settle_all(world, "character_replacement")

    # One sweep starts both the sheet and the next window.
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 2
    with world.db.transaction() as session:
        assert len(world.ops.recent(session, kind="face_sheet")) == 1
    sheet = settle_reference(world, "face_sheet")
    world.invoke(recast.feed)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["characters"][0]["target"]["reference"]["state"] == "waiting"
    assert state["run"]["state"] == "running"
    assert state["run"]["renders_used"] == 3
    state = edit(world, state, recast.approve_reference, state["characters"][0]["id"])
    assert state["characters"][0]["target"]["reference"] == {
        "asset_id": sheet["candidates"][0]["asset_id"],
        "state": "approved",
    }
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 2
    with world.db.transaction() as session:
        for operation_id in world.calls["replacement"]:
            request = world.ops.operation(session, operation_id)["request"]
            assert request["facts"]["performers"][0]["target_asset"] == "picture"
        assert len(world.ops.recent(session, kind="face_portraits")) == 1
        assert len(world.ops.recent(session, kind="face_sheet")) == 1
    state = drain(world, state["id"])
    assert state["run"]["renders_used"] == 2


def test_face_reference_feed_review_and_frozen_bindings(world):
    from gaiavfx.application import recast_planning as planning
    from gaiavfx.application.target_faces import target_report

    state = world.ready()
    cid = state["characters"][0]["id"]
    estimate = state["estimate"]
    world.invoke(recast.feed)
    first = world.invoke(recast.recast_state, state["id"])
    assert first["characters"][0]["target"]["reference"] == {"asset_id": None, "state": "making"}
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        assert len(world.ops.recent(session, kind="face_portraits")) == 1
    state, portraits, sheet = waiting_reference(world, state)
    sheet_id = sheet["candidates"][0]["asset_id"]
    assert portraits["budget_reserved"] == 3 and sheet["budget_reserved"] == 0
    assert state["run"]["renders_used"] == 0 and state["estimate"] == estimate
    assert state["characters"][0]["target"]["reference"] == {
        "asset_id": sheet_id,
        "state": "waiting",
    }
    assert sheet["jobs"][0]["step"] == "portrait_sheet"
    assert len(sheet["jobs"][0]["inputs"]) == 4
    # Render just the first shot before approval. Its frozen request must keep the picture.
    state = run(world, state, frame_range={"start": 0, "end": 73})
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        frozen = world.ops.operation(session, world.calls["replacement"][0])["request"]
    assert frozen["facts"]["performers"][0]["target_asset"] == "picture"
    stale = state.copy()
    state = edit(world, state, recast.approve_reference, cid)
    assert state["characters"][0]["target"]["reference"]["state"] == "approved"
    assert world.invoke(target_report, sheet_id) == {"state": "checking"}
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        assert planning.effective_target(stored["characters"][0]) == sheet_id
        assert stored["shots"][0]["plan"]["_bindings"][0]["target_asset"] == "picture"
        assert stored["shots"][1]["plan"]["_bindings"][0]["target_asset"] == sheet_id
        assert world.ops.operation(session, world.calls["replacement"][0])["request"] == frozen
        provenance = world.library.get(session, sheet_id)["provenance"][-1]
        assert provenance["approved_at"]
        assert provenance["recast_revision"] == state["revision"]
        assert provenance == {
            "type": "generated_reference",
            "subject": "recast_target",
            "recast_id": state["id"],
            "character_id": cid,
            "source_asset": "picture",
            "portraits_operation": portraits["id"],
            "sheet_operation": sheet["id"],
            "approved_by": "user",
            "approved_at": provenance["approved_at"],
            "recast_revision": state["revision"],
        }
        assert world.library.get(session, "picture")["provenance"] == []
    with pytest.raises(ApplicationError) as exc:
        edit(world, stale, recast.reject_reference, cid)
    assert exc.value.status == 409
    state = run(world, state)
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        assert (
            world.ops.operation(session, world.calls["replacement"][1])["request"]["facts"][
                "performers"
            ][0]["target_asset"]
            == sheet_id
        )
    state = edit(world, state, recast.set_target, cid, asset_id="picture", label="New label")
    assert state["characters"][0]["target"]["reference"]["state"] == "approved"
    state = edit(
        world, state, recast.set_target, cid, asset_id="picture2", label="Different picture"
    )
    assert state["characters"][0]["target"]["reference"] is None
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        assert stored["characters"][0]["reference_asset_id"] is None
        assert stored["shots"][2]["plan"]["_bindings"][0]["target_asset"] == "picture2"


@pytest.mark.parametrize("kind", ["face_portraits", "face_sheet"])
@pytest.mark.parametrize("failure", ["failed", "cancelled"])
def test_face_reference_failure_is_terminal(world, kind, failure):
    state = world.ready()
    world.invoke(recast.feed)
    if kind == "face_sheet":
        settle_reference(world, "face_portraits")
        world.invoke(recast.feed)
    settle_reference(world, kind, failure)
    world.invoke(recast.feed)
    world.invoke(recast.feed)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["characters"][0]["target"]["reference"]["state"] == "failed"
    assert state["run"]["renders_used"] == 0
    with world.db.transaction() as session:
        assert len(world.ops.recent(session, kind="face_portraits")) == 1


@pytest.mark.parametrize("report_state", ["no_face", "checking", "proposed"])
def test_face_reference_feed_requires_confirmed_human(world, report_state):
    state = world.create()
    command = recast.propose_target if report_state == "proposed" else recast.set_target
    state = edit(
        world, state, command, state["characters"][0]["id"], asset_id="picture", label="Target"
    )
    if report_state != "checking":
        world.settle_target(scores=(0.1,) if report_state == "no_face" else (0.9,))
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        assert world.ops.recent(session, kind="face_portraits") == []


def test_reference_rejection_and_review_conflicts(world):
    from gaiavfx.application.recast_planning import effective_target

    state = world.ready()
    cid = state["characters"][0]["id"]
    for command in (recast.approve_reference, recast.reject_reference):
        for review_state in (None, "making", "approved", "rejected", "failed"):
            with world.db.transaction() as session:
                stored = p.store.load(session, state["id"])
                stored["characters"][0]["reference_state"] = review_state
                stored["characters"][0]["reference_asset_id"] = (
                    "picture2" if review_state == "approved" else None
                )
                p.store.save(session, stored)
            with pytest.raises(ApplicationError) as exc:
                edit(world, state, command, cid)
            assert exc.value.status == 409
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        stored["characters"][0].update(reference_state=None, reference_asset_id=None)
        p.store.save(session, stored)
    state, _, sheet = waiting_reference(world, state)
    state = edit(world, state, recast.reject_reference, cid)
    assert state["characters"][0]["target"]["reference"]["state"] == "rejected"
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        assert effective_target(stored["characters"][0]) == "picture"
        assert (
            world.library.get(session, sheet["candidates"][0]["asset_id"])["provenance"][0]["type"]
            == "generation_candidate"
        )
    world.invoke(recast.feed)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["characters"][0]["target"]["reference"]["state"] == "rejected"
    state = world.invoke(recast.clear_target, state["id"], cid, state["revision"])
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        assert stored["characters"][0]["reference_asset_id"] is None
        assert stored["characters"][0]["reference_state"] is None


def fail_reference(world, kind, failure):
    with world.db.transaction() as session:
        operation = world.ops.recent(session, kind=kind)[0]
        if failure == "cancelled":
            world.ops.request_cancel(session, operation["id"])
        if failure == "partial":
            job = operation["jobs"][0]
            for state in ("claimed", "running", "collecting", "succeeded"):
                world.ops.transition(session, job["id"], state)
        world.settle(session, operation["id"], "failed" if failure == "partial" else failure)
        result = world.ops.operation(session, operation["id"])
        assert result["state"] == failure
        return result


@pytest.mark.parametrize("command", [recast.set_target, recast.confirm_target])
@pytest.mark.parametrize(
    "kind,failure",
    [
        ("face_portraits", "partial"),
        ("face_portraits", "failed"),
        ("face_portraits", "cancelled"),
        ("face_portraits", "needs_attention"),
        ("face_sheet", "failed"),
        ("face_sheet", "cancelled"),
        ("face_sheet", "needs_attention"),
    ],
)
def test_reference_retry_requires_human_mapping_and_keeps_seed(world, command, kind, failure):
    state = world.ready()
    cid = state["characters"][0]["id"]
    world.invoke(recast.feed)
    if kind == "face_sheet":
        settle_reference(world, "face_portraits")
        world.invoke(recast.feed)
    fail_reference(world, kind, failure)
    world.invoke(recast.feed)
    world.invoke(recast.feed)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["characters"][0]["target"]["reference"]["state"] == "failed"
    with world.db.transaction() as session:
        old = world.ops.recent(session, kind="face_portraits")
        assert len(old) == 1
    args = {"asset_id": "picture", "label": "Target"} if command == recast.set_target else {}
    state = edit(world, state, command, cid, **args)
    assert state["characters"][0]["target"]["reference"] == {"state": "making", "asset_id": None}
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        attempts = world.ops.recent(session, kind="face_portraits")
        assert len(attempts) == 2
        latest = next(o for o in attempts if o["request"]["attempt"] == 2)
        assert latest["request"]["seed"] == old[0]["request"]["seed"]
        assert latest["request_key"] != old[0]["request_key"]
        assert [j["seed"] for j in latest["jobs"]] == [j["seed"] for j in old[0]["jobs"]]
    state, _, sheet = waiting_reference(world, state)
    state = edit(world, state, recast.approve_reference, cid)
    with world.db.transaction() as session:
        provenance = world.library.get(session, sheet["candidates"][0]["asset_id"])["provenance"][
            -1
        ]
        assert provenance["portraits_operation"] == latest["id"]


def test_reference_start_exception_rolls_back_without_pausing_run(world, monkeypatch, caplog):
    from gaiavfx.application import recast_feed

    state = run(world, world.ready())
    world.invoke(recast.feed)
    settle_reference(world, "face_portraits")
    start = recast_feed.start_face_sheet

    def unavailable(*args, **kwargs):
        start(*args, **kwargs)
        raise ApplicationError("Cannot start sheet", 503)

    monkeypatch.setattr(recast_feed, "start_face_sheet", unavailable)
    world.invoke(recast.feed)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["run"]["state"] == "running" and state["run"]["reason"] is None
    assert state["characters"][0]["target"]["reference"]["state"] == "failed"
    with world.db.transaction() as session:
        assert world.ops.recent(session, kind="face_sheet") == []
    assert "Target face sheet failed" in caplog.text


def test_hidden_character_does_not_make_sheet(world):
    state = world.ready()
    cid = state["characters"][0]["id"]
    state = edit(world, state, recast.patch_character, cid, hidden=True)
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        assert world.ops.recent(session, kind="face_portraits") == []
        character = p.store.load(session, state["id"])["characters"][0]
        assert character["reference_asset_id"] is None and character["reference_state"] is None


def test_reference_review_states_do_not_replan_without_effective_target_change(world):
    state = world.ready()
    planned = len(world.calls["plan"])
    for reference_state in ("making", "waiting", "rejected", "failed"):
        with world.db.transaction() as session:
            stored = p.store.load(session, state["id"])
            stored["characters"][0].update(
                reference_asset_id="picture2", reference_state=reference_state
            )
            p.store.save(session, stored)
        world.invoke(recast.recast_state, state["id"])
        assert len(world.calls["plan"]) == planned


@pytest.mark.parametrize("kind", ["face_portraits", "face_sheet"])
@pytest.mark.parametrize("clear", [False, True])
def test_target_change_cancels_unsettled_reference_operations(world, kind, clear):
    state = world.ready()
    cid = state["characters"][0]["id"]
    world.invoke(recast.feed)
    if kind == "face_sheet":
        settle_reference(world, "face_portraits")
        world.invoke(recast.feed)
    if clear:
        world.invoke(recast.clear_target, state["id"], cid, state["revision"])
    else:
        edit(world, state, recast.set_target, cid, asset_id="picture2", label="Other")
    with world.db.transaction() as session:
        assert world.ops.recent(session, kind=kind)[0]["cancel_requested"]


def test_target_round_trip_brings_completed_sheet_back_for_review(world):
    state, _, sheet = waiting_reference(world, world.ready())
    cid = state["characters"][0]["id"]
    state = edit(world, state, recast.approve_reference, cid)
    state = edit(world, state, recast.set_target, cid, asset_id="picture2", label="Other")
    state = edit(world, state, recast.set_target, cid, asset_id="picture", label="Original")
    world.invoke(recast.feed)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["characters"][0]["target"]["reference"] == {
        "asset_id": sheet["candidates"][0]["asset_id"],
        "state": "waiting",
    }
    with world.db.transaction() as session:
        assert len(world.ops.recent(session, kind="face_portraits")) == 1


def test_recasts_sharing_a_picture_make_independent_sheets(world):
    world.ready("first")
    world.ready("second")
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        operations = world.ops.recent(session, kind="face_portraits")
        assert len(operations) == 2
        assert {o["request"]["target_asset"] for o in operations} == {"picture"}
        assert len({o["request_key"] for o in operations}) == 2


@pytest.mark.parametrize("command", [recast.approve_reference, recast.reject_reference])
def test_reference_review_requires_confirmed_target(world, command):
    state, _, _ = waiting_reference(world, world.ready())
    cid = state["characters"][0]["id"]
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        stored["characters"][0]["target_state"] = "proposed"
        p.store.save(session, stored)
    with pytest.raises(ApplicationError) as exc:
        edit(world, state, command, cid)
    assert exc.value.status == 409


def test_head_layer_over_bridges_reserves_and_assembles_same_structure(world):
    state = drain(world, run(world, long_shots(world), budget=10)["id"])
    shot = state["shots"][0]
    parent = world.invoke(recast.reveal_shot, state["id"], shot["id"])["versions"][0]
    assert len(parent["bridges"]) == 2
    state = edit(
        world,
        state,
        recast.start_head_pass,
        shot["id"],
        version=parent["id"],
        character_id=shot["plan"]["performers"][0],
    )
    assert state["run"]["renders_used"] == 10
    assert state["shots"][0]["layers"][0]["windows_total"] == 5
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        layer = p.store.load(session, state["id"])["shots"][0]["layers"][0]
        assembly = world.ops.operation(session, layer["assembly_op"])
        assert len(assembly["request"]["window_operations"]) == 3
        assert len(assembly["request"]["bridge_operations"]) == 2
        assert assembly["request"]["handoff"]["joins"] == [
            {**j, "kind": j["handoff"]} for j in parent["joins"]
        ]
    settle_all(world, "shot_assembly")
    revealed = world.invoke(recast.reveal_shot, state["id"], shot["id"])["versions"][-1]
    assert revealed["layer"] == "head" and len(revealed["bridges"]) == 2


@pytest.mark.parametrize("validation", [False, True])
def test_bridge_start_failure_becomes_crossfade_and_run_continues(world, monkeypatch, validation):
    from gaiavfx.application.replace_character import ReplacementPlanningError
    from gaiavfx.modules.direction.replacement import Gap

    error = (
        ApplicationError("Invalid frozen bridge: " + "x" * 600, 422)
        if validation
        else ReplacementPlanningError((Gap("bridge_facts_unresolved", "Performer absent"),))
    )

    def unresolved(*args, **kwargs):
        raise error

    monkeypatch.setattr(world.window_api, "start_shot_bridge", unresolved)
    state = run(world, long_shots(world, count=3), budget=15)
    state = drain(world, state["id"])
    assert state["run"]["renders_used"] == 9
    assert state["run"]["state"] == "finished"
    assert all(s["status"] == "needs_review" for s in state["shots"])
    assert len(world.calls["assembly"]) == 3
    with world.db.transaction() as session:
        for shot in p.store.load(session, state["id"])["shots"]:
            joins = shot["plan"]["_handoff"]["joins"]
            assert all(
                j["kind"] == "crossfade"
                and j["reason"] == "bridge_start_failed"
                and j["detail"] == str(error)[:500]
                for j in joins
            )
            assembly = world.ops.operation(session, shot["assembly_op"])
            assert assembly["request"]["handoff"]["joins"] == joins
            assert not assembly["request"]["bridge_operations"]


def test_bridge_reservation_overrun_still_pauses_run(world, monkeypatch):
    original = world.window_api.start_shot_bridge

    def overrun(*args, **kwargs):
        return {**original(*args, **kwargs), "budget_reserved": 2}

    monkeypatch.setattr(world.window_api, "start_shot_bridge", overrun)
    state = run(world, long_shots(world), budget=8)
    world.invoke(recast.feed)
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["run"]["state"] == "paused"
    assert "Bridge exceeded its shot reservation" in state["run"]["reason"]
    assert not world.calls["assembly"]
    with world.db.transaction() as session:
        shot = p.store.load(session, state["id"])["shots"][0]
        assert not shot["bridge_ops"]
        assert all(j["kind"] == "bridge" for j in shot["plan"]["_handoff"]["joins"])
