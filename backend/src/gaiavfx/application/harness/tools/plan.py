"""Typed proposals and explicit progress; no tool can approve a plan or a review."""

import re
from typing import Annotated, Literal

from pydantic import Field

from gaiavfx.application.harness.actions import Tool, ToolResult, inlined, pin_enum
from gaiavfx.application.harness.board import write_entry
from gaiavfx.application.harness.knowledge import load
from gaiavfx.application.harness.replacement_context import latest_read_analysis
from gaiavfx.application.harness.session import (
    PLAN_JOBS_MAX,
    STEP_JOBS_MAX,
    Plan,
    PlanStep,
    StepKind,
)
from gaiavfx.application.harness.tools.common import refusal
from gaiavfx.application.harness.tools.plan_costs import check_plan_costs
from gaiavfx.application.harness.tools.plan_scope import proposal_scope_refusal
from gaiavfx.application.harness.tools.preparation_progress import completion_refusal
from gaiavfx.application.harness.tools.request_rules import count_refusal, plan_segment_count
from gaiavfx.application.set_seed import fill_renders
from gaiavfx.infrastructure.database import new_id
from gaiavfx.shared.contracts import StrictModel

PLAN_TEXT = load()["prompts"]["plan"]

# Direction's typed action names, translated at the harness boundary. These
# hints describe a future plan step, not authority to execute it now.
ACTION_KINDS = {
    "declare_relation": "edit",
    "view_angle": "prepare",
    "view_place": "prepare",
    "insert_subject": "prepare",
    "review_view": "prepare",
    "review_keyframe": "prepare",
}


def edit_title(title):
    """Reject only explicit editing verbs; a survey *of* a place is preparation."""
    if re.search(
        r"\b(?:set|add|write|describe|edit|update)\b[^.!?]*\b(?:text|sound|overhead|description|angle)\b",
        title,
        re.IGNORECASE,
    ):
        return True
    if re.search(
        r"\b(?:from|into|onto|in|to)\s+(?:(?:a|the|reviewed|source)\s+)*"
        r"(?:plate|image|frame|view)\b|\bplace view\b",
        title,
        re.IGNORECASE,
    ):
        return False
    return any(
        action in title.casefold() for action, kind in ACTION_KINDS.items() if kind == "edit"
    ) or bool(
        re.search(
            r"\b(?:declare|set|change|edit)\s+(?:the\s+)?(?:reverse|relation|angle relation)\b|"
            r"\b(?:create|draft|add|edit|change|update|remove|move)\s+(?:(?:a|the|new|two|three|\d+)\s+){0,3}"
            r"(?:storyline|segments?|phases?|shots?|cuts?|cast|places?)\b",
            title,
            re.IGNORECASE,
        )
    )


def stage_title(title):
    """A step that builds a stage is a build step; the live trial's model proposed "Build stage from segment 1's
    selected take" as a prepare step of 1 job. Building views or openings *from* a stage stays preparation."""
    return bool(
        re.search(
            r"\b(?:build|construct)(?:s|ing)?\s+(?:\S+\s+){0,3}?(?:stage|set)s?\b",
            title,
            re.IGNORECASE,
        )
    )


def aim_title(title):
    """A step that aims a camera in a stage (save_set_view) is an edit step; the live trial's model proposed "Aim a
    camera at the Stove angle (segment 1)" as a prepare step, which grants no save_set_view."""
    return bool(
        re.search(
            r"\b(?:aim|save)(?:s|ed|ing)?\b[^.!?]*\b(?:cameras?|(?:set |stage )?views?)\b",
            title,
            re.IGNORECASE,
        )
    )


def setup_only(message):
    """B2: structure verbs without producer keywords. Ambiguous messages are allowed.

    Match whole English words, case-insensitively: set up/setup, draft, create,
    change/edit/add/update structure, storyline, cast, places, segments, shots or cuts.
    Any preparation producer or render/take keyword leaves the choice open.
    This is scope discipline, not a general language understanding classifier.
    """
    if re.search(
        r"\b(?:prepar(?:e|es|ing|ation)|render(?:s|ed|ing)?|survey(?:s|ed|ing)?|takes?|"
        r"pin(?:s|ned|ning)?|insert(?:s|ed|ing|ion)?|remov(?:e|al|ing)|assembl(?:e|y|ing))\b",
        message,
        re.IGNORECASE,
    ):
        return False
    return bool(
        re.search(
            r"\b(?:set\s+up|setup|draft(?:ing)?|create|change|edit|add|update)\b",
            message,
            re.IGNORECASE,
        )
        and re.search(
            r"\b(?:structure|storyline|cast|places?|segments?|shots?|cuts?|angles?|phases?)\b",
            message,
            re.IGNORECASE,
        )
    )


def refuse(key, **facts):
    return refusal(PLAN_TEXT["refusals"][key].format(**facts))


def archive(document, state):
    previous = document.plan.model_copy(deep=True)
    previous.state = state
    document.plan_history = [*document.plan_history, previous][-8:]


def finish_plan(document, turn):
    plan = document.plan
    if (
        plan
        and plan.state == "executing"
        and all(step.status in {"done", "skipped"} for step in plan.steps)
    ):
        plan.state = "done"
        write_entry(
            document.board,
            "decisions",
            PLAN_TEXT["completed"].format(id=plan.id, revision=plan.revision),
            turn,
        )


def view_ids(context):
    """Only ids actually present in the bounded text view can be proposed targets."""
    ids = set()

    def collect(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if isinstance(child, str) and (key == "id" or key.endswith("_id")):
                    if child and child in context.view:
                        ids.add(child)
                elif isinstance(child, (dict, list)):
                    collect(child)
        elif isinstance(value, list):
            for child in value:
                collect(child)

    collect(context.state.payload())
    return ["", *sorted(ids)]


def default_producer_targets(context, steps):
    """Fill an omitted prepare/render target when the scope leaves exactly one choice.

    local-e13 plan-reject: four proposals omitted the optional target and were
    refused with prepare_target_required; the movie had one cut. With several
    candidates the refusal names them (plan_costs).
    """
    cuts = context.state.cuts
    segments = context.state.segments
    for step in steps:
        if step.target or step.kind not in {"prepare", "render"}:
            continue
        recasts = getattr(context.state, "recasts", [])
        if (
            step.kind == "render"
            and len(recasts) == 1
            and re.search(r"\brecast\b", step.title, re.IGNORECASE)
        ):
            step.target = recasts[0]["id"]
            continue
        # A segment the title names ("Render segment 1") wins; e2e run 10 rendered the
        # wrong segment when the default took the only segment with a cut instead.
        named = re.search(r"\bsegment\s+(\d+)\b", step.title, re.IGNORECASE)
        titled = next(
            (s for s in segments if named and s["ordinal"] + 1 == int(named.group(1))), None
        )
        if step.kind == "prepare":
            scoped = [c for c in cuts if not titled or c["segment_id"] == titled["id"]]
            if len(scoped) == 1:
                step.target = scoped[0]["id"]
        elif titled:
            step.target = titled["id"]
        elif len(segments) == 1:
            step.target = segments[0]["id"]


def default_build_target(context, step, scenes, stages):
    """Fill an omitted build target with what the title names: a segment by number, a stage by name, or for a fill
    the only accepted stage, else the stage this conversation read last; else the only choice. The live trial's model
    titled its step "Build stage from segment 1's selected take", said in its thought it targeted segment 1, and left
    the target out four times; asked to fill more, it read the stage and then left the target out three times."""
    if step.kind != "build" or step.target:
        return
    named = re.search(r"\bsegment\s+(\d+)\b", step.title, re.IGNORECASE)
    choices = [
        s["id"]
        for s in context.state.segments
        if named and s["ordinal"] + 1 == int(named.group(1)) and s["id"] in scenes
    ] + [
        r["id"]
        for r in getattr(context.state, "sets", [])
        if r["id"] in stages and r["name"].casefold() in step.title.casefold()
    ]
    if not choices:
        fill = re.search(r"\bfill", step.title, re.IGNORECASE) and not stage_title(step.title)
        choices = stages if fill else scenes + stages
        read = latest_read_analysis(getattr(context, "report_turns", []), stages)
        if fill and len(choices) > 1 and read:
            choices = [read]
    if len(choices) == 1:
        step.target = choices[0]


class ProposedStep(StrictModel):
    kind: StepKind = Field(description=PLAN_TEXT["step_kinds"])
    title: str = Field(min_length=1, max_length=200)
    target: str = Field(default="", max_length=120)
    # A recast's render step may reserve up to the plan's limit (the user, 22 Oct: one draft shot can cost 14
    # renders); every other step keeps STEP_JOBS_MAX, checked in `_run`.
    jobs: int = Field(default=0, ge=0, le=PLAN_JOBS_MAX, strict=True)


class ProposePlan(Tool):
    name = "propose_plan"
    description = PLAN_TEXT["rules"]
    terminal = True

    class Input(StrictModel):
        goal: str = Field(min_length=1, max_length=600)
        steps: list[ProposedStep] = Field(min_length=1, max_length=12)
        questions: list[Annotated[str, Field(max_length=200)]] = Field(
            default_factory=list, max_length=3
        )

    def narrow(self, context):
        document = context.document
        if self.reading_refusal(context) or not document or not document.plan_mode:
            return None
        schema = inlined(self.Input.model_json_schema())
        schema["properties"]["steps"]["items"] = pin_enum(
            schema["properties"]["steps"]["items"], "target", view_ids(context)
        )
        return schema

    def _run(self, context, value):
        if self.narrow(context) is None:
            return refuse("unavailable")
        if reason := count_refusal(context.latest_user_message, plan_segment_count(value.steps)):
            return refusal(reason)
        old = context.document.plan
        continuing_kinds = (
            set(old.grant.step_kinds) if old and old.state == "executing" and old.grant else set()
        )
        if setup_only(context.latest_user_message) and any(
            s.kind in {"prepare", "render"} and s.kind not in continuing_kinds for s in value.steps
        ):
            return refuse("setup_only", message=context.latest_user_message[:1000])
        if reason := proposal_scope_refusal(context.latest_user_message, value.steps):
            return refusal(reason)
        default_producer_targets(context, value.steps)
        # A scene whose take can build a stage, or a stage with an accepted version to fill more of (ADR 0020 §8).
        buildable = [
            s
            for s in context.state.segments
            if not (context.state.segment_plans.get(s["id"]) or {}).get("build_refusal")
        ]
        scenes = [s["id"] for s in buildable]
        stages = [r["id"] for r in getattr(context.state, "sets", []) if r.get("accepted")]
        # Numbered as the model names them in titles ("segment 1").
        scene_words = ", ".join(f"segment {s['ordinal'] + 1} {s['id']}" for s in buildable)
        for step in value.steps:
            default_build_target(context, step, scenes, stages)
        for index, step in enumerate(value.steps):
            if step.kind == "review" and not any(s.kind == "render" for s in value.steps[:index]):
                return refuse("review_without_render")
            if step.kind == "prepare" and stage_title(step.title):
                return refuse(
                    "build_as_prepare",
                    title=step.title,
                    jobs=fill_renders(),
                    scenes=scene_words or "none",
                )
            if step.kind == "prepare" and aim_title(step.title):
                return refuse("aim_as_prepare", title=step.title)
            if step.kind == "prepare" and edit_title(step.title):
                return refuse("edit_as_prepare", title=step.title)
            plan = context.state.segment_plans.get(step.target)
            if step.kind == "prepare" and plan and not plan["cuts"] and not plan["refusals"]:
                return refuse("nothing_to_prepare", target=step.target)
            if step.jobs and step.kind not in {"prepare", "render", "build"}:
                return refuse("jobs_on_wrong_kind", kind=step.kind, title=step.title[:120])
            if step.kind == "build" and step.target not in scenes + stages:
                # Each kind named apart: the live eval's model left the target out three times when one list mixed
                # scenes and stages.
                return refuse(
                    "build_target", scenes=scene_words or "none", stages=", ".join(stages) or "none"
                )
            recast = step.kind == "render" and step.target in {
                r["id"] for r in getattr(context.state, "recasts", [])
            }
            if step.jobs > STEP_JOBS_MAX and not recast:
                return refuse("step_too_large", jobs=step.jobs, maximum=STEP_JOBS_MAX)
            if step.target not in view_ids(context):
                return refuse("unknown_target", target=step.target)
            if step.kind == "render" and not any(
                later.kind == "review" for later in value.steps[index + 1 :]
            ):
                return refuse("review_missing")
        if sum(step.kind == "build" for step in value.steps) > 1:
            return refuse("one_build")
        if reason := check_plan_costs(context, value.steps):
            return refusal(reason)
        jobs = sum(step.jobs for step in value.steps)
        if jobs > PLAN_JOBS_MAX:
            return refuse("plan_too_large", jobs=jobs, maximum=PLAN_JOBS_MAX)
        document, old = context.document, context.document.plan
        revising = bool(old and (old.revision_requested or old.state in {"approved", "executing"}))
        if (
            revising
            and old.goal == value.goal
            and [step.model_dump(include={"kind", "title", "target", "jobs"}) for step in old.steps]
            == [step.model_dump() for step in value.steps]
        ):
            return refuse("plan_unchanged")
        if old:
            archive(
                document,
                "revised" if revising else "superseded" if old.state == "proposed" else old.state,
            )
        document.plan = Plan(
            id=old.id if revising else new_id(),
            revision=old.revision + 1 if revising else 1,
            goal=value.goal,
            steps=[PlanStep(**step.model_dump()) for step in value.steps],
            budget_jobs=jobs,
            questions=value.questions,
            proposed_turn=context.turn,
        )
        return ToolResult(payload={"plan": document.plan.model_dump()}, changed=True)


class PlanStepTool(Tool):
    name = "plan_step"
    description = PLAN_TEXT["step"]

    class Input(StrictModel):
        step: int = Field(ge=0, strict=True)
        status: Literal["done", "skipped"]
        note: str = Field(default="", max_length=1000)

    def narrow(self, context):
        doc = context.document
        if not doc or not doc.plan_mode or not doc.plan or doc.plan.state != "executing":
            return None
        indices = [
            i
            for i, step in enumerate(doc.plan.steps)
            if step.status in {"pending", "awaiting_user"}
        ]
        return pin_enum(self.Input.model_json_schema(), "step", indices) if indices else None

    def _run(self, context, value):
        if self.narrow(context) is None or value.step >= len(context.document.plan.steps):
            return refuse("step_unavailable")
        step = context.document.plan.steps[value.step]
        if step.status not in {"pending", "awaiting_user"}:
            return refuse("step_complete")
        if step.kind == "review" or step.status == "awaiting_user":
            return refuse("user_review")
        if step.kind == "build" and value.status == "done":
            return refuse("build_decision")
        if value.status == "done" and (reason := completion_refusal(context, value.step)):
            return refusal(reason)
        step.status = value.status
        finish_plan(context.document, context.turn)
        return ToolResult(
            payload={"step": value.step, "status": step.status, "note": value.note}, changed=True
        )
