"""ADR 0020 §8: Fill more's authority, decisions, waiting state and settled spend.

The shared scene drives real transactions and CPU stages with fake inference only.
"""

import pytest
from test_harness_build import build, context, drain, operation_count, planned
from test_set_seed import operation
from test_set_seed import scene as scene  # noqa: PLC0414

from gaiavfx.api.agent import HarnessSessionDTO
from gaiavfx.application import set_fill, sets
from gaiavfx.application.harness.commands import jobs_reserved
from gaiavfx.application.harness.loop import ended_turn
from gaiavfx.application.harness.tools.plan_costs import step_jobs_left
from gaiavfx.application.harness.tools.sets import BuildSetFromTake, FillSetMore
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError


def stage(s, *, accept=True):
    h, row, document = planned(s, "build")
    started = build(h, row, document, shot=0, fill_unseen=False)
    drain(s.run)
    set_id = operation(s.workspace, started.waits_on)["review"][-1]["set_id"]
    with s.workspace.db.transaction() as session:
        version = sets.STORE.get(session, set_id)["versions"][0]
        if accept:
            sets.accept_version(
                session, s.workspace.library, set_id, version["id"], expected_current_id=None
            )
    return set_id, version


def fill_plan(s, set_id):
    h, row, document = planned(s, "build", "answer")
    document.plan.steps[0].target = set_id
    return h, row, document


def fill(h, row, document, set_id):
    return FillSetMore().run(context(h, row, document), FillSetMore.Input(set_id=set_id))


@pytest.mark.parametrize("decision", ["accept", "discard"])
def test_fill_chat_waits_for_human_and_decision_preserves_the_right_current_version(
    scene, decision
):
    s = scene
    set_id, v1 = stage(s)
    h, row, document = fill_plan(s, set_id)
    started = fill(h, row, document, set_id)
    assert started.ok, started
    document.plan.steps[0].receipts.extend(started.receipts)
    document.waiting_on = started.waits_on

    def payload(state):
        result = h.payload({**row, "state": state, "document": document.model_dump()})
        HarnessSessionDTO.model_validate(result)
        return result

    running = payload("waiting_operation")
    assert running["activity"] == "building_stage"
    assert running["build"]["state"] == "building"
    assert running["build"]["set_id"] == set_id
    assert running["build"]["shot"] is None
    assert running["build"]["note"] == (
        "Gaia answers when the build ends: Gaia's model and the build share the GPU."
    )
    drain(s.run)
    with s.workspace.db.transaction() as session:
        built = operation(s.workspace, started.waits_on)
        h._link_review(document, built, session)
        ended = ended_turn(session, built)
        assert not h._complete_reviews(session, row, document)
        state = sets.STORE.get(session, set_id)
    v2 = state["versions"][-1]
    assert state["current_version_id"] == v1["id"]
    assert v2["parent_id"] == v1["id"] and v2["state"] == "proposed"
    assert "user's decision" in ended["action"]["message"]
    assert "waits for you" in ended["action"]["notice"]
    assert started.waits_on not in ended["action"]["message"]
    document.waiting_on = ""
    waiting = payload("waiting_human")
    assert waiting["build"]["waiting"] and waiting["build"]["note"] == ""
    with s.workspace.db.transaction() as session:
        if decision == "accept":
            sets.accept_version(
                session, s.workspace.library, set_id, v2["id"], expected_current_id=v1["id"]
            )
        else:
            sets.discard_version(session, s.workspace.library, set_id, v2["id"])
        assert h._complete_reviews(session, row, document)
        state = sets.STORE.get(session, set_id)
        # A repeated poll must not append a second decision turn.
        turns = len(h.store.turns(session, row["id"]))
        assert not h._complete_reviews(session, row, document)
        assert len(h.store.turns(session, row["id"])) == turns
    assert state["current_version_id"] == (v2 if decision == "accept" else v1)["id"]
    assert document.plan.steps[0].status == ("done" if decision == "accept" else "skipped")
    decided = payload("waiting_human")["build"]
    assert not decided["waiting"] and decided["note"] == ""
    assert decided["label"] == f"v2 {decision}ed"


@pytest.mark.parametrize(
    "case", ["unaccepted", "outside_step", "scene_target", "stage_target", "running"]
)
def test_fill_refusals_do_not_queue_fixes_or_allocate_another_operation(scene, case):
    s = scene
    set_id, _ = stage(s, accept=case != "unaccepted")
    h, row, document = fill_plan(s, set_id)
    if case == "outside_step":
        document.plan = None
        document.plan_mode = False
    elif case == "scene_target":
        document.plan.steps[0].target = s.segment["id"]
    elif case == "running":
        assert fill(h, row, document, set_id).ok
        # Another conversation must also be refused, not just the step that spent its budget.
        h, row, document = fill_plan(s, set_id)
    before = operation_count(s)
    with s.workspace.db.transaction() as session:
        fixes = sets.STORE.fixes(session, set_id)
    if case == "stage_target":
        result = BuildSetFromTake().run(
            context(h, row, document), BuildSetFromTake.Input(segment_id=s.segment["id"])
        )
    else:
        result = fill(h, row, document, set_id)
    assert not result.ok
    assert {
        "unaccepted": "no accepted version",
        "outside_step": "approved plan",
        "scene_target": "step_target_mismatch",
        "stage_target": "step_target_mismatch",
        "running": "already running",
    }[case] in result.discrepancy
    assert operation_count(s) == before
    with s.workspace.db.transaction() as session:
        assert sets.STORE.fixes(session, set_id) == fixes


@pytest.mark.parametrize("failed_probe", [False, True])
def test_stopped_fill_settles_allocated_probes_in_step_and_conversation(scene, failed_probe):
    s = scene
    set_id, v1 = stage(s)
    h, row, document = fill_plan(s, set_id)
    if failed_probe:
        s.comfy.fail_graph = lambda graph: any(
            node["class_type"] == "MiniMaxH3FunControlNetApply" for node in graph.values()
        )
    started = fill(h, row, document, set_id)
    assert started.ok, started
    document.plan.steps[0].receipts.extend(started.receipts)
    for _ in range(400):
        op = operation(s.workspace, started.waits_on)
        probes = [j for j in op["jobs"] if set_fill.stage(j).get("name") == "probe"]
        if probes and (not failed_probe or any(j["state"] == "failed" for j in probes)):
            break
        assert s.run.run_once(), op
    else:
        pytest.fail("Fill never allocated a probe")
    with s.workspace.db.transaction() as session:
        OperationService().request_cancel(session, started.waits_on)
    drain(s.run)
    stopped = operation(s.workspace, started.waits_on)
    used = set_fill.renders_used(stopped)
    assert 0 < used < 6
    with s.workspace.db.transaction() as session:
        h._link_review(document, stopped, session)
        ended = ended_turn(session, stopped)
        assert sets.STORE.get(session, set_id)["current_version_id"] == v1["id"]
    assert document.plan.steps[0].status == "pending"
    # The user stopped it, so the agent asks before filling again.
    assert ended["action"]["message"].endswith(
        f"after {used} of its 6 renders: the user stopped it. The build step stays open: ask the user before you "
        "fill again."
    )
    assert step_jobs_left(context(h, row, document)) == 6 - used
    reserved = {"state": "accepted", "receipts": started.receipts}
    settled = {"state": "accepted", "receipts": ended["receipts"]}
    assert jobs_reserved([reserved, settled, reserved, settled]) == used
    retry = fill(h, row, document, set_id)
    assert not retry.ok and "step_budget_exhausted" in retry.discrepancy


@pytest.mark.parametrize("action", ["keep", "retract", "reshoot", "extend"])
def test_any_queued_fix_is_the_users_to_run_not_the_agents(scene, action):
    s = scene
    set_id, _ = stage(s)
    # Queue through the owner: rejection must precede interpreting or running ANY existing fix.
    with s.workspace.db.transaction() as session:
        fix_id = sets.STORE.queue_fix(
            session,
            set_id,
            action=action,
            author="human",
            target={},
            shows="Keep my direction",
            issue_id=None,
        )
    h, row, document = fill_plan(s, set_id)
    before = operation_count(s)
    refused = fill(h, row, document, set_id)
    assert not refused.ok and "run or cancel them first" in refused.discrepancy
    assert operation_count(s) == before
    with s.workspace.db.transaction() as session:
        fixes = sets.STORE.fixes(session, set_id)
    assert [(f["id"], f["state"], f["author"]) for f in fixes] == [(fix_id, "queued", "human")]


def test_failed_fill_start_rolls_back_the_agents_extension(scene, monkeypatch):
    from gaiavfx.application import set_build

    s = scene
    set_id, _ = stage(s)
    h, row, document = fill_plan(s, set_id)
    before = operation_count(s)

    def failed_start(session, library, actual_set_id, *, base_version_id):
        # The queueing happened in this transaction, before start failed.
        fixes = sets.STORE.fixes(session, actual_set_id)
        assert [(f["action"], f["author"]) for f in fixes] == [("extend", "agent")]
        raise ApplicationError("The base atlas is no longer available.", 409)

    monkeypatch.setattr(set_build, "start_set_build", failed_start)
    refused = fill(h, row, document, set_id)
    assert not refused.ok and "base atlas" in refused.discrepancy
    assert operation_count(s) == before
    with s.workspace.db.transaction() as session:
        assert sets.STORE.fixes(session, set_id) == []


def test_agent_fills_the_accepted_version_even_when_a_newer_proposal_exists(scene):
    s = scene
    set_id, v1 = stage(s)
    with s.workspace.db.transaction() as session:
        proposed_id = sets.STORE.add_version(
            session,
            set_id,
            origin="human",
            parent_id=v1["id"],
            atlas_artifact_id=v1["atlas_artifact_id"],
            keyframe_ids=[k["id"] for k in sets.STORE.keyframes(session, v1["id"])],
        )
    h, row, document = fill_plan(s, set_id)
    started = fill(h, row, document, set_id)
    assert started.ok, started
    op = operation(s.workspace, started.waits_on)
    assert op["request"]["base_version_id"] == v1["id"] != proposed_id
    assert op["budget_reserved"] == 6


def test_waiting_fill_does_not_ask_the_model_or_create_a_reply(scene):
    s = scene
    set_id, _ = stage(s)
    h, row, document = fill_plan(s, set_id)
    started = fill(h, row, document, set_id)
    assert started.ok, started
    document.waiting_on = started.waits_on
    document.plan.steps[0].receipts.extend(started.receipts)

    class NoModelCalls:
        def __getattr__(self, name):
            pytest.fail(f"The waiting conversation accessed the model: {name}")

    h.model = NoModelCalls()
    with s.workspace.db.transaction() as session:
        h.store.update_session(
            session, row["id"], row["revision"], state="running", document=document.model_dump()
        )
    before = h.turns(row["id"])
    result = h.advance(row["id"])
    assert result["state"] == "waiting_operation"
    assert result["document"]["waiting_on"] == started.waits_on
    assert h.turns(row["id"]) == before


def test_a_plan_may_fill_a_stage_but_may_not_build_twice(scene):
    from gaiavfx.application.harness.tools.plan import ProposePlan

    s = scene
    set_id, _ = stage(s)
    h, row, document = fill_plan(s, set_id)
    document.plan = None
    steps = [
        {"kind": "build", "title": "Fill more of the room", "target": set_id, "jobs": 6},
        {"kind": "build", "title": "Build another stage", "target": s.segment["id"], "jobs": 6},
    ]
    result = ProposePlan().run(
        context(h, row, document), ProposePlan.Input(goal="More of the room", steps=steps)
    )
    assert not result.ok and "one_build" in result.discrepancy
    assert document.plan is None
    allowed = ProposePlan().run(
        context(h, row, document), ProposePlan.Input(goal="More of the room", steps=steps[:1])
    )
    assert allowed.ok, allowed
    assert document.plan.steps[0].target == set_id
    assert document.plan.budget_jobs == 6


def test_a_build_step_without_a_target_is_told_the_stage_and_the_scene_apart(scene):
    # stage-local-a4: the model left the target out three times when one list mixed scene and stage ids.
    from gaiavfx.application.harness.tools.plan import ProposePlan

    s = scene
    set_id, _ = stage(s)
    h, row, document = fill_plan(s, set_id)
    document.plan = None
    refused = ProposePlan().run(
        context(h, row, document),
        ProposePlan.Input(
            goal="More of the room",
            steps=[{"kind": "build", "title": "Work on the room", "jobs": 6}],
        ),
    )
    assert not refused.ok
    assert refused.discrepancy.startswith(
        f"build_target: Give the build step a target: the id of the stage it fills more of ({set_id}), or of the "
        "segment whose selected take it builds a new stage from (segment 1 "
    )
    # A fill's title with one accepted stage to fill needs no target.
    filled = ProposePlan().run(
        context(h, row, document),
        ProposePlan.Input(
            goal="More of the room",
            steps=[{"kind": "build", "title": "Fill more of the room", "jobs": 6}],
        ),
    )
    assert filled.ok, filled
    assert document.plan.steps[0].target == set_id
