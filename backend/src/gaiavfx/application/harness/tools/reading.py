"""Preparation tools for a separate, durable vision reading turn."""

from pydantic import Field

from gaiavfx.application.harness.actions import Tool, ToolResult, pin_enum
from gaiavfx.application.harness.reading import TEXT, prepare_reference
from gaiavfx.application.harness.tools.common import refusal
from gaiavfx.shared.assistant import connection_for
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


class DescribeReference(Tool):
    name, kind = "describe_reference", "image"
    description = TEXT["describe"]

    class Input(StrictModel):
        asset_id: str = Field(min_length=1, max_length=120)
        question: str = Field(default="", max_length=600)

    def assets(self, context):
        return list(
            dict.fromkeys(
                a["id"]
                for a in [*context.state.assets, *context.attachments]
                if a["kind"] == self.kind
            )
        )

    def narrow(self, context):
        ids = self.assets(context)
        return pin_enum(self.Input.model_json_schema(), "asset_id", ids) if ids else None

    def _run(self, context, value):
        try:
            if not connection_for(context.settings)["vision"]:
                raise ApplicationError(TEXT["refusals"]["vision_unavailable"], 422)
            if value.asset_id not in self.assets(context):
                raise ApplicationError(TEXT["refusals"]["unknown_asset"], 422)
            payload, focus = prepare_reference(
                context.library, value.asset_id, self.kind, value.question, context.cancelled
            )
            return ToolResult(payload=payload, focus=focus, progress=True)
        except ApplicationError as exc:
            return refusal(exc)


class AnalyzeReferenceVideo(DescribeReference):
    name, kind = "analyze_reference_video", "video"
    description = TEXT["analyze"]
