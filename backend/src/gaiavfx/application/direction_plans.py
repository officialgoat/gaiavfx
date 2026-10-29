"""Compose stored Direction facts with an explicitly selected model integration.

A segment renders as one window of shots (ADR 0019): its opening, then the shot after each cut. Every shot has its
own plan; the window lowers them together, with at most one place-views sheet.
"""

from dataclasses import asdict, dataclass, is_dataclass, replace
from enum import Enum

from sqlalchemy.orm import Session

from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.prepare_references import (
    current_sheet,
    inactive_reference_assets,
    inactive_view_assets,
    latest_sheet,
    sheet_staleness,
)
from gaiavfx.application.scene_place import PlaceStatus, place_status
from gaiavfx.application.selected_takes import current_take
from gaiavfx.application.set_seed import segment_seed
from gaiavfx.application.sheet_recipes import sheet_reference
from gaiavfx.integrations import minimax_h3
from gaiavfx.integrations.minimax_h3 import continuation as continuity
from gaiavfx.integrations.minimax_h3.continuation import ContinuationFacts, Handoff
from gaiavfx.integrations.minimax_h3.prompt import compile_prompt, gaps
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
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

DEFAULT_INTEGRATION = "minimax_h3"
INTEGRATIONS = {DEFAULT_INTEGRATION: minimax_h3}


def _provider(integration):
    provider = INTEGRATIONS.get(integration)
    if provider is None:
        raise ApplicationError(f"Unknown direction integration: {integration}", 422)
    return provider


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
    plan = segment_render_inputs(session, segment_id, integration, **inputs)[0]
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
    segment, pictures, keyframes, artifact, source = None, (), (), None, None
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
            SegmentRefusal(g.code, g.message)
            for g in gaps(segment, pictures, keyframes)
            if g.code != "several_sheets" or not any(r.code == g.code for r in refusals)
        )
        missing_source = None
        try:
            artifact, source = continuation_input(session, row, context_artifact)
        except ApplicationError as exc:
            missing_source = SegmentRefusal("continuation_source", str(exc))
        handoff = _handoff(row, shots, keyframes, artifact, integration)
        handoff_refusals = [SegmentRefusal(*r) for r in (handoff.refusals if handoff else ())]
        if not refusals and not handoff_refusals:
            grid = INTEGRATIONS[integration].capabilities().frame_grid
            try:
                compile_prompt(
                    segment,
                    pictures,
                    keyframes,
                    grid,
                    handoff.context_frames if handoff else 0,
                )
            except ValueError as exc:
                refusals.append(SegmentRefusal("compiler", str(exc)))
        refusals.extend(handoff_refusals)
        if missing_source:
            refusals.append(missing_source)
    else:
        # The continuation row still says how this join would hand over, while the structure is refused.
        handoff = _handoff(row, shots, (), None, integration)
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
    return plan, segment, pictures, keyframes, artifact, source


def _continuation_facts(row, shots, keyframes, artifact):
    planned = sum(
        1 for s in shots if s.plan.recommended and Block.PINNED_FRAME in s.plan.recommended.blocks
    )
    return ContinuationFacts(
        continues=row["continues"],
        cuts=len(row["cuts"]),
        guides=max(planned, len(keyframes)),
        source_frames=continuity.source_frames(artifact.get("info")) if artifact else None,
    )


def _handoff(row, shots, keyframes, artifact, integration):
    if not row["continues"]:
        return None
    return continuity.resolve(
        _continuation_facts(row, shots, keyframes, artifact),
        method=row["continuation_method"],
        context_frames=row["context_frames"],
        grid=INTEGRATIONS[integration].capabilities().frame_grid,
    )


def continuation_facts(session, segment_id, integration=DEFAULT_INTEGRATION):
    """What the continuation rule reads about a segment, without lowering its window."""
    _provider(integration)
    row = direction.read_segment(session, segment_id)
    opening = plan_for_opening(session, segment_id, integration)
    plans = tuple(plan_for_cut(session, cut["id"], integration) for cut in row["cuts"])
    try:
        artifact, _ = continuation_input(session, row)
    except ApplicationError:
        artifact = None  # no selected take yet: the render plan checks the source
    return row, _continuation_facts(row, _shots(row, opening, plans), (), artifact)


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


def cut_references(session, cut, segment, choice, pictures, sheet_asset, *, strict, bindings):
    """`shot_references` for the shot after one cut."""
    number = segment.cut_frames.index(cut["frame"]) + 2
    shot = _Shot(cut["id"], "", cut["to_angle_id"], number, None, cut)
    return shot_references(
        session, shot, segment, choice, pictures, sheet_asset, strict=strict, bindings=bindings
    )


def continuation_input(session, segment_row, context_artifact=None):
    artifact = None
    movie_id = segment_row["movie_id"]
    context_source = None
    if segment_row["continues"]:
        if context_artifact:
            context_source = {"kind": "artifact"}
        else:
            ordinal = segment_row["ordinal"]
            if ordinal == 0:
                raise ApplicationError(
                    "The first segment has no predecessor; give --context-artifact.", 422
                )
            predecessor = direction.read_movie(session, movie_id)["segments"][ordinal - 1]
            selection = current_take(session, movie_id, predecessor["id"])
            if selection is None:
                raise ApplicationError(f"Select a take of segment {ordinal} first.", 422)
            # The take Program marks "Changed since this take" no longer matches its segment
            # (a split, an edit, a replaced pin): its motion context would continue the old one.
            if selection["stale"]:
                raise ApplicationError(f"Select a current take of segment {ordinal} first.", 422)
            candidate = OperationService().candidate(session, selection["candidate_id"])
            context_artifact = candidate["artifact_id"]
            if context_artifact is None:
                raise ApplicationError(
                    f"The selected take of segment {ordinal} captured no continuation context.", 422
                )
            context_source = {
                "kind": "selection",
                "segment_id": predecessor["id"],
                "selection_id": selection["id"],
                "candidate_id": candidate["id"],
                "authority": selection["authority"],
                "basis": selection["basis"],
            }
        artifact = MediaLibrary.artifact(session, context_artifact)
        if artifact["kind"] != "h3_context":
            raise ApplicationError("The continuation artifact must be H3 context.", 422)
    return artifact, context_source
