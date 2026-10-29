"""Compile authored insertion and removal requests with research wording."""

import tomllib
from dataclasses import dataclass
from functools import cache
from importlib import resources

from gaiavfx.modules.direction.insertion import Insertion, Removal
from gaiavfx.modules.direction.portrait import Portrait


@dataclass(frozen=True)
class Wording:
    lateral: str
    front: str
    remove: str
    portrait: dict[str, str]


def parse_wording(text: str) -> Wording:
    data = tomllib.loads(text)
    if data.get("schema") != 1 or set(data) != {"schema", "templates", "portrait"}:
        raise ValueError("wording requires schema = 1, [templates] and [portrait]")
    templates = data["templates"]
    if set(templates) != {"lateral", "front", "remove"} or not all(
        isinstance(value, str) and value for value in templates.values()
    ):
        raise ValueError("[templates] requires lateral, front and remove")
    portrait = data["portrait"]
    if set(portrait) != {"base", "front", "three_quarter", "profile"} or not all(
        isinstance(value, str) and value for value in portrait.values()
    ):
        raise ValueError("[portrait] requires base, front, three_quarter and profile")
    return Wording(**templates, portrait=portrait)


def compile_portrait(request: Portrait) -> str:
    templates = wording().portrait
    return templates["base"].format(view=templates[request.view])


@cache
def wording() -> Wording:
    return parse_wording(
        resources.files(__package__).joinpath("knowledge/wording.toml").read_text("utf-8")
    )


def compile_insertion(request: Insertion) -> str:
    pos = "his" if request.pronouns == "he" else "her"
    sub = "He" if request.pronouns == "he" else "She"
    common = {
        "plate": request.plate,
        "keep": request.keep,
        "who": request.who,
        "pos": pos,
        "looks": request.looks,
        "where": request.where,
        "size": request.size,
        "Sub": sub,
    }
    if request.facing == "toward_camera":
        return wording().front.format(**common, behind=request.behind)
    direction = "left" if request.facing == "screen_left" else "right"
    return wording().lateral.format(
        **common,
        direction=direction,
        starting_half="right" if direction == "left" else "left",
        edge=direction.upper(),
        Pos=pos.capitalize(),
        distance_description=request.distance_description,
        along=request.along,
        barrier=request.barrier,
        feet_placement=request.feet_placement,
        occlude=request.occlude,
    )


def compile_removal(request: Removal) -> str:
    return wording().remove.format(
        whom=request.whom, obj=request.object_pronoun, behind=request.behind
    )
