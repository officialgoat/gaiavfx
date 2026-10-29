"""What an integration can lower, and the model-specific knowledge it carries (ADR 0010).

The core never names a model. An integration publishes `Capabilities` (which conditioning it can express and its
frame grid) and a `KnowledgePack` (per-model technique status and evidence, and the presets the plan shows, such
as how many frames after a cut a pinned frame lands). Wording and graph details stay inside the integration.
"""

import math
import tomllib
from dataclasses import dataclass, field
from enum import StrEnum

from gaiavfx.modules.direction.techniques import Block, Status
from gaiavfx.modules.direction.timebase import FrameGrid


class CapabilityGap(ValueError):
    """An integration cannot lower a construct; callers can plan from the stable code."""

    def __init__(self, code: str, message: str):
        self.code = code
        self.message = message
        super().__init__(message)


class KeyframeAnchor(StrEnum):
    START = "start"
    END = "end"
    # a frame a fixed number of frames after a cut, without a picture slot
    CUT_OFFSET = "cut_offset"


@dataclass(frozen=True)
class ReplacementSupport:
    guide_video: bool
    pictures: int
    treatments: frozenset[str]
    groupings: frozenset[str]
    framings: frozenset[str]
    canvas_budget_pixels: int
    canvas_step: int
    tested_frames: frozenset[int]
    max_performers: int
    canvas_low: int
    canvas_high: int
    canvas_aspect: tuple[float, float]
    audio_lock: bool = False
    motion_context: bool = False
    faceswap: bool = False
    masked_span: bool = False

    def __post_init__(self):
        if any(
            type(v) is not int or v < 1
            for v in (
                self.pictures,
                self.canvas_budget_pixels,
                self.canvas_step,
                self.max_performers,
                self.canvas_low,
                self.canvas_high,
            )
        ):
            raise ValueError("replacement capacities must be positive integers")
        if (
            self.canvas_high < self.canvas_low
            or not isinstance(self.canvas_aspect, tuple)
            or len(self.canvas_aspect) != 2
            or any(type(v) not in {int, float} or not math.isfinite(v) for v in self.canvas_aspect)
            or not 0 < self.canvas_aspect[0] <= self.canvas_aspect[1]
        ):
            raise ValueError("invalid replacement canvas limits")
        if any(
            type(v) is not bool
            for v in (
                self.guide_video,
                self.audio_lock,
                self.motion_context,
                self.faceswap,
                self.masked_span,
            )
        ):
            raise ValueError("replacement feature flags must be boolean")
        for values, allowed in (
            (self.treatments, {"rgb", "inverted"}),
            (self.groupings, {"single", "joint"}),
            (self.framings, {"crop", "pair", "active", "full", "head"}),
        ):
            if not isinstance(values, frozenset) or not values or not values <= allowed:
                raise ValueError("unknown replacement capability")
        if (
            not isinstance(self.tested_frames, frozenset)
            or not self.tested_frames
            or any(type(v) is not int or v < 1 for v in self.tested_frames)
        ):
            raise ValueError("tested_frames must contain positive frame counts")


@dataclass(frozen=True)
class Capabilities:
    integration: str
    picture_slots: int
    keyframes: frozenset[KeyframeAnchor]
    motion_context: bool
    media_guides: frozenset[str]
    frame_grid: FrameGrid
    replacement: ReplacementSupport | None = None

    def lacks(self, block: Block) -> str | None:
        """Why this integration cannot lower a block, or None when it can."""
        if block is Block.PLACE_VIEWS and self.picture_slots < 2:
            return "no picture slot is left for place views beside the identity picture"
        if block is Block.PINNED_FRAME and KeyframeAnchor.CUT_OFFSET not in self.keyframes:
            return "the model cannot pin a frame after a cut"
        return None


@dataclass(frozen=True)
class ViewsSheetPreset:
    columns: int
    gap: int
    gap_rgb: tuple[int, int, int]


@dataclass(frozen=True)
class KnowledgePack:
    integration: str
    pin_offset_frames: int | None
    status: dict[str, Status] = field(default_factory=dict)
    evidence: dict[str, tuple[str, ...]] = field(default_factory=dict)
    views_sheet: ViewsSheetPreset | None = None


def parse_pack(text: str) -> KnowledgePack:
    data = tomllib.loads(text)
    if set(data) - {"schema", "integration", "presets", "technique"} or data.get("schema") != 1:
        raise ValueError(
            "a knowledge pack has schema, integration, [presets] and [technique.<id>] tables only"
        )
    presets = data.get("presets", {})
    if set(presets) - {"pin_offset_frames", "views_sheet"}:
        raise ValueError(
            f"unknown presets {sorted(set(presets) - {'pin_offset_frames', 'views_sheet'})}"
        )
    offset = presets.get("pin_offset_frames")
    if offset is not None and (not isinstance(offset, int) or offset < 1):
        raise ValueError("pin_offset_frames is a positive whole number of frames")
    views_sheet = None
    if "views_sheet" in presets:
        sheet = presets["views_sheet"]
        if not isinstance(sheet, dict) or set(sheet) != {"columns", "gap", "gap_rgb"}:
            raise ValueError("presets.views_sheet needs columns, gap and gap_rgb only")
        columns, gap, rgb = sheet["columns"], sheet["gap"], sheet["gap_rgb"]
        if type(columns) is not int or columns < 1:
            raise ValueError("presets.views_sheet.columns must be a positive whole number")
        if type(gap) is not int or gap < 0:
            raise ValueError("presets.views_sheet.gap must be a nonnegative whole number")
        if (
            not isinstance(rgb, list)
            or len(rgb) != 3
            or any(type(channel) is not int or not 0 <= channel <= 255 for channel in rgb)
        ):
            raise ValueError("presets.views_sheet.gap_rgb must be three RGB bytes")
        views_sheet = ViewsSheetPreset(columns, gap, tuple(rgb))
    status, evidence = {}, {}
    for technique_id, row in data.get("technique", {}).items():
        if set(row) - {"status", "evidence"}:
            raise ValueError(
                f"technique.{technique_id}: only status and evidence may be set per model"
            )
        if "status" in row:
            status[technique_id] = Status(row["status"])
        if "evidence" in row:
            evidence[technique_id] = tuple(str(e) for e in row["evidence"])
    return KnowledgePack(
        integration=str(data.get("integration", "")),
        pin_offset_frames=offset,
        status=status,
        evidence=evidence,
        views_sheet=views_sheet,
    )
