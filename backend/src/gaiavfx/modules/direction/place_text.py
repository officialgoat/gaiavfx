"""Empty-place prose: a closed vocabulary, not an inference about an image."""

import re

PERSON_WORDS = (
    "man",
    "woman",
    "person",
    "people",
    "he",
    "she",
    "him",
    "her",
    "walks",
    "walking",
    "stands",
    "standing",
    "his",
    "figure",
    "subject",
)
EMPTY_PLACE_REFUSAL = (
    "Describe only the empty place from this camera (what the lens sees with nobody in frame); "
    "people are added later by the pin"
)


# What each of a place's texts describes; an empty place has nobody in it.
PLACE_TEXT = {
    "description": "the empty place and its landmarks",
    "sound": "ambient sound of the empty place; no footsteps, voices, breathing or fabric",
    "overhead": "the final view looking straight down on the place",
}
SOUNDS = re.compile(r"\b(?:footsteps?|voices?|breathing|fabric)\b", re.IGNORECASE)


def validate_place_text(text, cast_names=()):
    words = [*PERSON_WORDS, *(name.strip() for name in cast_names if name.strip())]
    if re.search(r"(?<!\w)(?:" + "|".join(map(re.escape, words)) + r")(?!\w)", text, re.IGNORECASE):
        raise ValueError(EMPTY_PLACE_REFUSAL)
    return text


def validate_empty_sound(text, cast_names=()):
    validate_place_text(text, cast_names)
    if SOUNDS.search(text):
        raise ValueError("sound: " + PLACE_TEXT["sound"])
    return text


def validate_overhead(text, cast_names=()):
    validate_place_text(text, cast_names)
    if re.search(
        r"\b(?:sky|ceiling|overhead|look(?:s|ing)? up)\b", text, re.IGNORECASE
    ) and not re.search(
        r"\b(?:down|top[ -]down|bird.s.eye|from above|aerial)\b", text, re.IGNORECASE
    ):
        raise ValueError("overhead means " + PLACE_TEXT["overhead"] + ", not a view looking up")
    return text


def validate_place_fields(cast_names, fields):
    """Every word a place or angle is given describes the empty place: names and descriptions name no person or
    cast member, its sound has no people in it, and its overhead looks down. Fields that are None are not given."""
    rules = {"sound": validate_empty_sound, "overhead": validate_overhead}
    for field, text in fields.items():
        if text is not None:
            rules.get(field, validate_place_text)(text, cast_names)
