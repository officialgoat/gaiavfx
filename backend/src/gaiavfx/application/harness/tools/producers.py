"""The producers prepare_cut starts for a shot's views, one function each, chosen by the place's view producer
(application/prepare_references.view_producer): a plate and a survey for a place not built as a set, renders of the
set for one that is (ADR 0019 §3, §7)."""

from dataclasses import asdict
from typing import Literal

from pydantic import Field

from gaiavfx.application.harness.tools.authoring import place_problems
from gaiavfx.application.harness.tools.common import (
    available,
    revision_guidance,
    scoped,
)
from gaiavfx.application.harness.tools.input_hints import (
    example,
    known_empty_image,
    place_input,
    plate_input,
    removal_requested,
)
from gaiavfx.application.harness.tools.retries import require_revised
from gaiavfx.application.harness.world import direction
from gaiavfx.application.prepare_references import (
    start_empty_plate,
    start_removal,
    start_survey,
    survey_plan_for_place,
    view_producer,
)
from gaiavfx.application.set_views import start_set_views, views_request
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


class EmptyPlate(StrictModel):
    source_asset: str
    angle_id: str | None = None
    empty: Literal[True]


class RemovalPlate(StrictModel):
    source_asset: str
    angle_id: str | None = None
    frame: int | None = Field(default=None, ge=0, strict=True)
    whom: str = Field(
        min_length=1,
        max_length=1000,
        description="Who to remove in plain words, e.g. the man in the plaid shirt.",
    )
    object_pronoun: str = Field(min_length=1, max_length=100)
    behind: str = Field(
        min_length=1,
        max_length=1000,
        description="Background revealed behind the removed person; never a barrier or scene part to keep.",
    )


def start_views(session, context, value, shot, place_id, relevant, afford, seed, key):
    """The shot's views, made by its place's producer."""
    place = direction.read_place(session, place_id)
    if view_producer(place).plate:
        return survey_views(session, context, value, shot, place_id, relevant, afford, seed, key)
    return set_views(session, context, value, shot, place, relevant, afford, key)


def set_views(session, context, value, shot, place, relevant, afford, key):
    """A place built as a set: the shot's angle rendered from the set's accepted version with what it hides. No plate
    and no render budget (engine work); its camera is aimed in the set workbench."""
    if value.plate is not None:
        raise ApplicationError(
            f"plate_not_used: {place['name']} is built as a stage, so its views are rendered from the stage. "
            "Retry prepare_cut without plate.",
            422,
        )
    request = views_request(session, place_id=place["id"], angle_ids=[shot["angle_id"]])
    require_revised(session, context, relevant, shot, "set_views", request)
    afford(0)
    return start_set_views(
        session,
        context.library,
        place_id=place["id"],
        angle_ids=[shot["angle_id"]],
        request_key=key,
    )


def survey_views(session, context, value, shot, place_id, relevant, afford, seed, key):
    """A place not built as a set: a plate of the angle the shot's survey starts from (a removal, or an empty image
    fitted), then the survey."""
    place = direction.read_place(session, place_id)
    wrong_plate = bool(
        place["plate_asset_id"] and place["plate_angle_id"] != shot["plate_angle_id"]
    )
    if not place["plate_asset_id"] or wrong_plate:
        if value.plate is None:
            if wrong_plate:
                raise ApplicationError(
                    f"plate_angle_mismatch: The plate must show source angle "
                    f"{shot['plate_angle']} ({shot['plate_angle_id']}) of the cut's place. "
                    "Retry prepare_cut with plate to make a new plate from the same source "
                    "image: one removal job within the grant, then human review."
                    + plate_input(context, shot),
                    422,
                )
            raise ApplicationError(
                "plate_source_missing: Use an attached/imported place image as plate.source_asset "
                "and the cut's from_angle_id (or omit angle_id). Use empty: true if it is already empty. A video needs its delivered frame."
                + plate_input(context, shot),
                422,
            )
        source = scoped(
            [*context.attachments, *context.state.assets],
            value.plate.source_asset,
            "Plate source",
        )
        angle_id = value.plate.angle_id or shot["plate_angle_id"]
        require_revised(
            session,
            context,
            relevant,
            shot,
            "removal",
            {
                "source_asset": value.plate.source_asset,
                "frame": getattr(value.plate, "frame", None),
                "wording": value.plate.model_dump(include={"whom", "object_pronoun", "behind"}),
                "empty": True if isinstance(value.plate, EmptyPlate) else None,
            },
        )
        if (
            source["kind"] == "video"
            and isinstance(value.plate, RemovalPlate)
            and value.plate.frame is None
        ):
            raise ApplicationError(
                "plate_source_missing: A video source needs a delivered frame."
                + example(
                    {"cut_id": shot["id"], "plate": {**value.plate.model_dump(), "frame": 0}}
                ),
                422,
            )
        if isinstance(value.plate, EmptyPlate):
            if asked := removal_requested(context):
                raise ApplicationError(
                    f'The user asked for a removal ("{asked[:200]}"); use the removal plate with '
                    "whom (who to remove, in words), object_pronoun and behind (what is revealed).",
                    422,
                )
            if source["kind"] != "image":
                raise ApplicationError(
                    "empty: true needs an image without frame, whom, object_pronoun or behind; it fits the image for plate review.",
                    422,
                )
            afford(0)
            operation = start_empty_plate(
                session,
                place_id=place_id,
                angle_id=angle_id,
                source_asset=value.plate.source_asset,
                request_key=key,
            )
        else:
            if known_empty_image(context, source["id"]):
                raise ApplicationError(
                    "The source is known empty from reading or the user's words; use "
                    "plate={source_asset, empty: true} without removal wording.",
                    422,
                )
            from gaiavfx.application.harness.tools.plate_text import (
                require_removal_words,
                require_safe_removal,
            )

            require_removal_words(context, value.plate)
            require_safe_removal(session, context, shot, value.plate.behind)
            count = 1 if wrong_plate else 2
            afford(count)
            operation = start_removal(
                session,
                place_id=place_id,
                angle_id=angle_id,
                source_asset=value.plate.source_asset,
                frame=value.plate.frame,
                wording=value.plate.model_dump(include={"whom", "object_pronoun", "behind"}),
                seeds=[seed + i for i in range(count)],
                budget_limit=None,
                request_key=key,
            )
    else:
        problems = place_problems(session, context, place)
        if problems:
            raise ApplicationError(
                "place_text_missing: "
                + "; ".join(problems)
                + ". "
                + (
                    "Supply empty-place text with edit_place."
                    + example(place_input(session, context, place_id))
                    if available(context, "edit_place")
                    else revision_guidance(context)
                ),
                422,
            )
        survey = survey_plan_for_place(session, place_id, first_angle_id=shot["plate_angle_id"])
        require_revised(session, context, relevant, shot, "survey", {"plan": asdict(survey)})
        afford(1)
        operation = start_survey(
            session,
            place_id=place_id,
            plan=survey,
            seed=seed,
            budget_limit=None,
            request_key=key,
        )
    return operation
