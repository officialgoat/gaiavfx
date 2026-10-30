"""Measure the whole context for each S7a state, including the actual narrowed schema."""

from dataclasses import replace

import harness_support
import pytest

from gaiavfx.application.harness.actions import action_schema
from gaiavfx.application.harness.board import Whiteboard, write_entry
from gaiavfx.application.harness.context import (
    BOARD_CEILING,
    HEAD_CEILING,
    SCHEMA_CEILING,
    SUMMARY_CEILING,
    SYSTEM_CEILING,
    VIEW_CEILING,
    assemble,
    estimate,
)
from gaiavfx.application.harness.knowledge import (
    cinematography_head,
    knowledge_head,
    unsupported_head,
)
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.prompt import SYSTEM, examples, system_prompt
from gaiavfx.application.harness.scope import state_label
from gaiavfx.application.harness.session import AgentSession, Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.tools import (
    DEFAULT_TOOLS,
    applicable_tools,
    compact_schema,
)
from gaiavfx.application.harness.tools.common import TOOL_KINDS
from gaiavfx.application.harness.view import render_view
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.shared.errors import ApplicationError

world = harness_support.world


def test_authoring_with_existing_stage_and_takes_fits_context(world):
    work, setup = world
    harness = Harness(work.db, AgentStore(), None, work.settings)
    row = harness.open(setup["movie_id"])
    doc = AgentSession.model_validate(row["document"])
    doc.plan_mode = False
    base = harness.context(row, doc, [])
    state = replace(
        base.state,
        segments=[{**segment, "cuts": []} for segment in base.state.segments],
        plans={},
        sets=[
            {"id": "stage", "name": "Bridge", "version": "v2", "accepted": False, "open_issues": 0}
        ],
        operations=[
            {
                "id": "take",
                "kind": "render",
                "state": "succeeded",
                "candidate_count": 1,
                "review_set_id": "review",
                "review_pending": False,
                "ranked": True,
                "segment_id": base.state.segments[0]["id"],
            }
        ],
    )
    ctx = replace(
        base,
        autonomy="off",
        state=state,
        latest_user_message="Author the next continuing segment. Do not replace or duplicate the cast.",
    )
    applicable = applicable_tools(ctx)
    names = {tool.name for tool, _ in applicable}
    assert {"edit_segment", "read_candidates", "read_set", "save_set_view", "link_set"} <= names
    catalog = "\n".join(f"{tool.name}: {tool.describe(ctx)}" for tool, _ in applicable)
    arguments = {
        "system": system_prompt(ctx, applicable),
        "brief": "",
        "catalog": catalog,
        "schema": action_schema(applicable),
        "board": doc.board,
        "view": render_view(state, "off", 0, 6, document=doc),
        "messages": [{"role": "user", "content": ctx.latest_user_message}],
        "context_size": 32768,
        "output": 2400,
        "knowledge": knowledge_head(ctx),
    }
    assembled = assemble(**arguments)
    assert catalog in assembled.messages[0]["content"]
    assert assembled.report["total_reserved"] <= 32768
    # Raising a structural cap must not bypass the actual model-window admission.
    with pytest.raises(ApplicationError, match="context cannot hold"):
        assemble(**(arguments | {"context_size": 4096}))


@pytest.mark.parametrize("message", ["", "Arin walks left to right; prepare the reverse cut."])
def test_budget_per_state(world, message):
    work, setup = world
    harness = Harness(work.db, AgentStore(), None, work.settings)
    row = harness.open(setup["movie_id"])
    doc = AgentSession.model_validate(row["document"])
    doc.plan_mode = False
    base = replace(harness.context(row, doc, []), latest_user_message=message)
    reviewing = replace(
        base.state,
        operations=[
            {
                "id": "review-op",
                "kind": "survey",
                "state": "succeeded",
                "candidate_count": 3,
                "review_set_id": "review-op",
                "review_pending": True,
                "ranked": False,
                "place_id": setup["place_id"],
                "cut_id": None,
            }
        ],
    )
    states = [
        replace(base, autonomy="off"),
        replace(base, autonomy="off", state=replace(base.state, segments=[], plans={})),
        base,
        replace(base, state=reviewing),
    ]
    cut_id = setup["cut_id"]
    ready = replace(
        base.state,
        segment_plans={
            sid: {**plan, "ready": True, "refusals": []}
            for sid, plan in base.state.segment_plans.items()
        },
        plans={
            cut_id: {
                **base.state.plans[cut_id],
                "recommended": {
                    **base.state.plans[cut_id]["recommended"],
                    "ready": True,
                    "actions": [],
                },
            }
        },
    )
    rendering = replace(base, scope_kind="cut", autonomy="render", state=ready)
    pick_state = replace(
        ready,
        segments=[{**s, "continuing_successor": True} for s in ready.segments],
        operations=[
            {
                "id": "render-op",
                "kind": "render",
                "state": "succeeded",
                "candidate_count": 2,
                "review_set_id": "render-op",
                "ranked": True,
                "review_pending": False,
                "place_id": None,
                "cut_id": cut_id,
                "segment_id": ready.segments[0]["id"],
            }
        ],
    )
    picking = replace(rendering, state=pick_state)
    states += [rendering, picking]
    wide = replace(base, state=reviewing)
    # The widest catalog a turn could hold: every tool narrowed in some state, but no build tool. A build tool is in a
    # catalog only inside an approved plan's build step, where step_applicable keeps just the reading tools and the
    # build tools (tools/common.py); that catalog is measured below, as the executing build state.
    all_applicable = [
        (
            tool,
            compact_schema(
                next(tool.narrow(ctx) for ctx in states if tool.narrow(ctx) is not None)
            ),
        )
        for tool in DEFAULT_TOOLS
        if TOOL_KINDS.get(tool.name) != "build"
        and any(tool.narrow(ctx) is not None for ctx in states)
    ]
    planning = replace(base, document=doc.model_copy(update={"plan_mode": True}))
    executing_doc = doc.model_copy(deep=True)
    executing_doc.plan_mode = True
    executing_doc.plan = Plan(
        id="plan",
        revision=1,
        state="executing",
        goal="Prepare the movie",
        proposed_turn=0,
        steps=[
            PlanStep(kind=kind, title=kind, jobs=1 if kind in {"prepare", "render"} else 0)
            for kind in ("edit", "prepare", "render", "pick", "review", "answer")
        ],
        budget_jobs=2,
        grant=PlanGrant(
            budget_jobs=2,
            step_kinds=["edit", "prepare", "render", "pick", "review", "answer"],
            approved_turn=1,
            reserved_at_approval=0,
        ),
    )
    executing = replace(base, document=executing_doc)
    building_doc = executing_doc.model_copy(deep=True)
    building_doc.plan.steps = [
        PlanStep(kind="build", title="build", jobs=6, target=base.state.segments[0]["id"])
    ]
    building_doc.plan.grant.step_kinds = ["build"]
    building = replace(base, document=building_doc)
    states += [planning, executing, building]
    preparing_doc = executing_doc.model_copy(deep=True)
    preparing_doc.plan.steps[0].status = "done"
    # E2e run 5: a prepare step retains its edit grant for missing authored inputs.
    states.append(replace(base, document=preparing_doc))
    # Before the plate is fitted, the catalog must also carry its structural choice.
    states.append(
        replace(
            base,
            document=preparing_doc,
            state=replace(base.state, places=[{**p, "plate": None} for p in base.state.places]),
        )
    )
    plan_widest = [
        *all_applicable,
        next(item for item in applicable_tools(executing) if item[0].name == "plan_step"),
    ]
    reports = []
    for ctx, applicable in [
        *[(ctx, applicable_tools(ctx)) for ctx in states],
        (wide, all_applicable),
        (executing, plan_widest),
    ]:
        assert all(schema is not None for _, schema in applicable)
        label = (
            "synthetic/widest"
            if applicable is all_applicable
            else "synthetic/plan/widest"
            if applicable is plan_widest
            else state_label(ctx, applicable)
        )
        view = render_view(ctx.state, ctx.autonomy, 0, 6, document=ctx.document)
        catalog = "\n".join(f"{tool.name}: {tool.description}" for tool, _ in applicable)
        report = assemble(
            system_prompt(ctx, applicable),
            "Prepare the reverse cut.",
            catalog,
            action_schema(applicable),
            doc.board,
            view,
            [],
            32768,
            output=2400,
            schema_billed=True,
            state=label,
            knowledge=unsupported_head() + "\n" + cinematography_head(),
            summary="s" * 1200,
        ).report
        for key, ceiling in (
            ("system", SYSTEM_CEILING),
            ("head", HEAD_CEILING),
            ("schema", SCHEMA_CEILING),
            ("view", VIEW_CEILING),
            ("board", BOARD_CEILING),
            ("summary", SUMMARY_CEILING),
        ):
            assert report[key] <= ceiling, (label, key, report[key])
        reports.append(report)
        assert estimate(examples(ctx, applicable)) <= 150
        print(
            " | ".join(
                str(report[key])
                for key in (
                    "state",
                    "system",
                    "head",
                    "schema",
                    "branches",
                    "view",
                    "board",
                    "summary",
                    "fixed",
                )
            )
        )
    assert [r["state"] for r in reports[:-2]] == [
        "movie/off/idle",
        "movie/off/drafting",
        "movie/prepare/preparing",
        "movie/prepare/reviewing",
        "cut/render/rendering",
        "cut/render/picking",
        "movie/plan/planning",
        "movie/plan/executing",
        "movie/plan/executing",  # the build step
        "movie/plan/executing",
        "movie/plan/executing",
    ]

    from gaiavfx.application.harness.context import assemble_reading
    from gaiavfx.application.harness.reading import TEXT, notes_schema

    parts = [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,fixture"}}] * 6
    report = assemble_reading(
        SYSTEM,
        TEXT["video"],
        "What techniques suit it?",
        parts,
        notes_schema("video"),
        32768,
        2400,
        schema_billed=True,
    ).report
    assert report["images"] == 6 and report["image_reserve"] == 12288
    assert report["fixed"] <= 32768
    print(
        " | ".join(
            str(report[k])
            for k in ("state", "system", "head", "schema", "images", "image_reserve", "fixed")
        )
    )


def pack(**kwargs):
    options = {
        "system": "system",
        "brief": "",
        "catalog": "say: report",
        "schema": {},
        "board": Whiteboard(),
        "view": "view",
        "messages": [],
        "context_size": 16384,
    }
    return assemble(**(options | kwargs))


def test_n4_examples_follow_modes_and_read_obligations_within_budget(world):
    from gaiavfx.application.harness.prompt import PROMPTS

    work, setup = world
    harness = Harness(work.db, AgentStore(), None, work.settings)
    row = harness.open(setup["movie_id"])
    doc = AgentSession.model_validate(row["document"])
    base = harness.context(row, doc, [])
    for plan_mode in (False, True):
        for unread in (False, True):
            context = replace(
                base,
                document=doc.model_copy(update={"plan_mode": plan_mode}),
                attachments=[{"id": setup["take"], "kind": "video", "name": "take.mp4"}],
                unread_attachments=[setup["take"]] if unread else [],
            )
            applicable = applicable_tools(context)
            system = system_prompt(context, applicable)
            shots = examples(context, applicable)
            assert (PROMPTS["system"]["plan_on"] in system) is plan_mode
            assert (PROMPTS["system"]["plan_off"] in system) is not plan_mode
            for key in ("story_plan", "render_plan"):
                assert (PROMPTS["examples"][key] in shots) is (plan_mode and not unread)
            for key in ("techniques", "declared"):
                assert (PROMPTS["examples"][key] in shots) is not unread
            assert PROMPTS["examples"]["replacement"] in shots
            assert estimate(PROMPTS["examples"]["heading"] + "\n" + shots) <= 150
            report = pack(
                system=system,
                catalog="\n".join(f"{tool.name}: {tool.description}" for tool, _ in applicable),
                schema=action_schema(applicable),
                board=context.board,
                view=context.view,
                knowledge=unsupported_head() + "\n" + cinematography_head(),
                summary="s" * 1200,
                context_size=32768,
                schema_billed=True,
                output=2400,
            ).report
            assert report["system"] <= SYSTEM_CEILING
            assert report["head"] <= HEAD_CEILING
            assert report["schema"] <= SCHEMA_CEILING
    # No video, no replacement example; unauthored relations get no satisfied-request example.
    base.state.cuts[0]["relation_source"] = "unknown"
    shots = examples(base, applicable_tools(base))
    assert PROMPTS["examples"]["replacement"] not in shots
    assert PROMPTS["examples"]["declared"] not in shots


def test_schema_always_measured_reserved_only_when_billed():
    schema = {"type": "object", "properties": {"text": {"type": "string"}}}
    free, billed = pack(schema=schema), pack(schema=schema, schema_billed=True)
    assert free.report["schema"] == billed.report["schema"] > 0
    assert free.report["schema_reserve"] == 0
    assert billed.report["schema_reserve"] == billed.report["schema"]
    assert free.report["turns_budget"] - billed.report["turns_budget"] == billed.report["schema"]


@pytest.mark.parametrize(
    ("section", "kwargs"),
    [
        ("system", {"system": "s" * (SYSTEM_CEILING * 4)}),
        ("head", {"brief": "b" * (HEAD_CEILING * 4)}),
        ("schema", {"schema": {"description": "x" * (SCHEMA_CEILING * 4)}}),
        ("current view", {"view": "v" * (VIEW_CEILING * 4)}),
        ("summary", {"summary": "s" * (SUMMARY_CEILING * 4)}),
    ],
)
def test_overflows_fail_with_section(section, kwargs):
    with pytest.raises(ApplicationError, match=section):
        pack(**kwargs)


def test_board_overflow_newest_history_and_view_last():
    board = Whiteboard()
    for i in range(40):
        write_entry(board, "decisions", "x" * 500, i)
    with pytest.raises(ApplicationError, match="whiteboard"):
        pack(board=board)
    history = [{"role": "user", "content": f"old {i} " + "x" * 100} for i in range(100)]
    history.append({"role": "user", "content": "newest " + "x" * 40000})
    result = pack(messages=history, context_size=6000)
    assert result.report["dropped_turns"] > 0
    assert result.messages[-2]["content"].startswith("newest")
    assert "[truncated]" in result.messages[-2]["content"]
    assert result.messages[-1]["content"] == "Current view:\nview"
    assert estimate(result.messages[-2]["content"]) <= result.report["message_ceiling"]


def test_summary_stays_first_and_preserves_recent_receipts_under_unicode_pressure():
    from gaiavfx.application.harness.summary import conversation_summary

    receipts = [
        {"command": "update_segment", "target_kind": "segment", "target_id": f"target-{i}"}
        for i in range(8)
    ]
    turns = [
        {
            "role": "human",
            "state": "accepted",
            "receipts": [],
            "action": {
                "message": "雷" * 8000,
                "attachments": [{"name": "海" * 120 + str(i)} for i in range(6)],
            },
        },
        {"role": "agent", "state": "accepted", "receipts": receipts},
    ]
    operations = [
        {"id": f"operation-{i}", "kind": "render", "state": "succeeded"} for i in range(20)
    ]
    summary = conversation_summary(AgentSession(), turns, operations)
    assert summary == conversation_summary(AgentSession(), turns, operations)
    assert len(summary) <= 1200
    assert "target-2" not in summary
    assert all(f"update_segment segment target-{i}" in summary for i in range(3, 8))
    assert "Attachments:" in summary and "Operations:" in summary
    assembled = pack(summary=summary, messages=[{"role": "user", "content": "Newest request"}])
    assert assembled.messages[2]["content"] == "Summary so far:\n" + summary
    assert assembled.messages[3]["content"] == "Newest request"
    assert assembled.report["summary"] <= SUMMARY_CEILING


def test_all_twelve_plan_steps_remain_visible_with_maximum_unicode_titles():
    from gaiavfx.application.harness.world import WorldState

    state = WorldState(movie={"id": "movie", "revision": 1, "title": "雷" * 120})
    document = AgentSession(
        plan=Plan(
            id="plan",
            revision=1,
            state="executing",
            goal="Prepare",
            proposed_turn=0,
            steps=[PlanStep(kind="edit", title="🌧" * 200) for _ in range(12)],
            budget_jobs=0,
            grant=PlanGrant(
                budget_jobs=0, step_kinds=["edit"], approved_turn=1, reserved_at_approval=0
            ),
        )
    )
    view = render_view(state, "off", 0, 0, document=document)
    assert all(f"step {i} [edit] pending:" in view for i in range(12))
    assert estimate(view) <= VIEW_CEILING


def test_summary_reference_reads_come_from_accepted_turns_not_editable_board():
    from gaiavfx.application.harness.summary import conversation_summary

    turns = [
        {
            "state": "accepted",
            "role": "agent",
            "receipts": [],
            "action": {"tool": "describe_reference"},
            "result": {"focus": {"asset_id": "read-image"}},
        },
        {
            "state": "accepted",
            "role": "agent",
            "receipts": [],
            "action": {"tool": "reference_notes"},
        },
        {
            "state": "accepted",
            "role": "agent",
            "receipts": [],
            "action": {"tool": "describe_reference"},
            "result": {"focus": {"asset_id": "failed-image"}},
        },
        {
            "state": "rejected",
            "role": "agent",
            "receipts": [],
            "action": {"tool": "reference_notes"},
        },
    ]
    assert "References read: read-image" in conversation_summary(AgentSession(), turns, [])
    assert "failed-image" not in conversation_summary(AgentSession(), turns, [])
