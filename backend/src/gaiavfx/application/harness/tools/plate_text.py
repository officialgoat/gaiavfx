"""Removal wording must preserve the scene's authored structure."""

import re

from gaiavfx.application.harness.world import direction
from gaiavfx.shared.errors import ApplicationError

BARRIERS = re.compile(
    r"\b(?:railing|balustrade|fence|barrier|wall|hedge|rope|counter)s?\b", re.IGNORECASE
)


def require_safe_removal(session, context, shot, behind):
    angle = direction.read_angle(session, shot["plate_angle_id"])
    place = direction.read_place(session, angle["place_id"])
    text = " ".join(
        [place["description"], *(a["description"] for a in direction.angles(session, place["id"]))]
    )
    barriers = {m.casefold().rstrip("s") for m in BARRIERS.findall(text)}
    removed_barriers = {m.casefold().rstrip("s") for m in BARRIERS.findall(behind)}
    protected = re.findall(r"\b(?:keep|preserve|retain)\s+([^.!?;]+)", text, re.IGNORECASE)
    words = set(re.findall(r"\w+", behind.casefold()))
    kept_words = set(re.findall(r"\w+", " ".join(protected).casefold())) - {
        "the",
        "a",
        "an",
        "and",
        "of",
        "in",
        "place",
        "unchanged",
        "intact",
        "all",
    }
    if barriers & removed_barriers or kept_words & words:
        raise ApplicationError(
            "behind describes what is revealed behind the removed person. It must not name "
            "the scene's barrier or a scene part authored to keep; describe the unobstructed "
            "background instead. If nobody is present, use empty: true.",
            422,
        )


PLACEHOLDERS = {"none", "n/a", "na", "nobody", "no one", "-", "null", "unknown", "nothing", "empty"}


def require_removal_words(context, plate):
    """Removal wording names a person, a pronoun and the revealed background.

    local-e12 plate-from-removal: after "removing the walker first" the model sent
    whom, object_pronoun and behind as "none" and the removal ran on them.
    """
    from gaiavfx.application.harness.tools.input_hints import removal_requested
    from gaiavfx.modules.direction.place_text import PERSON_WORDS

    fields = {"whom": plate.whom, "object_pronoun": plate.object_pronoun, "behind": plate.behind}
    empty = [
        name for name, value in fields.items() if (value or "").strip().casefold() in PLACEHOLDERS
    ]
    if not empty:
        return
    asked = removal_requested(context)
    people = {*PERSON_WORDS, "walker", "passer-by", "passerby"}
    people |= {s["name"].casefold() for s in context.state.cast if s.get("name")}
    named = next((w for w in re.findall(r"[\w-]+", asked.casefold()) if w in people), "")
    whom = (
        f"the {named}"
        if named and named not in {"he", "she", "him", "her"}
        else "the person to remove"
    )
    raise ApplicationError(
        f"{', '.join(empty)} cannot be a placeholder. whom: who to remove in plain words, for example "
        f"'{whom}'; object_pronoun: him, her or them; behind: the background the removal reveals, for "
        "example 'the open courtyard paving and the far arcade'. If nobody is in the image, use empty: true.",
        422,
    )
