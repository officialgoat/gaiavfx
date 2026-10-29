"""Compile retained authored fixtures without requiring repository history."""

import json
from pathlib import Path

from gaiavfx.application.authored import load_segment
from gaiavfx.integrations import minimax_h3
from gaiavfx.integrations.minimax_h3.prompt import compile_prompt
from gaiavfx.modules.direction import content

GOLDEN = Path(__file__).parent / "golden" / "minimax_h3"
GRID = minimax_h3.capabilities().frame_grid
FIXTURES = (
    "one-phase",
    "two-phases",
    "cut",
    "two-cuts",
    "cut-state-at-cut",
    "cut-continuation",
    "cut-continuation-39",  # a chosen 39-frame context moves every window timecode by 17 frames
    "single-continuation",
    "dialogue",
    "event-beat",
    "planning-fields",
    "agent-courtyard-reverse",
    "agent-harbour-back",
    "agent-harbour-reverse",
    "agent-street-reverse",
    "lab-courtyard-rich",
    "lab-harbour-rich",
)


def compile_fixture(path: Path):
    fixture = json.loads(path.read_text(encoding="utf-8"))
    if fixture["sheet"] is not None or fixture["keyframes"]:
        raise ValueError("Retained wording fixtures have no sheet or keyframes")
    records = load_segment(fixture["segment"])
    segment, pictures = content.assemble(
        records["content"],
        content.segment_schema(records["content"]),
        [content.cast_member(member) for member in records["cast"]],
        [content.phase_record(phase) for phase in records["phases"]],
        fixture["segment"]["cut_frames"],
        records["delivered_frames"],
        records["continues"],
    )
    compiled = compile_prompt(segment, pictures, (), GRID, fixture["context_frames"])
    return fixture, segment, pictures, compiled
