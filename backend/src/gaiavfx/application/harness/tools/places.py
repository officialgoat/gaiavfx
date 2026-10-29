"""Atomic place authoring; places are reusable and have no movie receipt."""

from copy import deepcopy
from typing import Literal

from pydantic import Field

from gaiavfx.application.harness.actions import ToolResult, branch_by_op, inlined
from gaiavfx.application.harness.board import write_entry
from gaiavfx.application.harness.knowledge import load
from gaiavfx.application.harness.tools.authoring import (
    PLACE_TEXT,
    validate_empty_sound,
    validate_overhead,
)
from gaiavfx.application.harness.tools.common import TransactionTool, granted, revision, scoped
from gaiavfx.application.harness.tools.input_hints import example, place_input
from gaiavfx.application.harness.world import direction
from gaiavfx.modules.direction.place_text import validate_place_text
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


class AngleText(StrictModel):
    angle_id: str | None = None
    expected_revision: int | None = Field(default=None, ge=1)
    name: str | None = Field(default=None, min_length=1, max_length=120)
    description: str | None = Field(
        default=None, max_length=8000, description=load()["prompts"]["examples"]["empty_place"]
    )


class EditPlace(TransactionTool):
    name = "edit_place"
    description = "add_place: location; add_angle: camera at place_id; set: revised text. Empty place only; cast/people belong in pins. No undo."

    class Input(StrictModel):
        op: Literal["add_place", "add_angle", "set"]
        place_id: str | None = None
        expected_revision: int | None = Field(default=None, ge=1)
        name: str | None = Field(default=None, min_length=1, max_length=120)
        description: str | None = Field(default=None, max_length=8000)
        sound: str | None = Field(
            default=None,
            max_length=8000,
            description="Empty-place ambience; no footsteps, voices, breathing or fabric.",
        )
        overhead: str | None = Field(
            default=None, max_length=8000, description=PLACE_TEXT["overhead"]
        )
        angles: list[AngleText] = Field(default_factory=list, max_length=24)

    def narrow(self, context):
        if not granted(context, "edit"):
            return None
        schema = inlined(self.Input.model_json_schema())
        specs = {
            "add_place": {
                "required": ["name", "description", "sound", "overhead", "angles"],
                "drop": ["place_id", "expected_revision"],
            }
        }
        if context.state.places:
            specs["set"] = {"required": ["place_id", "expected_revision"], "drop": ["name"]}
            specs["add_angle"] = {
                "required": ["place_id", "name", "description"],
                "drop": ["expected_revision", "sound", "overhead", "angles"],
            }
        schema = branch_by_op(schema, specs)
        for branch in schema.get("oneOf", [schema]):
            op = branch["properties"]["op"]["const"]
            if op == "add_angle":
                branch["properties"]["place_id"] = {"enum": [p["id"] for p in context.state.places]}
                continue
            adding = op == "add_place"
            angles = deepcopy(branch["properties"]["angles"])
            branch["properties"]["angles"] = angles
            angles["maxItems"] = 6 if adding else 24
            angles["minItems"] = 1 if adding else 0
            props = angles["items"]["properties"]
            allowed = (
                {"name", "description"}
                if adding
                else {"angle_id", "expected_revision", "description"}
            )
            angles["items"]["properties"] = {k: v for k, v in props.items() if k in allowed}
            angles["items"]["required"] = ["name", "description"] if adding else sorted(allowed)
            if not adding:
                branch["properties"]["place_id"] = {"enum": [p["id"] for p in context.state.places]}
                ids = [a["id"] for p in context.state.places for a in p["angles"]]
                if ids:
                    angles["items"]["properties"]["angle_id"] = {"enum": ids}
                else:
                    branch["properties"].pop("angles")
        return schema

    def execute(self, session, context, value):
        # Read the full cast, not the bounded view (which can omit names).
        names = [s["name"] for s in direction.read_movie(session, context.movie_id)["subjects"]]
        texts = [value.name, value.description, value.sound, value.overhead]
        texts += [text for angle in value.angles for text in (angle.name, angle.description)]
        try:
            if value.sound is not None:
                validate_empty_sound(value.sound, names)
            if value.overhead is not None:
                validate_overhead(value.overhead, names)
            for text in texts:
                if text is not None:
                    validate_place_text(text, names)
        except ValueError as exc:
            raise ApplicationError(str(exc), 422) from exc
        if value.op == "add_place":
            return self.add(session, context, value)
        if value.op == "add_angle":
            scoped(context.state.places, value.place_id, "Place")
            if not value.name or value.description is None:
                raise ApplicationError(
                    "add_angle needs name and description for the new view.", 422
                )
            angle = direction.add_angle(session, value.place_id, value.name)
            angle = direction.update_angle(
                session, angle["id"], angle["revision"], description=value.description
            )
            return ToolResult(payload={"angle": angle, "revision": angle["revision"]}, changed=True)
        if not value.place_id or value.expected_revision is None:
            raise ApplicationError(
                "place_id and expected_revision are required."
                + example(place_input(session, context, value.place_id)),
                422,
            )
        scoped(context.state.places, value.place_id, "Place")
        before = direction.read_place(session, value.place_id)
        revision(before, value.expected_revision, "Place", self.name)
        changes = value.model_dump(
            exclude_none=True, exclude={"op", "name", "place_id", "expected_revision", "angles"}
        )
        if not changes and not value.angles:
            raise ApplicationError(
                "Specify at least one place or angle field."
                + example(place_input(session, context, value.place_id)),
                422,
            )
        # Build recovery hints before any writes: revisions from a rolled-back
        # partial update would make the suggested retry stale immediately.
        for angle in value.angles:
            if not angle.angle_id or angle.expected_revision is None or angle.description is None:
                raise ApplicationError(
                    "Each angle needs angle_id, expected_revision and description."
                    + example(place_input(session, context, value.place_id, angle.angle_id)),
                    422,
                )
        after = (
            direction.update_place(session, value.place_id, value.expected_revision, **changes)
            if changes
            else before
        )
        edits = []
        for angle in value.angles:
            old = direction.read_angle(session, angle.angle_id)
            if old["place_id"] != value.place_id:
                raise ApplicationError("The angle belongs to another place.", 422)
            revision(old, angle.expected_revision, f"Angle {old['name']}", self.name)
            new = direction.update_angle(
                session, angle.angle_id, angle.expected_revision, description=angle.description
            )
            edits.append({"before": old, "after": new})
        # An angle's words are not its camera in the place's stage (the stage eval's model rewrote the words to
        # "aim" a camera and called it done).
        stage = after.get("set_id")
        return ToolResult(
            payload={
                "before": before,
                "after": after,
                "angles": edits,
                "revision": after["revision"],
                **(
                    {
                        "stage": f"Only the angles' words changed; their cameras in stage {stage} did not: "
                        "save_set_view with an angle_id aims one."
                    }
                    if stage and edits
                    else {}
                ),
            },
            changed=True,
        )

    def add(self, session, context, value):
        missing = [
            f"{field}: {meaning}"
            for field, meaning in PLACE_TEXT.items()
            if not (getattr(value, field) or "").strip()
        ]
        missing += [
            f"angle {a.name or '?'} description: the empty camera view"
            for a in value.angles
            if not (a.description or "").strip()
        ]
        if missing:
            raise ApplicationError("Author all place text at creation: " + "; ".join(missing), 422)
        if (
            not value.name
            or not 1 <= len(value.angles) <= 6
            or any(not a.name for a in value.angles)
        ):
            raise ApplicationError(
                "A place needs a name and 1 to 6 named angles."
                + example(
                    {
                        "op": "add_place",
                        "name": value.name or "New place",
                        "angles": [{"name": "Opening view"}],
                    }
                ),
                422,
            )
        place = direction.create_place(session, value.name)
        changes = value.model_dump(exclude_none=True, include={"description", "sound", "overhead"})
        if changes:
            place = direction.update_place(session, place["id"], place["revision"], **changes)
        angles = []
        for draft in value.angles:
            angle = direction.add_angle(session, place["id"], draft.name)
            if draft.description is not None:
                angle = direction.update_angle(
                    session, angle["id"], angle["revision"], description=draft.description
                )
            angles.append(angle)
        note = (
            f"created place {place['name']} {place['id']} with angles "
            + ", ".join(f"{a['name']} {a['id']}" for a in angles)
            + " (no receipt; delete in the Place panel)"
        )
        write_entry(context.board, "decisions", note, context.turn)
        return ToolResult(
            payload={"place": place, "angles": angles, "note": note, "revision": place["revision"]},
            changed=True,
        )
