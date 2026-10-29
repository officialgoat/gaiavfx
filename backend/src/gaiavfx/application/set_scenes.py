"""Use a set for a scene (ADR 0019 §1, §5): the segment's first shot opens on an angle of a place built as the set and
asks for the place's views.

One command, three effects, said before they happen (`use_set_preview`):
- the place is built as the set: every scene in that place reads the set from then on;
- the segment opens on the angle (a segment with cuts starts its first cut there, which clears that cut's relation);
- the first shot asks for a views-only sheet.
Undo restores the segment's first shot; the place stays built as the set (place links have no movie receipt, like
plates). No camera is aimed here: an angle's camera is chosen in the set workbench, where every view shows which
angle it serves, so nothing is taken from another shot.
"""

from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.sets import STORE, link_place
from gaiavfx.shared.errors import ApplicationError

TECHNIQUE = "opening_views"


def _other_scenes(session, place_id, segment_id) -> list[dict]:
    """The other segments whose first shot opens in this place, as people name them."""
    return [
        {
            "segment_id": row["segment_id"],
            "movie": row["movie"],
            "label": f"Segment {row['ordinal'] + 1:02d}",
        }
        for row in DIRECTION.segments_opening_in(session, place_id)
        if row["segment_id"] != segment_id
    ]


def _target(session, segment, angle_id, place_id, angle_name):
    """The angle the first shot opens on: its own, or the one chosen, or a new one in the chosen place."""
    if segment["opening_angle_id"]:
        return DIRECTION.read_angle(session, segment["opening_angle_id"]), None
    if angle_id:
        return DIRECTION.read_angle(session, angle_id), None
    if place_id and angle_name and angle_name.strip():
        return None, (DIRECTION.read_place(session, place_id), angle_name.strip())
    raise ApplicationError(
        "Choose the angle the first shot opens on, or a place and a name for a new one.", 422
    )


def use_set_preview(session, segment_id, *, set_id, angle_id=None, place_id=None, angle_name=None):
    """What Use set would change, before it does."""
    segment = DIRECTION.read_segment(session, segment_id)
    row = STORE.get(session, set_id)
    angle, new = _target(session, segment, angle_id, place_id, angle_name)
    place = DIRECTION.read_place(session, angle["place_id"]) if angle else new[0]
    camera = STORE.view_for_angle(session, set_id, angle["id"]) if angle else None
    return {
        "set_name": row["name"],
        "accepted": row["current_version_id"] is not None,
        "place_name": place["name"],
        "angle_name": angle["name"] if angle else new[1],
        "new_angle": angle is None,
        "relinks_from": STORE.get(session, place["set_id"])["name"]
        if place["set_id"] and place["set_id"] != set_id
        else None,
        "other_scenes": _other_scenes(session, place["id"], segment_id),
        "first_cut_changes": bool(
            segment["cuts"] and angle and segment["cuts"][0]["from_angle_id"] != angle["id"]
        ),
        "camera": camera and camera["name"],
    }


def use_set(
    session, segment_id, *, expected_revision, set_id, angle_id=None, place_id=None, angle_name=None
):
    """Build the first shot's place as the set and ask the first shot for its views."""
    segment = DIRECTION.read_segment(session, segment_id)
    if segment["revision"] != expected_revision:
        raise ApplicationError("Segment revision is stale.", 409)
    STORE.get(session, set_id)
    angle, new = _target(session, segment, angle_id, place_id, angle_name)
    if angle is None:
        angle = DIRECTION.add_angle(session, new[0]["id"], new[1])
    place = DIRECTION.read_place(session, angle["place_id"])
    if place["set_id"] != set_id:
        link_place(session, place["id"], expected_revision=place["revision"], set_id=set_id)
    changes = {"opening_technique": TECHNIQUE}
    if segment["opening_angle_id"] != angle["id"]:
        changes["opening_angle_id"] = angle["id"]
    return DIRECTION.update_segment(session, segment_id, expected_revision, **changes)
