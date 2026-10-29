"""E2e run 5: a prepare step offered every edit tool and the session died on the schema ceiling."""

import json

import harness_support
from test_harness_round5 import executing

from gaiavfx.application.harness import context as harness_context
from gaiavfx.application.harness.actions import action_schema
from gaiavfx.application.harness.context import SCHEMA_CEILING, estimate
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.session import AgentSession, PlanStep
from gaiavfx.application.harness.tools import OVERFLOW_ORDER, applicable_tools
from gaiavfx.modules.agent.service import AgentStore

world = harness_support.world


def prepare_context(work, setup):
    harness = Harness(work.db, AgentStore(), None, work.settings)
    row = harness.open(setup["movie_id"])
    doc = AgentSession.model_validate(row["document"])
    doc.plan = executing(
        PlanStep(kind="edit", title="Declare the relation", target=setup["cut_id"], status="done"),
        PlanStep(kind="prepare", title="Prepare the reverse", target=setup["cut_id"], jobs=3),
        PlanStep(kind="render", title="Render segment 2", jobs=2),
        PlanStep(kind="review", title="Review takes of segment 2"),
    )
    return harness.context(row, doc, [], "prepare-step")


def names(tools):
    return {tool.name for tool, _ in tools}


def test_a_prepare_step_offers_only_the_edits_that_supply_preparation_inputs(world):
    work, setup = world
    tools = applicable_tools(prepare_context(work, setup))
    assert {"edit_place", "edit_phase", "edit_cut", "prepare_cut"} <= names(tools)
    assert not {"draft_storyline", "edit_segment"} & names(tools)
    size = estimate(json.dumps(action_schema(tools), separators=(",", ":")))
    assert size <= SCHEMA_CEILING


def test_an_overflowing_catalog_drops_tools_in_order_instead_of_failing(world, monkeypatch):
    work, setup = world
    ctx = prepare_context(work, setup)
    full = applicable_tools(ctx)
    full_size = estimate(json.dumps(action_schema(full), separators=(",", ":")))
    # A ceiling just below the current catalog forces the fallback.
    monkeypatch.setattr(harness_context, "SCHEMA_CEILING", full_size - 1)
    reduced = applicable_tools(ctx)
    dropped = names(full) - names(reduced)
    assert dropped and dropped <= set(OVERFLOW_ORDER)
    # The step's own producer, the plan tools and the human tools always stay.
    assert {"prepare_cut", "plan_step", "propose_plan", "ask_human"} <= names(reduced)
    first = next(name for name in OVERFLOW_ORDER if name in names(full))
    assert first in dropped
