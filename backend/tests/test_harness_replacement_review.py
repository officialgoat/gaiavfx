"""Production-shaped tracks, session scope, confirmation, retries and bounded schemas."""

import json
from copy import deepcopy
from unittest.mock import Mock

import pytest
from test_harness_replacement import replacement_context as replacement_context  # noqa: PLC0414
from test_harness_replacement import value

from gaiavfx.application.harness.actions import action_schema, parse_action
from gaiavfx.application.harness.context import SCHEMA_CEILING, estimate
from gaiavfx.application.harness.replacement_context import user_confirmed
from gaiavfx.application.harness.tools import applicable_tools, compact_schema
from gaiavfx.application.harness.tools import replacement as tools
from gaiavfx.application.harness.tools.plan import ProposePlan
from gaiavfx.modules.direction.replacement import validate_selector
from gaiavfx.shared.errors import ApplicationError


def add_person(fixture):
    track = deepcopy(fixture.row["report"]["tracks"][0])
    track.update(id="second", box={"x0": 40, "y0": 20, "x1": 140, "y1": 220})
    fixture.row["report"]["tracks"].append(track)
    fixture.context.state.people_analyses = [tools.analysis_view(fixture.row)]


def confirmation(fixture):
    return [
        {
            "role": "agent",
            "state": "accepted",
            "action": {"tool": "analyze_people"},
            "result": {"payload": tools.analysis_view(fixture.row)},
        },
        {"role": "agent", "state": "accepted", "action": {"tool": "ask_human"}},
        {"role": "human", "state": "accepted", "action": {"message": "Person 1 and person 2."}},
    ]


@pytest.mark.parametrize("confirmed", [False, True])
def test_multiple_people_withheld_until_report_question_and_answer(replacement_context, confirmed):
    fixture = replacement_context
    add_person(fixture)
    assert any(g["code"] == "choose_performer" for g in tools.analysis_view(fixture.row)["gaps"])
    fixture.context.report_turns = confirmation(fixture) if confirmed else []
    schema = tools.ReplaceCharacter().narrow(fixture.context)
    assert (schema is not None) is confirmed
    result = tools.ReplaceCharacter().run(fixture.context, value())
    assert result.ok is confirmed
    if confirmed:
        assert fixture.start.call_args.args[1].performers[0].selected_by == "user"
    else:
        fixture.start.assert_not_called()


@pytest.mark.parametrize(
    "case", ["wrong_analysis", "rejected_question", "before_report", "no_answer"]
)
def test_confirmation_is_ordered_and_bound_to_analysis(replacement_context, case):
    turns = confirmation(replacement_context)
    if case == "wrong_analysis":
        turns[0]["result"]["payload"]["id"] = "other"
    elif case == "rejected_question":
        turns[1]["state"] = "rejected"
    elif case == "before_report":
        turns = [turns[1], turns[2], turns[0]]
    else:
        turns.pop()
    assert not user_confirmed(turns, "analysis")


def test_pair_in_one_third_has_distinct_neutral_positions(replacement_context):
    fixture = replacement_context
    add_person(fixture)
    fixture.context.report_turns = confirmation(fixture)
    tool = tools.ReplaceCharacter()
    result = tool.run(
        fixture.context,
        tool.Input(
            analysis_id="analysis",
            performers=[{"track_index": i, "subject_id": "subject"} for i in (1, 2)],
        ),
    )
    assert result.ok, result
    performers = fixture.start.call_args.args[1].performers
    assert {p.position for p in performers} == {
        "on the left of the pair",
        "on the right of the pair",
    }
    for performer in performers:
        validate_selector(performer.role, performer.position)


@pytest.mark.parametrize("budget,expected", [(2, 2), (6, 4)])
def test_plan_default_before_approval_uses_configured_budget(replacement_context, budget, expected):
    context = replacement_context.context
    context.document.plan = None
    context.jobs_limit = context.jobs_reserved = 0
    context.settings["agent_budget_jobs"] = budget
    tool = ProposePlan()
    result = tool.run(
        context,
        tool.Input(
            goal="Replace the performer",
            steps=[
                {"kind": "render", "title": "Replace", "target": "analysis"},
                {"kind": "review", "title": "Review"},
            ],
        ),
    )
    assert result.ok, result
    assert context.document.plan.steps[0].jobs == expected
    assert context.document.plan.grant is None


def test_analysis_tools_do_not_scan_unrelated_library(replacement_context, monkeypatch):
    context = replacement_context.context
    context.attachments = []
    context.state.people_analyses = []
    assert tools.AnalyzePeople().narrow(context) is None
    context.latest_user_message = "Swap the performer."
    assert tools.AnalyzePeople().narrow(context)["properties"]["asset_id"]["enum"] == ["video"]
    read = Mock(side_effect=ApplicationError("report unavailable", 409))
    monkeypatch.setattr(tools, "people_for_asset", read)
    tools.refresh_analyses(None, context.state, [], object(), [])
    read.assert_not_called()
    turns = [
        {
            "state": "accepted",
            "role": "agent",
            "receipts": [{"kind": "people_analysis", "id": "analysis", "asset_id": "video"}],
        }
    ]
    tools.refresh_analyses(None, context.state, [], object(), turns)
    read.assert_called_once()
    context.latest_user_message = "Continue."
    context.report_turns = turns
    assert tools.AnalyzePeople().narrow(context) is not None


def test_analysis_schema_is_one_bounded_union_with_runtime_per_analysis_validation(
    replacement_context,
):
    fixture = replacement_context
    context = fixture.context
    context.document.plan_mode = False
    context.autonomy = "render"
    first = context.state.people_analyses[0]
    for i in range(1, 12):
        analysis = deepcopy(first)
        analysis["id"] = f"analysis-{i}"
        analysis["tracks"][0]["track_index"] = i + 1
        context.state.people_analyses.append(analysis)
    tool = tools.ReplaceCharacter()
    schema = tool.narrow(context)
    assert "oneOf" not in schema and len(schema["properties"]["analysis_id"]["enum"]) == 8
    assert estimate(json.dumps(action_schema([(tool, compact_schema(schema))]))) < SCHEMA_CEILING
    _, supplied = parse_action(
        {
            "tool": tool.name,
            "input": {
                "analysis_id": "analysis",
                "performers": [{"track_index": 2, "subject_id": "subject"}],
            },
        },
        [(tool, schema)],
    )
    assert not tool.run(context, supplied).ok
    fixture.start.assert_not_called()
    assert any(t.name == tool.name for t, _ in applicable_tools(context))


@pytest.mark.parametrize(
    "error,code",
    [
        ("Source contains a cut at relative frames [12]", "cross_cut"),
        ({"code": "cross_cut"}, "cross_cut"),
        ("Execution failed", "analysis_failed"),
    ],
)
def test_cut_diagnostic_is_specific_and_missing_report_is_guarded(
    replacement_context, monkeypatch, error, code
):
    monkeypatch.setattr(tools, "people_for_asset", lambda *a, **k: [])
    result = tools.completed_analysis(
        None,
        {
            "id": "analysis",
            "kind": "person_analysis",
            "state": "failed",
            "request": {"source_asset": "video"},
            "jobs": [{"error": error}],
        },
        object(),
    )
    assert result["payload"]["gaps"][0]["code"] == code


def test_failed_analysis_can_retry_with_fresh_request_key(replacement_context, monkeypatch):
    fixture = replacement_context
    fixture.row["state"] = "failed"
    fixture.context.turn_id = "retry-turn"
    monkeypatch.setattr(
        tools.MediaLibrary, "asset_info", lambda *a: {"info": {"frame_rate": "24", "duration": 1}}
    )
    start = Mock(return_value={"id": "retry", "kind": "person_analysis", "budget_reserved": 0})
    monkeypatch.setattr(tools, "start_people_analysis", start)
    result = tools.AnalyzePeople().run(fixture.context, tools.AnalyzePeople.Input(asset_id="video"))
    assert result.ok and result.waits_on == "retry"
    assert start.call_args.kwargs["request_key"] == "agent:retry-turn"
    assert start.call_args.kwargs["request_key"] != "agent:original-failed-turn"


def test_scope_limit_requires_three_resolved_people(replacement_context):
    fixture = replacement_context
    assert not tools.analysis_view(fixture.row)["scope_limits"]
    add_person(fixture)
    assert not tools.analysis_view(fixture.row)["scope_limits"]
    add_person(fixture)
    assert tools.analysis_view(fixture.row)["scope_limits"][0]["code"] == "too_many_performers"
    fixture.row["report"]["tracks"][-1]["presence"][0] = "unresolved"
    assert not tools.analysis_view(fixture.row)["scope_limits"]


@pytest.mark.parametrize("seen,confirmed", [(["analysis"], True), (["other"], False), ([], False)])
def test_question_asked_with_analysis_in_context_confirms(seen, confirmed):
    # An analysis made before the session is in context without an analyze_people turn.
    ask = {"tool": "ask_human"}
    payload = {"question": "Which person?", "options": []} | ({"analyses": seen} if seen else {})
    turns = [
        {"role": "agent", "state": "accepted", "action": ask, "result": {"payload": payload}},
        {"role": "human", "state": "accepted", "action": {"message": "Person 2."}},
    ]
    assert user_confirmed(turns, "analysis") is confirmed


def test_ask_human_records_the_analyses_in_view(replacement_context):
    from gaiavfx.application.harness.tools.talk import AskHuman

    tool = AskHuman()
    result = tool.run(replacement_context.context, tool.Input(question="Which person?"))
    assert result.payload["analyses"] == ["analysis"]


@pytest.mark.parametrize("ready", [1, 2])
def test_untargeted_replacement_step_gets_the_single_analysis(replacement_context, ready):
    context = replacement_context.context
    context.document.plan = None
    context.jobs_limit = context.jobs_reserved = 0
    if ready == 2:
        context.state.people_analyses = [
            *context.state.people_analyses,
            {**context.state.people_analyses[0], "id": "analysis-2"},
        ]
    tool = ProposePlan()
    result = tool.run(
        context,
        tool.Input(
            goal="Replace the performer",
            steps=[
                {"kind": "render", "title": "Replace the performer with the cast member"},
                {"kind": "review", "title": "Review"},
            ],
        ),
    )
    if ready == 1:
        assert result.ok, result
        assert context.document.plan.steps[0].target == "analysis"
    else:
        assert not result.ok and "replacement_target" in result.discrepancy


def test_answer_without_replace_word_still_targets_the_read_analysis(replacement_context):
    # The user's answer ("Person 1 and person 2") does not repeat the request; the session does.
    context = replacement_context.context
    context.document.plan = None
    context.jobs_limit = context.jobs_reserved = 0
    context.state.people_analyses = [
        *context.state.people_analyses,
        {**context.state.people_analyses[0], "id": "analysis-2"},
    ]
    context.latest_user_message = "Person 1 and person 2."
    context.report_turns = [
        {"role": "human", "state": "accepted", "action": {"message": "Swap the two attendants."}},
        {
            "role": "agent",
            "state": "accepted",
            "action": {"tool": "analyze_people"},
            "result": {"payload": {"id": "analysis-2", "tracks": []}},
        },
    ]
    tool = ProposePlan()
    result = tool.run(
        context,
        tool.Input(
            goal="Two performers",
            steps=[
                {"kind": "render", "title": "Render both performers"},
                {"kind": "review", "title": "Review"},
            ],
        ),
    )
    assert result.ok, result
    assert context.document.plan.steps[0].target == "analysis-2"
