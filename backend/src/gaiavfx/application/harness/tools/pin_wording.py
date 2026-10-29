"""Keep insertion placement prose short and proportionate to the reviewed scene."""

import re

from gaiavfx.application.harness.tools.authoring import MOTION


def height_fraction(text):
    """Read explicit numeric/word fractions; unknown quantities remain unknown."""
    text = text.casefold().replace("-", " ")
    values = []
    for a, b in re.findall(r"(\d+(?:\.\d+)?)\s*/\s*(\d+(?:\.\d+)?)", text):
        if float(b):
            values.append(float(a) / float(b))
    values.extend(float(n) / 100 for n in re.findall(r"(\d+(?:\.\d+)?)\s*(?:%|percent\b)", text))
    values.extend(float(n) for n in re.findall(r"\b(0\.\d+)\b", text))
    numerators = {
        "a": 1,
        "an": 1,
        "one": 1,
        "two": 2,
        "three": 3,
        "four": 4,
        "five": 5,
        "six": 6,
        "seven": 7,
        "eight": 8,
        "nine": 9,
    }
    denominators = {
        "half": 2,
        "halve": 2,
        "third": 3,
        "quarter": 4,
        "fifth": 5,
        "sixth": 6,
        "seventh": 7,
        "eighth": 8,
        "ninth": 9,
        "tenth": 10,
    }
    for numerator, denominator in re.findall(
        r"\b(a|an|one|two|three|four|five|six|seven|eight|nine|\d+)\s+"
        r"(half|halves|thirds?|quarters?|fifths?|sixths?|sevenths?|eighths?|ninths?|tenths?)\b",
        text,
    ):
        values.append(
            (float(numerator) if numerator.isdigit() else numerators[numerator])
            / denominators[denominator.rstrip("s")]
        )
    if not values and re.search(r"\bhalf\b", text):
        values.append(0.5)
    return max(values) if values else None


def placement_problems(wording, cut, plan, *, moving=False):
    errors = {}
    where = wording.get("where", "")
    if (
        len(where.split()) > 20
        or MOTION.search(where)
        or re.search(
            r"\b(?:sway(?:s|ing)?|mid[ -]stride|stepp?ing|turn(?:s|ing)?|head(?:s|ing)?|"
            r"approach(?:es|ing)?|depart(?:s|ing)?|travel(?:s|ling|ing)?)\b",
            where,
            re.IGNORECASE,
        )
    ):
        errors["where"] = (
            "use a short position phrase without motion verbs, e.g. 'on the open paving "
            "beyond the railing'; the template adds the walk"
        )
    beyond = re.search(r"\b(?:beyond|behind|far side|other side)\b", where, re.IGNORECASE)
    if wording.get("barrier") and beyond and wording.get("size"):
        fraction = height_fraction(wording["size"])
        over_half = re.search(
            r"\b(?:more than|greater than|over|above)\s+(?:one\s+|a\s+)?half\b",
            wording["size"],
            re.IGNORECASE,
        )
        if fraction is None or fraction > 0.5 or over_half:
            errors["size"] = (
                "beyond a barrier in a wide view, use one fifth to one third of image height; "
                "size must be an explicit fraction no greater than one half"
            )
    if cut.get("relation") == "reverse" and cut.get("crosses_barrier"):
        limitations = (plan.get("recommended") or {}).get("limitations", [])
        limitation = next((item for item in limitations if "8 of 8" in item), "")
        if moving:
            for field in ("where", "distance_description"):
                if re.search(
                    r"\b(?:stand(?:s|ing)?|paus(?:e[sd]?|ing)|wait(?:s|ing)?|"
                    r"at (?:the )?railing|arm['’]?s? length|next to|against|a short step|"
                    r"close to|fac(?:e[sd]?|ing))\b",
                    wording.get(field, ""),
                    re.IGNORECASE,
                ):
                    errors[field] = (
                        "for a moving subject across a barrier reverse, use where "
                        "'on the open paving beyond the railing', distance_description "
                        "'distant, small'; no static pose, barrier closeness or facing words. "
                        f'Cut plan limitation: "{limitation}"'
                    )
        distance = " ".join(wording.get(k, "") for k in ("where", "distance_description"))
        if not moving and re.search(r"\b(?:a short step|close to)\b", distance, re.IGNORECASE):
            errors["distance_description"] = (
                "use the distance from the camera, e.g. 'distant, small'. Cut plan limitation: "
                + limitation
            )
    return errors
