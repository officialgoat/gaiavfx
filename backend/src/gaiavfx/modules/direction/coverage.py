"""Cut coverage (ADR 0009): does this render already receive evidence of the geography the new view shows?

- `continuing`: the render receives it, because the preceding shot is part of it (the same generation, or its motion
  context) and the new camera looks the same way from the same side of every barrier.
- `established`: reviewed evidence of the target angle exists, but the render must be given it.
- `new`: no reviewed evidence exists yet; preparing a survey of the angle is the way forward.
- `unknown`: the relation between the two angles is not known, so nothing can be decided automatically.

A segment's opening shot (ADR 0019) has no cut before it: it is `continuing` when the segment continues its
predecessor on the same angle, `established` when its angle has reviewed evidence, and `new` otherwise.

A selected take is editorial use and never evidence. A relation computed from coordinates is `inferred`:
it models no occlusion, so it can suggest but a human or the agent may override it.
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Literal


class Relation(StrEnum):
    SAME_DIRECTION = "same_direction"  # closer, wider, push-in, a turn of the head on the same side
    SIDE = "side"  # a frontal or look-back view, a side view
    REVERSE = "reverse"  # the camera looks back at where it stood


class Coverage(StrEnum):
    CONTINUING = "continuing"
    ESTABLISHED = "established"
    NEW = "new"
    UNKNOWN = "unknown"


# The one phrasing of each coverage for every view (ADR 0017 §2: statuses come from Python), for cuts and first
# shots alike: what the render has already been shown of the area the shot sees.
COVERAGE_WORDS = {
    Coverage.CONTINUING: "Continues the last shot",
    Coverage.ESTABLISHED: "Has reviewed pictures",
    Coverage.NEW: "Reveals new area",
    Coverage.UNKNOWN: "Coverage unknown",
}


RelationSource = Literal["authored", "inferred", "unknown"]


@dataclass(frozen=True)
class AngleRelation:
    """How the cut's new angle relates to the one before it, and where that knowledge came from."""

    value: Relation | None = None
    crosses_barrier: bool | None = None
    source: RelationSource = "unknown"

    def __post_init__(self):
        if (self.value is None) != (self.source == "unknown"):
            raise ValueError("a relation is known exactly when its source is authored or inferred")
        if (self.value is None) != (self.crosses_barrier is None):
            raise ValueError("a known relation also says whether the camera crosses a barrier")


@dataclass(frozen=True)
class Evidence:
    """Reviewed or reviewable material showing an angle's geography."""

    id: str
    angle_id: str
    kind: Literal["survey_view", "take_frame", "imported", "set_view"]
    asset_id: str
    approved: bool
    stale: bool = False


@dataclass(frozen=True)
class CutContext:
    """What coverage needs to know about one cut. Ids are opaque: nothing depends on their names."""

    cut_id: str
    from_angle: str
    to_angle: str
    relation: AngleRelation
    receives_predecessor: (
        bool  # the preceding shot is inside this render (same generation or motion context)
    )
    evidence: tuple[Evidence, ...] = ()
    side_facts: bool = (
        False  # the layout knows which side of the frame each landmark of the new view is on
    )


@dataclass(frozen=True)
class Assessment:
    coverage: Coverage
    reasons: tuple[str, ...]
    evidence_ids: tuple[str, ...] = field(default=())


def relation_from_yaw(delta_degrees: float, same_max: float, reverse_min: float) -> Relation:
    """A relation suggested by the turn between two camera yaws (the thresholds are policy data)."""
    if not 0 <= same_max < reverse_min <= 180:
        raise ValueError("thresholds must satisfy 0 <= same_max < reverse_min <= 180")
    turn = abs((delta_degrees + 180.0) % 360.0 - 180.0)
    if turn <= same_max:
        return Relation.SAME_DIRECTION
    if turn >= reverse_min:
        return Relation.REVERSE
    return Relation.SIDE


def assess_cut(context: CutContext) -> Assessment:
    """Only approved, fresh evidence establishes current coverage; history stays unchanged."""
    evidence = tuple(e for e in context.evidence if e.angle_id == context.to_angle)
    approved = tuple(e.id for e in evidence if e.approved and not e.stale)
    pending = tuple(e.id for e in evidence if not e.approved)
    relation = context.relation

    if context.from_angle == context.to_angle and context.receives_predecessor:
        return Assessment(Coverage.CONTINUING, ("the cut keeps the same angle",))
    if (
        relation.value is Relation.SAME_DIRECTION
        and relation.crosses_barrier is False
        and context.receives_predecessor
    ):
        return Assessment(
            Coverage.CONTINUING,
            (f"the new angle looks the same way on the same side ({relation.source})",),
        )
    if approved:
        reason = "reviewed evidence of the new angle exists; the render must be given it"
        return Assessment(Coverage.ESTABLISHED, (reason,), approved)
    if relation.value is None:
        reasons = ("how the new angle relates to the previous one is not known",)
        if pending:
            reasons += ("evidence of the new angle is waiting for review",)
        return Assessment(Coverage.UNKNOWN, reasons, pending)
    why = {
        Relation.SAME_DIRECTION: "the new angle looks the same way from the other side of a barrier",
        Relation.SIDE: "the new angle looks across or back along the place",
        Relation.REVERSE: "the new angle looks back at where the camera stood",
    }[relation.value]
    if relation.value is Relation.SAME_DIRECTION and not relation.crosses_barrier:
        why = "the render does not receive the preceding shot"
    reasons = (why, "no reviewed evidence of the new angle exists")
    if pending:
        reasons += ("evidence of the new angle is waiting for review",)
    return Assessment(Coverage.NEW, reasons, pending)


@dataclass(frozen=True)
class OpeningContext:
    """What coverage needs to know about a segment's first shot."""

    segment_id: str
    angle: str
    previous_angle: str | None  # the predecessor's last shot, when the segment continues it
    receives_predecessor: bool  # the segment continues its predecessor (motion context)
    evidence: tuple[Evidence, ...] = ()


def assess_opening(context: OpeningContext) -> Assessment:
    """The opening's coverage; like a cut's, only approved, fresh evidence establishes it."""
    evidence = tuple(e for e in context.evidence if e.angle_id == context.angle)
    approved = tuple(e.id for e in evidence if e.approved and not e.stale)
    pending = tuple(e.id for e in evidence if not e.approved)
    if context.receives_predecessor and context.previous_angle == context.angle:
        return Assessment(
            Coverage.CONTINUING, ("the segment continues its predecessor on the same angle",)
        )
    if approved:
        reason = "reviewed evidence of the opening angle exists; the render must be given it"
        return Assessment(Coverage.ESTABLISHED, (reason,), approved)
    why = (
        "the segment continues its predecessor onto another angle"
        if context.receives_predecessor
        else "the segment opens without a predecessor in the render"
    )
    reasons = (why, "no reviewed evidence of the opening angle exists")
    if pending:
        reasons += ("evidence of the opening angle is waiting for review",)
    return Assessment(Coverage.NEW, reasons, pending)
