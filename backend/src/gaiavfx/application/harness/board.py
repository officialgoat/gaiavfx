"""Contracts for the durable agent whiteboard."""

from typing import Literal

from pydantic import Field

from gaiavfx.infrastructure.database import new_id
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


class BoardEntry(StrictModel):
    id: str = Field(min_length=1, max_length=80)
    text: str = Field(default="", max_length=2000)
    summary: str = Field(default="", max_length=200)
    turn: int | None = Field(default=None, ge=0)
    resolved: bool = False


class Whiteboard(StrictModel):
    version: Literal[1] = 1
    revision: int = Field(default=0, ge=0)
    brief: str = Field(default="", max_length=8000)
    understanding: str = Field(default="", max_length=8000)
    decisions: list[BoardEntry] = Field(default_factory=list)
    references: list[BoardEntry] = Field(default_factory=list)
    open_questions: list[BoardEntry] = Field(default_factory=list)


ENTRY_SECTIONS = ("decisions", "open_questions", "references")
TEXT_SECTIONS = ("brief", "understanding")
SECTION_LIMITS = {"decisions": 40, "open_questions": 12, "references": 8}
SUMMARY_LENGTH = 160

__all__ = [
    "BoardEntry",
    "Whiteboard",
    "render_board",
    "trim_section",
    "write_entry",
]


def render_board(board):
    lines = []
    if board.brief:
        lines.append("brief: " + board.brief)
    if board.understanding:
        lines.append("understanding: " + board.understanding)
    for section in ENTRY_SECTIONS:
        entries = getattr(board, section)
        if not entries:
            continue
        lines.append(section + ":")
        for entry in entries:
            mark = " [resolved]" if entry.resolved else ""
            lines.append(f"- {entry.id} (turn {entry.turn}){mark}: {entry.summary or entry.text}")
    return "\n".join(lines)


def write_entry(board, section, text, turn, entry_id=None):
    if section not in ENTRY_SECTIONS:
        raise ApplicationError(
            f"Write entries to one of {', '.join(ENTRY_SECTIONS)}; brief and understanding are "
            "human-owned text.",
            422,
        )
    if not text.strip():
        raise ApplicationError("A board entry needs text.", 422)
    entries = getattr(board, section)
    entry = BoardEntry(id=entry_id or new_id(), text=text.strip(), turn=turn)
    existing = next((item for item in entries if item.id == entry.id), None)
    if existing is not None:
        entries[entries.index(existing)] = entry
    else:
        entries.append(entry)
    trim_section(board, section)
    board.revision += 1
    return entry


def trim_section(board, section):
    entries = getattr(board, section)
    while len(entries) > SECTION_LIMITS[section]:
        oldest = entries.pop(0)
        oldest.summary = oldest.summary or oldest.text[:SUMMARY_LENGTH].strip()
        oldest.text = ""
