"""Direction as the application reads it (ADR 0010 §7, ADR 0019 §3).

Direction judges a binding by the inputs it was approved with. Two kinds of binding have inputs that live in other
modules: an assembled sheet's tiles (Generation) and a set view's camera and hidden regions (Sets). This module
carries both readers and the one configured service; every plan, preparation and binding check reads Direction
through it; a set view read without it is refused, never judged by a survey's inputs.

The readers depend only on module services, so any application module can import `DIRECTION` without a cycle.
"""

from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.modules.sets.service import SetStore
from gaiavfx.shared.errors import ApplicationError

SET_VIEWS = "set_views"
_operations = OperationService()
_sets = SetStore()


def sheet_reference(session, asset_id, place_id):
    """The recipe of an assembled views-only sheet of the place, its tiles checked as approved evidence of it."""
    MediaLibrary.asset_info(session, asset_id)
    for candidate in _operations.candidates_for_asset(session, asset_id):
        operation = _operations.operation(session, candidate["operation_id"])
        if operation["kind"] != "assemble" or operation["request"].get("place_id") != place_id:
            continue
        request = operation["request"]
        angles = {row["id"] for row in DIRECTION.angles(session, place_id)}
        for tile in request["tiles"]:
            if isinstance(tile, dict):
                continue
            try:
                evidence = DIRECTION.read_evidence(session, tile)
            except ApplicationError as exc:
                if exc.status != 404:
                    raise
                raise ApplicationError(
                    "The sheet contains a tile that is not approved evidence of this place.", 422
                ) from exc
            if not evidence["approved"] or evidence["angle_id"] not in angles:
                raise ApplicationError(
                    "The sheet contains a tile that is not approved evidence of this place.", 422
                )
        return request
    raise ApplicationError("The sheet must be an assembled sheet of this place.", 422)


def rendered(session, asset_id) -> dict | None:
    """How a picture was rendered: the set, version and view of the `set_views` operation that made it, or None
    for a picture that no set render produced."""
    for candidate in _operations.candidates_for_asset(session, asset_id):
        operation = _operations.operation(session, candidate["operation_id"])
        if operation["kind"] != SET_VIEWS:
            continue
        request = operation["request"]
        view = next((v for v in request["views"] if v["angle_id"] == candidate["output_key"]), None)
        if view:
            return {
                "set_id": request["set_id"],
                "version_id": request["version_id"],
                "hide_region_ids": request["hide_region_ids"],
                **view,
            }
    return None


def set_view_inputs(session, evidence: dict) -> dict:
    """The inputs a set view's picture depends on now: the place's set, the camera that set holds for the angle and
    the hidden regions of the version it was rendered at."""
    angle = DIRECTION.read_angle(session, evidence["angle_id"])
    set_id = DIRECTION.read_place(session, angle["place_id"])["set_id"]
    view = _sets.view_for_angle(session, set_id, angle["id"]) if set_id else None
    made = rendered(session, evidence["asset_id"])
    return {
        "set_id": set_id,
        "view_id": view and view["id"],
        "viewmat": view and view["viewmat"],
        "fov": view and view["fov"],
        "hide_region_ids": sorted(
            r["id"] for r in _sets.hide_regions(session, made["set_id"], made["version_id"])
        )
        if made
        else None,
    }


DIRECTION = DirectionService(sheet_reader=sheet_reference, view_reader=set_view_inputs)
