"""The per-state catalog: only applicable tools enter the action grammar."""

import json

from gaiavfx.application.harness.tools.board import BoardWrite
from gaiavfx.application.harness.tools.candidates import ReadCandidates
from gaiavfx.application.harness.tools.common import tool_schema
from gaiavfx.application.harness.tools.draft import DraftStoryline
from gaiavfx.application.harness.tools.edits import (
    EditCast,
    EditCut,
    EditPhase,
    EditPlace,
    EditSegment,
)
from gaiavfx.application.harness.tools.picks import PickProvisional
from gaiavfx.application.harness.tools.plan import PlanStepTool, ProposePlan
from gaiavfx.application.harness.tools.prepare import PrepareCut
from gaiavfx.application.harness.tools.reading import AnalyzeReferenceVideo, DescribeReference
from gaiavfx.application.harness.tools.reads import InspectCut, ReadMovie, ReadSegment
from gaiavfx.application.harness.tools.recast import (
    ProposeAppearance,
    ProposeCast,
    ReadRecast,
    StartRecastRun,
)
from gaiavfx.application.harness.tools.render import RenderCandidates
from gaiavfx.application.harness.tools.replacement import AnalyzePeople, ReplaceCharacter
from gaiavfx.application.harness.tools.sets import (
    BuildSetFromTake,
    FillSetMore,
    LinkSet,
    MarkSetIssue,
    ReadSet,
    SaveSetView,
)
from gaiavfx.application.harness.tools.talk import AskHuman, Say

DEFAULT_TOOLS = (
    ReadMovie(),
    ReadSegment(),
    InspectCut(),
    EditCut(),
    EditPlace(),
    EditSegment(),
    EditPhase(),
    EditCast(),
    PrepareCut(),
    ReadCandidates(),
    RenderCandidates(),
    AnalyzePeople(),
    ReplaceCharacter(),
    ReadRecast(),
    ProposeCast(),
    ProposeAppearance(),
    StartRecastRun(),
    ReadSet(),
    MarkSetIssue(),
    SaveSetView(),
    LinkSet(),
    BuildSetFromTake(),
    FillSetMore(),
    PickProvisional(),
    BoardWrite(),
    AskHuman(),
    Say(),
    ProposePlan(),
    PlanStepTool(),
    DescribeReference(),
    AnalyzeReferenceVideo(),
    DraftStoryline(),
)


# Dropped first when a state's catalog would exceed the schema ceiling. A failed
# context assembly ends the session, so a smaller grammar is always preferable.
OVERFLOW_ORDER = (
    "fill_set_more",
    "build_set_from_take",
    "save_set_view",
    "link_set",
    "mark_set_issue",
    "read_set",
    "propose_appearance",
    "propose_cast",
    "start_recast_run",
    "read_recast",
    "draft_storyline",
    "replace_character",
    "analyze_people",
    "edit_segment",
    "board_write",
    "analyze_reference_video",
    "describe_reference",
    "edit_cast",
    "edit_phase",
    "edit_place",
    "edit_cut",
)


def applicable_tools(context, tools=DEFAULT_TOOLS):
    applicable = [
        (tool, compact_schema(schema))
        for tool in tools
        if (schema := tool_schema(context, tool)) is not None
    ]
    return budgeted_tools(context, applicable)


def budgeted_tools(context, applicable):
    from gaiavfx.application.harness.actions import action_schema
    from gaiavfx.application.harness.context import SCHEMA_CEILING, estimate
    from gaiavfx.application.harness.tools.common import TOOL_KINDS, current_step

    step = current_step(context)
    for name in OVERFLOW_ORDER:
        size = estimate(json.dumps(action_schema(applicable), separators=(",", ":")))
        if size <= SCHEMA_CEILING:
            break
        if step and TOOL_KINDS.get(name) == step.kind:
            continue
        applicable = [(tool, schema) for tool, schema in applicable if tool.name != name]
    return applicable


def compact_schema(value):
    """Drop model display metadata; optional edit fields are omitted, never null writes."""
    if isinstance(value, list):
        return [compact_schema(item) for item in value]
    if not isinstance(value, dict):
        return value
    value = {
        key: {name: compact_schema(rule) for name, rule in item.items()}
        if key in {"properties", "$defs"}
        else compact_schema(item)
        for key, item in value.items()
        if key not in {"title", "default"}
    }
    choices = value.get("anyOf", [])
    if len(choices) == 2 and {"type": "null"} in choices:
        return {
            **{key: item for key, item in value.items() if key != "anyOf"},
            **next(item for item in choices if item != {"type": "null"}),
        }
    # Membership in a finite enum/const already fixes the type. Repeating it in
    # each operation branch adds tokens without narrowing the accepted values.
    if "enum" in value or "const" in value:
        value.pop("type", None)
    # The runtime grammar already omits large string repetition bounds. Python's
    # input model still enforces them; don't spend schema context on ignored rules.
    if value.get("maxLength", 0) > 400:
        value.pop("maxLength")
    return value
