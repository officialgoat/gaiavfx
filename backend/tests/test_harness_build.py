"""build_set_from_take (ADR 0019 §7; Fable and Astra, round 7): inside an approved plan's build step the agent builds
a set from one shot of a scene's take, as the user's Build set does, filling its unseen space unless it says not to
(ADR 0020 §7). The step stops for the user's decision on the new set's v1; a refused build ends it in the seed's
words; without a plan's grant there is no build."""

from itertools import count

from test_set_seed import operation
from test_set_seed import scene as scene  # noqa: PLC0414 - pytest fixture export

from gaiavfx.api.agent import HarnessSessionDTO
from gaiavfx.application import sets
from gaiavfx.application.harness.loop import Harness, ended_turn, ended_words
from gaiavfx.application.harness.session import AgentSession, Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.application.harness.tools.plan import PlanStepTool
from gaiavfx.application.harness.tools.sets import BuildSetFromTake
from gaiavfx.application.harness.view import render_view
from gaiavfx.application.set_seed import fill_renders
from gaiavfx.modules.agent.service import AgentStore

TURNS = count(1)
# The box room's take, as build_set_from_take numbers its shots: the first shot, then the shot after the cut at f12.
SHOTS = "shot 0 First shot 0:00–0:00; shot 1 Cut 1 (f12) 0:00–0:01"


def drain(run, rounds=400):
    # A build that fills runs its rounds: about two dozen jobs.
    for _ in range(rounds):
        if not run.run_once():
            break


def harness(s):
    s.workspace.save_settings(s.workspace.settings() | {"agent_autonomy": "render"})
    return Harness(
        s.workspace.db, AgentStore(), None, s.workspace.settings, library=s.workspace.library
    )


def planned(s, *kinds):
    """A session whose approved plan is executing its steps, the first targeting the scene."""
    h = harness(s)
    row = h.open(s.segment["movie_id"])
    document = AgentSession.model_validate(row["document"])
    document.plan_mode = True
    document.plan = Plan(
        id="plan",
        revision=1,
        state="executing",
        goal="Build the box room from its take",
        proposed_turn=0,
        # A build step reserves the renders its filling may spend, as the plan's cost check sets them.
        steps=[
            PlanStep(
                kind=kind,
                title=f"{kind} step",
                target=s.segment["id"],
                jobs=fill_renders() if kind == "build" else 0,
            )
            for kind in kinds
        ],
        budget_jobs=0,
        grant=PlanGrant(
            budget_jobs=0, step_kinds=list(kinds), approved_turn=1, reserved_at_approval=0
        ),
    )
    return h, row, document


def context(h, row, document):
    return h.context(row, document, [], f"turn-{next(TURNS)}")


def build(h, row, document, shot, **fields):
    result = BuildSetFromTake().run(
        context(h, row, document),
        BuildSetFromTake.Input(segment_id=document.plan.steps[0].target, shot=shot, **fields),
    )
    assert result.ok, result
    # As the loop links a tool's receipts to the current step.
    document.plan.steps[0].receipts.extend(result.receipts)
    return result


def test_no_build_without_a_plan_that_grants_it(scene):
    s = scene
    h = harness(s)
    row = h.open(s.segment["movie_id"])
    document = AgentSession.model_validate(row["document"])
    document.plan_mode = False  # even with render autonomy
    ctx = context(h, row, document)
    assert "build_set_from_take" not in {tool.name for tool, _ in applicable_tools(ctx)}
    refused = BuildSetFromTake().run(ctx, BuildSetFromTake.Input(segment_id=s.segment["id"]))
    assert not refused.ok and "approved plan" in refused.discrepancy
    assert operation_count(s) == 0


def operation_count(s):
    from gaiavfx.modules.generation.operations.service import OperationService

    with s.workspace.db.transaction() as session:
        return len(OperationService().recent(session, kind="set_build"))


def test_a_build_step_builds_the_set_and_waits_for_the_user_s_decision_on_its_v1(scene):
    s = scene
    h, row, document = planned(s, "build", "answer")
    assert "build_set_from_take" in {
        tool.name for tool, _ in applicable_tools(context(h, row, document))
    }
    started = build(h, row, document, shot=0)
    assert started.payload["message"].startswith("Building Box room from a take from First shot")
    assert "Up to 6 renders, about 15 to 25 minutes of GPU work" in started.payload["message"]
    # The agent cannot mark its build done: the user decides the new set.
    refused = PlanStepTool().run(
        context(h, row, document), PlanStepTool.Input(step=0, status="done")
    )
    assert not refused.ok and "build_decision" in refused.discrepancy
    drain(s.run)
    seed = operation(s.workspace, started.waits_on)
    with s.workspace.db.transaction() as session:
        h._link_review(document, seed, session)
        ended = ended_turn(session, seed)
    # The agent hears what happened and what it may do, never the user's "you" or an id it cannot use; the user's
    # words ride along as the turn's notice.
    assert ended["action"]["message"] == (
        "The stage build ended. Box room from a take is built and filled its unseen space in 2 rounds. Its v1 waits "
        "for the user's decision in the stage workbench; the build step completes when they accept or discard it. "
        "Tell the user where it waits: accepting is theirs."
    )
    assert seed["id"] not in ended["action"]["message"]
    assert ended["action"]["notice"].endswith("its v1 waits for you in the stage workbench.")
    set_id = seed["review"][-1]["set_id"]
    assert ended["receipts"] == [
        {"kind": "set_build", "set_id": set_id, "operation_id": seed["id"]}
    ]
    step = document.plan.steps[0]
    assert (step.status, step.review_of) == ("awaiting_user", f"seed:{seed['id']}")
    with s.workspace.db.transaction() as session:
        assert not h._complete_reviews(session, row, document)
    # The user accepts v1 in the set workbench; the step completes and the agent hears it.
    with s.workspace.db.transaction() as session:
        version = sets.STORE.get(session, set_id)["versions"][0]
        sets.accept_version(
            session, s.workspace.library, set_id, version["id"], expected_current_id=None
        )
        assert h._complete_reviews(session, row, document)
        turn = h.store.turns(session, row["id"])[-1]
    assert step.status == "done"
    assert turn["action"]["message"] == (
        "The user accepted v1 of Box room from a take. Box room is built as it, so the scenes there read its "
        "views. Continue the plan."
    )
    assert turn["action"]["notice"] == (
        "Box room from a take is built and filled its unseen space in 2 rounds; v1 accepted."
    )
    # The decision's receipt keeps the stage in the agent's context.
    assert turn["receipts"][0]["set_id"] == set_id


def test_a_discarded_v1_skips_the_build_step_and_the_agent_asks(scene):
    s = scene
    h, row, document = planned(s, "build", "answer")
    started = build(h, row, document, shot=0, fill_unseen=False)
    drain(s.run)
    seed = operation(s.workspace, started.waits_on)
    set_id = seed["review"][-1]["set_id"]
    with s.workspace.db.transaction() as session:
        h._link_review(document, seed, session)
        version = sets.STORE.get(session, set_id)["versions"][0]
        sets.discard_version(session, s.workspace.library, set_id, version["id"])
        assert h._complete_reviews(session, row, document)
        said = h.store.turns(session, row["id"])[-1]["action"]["message"]
    assert document.plan.steps[0].status == "skipped"
    assert said == (
        "The user discarded v1 of Box room from a take; the stage has no accepted version. Box room stays built as "
        "it, so the scenes there have no views from it until the user accepts a version or uses another stage. "
        "The build step is skipped: tell the user and ask what they want."
    )


def test_a_refused_build_ends_its_step_in_the_seed_s_words(scene):
    s = scene
    h, row, document = planned(s, "build", "answer")
    # The cut's shot: its camera stands still, and the agent does not fill it.
    started = build(h, row, document, shot=1, fill_unseen=False)
    drain(s.run)
    seed = operation(s.workspace, started.waits_on)
    with s.workspace.db.transaction() as session:
        h._link_review(document, seed, session)
        words = ended_words(session, seed)
    assert document.plan.steps[0].status == "skipped"
    assert "The camera stands still in this take" in words
    # Filling would have built it: the agent may offer that, and build again only in an approved plan.
    assert words.endswith(
        "The build step is skipped. Filling its unseen space (fill_unseen) builds from a still or turning camera: "
        "offer that, and build again only in a plan the user approves."
    )
    # The refusal holds: a new plan's build of the same shot is refused without running.
    h, row, document = planned(s, "build")
    again = BuildSetFromTake().run(
        context(h, row, document),
        BuildSetFromTake.Input(segment_id=s.segment["id"], shot=1, fill_unseen=False),
    )
    assert not again.ok and "would give the same answer" in again.discrepancy
    assert "Fill in its unseen space" in again.discrepancy


def test_a_still_shot_builds_when_the_agent_fills_its_unseen_space(scene):
    s = scene
    h, row, document = planned(s, "build")
    started = build(h, row, document, shot=1)
    drain(s.run)
    seed = operation(s.workspace, started.waits_on)
    assert seed["budget_reserved"] == 6
    with s.workspace.db.transaction() as session:
        h._link_review(document, seed, session)
        assert "is built and filled" in ended_words(session, seed)
    assert document.plan.steps[0].status == "awaiting_user"


def test_a_build_that_fills_needs_the_renders_its_step_reserved(scene):
    s = scene
    h, row, document = planned(s, "build")
    document.plan.steps[0].jobs = 0
    refused = BuildSetFromTake().run(
        context(h, row, document), BuildSetFromTake.Input(segment_id=s.segment["id"])
    )
    assert not refused.ok and "step_budget_exhausted" in refused.discrepancy
    plain = BuildSetFromTake().run(
        context(h, row, document),
        BuildSetFromTake.Input(segment_id=s.segment["id"], fill_unseen=False),
    )
    assert plain.ok and "About 3 minutes" in plain.payload["message"]


def test_the_view_names_a_set_candidate_while_a_set_could_keep_a_shared_place(scene):
    from test_set_seed import transact

    from gaiavfx.application import set_seed
    from gaiavfx.application.direction_reading import DIRECTION
    from gaiavfx.application.harness.view import render_view

    s = scene
    h, row, document = planned(s, "answer")
    assert "Stage candidate" not in render_view(context(h, row, document).state, "off", 0, 6)
    # A second scene opens in the same box room, by the place's id, not its name.
    with s.workspace.db.transaction() as session:
        movie = DIRECTION.read_movie(session, s.segment["movie_id"])
        second = DIRECTION.add_segment(
            session,
            movie["id"],
            movie["revision"],
            content=s.segment["content"],
            delivered_frames=24,
            continues=False,
            phases=[(0, {"description": "Ivo sits.", "screen_direction": "", "sound": ""})],
        )
        DIRECTION.update_segment(
            session,
            second["id"],
            second["revision"],
            opening_angle_id=s.segment["opening_angle_id"],
        )
    view = render_view(context(h, row, document).state, "off", 0, 6)
    # The line names the shots build_set_from_take can build from, so the agent need not guess one.
    assert (
        f"Stage candidate: segments 1, 2 share place Box room ({s.place['id']}), not built as a stage; "
        f"segment 1 ({s.segment['id']}) has a selected take to build one from ({SHOTS})."
    ) in view
    # A build of that take that stopped may be tried again, so the view still offers it (the live trial's two stopped
    # builds hid scene 1).
    s.comfy.execution_error = True
    stopped = transact(
        s.workspace,
        set_seed.start_seed,
        s.workspace.library,
        segment_id=s.segment["id"],
        shot=0,
        name="Box room",
        use_for_place=True,
        fill_unseen=False,
    )
    drain(s.run)
    assert f"segment 1 ({s.segment['id']}) has a selected take" in render_view(
        context(h, row, document).state, "off", 0, 6
    )
    # Once a build of that take is under way, the view no longer offers one.
    s.comfy.execution_error = False
    transact(s.workspace, set_seed.retry_seed, s.workspace.library, stopped["id"])
    assert "Stage candidate" not in render_view(context(h, row, document).state, "off", 0, 6)


def test_the_agent_sees_its_build_in_words_and_builds_only_the_scene_its_step_names(scene):
    from gaiavfx.application.direction_reading import DIRECTION
    from gaiavfx.application.harness.tools.reads import ReadSegment
    from gaiavfx.application.harness.view import render_view

    s = scene
    h, row, document = planned(s, "build")

    def view():
        return render_view(context(h, row, document).state, "off", 0, 6, document=document)

    assert (
        f"target exists: {s.segment['id']}; build_set_from_take from one of its take's shots ({SHOTS}); the user "
        "decides its v1"
    ) in view()
    read = ReadSegment().run(
        context(h, row, document), ReadSegment.Input(segment_id=s.segment["id"])
    )
    assert read.payload["take_shots"] == SHOTS
    # Another scene is not the step's to build from.
    with s.workspace.db.transaction() as session:
        movie = DIRECTION.read_movie(session, s.segment["movie_id"])
        other = DIRECTION.add_segment(
            session,
            movie["id"],
            movie["revision"],
            content=s.segment["content"],
            delivered_frames=24,
            continues=False,
            phases=[(0, {"description": "Ivo sits.", "screen_direction": "", "sound": ""})],
        )
    refused = BuildSetFromTake().run(
        context(h, row, document), BuildSetFromTake.Input(segment_id=other["id"])
    )
    assert not refused.ok and "step_target_mismatch" in refused.discrepancy
    started = build(h, row, document, shot=0, fill_unseen=False)
    building = view()
    assert (
        "Stage build: Box room from a take: building from this take; the conversation waits for the build."
        in building
    )
    assert started.waits_on not in building
    drain(s.run)
    waiting = view()
    assert (
        "Stage build: Box room from a take is built. Its v1 waits for the user's decision"
        in waiting
    )
    set_id = operation(s.workspace, started.waits_on)["review"][-1]["set_id"]
    # The place is built as the new stage, which waits for its first accepted version.
    assert (
        f"Stage Box room from a take ({set_id}): proposed v1, none accepted; 0 open issues"
        in waiting
    )
    with s.workspace.db.transaction() as session:
        version = sets.STORE.get(session, set_id)["versions"][0]
        sets.accept_version(
            session, s.workspace.library, set_id, version["id"], expected_current_id=None
        )
    decided = view()
    assert "Stage build:" not in decided
    assert (
        f"Stage Box room from a take ({set_id}): accepted v1; 0 open issues; built as place Box room"
        in decided
    )


def test_the_chat_shows_the_build_in_python_s_words_and_why_the_agent_is_quiet(scene):
    s = scene
    h, row, document = planned(s, "build", "answer")
    started = build(h, row, document, shot=0, fill_unseen=False)
    document.waiting_on = started.waits_on
    waiting = h.payload({**row, "state": "waiting_operation", "document": document.model_dump()})
    assert waiting["activity"] == "building_stage"
    assert waiting["build"] | {"operation_id": None} == {
        "state": "building",
        "name": "Box room from a take",
        "label": "Building from this take…",
        "message": "Box room from a take: building from this take…",
        "operation_id": None,
        "set_id": None,
        "shot": 0,
        "waiting": False,
        "can_retry": False,
        "step": 0,
        # The local model is the default agent.
        "note": "Gaia answers when the build ends: Gaia's model and the build share the GPU.",
    }
    HarnessSessionDTO.model_validate(waiting)
    drain(s.run)
    with s.workspace.db.transaction() as session:
        h._link_review(document, operation(s.workspace, started.waits_on), session)
    document.waiting_on = ""
    done = h.payload({**row, "state": "waiting_human", "document": document.model_dump()})
    assert (done["build"]["state"], done["build"]["waiting"], done["build"]["note"]) == (
        "done",
        True,
        "",
    )
    assert done["activity"] == "waiting_human"


def test_a_stopped_build_returns_the_renders_it_never_allocated(scene):
    from gaiavfx.application import set_fill
    from gaiavfx.application.harness.commands import jobs_reserved
    from gaiavfx.application.harness.tools.plan_costs import step_jobs_left
    from gaiavfx.modules.generation.operations.service import OperationService

    s = scene
    h, row, document = planned(s, "build")
    started = build(h, row, document, shot=1)
    for _ in range(400):
        if set_fill.renders_used(operation(s.workspace, started.waits_on)) or not s.run.run_once():
            break
    with s.workspace.db.transaction() as session:
        OperationService().request_cancel(session, started.waits_on)
    drain(s.run)
    stopped = operation(s.workspace, started.waits_on)
    used = set_fill.renders_used(stopped)
    assert 1 <= used < 6
    with s.workspace.db.transaction() as session:
        h._link_review(document, stopped, session)
        ended = ended_turn(session, stopped)
    assert document.plan.steps[0].status == "pending"
    # The user stopped it: the agent asks before building again.
    assert ended["action"]["message"].endswith(
        f"after {used} of its 6 renders: the user stopped it. The build step stays open: ask the user before you "
        "build again."
    )
    assert (
        ended["action"]["notice"] == "Building Box room from a take was stopped before it finished."
    )
    # The view says it stopped only inside the step it stopped in: the live trial's model read an old stopped build's
    # "the build step stays open" outside any plan and offered to rerun it instead of the stage candidate.
    stopped_line = "Stage build: Building Box room from a take stopped before it finished"
    assert stopped_line in render_view(
        context(h, row, document).state, "off", 0, 6, document=document
    )
    later = planned(s, "answer")
    assert "Stage build:" not in render_view(context(*later).state, "off", 0, 6, document=later[2])
    assert "Stage build:" not in render_view(context(h, row, document).state, "off", 0, 6)
    # The step and the conversation count what the build allocated, not what it reserved.
    assert step_jobs_left(context(h, row, document)) == 6 - used
    turns = [
        {"state": "accepted", "receipts": started.receipts},
        {"state": "accepted", "receipts": ended["receipts"]},
    ]
    assert jobs_reserved(turns) == used
    # A build that fits in what is left may run; one that fills needs a revised plan.
    refused = BuildSetFromTake().run(
        context(h, row, document), BuildSetFromTake.Input(segment_id=s.segment["id"], shot=0)
    )
    assert not refused.ok and "step_budget_exhausted" in refused.discrepancy
    build(h, row, document, shot=0, fill_unseen=False)


def test_a_stop_the_next_build_would_meet_is_named_and_not_retried_blindly():
    # The live trial on a dead ComfyUI port: told only that its build stopped, the agent built again and then offered
    # to build from the other scene, against the same dead port.
    from gaiavfx.application.set_agent import after_stop
    from gaiavfx.application.set_seed import stop_cause

    def failed(error, job_error=""):
        jobs = [{"state": "failed", "error": job_error}] if job_error else []
        return {"state": "failed", "cancel_requested": False, "error": error, "jobs": jobs}

    refused = failed(
        "6c8f: [WinError 10061] No connection could be made because the target machine actively refused it"
    )
    assert stop_cause(refused) == "comfyui"
    assert after_stop(refused, "build", True) == (
        ": ComfyUI did not answer. Another build stops the same way until that changes: tell the user, and build "
        "once more in this step only when they say it is fixed."
    )
    assert stop_cause(failed("", "CUDA out of memory. Tried to allocate 2.00 GiB")) == "memory"
    assert stop_cause(failed("", "The engines root has no worldbuilder engine")) == "engines"
    assert stop_cause({**failed("x"), "state": "cancelled"}) == "stopped"
    # Anything else may pass on a second try; the build's own reason is kept when it has one.
    other = failed("the splat did not converge")
    assert stop_cause(other) is None
    reason = (
        "The build stopped filling in round 1: its consolidation failed; the stage is as it was."
    )
    assert after_stop(other, "fill", True, reason).startswith(
        ". The build stopped filling in round 1: its consolidation failed; the stage is as it was. The build step "
        "stays open with the rest: fill once more in it"
    )


def test_the_agent_fills_more_of_an_accepted_stage_in_a_build_step(scene):
    # ADR 0020 §8: the agent's Fill more runs as the user's would, from the accepted version, only inside a build
    # step, never past the user's own queued fixes; the version it proposes waits for the user like v1.
    from gaiavfx.application.harness.tools.sets import FillSetMore

    s = scene
    h, row, document = planned(s, "build")
    started = build(h, row, document, shot=0, fill_unseen=False)
    drain(s.run)
    set_id = operation(s.workspace, started.waits_on)["review"][-1]["set_id"]
    library = s.workspace.library
    with s.workspace.db.transaction() as session:
        v1 = sets.STORE.get(session, set_id)["versions"][0]
        sets.accept_version(session, library, set_id, v1["id"], expected_current_id=None)

    # The live eval (stage-local-a3): asked to fill more, the model read the stage and asked where, since nothing
    # told it Fill more was its to plan. The reading of an accepted stage says how.
    from gaiavfx.application.set_agent import agent_read_set

    with s.workspace.db.transaction() as session:
        reading = agent_read_set(session, library, set_id)
    assert reading["fill_more"].startswith(
        f"To fill in what no view of it has seen yet, all around, propose a plan with one build step targeting "
        f"{set_id} (6 jobs); fill_set_more runs in it"
    )

    h, row, document = planned(s, "build")
    document.plan.steps[0].target = set_id
    assert "fill_set_more" in {t.name for t, _ in applicable_tools(context(h, row, document))}
    assert f"target exists: {set_id}; fill_set_more" in render_view(
        context(h, row, document).state, "off", 0, 6, document=document
    )
    # The user's own exclusion is queued: Run queued fixes would run it too, so the agent asks first.
    with s.workspace.db.transaction() as session:
        keyframe = sets.STORE.keyframes(session, v1["id"])[1]
        queued = sets.queue_fix(
            session,
            library,
            set_id,
            version_id=v1["id"],
            action="retract",
            target={"keyframe": keyframe["id"]},
            shows="",
            issue_id=None,
        )["fixes"][0]
    value = FillSetMore.Input(set_id=set_id)
    refused = FillSetMore().run(context(h, row, document), value)
    assert not refused.ok and "fixes queued" in refused.discrepancy
    with s.workspace.db.transaction() as session:
        sets.cancel_fix(session, library, set_id, queued["id"], version_id=v1["id"])
    filling = FillSetMore().run(context(h, row, document), value)
    assert filling.ok, filling
    document.plan.steps[0].receipts.extend(filling.receipts)
    assert filling.payload["message"].startswith(
        "Filling more of Box room from a take: up to 6 renders"
    )
    # The chat shows these words as the build's card: they address the user.
    assert filling.payload["message"].endswith(
        "its new version waits for you in the stage workbench."
    )
    with s.workspace.db.transaction() as session:
        fix = next(f for f in sets.STORE.fixes(session, set_id) if f["action"] == "extend")
    assert fix["author"] == "agent"
    drain(s.run)
    built = operation(s.workspace, filling.waits_on)
    with s.workspace.db.transaction() as session:
        h._link_review(document, built, session)
        ended = ended_turn(session, built)
    step = document.plan.steps[0]
    assert (step.status, step.review_of) == ("awaiting_user", f"build:{built['id']}")
    assert ended["action"]["message"].startswith(
        "The stage build ended. Box room from a take v2 is built (v1 rebuilt; filled its unseen space in 2 rounds). "
        "It waits for the user's decision in the stage workbench"
    )
    # The user discards v2: v1 stays the stage's, and the step is skipped.
    with s.workspace.db.transaction() as session:
        v2 = sets.STORE.get(session, set_id)["versions"][-1]
        sets.discard_version(session, library, set_id, v2["id"])
        assert h._complete_reviews(session, row, document)
        said = h.store.turns(session, row["id"])[-1]["action"]["message"]
    assert step.status == "skipped"
    assert said == (
        "The user discarded v2 of Box room from a take; v1 stays the stage's. The build step is skipped: tell the "
        "user and ask what they want."
    )


def test_a_stage_build_proposed_as_preparation_is_refused_with_the_build_step_s_form(scene):
    # The live trial: the model proposed "Build stage from segment 1's selected take" as a prepare step of 1 job, and
    # the plan passed.
    from gaiavfx.application.harness.tools.plan import ProposePlan, stage_title

    s = scene
    h, row, document = planned(s, "answer")
    document.plan = None
    refused = ProposePlan().run(
        context(h, row, document),
        ProposePlan.Input(
            goal="Keep the room the same",
            steps=[
                {
                    "kind": "prepare",
                    "title": "Build stage from segment 1's selected take",
                    "jobs": 1,
                }
            ],
        ),
    )
    assert not refused.ok
    assert refused.discrepancy == (
        "wrong_step_kind: Build stage from segment 1's selected take builds a stage; use kind build with 6 jobs, not "
        f"prepare, its target the segment whose selected take it builds from (segment 1 {s.segment['id']}). The user "
        "decides its v1."
    )
    assert document.plan is None
    # Preparing from a built stage stays preparation.
    assert stage_title("Build the Fishing shack stage")
    assert not stage_title("Re-prepare segment 1 opening against the built stage")
    assert not stage_title("Build opening views from the stage")
    # Corrected, the model left the build step's target out four times; the scene its title names is the target.
    proposed = ProposePlan().run(
        context(h, row, document),
        ProposePlan.Input(
            goal="Keep the room the same",
            steps=[{"kind": "build", "title": "Build stage from segment 1's selected take"}],
        ),
    )
    assert proposed.ok, proposed
    assert (document.plan.steps[0].target, document.plan.steps[0].jobs) == (s.segment["id"], 6)


def test_an_omitted_build_target_is_what_the_title_names_or_the_only_choice():
    from types import SimpleNamespace

    from gaiavfx.application.harness.tools.plan import default_build_target

    state = SimpleNamespace(
        segments=[{"id": "s1", "ordinal": 0}, {"id": "s2", "ordinal": 1}],
        sets=[{"id": "shack", "name": "Tiny shack"}, {"id": "loft", "name": "Loft"}],
    )

    def target(title, scenes=("s1", "s2"), stages=("shack", "loft"), turns=()):
        step = PlanStep(kind="build", title=title)
        context = SimpleNamespace(state=state, report_turns=list(turns))
        default_build_target(context, step, list(scenes), list(stages))
        return step.target

    assert target("Build stage from segment 2's take") == "s2"
    assert target("Fill more of the tiny shack") == "shack"
    assert target("Fill more of the room", stages=["loft"]) == "loft"
    # Several choices and a title that names none, or a scene that cannot build: the refusal lists them.
    assert target("Fill more of the room") == ""
    assert target("Build the room's stage") == ""
    assert target("Build stage from segment 2's take", scenes=["s1"]) == ""
    assert target("Build the room's stage", scenes=["s1"], stages=[]) == "s1"
    # Asked to fill more, the trial's model read the stage and left the target out three times: a fill fills the stage
    # this conversation read last.
    read = {"state": "accepted", "result": {"payload": {"id": "loft"}}}
    assert target("Fill unseen space in the room", turns=[read]) == "loft"
    assert target("Build the room's stage", turns=[read]) == ""


def test_the_build_s_receipt_says_what_it_reserved(scene):
    # The session's budget reads every accepted turn's operation receipts (commands.jobs_reserved); a build's receipt
    # without its reservation failed the next turn.
    from gaiavfx.application.harness.commands import jobs_reserved

    s = scene
    h, row, document = planned(s, "build")
    started = build(h, row, document, shot=0)
    seed = operation(s.workspace, started.waits_on)
    assert (
        jobs_reserved([{"state": "accepted", "receipts": started.receipts}])
        == seed["budget_reserved"]
    )


def test_a_build_step_reserves_the_renders_its_filling_may_spend(scene):
    # Filling a stage's unseen space renders up to six probes (ADR 0020 §7), so a build step budgets them; the build
    # spends what its operation reserved.
    from gaiavfx.application.harness.tools.plan_costs import check_plan_costs, step_jobs_left
    from gaiavfx.application.set_seed import fill_renders

    s = scene
    h, row, document = planned(s, "build")
    ctx = context(h, row, document)
    step = PlanStep(kind="build", title="Build the shack", target=s.segment["id"])
    assert check_plan_costs(ctx, [step]) == "" and step.jobs == fill_renders() == 6
    wrong = PlanStep(kind="build", title="Build the shack", target=s.segment["id"], jobs=2)
    assert "build_jobs" in check_plan_costs(ctx, [wrong])
    document.plan.steps[0].jobs = fill_renders()
    assert step_jobs_left(context(h, row, document)) == 6
    started = build(h, row, document, shot=0)
    reserved = operation(s.workspace, started.waits_on)["budget_reserved"]
    assert step_jobs_left(context(h, row, document)) == 6 - reserved
