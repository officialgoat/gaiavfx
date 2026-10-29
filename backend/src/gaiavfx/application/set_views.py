"""Set views (ADR 0019 §3): pictures of a place's angles rendered from the set the place is built as.

The `set_views` operation renders the chosen angles' cameras from the set's current accepted version, frozen when
it starts, with that version's hidden regions hidden. Its pictures are candidates, reviewed in the angle-view review
like a survey's; approval records them as `set_view` evidence.

What such a picture depends on (`set_view_inputs`) is the place's set, the camera that set holds for the angle and
the hidden regions of the version it was rendered at. A newer accepted version of the set is not one of them: the
approved pixels have not changed, so it is an update to offer, never staleness.
"""

from gaiavfx.application.engine_jobs import engine_job
from gaiavfx.application.operations import PRODUCERS, create_operation
from gaiavfx.application.sets import STORE
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

OPERATION = "set_views"
STEP = "worldbuilder.render_views"
_direction = DirectionService()  # plain reads only: places and angles
_operations = OperationService()


def rendered(session, asset_id) -> dict | None:
    """How a picture was rendered: the set, version and view of the `set_views` operation that made it, or None
    for a picture that no set render produced."""
    for candidate in _operations.candidates_for_asset(session, asset_id):
        operation = _operations.operation(session, candidate["operation_id"])
        if operation["kind"] != OPERATION:
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
    """The inputs a set view's picture depends on now (Direction's view reader for `set_view` evidence)."""
    angle = _direction.read_angle(session, evidence["angle_id"])
    set_id = _direction.read_place(session, angle["place_id"])["set_id"]
    view = STORE.view_for_angle(session, set_id, angle["id"]) if set_id else None
    made = rendered(session, evidence["asset_id"])
    return {
        "set_id": set_id,
        "view_id": view and view["id"],
        "viewmat": view and view["viewmat"],
        "fov": view and view["fov"],
        "hide_region_ids": sorted(
            r["id"] for r in STORE.hide_regions(session, made["set_id"], made["version_id"])
        )
        if made
        else None,
    }


def place_cameras(session, place_id) -> dict:
    """The place's set, its current accepted version, and each angle's camera in it (None where none is aimed)."""
    place = _direction.read_place(session, place_id)
    if not place["set_id"]:
        raise ApplicationError(f"{place['name']} is not built as a stage yet.", 409)
    row = STORE.get(session, place["set_id"])
    version = next((v for v in row["versions"] if v["id"] == row["current_version_id"]), None)
    return {
        "place": place,
        "set": row,
        "version": version,
        "cameras": {
            angle["id"]: STORE.view_for_angle(session, row["id"], angle["id"])
            for angle in _direction.angles(session, place_id)
        },
    }


def views_request(session, *, place_id, angle_ids) -> dict:
    """What rendering these angles from the place's set would freeze, after every check a render makes: the set's
    current accepted version and its splat, each angle's camera, and the version's hidden regions. Retries compare a
    rejected render's request with this one."""
    found = place_cameras(session, place_id)
    version, cameras = found["version"], found["cameras"]
    if version is None:
        raise ApplicationError(
            f"Accept a version of {found['set']['name']} before rendering views from it.", 409
        )
    if not version["splat_artifact_id"]:
        raise ApplicationError(f"v{version['ordinal']} of the stage has no splat to render.", 409)
    angle_ids = list(dict.fromkeys(angle_ids))
    if not angle_ids:
        raise ApplicationError("Name at least one angle to render.", 422)
    unknown = [a for a in angle_ids if a not in cameras]
    if unknown:
        raise ApplicationError("Render only angles of this place.", 422)
    unaimed = [a for a in angle_ids if cameras[a] is None]
    if unaimed:
        names = {a["id"]: a["name"] for a in _direction.angles(session, place_id)}
        raise ApplicationError(
            "Aim a camera in the stage for "
            + ", ".join(names[a] for a in unaimed)
            + " first: save a view in the stage workbench for that angle.",
            409,
        )
    splat = MediaLibrary.artifact(session, version["splat_artifact_id"])
    hide = STORE.hide_regions(session, found["set"]["id"], version["id"])
    return {
        "place_id": place_id,
        "set_id": found["set"]["id"],
        "version_id": version["id"],
        "splat_artifact_id": splat["id"],
        "splat_sha256": splat["sha256"],
        "hide_region_ids": sorted(r["id"] for r in hide),
        "hide": [{"shape": r["shape"], "params": r["params"]} for r in hide],
        "views": [
            {
                "angle_id": angle_id,
                "view_id": cameras[angle_id]["id"],
                **{k: cameras[angle_id][k] for k in ("viewmat", "fov", "width", "height")},
            }
            for angle_id in angle_ids
        ],
    }


def start_set_views(session, library, *, place_id, angle_ids, request_key=None):
    """Render the angles' cameras from the place's set: one engine job, its pictures the operation's candidates."""
    request = views_request(session, place_id=place_id, angle_ids=angle_ids)
    job = engine_job(
        STEP,
        kind="image",
        params={
            "views": [
                {"key": v["angle_id"], **{k: v[k] for k in ("viewmat", "fov", "width", "height")}}
                for v in request["views"]
            ],
            "hide": request["hide"],
        },
        inputs={
            "splat": {
                "source": "artifact",
                "artifact_id": request["splat_artifact_id"],
                "sha256": request["splat_sha256"],
                "directory": "input",
            }
        },
    )
    return create_operation(
        session,
        _operations,
        PRODUCERS,
        kind=OPERATION,
        request_key=request_key,
        request=request,
        jobs=[job],
        budget_limit=None,
        # engine work reserves no render budget (ADR 0015 §5)
        budget=0,
    )
