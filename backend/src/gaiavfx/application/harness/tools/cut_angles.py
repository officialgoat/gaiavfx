"""Resolve camera names and report the cut actually stored by Direction."""

import re

from gaiavfx.application.harness.actions import CROSSES_BARRIER_GUIDANCE
from gaiavfx.application.harness.tools.authoring import require_different_angles
from gaiavfx.application.harness.tools.common import candidates, scoped
from gaiavfx.application.harness.world import direction
from gaiavfx.shared.errors import ApplicationError


def angle(place, value, field):
    """IDs win; names must be unique within the supplied place, ignoring case."""
    rows = place["angles"]
    if not value:
        return scoped(rows, value, field)
    exact = next((row for row in rows if row["id"] == value), None)
    if exact:
        return exact
    matches = [row for row in rows if row["name"].casefold() == value.casefold()]
    if len(matches) == 1:
        return matches[0]
    reason = "ambiguous" if matches else "unknown"
    raise ApplicationError(
        f"{field} {value!r} is {reason} in {place['name']}: use an angle id or unique name from {candidates(rows)}.",
        422,
    )


def resolve_angles(context, source, target, *, place_id=None):
    places = [p for p in context.state.places if not place_id or p["id"] == place_id]
    source = angle(
        {"name": "this scope", "angles": [a for p in places for a in p["angles"]]},
        source,
        "from_angle_id",
    )
    place = next(p for p in places if any(a["id"] == source["id"] for a in p["angles"]))
    if any(
        p["id"] != place["id"] and any(a["id"] == target for a in p["angles"])
        for p in context.state.places
    ):
        raise ApplicationError(
            "A cut's angles must belong to the same place. Use edit_place add_angle "
            "at the source place for the new camera.",
            422,
        )
    target = angle(place, target, "to_angle_id")
    require_different_angles(source["id"], target["id"])
    return source["id"], target["id"]


def cut_result(session, cut):
    source = direction.read_angle(session, cut["from_angle_id"])
    target = direction.read_angle(session, cut["to_angle_id"])
    hint = barrier_hint(direction.read_place(session, target["place_id"]), target)
    return {
        "cut_id": cut["id"],
        "revision": cut["revision"],
        "from_angle_id": source["id"],
        "to_angle_id": target["id"],
        "relation": cut["relation"],
        "crosses_barrier": cut["crosses_barrier"],
        "message": f"cut {cut['id']} f{cut['frame']}: {source['name']} -> {target['name']}, "
        f"relation {cut['relation'] or 'unknown'}" + (f". {hint}" if hint else ""),
    }


def barrier_hint(place, target):
    """Quote camera-location evidence, never infer a crossing from a subject or view.

    The source might already be behind the same barrier. Keep the authored answer
    and invite comparison; the target description alone does not decide it.
    """
    barriers = re.findall(
        r"\b(?:railing|balustrade|fence|wall|rope|counter|hedge)\b",
        place.get("description", ""),
        re.IGNORECASE,
    )
    description = target.get("description", "")
    for clause in re.split(r"[.!?;\n]", description):
        location = re.search(
            r"\b(?:from|camera (?:is|stands|sits|positioned))\b(.*)", clause, re.IGNORECASE
        )
        if not location:
            continue
        location = re.split(
            r"\b(?:looking|facing|viewing|seeing)\b", location[1], flags=re.IGNORECASE
        )[0]
        if re.search(r"\b(?:not|never|no)\b", location, re.IGNORECASE):
            continue
        if any(
            re.search(
                r"\b(?:behind|across|other side of)\s+(?:\w+\s+){0,4}" + re.escape(barrier) + r"\b",
                location,
                re.IGNORECASE,
            )
            for barrier in barriers
        ):
            return (
                f'Target angle {target["name"]} says "{clause.strip()}". '
                f"Compare it with the source camera: {CROSSES_BARRIER_GUIDANCE}."
            )
    return ""
