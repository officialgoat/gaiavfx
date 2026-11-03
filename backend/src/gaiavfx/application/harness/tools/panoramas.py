"""The agent's panorama tools: two panoramas of a place in a plan's prepare step (from words or from a picture), and a
stage built from the one the user chose in a build step. Choosing stays the user's, in Library > Stages: Gaia never
chooses, and builds only from a chosen panorama."""

import logging
import re

from pydantic import Field

from gaiavfx.application import panoramas, set_panorama
from gaiavfx.application.harness import budget
from gaiavfx.application.harness.actions import ToolResult, pin_enum
from gaiavfx.application.harness.tools.common import (
    TransactionTool,
    current_step,
    operation_receipt,
    permitted,
    scoped,
)
from gaiavfx.application.harness.tools.plan_scope import producer_kinds
from gaiavfx.application.harness.world import direction
from gaiavfx.application.set_fill import FILL_TIME
from gaiavfx.application.set_seed import fill_renders
from gaiavfx.modules.panoramas.ask import MAX_LANDMARKS, MAX_PLACE, MAX_WHAT, PicturedPlace
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


def requested(message):
    return bool(re.search(r"\b(?:panoramas?|360|surround|equirect\w*)\b", message, re.IGNORECASE))


def _row(state: dict) -> dict:
    """A panorama attempt as the agent reads it: never a seed, never a letter's provenance."""
    return {
        "id": state["id"],
        "place_id": state["place_id"],
        "place": state["place"],
        "mode": state["mode"],
        "state": state["state"],
        "message": state["message"],
        "candidates": [
            {"letter": c["letter"], "candidate_id": c["candidate_id"], "chosen": c["chosen"]}
            for c in state["candidates"]
        ],
        "chosen": state["chosen"],
        "build": {k: state["build"][k] for k in ("state", "label", "operation_id")}
        if state["build"]
        else None,
    }


def refresh_panoramas(session, state, library, turns, message):
    """Panoramas enter the agent's context when a scoped place has some, when the user talks about panoramas, or
    when an earlier turn made one."""
    if library is None:
        return
    retained = any(
        r.get("operation_kind") == panoramas.NAME
        for t in turns
        if t["state"] == "accepted"
        for r in t["receipts"]
    )
    places = [p["id"] for p in state.places]
    try:
        rows = [
            row
            for place_id in places
            for row in panoramas.panoramas_for(session, library, place_id, 4)
        ]
        if retained or requested(message):
            seen = {row["id"] for row in rows}
            rows += [
                row
                for row in panoramas.panoramas_for(session, library, None, 6)
                if row["id"] not in seen
            ]
        state.panoramas = [_row(row) for row in rows][:8]
    except Exception:  # panorama context must never end the agent session
        logging.getLogger(__name__).exception("Panorama context unavailable")


def rows(context):
    return getattr(context.state, "panoramas", [])


class LandmarkInput(StrictModel):
    what: str = Field(min_length=1, max_length=MAX_WHAT)
    heading: int = Field(
        ge=0, le=359, description="Degrees: 0 ahead, 90 right, 180 behind, 270 left."
    )


def film_words(place: dict | None) -> str:
    """The place's own description in the film, cut at a sentence end to the length a panorama request takes."""
    words = (place or {}).get("description", "").strip()
    if len(words) <= MAX_PLACE:
        return words
    cut = words[:MAX_PLACE]
    return cut[: cut.rfind(". ") + 1] or cut


class MakePanorama(TransactionTool):
    name = "make_panorama"
    target = "place_id"
    step_kind = "prepare"
    overflow = 1
    description = (
        "Draw two 360° panoramas of a place for the user to choose from in Library > Stages, inside a prepare step "
        f"titled for panoramas ({panoramas.RENDERS} jobs; one call draws both). From words: description is the place "
        "as someone standing in it sees it, with up to eight landmarks by heading and the light; never describe the "
        "image format, Python writes the model's prompt. From a picture: picture_id grows that still into a "
        "panorama that keeps it, and from_plate grows the place's approved plate (its scene with the people "
        "removed; plan the plate step first when it has none). Around the picture Fibo continues the place's words "
        "but invents what it cannot see, so say so. The user chooses one; never choose for them."
    )

    class Input(StrictModel):
        place_id: str | None = None
        description: str = Field(
            default="",
            max_length=MAX_PLACE,
            description="The place in words, as someone standing in it sees it; left out, the place's own "
            "description in the film.",
        )
        landmarks: list[LandmarkInput] = Field(default_factory=list, max_length=MAX_LANDMARKS)
        light: str = Field(default="", max_length=2 * MAX_WHAT, description="The light, in words.")
        picture_id: str | None = Field(
            default=None, description="A still to grow into the panoramas instead of words."
        )
        from_plate: bool = Field(
            default=False,
            description="Grow the place's approved plate (its scene with the people removed) instead of a picture.",
        )
        # Unbounded here: the live eval's model sent 360 for a panorama from words, which the schema then refused.
        hfov: float | None = Field(
            default=None,
            description="With picture_id only: the still's horizontal field of view, 30 to 120 degrees; left "
            "out, it is measured.",
        )

    def narrow(self, context):
        step = current_step(context)
        if not step or step.kind != "prepare" or "panorama" not in producer_kinds(step.title):
            return None
        schema = self.Input.model_json_schema()
        ids = [p["id"] for p in context.state.places]
        if step.target in ids:
            ids = [step.target]
        return pin_enum(schema, "place_id", [*ids, None]) if ids else schema

    def link(self, plan, result, value):
        return result.payload.get("operation_id", "")

    def execute(self, session, context, value):
        if not permitted(context, "prepare"):
            raise ApplicationError("Preparation autonomy is off.", 422)
        # The live eval's model never named the place; its step's target (Python fills the only place) is the one.
        step = current_step(context)
        places = {p["id"] for p in context.state.places}
        place_id = value.place_id or (step.target if step and step.target in places else None)
        place = None
        if place_id:
            scoped(context.state.places, place_id, "Place")
            place = direction.read_place(session, place_id)
        budget.afford_step(context, panoramas.RENDERS)
        # The live eval's model sent landmarks and light under a place_id but never the words: the film's own
        # description of the place stands in for them.
        words = value.description.strip() or film_words(place)
        picture_id = value.picture_id
        if value.from_plate:
            row = next((p for p in context.state.places if p["id"] == place_id), None)
            picture_id = ((row or {}).get("plate") or {}).get("asset_id")
            if not picture_id:
                raise ApplicationError(
                    f"{row['name'] if row else 'This place'} has no approved plate yet: plan a prepare step that "
                    "makes one from the scene with the people removed; the user approves it.",
                    409,
                )
        if picture_id:
            if value.hfov is not None and not 30 <= value.hfov <= 120:
                raise ApplicationError(
                    "hfov is the still's own field of view, 30 to 120 degrees; leave it out to measure it.",
                    422,
                )
            name = words or (place["name"] if place else "")
            request = {"pictured": PicturedPlace(picture_id, value.hfov, name)}
        elif words:
            landmarks = [item.model_dump() for item in value.landmarks]
            request = {"described_place": panoramas.described(words, landmarks, value.light)}
        else:
            raise ApplicationError(
                "Write the place in description, as someone standing in it sees it (landmarks and light are "
                "optional), or give picture_id to grow a still.",
                422,
            )
        operation = panoramas.start_panorama(session, context.library, place_id=place_id, **request)
        words = (
            "growing the picture into two panoramas that keep it; Fibo continues the place around it but "
            "invents what it cannot see"
            if picture_id
            else "drawing two panoramas from the description"
        )
        return ToolResult(
            payload={
                "operation_id": operation["id"],
                "review": True,
                "message": f"Panoramas {operation['id']}: {words}. They run in Jobs; the user chooses one in "
                "Library > Stages, and a build step can then make a stage from it.",
                # The chat card's words (round 1, T5-F3: the message's id and third person reached the user).
                "notice": f"{words[0].upper()}{words[1:]}. Choose one in Library › Stages when they are ready.",
            },
            receipts=[operation_receipt(operation)],
            waits_on=operation["id"],
            changed=True,
        )


class BuildStageFromPanorama(TransactionTool):
    name = "build_stage_from_panorama"
    target = "candidate_id"
    step_kind = "build"
    overflow = 1
    description = (
        "Build a new stage from the panorama the user chose, as the user's Build the stage does. With fill_unseen "
        f"(on by default) H3 fills what the panorama never saw: {FILL_TIME} and up to {fill_renders()} renders, which "
        "its build step reserves. Only a chosen panorama builds; the user decides its v1."
    )

    class Input(StrictModel):
        candidate_id: str
        name: str | None = Field(default=None, max_length=120)
        fill_unseen: bool = True

    def narrow(self, context):
        ids = [
            p["chosen"]
            for p in rows(context)
            if p["chosen"] and not (p["build"] and p["build"]["state"] in ("building", "done"))
        ]
        step = current_step(context)
        if step and step.kind == "build" and step.target:
            targeted = self.targeted(context, step)
            ids = [i for i in ids if i in targeted] or ids
        return pin_enum(self.Input.model_json_schema(), "candidate_id", ids) if ids else None

    def step_targets(self, context):
        # Until the user chooses, the step names nothing to build from: the call is refused for that, in words.
        return super().step_targets(context) or None

    def targeted(self, context, step):
        """A build step names the place (or the attempt) whose chosen panorama it builds from."""
        return [
            p["chosen"]
            for p in rows(context)
            if p["chosen"] and step.target in (p["place_id"], p["id"], p["chosen"])
        ]

    def execute(self, session, context, value):
        row = next((p for p in rows(context) if p["chosen"] == value.candidate_id), None)
        if row is None:
            raise ApplicationError(
                "Only the panorama the user chose builds a stage; ask the user to choose one in Library > Stages.",
                409,
            )
        budget.afford_step(context, fill_renders() if value.fill_unseen else 0)
        place = next((p for p in context.state.places if p["id"] == row["place_id"]), None)
        operation = set_panorama.start_panorama_stage(
            session,
            context.library,
            candidate_id=value.candidate_id,
            name=value.name,
            fill_unseen=value.fill_unseen,
            use_for_place=bool(place and not place.get("set_id")),
        )
        estimate = set_panorama.estimate(value.fill_unseen)
        return ToolResult(
            payload={
                "operation_id": operation["id"],
                "message": f"Building {operation['request']['name']} from the chosen panorama. {estimate}",
            },
            receipts=[operation_receipt(operation)],
            waits_on=operation["id"],
            changed=True,
        )
