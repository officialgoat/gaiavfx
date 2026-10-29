"""What a segment's first shot takes from its place (ADR 0019 §5): the place, the set it is built as, and the one
thing still in the way, for the timeline's References row and Properties. Computed here so the renderer decides
nothing.

Readiness is the opening plan's (its recommended technique and next actions); this only phrases its first missing
step for the place's producer, and adds what the plan cannot know: that a newer accepted version of the set exists
than the one the shot's view was rendered at. That is an update to offer, never a blocker.
"""

from dataclasses import dataclass

from gaiavfx.application import set_views
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.prepare_references import view_producer
from gaiavfx.application.sets import STORE
from gaiavfx.modules.direction.planning import Action, OpeningPlan
from gaiavfx.modules.direction.techniques import Block

SHEET_GAPS = frozenset({"sheet_several_subjects", "sheet_unbound_subject"})


@dataclass(frozen=True)
class PlaceStatus:
    place_id: str
    place_name: str
    set_id: str | None
    set_name: str | None
    set_version: int | None  # the set's current accepted version, if it has one
    uses_views: bool  # the first shot was asked to use the place's views
    uses: str  # that, in words: "Text only", "Pictures of the set" or "Pictures of the place"
    rendered_version: int | None  # the version its approved view was rendered at
    update_available: bool
    blocker: str | None  # the one next step, in words; None when nothing is in the way


def _blocker(session, opening, found, refusals, view, producer):
    choice = opening.recommended
    if found["set_id"]:
        if found["set_version"] is None:
            return "Accept a version of the stage first"
        if STORE.view_for_angle(session, found["set_id"], opening.angle_id) is None:
            return "No camera for the first shot in the stage"
    actions = {a.action for a in choice.actions}
    if Action.REVIEW_VIEW in actions:
        return "Views to review"
    if actions & {Action.VIEW_ANGLE, Action.VIEW_PLACE} and view is None:
        return producer.missing
    if actions:
        return choice.actions[0].reason[:1].upper() + choice.actions[0].reason[1:]
    if any(r.code in SHEET_GAPS for r in refusals):
        return "A sheet needs one subject with a picture"
    if not any(b.kind == "sheet" and not b.stale for b in opening.bindings):
        return "Assemble the sheet"
    return None


def place_status(session, opening: OpeningPlan | None, refusals) -> PlaceStatus | None:
    """The first shot's place status, or None while the segment names no opening angle."""
    if opening is None:
        return None
    angle = DIRECTION.read_angle(session, opening.angle_id)
    place = DIRECTION.read_place(session, angle["place_id"])
    found = {"set_id": None, "set_name": None, "set_version": None}
    current = None
    if place["set_id"]:
        row = STORE.get(session, place["set_id"])
        current = next((v for v in row["versions"] if v["id"] == row["current_version_id"]), None)
        found = {
            "set_id": row["id"],
            "set_name": row["name"],
            "set_version": current and current["ordinal"],
        }
    choice = opening.recommended
    uses_views = bool(choice and Block.PLACE_VIEWS in choice.blocks)
    view = next((b for b in opening.bindings if b.kind == "view" and not b.stale), None)
    made = set_views.rendered(session, view.asset_id) if view and found["set_id"] else None
    rendered = (
        next(
            v["ordinal"]
            for v in STORE.get(session, made["set_id"])["versions"]
            if v["id"] == made["version_id"]
        )
        if made
        else None
    )
    return PlaceStatus(
        place_id=place["id"],
        place_name=place["name"],
        **found,
        uses_views=uses_views,
        uses=view_producer(place).uses if uses_views else "Text only",
        rendered_version=rendered,
        update_available=bool(made and current and made["version_id"] != current["id"]),
        blocker=_blocker(session, opening, found, refusals, view, view_producer(place))
        if uses_views
        else None,
    )
