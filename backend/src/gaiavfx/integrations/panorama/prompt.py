"""Compile panorama requests with the research's wording (`knowledge/wording.toml`)."""

import tomllib
from dataclasses import dataclass
from functools import cache
from importlib import resources

from gaiavfx.modules.panoramas.ask import DescribedPlace


@dataclass(frozen=True)
class Wording:
    trigger: str
    preamble: str
    viewpoint: str
    landmark: str
    tail: str
    outpaint: str
    outpaint_place: str
    seam: str


def parse_wording(text: str) -> Wording:
    data = tomllib.loads(text)
    if data.get("schema") != 1 or set(data) != {"schema", "description", "edit"}:
        raise ValueError("wording requires schema = 1, [description] and [edit]")
    description, edit = data["description"], data["edit"]
    if set(description) != {"trigger", "preamble", "viewpoint", "landmark", "tail"}:
        raise ValueError("[description] requires trigger, preamble, viewpoint, landmark and tail")
    if set(edit) != {"outpaint", "outpaint_place", "seam"}:
        raise ValueError("[edit] requires outpaint, outpaint_place and seam")
    values = [*description.values(), *edit.values()]
    if not all(isinstance(value, str) and value.strip() for value in values):
        raise ValueError("every wording is a non-empty string")
    return Wording(**description, **edit)


@cache
def wording() -> Wording:
    return parse_wording(
        resources.files(__package__).joinpath("knowledge/wording.toml").read_text("utf-8")
    )


def _sentence(text: str) -> str:
    text = " ".join(text.split())
    return text if text.endswith((".", "!", "?")) else text + "."


def compile_description(request: DescribedPlace, words: Wording | None = None) -> str:
    """Krea's prompt: the LoRA's trigger, the panorama layout, the place, its landmarks by heading and its light."""
    words = words or wording()
    parts = [words.preamble, _sentence(request.place)]
    if request.landmarks:
        named = ", ".join(
            words.landmark.format(what=" ".join(mark.what.split()), heading=mark.heading)
            for mark in sorted(request.landmarks, key=lambda mark: mark.heading)
        )
        parts.append(words.viewpoint.format(landmarks=named))
    if request.light.strip():
        parts.append(_sentence(request.light))
    parts.append(words.tail)
    return f"{words.trigger}, " + " ".join(parts)


def outpaint_prompt(words: Wording | None = None, place: str = "") -> str:
    """The fill instruction for a picture's canvas, ending with the place in words when the person gave any."""
    words = words or wording()
    place = " ".join(place.split())
    return words.outpaint + (words.outpaint_place.format(place=place) if place else "")


def seam_prompt(words: Wording | None = None) -> str:
    return (words or wording()).seam
