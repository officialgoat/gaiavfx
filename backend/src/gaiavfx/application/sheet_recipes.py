"""The recipe of an assembled views-only sheet: the place it shows and its approved tiles (Direction's sheet reader)."""

from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

_direction = DirectionService()  # plain reads: evidence and angles
_operations = OperationService()


def sheet_reference(session, asset_id, place_id):
    """Validate sheet membership using public owner interfaces, for render and judgments."""
    MediaLibrary.asset_info(session, asset_id)
    for candidate in _operations.candidates_for_asset(session, asset_id):
        operation = _operations.operation(session, candidate["operation_id"])
        if operation["kind"] != "assemble" or operation["request"].get("place_id") != place_id:
            continue
        request = operation["request"]
        angles = {row["id"] for row in _direction.angles(session, place_id)}
        for tile in request["tiles"]:
            if isinstance(tile, dict):
                continue
            try:
                evidence = _direction.read_evidence(session, tile)
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
