"""Quiet-mouth facts from the source performer's own lips; model-free and pure.

Film soundtracks rarely fall silent (music and ambience), and the quiet wording describes
the performer's lips, so the facts come from the measured source mouth, not from audio.
"""

import math
from dataclasses import dataclass

from gaiavfx.modules.direction.replacement import MouthPolicy


@dataclass(frozen=True)
class MouthSeries:
    """Per delivered frame: inner-lip aperture over face height, and face height in pixels."""

    aperture: tuple[float | None, ...]
    face_height: tuple[float | None, ...]

    def __post_init__(self):
        if (
            not isinstance(self.aperture, tuple)
            or not isinstance(self.face_height, tuple)
            or len(self.aperture) != len(self.face_height)
            or not self.aperture
        ):
            raise ValueError("mouth series need one aperture and face height per frame")
        for value in (*self.aperture, *self.face_height):
            if value is not None and (
                isinstance(value, bool)
                or not isinstance(value, (int, float))
                or not math.isfinite(value)
                or value < 0
            ):
                raise ValueError("mouth measurements must be finite and nonnegative")


@dataclass(frozen=True)
class MouthFacts:
    """Counts are window frames: any open mouth, or all present mouths closed, is measured."""

    quiet: bool
    silent_intervals: tuple[tuple[int, int], ...]
    reason: str
    open_frames: int
    measured_frames: int


def _runs(states, wanted):
    """Maximal half-open runs of frames whose state is ``wanted``."""
    runs, start = [], None
    for i, state in enumerate([*states, None]):
        if state == wanted and start is None:
            start = i
        elif state != wanted and start is not None:
            runs.append((start, i))
            start = None
    return runs


def frame_states(series: MouthSeries, presence, policy: MouthPolicy):
    """Each frame is ``closed``, ``open``, ``unmeasured`` or ``absent``.

    An ``unresolved`` track frame (an uncertain mask) still counts when its lips are measured
    open, but a closed measurement there is not trusted and counts as unmeasured.
    """
    states = []
    for aperture, height, state in zip(series.aperture, series.face_height, presence, strict=True):
        if state not in {"present", "absent", "unresolved"}:
            raise ValueError("invalid presence state")
        if state == "absent":
            states.append("absent")
        elif aperture is None or height is None or height < policy.min_face_height_px:
            states.append("unmeasured")
        elif aperture > policy.closed_aperture_at_most:
            states.append("open")
        else:
            states.append("closed" if state == "present" else "unmeasured")
    return states


def _joint(states):
    present = [s for s in states if s != "absent"]
    if not present:
        return "absent"
    if "open" in present:
        return "open"
    return "closed" if all(s == "closed" for s in present) else "unmeasured"


def quiet_facts(
    performers: tuple[MouthSeries, ...],
    presence: tuple[tuple[str, ...], ...],
    policy: MouthPolicy,
    max_intervals: int = 8,
) -> MouthFacts:
    """Closed source lips become ``quiet`` or silent intervals (window-relative frames).

    ``presence`` holds each performer's track states (present, absent, unresolved). In a
    joint shot a frame counts as closed only when every present performer is closed,
    matching the joint wording. ``quiet`` needs no open frame, the coverage minimum, and no
    unmeasured stretch as long as the shortest silent interval. Missing measurements never
    produce a fact.
    """
    if not performers or len(performers) != len(presence):
        raise ValueError("every performer needs a mouth series and presence")
    per_performer = [frame_states(s, p, policy) for s, p in zip(performers, presence)]
    frames = len(per_performer[0])
    if any(len(states) != frames for states in per_performer):
        raise ValueError("mouth series must share the window's frames")
    states = [_joint(column) for column in zip(*per_performer)]
    present = sum(s != "absent" for s in states)
    closed = states.count("closed")
    opened = states.count("open")
    if not closed and "open" not in states:
        return MouthFacts(
            False,
            (),
            f"Source lips unmeasured (faces below {policy.min_face_height_px:g} px or not "
            "found); the mouth follows the source.",
            opened,
            closed + opened,
        )
    gap = max((b - a for a, b in _runs(states, "unmeasured")), default=0)
    if (
        "open" not in states
        and closed >= policy.quiet_min_coverage * present
        and gap < policy.silent_min_frames
    ):
        return MouthFacts(
            True,
            (),
            f"Quiet mouth from the source lips: closed in {closed}/{present} frames.",
            opened,
            closed + opened,
        )
    runs = [r for r in _runs(states, "closed") if r[1] - r[0] >= policy.silent_min_frames]
    runs = tuple(sorted(sorted(runs, key=lambda r: r[0] - r[1])[:max_intervals]))
    if not runs:
        return MouthFacts(
            False, (), "Source lips move; the mouth follows the source.", opened, closed + opened
        )
    spans = ", ".join(f"{a}-{b - 1}" for a, b in runs)
    return MouthFacts(
        False, runs, f"Closed source lips at frames {spans}.", opened, closed + opened
    )
