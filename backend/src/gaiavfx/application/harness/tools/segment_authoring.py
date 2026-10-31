"""Segment authoring for Gaia: the one sequence of Direction commands behind edit_segment add, edit_cut create and
draft_storyline.

A segment is added with its cast, place and phases, each cut with the shot it opens; then the Join judges the
handoff on the real segment and its cuts, Direction assembles it, and Gaia's continuity checks run, all inside the
caller's transaction. Every tool applies the same guards; they are Gaia's editorial guardrails at the harness
boundary, and Direction's own rules stay in Direction. The tools translate their input and word refusals.
"""

from dataclasses import dataclass

from pydantic import Field

from gaiavfx.application.harness.knowledge import load
from gaiavfx.application.harness.tools import handoff
from gaiavfx.application.harness.tools.authoring import (
    MOTION,
    require_assemblable,
    require_different_angles,
    require_motion_direction,
    require_shot,
)
from gaiavfx.application.harness.tools.common import direction_receipt, scoped
from gaiavfx.application.harness.tools.cut_continuity import (
    require_cut_timing,
    require_segment_continuity,
)
from gaiavfx.application.harness.world import LIST, direction
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError

CASTING = load()["prompts"]["segments"]["cast"]
NO_PREDECESSOR = (
    "continues: true has no predecessor. Use continues: false for the opening beat; "
    "a continuing beat needs the previous segment."
)


class PhaseText(StrictModel):
    description: str = Field(max_length=8000)
    screen_direction: str = Field(
        default="",
        max_length=8000,
        description="Motion: left to right; empty if stationary.",
    )
    sound: str = Field(default="", max_length=8000)


class PhaseDraft(PhaseText):
    start_frame: int = Field(ge=0)


class PlaceText(StrictModel):
    noun: str
    description: str = Field(max_length=8000)
    landmarks: str = Field(default="", max_length=8000)


@dataclass(frozen=True)
class CutDraft:
    """A cut to create, with resolved angle ids, and the shot it opens."""

    frame: int
    from_angle_id: str
    to_angle_id: str
    relation: str | None
    crosses_barrier: bool | None
    shot: dict  # the phase the cut opens: description, screen_direction and sound


def offer(properties, required, context, *, handoff_fields=False):
    """The one rule for which segment fields a tool's schema offers, applied to a branch or beat in place.

    A segment may have no cast: with a cast, `subject_ids` names its members (never an empty enum); without one it
    is left out. Phases state their screen direction, which motion in the request requires. The handoff follows the
    rule unless the user asks about it (docs/CONTINUATION.md), so its fields are offered only then.
    """
    if "subject_ids" in properties:
        if context.state.cast:
            properties["subject_ids"] = {
                "type": "array",
                "maxItems": LIST,
                "items": {"enum": [s["id"] for s in context.state.cast]},
            }
            if "subject_ids" not in required:
                required.append("subject_ids")
        else:
            properties.pop("subject_ids")
            if "subject_ids" in required:
                required.remove("subject_ids")
    if "phases" in properties:
        phases = properties["phases"]
        phase = (phases["anyOf"][0] if "anyOf" in phases else phases)["items"]
        phase.setdefault("required", []).append("screen_direction")
        if MOTION.search(context.latest_user_message):
            phase["properties"]["screen_direction"] = {"type": "string", "minLength": 1}
    if handoff_fields and handoff.asked(context):
        properties |= handoff.schema()
    else:
        for name in ("continuation_method", "context_frames"):
            properties.pop(name, None)


def add_segment(
    session,
    context,
    revision,
    *,
    subject_ids,
    place,
    style,
    delivered_frames,
    continues,
    phases,
    cuts=(),
    at=None,
    continuation_method=None,
    context_frames=None,
    draft=False,
):
    """Add a segment and its cuts as one authored unit. `phases` are (start frame, content) pairs; a cut's shot
    becomes the phase at its frame. Returns the final row and every receipt, in order."""
    for subject_id in subject_ids:
        scoped(context.state.cast, subject_id, "Subject")
    for _, content in phases:
        require_motion_direction(content)
    timing = {"continues": continues, "delivered_frames": delivered_frames}
    frames = [{"id": str(c.frame), "frame": c.frame} for c in cuts]
    opened = dict(phases)
    for cut in cuts:
        require_cut_timing(timing, cut.frame, frames)
        require_shot(cut.shot["description"])
        if not cut.shot["screen_direction"].strip():
            raise ApplicationError("Each cut needs shot and screen_direction.", 422)
        opened[cut.frame] = {**cut.shot, "sound": opened.get(cut.frame, {}).get("sound", "")}
    content = {"subjects": list(subject_ids), "place": place}
    if style is not None:
        content["style"] = style
    row = direction.add_segment(
        session,
        context.movie_id,
        revision,
        content=content,
        delivered_frames=delivered_frames,
        continues=continues,
        phases=sorted(opened.items()),
        at=at,
        **handoff.added(continuation_method, context_frames),
    )
    receipts = [direction_receipt(row)]
    previous_frame = 0
    for cut in cuts:
        require_different_angles(cut.from_angle_id, cut.to_angle_id)
        if cut.frame <= previous_frame:
            raise ApplicationError("Cuts must be in increasing frame order.", 422)
        receipts.append(direction_receipt(create_cut(session, row, cut)))
        row = direction.read_segment(session, row["id"])
        previous_frame = cut.frame
    # A stored segment may continue from an explicit context artifact; Gaia adds one only after its predecessor.
    if continues and direction.predecessor(session, row) is None:
        raise ApplicationError(NO_PREDECESSOR, 422)
    # Only a new segment's choice is judged here: a stored one a later pin disqualified is the plan's to report.
    handoff.judge(session, row["id"])
    settle(session, context, row, draft=draft)
    return row, receipts


def add_cut(session, context, segment, revision, cut):
    """Create a cut on an existing segment, adding the phase its shot opens when none starts there. Returns the
    cut and every receipt, in order."""
    require_cut_timing(segment, cut.frame, segment["cuts"])
    previous_segment = segment
    receipts = []
    # The first command checks the revision the caller read; a later one the revision the first left.
    segment = {**segment, "revision": revision}
    if not any(p["start_frame"] == cut.frame for p in segment["phases"]):
        require_motion_direction(cut.shot)
        require_shot(cut.shot["description"])
        phase = direction.add_phase(
            session, segment["id"], revision, start_frame=cut.frame, content=cut.shot
        )
        receipts.append(direction_receipt(phase))
        segment = direction.read_segment(session, segment["id"])
    created = create_cut(session, segment, cut)
    receipts.append(direction_receipt(created))
    settle(
        session,
        context,
        direction.read_segment(session, segment["id"]),
        previous_segment=previous_segment,
    )
    return created, receipts


def create_cut(session, segment, cut):
    return direction.create_cut(
        session,
        segment["id"],
        segment["revision"],
        frame=cut.frame,
        from_angle_id=cut.from_angle_id,
        to_angle_id=cut.to_angle_id,
        relation=cut.relation,
        crosses_barrier=cut.crosses_barrier,
        relation_source="authored" if cut.relation else "unknown",
        receives_predecessor=True,
    )


def settle(session, context, row, *, draft=False, previous_segment=None):
    """What every authored segment must hold before the transaction commits: a structure Direction assembles,
    and continuous action across its cuts and from its predecessor."""
    require_assemblable(session, row["id"], draft=draft)
    require_segment_continuity(
        session,
        row["id"],
        context.latest_user_message,
        check_timing=True,
        previous_segment=previous_segment,
    )
