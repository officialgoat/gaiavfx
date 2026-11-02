"""Terminal tools that report to the human and yield the turn."""

from pydantic import Field

from gaiavfx.application.harness import prompt
from gaiavfx.application.harness.actions import Tool, ToolResult
from gaiavfx.application.harness.tools.common import current_step, granted, refusal
from gaiavfx.application.harness.tools.report_checks import first_refusal
from gaiavfx.application.harness.tools.report_truth import (
    completed_plate_refusal,
    correction_refusal,
    ranking_refusal,
)
from gaiavfx.application.harness.tools.request_rules import promise_refusal
from gaiavfx.shared.contracts import StrictModel


class Say(Tool):
    name = "say"
    reports = True
    film = True
    always = True
    description = prompt.SAY
    terminal = True

    class Input(StrictModel):
        text: str = Field(min_length=1, max_length=4000)

    def describe(self, context):
        return prompt.SAY_FILM if prompt.film(context) else self.description

    def narrow(self, context):
        if self.reading_refusal(context):
            return None
        step = current_step(context)
        if step and step.status == "pending" and step.kind != "review":
            # A genuine refusal may leave no executable action of this step's kind.
            # Read/progress tools stay available, but must not trap an honest report.
            discrepancy = context.document.previous_discrepancy
            if discrepancy and "say" not in discrepancy:
                from gaiavfx.application.harness.tools import DEFAULT_TOOLS

                legal = any(
                    tool.step_kind == step.kind
                    and granted(context, step.kind)
                    and tool.narrow(context) is not None
                    for tool in DEFAULT_TOOLS
                )
                if not legal and step.kind != "answer":
                    return super().narrow(context)
            return None
        return super().narrow(context)

    def _run(self, context, value):
        from gaiavfx.application.harness.tools.replacement import replacement_claim_refusal

        def unavailable(context, text):
            if self.narrow(context) is None:
                return (
                    "say is unavailable while an executing plan has an actionable step. "
                    "Execute it, or use plan_step with a note to mark it done/skipped; "
                    "ask_human for a genuine decision."
                )
            return ""

        checks = (
            replacement_claim_refusal,
            completed_plate_refusal,
            unavailable,
            ranking_refusal,
            correction_refusal,
            promise_refusal,
        )
        if reason := first_refusal(context, value.text, checks):
            return refusal(reason)
        return ToolResult(payload={"text": value.text})


class AskHuman(Tool):
    name = "ask_human"
    film = True
    always = True
    reads_attachments = True
    waits_for_human = True
    description = prompt.ASK_HUMAN
    terminal = True

    class Input(StrictModel):
        question: str = Field(min_length=1, max_length=2000)
        options: list[str] = Field(default_factory=list, max_length=6)

    def describe(self, context):
        return prompt.ASK_HUMAN_FILM if prompt.film(context) else self.description

    def _run(self, context, value):
        if reason := first_refusal(context, value.question, (completed_plate_refusal,)):
            return refusal(reason)
        payload = {"question": value.question, "options": list(value.options)}
        # The analyses in view when asking; an answer confirms performers only for these.
        if analyses := [
            a["id"]
            for a in getattr(context.state, "people_analyses", [])
            if a["state"] == "succeeded"
        ]:
            payload["analyses"] = analyses
        return ToolResult(payload=payload)
