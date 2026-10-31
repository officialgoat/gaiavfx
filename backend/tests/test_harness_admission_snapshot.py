"""Characterization of tool admission: for a matrix of conversation states, the exact action schema Gaia is sent, the
tools offered, and the words a refused call, a missing step target and an absent tool get.

Admission decides what Gaia may call. Moving that decision must not change one byte of what Gaia sees, so this test
compares against a recorded snapshot. When a change is meant to alter admission, regenerate the snapshot with
GAIAVFX_UPDATE_ADMISSION_SNAPSHOT=1 and review the diff.
"""

import dataclasses
import hashlib
import json
import os
import re
from pathlib import Path

import harness_support
from harness_support import action
from test_harness_film import RECAST, film, plan  # noqa: F401 (fixture)

from gaiavfx.application.harness import context as harness_context
from gaiavfx.application.harness.actions import action_schema, parse_action
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.session import AgentSession, Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.tools import DEFAULT_TOOLS, applicable_tools
from gaiavfx.application.harness.tools.common import truthful_refusal
from gaiavfx.application.harness.tools.plan_costs import step_jobs_left
from gaiavfx.application.harness.tools.step_targets import require_target, target_problem
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.shared.errors import ApplicationError

world = harness_support.world
SNAPSHOT = Path(__file__).parent / "fixtures" / "harness" / "admission_snapshot.json"
UUID = re.compile(r"[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}")
PRODUCERS = ("render_candidates", "prepare_cut", "build_set_from_take", "fill_set_more")
KINDS = ("edit", "prepare", "render", "pick", "review", "answer", "build")
# A target no step names, to hear each producer's mismatch refusal.
ELSEWHERE = {"segment_id": "elsewhere", "cut_id": "elsewhere", "set_id": "elsewhere"}
ASKED = "Use render_candidates, prepare_cut and fill_set_more, then say it."


def stable(text):
    """Random record ids become placeholders in the order they appear."""
    seen = {}
    return UUID.sub(lambda m: seen.setdefault(m.group(0), f"<id{len(seen) + 1}>"), text)


def refused(call):
    try:
        call()
    except ApplicationError as error:
        return f"{error.status}: {error}"
    return None


def observe(context):
    tools = applicable_tools(context)
    schema = json.dumps(action_schema(tools), separators=(",", ":"))
    names = [tool.name for tool, _ in tools]
    absent = next((tool.name for tool in DEFAULT_TOOLS if tool.name not in names), None)
    record = {
        "tools": names,
        "schema_sha256": hashlib.sha256(stable(schema).encode()).hexdigest(),
        "schema_bytes": len(schema),
        "absent_call": absent and refused(lambda: parse_action(action(absent), tools, context)),
        "targets": {
            name: refused(lambda name=name: require_target(context, name, ELSEWHERE))
            for name in PRODUCERS
        },
        "target_problems": {name: target_problem(context, name) for name in PRODUCERS},
        "step_jobs_left": step_jobs_left(context),
        "truthful_refusal": truthful_refusal(context, ASKED),
    }
    return json.loads(stable(json.dumps(record)))


def executing(*steps):
    return Plan(
        id="plan",
        revision=1,
        state="executing",
        goal="Work on the movie",
        proposed_turn=0,
        steps=list(steps),
        budget_jobs=6,
        grant=PlanGrant(
            budget_jobs=6,
            step_kinds=list(dict.fromkeys(s.kind for s in steps)),
            approved_turn=1,
            reserved_at_approval=0,
        ),
    )


def movie_states(work, setup):
    harness = Harness(work.db, AgentStore(), None, work.settings)
    row = harness.open(setup["movie_id"])
    off = harness.set_plan_mode(row["id"], row["revision"], False)
    on = harness.set_plan_mode(off["id"], off["revision"], True)

    def context(row, plan=None):
        doc = AgentSession.model_validate(row["document"])
        if plan is not None:
            doc.plan = plan
        return harness.context(row, doc, [], "snapshot")

    targets = {
        "edit": setup["cut_id"],
        "prepare": setup["cut_id"],
        "render": setup["segment_id"],
        "pick": setup["segment_id"],
        "build": setup["segment_id"],
    }
    states = {}
    base = context(off)
    for autonomy in ("off", "prepare", "render"):
        states[f"movie/autonomous/{autonomy}"] = dataclasses.replace(base, autonomy=autonomy)
    states["movie/autonomous/unread-attachment"] = dataclasses.replace(
        base, unread_attachments=["attached-picture"]
    )
    states["movie/plan-mode/no-plan"] = context(on)
    proposed = executing(PlanStep(kind="render", title="Render segment 1", jobs=2))
    proposed.state, proposed.grant = "proposed", None
    states["movie/plan-mode/proposed"] = context(on, proposed)
    for kind in KINDS:
        for status in ("pending", "awaiting_user"):
            step = PlanStep(
                kind=kind,
                title=f"{kind.title()} the first segment",
                target=targets.get(kind, ""),
                jobs=2 if kind in {"prepare", "render", "build"} else 0,
                status=status,
            )
            done = PlanStep(kind="edit", title="Declare the relation", status="done")
            states[f"movie/executing/{kind}/{status}"] = context(
                on, executing(done, step, PlanStep(kind="review", title="Review the takes"))
            )
    states["movie/executing/render/unread-render"] = dataclasses.replace(
        states["movie/executing/render/pending"], unread_renders=["finished-render"]
    )
    return states


def film_states(film):  # noqa: F811
    states = {}
    for phase in ("no-plan", "proposed", "executing"):
        document = AgentSession(
            scope_kind="film",
            scope_id=RECAST,
            plan=None if phase == "no-plan" else plan(phase),
        )
        states[f"film/{phase}"] = film.context(document, jobs_limit=2)
    review = plan()
    review.steps[0].status = "done"
    states["film/executing/review"] = film.context(
        AgentSession(scope_kind="film", scope_id=RECAST, plan=review), jobs_limit=2
    )
    return states


def test_admission_matches_the_recorded_snapshot(world, film, monkeypatch):  # noqa: F811
    work, setup = world
    observed = {name: observe(ctx) for name, ctx in movie_states(work, setup).items()}
    observed |= {name: observe(ctx) for name, ctx in film_states(film).items()}
    prepare = movie_states(work, setup)["movie/executing/prepare/pending"]
    full = harness_context.estimate(
        json.dumps(action_schema(applicable_tools(prepare)), separators=(",", ":"))
    )
    for pressure in (1, 1500):
        monkeypatch.setattr(harness_context, "SCHEMA_CEILING", full - pressure)
        observed[f"movie/executing/prepare/overflow-{pressure}"] = observe(prepare)
    if os.environ.get("GAIAVFX_UPDATE_ADMISSION_SNAPSHOT"):
        SNAPSHOT.write_text(json.dumps(observed, indent=1, sort_keys=True) + "\n", "utf-8")
    recorded = json.loads(SNAPSHOT.read_text("utf-8"))
    assert observed.keys() == recorded.keys()
    for name in recorded:
        assert observed[name] == recorded[name], name


def test_the_snapshot_is_stable_between_runs(world):
    work, setup = world
    first = {n: observe(c) for n, c in movie_states(work, setup).items()}
    second = {n: observe(c) for n, c in movie_states(work, setup).items()}
    assert first == second
