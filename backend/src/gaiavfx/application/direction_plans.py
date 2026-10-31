"""Compose stored Direction facts with H3, the model integration segments render with.

A segment renders as one window of shots (ADR 0019): its opening, then the shot after each cut. Every shot has its
own plan; the window lowers them together, with at most one place-views sheet.
"""

from dataclasses import asdict, dataclass, is_dataclass, replace
from enum import Enum

from gaiavfx.application import continuation as join
from gaiavfx.application import shot_references as references
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.prepare_references import current_sheet
from gaiavfx.application.scene_place import PlaceStatus, place_status
from gaiavfx.application.set_seed import segment_seed
from gaiavfx.integrations.minimax_h3.continuation import Handoff
from gaiavfx.integrations.minimax_h3.prompt import gaps
from gaiavfx.integrations.minimax_h3.window import Window, compile_window
from gaiavfx.modules.direction.planning import (
    CutPlan,
    OpeningPlan,
    binding_problem,
)
from gaiavfx.modules.direction.techniques import Block
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError


def plan_to_dict(plan: "CutPlan | OpeningPlan | SegmentPlan") -> dict:
    """Return a JSON-ready, string-enum representation shared by CLI and API."""

    def convert(value):
        if isinstance(value, Enum):
            return value.value
        if is_dataclass(value):
            return {key: convert(item) for key, item in asdict(value).items()}
        if isinstance(value, dict):
            return {key: convert(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [convert(item) for item in value]
        return value

    return convert(plan)


@dataclass(frozen=True)
class SegmentRefusal:
    code: str
    message: str
    cut_ids: tuple[str, ...] = ()
    status: int = 422
    scope: str | None = None  # a capability gap's subject, such as `sheet_cast`


@dataclass(frozen=True)
class SegmentPlan:
    segment_id: str
    cuts: tuple[CutPlan, ...]
    technique_id: str
    title: str
    ready: bool
    refusals: tuple[SegmentRefusal, ...]
    sheet_asset: str | None
    opening: OpeningPlan | None = None
    place: PlaceStatus | None = None  # what the first shot takes from its place (ADR 0019 §5)
    seed: dict | None = (
        None  # a set being built from the segment's current take (ADR 0019 §4), in words
    )
    build_refusal: str | None = None  # why no set can be built from that take now
    continuation: Handoff | None = None  # a continuation's handoff and the rule's hint


@dataclass(frozen=True)
class SegmentRenderPlan:
    """The plan a render reads, with the window it lowers. The window holds prompt text, so it stays beside the
    plan the UI and the agent read, never inside it."""

    plan: SegmentPlan
    segment: object | None  # the assembled Segment, when its structure assembles
    pictures: tuple
    window: Window | None  # compiled once, only when nothing refuses the render
    artifact: dict | None  # the motion context a continuation reads
    source: dict | None  # where that context came from


def _shapes_window(shot: references.Shot) -> bool:
    """A cut always shapes the window's technique; an opening only when it adds more than direction text."""
    choice = shot.plan.recommended
    return shot.cut is not None or bool(choice and choice.blocks != (Block.DIRECTION_TEXT,))


def plan_for_segment(session, segment_id, **inputs):
    """The segment's plan as the UI and the agent read it: the render's plan, and the set being built from the
    segment's take with why none can be built now (ADR 0019 §4), which rendering never reads."""
    plan = segment_render_inputs(session, segment_id, **inputs).plan
    seed, build_refusal = segment_seed(session, direction.read_segment(session, segment_id))
    return replace(plan, seed=seed, build_refusal=build_refusal)


def segment_render_inputs(session, segment_id, *, sheet_asset=None, context_artifact=None):
    """Inspect and lower the whole window before reserving any jobs.

    Each shot retains its own policy and binding judgments. A repeated sheet is validated for every shot, then
    bound once at the first shot that needs it: the opening's comes first.
    """
    row = direction.read_segment(session, segment_id)
    shots = references.shots(session, row)
    opening = next((shot.plan for shot in shots if not shot.cut), None)
    plans = tuple(shot.plan for shot in shots if shot.cut)
    refusals = []
    sheet_shots = []
    for shot in shots:
        choice = shot.plan.recommended
        if choice is None:
            refusals.append(
                SegmentRefusal(
                    "technique_unavailable",
                    f"{shot.label}: No technique is available: "
                    + "; ".join(gap.message for gap in shot.plan.gaps),
                    shot.cut_ids,
                    409,
                )
            )
        elif not choice.ready:
            refusals.append(
                SegmentRefusal(
                    "cut_not_ready" if shot.cut else "opening_not_ready",
                    f"{shot.label}: " + "; ".join(action.reason for action in choice.actions),
                    shot.cut_ids,
                    422 if any(binding_problem(b) for b in shot.plan.bindings) else 409,
                )
            )
        if choice and Block.PLACE_VIEWS in choice.blocks:
            place = direction.read_angle(session, shot.angle_id)["place_id"]
            sheet_shots.append((shot, place, sheet_asset or current_sheet(session, place)))
    if (
        len({place for _, place, _ in sheet_shots}) > 1
        or len({asset for _, _, asset in sheet_shots if asset}) > 1
    ):
        refusals.insert(
            0,
            SegmentRefusal(
                "several_sheets",
                f"{' and '.join(shot.label for shot, _, _ in sheet_shots)} need different "
                "place-views sheets. One window supports one sheet; split the segment.",
                tuple(cut for shot, _, _ in sheet_shots for cut in shot.cut_ids),
            ),
        )
    if sheet_asset and not sheet_shots:
        refusals.append(
            SegmentRefusal(
                "unused_sheet", "The segment's techniques do not use a views-only sheet."
            )
        )
    segment, pictures, keyframes, artifact, source, window = None, (), (), None, None, None
    try:
        segment, pictures = direction.assemble_segment(session, segment_id)
    except ApplicationError as exc:
        if exc.status != 422:
            raise
        refusals.append(SegmentRefusal("structure", str(exc)))
    if segment:
        for picture in pictures:
            try:
                MediaLibrary.asset_info(session, picture.asset_id)
            except ApplicationError as exc:
                if exc.status != 404:
                    raise
                refusals.append(
                    SegmentRefusal(
                        "identity_asset",
                        f"Unknown identity asset {picture.asset_id} for subject {picture.subject_id}.",
                    )
                )
        sheets = {shot.id: asset for shot, _, asset in sheet_shots}
        # A shot that is not ready has said what it lacks; only ready shots are lowered.
        for shot in (s for s in shots if s.plan.recommended and s.plan.recommended.ready):
            try:
                refs, pins = references.lower(session, shot, sheets.get(shot.id), strict=True)
                pictures += tuple(
                    p
                    for p in refs
                    if not any(
                        old.asset_id == p.asset_id and old.role == p.role for old in pictures
                    )
                )
                keyframes += pins
            except ApplicationError as exc:
                refusals.append(SegmentRefusal("cut_binding", f"{shot.label}: {exc}", shot.cut_ids))
        refusals.extend(
            SegmentRefusal(g.code, g.message, scope=g.scope)
            for g in gaps(segment, pictures, keyframes)
            if not any(r.code == g.code for r in refusals)
        )
        missing_source = None
        try:
            artifact, source = join.source(session, row, context_artifact)
        except ApplicationError as exc:
            missing_source = SegmentRefusal("continuation_source", str(exc))
        handoff = join.resolve(session, row, pins=_planned_pins(shots), artifact=artifact)
        handoff_refusals = [SegmentRefusal(*r) for r in (handoff.refusals if handoff else ())]
        if not refusals and not handoff_refusals:
            try:
                window = compile_window(segment, pictures, keyframes, join.grid(), handoff)
            except ValueError as exc:
                refusals.append(SegmentRefusal("compiler", str(exc)))
        refusals.extend(handoff_refusals)
        if missing_source:
            refusals.append(missing_source)
    else:
        # The continuation row still says how this join would hand over, while the structure is refused.
        handoff = join.resolve(session, row, pins=_planned_pins(shots))
    shaping = [shot.plan for shot in shots if _shapes_window(shot)]
    techniques = {p.recommended.technique_id for p in shaping if p.recommended}
    technique = (
        "single_take"
        if not shaping
        else next(iter(techniques))
        if len(techniques) == 1
        else "segment"
    )
    title = (
        "Single continuous take"
        if not shaping
        else " / ".join(dict.fromkeys(p.recommended.title for p in shaping if p.recommended))
        or "No technique available"
    )
    plan = SegmentPlan(
        segment_id,
        plans,
        technique,
        title,
        not refusals,
        tuple(refusals),
        sheet_shots[0][2] if sheet_shots else None,
        opening,
        place_status(session, opening, refusals),
        continuation=handoff,
    )
    return SegmentRenderPlan(
        plan, segment, pictures, window if plan.ready else None, artifact, source
    )


def _planned_pins(shots):
    """The window's planned pinned keyframes; lowering binds exactly one approved pin to each."""
    return sum(
        1 for s in shots if s.plan.recommended and Block.PINNED_FRAME in s.plan.recommended.blocks
    )


def planned_pins(session, row):
    """`_planned_pins` for a segment whose window is not being lowered: its shots are planned here."""
    return _planned_pins(references.shots(session, row))


direction = DIRECTION
