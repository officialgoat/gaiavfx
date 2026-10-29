"""Recast tools enforce scope, proposals, current human mappings and grant costs."""

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gaiavfx.application.harness.actions import ToolContext, parse_action
from gaiavfx.application.harness.board import Whiteboard
from gaiavfx.application.harness.commands import jobs_reserved
from gaiavfx.application.harness.session import AgentSession, Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.application.harness.tools import recast as tools
from gaiavfx.application.harness.tools.plan_costs import check_plan_costs, step_jobs_left
from gaiavfx.application.harness.view import render_view
from gaiavfx.application.harness.world import WorldState
from gaiavfx.shared.errors import ApplicationError


@pytest.fixture
def fixture(monkeypatch):
    state = {
        "id": "film-recast",
        "revision": 3,
        "title": "Harbor film",
        "film": {"frame_count": 200, "asset_id": "film"},
        "characters": [
            {
                "id": "c01",
                "label": "Navigator",
                "hidden": False,
                "screen_time_frames": 100,
                "target": {
                    "asset_id": "portrait",
                    "label": "Veyra",
                    "state": "confirmed",
                    "proposed_by": "user",
                },
            }
        ],
        "shots": [
            {
                "id": f"s{i}",
                "index": i,
                "start": i * 50,
                "end": (i + 1) * 50,
                "included": True,
                "status": "planned",
                "choice": None,
                "versions": [],
                "plan": {
                    "performers": ["c01"],
                    "gaps": [],
                    "renders_per_window": 2,
                    "windows": [{"start": i * 50, "end": (i + 1) * 50, "length": 56}],
                },
            }
            for i in range(4)
        ],
        "run": {"state": "idle", "mode": "draft", "renders_used": 0},
        "estimate": {"renders": 8},
        "conforms": [{"seed": "never exposed"}],
    }
    document = AgentSession(
        plan_mode=True,
        plan=Plan(
            id="plan",
            revision=1,
            goal="Recast",
            proposed_turn=0,
            state="executing",
            budget_jobs=2,
            steps=[
                PlanStep(kind="render", title="Recast the first shot", target=state["id"], jobs=2),
                PlanStep(kind="review", title="Review recast"),
            ],
            grant=PlanGrant(
                budget_jobs=2,
                step_kinds=["render", "review"],
                approved_turn=1,
                reserved_at_approval=0,
            ),
        ),
    )
    world = WorldState(
        movie={"id": "movie", "title": "Harbor", "revision": 1},
        assets=[{"id": "portrait", "kind": "image", "name": "Veyra"}],
        recasts=[tools.compact(state)],
    )
    context = ToolContext(
        db=SimpleNamespace(transaction=nullcontext),
        session_id="session",
        movie_id="movie",
        scope_kind="movie",
        scope_id="movie",
        board=Whiteboard(),
        view=state["id"],
        turn=1,
        turn_id="turn",
        state=world,
        document=document,
        jobs_limit=2,
        latest_user_message="Recast the Navigator with Veyra.",
        library=SimpleNamespace(get=lambda *args: {"name": "Veyra", "kind": "image"}),
    )
    monkeypatch.setattr(tools, "recast_state", lambda *args, **kwargs: deepcopy(state))
    start = Mock(return_value={**state, "revision": 4, "run": {"state": "running"}})
    propose = Mock(return_value=state)
    monkeypatch.setattr(tools, "start_run", start)
    monkeypatch.setattr(tools, "propose_target", propose)
    return SimpleNamespace(context=context, state=state, start=start, propose=propose)


def value(**changes):
    return tools.StartRecastRun.Input(
        recast_id="film-recast", mode="draft", render_budget=2, **changes
    )


def test_over_grant_returns_longest_prefix_and_no_run(fixture):
    result = tools.StartRecastRun().run(fixture.context, value())
    assert not result.ok and result.payload["status"] == "over_grant"
    assert result.payload["estimate"]["renders"] == 8
    assert result.payload["suggested_range"] == {"start": 0, "end": 50}
    assert result.payload["suggested_renders"] == 2
    fixture.start.assert_not_called()
    fixture.context.jobs_reserved = 2
    assert tools.StartRecastRun().run(fixture.context, value()).payload["suggested_range"] is None


def test_run_reservation_counts_future_windows_and_prior_usage(fixture):
    fixture.state["run"]["renders_used"] = 7
    result = tools.StartRecastRun().run(fixture.context, value(range={"start": 0, "end": 50}))
    assert result.ok and "film's Review bin" in result.payload["next"]
    assert fixture.start.call_args.kwargs["render_budget"] == 9
    assert result.receipts[0]["budget_reserved"] == 2
    turns = [{"state": "accepted", "receipts": result.receipts}] * 2
    assert jobs_reserved(turns) == 2
    fixture.context.document.plan.steps[0].receipts = result.receipts
    assert step_jobs_left(fixture.context) == 0


@pytest.mark.parametrize(
    "problem",
    ["unconfirmed", "grant", "target", "scope", "cancelled", "gaps", "running", "user_paused"],
)
def test_run_rechecks_authority_and_fresh_state(fixture, problem):
    if problem == "unconfirmed":
        fixture.state["characters"][0]["target"]["state"] = "proposed"
    elif problem == "grant":
        fixture.context.document.plan.grant.step_kinds = ["edit"]
    elif problem == "target":
        fixture.context.document.plan.steps[0].target = "elsewhere"
    elif problem == "scope":
        fixture.context.state.recasts = []
    elif problem == "cancelled":
        fixture.context.cancelled = lambda: True
    elif problem == "running":
        fixture.state["run"]["state"] = "running"
    elif problem == "user_paused":
        fixture.state["run"].update(state="paused", reason="Paused by the user.")
    else:
        fixture.state["shots"][0]["plan"]["gaps"] = [{"code": "identity_conflict"}]
    result = tools.StartRecastRun().run(fixture.context, value(range={"start": 0, "end": 50}))
    assert not result.ok
    fixture.start.assert_not_called()


def test_proposals_cost_nothing_pin_images_and_require_character_resolution(fixture):
    context = fixture.context
    context.document.plan = None
    context.jobs_limit = 0
    fixture.state["characters"][0]["target"] = None
    context.state.recasts = [tools.compact(fixture.state)]
    tool = tools.ProposeCast()
    schema = tool.narrow(context)
    assert schema["properties"]["asset_id"] == {"enum": ["portrait"]}
    with pytest.raises(ApplicationError):
        parse_action(
            {
                "tool": tool.name,
                "input": {
                    "recast_id": "film-recast",
                    "character_id": "c01",
                    "asset_id": "invented",
                },
            },
            [(tool, schema)],
        )
    supplied = tool.Input(recast_id="film-recast", character_id="c01", asset_id="portrait")
    assert tool.run(context, supplied).ok
    assert fixture.propose.call_args.kwargs["expected_revision"] == 3
    fixture.propose.reset_mock()
    fixture.state["characters"].append(
        {**fixture.state["characters"][0], "id": "c02", "label": "Lookalike"}
    )
    context.latest_user_message = "Replace one of these two similar people."
    assert tool.run(context, supplied).payload["status"] == "choose_character"
    fixture.propose.assert_not_called()
    context.latest_user_message = "The Navigator, c01."
    assert tool.run(context, supplied).ok


def test_blind_read_catalog_plan_costs_and_context(fixture):
    context = fixture.context
    names = {t.name for t, _ in applicable_tools(context)}
    assert {"read_recast", "start_recast_run"} <= names
    assert (
        not {
            "confirm_target",
            "choose_version",
            "start_conform",
            "confirm_appearance",
            "reject_appearance",
            "clear_appearance",
        }
        & names
    )
    result = tools.ReadRecast().run(context, tools.ReadRecast.Input(recast_id="film-recast"))
    assert "conforms" not in result.payload and "shots" not in result.payload
    assert "never exposed" not in str(result)
    assert not check_plan_costs(context, [context.document.plan.steps[0]])
    assert "film-recast" in render_view(context.state, "off", 0, 2)
    context.state.recasts = []
    context.latest_user_message = "Describe the light."
    assert tools.ProposeCast().narrow(context) is None


def test_final_costs_use_policy_and_shot_order_not_priority(fixture):
    shots, estimate = tools.estimate_run(
        fixture.state, mode="final", frame_range={"start": 0, "end": 50}
    )
    assert estimate["renders"] == 4
    fixture.state["shots"].reverse()
    result = tools.StartRecastRun().run(fixture.context, value())
    assert result.payload["suggested_range"] == {"start": 0, "end": 50}
    assert len(shots) == 1


def test_recast_read_satisfies_only_its_source_attachment(fixture):
    from gaiavfx.application.harness.history import unread_attachments

    context = fixture.context
    context.unread_attachments = ["film", "portrait"]
    names = {tool.name for tool, _ in applicable_tools(context)}
    assert "read_recast" in names and "start_recast_run" not in names
    result = tools.ReadRecast().run(context, tools.ReadRecast.Input(recast_id="film-recast"))
    assert result.ok
    turns = [
        {
            "role": "human",
            "state": "accepted",
            "action": {"attachments": [{"asset_id": "film"}, {"asset_id": "portrait"}]},
        },
        {
            "role": "agent",
            "state": "accepted",
            "action": {"tool": "read_recast"},
            "result": result.model_dump(),
            "receipts": result.receipts,
        },
    ]
    assert unread_attachments(turns) == ["portrait"]


@pytest.mark.parametrize("mode,renders", [("draft", 2), ("final", 4)])
def test_harness_and_stage_share_bridge_seed_estimate(fixture, mode, renders):
    from gaiavfx.application.recast import _estimate

    state = fixture.state
    state["shots"] = state["shots"][:1]
    shot = state["shots"][0]
    shot["end"] = 175
    shot["plan"]["windows"] = [
        {"start": start, "end": start + 73, "length": 73} for start in (0, 51, 102)
    ]
    shot["plan"]["renders_per_window"] = renders
    shot["plan"]["_handoff"] = {
        "kind": "bridge",
        "joins": [{"join": 1, "kind": "bridge"}, {"join": 2, "kind": "bridge"}],
    }
    _, harness = tools.estimate_run(state, mode=mode)
    stage = _estimate(
        {"run_mode": mode, "run_range_start": None, "run_range_end": None}, state["shots"], []
    )
    seconds = (renders * 3 + 1 + (1 if mode == "draft" else renders)) * 280
    for estimate in (harness, stage):
        assert estimate["gpu_seconds_low"] == int(seconds * 0.7)
        assert estimate["gpu_seconds_high"] == int(seconds * 1.3)


def test_appearance_tool_requires_user_naming_even_for_one_character(fixture, monkeypatch):
    propose = Mock(return_value=fixture.state)
    monkeypatch.setattr(tools, "propose_appearance", propose)
    tool = tools.ProposeAppearance()
    value = tool.Input(
        recast_id="film-recast",
        shot_id="s0",
        analysis_operation_id="analysis",
        track_id="back",
        character_id="c01",
        evidence="Continuity from adjacent window.",
    )
    assert tool.narrow(fixture.context)["properties"]["character_id"] == {"enum": ["c01"]}
    fixture.context.latest_user_message = "Who is seen from behind?"
    assert tool.run(fixture.context, value).payload["status"] == "choose_character"
    propose.assert_not_called()
    fixture.context.latest_user_message = "The Navigator is seen from behind."
    assert tool.run(fixture.context, value).ok
    assert propose.call_args.kwargs["proposed_by"] == "agent"
    assert propose.call_args.kwargs["user_message"] == fixture.context.latest_user_message
    assert propose.call_args.kwargs["expected_revision"] == 3
    fixture.state["characters"][0]["hidden"] = True
    assert not tool.run(fixture.context, value).ok
