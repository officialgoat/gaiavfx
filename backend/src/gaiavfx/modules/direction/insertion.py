"""Model-free requests for placing or removing a subject in a reviewed view."""

import math
import re
from dataclasses import dataclass
from typing import Literal

PLACEMENT_FIELDS = {
    "distance_description": "distance from the camera, such as mid-distance",
    "along": "the scene path followed, such as along the paving",
    "barrier": "the scene barrier parallel to that path, such as the iron railing",
    "feet_placement": "where the feet contact the scene's ground",
    "occlude": "which scene features hide which parts of the body, or that none do",
}
KEEP_REFUSAL = (
    "keep names parts of the target scene to preserve (railing, columns, trees and light), "
    "never the subject's clothing or keeps items; looks describes the subject's clothing."
)


def validate_scene_keep(keep, looks, keeps=()):
    """Reject repeated subject terms, ignoring function words and shared colour adjectives."""
    neutral = {
        "a",
        "an",
        "the",
        "and",
        "or",
        "with",
        "in",
        "of",
        "from",
        "on",
        "at",
        "to",
        "as",
        "is",
        "same",
        "wearing",
        "wears",
        "has",
        "carrying",
        "light",
        "lighting",
        "dark",
        "black",
        "brown",
        "red",
        "blue",
        "white",
        "green",
        "tan",
        "grey",
        "gray",
        "long",
        "short",
    }
    words = lambda text: set(re.findall(r"\w+", text.casefold())) - neutral
    if words(keep) & (words(looks) | set().union(*(words(item) for item in keeps))):
        raise ValueError(KEEP_REFUSAL)


def screen_facing(text):
    """Recognize explicit screen-direction phrases only; unknown directions stay unknown."""
    text = re.sub(r"[_\-]", " ", text.casefold())
    left = bool(re.search(r"\bright\s+to\s+left\b|\bscreen\s+left\b|\bleftward\b", text))
    right = bool(re.search(r"\bleft\s+to\s+right\b|\bscreen\s+right\b|\brightward\b", text))
    return "screen_left" if left and not right else "screen_right" if right and not left else None


def continuity_facing(previous_direction, relation, crosses_barrier):
    facing = screen_facing(previous_direction)
    if facing and relation == "reverse" and crosses_barrier is True:
        return {"screen_left": "screen_right", "screen_right": "screen_left"}[facing]
    return facing


@dataclass(frozen=True)
class Insertion:
    view_asset: str
    subject_asset: str
    who: str
    looks: str
    pronouns: Literal["he", "she"]
    facing: Literal["screen_left", "screen_right", "toward_camera"]
    plate: str
    keep: str
    where: str
    size: str
    distance_description: str | None = None
    along: str | None = None
    barrier: str | None = None
    feet_placement: str | None = None
    occlude: str | None = None
    behind: str | None = None
    # Wording placed pins too close in 8/8 takes. Structured placement must replace the authored `where` phrase
    # after testing; this fact is deliberately not used by the current prompt compiler.
    distance_to_barrier_m: float | None = None

    def __post_init__(self):
        if self.facing not in {"screen_left", "screen_right", "toward_camera"}:
            raise ValueError("unknown facing")
        if self.pronouns not in {"he", "she"}:
            raise ValueError("pronouns must be he or she")
        for name in (
            "view_asset",
            "subject_asset",
            "who",
            "looks",
            "plate",
            "keep",
            "where",
            "size",
        ):
            if not getattr(self, name):
                raise ValueError(f"{name} is required")
        if self.distance_to_barrier_m is not None and (
            not math.isfinite(self.distance_to_barrier_m) or self.distance_to_barrier_m <= 0
        ):
            raise ValueError("distance to barrier must be positive and finite")
        if not re.search(
            r"\b(?:half|halves|thirds?|quarters?|fifths?|sixths?|sevenths?|eighths?|ninths?|tenths?|"
            r"percent|percentage|fraction|proportion)\b|\d+\s*/\s*\d+|\d+(?:\.\d+)?\s*%|\b0\.\d+\b",
            self.size,
            re.IGNORECASE,
        ):
            raise ValueError(
                'size is a fraction of the image height, for example "about two thirds"'
            )
        validate_scene_keep(self.keep, self.looks)
        required = (
            ("behind",)
            if self.facing == "toward_camera"
            else ("distance_description", "along", "barrier", "feet_placement", "occlude")
        )
        missing = [name for name in required if not getattr(self, name)]
        if missing:
            if self.facing == "toward_camera":
                raise ValueError(
                    "behind is required for toward_camera: scene features behind the subject to preserve"
                )
            raise ValueError(
                "Lateral facing requires five placement fields: "
                + "; ".join(f"{name}: {meaning}" for name, meaning in PLACEMENT_FIELDS.items())
            )


@dataclass(frozen=True)
class Removal:
    view_asset: str
    whom: str
    object_pronoun: str
    behind: str

    def __post_init__(self):
        for name in ("view_asset", "whom", "object_pronoun", "behind"):
            if not getattr(self, name):
                raise ValueError(f"{name} is required")
