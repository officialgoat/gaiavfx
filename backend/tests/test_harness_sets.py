"""Set tools (ADR 0018 §7): sets enter the agent's context when talked about, the agent reads them in words, proposes
issues as itself and saves views only with an edit grant; it never accepts, imports or binds."""

from pathlib import Path

import numpy as np
import pytest
from test_sets import make_bundle

from gaiavfx.application import set_agent, set_bundles, sets
from gaiavfx.application.harness.actions import ToolContext
from gaiavfx.application.harness.board import Whiteboard
from gaiavfx.application.harness.session import AgentSession, Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.application.harness.tools import sets as tools
from gaiavfx.application.harness.tools.common import tool_schema
from gaiavfx.application.harness.view import render_view
from gaiavfx.application.harness.world import WorldState
from gaiavfx.application.workspace import Workspace
from gaiavfx.modules.library.application import LibraryService

MIGRATIONS = Path(__file__).parents[1] / "migrations"


@pytest.fixture
def world(tmp_path):
    root = tmp_path / "library"
    LibraryService(root, MIGRATIONS).create("Sets")
    workspace = Workspace(root)
    set_id = set_bundles.import_bundle(workspace.db, workspace.library, make_bundle(tmp_path))
    yield workspace, set_id
    workspace.db.close()


def context(workspace, *, message="", document=None, sets_rows=None):
    state = WorldState(movie={"id": "movie", "title": "Harbor", "revision": 1})
    if sets_rows is not None:
        state.sets = sets_rows
    return ToolContext(
        db=workspace.db,
        session_id="session",
        movie_id="movie",
        scope_kind="movie",
        scope_id="movie",
        board=Whiteboard(),
        view="",
        turn=1,
        turn_id="turn",
        state=state,
        document=document or AgentSession(plan_mode=False),
        library=workspace.library,
        latest_user_message=message,
    )


def listed(workspace):
    with workspace.db.transaction() as session:
        return set_agent.agent_sets(session, workspace.library)


def test_sets_enter_the_context_only_when_talked_about_or_read_before(world):
    workspace, set_id = world
    state = WorldState(movie={"id": "movie", "title": "Harbor", "revision": 1})
    with workspace.db.transaction() as session:
        tools.refresh_sets(session, state, workspace.library, [], "Make the cut warmer.")
        assert state.sets == []
        tools.refresh_sets(session, state, workspace.library, [], "Can you check the shack set?")
        assert [row["name"] for row in state.sets] == ["Tiny shack"]
        later = WorldState(movie={"id": "movie", "title": "Harbor", "revision": 1})
        turns = [{"state": "accepted", "receipts": [{"kind": "set_read", "set_id": set_id}]}]
        tools.refresh_sets(session, later, workspace.library, turns, "And now?")
        assert later.sets and "read_set reads a stage's cameras" in render_view(later, "off", 0, 6)
    assert "provenance" not in str(state.sets) and "artifact" not in str(state.sets)


def test_the_stages_in_scope_and_in_use_come_first_of_at_most_eight(monkeypatch):
    # The Library may hold more stages than the agent reads; the ones its places and turns use must not drop out.
    rows = [
        {
            "id": f"s{n}",
            "name": f"Stage {n}",
            "current_version_id": "v" if n == 9 else None,
            "shown_version": {"state": "accepted" if n == 9 else "proposed", "ordinal": 1},
            "keyframes": 3,
            "open_issues": 0,
        }
        for n in range(12)
    ]
    monkeypatch.setattr(set_agent, "list_sets", lambda session, library: [dict(r) for r in rows])
    listed = set_agent.agent_sets(None, None, first=["s11", "s9", "s11"])
    assert [r["id"] for r in listed] == ["s11", "s9", "s0", "s1", "s2", "s3", "s4", "s5"]
    assert [r["accepted"] for r in listed[:2]] == [False, True]


def test_read_set_gives_words_not_files(world):
    workspace, set_id = world
    ctx = context(workspace, sets_rows=listed(workspace))
    names = {tool.name for tool, _ in applicable_tools(ctx)}
    assert {"read_set", "mark_set_issue", "save_set_view"} <= names
    result = tools.ReadSet().run(ctx, tools.ReadSet.Input(set_id=set_id))
    assert result.ok, result.discrepancy
    payload = result.payload
    assert payload["version"] == "proposed v1 (import)" and payload["current"] is False
    assert [(i["number"], i["author"], i["kind"]) for i in payload["issues"]] == [
        (1, "judge", "floaters"),
        (2, "judge", "other"),
    ]
    assert "can be wrong" in payload["judge_note"]
    # Filling more needs an accepted version; the reading says so before the agent plans it.
    assert payload["fill_more"].startswith("It has no accepted version yet")
    assert result.receipts == [{"kind": "set_read", "set_id": set_id}]
    flat = str(payload)
    assert "sha256" not in flat and "asset" not in flat and "G:" not in flat
    outside = context(workspace, sets_rows=[])
    assert tool_schema(outside, tools.ReadSet()) is None


def test_the_agent_proposes_issues_as_itself_and_names_only_known_cameras(world):
    workspace, set_id = world
    ctx = context(workspace, sets_rows=listed(workspace))
    result = tools.MarkSetIssue().run(
        ctx,
        tools.MarkSetIssue.Input(
            set_id=set_id,
            kind="shape",
            why="The back wall bends where a rectangular room needs a corner.",
            cells=["B2"],
            cameras=["K1"],
        ),
    )
    assert result.ok and result.changed
    agent = [i for i in result.payload["issues"] if i["author"] == "agent"]
    assert agent and agent[0]["status"] == "open" and agent[0]["cameras"] == ["K1"]
    assert result.receipts[0]["issue_id"] == agent[0]["id"]
    refused = tools.MarkSetIssue().run(
        ctx,
        tools.MarkSetIssue.Input(set_id=set_id, kind="hole", why="A hole.", cameras=["K9"]),
    )
    assert not refused.ok and "Unknown cameras" in refused.discrepancy


def test_saving_a_view_is_an_edit_inside_a_grant(world):
    workspace, set_id = world
    planning = AgentSession(
        plan_mode=True,
        plan=Plan(
            id="plan",
            revision=1,
            goal="Frame shots",
            proposed_turn=0,
            state="executing",
            budget_jobs=0,
            steps=[PlanStep(kind="edit", title="Save the door view", target=set_id)],
            grant=PlanGrant(
                budget_jobs=0, step_kinds=["review"], approved_turn=1, reserved_at_approval=0
            ),
        ),
    )
    ctx = context(workspace, sets_rows=listed(workspace), document=planning)
    assert tool_schema(ctx, tools.SaveSetView()) is None
    value = tools.SaveSetView.Input(set_id=set_id, name="Door", camera="K1", heading=90)
    assert not tools.SaveSetView().run(ctx, value).ok
    free = context(workspace, sets_rows=listed(workspace))
    result = tools.SaveSetView().run(free, value)
    assert result.ok and result.payload["views"] == [{"name": "Door", "fov": 50}]
    with workspace.db.transaction() as session:
        view = sets.set_state(session, workspace.library, set_id)["views"][0]
    # The receipt names the view, so the chat can open the workbench at it.
    assert result.receipts == [
        {"kind": "set_view", "set_id": set_id, "view_id": view["id"], "angle_id": None}
    ]
    viewmat = np.asarray(view["viewmat"])
    assert view["author"] == "agent"
    # Level (no roll or pitch), looking along +x (heading 90), standing at K1's centre (the origin here).
    assert np.allclose(viewmat[2, :3], [1, 0, 0], atol=1e-6)
    assert np.allclose(viewmat[1, :3], [0, 1, 0], atol=1e-6)
    assert np.allclose(viewmat[:3, 3], 0, atol=1e-6)
