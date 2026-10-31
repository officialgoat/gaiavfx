"""Movie structure: what a movie's direction rows must hold, each rule written once.

Rows have the journal's shape, `{(kind, id): row}`; a segment's content may be stored JSON text or a value. A command
checks the rows it would leave before it writes, so a duplicate frame is refused in words rather than by a unique
index; Undo checks the rows its receipt would put back (`journal.preview`). A removal is refused in the words of the
command that removes; Undo words any other broken rule as a later change.
"""

import json
from dataclasses import dataclass, field
from itertools import pairwise

from gaiavfx.modules.direction import content as authored
from gaiavfx.modules.direction.segment import require_frame

CHANGED_AFTER = "The record changed after this command; undo the later command first."


@dataclass(frozen=True)
class Problem:
    code: str
    message: str
    status: int = 409
    ids: tuple = field(default=())


def with_row(rows, kind, record_id, values):
    """The rows with one row added or changed."""
    return {
        **rows,
        (kind, record_id): {**rows.get((kind, record_id), {}), "id": record_id, **values},
    }


def without(rows, kind, record_id):
    return {key: row for key, row in rows.items() if key != (kind, record_id)}


def removal_problem(rows, kind, record_id) -> Problem | None:
    """Why removing the row would leave something hanging on it: a Subject a Segment shows, a Segment's Cuts, a
    Cut's Pins or Suitability history."""
    if kind == "subject":
        for segment in _segments(rows):
            if record_id in _subjects(segment):
                return Problem(
                    "subject_in_use",
                    f"Subject {record_id} appears in segment {segment['ordinal']}; remove the reference "
                    "first.",
                )
    elif kind == "segment" and (cuts := _kids(rows, "cut", "segment_id", record_id)):
        return Problem(
            "segment_has_cuts", "Remove the segment's cuts first.", ids=_ids(cuts, "frame")
        )
    elif kind == "cut" and _kids(rows, "keyframe", "cut_id", record_id):
        return Problem("cut_has_keyframes", "A cut with recorded keyframes cannot be removed.")
    elif kind == "cut" and _kids(rows, "suitability", "cut_id", record_id):
        return Problem(
            "cut_has_suitability_history", "A cut with suitability history cannot be removed."
        )
    return None


def segment_problem(rows, segment_id) -> Problem | None:
    """What the Segment's Phases and Cuts break: a frame outside it, two Phases or two Cuts on one frame, or a Cut
    that does not start from the angle the Cut before it ends on."""
    delivered = rows[("segment", segment_id)]["delivered_frames"]
    phases = sorted(
        _kids(rows, "phase", "segment_id", segment_id), key=lambda p: (p["start_frame"], p["id"])
    )
    cuts = sorted(_kids(rows, "cut", "segment_id", segment_id), key=lambda c: (c["frame"], c["id"]))
    for phase in phases:
        if problem := _outside(phase["start_frame"], delivered, cut=False):
            return problem
    for a, b in pairwise(phases):
        if a["start_frame"] == b["start_frame"]:
            return Problem("phase_frame_taken", f"Phase frame {b['start_frame']} already exists.")
    for cut in cuts:
        if problem := _outside(cut["frame"], delivered, cut=True):
            return problem
    for a, b in pairwise(cuts):
        if a["frame"] == b["frame"]:
            return Problem("cut_frame_taken", f"Cut frame {b['frame']} already exists.")
    for a, b in pairwise(cuts):
        if b["from_angle_id"] != a["to_angle_id"]:
            return Problem(
                "cut_chain",
                f"Cuts chain: the cut at frame {b['frame']} must start from the previous cut's target angle "
                f"{a['to_angle_id']}. Move or remove the existing cut instead of creating a duplicate reverse.",
                422,
            )
    return None


def undo_problem(rows, removed) -> Problem | None:
    """Why putting a receipt's rows back would break the movie: removing what it created, in the removing
    command's words, or any other rule broken, as a later change."""
    for kind, record_id in removed:
        if problem := removal_problem(rows, kind, record_id):
            return problem
    cast = {record_id for kind, record_id in rows if kind == "subject"}
    for segment in _segments(rows):
        if segment_problem(rows, segment["id"]) or not _subjects(segment) <= cast:
            return Problem("changed_after", CHANGED_AFTER)
    return None


def _outside(frame, delivered, *, cut):
    try:
        require_frame(frame, delivered, cut=cut)
    except ValueError as exc:
        return Problem("frame_outside", str(exc), 422)
    return None


def _kids(rows, kind, column, parent):
    return [row for (k, _), row in rows.items() if k == kind and row[column] == parent]


def _ids(rows, order):
    return tuple(row["id"] for row in sorted(rows, key=lambda row: row[order]))


def _segments(rows):
    return sorted(
        (row for (kind, _), row in rows.items() if kind == "segment"), key=lambda s: s["ordinal"]
    )


def _subjects(segment):
    document = segment["content"]
    if isinstance(document, str):
        document = json.loads(document)
    return set(authored.segment_content(document, segment["content_schema"]).subject_ids)
