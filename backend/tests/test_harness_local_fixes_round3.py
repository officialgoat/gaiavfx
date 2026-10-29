"""CPU replays of local-e3 failures; guidance probes do not claim model quality."""

import json
from dataclasses import replace

import harness_support
import pytest
from harness_eval import ScriptedModel, run_task
from harness_eval_scripts import (
    GOOD,
    SAY,
    action,
    candidates,
)
from harness_eval_tasks import TASKS

from gaiavfx.application.harness.actions import inlined
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.session import AgentSession
from gaiavfx.application.harness.tools import DEFAULT_TOOLS
from gaiavfx.application.harness.tools.plan import ProposePlan
from gaiavfx.application.harness.tools.talk import AskHuman, Say
from gaiavfx.modules.agent.service import AgentStore

kernel = harness_support.kernel
world = harness_support.world


def current_context(work, setup):
    harness = Harness(work.db, AgentStore(), None, work.settings)
    row = harness.read(setup["session_id"])
    return harness.context(
        row, AgentSession.model_validate(row["document"]), harness.turns(row["id"])
    )


def task(name):
    return next(t for t in TASKS if t.id == name)


@pytest.mark.parametrize(
    "name",
    [
        "text-renders-never-selects",
        "segment-0-cuts",
        "segment-2-cuts",
        "stale-sheet-reassembled",
        "never-approves-in-prepare-chain",
    ],
)
def test_n1_unread_render_report_is_rejected_then_read(tmp_path, monkeypatch, name):
    # local-e3: successful render -> "candidates are ready" without reading.
    sequence = list(GOOD[name])
    index = max(i for i, entry in enumerate(sequence) if entry is candidates)

    def premature_report(work, setup):
        context = current_context(work, setup)
        assert context.unread_renders
        if name == "text-renders-never-selects":
            from gaiavfx.application.harness import scope
            from gaiavfx.application.harness.tools import applicable_tools

            original = scope.world_state

            def bounded(*args):
                return replace(original(*args), operations=[])

            with monkeypatch.context() as patch:
                patch.setattr(scope, "world_state", bounded)
                restored = current_context(work, setup)
            assert restored.unread_renders == context.unread_renders
            schema = next(s for t, s in applicable_tools(restored) if t.name == "read_candidates")
            assert set(restored.unread_renders) <= set(schema["properties"]["operation_id"]["enum"])
        for tool in (Say(), ProposePlan()):
            assert tool.narrow(context) is None
            result = tool.run(context, tool.Input.model_construct())
            assert not result.ok and "read_candidates" in result.discrepancy
        assert AskHuman().run(context, AskHuman.Input(question="Which cut next?")).ok
        return SAY

    sequence.insert(index, premature_report)
    observed = []
    spec = task(name)
    spec = replace(spec, criteria=spec.criteria | {"rejected": spec.criteria["rejected"] + 1})
    result = run_task(spec, sequence, tmp_path, evidence=observed.append)
    assert result["passed"], result
    rejected = [
        t
        for t in observed[0]["turns"]
        if t["state"] == "rejected" and (t["action"] or {}).get("tool") == "say"
    ]
    assert len(rejected) == 1
    tools = rejected[0]["provenance"]["tools"]
    assert "say" not in tools and "propose_plan" not in tools
    assert {"read_candidates", "ask_human"} <= set(tools)


def test_n2_unread_video_claim_is_rejected_before_analysis(tmp_path):
    spec = task("replacement-similar")
    observed = []
    result = run_task(
        spec,
        [action("say", text="I can see the video."), *GOOD[spec.id]],
        tmp_path,
        evidence=observed.append,
    )
    assert result["passed"], result
    turns = [t for t in observed[0]["turns"] if t["role"] == "agent"]
    assert turns[0]["state"] == "rejected"
    assert "analyze_people" in turns[0]["provenance"]["tools"]
    assert "say" not in turns[0]["provenance"]["tools"]


def turn(role="agent", state="accepted", *, action=None, result=None, receipts=()):
    return {"role": role, "state": state, "action": action, "result": result, "receipts": receipts}


def test_n2_backstop_for_every_tool_and_session_read_cache(kernel):
    k = kernel
    attachment = {"asset_id": "image", "kind": "image", "name": "still.png"}
    human = turn("human", action={"message": "Discuss this", "attachments": [attachment]})
    read = turn(
        action=action("describe_reference", asset_id="image"),
        result={"focus": {"asset_id": "image"}},
    )
    notes = turn(action={"tool": "reference_notes"})
    doc = AgentSession()
    context = k.harness.context(k.row, doc, [human])
    for tool in DEFAULT_TOOLS:
        if tool.name in {
            "describe_reference",
            "analyze_reference_video",
            "analyze_people",
            "read_recast",  # a reading tool that satisfies its film's attachment (stage D)
            "ask_human",
        }:
            continue
        result = tool.run(context, tool.Input.model_construct())
        assert not result.ok and "attachments" in result.discrepancy, tool.name
    assert AskHuman().run(context, AskHuman.Input(question="Can you clarify the shot?")).ok
    # Neither a cached image file, an editable board claim, nor failed notes unlock discussion.
    assert k.harness.context(k.row, doc, [human, read]).unread_attachments == ["image"]
    assert k.harness.context(
        k.row, doc, [human, read, notes | {"state": "rejected"}]
    ).unread_attachments == ["image"]
    assert k.harness.context(
        k.row, doc, [human, read | {"state": "rejected"}, notes]
    ).unread_attachments == ["image"]
    from gaiavfx.application.harness.board import write_entry

    write_entry(doc.board, "references", "Already read image", 1)
    assert k.harness.context(k.row, doc, [human]).unread_attachments == ["image"]
    # An accepted read+notes pair counts across human messages and a new Harness instance.
    fresh = Harness(k.db, k.store, None, lambda: k.settings)
    cached = fresh.context(k.row, doc, [human, read, notes, human])
    assert cached.unread_attachments == [] and Say().run(cached, Say.Input(text="Read earlier.")).ok
    other = {"asset_id": "other", "kind": "video", "name": "clip.mp4"}
    latest = turn("human", action={"attachments": [attachment, other]})
    assert fresh.context(k.row, doc, [human, read, notes, latest]).unread_attachments == ["other"]
    # An older unread attachment does not trap a later message with no attachments.
    assert not fresh.context(k.row, doc, [human, turn("human", action={})]).unread_attachments


@pytest.mark.parametrize(
    "state,blocked", [("succeeded", True), ("partial", True), ("failed", False), ("running", False)]
)
def test_n1_only_readable_renders_started_in_this_message_block(
    kernel, monkeypatch, state, blocked
):
    k = kernel
    from gaiavfx.application.harness.world import WorldState

    operation = {
        "id": "render",
        "kind": "render",
        "state": state,
        "review_pending": False,
        "candidate_count": 1,
    }
    world_state = WorldState(
        movie={"id": k.movie["id"], "revision": 1, "title": "Test"}, operations=[operation]
    )
    monkeypatch.setattr("gaiavfx.application.harness.scope.world_state", lambda *args: world_state)
    receipt = {
        "kind": "operation",
        "operation_kind": "render",
        "id": "render",
        "budget_reserved": 1,
    }
    human = turn("human", action={})
    started = turn(receipts=[receipt])
    doc = AgentSession()
    context = k.harness.context(k.row, doc, [human, started])
    assert bool(context.unread_renders) is blocked
    assert bool(Say().run(context, Say.Input(text="Ready.")).ok) is not blocked
    # Other sessions, earlier messages, and surveys never acquire this obligation.
    assert not k.harness.context(k.row, doc, [human]).unread_renders
    assert not k.harness.context(k.row, doc, [human, started, human]).unread_renders
    survey = turn(receipts=[receipt | {"operation_kind": "survey"}])
    assert not k.harness.context(k.row, doc, [human, survey]).unread_renders
    # A rejected candidate read cannot release the gate; a retained review receipt can.
    read = turn(receipts=[{"kind": "review_set", "id": "render"}])
    assert (
        bool(
            k.harness.context(
                k.row, doc, [human, started, read | {"state": "rejected"}]
            ).unread_renders
        )
        is blocked
    )
    assert not k.harness.context(k.row, doc, [human, started, read]).unread_renders
    recovered = started | {"role": "system"}
    assert bool(k.harness.context(k.row, doc, [human, recovered]).unread_renders) is blocked


def test_n1_executing_render_plan_requires_read_before_review_report(tmp_path):
    spec = task("plan-revise-and-render")
    sequence = list(GOOD[spec.id])
    sequence.insert(sequence.index(candidates), SAY)
    observed = []
    result = run_task(spec, sequence, tmp_path, outcome=True, evidence=observed.append)
    assert result["passed"] and result["outcome_rubric"]["plan_review_resume"], result
    rejected = [t for t in observed[0]["turns"] if t["state"] == "rejected"]
    assert len(rejected) == 2 and all(t["action"]["tool"] == "say" for t in rejected)
    assert "call propose_plan now" in rejected[0]["result"]["discrepancy"]
    assert "say" not in rejected[1]["provenance"]["tools"]


def test_n3_explicit_seed_dispatch_refused_but_direct_input_retained(world):
    from test_harness_tools import context_for

    from gaiavfx.application.harness.actions import parse_action
    from gaiavfx.application.harness.tools.render import RenderCandidates, render_seeds
    from gaiavfx.shared.errors import ApplicationError

    work, setup = world
    context = context_for(work, setup)
    tool = RenderCandidates()
    context.autonomy = "render"
    context.state.segment_plans = {
        key: value | {"refusals": []} for key, value in context.state.segment_plans.items()
    }
    shape = tool.narrow(context)
    assert shape is not None
    for seeds in ([], [42], [42, 43]):
        with pytest.raises(ApplicationError):
            parse_action(
                action("render_candidates", segment_id=setup["segment_id"], count=2, seeds=seeds),
                [(tool, shape)],
            )
    value = tool.Input(segment_id=setup["segment_id"], count=2, seeds=[42, 43])
    assert render_seeds(context, value) == [42, 43]


def test_n6_already_declared_inspection_reports_without_preparation(world):
    from harness_eval_scripts import inspect, prepare

    from gaiavfx.application.harness.driver import AgentRunner
    from gaiavfx.modules.generation.operations.service import OperationService

    def report(work, setup):
        context = current_context(work, setup)
        from gaiavfx.application.harness.tools.reads import InspectCut

        result = InspectCut().run(context, InspectCut.Input(cut_id=setup["cut_id"]))
        assert (
            "This cut (segment 1, f36, gate -> reverse) already has an authored relation."
            in result.payload["message"]
        )
        assert "Legal actions are options" in result.payload["message"]
        assert "Do not prepare unless asked" in result.payload["message"]
        assert "prepare_cut" in result.payload["legal_now"]  # option, never auto-started
        # Replay the observed wrong decision unless the inspection explicitly reports satisfaction.
        return (
            action("say", text="The reverse is already declared. I can prepare it next.")
            if "already has an authored relation" in result.payload["message"]
            else prepare(work, setup)
        )

    work, setup = world
    model = ScriptedModel([inspect, report], work, setup)
    harness = Harness(work.db, AgentStore(), model, work.settings)
    row = harness.open(setup["movie_id"])
    setup["session_id"] = row["id"]
    harness.set_plan_mode(row["id"], row["revision"], False)
    harness.post_message(row["id"], "Declare the reverse.", "declare")
    AgentRunner(work.db, harness).run_once()
    assert harness.read(row["id"])["state"] == "idle"
    turns = harness.turns(row["id"])
    assert [t["action"]["tool"] for t in turns if t["role"] == "agent"] == ["inspect_cut", "say"]
    assert not any(t["receipts"] for t in turns)
    with work.db.transaction() as session:
        assert not OperationService().recent(session)


@pytest.mark.parametrize("relation,source", [(None, "unknown"), ("reverse", "inferred")])
def test_n6_does_not_claim_unauthored_relation_is_declared(world, relation, source):
    from test_harness_tools import context_for

    from gaiavfx.application.harness.tools.reads import InspectCut
    from gaiavfx.modules.direction.service import DirectionService

    work, setup = world
    service = DirectionService()
    with work.db.transaction() as session:
        cut = service.read_cut(session, setup["cut_id"])
        service.update_cut(
            session,
            cut["id"],
            cut["revision"],
            relation=relation,
            relation_source=source,
            crosses_barrier=True if relation else None,
        )
    result = InspectCut().run(context_for(work, setup), InspectCut.Input(cut_id=setup["cut_id"]))
    assert result.ok and "already has an authored relation" not in result.payload["message"]


def test_n3_empty_seeds_are_absent_from_render_grammar(tmp_path, monkeypatch):
    original = ScriptedModel.call
    seen = []

    def probe(self, settings, messages, schema, cancelled, log_path):
        for branch in inlined(schema).get("oneOf", [inlined(schema)]):
            if branch["properties"]["tool"].get("const") == "render_candidates":
                shape = branch["properties"]["input"]
                assert set(shape["properties"]) == {"segment_id", "count"}
                seen.append(True)
        return original(self, settings, messages, schema, cancelled, log_path)

    monkeypatch.setattr(ScriptedModel, "call", probe)
    sequence = list(GOOD["text-renders-never-selects"])
    sequence[1] = lambda w, s: action("render_candidates", segment_id=s["segment_id"], count=2)
    result = run_task(task("text-renders-never-selects"), sequence, tmp_path)
    assert result["passed"] and seen, result


def test_n5_direct_storyline_has_no_plan_mode_instructions(tmp_path, monkeypatch):
    # local-e3/1/drafts-structure-from-brief offered a plan with the toggle off.
    original = ScriptedModel.call

    def probe(self, settings, messages, schema, cancelled, log_path):
        text = json.dumps(messages)
        assert "Act directly with the tools; reply with say when done." in text
        assert "In plan mode" not in text and "propose_plan" not in text
        return original(self, settings, messages, schema, cancelled, log_path)

    monkeypatch.setattr(ScriptedModel, "call", probe)
    spec = task("drafts-structure-from-brief")
    result = run_task(spec, GOOD[spec.id], tmp_path)
    assert result["passed"], result
