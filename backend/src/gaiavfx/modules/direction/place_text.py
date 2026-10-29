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


def validate_place_text(text, cast_names=()):
    words = [*PERSON_WORDS, *(name.strip() for name in cast_names if name.strip())]
    if re.search(r"(?<!\w)(?:" + "|".join(map(re.escape, words)) + r")(?!\w)", text, re.IGNORECASE):
        raise ValueError(EMPTY_PLACE_REFUSAL)
    return text
