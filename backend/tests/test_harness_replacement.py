"""Replacement grammar, grants, runtime revalidation and honest receipts."""

from contextlib import nullcontext
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from gaiavfx.application.harness.actions import ToolContext, parse_action
from gaiavfx.application.harness.board import Whiteboard
from gaiavfx.application.harness.session import AgentSession, Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.application.harness.tools import replacement as tools
from gaiavfx.application.harness.world import WorldState
from gaiavfx.shared.errors import ApplicationError


@pytest.fixture
def replacement_context(monkeypatch):
    report = {
        "source_asset": "video",
        "shot_start": 0,
        "shot_end": 24,
        "native": [320, 240],
        "tracks": [
            {
                "id": "track",
                "concept": "person",
                "box": {"x0": 30, "y0": 20, "x1": 130, "y1": 220},
                "presence": ["present"] * 24,
            }
        ],
    }
    row = {"operation_id": "analysis", "state": "succeeded", "report": report}
    state = WorldState(
        movie={"id": "movie"},
        assets=[{"id": "video", "kind": "video"}],
        cast=[{"id": "subject", "name": "Ivara", "identity_asset_id": "identity"}],
        people_analyses=[tools.analysis_view(row)],
    )
    document = AgentSession(
        plan_mode=True,
        plan=Plan(
            id="plan",
            revision=1,
            goal="Replacement",
            proposed_turn=0,
            state="executing",
            budget_jobs=2,
            steps=[
                PlanStep(kind="render", title="Replace performer", target="analysis", jobs=2),
                PlanStep(kind="review", title="Review candidates"),
            ],
            grant=PlanGrant(
                budget_jobs=2,
                step_kinds=["render", "review"],
                approved_turn=1,
                reserved_at_approval=0,
            ),
        ),
    )
    context = ToolContext(
        db=SimpleNamespace(transaction=nullcontext),
        session_id="session",
        movie_id="movie",
        scope_kind="movie",
        scope_id="movie",
        board=Whiteboard(),
        view="analysis",
        turn=1,
        turn_id="turn",
        state=state,
        document=document,
        jobs_limit=2,
        attachments=[{"id": "video", "kind": "video"}],
    )
    monkeypatch.setattr(tools, "people_for_asset", lambda *a, **k: [row])
    monkeypatch.setattr(
        tools.operations,
        "operation",
        lambda *a: {
            "id": "analysis",
            "kind": "person_analysis",
            "budget_reserved": 0,
            "request": {"source_asset": "video"},
        },
    )
    monkeypatch.setattr(tools.operations, "recent", lambda *a, **k: [])
    preview = Mock(return_value={"gaps": []})
    start = Mock(
        return_value={"id": "replacement", "kind": "character_replacement", "budget_reserved": 2}
    )
    monkeypatch.setattr(tools, "replacement_plan", preview)
    monkeypatch.setattr(tools, "start_replacement", start)
    return SimpleNamespace(context=context, row=row, preview=preview, start=start)


def value(**changes):
    return tools.ReplaceCharacter.Input(
        analysis_id="analysis",
        shot=1,
        performers=[{"track_index": 1, "subject_id": "subject"}],
        **changes,
    )


def test_replacement_uses_render_grant_and_returns_only_operation_receipt(replacement_context):
    fixture = replacement_context
    result = tools.ReplaceCharacter().run(fixture.context, value())
    assert result.ok and result.waits_on == "replacement"
    assert result.receipts == [
        {
            "kind": "operation",
            "id": "replacement",
            "operation_kind": "character_replacement",
            "budget_reserved": 2,
        }
    ]
    assert fixture.start.call_args.kwargs["renders"] == 2
    request = fixture.start.call_args.args[1]
    assert request.performers[0].target_asset == "identity"
    assert request.performers[0].selected_by == "user"
    assert request.performers[0].action is None
    assert "seed" not in result.payload


@pytest.mark.parametrize(
    "problem", ["analysis", "identity", "budget", "unresolved", "unconfirmed", "grant", "target"]
)
def test_runtime_refuses_before_application_submission(replacement_context, problem):
    fixture = replacement_context
    context = fixture.context
    if problem == "analysis":
        context.state.people_analyses = []
    elif problem == "identity":
        context.state.cast[0]["identity_asset_id"] = None
    elif problem == "budget":
        context.jobs_reserved = 2
    elif problem == "unresolved":
        fixture.row["report"]["tracks"][0]["presence"][5] = "unresolved"
    elif problem == "unconfirmed":
        fixture.row["report"]["tracks"].append(deepcopy(fixture.row["report"]["tracks"][0]))
    elif problem == "grant":
        context.document.plan.grant.step_kinds = ["edit"]
    else:
        context.document.plan.steps[0].target = "other-analysis"
    result = tools.ReplaceCharacter().run(context, value())
    assert not result.ok
    fixture.preview.assert_not_called()
    fixture.start.assert_not_called()
    if problem in {"unresolved", "unconfirmed"}:
        assert "ask_human" in result.discrepancy


def test_schema_pins_every_replacement_choice_and_rejects_appearance(replacement_context):
    context = replacement_context.context
    tool = tools.ReplaceCharacter()
    schema = tool.narrow(context)
    for changes in (
        {"appearance": "red hair"},
        {"renders": 3},
        {"analysis_id": "invented"},
        {"performers": [{"track_index": 2, "subject_id": "subject"}]},
        {"performers": [{"track_index": 1, "subject_id": "invented"}]},
        {"context_boxes": [20]},
    ):
        raw = {
            "analysis_id": "analysis",
            "shot": 1,
            "renders": 2,
            "performers": [{"track_index": 1, "subject_id": "subject"}],
            **changes,
        }
        with pytest.raises(ApplicationError):
            parse_action({"tool": tool.name, "input": raw}, [(tool, schema)])


def test_uncertain_tracks_offer_human_instead_of_replacement(replacement_context):
    fixture = replacement_context
    fixture.row["report"]["tracks"][0]["presence"][0] = "unresolved"
    fixture.context.state.people_analyses = [tools.analysis_view(fixture.row)]
    names = {tool.name for tool, _ in applicable_tools(fixture.context)}
    assert "replace_character" not in names and "ask_human" in names


def test_analysis_needs_no_grant_costs_zero_and_waits(replacement_context, monkeypatch):
    fixture = replacement_context
    context = fixture.context
    context.document.plan = None
    context.jobs_limit = 0
    context.unread_attachments = ["video"]
    monkeypatch.setattr(tools, "people_for_asset", lambda *a, **k: [])
    monkeypatch.setattr(
        tools.MediaLibrary, "asset_info", lambda *a: {"info": {"frame_rate": "24", "duration": 1}}
    )
    start = Mock(return_value={"id": "analysis", "kind": "person_analysis", "budget_reserved": 0})
    monkeypatch.setattr(tools, "start_people_analysis", start)
    tool = tools.AnalyzePeople()
    result = tool.run(context, tool.Input(asset_id="video"))
    assert result.ok and result.waits_on == "analysis"
    assert result.receipts[0]["budget_reserved"] == 0
    assert start.call_args.args[2:4] == (0, 24)


def test_analysis_limits_and_concepts_never_reach_render(replacement_context, monkeypatch):
    context = replacement_context.context
    monkeypatch.setattr(
        tools.MediaLibrary, "asset_info", lambda *a: {"info": {"frame_rate": "24", "duration": 20}}
    )
    start = Mock()
    monkeypatch.setattr(tools, "start_people_analysis", start)
    tool = tools.AnalyzePeople()
    result = tool.run(context, tool.Input(asset_id="video"))
    assert result.payload["gaps"][0]["code"] == "untested_length"
    bad = tool.run(context, tool.Input(asset_id="video", concepts=["please replace everyone"]))
    assert not bad.ok
    start.assert_not_called()


def test_claim_requires_completed_receipt(replacement_context):
    context = replacement_context.context
    assert tools.replacement_claim_refusal(context, "The replacement is ready.")
    assert tools.replacement_claim_refusal(context, "I swapped Ivara in")
    assert not tools.replacement_claim_refusal(context, "I replaced the plate in the clip")
    context.report_turns = [
        {"state": "accepted", "receipts": [{"id": "op", "operation_kind": "character_replacement"}]}
    ]
    context.state.operations = [
        {"id": "op", "kind": "character_replacement", "state": "running", "candidate_count": 0}
    ]
    assert tools.replacement_claim_refusal(context, "I replaced the performer.")
    context.state.operations[0].update(state="succeeded", candidate_count=1)
    assert not tools.replacement_claim_refusal(context, "The replacement is ready in Review.")
    assert not tools.replacement_claim_refusal(context, "I will propose a replacement plan.")


def test_compact_analysis_never_exposes_masks_or_provenance(replacement_context):
    row = deepcopy(replacement_context.row)
    row["report"]["tracks"][0].update(mask_artifact="secret-mask", seed=123, prompt="private")
    result = tools.analysis_view(row)
    assert "secret-mask" not in str(result) and "private" not in str(result)
    assert result["contact_sheet"]["boxes"][0]["track_index"] == 1


def test_async_analysis_completion_satisfies_read_and_preserves_cut_gap(replacement_context):
    from gaiavfx.application.harness.history import references_read

    operation = {
        "id": "analysis",
        "kind": "person_analysis",
        "state": "succeeded",
        "request": {"source_asset": "video"},
    }
    result = tools.completed_analysis(None, operation, object())
    turn = {"role": "system", "state": "accepted", "action": {}, "result": result}
    assert references_read([turn])["video"]["tracks"][0]["track_index"] == 1
    operation.update(
        state="failed", jobs=[{"error": "Source contains a cut at relative frames [5]"}]
    )
    result = tools.completed_analysis(None, operation, object())
    assert result["payload"]["gaps"][0]["code"] == "cross_cut"
