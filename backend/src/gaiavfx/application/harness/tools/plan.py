"""Typed proposals and explicit progress; no tool can approve a plan or a review."""

from typing import Annotated, Literal

from pydantic import Field

from gaiavfx.application.harness.actions import Tool, ToolResult, inlined, pin_enum
from gaiavfx.application.harness.board import write_entry
from gaiavfx.application.harness.session import (
    PLAN_JOBS_MAX,
    Plan,
    PlanStep,
    StepKind,
)
from gaiavfx.application.harness.tools.common import refusal
from gaiavfx.application.harness.tools.plan_check import PLAN_TEXT, check, refuse, view_ids
from gaiavfx.application.harness.tools.preparation_progress import completion_refusal
from gaiavfx.infrastructure.database import new_id
from gaiavfx.shared.contracts import StrictModel


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
        if problem := check(context, value.steps):
            return problem
        jobs = sum(step.jobs for step in value.steps)
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
        indices = [i for i, step in enumerate(doc.plan.steps) if step.open]
        return pin_enum(self.Input.model_json_schema(), "step", indices) if indices else None

    def _run(self, context, value):
        if self.narrow(context) is None or value.step >= len(context.document.plan.steps):
            return refuse("step_unavailable")
        step = context.document.plan.steps[value.step]
        if not step.open:
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
