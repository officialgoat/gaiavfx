"""Pure head-track geometry; pixel masks and media belong to application steps."""

import math
from dataclasses import dataclass
from statistics import median

from gaiavfx.modules.direction import person_crop


@dataclass(frozen=True)
class HeadPolicy:
    min_coverage: float = 0.5
    min_face_height_px: float = 48
    expand: float = 1.8
    smoothing_window: int = 4
    up_shift: float = 0.1
    canvas_sides: tuple[int, ...] = (512, 640)
    min_scale: float = 1.0
    mask_dilate_px: int = 12
    mask_feather_px: int = 16
    treatment: str = "rgb"

    def __post_init__(self):
        for name in ("min_coverage", "min_face_height_px", "expand", "up_shift", "min_scale"):
            value = getattr(self, name)
            if type(value) not in {int, float} or not math.isfinite(value) or value < 0:
                raise ValueError(f"invalid head {name}")
        if self.min_coverage > 1 or self.expand <= 0 or self.min_face_height_px <= 0:
            raise ValueError("invalid head coverage or size")
        for name in ("smoothing_window", "mask_dilate_px", "mask_feather_px"):
            if type(getattr(self, name)) is not int or getattr(self, name) < 0:
                raise ValueError(f"invalid head {name}")
        if (
            not isinstance(self.canvas_sides, tuple)
            or not self.canvas_sides
            or any(type(s) is not int or s <= 0 or s % 32 for s in self.canvas_sides)
            or tuple(sorted(set(self.canvas_sides))) != self.canvas_sides
        ):
            raise ValueError("head canvas sides must be ascending distinct multiples of 32")
        if self.treatment != "rgb":
            raise ValueError("head treatment must be rgb")


class HeadGap(ValueError):
    def __init__(self, code, message):
        self.code, self.message = code, message
        super().__init__(message)


def head_track(face_boxes, presence, policy):
    """Nearest-frame fill (earlier frame wins ties), then median-smoothed squares."""
    if len(face_boxes) != len(presence):
        raise ValueError("face boxes must cover every delivered frame")
    measured = {i: tuple(b) for i, b in enumerate(face_boxes) if b is not None}
    for box in measured.values():
        if (
            len(box) != 4
            or any(type(v) not in {int, float} or not math.isfinite(v) for v in box)
            or box[2] <= box[0]
            or box[3] <= box[1]
        ):
            raise ValueError("invalid face box")
    present = presence.count("present")
    coverage = sum(presence[i] == "present" for i in measured) / present if present else 0
    if not measured or coverage < policy.min_coverage:
        raise HeadGap(
            "no_face_track",
            "No usable face track in this window; analyse the shot again if its analysis predates the window face scan.",
        )
    if median(b[3] - b[1] for b in measured.values()) < policy.min_face_height_px:
        raise HeadGap("face_too_small", "The measured face is too small for a head pass.")
    filled = []
    for i in range(len(face_boxes)):
        x0, y0, x1, y1 = measured[min(measured, key=lambda j: (abs(j - i), j))]
        filled.append(
            (
                (x0 + x1) / 2,
                (y0 + y1) / 2 - policy.up_shift * (y1 - y0),
                policy.expand * max(x1 - x0, y1 - y0),
            )
        )
    result = []
    for i in range(len(filled)):
        rows = filled[max(0, i - policy.smoothing_window) : i + policy.smoothing_window + 1]
        x, y, side = (median(row[k] for row in rows) for k in range(3))
        result.append((x - side / 2, y - side / 2, x + side / 2, y + side / 2))
    return tuple(result)


def head_region(track, native, margin):
    return person_crop.grow(person_crop.union(track), margin, native)


def head_plan(face_boxes, presence, native, policy, margin):
    """Return person_crop geometry or a typed geometry gap, without model dependencies."""
    region = head_region(head_track(face_boxes, presence, policy), native, margin)
    for side in policy.canvas_sides:
        geometry = person_crop.fit(region, (side, side), native, margin, (0, 0, *native))
        if geometry is not None and geometry["scale"] >= policy.min_scale:
            return geometry
    raise HeadGap(
        "head_too_large", "The body pass already renders this head at native resolution or better."
    )
