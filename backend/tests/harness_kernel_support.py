"""Helpers several Gaia harness test files share, beyond harness_support's kernel and world fixtures: a world's tool
context and plan grants, a drafted courtyard on the kernel, scripted posts, tool calls through the grammar, the
media-backed library variants and a packed long conversation."""

from copy import deepcopy

import pytest
from harness_support import action

from gaiavfx.application.harness.actions import parse_action
from gaiavfx.application.harness.board import Whiteboard
from gaiavfx.application.harness.budget import Budget
from gaiavfx.application.harness.context import assemble, turn_messages
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.session import AgentSession, Plan, PlanGrant, PlanStep
from gaiavfx.application.harness.tools.draft import DraftStoryline
from gaiavfx.application.harness.tools.prepare import PrepareCut
from gaiavfx.application.harness.world import direction
from gaiavfx.application.prepare_references import approve_view, open_views_review
from gaiavfx.modules.agent.service import AgentStore

# An empty camera view of the courtyard, as run 3 authored it.
ANGLE = (
    "From inside the gallery behind the black iron railing, looking out across the railing "
    "into the sunlit cobblestone courtyard with potted olive trees and the far arcade"
)


# A world's movie, its tool context and its plans.


def context_for(work, setup):
    harness = Harness(work.db, AgentStore(), None, work.settings)
    row = harness.open(setup["movie_id"])
    row = harness.set_plan_mode(row["id"], row["revision"], False)
    return harness.context(row, AgentSession.model_validate(row["document"]), [], "test-turn")


def executing(*steps):
    return Plan(
        id="plan",
        revision=1,
        state="executing",
        goal="Prepare then render Segment 2",
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


def prepare_grant(context):
    context.document.plan_mode = True
    context.document.plan = Plan(
        id="prepare-only",
        revision=1,
        state="executing",
        goal="Prepare the reverse",
        proposed_turn=0,
        budget_jobs=2,
        grant=PlanGrant(
            budget_jobs=2, step_kinds=["prepare"], approved_turn=1, reserved_at_approval=0
        ),
        steps=[PlanStep(kind="prepare", title="Prepare reverse pin", jobs=2)],
    )
    context.budget = Budget(2, context.budget.spent)
    return context


def call(tool, context, **values):
    """Run a tool on a value parsed through its narrowed grammar, as the model's action is."""
    _, value = parse_action(action(tool.name, **values), [(tool, tool.narrow(context))])
    return tool.run(context, value)


def prepare(work, setup, **values):
    ctx = context_for(work, setup)
    ctx.turn_id += str(values.get("seed", 1))
    return PrepareCut().run(ctx, PrepareCut.Input(cut_id=setup["cut_id"], **values))


def survey(work, setup):
    from harness_eval_tasks import run_idle

    result = prepare(work, setup, seed=31)
    assert result.ok, result.discrepancy
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        return open_views_review(session, result.waits_on)


def approved_views(work, setup):
    review = survey(work, setup)
    with work.db.transaction() as session:
        for member in review["members"]:
            approve_view(session, candidate_id=member["candidate_id"])


# Media-backed library variants.


@pytest.fixture
def unplated(tmp_path):
    from harness_eval_tasks import close, dz_library

    work, setup = dz_library(tmp_path, plate=False)
    try:
        yield work, setup
    finally:
        close(work)


@pytest.fixture
def insertion_ready(tmp_path):
    from harness_eval_tasks import close, dz_library

    work, setup = dz_library(tmp_path, insertion=False)
    with work.db.transaction() as session:
        angle = direction.read_angle(session, setup["angles"][2])
        direction.update_angle(session, angle["id"], angle["revision"], description=ANGLE)
        view = direction.record_evidence(session, angle["id"], "imported", setup["plate"])
        direction.approve_evidence(session, view["id"])
        segment = direction.read_segment(session, setup["segment_id"])
        phase = segment["phases"][0]
        direction.update_phase(
            session,
            phase["id"],
            phase["revision"],
            content={**phase["content"], "screen_direction": "left to right"},
        )
    try:
        yield work, setup
    finally:
        close(work)


# The fast kernel: a cast member, a drafted courtyard and scripted turns.


def draft_context(kernel):
    with kernel.db.transaction() as session:
        subject = direction.add_subject(
            session,
            kernel.movie["id"],
            1,
            name="Storm lover",
            description="A walker",
            keeps=["plaid shirt"],
            identity_asset_id=None,
        )
    doc = AgentSession.model_validate(kernel.row["document"])
    doc.plan = Plan(
        id="plan",
        revision=1,
        state="executing",
        goal="Draft",
        proposed_turn=0,
        steps=[PlanStep(kind="edit", title="Draft")],
        budget_jobs=0,
        grant=PlanGrant(
            budget_jobs=0, step_kinds=["edit"], approved_turn=1, reserved_at_approval=0
        ),
    )
    return kernel.harness.context(kernel.row, doc, []), subject


def courtyard(kernel):
    ctx, subject = draft_context(kernel)
    with kernel.db.transaction() as session:
        place = direction.create_place(session, "Arcade courtyard")
        angles = [direction.add_angle(session, place["id"], n) for n in ("Side-on", "Reverse")]
        direction.update_angle(
            session,
            angles[1]["id"],
            1,
            description="Wide view of open paving beyond the railing, olive trees and columns.",
        )
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    beat = {
        "title": "Walk",
        "place": {"noun": "courtyard", "description": "Open paving"},
        "subject_ids": [subject["id"]],
        "continues": False,
        "delivered_frames": 102,
        "phases": [
            {
                "start_frame": 0,
                "description": "Arin walks along the railing",
                "screen_direction": "left to right",
            }
        ],
        "cuts": [],
    }
    reverse = deepcopy(beat)
    reverse.update(continues=True, title="Reverse")
    reverse["cuts"] = [
        {
            "frame": 36,
            "from_angle": angles[0]["id"],
            "to_angle": angles[1]["id"],
            "relation": "reverse",
            "crosses_barrier": True,
            "shot": "Arin walks beyond the railing",
            "screen_direction": "right to left",
        }
    ]
    return ctx, [beat, reverse]


def drafted(kernel):
    ctx, beats = courtyard(kernel)
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=2, beats=beats))
    assert result.ok, result.discrepancy
    with kernel.db.transaction() as session:
        segment = direction.read_movie(session, kernel.movie["id"])["segments"][1]
    return kernel.harness.context(kernel.row, ctx.document, []), segment


def story(kernel, *, places=1, duplicate=False):
    ctx, subject = draft_context(kernel)
    with kernel.db.transaction() as session:
        place = direction.create_place(session, "Courtyard")
        angles = [direction.add_angle(session, place["id"], n) for n in ("gate", "reverse")]
        if duplicate:
            angles.append(direction.add_angle(session, place["id"], "REVERSE"))
        if places == 2:
            gallery = direction.create_place(session, "Gallery")
            for name in ("gate", "reverse"):
                direction.add_angle(session, gallery["id"], name)
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    beat = {
        "title": "Reverse",
        "place": {"noun": "courtyard", "description": "Stone paving"},
        "subject_ids": [subject["id"]],
        "continues": False,
        "phases": [
            {"start_frame": 0, "description": "Arin walks", "screen_direction": "left to right"}
        ],
        "from_angle": "GATE",
        "cuts": [
            {
                "frame": 36,
                "to_angle": "reverse",
                "relation": "reverse",
                "crosses_barrier": True,
                "shot": "Arin walks past the railing from the reverse side.",
                "screen_direction": "right to left",
            }
        ],
    }
    return ctx, beat, place, angles


def draft(ctx, beats):
    tool = DraftStoryline()
    return tool.run(ctx, tool.Input(expected_revision=ctx.state.movie["revision"], beats=beats))


def post(kernel, *sequence, message="Survey the courtyard for the reverse cut."):
    """Post one human message, run the scripted replies and return the agent's turns."""
    kernel.model.replies = iter(sequence)
    kernel.harness.post_message(kernel.id, message, str(len(kernel.model.calls)))
    kernel.runner.run_once()
    return [t for t in kernel.harness.turns(kernel.id) if t["role"] == "agent"]


# A long conversation packed into a fixed window.


def pack(turns, **kwargs):
    return assemble(
        "System",
        "Brief",
        "Tools",
        {},
        Whiteboard(),
        "Active plan: create cut; pending review A/B",
        turn_messages(turns),
        32768,
        output=2400,
        summary="Earlier decisions and receipts",
        **kwargs,
    )
