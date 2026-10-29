"""ADR 0020 failure recovery through the existing CPU worker and fake ComfyUI."""

import sys

from set_scene import FAKE
from test_set_seed import OPERATIONS, operation, transact
from test_set_seed import scene as scene  # noqa: PLC0414
from test_set_seed_fill import drain, stages, start

from gaiavfx.application import set_fill, set_seed, sets
from gaiavfx.application.engine_jobs import EngineCommand
from gaiavfx.modules.generation.operations.persistence import OperationRecord


def until(s, operation_id, predicate):
    for _ in range(400):
        op = operation(s.workspace, operation_id)
        if predicate(op):
            return op
        assert s.run.run_once(), "runner stopped before the requested failure boundary"
    raise AssertionError("runner did not reach the requested failure boundary")


def built(s, operation_id):
    op = operation(s.workspace, operation_id)
    assert op["review"][-1]["stage"] == "done", op["review"]
    with s.workspace.db.transaction() as session:
        stage = sets.STORE.get(session, op["review"][-1]["set_id"])
    assert stage["versions"][0]["state"] == "proposed"
    assert stage["current_version_id"] is None
    return op, stage


def output_id(job, key):
    return next(o["artifact_id"] for o in job["outputs"] if o["output_key"] == key)


def test_failed_second_extension_writes_only_the_first_round_checkpoint(scene):
    s = scene
    started = start(s)
    until(
        s,
        started["id"],
        lambda op: any(
            set_fill.stage(j)["round"] == 2 and j["state"] == "queued" for j in stages(op, "extend")
        ),
    )
    s.run.engines[set_fill.EXTEND] = EngineCommand(
        (sys.executable, str(FAKE), "--crash", "splat_extend")
    )
    drain(s.run)
    op, stage = built(s, started["id"])
    first, newer = stages(op, "consolidate")
    first_fit, failed_fit = stages(op, "extend")
    assert newer["state"] == "succeeded" and failed_fit["state"] == "failed"
    version = stage["versions"][0]
    assert version["atlas_artifact_id"] == output_id(first, "atlas")
    assert version["atlas_artifact_id"] != output_id(newer, "atlas")
    assert version["map_artifact_id"] == output_id(first, "map")
    assert version["splat_artifact_id"] == output_id(first_fit, "splat")
    assert stage["provenance"]["fill"]["rounds"] == 1
    assert "stopped filling in round 2: its splat extension failed" in version["note"]


def test_one_failed_still_geometry_does_not_discard_the_other_probes(scene):
    s = scene
    s.comfy.fail_graph = lambda graph: (
        any(n["class_type"] == "GaiaSaveStillGeometry" for n in graph.values())
        and "/r1/p0-still.png" in graph["still"]["inputs"]["image"]
    )
    started = start(s)
    drain(s.run)
    op, stage = built(s, started["id"])
    geometries = [j for j in stages(op, "still_geometry") if set_fill.stage(j)["round"] == 1]
    assert len(geometries) > 1
    assert [set_fill.stage(j)["probe"] for j in geometries if j["state"] == "failed"] == [0]
    consolidated = stages(op, "consolidate")[0]
    assert consolidated["state"] == "succeeded"
    inputs = consolidated["inputs"]
    assert "geometry:0" not in inputs
    assert any(key.startswith("geometry:") for key in inputs)
    assert stages(op, "extend")[0]["state"] == "succeeded"
    assert stage["provenance"]["fill"]["rounds"] >= 1


def test_retry_of_a_request_recorded_before_fill_existed_stays_off(scene):
    s = scene
    s.comfy.execution_error = True
    started = start(s, shot=0, fill_unseen=False)
    drain(s.run)
    # Reproduce the historical persisted request shape, without changing today's defaults.
    with s.workspace.db.transaction() as session:
        row = session.get(OperationRecord, started["id"])
        row.request = {key: value for key, value in row.request.items() if key != "fill"}
    s.comfy.execution_error = False
    again = transact(s.workspace, set_seed.retry_seed, s.workspace.library, started["id"])
    assert again["budget_reserved"] == 0 and again["request"]["fill"] is None
    drain(s.run)
    op, stage = built(s, again["id"])
    assert not stages(op, "explore") and not stages(op, "probe")
    assert stage["provenance"]["layout"] == "moving"
    assert stage["provenance"]["fill"] is None


def test_uncertain_probe_stops_the_build_and_retry_keeps_the_frozen_fill(scene):
    s = scene
    started = start(s)
    op = until(s, started["id"], lambda op: bool(stages(op, "probe")))
    probe = stages(op, "probe")[0]
    # An accepted remote submission whose outcome cannot be recovered: use real lifecycle transitions.
    with s.workspace.db.transaction() as session:
        OPERATIONS.transition(session, probe["id"], "claimed")
        OPERATIONS.transition(session, probe["id"], "submitting")
        OPERATIONS.transition(
            session, probe["id"], "needs_attention", error="Unknown remote outcome"
        )
    drain(s.run)
    op = operation(s.workspace, started["id"])
    state = transact(s.workspace, set_seed.seed_state, op)
    assert (state["state"], state["can_retry"]) == ("failed", True)
    assert not any(r.get("set_id") for r in op["review"])
    assert not stages(op, "stills")
    again = transact(s.workspace, set_seed.retry_seed, s.workspace.library, started["id"])
    assert again["request"]["fill"] == started["request"]["fill"]
    assert again["budget_reserved"] == started["budget_reserved"] == 6
