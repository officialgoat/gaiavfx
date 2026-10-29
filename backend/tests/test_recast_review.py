"""Cross-family review regressions, exercising real persistence without inference."""

import inspect
import sys
from copy import deepcopy
from types import ModuleType

import pytest
from test_recast_feed import drain, run, settle_all
from test_recast_service import edit
from test_recast_service import recast_template as recast_template  # noqa: PLC0414
from test_recast_service import world as world  # noqa: PLC0414

from gaiavfx.application import operations, recast, replace_character
from gaiavfx.application import recast_planning as planning
from gaiavfx.application import recast_projection as p
from gaiavfx.shared.errors import ApplicationError


@pytest.mark.parametrize("identity", [{}, {"t2": 3}, {"t2": 1, "t3": 1}, {"t4": 3}])
def test_unmapped_and_faceless_people_are_bystanders(world, identity):
    for t in world.report["tracks"][1:]:
        t.update(shot=1, frames=[0, 5, 10])
    state = world.create()
    for track in ("t2", "t3"):
        state = edit(world, state, recast.move_tracks, track_ids=[track], to="new")
    state = edit(world, state, recast.move_tracks, track_ids=["t4"], to="unknown")
    state = edit(
        world,
        state,
        recast.set_target,
        state["characters"][0]["id"],
        asset_id="picture",
        label="Lead",
    )
    state = edit(world, state, recast.plan_recast, range=None)
    analysis = world.calls["analysis"][0]
    person = deepcopy(world.window_reports[analysis]["tracks"][0])
    person.update(id="bystander", identity={"film_tracks": identity, "margin": 0.5})
    world.window_reports[analysis]["tracks"] = [world.window_reports[analysis]["tracks"][0], person]
    world.touch_window(analysis)
    state = world.invoke(recast.recast_state, state["id"])
    shot = state["shots"][0]
    assert shot["status"] == "planned" and shot["plan"]["gaps"] == []
    assert all(
        not a["confirmed"]
        for a in shot["appearances"]
        if a["character_id"] != state["characters"][0]["id"]
    )


@pytest.mark.parametrize("failure", ["planner", "report", "analyses", "missing"])
def test_planning_failures_are_cached_gaps_and_shot_can_be_excluded(world, monkeypatch, failure):
    calls = []
    working_planner = replace_character.replacement_plan

    def broken(*args, **kwargs):
        calls.append(failure)
        raise ApplicationError("Fixture planning failure", 422)

    if failure == "planner":
        monkeypatch.setattr(replace_character, "replacement_plan", broken)
    elif failure == "report":
        monkeypatch.setattr(p, "window_report", broken)
    elif failure == "analyses":
        original = world.window_api.start_window_analyses

        def partial(session, library, **kwargs):
            original(session, library, **kwargs)
            broken()

        monkeypatch.setattr(world.window_api, "start_window_analyses", partial)
    else:
        # W is now real: simulate a missing integration function at the lookup itself.
        lookup = p.window_function

        def missing(name):
            if name == "shot_windows":
                raise ApplicationError(
                    "Window integration function shot_windows is not available.", 409
                )
            return lookup(name)

        monkeypatch.setattr(p, "window_function", missing)
    state = world.ready()
    assert state["shots"][0]["plan"]["gaps"][0]["code"] == "planning_error"
    if failure == "analyses":
        with world.db.transaction() as session:
            assert world.ops.recent(session, kind="person_analysis") == []
    count = len(calls)
    world.invoke(recast.recast_state, state["id"])
    if failure == "planner":
        assert len(calls) == count
    state = edit(world, state, recast.patch_shot, "s0001", included=False)
    assert state["shots"][0]["status"] == "excluded"
    state = edit(
        world, state, recast.patch_character, state["characters"][0]["id"], label="Still editable"
    )
    assert state["characters"][0]["label"] == "Still editable"
    if failure == "planner":
        monkeypatch.setattr(replace_character, "replacement_plan", working_planner)
        state = edit(world, state, recast.plan_recast, range=None)
        assert state["shots"][1]["status"] == "planned"


def test_feed_savepoint_isolates_failure_and_pauses_without_retry(world, monkeypatch):
    good = run(world, world.ready("good"))
    bad = run(world, world.ready("bad"))
    original = world.window_api.start_shot_replacement
    attempted = []
    bad_key = planning.key(bad["id"], "s0001", "replacement", 0)

    def start(session, library, **kwargs):
        result = original(session, library, **kwargs)
        if kwargs["request_key"] == bad_key:
            attempted.extend(o["id"] for o in result)
            raise ApplicationError("One recast failed", 422)
        return result

    monkeypatch.setattr(world.window_api, "start_shot_replacement", start)
    world.invoke(recast.feed)
    world.invoke(recast.feed)
    failed = world.invoke(recast.recast_state, bad["id"])
    assert failed["run"]["state"] == "paused" and "One recast failed" in failed["run"]["reason"]
    assert len(attempted) == 1
    with world.db.transaction() as session:
        ids = {o["id"] for o in world.ops.recent(session, kind="character_replacement")}
        assert ids.isdisjoint(attempted) and len(ids) == 2
    assert world.invoke(recast.recast_state, good["id"])["run"]["state"] == "running"


@pytest.mark.parametrize("running", [False, True])
def test_cancel_resume_render_assemble(world, running):
    state = run(world, world.ready(), frame_range={"start": 0, "end": 73})
    world.invoke(recast.feed)
    cancelled = world.calls["replacement"][0]
    with world.db.transaction() as session:
        job = world.ops.operation(session, cancelled)["jobs"][0]
        if running:
            world.ops.transition(session, job["id"], "claimed")
            world.ops.transition(session, job["id"], "running")
    state = edit(world, state, recast.cancel_run)
    state = run(world, state, frame_range={"start": 0, "end": 73})
    world.invoke(recast.feed)
    if running:
        assert len(world.calls["replacement"]) == 1
        with world.db.transaction() as session:
            assert world.ops.operation(session, cancelled)["cancel_requested"]
            world.ops.transition(session, job["id"], "cancelled")
    state = drain(world, state["id"])
    assert len(world.calls["replacement"]) == 2
    assert len(world.calls["assembly"]) == 1
    assert state["shots"][0]["status"] == "needs_review"
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])["shots"][0]
        assert stored["plan"]["_history"][0]["operations"] == [cancelled]
        assert stored["plan"]["_history"][0]["cancelled"]
        assembly = world.ops.operation(session, stored["assembly_op"])
        assert cancelled not in assembly["request"]["window_operations"]


def test_agent_cannot_replace_confirmed_mapping_and_authority_is_keyword_only(world):
    state = world.ready()
    cid = state["characters"][0]["id"]
    with pytest.raises(ApplicationError) as error:
        edit(world, state, recast.propose_target, cid, asset_id="picture2", label="Agent")
    assert error.value.status == 409
    assert (
        inspect.signature(recast._target).parameters["authority"].kind
        == inspect.Parameter.KEYWORD_ONLY
    )
    with pytest.raises(TypeError):
        world.invoke(
            recast._target, state["id"], cid, state["revision"], "picture2", "Agent", "agent"
        )
    assert (
        world.invoke(recast.recast_state, state["id"])["characters"][0]["target"]["asset_id"]
        == "picture"
    )


def test_reads_do_not_lock_or_save_and_cache_plan_and_face_index(world, monkeypatch, tmp_path):
    state = world.ready()
    plans = len(world.calls["plan"])
    world.report["tracks"][0]["crop_artifact"] = "crop"
    face = tmp_path / "crop.png"
    face.write_bytes(b"fixture")
    monkeypatch.setattr(world.library, "artifact_path", lambda session, artifact: face)

    def write(*args, **kwargs):
        pytest.fail("A read attempted to lock or save")

    monkeypatch.setattr(p.store, "lock", write)
    monkeypatch.setattr(p.store, "save", write)
    monkeypatch.setattr(recast, "start_target_report", write)
    monkeypatch.setattr(p.operations, "create", write)
    # One report update invalidates the unstarted plans, then reads reuse them.
    state = world.invoke(recast.recast_state, state["id"])
    assert state["characters"][0]["target"]["face"] == "face"
    assert len(world.calls["plan"]) > plans
    plans = len(world.calls["plan"])
    world.invoke(recast.recast_state, state["id"])
    world.invoke(recast.list_recasts)
    world.invoke(recast.reveal_shot, state["id"], "s0001")
    assert len(world.calls["plan"]) == plans
    reports = []
    original = world.film_api.film_analysis_report

    def report(*args):
        reports.append(True)
        return original(*args)

    monkeypatch.setattr(world.film_api, "film_analysis_report", report)
    assert world.invoke(recast.face_path, state["id"], "crop") == face
    assert world.invoke(recast.face_path, state["id"], "crop") == face
    assert len(reports) == 1


def test_cache_invalidation_mapping_overrides_and_policy(world, monkeypatch):
    state = world.ready()
    count = len(world.calls["plan"])
    state = edit(
        world,
        state,
        recast.set_target,
        state["characters"][0]["id"],
        asset_id="picture2",
        label="New",
    )
    assert len(world.calls["plan"]) > count
    count = len(world.calls["plan"])
    state = edit(
        world, state, recast.move_tracks, track_ids=["t1"], to=state["characters"][0]["id"]
    )
    assert len(world.calls["plan"]) > count
    count = len(world.calls["plan"])
    original = planning.replacement_policy_text()
    monkeypatch.setattr(
        planning, "replacement_policy_text", lambda: original + "\n# updated policy\n"
    )
    world.invoke(recast.recast_state, state["id"])
    assert len(world.calls["plan"]) > count


def test_assemblies_do_not_block_new_window_work(world):
    state = run(world, world.ready())
    assert all(w["state"] == "planned" for s in state["shots"] for w in s["plan"]["windows"])
    world.invoke(recast.feed)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["shots"][0]["plan"]["windows"][0]["state"] == "queued"
    assert state["shots"][0]["status"] == "queued"
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    assert len(world.calls["assembly"]) == 2
    assert len(world.calls["replacement"]) == 4


def test_busy_runner_calls_sweeps_on_timer_without_starving_jobs(world, monkeypatch):
    events, jobs, now = [], [], [100.0]
    monkeypatch.setattr(operations, "SWEEPS", [lambda session, *, library: events.append(now[0])])
    monkeypatch.setattr(operations.time, "monotonic", lambda: now[0])
    runner = operations.OperationRunner(world.db, None, world.library, "unused", sweep_seconds=5)
    monkeypatch.setattr(
        runner.service,
        "claim_next",
        lambda session: {
            "executor": "local",
            "state": "claimed",
            "step": "fixture",
            "operation_id": "fixture",
        },
    )
    monkeypatch.setattr(runner, "_local", lambda job: jobs.append(now[0]))
    monkeypatch.setattr(runner, "_advance", lambda operation_id: False)
    for moment in (100, 101, 105, 106, 111):
        now[0] = moment
        runner.run_once()
    assert events == [100, 105, 111]
    assert jobs == [100, 101, 105, 106, 111]


def test_lazy_lookup_across_modules_and_short_request_keys(world, monkeypatch):
    module = ModuleType("gaiavfx.application.shot_assembly")
    module.start_shot_assembly = world.window_api.start_shot_assembly
    monkeypatch.setitem(sys.modules, module.__name__, module)
    monkeypatch.delattr(world.window_api, "start_shot_assembly")
    assert p.window_function("start_shot_assembly") == module.start_shot_assembly
    assert len(planning.key("x" * 100, "shot" * 20, "replacement", 123456)) <= 12
    state = drain(world, run(world, world.ready())["id"])
    assert state["run"]["state"] == "finished"
    with world.db.transaction() as session:
        assert all(len(o["request_key"]) <= 80 for o in world.ops.recent(session))


def test_missing_feed_function_pauses_with_clear_reason(world, monkeypatch):
    state = run(world, world.ready())
    monkeypatch.delattr(world.window_api, "start_shot_replacement")
    world.invoke(recast.feed)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["run"]["state"] == "paused"
    assert "start_shot_replacement" in state["run"]["reason"]


def test_final_after_draft_accumulates_versions(world):
    selected = {"start": 0, "end": 73}
    state = drain(world, run(world, world.ready(), budget=5, frame_range=selected)["id"])
    assert [v["letter"] for v in state["shots"][0]["versions"]] == ["A"]
    state = run(world, state, budget=5, mode="final", frame_range=selected)
    assert state["run"]["shots_done"] == 0
    state = drain(world, state["id"])
    assert state["run"]["renders_used"] == 5
    assert [v["letter"] for v in state["shots"][0]["versions"]] == ["A", "B"]
    with world.db.transaction() as session:
        assert [
            world.ops.operation(session, o)["request"]["mode"] for o in world.calls["replacement"]
        ] == ["draft", "final"]


def test_real_replacement_plan_accepts_recast_request(world, monkeypatch):
    monkeypatch.setattr(replace_character, "replacement_plan", world.real_planner)
    state = world.ready()
    plan = state["shots"][0]["plan"]
    assert plan["gaps"] == []
    assert plan["canvas_scale"] > 0
    with world.db.transaction() as session:
        row = p.store.load(session, state["id"])
        shot = row["shots"][0]
        analysis = shot["window_analysis_ops"][0]
        request = planning.request_for(
            row,
            world.report["shots"][0],
            world.window_reports[analysis],
            analysis,
            shot["plan"]["_bindings"],
        )
        result = world.real_planner(session, request, library=world.library, renders=1)
    assert result["facts"]["performers"][0]["target_asset"] == "picture"
    assert result["arm"] and result["crop_preview"]["scale"] == plan["canvas_scale"]


def test_render_timing_uses_execution_fields_or_seed_when_unknown():
    operation = {
        "state": "succeeded",
        "jobs": [
            {
                "executor": "comfy",
                "kind": "video",
                "state": "succeeded",
                "started_at": "2026-10-08T00:01:00+00:00",
                "finished_at": "2026-10-08T00:03:00+00:00",
                "updated_at": "2026-10-08T00:04:00+00:00",
            }
        ],
    }
    assert p.render_seconds(operation) == 120
    del operation["jobs"][0]["started_at"]
    assert p.render_seconds(operation) is None


def test_cleared_mapping_does_not_feed_absent_shots(world):
    state = world.ready()
    state = edit(world, state, recast.clear_target, state["characters"][0]["id"])
    state = run(world, state)
    world.invoke(recast.feed)
    assert not world.calls["replacement"]
    assert world.invoke(recast.recast_state, state["id"])["run"]["state"] == "finished"
