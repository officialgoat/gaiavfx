"""Resolve the agent's insertion prose against the cut and its reviewed scene."""

import re
from typing import Literal

from pydantic import Field

from gaiavfx.application.harness.tools.authoring import MOTION
from gaiavfx.application.harness.tools.pin_wording import placement_problems
from gaiavfx.application.harness.world import direction
from gaiavfx.modules.direction.insertion import (
    PLACEMENT_FIELDS,
    continuity_facing,
    screen_facing,
    validate_scene_keep,
)
from gaiavfx.modules.direction.place_text import validate_place_text
from gaiavfx.modules.direction.service import validate_insertion_wording
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


class InsertionInput(StrictModel):
    who: str | None = None
    looks: str | None = Field(
        default=None, description="Clothing; defaults to the cast description."
    )
    pronouns: Literal["he", "she"]
    facing: Literal["screen_left", "screen_right", "toward_camera"] | None = Field(
        default=None,
        description="Omit for continuity; toward_camera only if requested, with behind.",
    )
    plate: str | None = Field(
        default=None,
        description="Empty target view; defaults to angle text.",
    )
    keep: str | None = Field(
        default=None,
        description="Preserve scene (railing, light), never subject items.",
    )
    where: str | None = Field(
        default=None, description="Short position phrase; no motion. Template adds walk."
    )
    size: str | None = Field(
        default=None, description="Image-height fraction; beyond a barrier: one fifth to one third."
    )
    distance_description: str | None = None
    along: str | None = None
    barrier: str | None = None
    feet_placement: str | None = None
    occlude: str | None = None
    behind: str | None = None


def insertion_facts(session, cut):
    segment = direction.read_segment(session, cut["segment_id"])
    before = [p for p in segment["phases"] if p["start_frame"] < cut["frame"]]
    previous = max(before, key=lambda p: p["start_frame"], default=None)
    if previous is None and segment["continues"] and segment["ordinal"] > 0:
        movie = direction.read_movie(session, segment["movie_id"])
        predecessor = movie["segments"][segment["ordinal"] - 1]
        previous = max(predecessor["phases"], key=lambda p: p["start_frame"], default=None)
    facing = continuity_facing(
        previous["content"].get("screen_direction", "") if previous else "",
        cut["relation"],
        cut["crosses_barrier"],
    )
    target = direction.read_angle(session, cut["to_angle_id"])
    after = next((p for p in segment["phases"] if p["start_frame"] == cut["frame"]), None)
    intent = (
        " ".join(after["content"].get(k, "") for k in ("description", "screen_direction"))
        if after
        else ""
    )
    return segment, target, facing, intent


def toward_camera_requested(text):
    # A small explicit phrase rule; a reverse alone never implies frontal facing.
    return any(
        re.search(
            r"\b(?:towards?[_ -](?:the[_ -])?camera|faces? (?:the )?camera|facing (?:the )?camera)\b",
            clause,
            re.IGNORECASE,
        )
        and not re.search(r"\b(?:not|never|don't|no)\b", clause, re.IGNORECASE)
        for clause in re.split(r"[.!?;,\n]", text)
    )


FIELD_MEANINGS = {
    "who": "the person's name, for example 'Arin'",
    "looks": "the person's clothing from the cast description",
    "plate": "the empty approved target view, from its angle text",
    "keep": "scene parts to preserve, never clothing or cast keeps",
    "where": "the person's position in the target scene",
    "size": "a fraction of the image height (beyond a barrier, one fifth to one third)",
    "facing": "screen_left or screen_right; toward_camera only on explicit request",
    "behind": "scene features behind the subject to preserve",
    **PLACEMENT_FIELDS,
}


def insertion_defaults(session, context, cut):
    from gaiavfx.application.harness.tools.input_hints import (
        scene_keep_suggestion,
        scene_placement,
    )

    segment, target, facing, _ = insertion_facts(session, cut)
    subjects = [direction.read_subject(session, sid) for sid in segment["content"]["subjects"]]
    front = toward_camera_requested(context.latest_user_message)
    defaults = {}
    if len(subjects) == 1:
        defaults.update(who=subjects[0]["name"], looks=subjects[0]["description"])
    if front or facing:
        defaults["facing"] = "toward_camera" if front else facing
    approved = any(e["approved"] for e in direction.evidence(session, target["id"]))
    if approved and target["description"].strip():
        defaults.update(
            plate=target["description"], keep=scene_keep_suggestion(target["description"])
        )
        placement = scene_placement(target["description"], cut["crosses_barrier"])
        # The fallback wording is a hint, not a grounded position to persist.
        if placement["where"].startswith("at the position in the approved view"):
            placement.pop("where")
        defaults.update(placement)
    return defaults, subjects, front


def resolve_insertion(session, context, cut, wording):
    from gaiavfx.application.harness.tools.common import UUID_TEXT

    defaults, subjects, requested_front = insertion_defaults(session, context, cut)
    supplied = {k: v for k, v in wording.items() if v is not None and v != ""}
    errors = {}
    known_ids = (
        {str(subjects[0][key]) for key in ("id", "identity_asset_id")}
        if len(subjects) == 1
        else set()
    )
    for field in ("who", "looks"):
        value = supplied.get(field)
        if value in known_ids and defaults.get(field):
            supplied.pop(field)
        elif value and UUID_TEXT.search(value):
            errors[field] = FIELD_MEANINGS[field]
    looks = supplied.get("looks", "")
    if re.search(
        r"\b(?:camera|facing|turned|walking|screen_left|screen_right)\b", looks, re.IGNORECASE
    ):
        if defaults.get("looks"):
            supplied.pop("looks")
        else:
            errors["looks"] = FIELD_MEANINGS["looks"]
    result = {**defaults, **supplied}
    _, _, _, intent = insertion_facts(session, cut)
    errors.update(
        placement_problems(
            result,
            cut,
            context.state.plans.get(cut["id"], {}),
            moving=bool(MOTION.search(intent) or screen_facing(intent)),
        )
    )
    for field, meaning in FIELD_MEANINGS.items():
        if isinstance(result.get(field), str) and UUID_TEXT.search(result[field]):
            errors[field] = meaning
    if result.get("facing") == "toward_camera" and not requested_front:
        errors["facing"] = (
            "toward_camera needs an explicit user request; use continuity or screen_left/screen_right"
        )
    if result.get("facing") not in {"screen_left", "screen_right", "toward_camera"}:
        errors["facing"] = FIELD_MEANINGS["facing"]
    required = ["who", "looks", "plate", "keep", "where", "size"]
    required += ["behind"] if result.get("facing") == "toward_camera" else list(PLACEMENT_FIELDS)
    for field in required:
        if not result.get(field, "").strip():
            errors[field] = FIELD_MEANINGS[field]
    if result.get("plate", "").strip().casefold() in {
        "the reviewed target view",
        "reviewed target view",
        "the target view",
    }:
        errors["plate"] = FIELD_MEANINGS["plate"] + "; not its review status"
    if result.get("plate"):
        try:
            validate_place_text(result["plate"], [s["name"] for s in subjects])
        except ValueError as exc:
            errors["plate"] = str(exc)
    if result.get("keep"):
        try:
            validate_scene_keep(
                result["keep"],
                result.get("looks", ""),
                [item for s in subjects for item in s["keeps"]],
            )
        except ValueError as exc:
            errors["keep"] = str(exc)
    if result.get("size"):
        # Direction remains authoritative for size syntax. Validate it in an
        # otherwise complete value so all errors can be returned in one refusal.
        probe = {
            "who": "Arin",
            "looks": "coat",
            "pronouns": "he",
            "facing": "toward_camera",
            "plate": "courtyard",
            "keep": "paving",
            "where": "on paving",
            "behind": "wall",
            "size": result["size"],
        }
        try:
            validate_insertion_wording(probe)
        except ApplicationError:
            errors["size"] = FIELD_MEANINGS["size"]
    if errors:
        raise ApplicationError(
            "Invalid insertion fields: "
            + "; ".join(f"{field}: {meaning}" for field, meaning in errors.items()),
            422,
        )
    return validate_insertion_wording(result)
