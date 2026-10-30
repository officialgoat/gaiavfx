"""Compose stored Direction facts with an explicitly selected model integration.

A segment renders as one window of shots (ADR 0019): its opening, then the shot after each cut. Every shot has its
own plan; the window lowers them together, with at most one place-views sheet.
"""

from dataclasses import asdict, dataclass, is_dataclass, replace
from enum import Enum

from sqlalchemy.orm import Session

from gaiavfx.application import continuation as join
from gaiavfx.application.continuation import DEFAULT_INTEGRATION, INTEGRATIONS
from gaiavfx.application.continuation import provider as _provider
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.prepare_references import (
    current_sheet,
    inactive_reference_assets,
    inactive_view_assets,
    latest_sheet,
    sheet_staleness,
)
from gaiavfx.application.scene_place import PlaceStatus, place_status
from gaiavfx.application.set_seed import segment_seed
from gaiavfx.application.sheet_recipes import sheet_reference
from gaiavfx.integrations.minimax_h3.continuation import Handoff
from gaiavfx.integrations.minimax_h3.prompt import gaps
from gaiavfx.integrations.minimax_h3.window import Window, compile_window
from gaiavfx.modules.direction.application import default_policy
from gaiavfx.modules.direction.planning import (
    Binding,
    CutPlan,
    OpeningPlan,
    binding_problem,
    plan_cut,
    plan_opening,
)
from gaiavfx.modules.direction.segment import Keyframe, Picture, PictureRole
from gaiavfx.modules.direction.techniques import Block
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError


def sheet_binding(session, place_id, asset_id, recipe, judgments=()):
    """The same sheet judgment and freshness rules for the plan and explicit render inputs. `judgments` are the
    shot's suitability judgments (a cut's; an opening has none yet)."""
    reasons = sheet_staleness(session, place_id, recipe["tiles"])
    judgment = next(
        (j for j in judgments if j["reference_kind"] == "sheet" and j["reference_id"] == asset_id),
        None,
    )
    return Binding(
        "sheet",
        asset_id,
        asset_id,
        None,
        bool(reasons),
        reasons,
        judgment["verdict"] if judgment and not judgment["stale"] else "unjudged",
        judgment["note"] if judgment else "",
    )


def _with_sheet(session, place_id, available, bindings, judgments=()):
    """Add the place's latest sheet to a shot's bindings; a stale or unsuitable one supplies no place views."""
    sheet = latest_sheet(session, place_id)
    if not sheet:
        return available, bindings
    binding = sheet_binding(
        session, place_id, sheet["candidates"][0]["asset_id"], sheet["request"], judgments
    )
    if binding.stale or binding.suitability == "unsuitable":
        available = replace(available, place_views=0)
    return available, bindings + (binding,)


def plan_for_cut(session: Session, cut_id: str, integration: str = DEFAULT_INTEGRATION) -> CutPlan:
    provider = _provider(integration)
    service = DIRECTION
    cut = service.read_cut(session, cut_id)
    place_id = service.read_angle(session, cut["to_angle_id"])["place_id"]
    inactive_views, inactive_pins = inactive_reference_assets(
        session, place_id=place_id, cut_id=cut_id
    )
    context, available, bindings = service.context_and_available(
        session,
        cut_id,
        inactive_view_assets=inactive_views,
        inactive_pin_assets=inactive_pins,
    )
    available, bindings = _with_sheet(
        session, place_id, available, bindings, service.suitability(session, cut_id)
    )
    try:
        return plan_cut(
            context,
            default_policy(),
            provider.capabilities(),
            provider.knowledge_pack(),
            available,
            fidelity=cut["fidelity"],
            override=cut["override_technique"],
            bindings=bindings,
        )
    except (KeyError, ValueError) as exc:
        raise ApplicationError(f"Invalid direction technique: {exc}", 422) from exc


def plan_for_opening(
    session: Session, segment_id: str, integration: str = DEFAULT_INTEGRATION
) -> OpeningPlan | None:
    """The plan of the segment's first shot, or None while it names no opening angle (ADR 0019 §1)."""
    provider = _provider(integration)
    service = DIRECTION
    segment = service.read_segment(session, segment_id)
    if segment["opening_angle_id"] is None:
        return None
    place_id = service.read_angle(session, segment["opening_angle_id"])["place_id"]
    context, available, bindings = service.opening_context_and_available(
        session, segment_id, inactive_view_assets=inactive_view_assets(session, place_id)
    )
    available, bindings = _with_sheet(session, place_id, available, bindings)
    try:
        return plan_opening(
            context,
            default_policy(),
            provider.capabilities(),
            provider.knowledge_pack(),
            available,
            override=segment["opening_technique"],
            bindings=bindings,
        )
    except (KeyError, ValueError) as exc:
        raise ApplicationError(f"Invalid direction technique: {exc}", 422) from exc


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


@dataclass(frozen=True)
class _Shot:
    """One shot of the window: the opening (its id is the segment's) or the shot after a cut."""

    id: str
    label: str
    angle_id: str
    number: int  # 1-based, as the compiler counts shots
    plan: CutPlan | OpeningPlan
    cut: dict | None = None

    @property
    def cut_ids(self) -> tuple[str, ...]:
        return (self.id,) if self.cut else ()


def _shots(row, opening, plans):
    shots = [_Shot(row["id"], "Opening", row["opening_angle_id"], 1, opening)] if opening else []
    for n, (cut, plan) in enumerate(zip(row["cuts"], plans, strict=True), 1):
        shots.append(
            _Shot(cut["id"], f"Cut {n} at f{cut['frame']}", cut["to_angle_id"], n + 1, plan, cut)
        )
    return shots


def _shapes_window(shot: _Shot) -> bool:
    """A cut always shapes the window's technique; an opening only when it adds more than direction text."""
    choice = shot.plan.recommended
    return shot.cut is not None or bool(choice and choice.blocks != (Block.DIRECTION_TEXT,))


def plan_for_segment(session, segment_id, integration=DEFAULT_INTEGRATION, **inputs):
    """The segment's plan as the UI and the agent read it: the render's plan, and the set being built from the
    segment's take with why none can be built now (ADR 0019 §4), which rendering never reads."""
    plan = segment_render_inputs(session, segment_id, integration, **inputs).plan
    seed, build_refusal = segment_seed(session, direction.read_segment(session, segment_id))
    return replace(plan, seed=seed, build_refusal=build_refusal)


def segment_render_inputs(
    session, segment_id, integration=DEFAULT_INTEGRATION, *, sheet_asset=None, context_artifact=None
):
    """Inspect and lower the whole window before reserving any jobs.

    Each shot retains its own policy and binding judgments. A repeated sheet is validated for every shot, then
    bound once at the first shot that needs it: the opening's comes first.
    """
    _provider(integration)
    row = direction.read_segment(session, segment_id)
    opening = plan_for_opening(session, segment_id, integration)
    plans = tuple(plan_for_cut(session, cut["id"], integration) for cut in row["cuts"])
    shots = _shots(row, opening, plans)
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
                refs, pins = shot_references(
                    session,
                    shot,
                    segment,
                    shot.plan.recommended,
                    (),
                    sheets.get(shot.id),
                    strict=True,
                    bindings=shot.plan.bindings,
                )
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
        handoff = join.resolve(
            session, row, pins=_planned_pins(shots), artifact=artifact, integration=integration
        )
        handoff_refusals = [SegmentRefusal(*r) for r in (handoff.refusals if handoff else ())]
        if not refusals and not handoff_refusals:
            grid = INTEGRATIONS[integration].capabilities().frame_grid
            try:
                window = compile_window(segment, pictures, keyframes, grid, handoff)
            except ValueError as exc:
                refusals.append(SegmentRefusal("compiler", str(exc)))
        refusals.extend(handoff_refusals)
        if missing_source:
            refusals.append(missing_source)
    else:
        # The continuation row still says how this join would hand over, while the structure is refused.
        handoff = join.resolve(session, row, pins=_planned_pins(shots), integration=integration)
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


def planned_pins(session, row, integration=DEFAULT_INTEGRATION):
    """`_planned_pins` for a segment whose window is not being lowered: its shots are planned here."""
    opening = plan_for_opening(session, row["id"], integration)
    plans = tuple(plan_for_cut(session, cut["id"], integration) for cut in row["cuts"])
    return _planned_pins(_shots(row, opening, plans))


direction = DIRECTION


def shot_references(session, shot, segment, choice, pictures, sheet_asset, *, strict, bindings):
    """Share compiler inputs between inspection and rendering; inspection tolerates missing refs."""
    place_id = direction.read_angle(session, shot.angle_id)["place_id"]
    judgments = direction.suitability(session, shot.id) if shot.cut else ()
    pictures, keyframes = tuple(pictures), []
    for block in choice.blocks if choice else ():
        if block is Block.PLACE_VIEWS:
            if not sheet_asset:
                if strict:
                    raise ApplicationError(
                        "A views-only sheet is required: assemble it with prepare_cut (no jobs)",
                        422,
                    )
                continue
            recipe = sheet_reference(session, sheet_asset, place_id)
            binding = sheet_binding(session, place_id, sheet_asset, recipe, judgments)
            if problem := binding_problem(binding):
                if strict:
                    raise ApplicationError(problem[0].upper() + problem[1:], 422)
                continue
            pictures += (
                Picture(
                    PictureRole.PLACE_VIEWS,
                    sheet_asset,
                    first_shot=shot.number,
                    panels=recipe["panels"],
                    blank_panels=recipe["blank_panels"],
                    origin=recipe["origin"],
                ),
            )
        elif block is Block.PINNED_FRAME:
            approved = [b for b in bindings if b.kind == "pin"]
            if len(approved) != 1:
                if strict:
                    raise ApplicationError("The cut needs one approved keyframe.", 422)
                continue
            if problem := binding_problem(approved[0]):
                if strict:
                    raise ApplicationError(problem[0].upper() + problem[1:], 422)
                continue
            keyframes.append(
                Keyframe(approved[0].asset_id, shot.number - 1, choice.keyframe_offset_frames)
            )
        elif block is not Block.DIRECTION_TEXT:
            raise ApplicationError(f"Cannot lower block {block}.", 422)
    return pictures, tuple(keyframes)


def cut_gaps(session, cut_id):
    """A cut's typed capability gaps before it is ready, without compiling or exposing prompt text: inspection
    lowers the shot after the cut leniently, with the place's current sheet, so a sheet's cast shows early."""
    cut = direction.read_cut(session, cut_id)
    try:
        segment, pictures = direction.assemble_segment(session, cut["segment_id"])
    except ApplicationError as exc:
        if exc.status != 422:
            raise
        return [{"code": "structure", "message": str(exc)}]
    plan = plan_for_cut(session, cut_id)
    choice = plan.recommended
    place_id = direction.read_angle(session, cut["to_angle_id"])["place_id"]
    sheet = (
        current_sheet(session, place_id) if choice and Block.PLACE_VIEWS in choice.blocks else None
    )
    number = segment.cut_frames.index(cut["frame"]) + 2
    pictures, keyframes = shot_references(
        session,
        _Shot(cut["id"], "", cut["to_angle_id"], number, plan, cut),
        segment,
        choice,
        pictures,
        sheet,
        strict=False,
        bindings=plan.bindings if choice and Block.PINNED_FRAME in choice.blocks else (),
    )
    return [
        {"code": gap.code, "message": gap.message} for gap in gaps(segment, pictures, keyframes)
    ]
