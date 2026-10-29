"""Bounded authoring checks at the harness boundary; Direction rules stay unchanged."""

import re
from copy import deepcopy

from gaiavfx.application.harness.world import direction
from gaiavfx.modules.direction.place_text import validate_place_text
from gaiavfx.shared.errors import ApplicationError

MOTION = re.compile(
    r"\b(?:walk(?:s|ing)?|run(?:s|ning)?|jog(?:s|ging)?|stroll(?:s|ing)?|stride[sd]?|striding|"
    r"cross(?:es|ing)?|mov(?:e[sd]?|ing)|pac(?:e[sd]?|ing))\b",
    re.IGNORECASE,
)
SOUNDS = re.compile(r"\b(?:footsteps?|voices?|breathing|fabric)\b", re.IGNORECASE)
PLACE_TEXT = {
    "description": "the empty place and its landmarks",
    "sound": "ambient sound of the empty place; no footsteps, voices, breathing or fabric",
    "overhead": "the final view looking straight down on the place",
}


def require_different_angles(source, target):
    if source == target:
        raise ApplicationError(
            "same_angle_cut: the cut needs a different target angle: add it with edit_place "
            "add_angle (the empty place from that camera), then cut to it",
            422,
        )


def require_shot(text):
    words = re.findall(r"[\w]+", text)
    if len(words) < 3 or re.fullmatch(
        r"(?i)(?:(?:extreme|medium|full|wide|long|close|up|body|shot|size)[\s-]*)+", text.strip()
    ):
        raise ApplicationError(
            "shot describes what the new camera shows in several words, not a bare shot size; "
            "for example 'Arin beyond the railing with the sunlit courtyard behind him'.",
            422,
        )


def validate_overhead(text, names=()):
    validate_place_text(text, names)
    if re.search(
        r"\b(?:sky|ceiling|overhead|look(?:s|ing)? up)\b", text, re.IGNORECASE
    ) and not re.search(
        r"\b(?:down|top[ -]down|bird.s.eye|from above|aerial)\b", text, re.IGNORECASE
    ):
        raise ValueError("overhead means " + PLACE_TEXT["overhead"] + ", not a view looking up")


def require_motion_direction(content):
    if (
        MOTION.search(content.get("description", ""))
        and not content.get("screen_direction", "").strip()
    ):
        raise ApplicationError("state the walker's screen direction, e.g. 'left to right'", 422)


def require_assemblable(session, segment_id, *, draft=False):
    """Validate through Direction before a structure edit's transaction can commit."""
    try:
        direction.assemble_segment(session, segment_id)
    except ApplicationError as exc:
        hint = (
            "Add each cut's shot and screen_direction; phases start at 0 and at every cut frame."
            if draft
            else "Supply a phase describing the new shot and screen_direction at every cut frame; keep the phase at 0."
        )
        raise ApplicationError(f"Segment cannot assemble: {exc}. {hint}", exc.status) from exc


def phase_direction_schema(schema, context):
    """Require direction for the moving target that lacks it, not unrelated phases."""
    from gaiavfx.application.harness.actions import pin_enum

    missing = {
        p["id"]
        for s in context.state.segments
        for p in s["phases"]
        if MOTION.search(p.get("description", "")) and not p.get("screen_direction", "").strip()
    }
    moving_request = bool(MOTION.search(context.latest_user_message))
    result = []
    for branch in schema.get("oneOf", [schema]):
        if branch["properties"]["op"]["const"] != "set":
            if branch["properties"]["op"]["const"] == "add" and moving_request:
                branch["properties"]["screen_direction"] = {"type": "string", "minLength": 1}
            result.append(branch)
            continue
        ids = branch["properties"]["phase_id"]["enum"]
        needed = ids if moving_request else [pid for pid in ids if pid in missing]
        if len(needed) < len(ids):
            result.append(pin_enum(branch, "phase_id", [pid for pid in ids if pid not in needed]))
        if needed:
            required = pin_enum(deepcopy(branch), "phase_id", needed)
            required["required"].append("screen_direction")
            required["properties"]["screen_direction"] = {"type": "string", "minLength": 1}
            result.append(required)
    return result[0] if len(result) == 1 else {"oneOf": result}


def validate_empty_sound(text, names=()):
    validate_place_text(text, names)
    if SOUNDS.search(text):
        raise ValueError("sound: " + PLACE_TEXT["sound"])


def place_problems(session, context, place):
    """Report the complete survey text prerequisite set before calling its producer."""
    names = [s["name"] for s in direction.read_movie(session, context.movie_id)["subjects"]]
    problems = []
    for field, meaning in PLACE_TEXT.items():
        text = place[field]
        if not text.strip():
            problems.append(f"{field}: {meaning}")
        else:
            try:
                validator = {"sound": validate_empty_sound, "overhead": validate_overhead}.get(
                    field, validate_place_text
                )
                validator(text, names)
            except ValueError as exc:
                problems.append(f"{field}: {exc}")
    for angle in direction.angles(session, place["id"]):
        label = f"angle {angle['name']} ({angle['id']}) description"
        if not angle["description"].strip():
            problems.append(f"{label}: the empty view from this camera")
        else:
            try:
                validate_place_text(angle["description"], names)
            except ValueError as exc:
                problems.append(f"{label}: {exc}")
    return problems
