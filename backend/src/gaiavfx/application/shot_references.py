"""Shot references: what a shot may use, and whether it still holds (ADR 0019 §1).

A shot is a segment's opening or the shot after one of its cuts. Its references are its place's approved views, the
cut's pin and the place's latest views sheet, each with its freshness and the user's suitability verdict. Direction
stores the evidence, keyframes and judgments and judges each one's freshness; this module adds what other modules
know (the candidates Review set aside, the place's latest sheet), so openings and cuts are planned and lowered one
way.
"""

from dataclasses import dataclass

from gaiavfx.application.direction_reading import DIRECTION as direction
from gaiavfx.application.prepare_references import (
    current_sheet,
    inactive_reference_assets,
    inactive_view_assets,
    latest_sheet_asset,
)
from gaiavfx.integrations import minimax_h3
from gaiavfx.integrations.minimax_h3.prompt import gaps
from gaiavfx.modules.direction.application import default_policy
from gaiavfx.modules.direction.planning import (
    CutPlan,
    OpeningPlan,
    plan_cut,
    plan_opening,
)
from gaiavfx.modules.direction.segment import Keyframe, Picture, PictureRole
from gaiavfx.modules.direction.techniques import Block
from gaiavfx.shared.errors import ApplicationError


@dataclass(frozen=True)
class Shot:
    """One shot of a segment's window: the opening (its id is the segment's) or the shot after a cut."""

    id: str
    label: str
    angle_id: str
    number: int  # 1-based, as the compiler counts shots
    plan: CutPlan | OpeningPlan
    cut: dict | None = None

    @property
    def cut_ids(self) -> tuple[str, ...]:
        return (self.id,) if self.cut else ()


def shots(session, row) -> list[Shot]:
    """The segment's shots in window order, each with its own plan: the opening while it names an opening angle,
    then the shot after each cut."""
    opening = plan_for_opening(session, row["id"])
    found = [Shot(row["id"], "Opening", row["opening_angle_id"], 1, opening)] if opening else []
    for n, cut in enumerate(row["cuts"], 1):
        found.append(
            Shot(
                cut["id"],
                f"Cut {n} at f{cut['frame']}",
                cut["to_angle_id"],
                n + 1,
                plan_for_cut(session, cut["id"]),
                cut,
            )
        )
    return found


def plan_for_cut(session, cut_id) -> CutPlan:
    cut = direction.read_cut(session, cut_id)
    place_id = direction.read_angle(session, cut["to_angle_id"])["place_id"]
    views, pins = inactive_reference_assets(session, place_id=place_id, cut_id=cut_id)
    found = direction.shot_references(
        session,
        cut_id=cut_id,
        inactive_view_assets=views,
        inactive_pin_assets=pins,
        sheet_asset=latest_sheet_asset(session, place_id),
    )
    return _planned(
        plan_cut,
        found.context,
        found.available,
        fidelity=cut["fidelity"],
        override=cut["override_technique"],
        bindings=found.bindings,
    )


def plan_for_opening(session, segment_id) -> OpeningPlan | None:
    """The plan of the segment's first shot, or None while it names no opening angle."""
    segment = direction.read_segment(session, segment_id)
    if segment["opening_angle_id"] is None:
        return None
    place_id = direction.read_angle(session, segment["opening_angle_id"])["place_id"]
    found = direction.shot_references(
        session,
        segment_id=segment_id,
        inactive_view_assets=inactive_view_assets(session, place_id),
        sheet_asset=latest_sheet_asset(session, place_id),
    )
    return _planned(
        plan_opening,
        found.context,
        found.available,
        override=segment["opening_technique"],
        bindings=found.bindings,
    )


def _planned(planner, context, available, **choices):
    try:
        return planner(
            context,
            default_policy(),
            minimax_h3.capabilities(),
            minimax_h3.knowledge_pack(),
            available,
            **choices,
        )
    except (KeyError, ValueError) as exc:
        raise ApplicationError(f"Invalid direction technique: {exc}", 422) from exc


def lower(session, shot, sheet_asset, *, strict):
    """The pictures and pinned keyframes the window binds for the shot's recommended technique. A render lowers
    strictly and refuses a missing or unusable reference; inspection skips it."""
    choice = shot.plan.recommended
    place_id = direction.read_angle(session, shot.angle_id)["place_id"]
    pictures, keyframes = [], []

    def missing(problem):
        if strict:
            raise ApplicationError(problem, 422)

    for block in choice.blocks if choice else ():
        if block is Block.PLACE_VIEWS:
            if not sheet_asset:
                missing("A views-only sheet is required: assemble it with prepare_cut (no jobs)")
                continue
            recipe = direction.sheet_recipe(session, sheet_asset, place_id)
            # The plan judged the place's latest sheet; a render may bind an older one of its own.
            binding = next(
                (b for b in shot.plan.bindings if b.kind == "sheet" and b.id == sheet_asset), None
            ) or direction.reference_binding(
                session,
                "sheet",
                sheet_asset,
                direction.suitability(session, shot.id) if shot.cut else (),
                place_id=place_id,
            )
            if problem := binding.problem:
                missing(problem[0].upper() + problem[1:])
                continue
            pictures.append(
                Picture(
                    PictureRole.PLACE_VIEWS,
                    sheet_asset,
                    first_shot=shot.number,
                    panels=recipe["panels"],
                    blank_panels=recipe["blank_panels"],
                    origin=recipe["origin"],
                )
            )
        elif block is Block.PINNED_FRAME:
            approved = [b for b in shot.plan.bindings if b.kind == "pin"]
            if len(approved) != 1:
                missing("The cut needs one approved keyframe.")
                continue
            if problem := approved[0].problem:
                missing(problem[0].upper() + problem[1:])
                continue
            keyframes.append(
                Keyframe(approved[0].asset_id, shot.number - 1, choice.keyframe_offset_frames)
            )
        elif block is not Block.DIRECTION_TEXT:
            raise ApplicationError(f"Cannot lower block {block}.", 422)
    return tuple(pictures), tuple(keyframes)


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
    shot = Shot(cut["id"], "", cut["to_angle_id"], number, plan, cut)
    references, keyframes = lower(session, shot, sheet, strict=False)
    return [
        {"code": gap.code, "message": gap.message}
        for gap in gaps(segment, tuple(pictures) + references, keyframes)
    ]
