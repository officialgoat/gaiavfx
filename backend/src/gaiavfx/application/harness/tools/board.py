"""Whiteboard tools. Board writes are the agent's durable memory across turns."""

from typing import Literal

from pydantic import Field

from gaiavfx.application.harness import prompt
from gaiavfx.application.harness.actions import Tool, ToolResult, pin_enum
from gaiavfx.application.harness.board import ENTRY_SECTIONS, write_entry
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


class BoardWrite(Tool):
    name = "board_write"
    description = prompt.BOARD_WRITE

    class Input(StrictModel):
        section: Literal["decisions", "open_questions"]
        text: str = Field(min_length=1, max_length=2000)
        entry_id: str | None = Field(default=None, max_length=80)

    def narrow(self, context):
        return pin_enum(self.Input.model_json_schema(), "section", ENTRY_SECTIONS)

    def _run(self, context, value):
        try:
            entry = write_entry(
                context.board, value.section, value.text, context.turn, value.entry_id
            )
        except ApplicationError as error:
            return ToolResult(ok=False, discrepancy=str(error))
        return ToolResult(payload={"entry": entry.model_dump()}, changed=True)
