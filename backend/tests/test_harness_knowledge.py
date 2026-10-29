"""Conversation knowledge is packaged data, loaded once and within the head budget."""

import ast
from pathlib import Path
from types import SimpleNamespace
from typing import get_args

from gaiavfx.application.harness.context import SYSTEM_CEILING, estimate
from gaiavfx.application.harness.knowledge import load, unsupported_head
from gaiavfx.application.harness.prompt import ASK_HUMAN, BOARD_WRITE, SAY, SYSTEM
from gaiavfx.application.harness.session import StepKind
from gaiavfx.application.harness.tools import compact_schema
from gaiavfx.application.harness.tools.plan import PLAN_TEXT, PlanStepTool, ProposePlan
from gaiavfx.infrastructure.assistant import grammar_schema


def test_replacement_knowledge_is_available_before_analysis_without_growing_ordinary_context():
    from gaiavfx.application.harness.knowledge import cinematography_head, knowledge_head

    context = SimpleNamespace(
        state=SimpleNamespace(people_analyses=[], assets=[]),
        attachments=[{"kind": "video"}],
        latest_user_message="Replace the performer with a cast member.",
    )
    rules = load()["cinematography"]["replacement"]["rules"]
    assert estimate(rules) <= 145
    assert knowledge_head(context) == cinematography_head() + "\n" + rules
    context.latest_user_message = "Plan camera coverage."
    assert knowledge_head(context).strip() == cinematography_head()
    context.attachments = []
    context.state.assets = [{"kind": "video"}]
    context.latest_user_message = "Replace a person."
    assert knowledge_head(context).strip() == cinematography_head()
    context.state.people_analyses = [{"id": "analysis"}]
    assert knowledge_head(context) == cinematography_head() + "\n" + rules


def test_recast_guidance_is_bounded_and_only_appended_for_recast_context():
    from gaiavfx.application.harness.knowledge import cinematography_head, knowledge_head

    context = SimpleNamespace(
        state=SimpleNamespace(recasts=[{"id": "recast"}], people_analyses=[], assets=[]),
        attachments=[],
        latest_user_message="Continue the plan.",
    )
    replacement = load()["cinematography"]["replacement"]
    assert estimate(replacement["recast"]) <= 145
    assert knowledge_head(context) == "\n".join(
        [cinematography_head(), replacement["rules"], replacement["recast"]]
    )
    context.state.recasts = []
    assert knowledge_head(context) == cinematography_head()


def test_pack_is_cached_and_model_facing_conversation_strings_are_from_it():
    pack = load()
    assert pack is load()
    assert pack["prompts"]["system"]["conversation"] in SYSTEM
    assert SAY == pack["prompts"]["tools"]["say"]
    assert ASK_HUMAN == pack["prompts"]["tools"]["ask_human"]
    assert BOARD_WRITE == pack["prompts"]["tools"]["board_write"]
    assert ProposePlan.description == pack["prompts"]["plan"]["rules"]
    assert PlanStepTool.description == pack["prompts"]["plan"]["step"]
    assert PLAN_TEXT is pack["prompts"]["plan"]
    assert list(get_args(StepKind)) == pack["cinematography"]["vocabulary"]["plan_steps"]
    assert estimate(SYSTEM) <= SYSTEM_CEILING
    assert estimate(unsupported_head()) <= 120
    for entry in pack["cinematography"].get("unsupported", []):
        assert all(entry[key] in unsupported_head() for key in ("request", "reply", "offer"))


def test_plan_title_survives_schema_compaction_and_provider_grammar():
    schema = grammar_schema(compact_schema(ProposePlan.Input.model_json_schema()))
    step = schema["$defs"]["ProposedStep"]
    assert step["properties"]["title"]["type"] == "string"
    assert "title" in step["required"] and "title" not in step


def test_reading_pack_enums_policy_and_all_prompt_sources():
    from gaiavfx.application.harness.knowledge import cinematography_head
    from gaiavfx.application.harness.reading import TEXT, notes_schema
    from gaiavfx.application.harness.tools.reading import (
        AnalyzeReferenceVideo,
        DescribeReference,
    )
    from gaiavfx.modules.direction.application import default_policy

    pack = load()
    vocabulary = pack["cinematography"]["vocabulary"]
    image = notes_schema("image")["properties"]
    video = notes_schema("video")["properties"]
    shot = video["shots"]["items"]["properties"]
    assert image["shot_size"]["enum"] == shot["shot_size"]["enum"] == vocabulary["shot_sizes"]
    assert image["camera_height"]["enum"] == vocabulary["camera_heights"]
    assert shot["camera_move"]["enum"] == vocabulary["camera_moves"]
    assert video["suggested_techniques"]["items"]["enum"] == vocabulary["techniques"]
    assert [t["id"] for t in pack["cinematography"]["techniques"]] == [
        t.id for t in default_policy().techniques
    ]
    # The head names every technique with its constraint; a technique with limitations always states one, and
    # the limitations themselves ride with the plan.
    assert all(
        t.title in cinematography_head()
        and t.constraint in cinematography_head()
        and (t.constraint or not t.limitations)
        for t in default_policy().techniques
    )
    assert TEXT is pack["prompts"]["reading"]
    assert (
        DescribeReference.description == TEXT["describe"]
        and AnalyzeReferenceVideo.description == TEXT["analyze"]
    )
    assert all(estimate(TEXT[k]) <= 250 for k in ("image", "video"))
    assert pack["cinematography"]["reading"] == {"frames_max": 6, "video_seconds_max": 60}
    assert pack["cinematography"]["storyline"] == {"default_delivered_frames": 102, "beats_max": 6}
    root = Path(__file__).parents[1] / "src/gaiavfx/application/harness"
    for path in (root / "prompt.py", root / "tools/reading.py", root / "tools/plan.py"):
        # Tool descriptions, prompts and refusals must not acquire embedded prose.
        tree = ast.parse(path.read_text("utf-8"))
        docstrings = {
            id(node.body[0].value)
            for node in ast.walk(tree)
            if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
            and ast.get_docstring(node)
        }
        assert not [
            node.value
            for node in ast.walk(tree)
            if isinstance(node, ast.Constant)
            and isinstance(node.value, str)
            and id(node) not in docstrings
            and len(node.value.split()) > 4
        ], path


POLICY = """schema = 1
[relation]
same_direction_max_degrees = 45.0
reverse_min_degrees = 135.0
[[technique]]
id = "x"
blocks = ["direction_text"]
coverage = ["new"]
status = "default"
limitations = ["A long account of what went wrong in one test."]
"""


def test_a_technique_with_limitations_states_its_constraint_in_one_line():
    import pytest

    from gaiavfx.modules.direction.techniques import parse_policy

    with pytest.raises(ValueError, match="constraint"):
        parse_policy(POLICY)
    assert (
        parse_policy(POLICY + 'constraint = "Short."' + chr(10)).techniques[0].constraint
        == "Short."
    )
