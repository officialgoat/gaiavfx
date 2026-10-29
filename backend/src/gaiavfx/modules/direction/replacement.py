"""Model-free shot replacement facts and bounded planning; no execution or take authority."""

import math
import re
import tomllib
from dataclasses import dataclass, fields, replace
from fractions import Fraction
from typing import Literal

from gaiavfx.modules.direction import person_crop
from gaiavfx.modules.direction.capabilities import CapabilityGap, ReplacementSupport
from gaiavfx.modules.direction.head import HeadGap, HeadPolicy, head_plan
from gaiavfx.modules.direction.target_face import TargetFacePolicy
from gaiavfx.modules.direction.timebase import FrameGrid

Treatment = Literal["rgb", "inverted"]
Grouping = Literal["single", "joint"]
Framing = Literal["crop", "pair", "active", "full", "head"]
Presence = Literal["present", "absent", "unresolved"]
Check = Literal[
    "arcface_target",
    "residual_source",
    "mouth_aperture",
    "background_geometry",
    "letterbox_difference",
    "seam",
    "inverted_copy",
]
CHECKS = frozenset(
    {
        "arcface_target",
        "residual_source",
        "mouth_aperture",
        "background_geometry",
        "letterbox_difference",
        "seam",
        "inverted_copy",
    }
)


def _text(value, name):
    if not isinstance(value, str) or not value.strip() or any(c in value for c in "<>\r\n"):
        raise ValueError(f"{name} must be nonempty plain text")


def _number(value, name):
    if (
        isinstance(value, bool)
        or not isinstance(value, (int, float, Fraction))
        or not math.isfinite(value)
    ):
        raise ValueError(f"{name} must be finite")


def _integer(value, name, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError(f"{name} must be an integer >= {minimum}")


def _size(value, name):
    if not isinstance(value, tuple) or len(value) != 2:
        raise ValueError(f"{name} must be a width/height tuple")
    for v in value:
        _integer(v, name, 1)


def validate_selector(role: str, position: str):
    _text(role, "role")
    _text(position, "position")
    if not re.search(r"\b(performer|person|figure|subject)\b", role, re.IGNORECASE):
        raise ValueError("role must identify a performer, person, figure or subject")
    if re.search(
        r"\b(hair|haired|curly|blond|blonde|robe|jacket|coat|shirt|dress|trousers)\b",
        role + " " + position,
        re.IGNORECASE,
    ):
        raise ValueError("Neutral selector cannot repeat source hair or costume")


@dataclass(frozen=True)
class Box:
    x0: float
    y0: float
    x1: float
    y1: float

    def __post_init__(self):
        for v in self:
            _number(v, "box edge")
        if self.x0 >= self.x1 or self.y0 >= self.y1:
            raise ValueError("box must have positive area")

    def __iter__(self):
        return iter((self.x0, self.y0, self.x1, self.y1))

    def __getitem__(self, index):
        return tuple(self)[index]


@dataclass(frozen=True)
class Letterbox:
    fit: tuple[int, int]
    offset: tuple[int, int]
    canvas: tuple[int, int]

    def __post_init__(self):
        _size(self.fit, "fit")
        _size(self.canvas, "canvas")
        if not isinstance(self.offset, tuple) or len(self.offset) != 2:
            raise ValueError("offset needs two coordinates")
        for i, v in enumerate(self.offset):
            _integer(v, "offset")
            if v + self.fit[i] > self.canvas[i]:
                raise ValueError("fit extends beyond canvas")


@dataclass(frozen=True)
class Track:
    id: str
    box: Box
    presence: tuple[Presence, ...]
    identity_margin: float | None
    selected_by: Literal["user", "identity"]

    def __post_init__(self):
        _text(self.id, "track id")
        if not isinstance(self.box, Box):
            raise TypeError("track requires a Box")
        if (
            not isinstance(self.presence, tuple)
            or not self.presence
            or any(v not in {"present", "absent", "unresolved"} for v in self.presence)
        ):
            raise ValueError("invalid presence frames")
        if self.selected_by not in {"user", "identity"}:
            raise ValueError("invalid track selection")
        if self.identity_margin is not None:
            _number(self.identity_margin, "identity_margin")
            if not -2 <= self.identity_margin <= 2:
                raise ValueError("identity margin is a difference of cosine similarities")


@dataclass(frozen=True)
class Performer:
    track_id: str
    target_asset: str
    target_subject_id: str | None
    target_text: str | None
    role: str
    position: str
    action: str | None

    def __post_init__(self):
        validate_selector(self.role, self.position)
        for name in ("track_id", "target_asset"):
            _text(getattr(self, name), name)
        for name in ("target_subject_id", "target_text", "action"):
            if getattr(self, name) is not None:
                _text(getattr(self, name), name)


@dataclass(frozen=True)
class Bystander:
    track_id: str
    role: str
    position: str
    items: tuple[str, ...]

    def __post_init__(self):
        for name in ("track_id", "role", "position"):
            _text(getattr(self, name), name)
        if not isinstance(self.items, tuple):
            raise TypeError("items must be a tuple")
        for item in self.items:
            _text(item, "item")


@dataclass(frozen=True)
class Occluder:
    name: str
    owner: Literal["scene", "performer"]
    covers: str

    def __post_init__(self):
        _text(self.name, "occluder")
        _text(self.covers, "covers")
        if self.owner not in {"scene", "performer"}:
            raise ValueError("invalid occluder owner")


@dataclass(frozen=True)
class ReplacementFacts:
    source_asset: str
    shot_start: int
    shot_end: int
    native: tuple[int, int]
    fps: Fraction
    letterbox: Letterbox | None
    performers: tuple[Performer, ...]
    bystanders: tuple[Bystander, ...]
    occluders: tuple[Occluder, ...]
    scene_items: tuple[str, ...]
    quiet: bool
    silent_intervals: tuple[tuple[int, int], ...]
    # Track IDs need a model-free source of geometry, presence and selection evidence.
    tracks: tuple[Track, ...]
    performer_hands_visible: bool | None = None
    source_target_similarity: float | None = None
    required_context: tuple[Box, ...] = ()
    avoided_regions: tuple[Box, ...] = ()
    speaking: bool = False
    context_frames: int = 0
    close_up: bool = False
    handoff: Literal["none", "latent", "bridge"] = "none"
    lips_unmeasured: bool = False

    def __post_init__(self):
        _text(self.source_asset, "source_asset")
        if self.handoff not in {"none", "latent", "bridge"}:
            raise ValueError("invalid handoff")
        if self.handoff != "none" and self.context_frames:
            raise ValueError("handoff cannot also use pixel context")
        if type(self.close_up) is not bool:
            raise ValueError("close_up must be boolean")
        if type(self.lips_unmeasured) is not bool or (self.quiet and self.lips_unmeasured):
            raise ValueError("lips_unmeasured must be boolean and quiet windows cannot lock")
        if type(self.speaking) is not bool or (self.quiet and self.speaking):
            raise ValueError("speaking must be boolean and quiet windows cannot speak")
        _integer(self.shot_start, "shot_start")
        _integer(self.shot_end, "shot_end", self.shot_start + 1)
        _integer(self.context_frames, "context_frames")
        if self.context_frames >= self.delivered_frames:
            raise ValueError("context_frames must be shorter than the delivered window")
        if isinstance(self.fps, bool) or not isinstance(self.fps, (int, Fraction)) or self.fps <= 0:
            raise ValueError("fps must be a positive fraction")
        object.__setattr__(self, "fps", Fraction(self.fps))
        _size(self.native, "native")
        if self.letterbox is not None and not isinstance(self.letterbox, Letterbox):
            raise ValueError("invalid letterbox")
        if type(self.quiet) is not bool or (
            self.performer_hands_visible is not None
            and type(self.performer_hands_visible) is not bool
        ):
            raise ValueError("quiet and hands visibility must be boolean")
        for name, kind in (
            ("performers", Performer),
            ("bystanders", Bystander),
            ("tracks", Track),
            ("occluders", Occluder),
            ("scene_items", str),
            ("silent_intervals", tuple),
            ("required_context", Box),
            ("avoided_regions", Box),
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(v, kind) for v in values):
                raise ValueError(f"invalid {name}")
        ids = [p.track_id for p in (*self.performers, *self.bystanders)]
        tracks = {t.id: t for t in self.tracks}
        if (
            not self.performers
            or len(ids) != len(set(ids))
            or len(tracks) != len(self.tracks)
            or set(ids) != set(tracks)
        ):
            raise ValueError("performers and bystanders need distinct, resolved tracks")
        for track in self.tracks:
            if len(track.presence) != self.delivered_frames:
                raise ValueError("presence must cover every delivered frame")
            if (
                track.box.x0 < 0
                or track.box.y0 < 0
                or track.box.x1 > self.native[0]
                or track.box.y1 > self.native[1]
            ):
                raise ValueError("track box must be inside native frame")
        for box in (*self.required_context, *self.avoided_regions):
            if box.x0 < 0 or box.y0 < 0 or box.x1 > self.native[0] or box.y1 > self.native[1]:
                raise ValueError("context and avoided regions must be inside native frame")
        for item in self.scene_items:
            _text(item, "scene item")
        if self.quiet and self.silent_intervals:
            raise ValueError("quiet and silent_intervals are mutually exclusive")
        end = 0
        for interval in self.silent_intervals:
            if len(interval) != 2:
                raise ValueError("silent interval needs start and end")
            a, b = interval
            _integer(a, "interval start")
            _integer(b, "interval end")
            if not end <= a < b <= self.delivered_frames:
                raise ValueError("silent intervals must be ordered, disjoint and inside the shot")
            end = b
        if self.source_target_similarity is not None:
            _number(self.source_target_similarity, "source_target_similarity")
            if not -1 <= self.source_target_similarity <= 1:
                raise ValueError("similarity must be a cosine")

    @property
    def delivered_frames(self):
        return self.shot_end - self.shot_start


@dataclass(frozen=True)
class CropPlan:
    canvas: tuple[int, int]
    box: Box
    scale: float
    interior_edges: tuple[str, ...]
    feather: float
    clearance: float | None
    matrix: tuple[tuple[float, float, float], tuple[float, float, float]]

    def __post_init__(self):
        _size(self.canvas, "canvas")
        if not isinstance(self.box, Box):
            raise TypeError("crop requires a Box")
        for name in ("scale", "feather"):
            _number(getattr(self, name), name)
        if self.scale <= 0 or self.feather < 0:
            raise ValueError("invalid crop scale or clearance")
        if (
            not isinstance(self.interior_edges, tuple)
            or len(set(self.interior_edges)) != len(self.interior_edges)
            or not set(self.interior_edges) <= {"left", "top", "right", "bottom"}
        ):
            raise ValueError("invalid interior edges")
        if self.interior_edges:
            _number(self.clearance, "clearance")
            if self.clearance < self.feather:
                raise ValueError("clearance must cover the feather")
        elif self.clearance is not None:
            raise ValueError("clearance must be None without interior edges")
        if not math.isclose(
            (self.box.x1 - self.box.x0) * self.scale, self.canvas[0], abs_tol=1e-6
        ) or not math.isclose(
            (self.box.y1 - self.box.y0) * self.scale, self.canvas[1], abs_tol=1e-6
        ):
            raise ValueError("crop must have exact canvas aspect and scale")
        expected = person_crop.forward_matrix(self.box, self.scale)
        if (
            not isinstance(self.matrix, tuple)
            or len(self.matrix) != 2
            or any(not isinstance(row, tuple) or len(row) != 3 for row in self.matrix)
            or any(
                not math.isclose(v, e, abs_tol=1e-6)
                for row, exp in zip(self.matrix, expected)
                for v, e in zip(row, exp)
            )
        ):
            raise ValueError("matrix must use pixel-centre coordinates")

    @classmethod
    def from_geometry(cls, geometry, feather):
        return cls(
            tuple(geometry["canvas"]),
            Box(*geometry["box"]),
            geometry["scale"],
            tuple(k for k, v in geometry["interior_edges"].items() if v),
            feather,
            geometry["clearance"] if any(geometry["interior_edges"].values()) else None,
            tuple(
                tuple(row) for row in person_crop.forward_matrix(geometry["box"], geometry["scale"])
            ),
        )


@dataclass(frozen=True)
class Arm:
    treatment: Treatment
    grouping: Grouping
    framing: Framing
    crop: CropPlan | None
    seed_count: int
    round: int
    seed_offset: int = 0
    audio_lock: bool = False
    faceswap: bool = False

    def __post_init__(self):
        if type(self.faceswap) is not bool or (
            self.faceswap and (self.grouping != "single" or self.framing == "head")
        ):
            raise ValueError("faceswap requires a single performer outside a head pass")
        if type(self.audio_lock) is not bool or (self.audio_lock and self.framing == "head"):
            raise ValueError("audio_lock is forbidden in a head pass")
        if (
            self.treatment not in {"rgb", "inverted"}
            or self.grouping not in {"single", "joint"}
            or self.framing not in {"crop", "pair", "active", "full", "head"}
        ):
            raise ValueError("unknown replacement arm")
        if (self.framing in {"crop", "pair", "head"}) != isinstance(self.crop, CropPlan):
            raise ValueError("crop/pair/head require a CropPlan; other framings forbid it")
        if self.crop is not None and not isinstance(self.crop, CropPlan):
            raise ValueError("invalid crop")
        if (self.framing == "pair" and self.grouping != "joint") or (
            self.framing in {"crop", "head"} and self.grouping != "single"
        ):
            raise ValueError("crop is single; pair is joint")
        _integer(self.seed_count, "seed_count", 1)
        _integer(self.round, "round")
        _integer(self.seed_offset, "seed_offset")


@dataclass(frozen=True)
class CheckResult:
    check: Check
    verdict: Literal["pass", "fail", "unsupported"]
    advice: Literal["none", "retry"]
    measured: float | None
    reason: str = ""

    def __post_init__(self):
        if (
            self.check not in CHECKS
            or self.verdict not in {"pass", "fail", "unsupported"}
            or self.advice not in {"none", "retry"}
        ):
            raise ValueError("unknown check result")
        if self.measured is not None:
            _number(self.measured, "measured")
        if not isinstance(self.reason, str):
            raise TypeError("check reason must be text")


@dataclass(frozen=True)
class Attempt:
    arm: Arm
    # Successful composites only; arm.seed_count still charges every scheduled render.
    takes: tuple[tuple[CheckResult, ...], ...]

    def __post_init__(self):
        if (
            not isinstance(self.arm, Arm)
            or not isinstance(self.takes, tuple)
            or not 1 <= len(self.takes) <= self.arm.seed_count
        ):
            raise ValueError("attempt needs results for one or more successful rendered takes")
        for checks in self.takes:
            if not isinstance(checks, tuple) or any(not isinstance(c, CheckResult) for c in checks):
                raise ValueError("invalid take checks")
            if len({c.check for c in checks}) != len(checks):
                raise ValueError("duplicate check result in take")


@dataclass(frozen=True)
class Gap:
    code: str
    message: str

    def __post_init__(self):
        _text(self.code, "gap code")
        _text(self.message, "gap message")

    @classmethod
    def from_capability_gap(cls, gap: CapabilityGap):
        return cls(gap.code, gap.message)


@dataclass(frozen=True)
class ReplacementPlan:
    arm: Arm | None
    gaps: tuple[Gap, ...]
    reasons: tuple[str, ...]
    stop: bool
    technique_ids: tuple[str, ...]

    def __post_init__(self):
        if type(self.stop) is not bool or self.stop != (self.arm is None):
            raise ValueError("a plan has an arm or stops")
        if self.arm is not None and not isinstance(self.arm, Arm):
            raise ValueError("invalid planned arm")
        for name, kind in (
            ("gaps", Gap),
            ("reasons", str),
            ("technique_ids", str),
        ):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(v, kind) for v in values):
                raise ValueError(f"invalid plan {name}")


def _closed_tuple(values, allowed, name):
    if (
        not isinstance(values, tuple)
        or not values
        or any(not isinstance(v, str) or v not in allowed for v in values)
        or len(set(values)) != len(values)
    ):
        raise ValueError(f"invalid {name}")


@dataclass(frozen=True)
class Escalation:
    on: Literal["likeness", "geometry", "mouth"]
    checks: tuple[Check, ...]
    fires_on: tuple[Literal["fail", "retry"], ...]
    to: Literal["treatment", "framing", "faceswap", "seed"]

    def __post_init__(self):
        if self.on not in {"likeness", "geometry", "mouth"} or self.to not in {
            "treatment",
            "framing",
            "faceswap",
            "seed",
        }:
            raise ValueError("invalid escalation")
        if self.to == "faceswap" and (self.on != "likeness" or self.fires_on != ("fail",)):
            raise ValueError("faceswap escalation requires a likeness failure")
        _closed_tuple(self.checks, CHECKS, "escalation checks")
        _closed_tuple(self.fires_on, {"fail", "retry"}, "escalation fires_on")


@dataclass(frozen=True)
class TechniqueApplicability:
    treatment: tuple[Treatment, ...]
    grouping: tuple[Grouping, ...]
    framing: tuple[Framing, ...]

    def __post_init__(self):
        _closed_tuple(self.treatment, {"rgb", "inverted"}, "technique treatments")
        _closed_tuple(self.grouping, {"single", "joint"}, "technique groupings")
        _closed_tuple(
            self.framing, {"crop", "pair", "active", "full", "head"}, "technique framings"
        )

    def matches(self, arm: Arm):
        return (
            arm.treatment in self.treatment
            and arm.grouping in self.grouping
            and arm.framing in self.framing
        )


@dataclass(frozen=True)
class ReplacementTechnique:
    id: str
    title: str
    status: Literal["default", "developing", "experimental", "retired"]
    evidence: tuple[str, ...]
    limitations: str
    applies_to: TechniqueApplicability

    def __post_init__(self):
        if not isinstance(self.applies_to, TechniqueApplicability):
            raise TypeError("technique requires typed applicability")
        if self.status not in {"default", "developing", "experimental", "retired"}:
            raise ValueError("invalid technique status")
        for name in ("id", "title", "limitations"):
            _text(getattr(self, name), name)
        if not isinstance(self.evidence, tuple) or not self.evidence:
            raise ValueError("technique needs evidence")
        for evidence in self.evidence:
            _text(evidence, "evidence")
            if (
                evidence.startswith(("/", "\\"))
                or ":" in evidence.replace("::", "")
                or ".." in evidence.replace("\\", "/").split("/")
                or ".cache" in evidence
            ):
                raise ValueError("evidence must be a repository path or test id")


def _evidence(evidence, note, name):
    _text(evidence, f"{name} evidence")
    _text(note, f"{name} note")
    if (
        evidence.startswith(("/", "\\"))
        or "\\" in evidence
        or ":" in evidence
        or any(p in {"", ".", "..", ".cache"} for p in evidence.split("/"))
    ):
        raise ValueError(f"{name} evidence must be a repository path")


@dataclass(frozen=True)
class MouthPolicy:
    """When the source performer's own lips count as closed (106-point aperture / face height).

    Faces below ``min_face_height_px`` (native pixels) are unmeasured. ``quiet`` needs
    ``quiet_min_coverage`` of the present frames closed and none open; otherwise closed runs
    of at least ``silent_min_frames`` become silent intervals.
    """

    closed_aperture_at_most: float
    min_face_height_px: float
    quiet_min_coverage: float
    silent_min_frames: int
    evidence: str
    note: str

    def __post_init__(self):
        for name in ("closed_aperture_at_most", "min_face_height_px", "quiet_min_coverage"):
            _number(getattr(self, name), name)
        if not 0 < self.closed_aperture_at_most < 1 or self.min_face_height_px < 0:
            raise ValueError("invalid closed-mouth aperture or face height")
        if not 0 < self.quiet_min_coverage <= 1:
            raise ValueError("quiet_min_coverage must be a fraction above zero")
        _integer(self.silent_min_frames, "silent_min_frames", 1)
        _evidence(self.evidence, self.note, "mouth")


@dataclass(frozen=True)
class Calibration:
    """Inclusive, separated verdict regions; multiple pass bounds all must hold.

    Scale bounds measure absolute deviation from unit scale; shift is in crop pixels.
    Evaluation is table-driven and does not assign special verdicts by check name.
    """

    check: Check
    evidence: str
    note: str
    pass_at_most: float | None = None
    pass_at_least: float | None = None
    fail_at_least: float | None = None
    fail_at_most: float | None = None
    pass_scale_delta_at_most: float | None = None
    fail_scale_delta_at_least: float | None = None
    pass_shift_at_most: float | None = None
    fail_shift_at_least: float | None = None
    unsupported_advice: Literal["none", "retry"] = "none"

    def bounds(self):
        return {
            f.name: getattr(self, f.name)
            for f in fields(self)
            if f.name.startswith(("pass_", "fail_")) and getattr(self, f.name) is not None
        }

    def __post_init__(self):
        if self.check not in CHECKS or self.unsupported_advice not in {"none", "retry"}:
            raise ValueError("invalid calibration check or advice")
        _evidence(self.evidence, self.note, "calibration")
        bounds = self.bounds()
        if not bounds:
            raise ValueError("calibration needs bounds")
        for name, value in bounds.items():
            _number(value, name)
            if ("scale_delta" in name or "shift" in name) and value < 0:
                raise ValueError("geometry bounds must be nonnegative")
        scalar = any(
            k in bounds for k in ("pass_at_most", "pass_at_least", "fail_at_most", "fail_at_least")
        )
        if scalar and any("scale_delta" in k or "shift" in k for k in bounds):
            raise ValueError("cannot mix scalar and geometry calibration")
        has_pass = any(k.startswith("pass_") for k in bounds)
        for metric in ("", "scale_delta_", "shift_"):
            pmax = bounds.get(f"pass_{metric}at_most")
            pmin = bounds.get(f"pass_{metric}at_least")
            fmax = bounds.get(f"fail_{metric}at_most")
            fmin = bounds.get(f"fail_{metric}at_least")
            if (
                has_pass
                and (fmax is not None or fmin is not None)
                and pmax is None
                and pmin is None
            ):
                raise ValueError("overlapping calibration regions on an unbounded metric")
            if pmax is not None and pmin is not None and pmin > pmax:
                raise ValueError("contradictory pass bounds")
            if fmax is not None and fmin is not None and fmax >= fmin:
                raise ValueError("contradictory fail bounds")
            if has_pass and (
                (fmin is not None and (pmax is None or pmax >= fmin))
                or (fmax is not None and (pmin is None or pmin <= fmax))
            ):
                raise ValueError("overlapping calibration regions")


def calibrated_check(policy, check, measured, *, metrics=None, reason=""):
    row = next((r for r in policy.calibration if r.check == check), None)
    values = {"value": measured, **(metrics or {})}
    if row is None:
        return CheckResult(check, "unsupported", "none", measured, reason or "uncalibrated")
    matches = {"pass": [], "fail": []}
    for name, bound in row.bounds().items():
        verdict, rest = name.split("_", 1)
        metric, direction = rest.rsplit("at_", 1)
        value = values.get(metric.rstrip("_") or "value")
        if value is None:
            return CheckResult(
                check, "unsupported", row.unsupported_advice, measured, reason or "unmeasured"
            )
        _number(value, "measurement")
        inclusive = math.isclose(value, bound, rel_tol=1e-12, abs_tol=1e-12)
        matches[verdict].append(
            inclusive or (value <= bound if direction == "most" else value >= bound)
        )
    verdict = (
        "fail"
        if any(matches["fail"])
        else "pass"
        if matches["pass"] and all(matches["pass"])
        else "unsupported"
    )
    return CheckResult(
        check,
        verdict,
        row.unsupported_advice if verdict == "unsupported" else "none",
        measured,
        # The file name identifies the calibration evidence; the full path stays in the policy.
        "; ".join(v for v in (reason, "calibrated on " + row.evidence.rsplit("/", 1)[-1]) if v),
    )


@dataclass(frozen=True)
class FaceSwapPolicy:
    close_up_min_face_fraction: float
    evidence: str
    note: str
    # Target likeness at or below this fails a close-up take; wider framings read lower for
    # accepted takes and keep the check uncalibrated. Frozen policies retain their bound.
    close_up_likeness_fail_at_most: float | None = None
    likeness_evidence: str | None = None

    def __post_init__(self):
        _number(self.close_up_min_face_fraction, "close_up_min_face_fraction")
        if not 0 < self.close_up_min_face_fraction <= 1:
            raise ValueError("close_up_min_face_fraction must be a fraction above zero")
        _evidence(self.evidence, self.note, "faceswap")
        if (self.close_up_likeness_fail_at_most is None) != (self.likeness_evidence is None):
            raise ValueError("close-up likeness bound and its evidence go together")
        if self.close_up_likeness_fail_at_most is not None:
            _number(self.close_up_likeness_fail_at_most, "close_up_likeness_fail_at_most")
            if not -1 < self.close_up_likeness_fail_at_most < 1:
                raise ValueError("close-up likeness bound must be a cosine")
            _evidence(self.likeness_evidence, self.note, "faceswap likeness")


@dataclass(frozen=True)
class LatentChainPolicy:
    max_windows: int
    evidence: str
    note: str

    def __post_init__(self):
        _integer(self.max_windows, "max_windows", 3)
        _evidence(self.evidence, self.note, "latent_chain")


@dataclass(frozen=True)
class ShotCropPolicy:
    max_scale_loss: float
    evidence: str
    note: str

    def __post_init__(self):
        _number(self.max_scale_loss, "max_scale_loss")
        if not 0 <= self.max_scale_loss <= 1:
            raise ValueError("max_scale_loss must be a fraction between zero and one")
        _evidence(self.evidence, self.note, "shot_crop")


@dataclass(frozen=True)
class TonePolicy:
    smooth_frames: int
    gain_min: float
    gain_max: float
    min_agreeing_fraction: float
    min_pixels: int
    dilate_fraction: float
    evidence: str
    note: str
    min_window_agreeing_fraction: float | None = None

    def __post_init__(self):
        _integer(self.smooth_frames, "smooth_frames", 1)
        _integer(self.min_pixels, "min_pixels", 1)
        if self.smooth_frames % 2 != 1:
            raise ValueError("smooth_frames must be odd")
        for name in ("gain_min", "gain_max", "min_agreeing_fraction", "dilate_fraction"):
            _number(getattr(self, name), name)
        if not 0 < self.gain_min <= 1 <= self.gain_max:
            raise ValueError("tone gains must bracket neutral")
        if not 0 < self.min_agreeing_fraction <= 1 or not 0 <= self.dilate_fraction <= 1:
            raise ValueError("invalid tone fractions")
        _evidence(self.evidence, self.note, "tone_anchor")
        if self.min_window_agreeing_fraction is not None:
            _number(self.min_window_agreeing_fraction, "min_window_agreeing_fraction")
            if not 0 < self.min_window_agreeing_fraction <= 1:
                raise ValueError("min_window_agreeing_fraction must be a fraction above zero")


@dataclass(frozen=True)
class DraftPolicy:
    renders_per_round: tuple[int, ...]
    retry_on: tuple[str, ...]
    evidence: str
    note: str

    def __post_init__(self):
        if self.renders_per_round != (1, 1) or any(
            type(n) is not int for n in self.renders_per_round
        ):
            raise ValueError("draft requires one render and one retry")
        if self.retry_on != ("likeness",):
            raise ValueError("draft retries only on likeness")
        _evidence(self.evidence, self.note, "draft")


@dataclass(frozen=True)
class ReplacementPolicy:
    max_rounds: int
    renders: int
    renders_per_round: tuple[int, ...]
    take_rule: Literal["all", "any"]
    identity_margin_min: float
    feather: float
    safety: float
    dilate: float
    escalation: tuple[Escalation, ...]
    techniques: tuple[ReplacementTechnique, ...]
    inverted_when_similarity_at_least: float | None = None
    treatment: Treatment = "rgb"
    calibration: tuple[Calibration, ...] = ()
    min_bystander_area_fraction: float = 0.0
    min_bystander_presence: float = 0.0
    min_face_height_fraction: float = 0.0
    min_face_frames_fraction: float = 0.0
    frame_rates: tuple[tuple[Fraction, str], ...] = ((Fraction(24), "tested"),)
    max_fps: int = 30
    mouth: MouthPolicy | None = None
    head: HeadPolicy | None = None
    faceswap: FaceSwapPolicy | None = None
    audio_lock: Literal["speaking", "never"] = "never"
    speaking_min_open_frames: int = 12
    target_face: TargetFacePolicy | None = None
    tone_anchor: TonePolicy | None = None
    first_arm_faceswap: Literal["close_up", "never"] = "never"
    # Absence retains legacy recast escalation; an explicit "never" pins it off per shot.
    first_arm_faceswap_configured: bool = False
    audio_lock_groupings: tuple[Grouping, ...] = ("single",)
    audio_lock_unmeasured: tuple[Grouping, ...] = ()
    first_arm_evidence: str | None = None
    first_arm_note: str | None = None
    draft: DraftPolicy | None = None
    shot_crop: ShotCropPolicy | None = None
    latent_chain: LatentChainPolicy | None = None

    def fps_status(self, fps, *, variable=False):
        if variable or fps <= 0 or fps > self.max_fps:
            return "unsupported"
        return dict(self.frame_rates).get(Fraction(fps), "experimental")

    def __post_init__(self):
        if self.latent_chain is not None and not isinstance(self.latent_chain, LatentChainPolicy):
            raise ValueError("invalid latent_chain policy")
        if self.shot_crop is not None and not isinstance(self.shot_crop, ShotCropPolicy):
            raise ValueError("invalid shot_crop policy")
        if self.draft is not None and not isinstance(self.draft, DraftPolicy):
            raise ValueError("invalid draft policy")
        if self.first_arm_faceswap not in {"close_up", "never"}:
            raise ValueError("invalid first_arm faceswap")
        for name in ("audio_lock_groupings", "audio_lock_unmeasured"):
            values = getattr(self, name)
            if (
                not isinstance(values, tuple)
                or len(values) != len(set(values))
                or set(values) - {"single", "joint"}
            ):
                raise ValueError(f"invalid {name}")
        if self.first_arm_evidence is not None or self.first_arm_note is not None:
            _evidence(self.first_arm_evidence, self.first_arm_note, "first_arm")
        if self.tone_anchor is not None and not isinstance(self.tone_anchor, TonePolicy):
            raise ValueError("invalid tone_anchor policy")
        for name in (
            "min_bystander_area_fraction",
            "min_bystander_presence",
            "min_face_height_fraction",
            "min_face_frames_fraction",
        ):
            value = getattr(self, name)
            _number(value, name)
            if not 0 <= value <= 1:
                raise ValueError(f"{name} must be a fraction between zero and one")
        if self.treatment not in {"rgb", "inverted"}:
            raise ValueError("invalid first_arm treatment")
        if self.audio_lock not in {"speaking", "never"}:
            raise ValueError("invalid first_arm audio_lock")
        _integer(self.speaking_min_open_frames, "speaking_min_open_frames", 1)
        if self.faceswap is not None and not isinstance(self.faceswap, FaceSwapPolicy):
            raise ValueError("invalid faceswap policy")
        if self.head is not None and not isinstance(self.head, HeadPolicy):
            raise ValueError("invalid head policy")
        if self.target_face is not None and not isinstance(self.target_face, TargetFacePolicy):
            raise ValueError("invalid target_face policy")
        if self.mouth is not None and not isinstance(self.mouth, MouthPolicy):
            raise ValueError("invalid mouth policy")
        if not isinstance(self.calibration, tuple) or any(
            not isinstance(r, Calibration) for r in self.calibration
        ):
            raise ValueError("invalid calibration")
        if len({r.check for r in self.calibration}) != len(self.calibration):
            raise ValueError("duplicate calibration check")
        if self.take_rule not in {"all", "any"}:
            raise ValueError("take_rule must be all or any")
        _integer(self.max_rounds, "max_rounds", 1)
        _integer(self.renders, "renders", 1)
        if (
            not isinstance(self.renders_per_round, tuple)
            or len(self.renders_per_round) != self.max_rounds
        ):
            raise ValueError("renders_per_round must cover max_rounds")
        for count in self.renders_per_round:
            _integer(count, "round renders", 1)
        for name in ("identity_margin_min", "feather", "safety", "dilate"):
            _number(getattr(self, name), name)
            if getattr(self, name) < 0:
                raise ValueError(f"{name} must be nonnegative")
        if self.identity_margin_min > 2:
            raise ValueError("invalid identity margin minimum")
        threshold = self.inverted_when_similarity_at_least
        if threshold is not None:
            _number(threshold, "similarity threshold")
            if not -1 <= threshold <= 1:
                raise ValueError("threshold must be a cosine")
        for name, kind in (("escalation", Escalation), ("techniques", ReplacementTechnique)):
            values = getattr(self, name)
            if not isinstance(values, tuple) or any(not isinstance(v, kind) for v in values):
                raise ValueError(f"invalid {name}")
        if len({t.id for t in self.techniques}) != len(self.techniques):
            raise ValueError("duplicate technique id")


def minor_bystander_reason(track: Track, native, policy: ReplacementPolicy):
    """Classify an unselected person by box area and present-frame fraction."""
    box = track.box
    area = (box.x1 - box.x0) * (box.y1 - box.y0) / (native[0] * native[1])
    present = track.presence.count("present") / len(track.presence)
    if area < policy.min_bystander_area_fraction or present < policy.min_bystander_presence:
        return (
            f"Ignored minor person track {track.id}: area={area:.6f} of frame, "
            f"presence={present:.6f} of shot; dropped its automatic avoided region and inversion protection."
        )
    return None


def parse_replacement_policy(text: str) -> ReplacementPolicy:
    data = tomllib.loads(text)
    if (
        set(data)
        - {
            "schema",
            "loop",
            "first_arm",
            "escalation",
            "technique",
            "calibration",
            "analysis",
            "frame_rates",
            "mouth",
            "head",
            "target_face",
            "faceswap",
            "tone_anchor",
            "draft",
            "shot_crop",
            "latent_chain",
        }
        or not {"schema", "loop", "escalation", "technique"} <= set(data)
        or type(data["schema"]) is not int
        or data["schema"] != 1
    ):
        raise ValueError("replacement policy needs schema = 1, loop, escalation and technique")
    rates = data.get(
        "frame_rates", {"max": 30, "tested": ["24"], "developing": [], "experimental": []}
    )
    if not isinstance(rates, dict) or set(rates) != {"max", "tested", "developing", "experimental"}:
        raise ValueError("invalid frame_rates keys")
    _integer(rates["max"], "maximum frame rate", 1)
    rows = []
    for status in ("tested", "developing", "experimental"):
        if not isinstance(rates[status], list):
            raise TypeError("frame rate statuses must be arrays")
        for rate in rates[status]:
            if not isinstance(rate, str):
                raise TypeError("frame rates must be rational strings")
            fps = Fraction(rate)
            if not 0 < fps <= rates["max"] or fps in dict(rows):
                raise ValueError("invalid or duplicate frame rate")
            rows.append((fps, status))
    frame_rates = tuple(rows)
    loop = data["loop"]
    if not isinstance(loop, dict) or set(loop) != {
        "max_rounds",
        "renders",
        "renders_per_round",
        "take_rule",
        "identity_margin_min",
        "feather",
        "safety",
        "dilate",
    }:
        raise ValueError("invalid loop keys")
    if not isinstance(loop["renders_per_round"], list):
        raise TypeError("renders_per_round must be an array")
    first = data.get("first_arm", {})
    analysis = data.get("analysis", {})
    if not isinstance(analysis, dict) or set(analysis) - {
        "min_bystander_area_fraction",
        "min_bystander_presence",
        "min_face_height_fraction",
        "min_face_frames_fraction",
    }:
        raise ValueError("invalid analysis keys")
    if not isinstance(first, dict) or set(first) - {
        "inverted_when_similarity_at_least",
        "treatment",
        "audio_lock",
        "speaking_min_open_frames",
        "faceswap",
        "audio_lock_groupings",
        "audio_lock_unmeasured",
        "evidence",
        "note",
    }:
        raise ValueError("invalid first_arm keys")
    if set(first) & {
        "faceswap",
        "audio_lock_groupings",
        "audio_lock_unmeasured",
        "evidence",
        "note",
    }:
        _evidence(first.get("evidence"), first.get("note"), "first_arm")
    first = dict(first)
    first["first_arm_faceswap_configured"] = "faceswap" in first
    for name in ("audio_lock_groupings", "audio_lock_unmeasured"):
        if name in first:
            if not isinstance(first[name], list):
                raise ValueError(f"{name} must be an array")
            first[name] = tuple(first[name])
    for name in ("faceswap", "evidence", "note"):
        if name in first:
            first["first_arm_" + name] = first.pop(name)
    draft = data.get("draft")
    if draft is not None:
        if not isinstance(draft, dict) or set(draft) != {f.name for f in fields(DraftPolicy)}:
            raise ValueError("invalid draft keys")
        if any(not isinstance(draft[name], list) for name in ("renders_per_round", "retry_on")):
            raise ValueError("draft rounds and triggers must be arrays")
        draft = DraftPolicy(
            **{
                **draft,
                "renders_per_round": tuple(draft["renders_per_round"]),
                "retry_on": tuple(draft["retry_on"]),
            }
        )
    latent_chain = data.get("latent_chain")
    if latent_chain is not None:
        if not isinstance(latent_chain, dict) or set(latent_chain) != {
            f.name for f in fields(LatentChainPolicy)
        }:
            raise ValueError("invalid latent_chain keys")
        latent_chain = LatentChainPolicy(**latent_chain)
    shot_crop = data.get("shot_crop")
    if shot_crop is not None:
        if not isinstance(shot_crop, dict) or set(shot_crop) != {
            f.name for f in fields(ShotCropPolicy)
        }:
            raise ValueError("invalid shot_crop keys")
        shot_crop = ShotCropPolicy(**shot_crop)
    tone = data.get("tone_anchor")
    if tone is not None and (
        not isinstance(tone, dict)
        or not {f.name for f in fields(TonePolicy)} - {"min_window_agreeing_fraction"}
        <= set(tone)
        <= {f.name for f in fields(TonePolicy)}
    ):
        raise ValueError("invalid tone_anchor keys")
    target_face = data.get("target_face")
    if target_face is not None and (
        not isinstance(target_face, dict)
        or set(target_face) != {f.name for f in fields(TargetFacePolicy)}
    ):
        raise ValueError("invalid target_face keys")
    mouth = data.get("mouth")
    if mouth is not None and (
        not isinstance(mouth, dict) or set(mouth) != {f.name for f in fields(MouthPolicy)}
    ):
        raise ValueError("invalid mouth keys")
    faceswap = data.get("faceswap")
    if faceswap is not None:
        optional = {"close_up_likeness_fail_at_most", "likeness_evidence"}
        names = {f.name for f in fields(FaceSwapPolicy)}
        if not isinstance(faceswap, dict) or not names - optional <= set(faceswap) <= names:
            raise ValueError("invalid faceswap keys")
        faceswap = FaceSwapPolicy(**faceswap)
    head = data.get("head")
    if head is not None:
        if not isinstance(head, dict) or set(head) != {f.name for f in fields(HeadPolicy)}:
            raise ValueError("invalid head keys")
        if not isinstance(head["canvas_sides"], list):
            raise ValueError("head canvas_sides must be an array")
        head = HeadPolicy(**{**head, "canvas_sides": tuple(head["canvas_sides"])})
    calibration = data.get("calibration", [])
    if not isinstance(calibration, list):
        raise TypeError("calibration must be an array of tables")
    for row in calibration:
        if (
            not isinstance(row, dict)
            or set(row) - {f.name for f in fields(Calibration)}
            or not {"check", "evidence", "note"} <= set(row)
        ):
            raise ValueError("invalid calibration keys")
    parsed = {}
    for name, kind, keys, arrays in (
        ("escalation", Escalation, {"on", "checks", "fires_on", "to"}, {"checks", "fires_on"}),
        (
            "technique",
            ReplacementTechnique,
            {"id", "title", "status", "evidence", "limitations", "applies_to"},
            {"evidence"},
        ),
    ):
        if not isinstance(data[name], list):
            raise TypeError(f"{name} must be an array of tables")
        rows = []
        for row in data[name]:
            if not isinstance(row, dict) or set(row) != keys:
                raise ValueError(f"invalid {name} keys")
            if any(not isinstance(row[key], list) for key in arrays):
                raise ValueError(f"invalid {name} arrays")
            if name == "technique":
                applies = row["applies_to"]
                if not isinstance(applies, dict) or set(applies) != {
                    "treatment",
                    "grouping",
                    "framing",
                }:
                    raise ValueError("invalid technique applies_to keys")
                if any(not isinstance(v, list) for v in applies.values()):
                    raise ValueError("applies_to values must be arrays")
                row = {
                    **row,
                    "applies_to": TechniqueApplicability(
                        **{k: tuple(v) for k, v in applies.items()}
                    ),
                }
            rows.append(
                kind(
                    **{key: tuple(value) if key in arrays else value for key, value in row.items()}
                )
            )
        parsed[name] = tuple(rows)
    return ReplacementPolicy(
        **{**loop, "renders_per_round": tuple(loop["renders_per_round"])},
        frame_rates=frame_rates,
        max_fps=rates["max"],
        escalation=parsed["escalation"],
        techniques=parsed["technique"],
        calibration=tuple(Calibration(**row) for row in calibration),
        mouth=None if mouth is None else MouthPolicy(**mouth),
        head=head,
        target_face=None if target_face is None else TargetFacePolicy(**target_face),
        faceswap=faceswap,
        tone_anchor=None if tone is None else TonePolicy(**tone),
        draft=draft,
        shot_crop=shot_crop,
        latent_chain=latent_chain,
        **analysis,
        **first,
    )


def presence(area, median, iou, overlap):
    """A missing face is never absence; only an empty track is."""
    if area == 0:
        return "absent"
    if area >= 0.5 * median and (iou is None or iou >= 0.6) and overlap <= 0.01:
        return "present"
    return "unresolved"


def scope_gaps(facts, support, policy, grid):
    gaps = []
    if facts.handoff != "none" and (support is None or not support.masked_span):
        gaps.append(Gap("handoff_unsupported", "integration does not support masked spans"))
    if facts.context_frames and (support is None or not support.motion_context):
        gaps.append(Gap("chain_unsupported", "integration does not support motion context"))
    if facts.context_frames not in {0, grid.context_default}:
        gaps.append(Gap("untested_context", "only 22 context frames are tested"))
    if support is None or not support.guide_video:
        gaps.append(Gap("replacement_unsupported", "integration does not support replacement"))
        return tuple(gaps)
    if len(facts.performers) > min(2, support.max_performers, support.pictures):
        gaps.append(Gap("too_many_performers", "performer count exceeds the tested capability"))
    try:
        window = grid.window_for(facts.delivered_frames, 0)
    except ValueError:
        window = max(support.tested_frames) + 1
    if window > max(support.tested_frames):
        gaps.append(Gap("untested_length", "generated window exceeds the tested lengths"))
    if policy.fps_status(facts.fps) == "unsupported":
        gaps.append(Gap("unsupported_fps", "shot frame rate is unsupported by replacement policy"))
    tracks = {t.id: t for t in facts.tracks}
    for performer in facts.performers:
        track = tracks[performer.track_id]
        if track.selected_by == "identity" and (
            track.identity_margin is None or track.identity_margin < policy.identity_margin_min
        ):
            gaps.append(
                Gap("ambiguous_identity", "identity selection requires an unambiguous margin")
            )
        if all(state == "absent" for state in track.presence):
            gaps.append(Gap("performer_absent", "performer is absent throughout the shot"))
        if "unresolved" in track.presence:
            gaps.append(Gap("presence_unresolved", "performer presence remains unresolved"))
    return tuple(gaps)


def plan_head_pass(facts, face_boxes, support, policy, grid, prior=()):
    gaps = scope_gaps(facts, support, policy, grid)
    if gaps:
        return ReplacementPlan(None, gaps, ("scope gaps",), True, ())
    if (
        policy.head is None
        or "head" not in support.framings
        or "single" not in support.groupings
        or "rgb" not in support.treatments
    ):
        return ReplacementPlan(
            None,
            (Gap("replacement_unsupported", "integration does not support head replacement"),),
            (),
            True,
            (),
        )
    if len(facts.performers) != 1:
        return ReplacementPlan(
            None, (Gap("too_many_performers", "A head pass refines one performer."),), (), True, ()
        )
    if prior:
        return ReplacementPlan(None, (), ("head pass runs one round",), True, ())
    sides = tuple(
        s
        for s in policy.head.canvas_sides
        if support.canvas_low <= s <= support.canvas_high
        and s % support.canvas_step == 0
        and s * s <= support.canvas_budget_pixels
        and support.canvas_aspect[0] <= 1 <= support.canvas_aspect[1]
    )
    if not sides:
        return ReplacementPlan(
            None, (Gap("replacement_unsupported", "No supported head canvas."),), (), True, ()
        )
    track = next(t for t in facts.tracks if t.id == facts.performers[0].track_id)
    try:
        geometry = head_plan(
            face_boxes,
            track.presence,
            facts.native,
            replace(policy.head, canvas_sides=sides),
            policy.feather + policy.safety,
        )
    except HeadGap as exc:
        return ReplacementPlan(None, (Gap(exc.code, exc.message),), (), True, ())
    arm = Arm(
        policy.head.treatment,
        "single",
        "head",
        CropPlan.from_geometry(geometry, policy.feather),
        1,
        0,
    )
    return ReplacementPlan(
        arm,
        (),
        (),
        False,
        tuple(
            t.id
            for t in policy.techniques
            if t.status in {"default", "developing"} and t.applies_to.matches(arm)
        ),
    )


def crop_for(required_boxes, avoided, native, policy, support) -> CropPlan:
    """Plan one crop against required and avoided native regions; refuse no fit."""
    required = person_crop.union(required_boxes)
    bounds = person_crop.limits(required, avoided, native)
    geometry, _ = person_crop.choose(
        required,
        native,
        policy.feather + policy.safety,
        bounds,
        person_crop.canvas_candidates(
            support.canvas_budget_pixels,
            support.canvas_step,
            support.canvas_low,
            support.canvas_high,
            support.canvas_aspect,
        ),
    )
    return CropPlan.from_geometry(geometry, policy.feather)


def _crop_regions(facts, policy):
    tracks = {t.id: t for t in facts.tracks}
    required = (
        *(
            person_crop.grow(tracks[p.track_id].box, policy.dilate, facts.native)
            for p in facts.performers
        ),
        *facts.required_context,
    )
    avoided = [
        (b.track_id, person_crop.grow(tracks[b.track_id].box, policy.dilate, facts.native))
        for b in facts.bystanders
    ] + [(f"avoided region {i}", box) for i, box in enumerate(facts.avoided_regions)]
    return required, avoided


def union_crop(facts_list, policy, support) -> CropPlan:
    facts_list = tuple(facts_list)
    if not facts_list or any(f.native != facts_list[0].native for f in facts_list):
        raise ValueError("shared crop needs windows on the same native canvas")
    parts = [_crop_regions(f, policy) for f in facts_list]
    return crop_for(
        [box for required, _ in parts for box in required],
        [region for _, avoided in parts for region in avoided],
        facts_list[0].native,
        policy,
        support,
    )


def plan_bridge(facts, arm, crop, support, policy, grid, prior=()):
    """A join regenerates once, keeping its successor's chosen treatment and locks."""
    if prior:
        return ReplacementPlan(None, (), ("bridge runs one round",), True, ())
    gaps = scope_gaps(facts, support, policy, grid)
    if gaps:
        return ReplacementPlan(None, gaps, ("scope gaps",), True, ())
    arm = replace(
        arm,
        framing="pair" if len(facts.performers) == 2 else "crop",
        crop=crop,
        seed_count=1,
        round=0,
        seed_offset=0,
    )
    return ReplacementPlan(
        arm,
        (),
        (
            f"Bridge inherits {arm.grouping} audio lock from its successor"
            + ("; lips unmeasured." if facts.lips_unmeasured and not facts.speaking else "."),
        )
        if arm.audio_lock and policy.first_arm_evidence is not None
        else (),
        False,
        tuple(
            t.id
            for t in policy.techniques
            if t.status in {"default", "developing"} and t.applies_to.matches(arm)
        ),
    )


def plan_replacement(
    facts: ReplacementFacts,
    support: ReplacementSupport | None,
    policy: ReplacementPolicy,
    grid: FrameGrid,
    prior: tuple[Attempt, ...] = (),
    shared_crop: CropPlan | None = None,
) -> ReplacementPlan:
    """Return a proposed arm or a reasoned stop; rounds are zero-based."""

    def techniques_for(arm):
        if arm is None:
            return ()
        return tuple(
            t.id
            for t in policy.techniques
            if t.status in {"default", "developing"} and t.applies_to.matches(arm)
        )

    techniques = ()
    gaps = []
    reasons = []

    def stop(reason):
        return ReplacementPlan(None, tuple(gaps), (*reasons, reason), True, techniques)

    gaps.extend(scope_gaps(facts, support, policy, grid))
    if gaps:
        return stop(
            "replacement unsupported"
            if support is None or not support.guide_video
            else "scope gaps"
        )
    if not isinstance(prior, tuple) or any(
        not isinstance(a, Attempt) or a.arm.round != i for i, a in enumerate(prior)
    ):
        raise ValueError("prior attempts must be contiguous zero-based rounds")
    used = 0
    for attempt in prior:
        if attempt.arm.seed_offset != used:
            raise ValueError("prior seed offsets must follow renders used")
        used += attempt.arm.seed_count
    round_index = len(prior)
    remaining = policy.renders - used
    if prior:
        techniques = techniques_for(prior[-1].arm)
        checks = tuple(c for take in prior[-1].takes for c in take)
        calibrated = [c for c in checks if c.verdict != "unsupported"]
        if (
            calibrated
            and all(c.verdict == "pass" for c in calibrated)
            and all(c.advice == "none" for c in checks)
        ):
            return stop("all calibrated checks passed")
    if round_index >= policy.max_rounds:
        return stop("maximum rounds reached")
    if remaining <= 0:
        return stop("render budget exhausted")
    count = min(policy.renders_per_round[round_index], remaining)
    if prior:
        last = prior[-1]
        aggregate = all if policy.take_rule == "all" else any
        matched = False
        for row in policy.escalation:
            if shared_crop is not None and row.to == "framing":
                continue
            if row.to == "faceswap" and not (
                policy.faceswap is not None
                and facts.close_up
                and last.arm.grouping == "single"
                and last.arm.framing != "head"
                and not last.arm.faceswap
                and support.faceswap
            ):
                continue
            fires = aggregate(
                any(
                    c.check in row.checks
                    and (
                        (c.verdict == "fail" and "fail" in row.fires_on)
                        or (c.advice == "retry" and "retry" in row.fires_on)
                    )
                    for c in take
                )
                for take in last.takes
            )
            if not fires:
                continue
            matched = True
            changes = {
                "treatment": {"treatment": "inverted"},
                "framing": {"framing": "active", "crop": None},
                "seed": {},
                "faceswap": {"faceswap": True},
            }[row.to]
            arm = replace(
                last.arm, **changes, seed_count=count, round=round_index, seed_offset=used
            )
            # Treatment/framing retries need a new configuration; seed retries need a new offset.
            if row.to != "seed" and any(
                (a.arm.treatment, a.arm.grouping, a.arm.framing, a.arm.crop, a.arm.faceswap)
                == (arm.treatment, arm.grouping, arm.framing, arm.crop, arm.faceswap)
                for a in prior
            ):
                continue
            reasons.append(f"{row.on}: {row.to}")
            break
        else:
            if matched:
                return stop("escalation would repeat an earlier arm")
            return stop("no calibrated signal" if not calibrated else "no escalation trigger")
    else:
        joint = len(facts.performers) == 2
        framing = "pair" if joint else "crop"
        treatment = policy.treatment
        if policy.first_arm_evidence is not None:
            reasons.append(f"First arm: {treatment} guide.")
        wants_faceswap = policy.first_arm_faceswap == "close_up" and facts.close_up and not joint
        faceswap = wants_faceswap and policy.faceswap is not None and support.faceswap
        if faceswap:
            reasons.append("Close-up: FaceSwap from round 0.")
        elif wants_faceswap and not support.faceswap and treatment == "rgb":
            treatment = "inverted"
            reasons.append("Close-up without FaceSwap support: inverted guide fallback.")
        threshold = policy.inverted_when_similarity_at_least
        if (
            treatment == "rgb"
            and threshold is not None
            and facts.source_target_similarity is not None
            and facts.source_target_similarity >= threshold
        ):
            treatment = "inverted"
            reasons.append("source-target similarity meets first-arm threshold")
        try:
            crop = shared_crop or crop_for(
                *_crop_regions(facts, policy), facts.native, policy, support
            )
        except ValueError:
            gaps.append(
                Gap(
                    "no_crop_fits",
                    "no supported canvas fits the required region and avoided tracks",
                )
            )
            reasons.append("no_crop_fits: falling back to active framing")
            crop, framing = None, "active"
        arm = Arm(
            treatment,
            "joint" if joint else "single",
            framing,
            crop,
            count,
            0,
            audio_lock=(
                policy.audio_lock == "speaking"
                and ("joint" if joint else "single") in policy.audio_lock_groupings
                and (
                    facts.speaking
                    or (
                        facts.lips_unmeasured
                        and ("joint" if joint else "single") in policy.audio_lock_unmeasured
                    )
                )
                and framing != "head"
                and support.audio_lock
            ),
            faceswap=faceswap,
        )
        if arm.audio_lock and policy.first_arm_evidence is not None:
            reasons.append(
                f"Audio lock: {arm.grouping}; "
                + ("speaking." if facts.speaking else "lips unmeasured.")
            )
    if (
        arm.treatment not in support.treatments
        or arm.grouping not in support.groupings
        or arm.framing not in support.framings
    ):
        gaps.append(Gap("replacement_unsupported", "integration cannot lower the planned arm"))
        return stop("replacement arm unsupported")
    return ReplacementPlan(arm, tuple(gaps), tuple(reasons), False, techniques_for(arm))


# Explicit record boundary for the values A2 stores in operation requests/results.
# These functions convert data only; no JSON parser, database or filesystem is involved.
_RECORD_TYPES = (
    Box,
    Letterbox,
    Track,
    Performer,
    Bystander,
    Occluder,
    ReplacementFacts,
    CropPlan,
    Arm,
    CheckResult,
    Attempt,
    Gap,
    ReplacementPlan,
)


def to_record(value) -> dict:
    """JSON-safe records retain rational rates exactly and intervals as frame indices."""
    if type(value) not in _RECORD_TYPES:
        raise TypeError("unsupported replacement record type")

    def encode(item):
        if type(item) in _RECORD_TYPES:
            return {field.name: encode(getattr(item, field.name)) for field in fields(item)}
        if isinstance(item, tuple):
            return [encode(v) for v in item]
        if isinstance(item, Fraction):
            return float(item)
        return item

    record = encode(value)
    if isinstance(value, ReplacementFacts):
        record["fps"] = value.fps.numerator if value.fps.denominator == 1 else str(value.fps)
    return record


def from_record(kind, record):
    """Reconstruct typed tuples and re-run contract validation; unknown/missing keys fail."""
    if kind not in _RECORD_TYPES:
        raise TypeError("unsupported replacement record type")
    # A1/A2 persisted checks predate explanatory reasons.
    if kind is CheckResult and isinstance(record, dict):
        record = {"reason": "", **record}
    if kind is ReplacementFacts and isinstance(record, dict):
        record = {
            "speaking": False,
            "context_frames": 0,
            "close_up": False,
            "handoff": "none",
            "lips_unmeasured": False,
            **record,
        }
    if kind is Arm and isinstance(record, dict):
        record = {"audio_lock": False, "faceswap": False, **record}
    if not isinstance(record, dict) or set(record) != {f.name for f in fields(kind)}:
        raise ValueError("invalid replacement record keys")
    data = dict(record)

    def sequence(value):
        if not isinstance(value, (list, tuple)):
            raise TypeError("record sequence must be an array")
        return tuple(value)

    def nested(name, cls):
        data[name] = from_record(cls, data[name])

    def rows(name, cls):
        data[name] = tuple(from_record(cls, v) for v in sequence(data[name]))

    if kind is Letterbox:
        for name in ("fit", "offset", "canvas"):
            data[name] = sequence(data[name])
    elif kind is Track:
        nested("box", Box)
        data["presence"] = sequence(data["presence"])
    elif kind is Bystander:
        data["items"] = sequence(data["items"])
    elif kind is CropPlan:
        nested("box", Box)
        for name in ("canvas", "interior_edges"):
            data[name] = sequence(data[name])
        data["matrix"] = tuple(sequence(row) for row in sequence(data["matrix"]))
    elif kind is Arm:
        if data["crop"] is not None:
            nested("crop", CropPlan)
    elif kind is Attempt:
        nested("arm", Arm)
        data["takes"] = tuple(
            tuple(from_record(CheckResult, c) for c in sequence(take))
            for take in sequence(data["takes"])
        )
    elif kind is ReplacementPlan:
        if data["arm"] is not None:
            nested("arm", Arm)
        rows("gaps", Gap)
        for name in ("reasons", "technique_ids"):
            data[name] = sequence(data[name])
    elif kind is ReplacementFacts:
        if data["letterbox"] is not None:
            nested("letterbox", Letterbox)
        for name, cls in (
            ("performers", Performer),
            ("bystanders", Bystander),
            ("tracks", Track),
            ("occluders", Occluder),
            ("required_context", Box),
            ("avoided_regions", Box),
        ):
            rows(name, cls)
        for name in ("native", "scene_items"):
            data[name] = sequence(data[name])
        data["fps"] = Fraction(data["fps"])
        data["silent_intervals"] = tuple(
            sequence(row) for row in sequence(data["silent_intervals"])
        )
    return kind(**data)
