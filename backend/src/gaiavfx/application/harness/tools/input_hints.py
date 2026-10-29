"""Bounded recovery examples from scoped public records, never invented ids.

Text is a drafting example, not a visual observation or a human decision.
"""

import json
import re

from gaiavfx.application.harness.tools.common import available, revision_guidance
from gaiavfx.application.harness.tools.insertion import insertion_facts
from gaiavfx.application.harness.world import direction


def empty_language(text):
    text = re.sub(
        r"\b(?:not|isn't|isnt|never)\s+empty\b|\bempty\s*:\s*false\b", "", text, flags=re.IGNORECASE
    )
    return bool(
        re.search(
            r"\b(?:no (?:people|persons?|one)|nobody|none visible|empty)\b", text, re.IGNORECASE
        )
    )


REMOVAL_VERBS = re.compile(
    r"\b(?:remov(?:e|es|ed|ing|al)|eras(?:e|es|ing)|take out|taking out|without)\b", re.IGNORECASE
)


def removal_requested(context):
    """The user asked to remove a person from the plate source, in this message.

    A small keyword rule: a removal verb and a person word (or a cast name, or
    "walker") in one sentence of the latest message. The model otherwise picks the
    simpler empty plate even when the user said "removing the walker first".
    """
    from gaiavfx.modules.direction.place_text import PERSON_WORDS

    people = {*PERSON_WORDS, "walker", "passer-by", "passerby", "passers-by"}
    people |= {s["name"].casefold() for s in context.state.cast if s.get("name")}
    for sentence in re.split(r"[.!?\n]", context.latest_user_message or ""):
        words = set(re.findall(r"[\w-]+", sentence.casefold()))
        if REMOVAL_VERBS.search(sentence) and words & people:
            return sentence.strip()
    return ""


def known_empty_image(context, asset_id):
    if any(
        a["id"] == asset_id and a["kind"] != "image"
        for a in [*context.state.assets, *context.attachments]
    ):
        return False
    notes = context.reference_notes.get(asset_id, {})
    if empty_language(json.dumps(notes)) or empty_language(
        context.plate_rejections.get(asset_id, "")
    ):
        return True
    identities = {s.get("identity_asset_id") for s in context.state.cast}
    images = {
        a["id"]: a
        for a in [*context.state.assets, *context.attachments]
        if a["kind"] == "image" and a["id"] not in identities
    }
    if asset_id not in images:
        return False
    names = {
        key: set(re.findall(r"\w{4,}", a.get("name", "").rsplit(".", 1)[0].casefold()))
        - {"empty", "image", "screenshot", "photo", "picture"}
        for key, a in images.items()
    }
    for words in [*context.recent_user_words, context.latest_user_message]:
        for sentence in re.split(r"[.!?\n]", words):
            matches = {
                key
                for key, words in names.items()
                if words & set(re.findall(r"\w+", sentence.casefold()))
            }
            if empty_language(sentence) and (
                len(images) == 1 or asset_id in sentence or matches == {asset_id}
            ):
                return True
    return False


def scene_keep_suggestion(text):
    """Quote visible scene nouns from the angle, never the cast's belongings.

    Unrecognized scenes retain their authored text. Callers use this as a hint,
    or as an insertion default only after checking that the view is approved.
    """
    parts = re.findall(
        r"\b(?:railing|balustrade|columns?|pillars?|olive trees|trees|arcade|arches|"
        r"cobblestones?|paving|pavement|floor|walls?|windows?|doors?|buildings?|"
        r"furniture|fence|posts|glass|cable|water|pier|light|lighting)\b",
        text,
        re.IGNORECASE,
    )
    return ", ".join(dict.fromkeys(part.casefold() for part in parts))[:240] or text[:240]


def scene_placement(text, crosses_barrier):
    """Placement examples built only from the target view's own nouns.

    A fixed example would be copied into unrelated scenes, as run 3 copied "the
    reviewed target view"; unknown scenes ask for the user's position instead.
    """
    found = lambda pattern: next(iter(re.findall(pattern, text, re.IGNORECASE)), "").casefold()
    ground = found(r"\b(?:cobblestones?|paving|pavement|floor|sand|grass|path|road|street|deck)\b")
    barrier = found(r"\b(?:railing|balustrade|fence|wall|rope|counter|hedge)\b")
    where = "at the position in the approved view that the brief asks for"
    if ground:
        position = (
            "open paving" if ground == "paving" and "open paving" in text.casefold() else ground
        )
        where = f"on the {position}" + (
            f" beyond the {barrier}" if barrier and crosses_barrier else ""
        )
    values = {"where": where}
    if ground:
        values |= {
            "along": f"along the {ground}",
            "feet_placement": f"Both feet contact the {ground}.",
        }
    if barrier:
        values |= {
            "barrier": f"the {barrier}",
            "occlude": f"The {barrier} hides the parts of the body behind it, if any.",
        }
    if ground and barrier and crosses_barrier:
        values |= {"size": "one fifth", "distance_description": "distant, small"}
    return values


def example(value):
    return " Adapt the wording to the brief/reference. Example input: " + json.dumps(value)


def place_input(session, context, place_id=None, angle_id=None):
    candidates = [p for p in context.state.places if not place_id or p["id"] == place_id]
    if not candidates:
        return {
            "op": "add_place",
            "name": "New place",
            "description": "Describe the empty place and its landmarks.",
            "sound": "Describe ambient sound of the empty place.",
            "overhead": "Describe the final straight-down view.",
            "angles": [{"name": "Opening view", "description": "Describe this empty camera view."}],
        }
    place = direction.read_place(session, candidates[0]["id"])
    angles = direction.angles(session, place["id"])
    angles = (
        [a for a in angles if a["id"] == angle_id]
        if angle_id
        else [a for a in angles if not a["description"].strip()] or angles[:1]
    )
    return {
        "op": "set",
        "place_id": place["id"],
        "expected_revision": place["revision"],
        **{
            key: wording
            for key, wording in {
                "description": f"Describe {place['name'][:120]} and its landmarks.",
                "sound": "Describe ambient sound of the empty place.",
                "overhead": "Describe the final straight-down view.",
            }.items()
            if not place[key].strip()
        },
        "angles": [
            {
                "angle_id": a["id"],
                "expected_revision": a["revision"],
                "description": f"Describe the view from {a['name'][:80]}.",
            }
            for a in angles[:3]
        ],
    }


def plate_input(context, shot):
    assets = {a["id"]: a for a in [*context.state.assets, *context.attachments]}
    identities = {s.get("identity_asset_id") for s in context.state.cast}
    place = next(p for p in context.state.places if p["id"] == shot["place_id"])
    original = (place.get("plate") or {}).get("source")
    if original:
        empty = original.get("empty") or known_empty_image(context, original["source_asset"])
        return example(
            {
                "cut_id": shot["id"],
                "plate": {
                    "source_asset": original["source_asset"],
                    "angle_id": shot["plate_angle_id"],
                    **(
                        {"frame": original["frame"]}
                        if original.get("frame") is not None and not empty
                        else {}
                    ),
                    **({"empty": True} if empty else original["wording"]),
                },
            }
        )
    linked = place.get("imported_images", [])
    recent = [
        a["id"]
        for a in reversed(context.attachments)
        if a["kind"] == "image" and a["id"] not in identities
    ]
    source = next((assets[s] for s in [*linked, *recent] if s in assets), None)
    if source is None:
        source = next(
            (a for a in assets.values() if a["kind"] == "image" and a["id"] not in identities), None
        )
    selected = [s["selection"]["asset_id"] for s in context.state.segments if s["selection"]]
    if source is None:
        source = next((assets[s] for s in selected if s in assets), None)
    if source is None:
        source = next((a for a in assets.values() if a["kind"] == "video"), None)
    if source is None:
        source = next((a for a in assets.values() if a["kind"] == "image"), None)
    if source is None:
        return " No scoped source asset is available; ask the user for an image or selected take."
    names = ", ".join(s["name"][:80] for s in context.state.cast[:4])
    empty = source["kind"] == "image" and known_empty_image(context, source["id"])
    return (
        " Reading notes say no people; use empty: true for a fitted plate, then user review."
        if empty
        else ""
    ) + example(
        {
            "cut_id": shot["id"],
            "plate": {
                "source_asset": source["id"],
                "angle_id": shot["plate_angle_id"]
                if any(a["id"] == shot["plate_angle_id"] for a in place["angles"])
                else place["angles"][0]["id"],
                **({"frame": 0} if source["kind"] == "video" else {}),
                **(
                    {"empty": True}
                    if empty
                    else {
                        "whom": names or "the person to remove",
                        "object_pronoun": "them",
                        "behind": f"the unobstructed background seen from {shot['plate_angle'][:120]}",
                    }
                ),
            },
        }
    )


def insertion_input(session, context, cut):
    segment, target, facing, _ = insertion_facts(session, cut)
    subjects = [direction.read_subject(session, sid) for sid in segment["content"]["subjects"][:4]]
    subject = next((s for s in subjects if s["identity_asset_id"]), None)
    if not subject:
        return identity_input(context, subjects, segment)
    return example(
        {
            "cut_id": cut["id"],
            "insertion": {
                "who": subject["name"][:120],
                "looks": subject["description"][:160] or subject["name"][:120],
                "pronouns": "he",
                **({"facing": facing} if facing else {}),
                "plate": target["description"][:240],
                "keep": scene_keep_suggestion(target["description"]),
                "size": "one third",
                "distance_description": "mid-distance",
                **scene_placement(target["description"], cut["crosses_barrier"]),
            },
        }
    )


def identity_input(context, subjects, segment):
    candidates = ", ".join(f"{s['name'][:80]} ({s['id']})" for s in subjects[:4]) or "none"
    if len(subjects) != 1:
        return (
            f" Cast on this segment: {candidates}. In Direct, choose one cast member for "
            f"segment {segment['id']} before insertion; the agent cannot change its cast membership."
        )
    if not available(context, "edit_cast"):
        return " " + revision_guidance(context)
    images = [a for a in context.state.assets if a["kind"] == "image"]
    if not images:
        return f" Cast: {candidates}. Ask the user for an identity image; no scoped image exists."
    subject = subjects[0]
    return (
        " Bind the intended identity with edit_cast; image candidates: "
        + ", ".join(f"{a['name'][:80]} ({a['id']})" for a in images[:3])
        + example(
            {
                "op": "set",
                "subject_id": subject["id"],
                "expected_revision": subject["revision"],
                "identity_asset_id": images[0]["id"],
            }
        )
    )
