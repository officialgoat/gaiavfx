"""Typed revision-checked edits through Direction's public commands."""

from typing import Literal

from pydantic import Field

from gaiavfx.application.harness.actions import (
    CROSSES_BARRIER_GUIDANCE,
    ToolResult,
    branch_by_op,
    inlined,
    pin_enum,
)
from gaiavfx.application.harness.tools.authoring import (
    MOTION,
    phase_direction_schema,
    require_assemblable,
    require_different_angles,
    require_motion_direction,
)
from gaiavfx.application.harness.tools.common import (
    TransactionTool,
    direction_receipt,
    edited,
    granted,
    revision,
    scoped,
)
from gaiavfx.application.harness.tools.cut_angles import cut_result, resolve_angles
from gaiavfx.application.harness.tools.cut_continuity import (
    require_cut_timing,
    require_segment_continuity,
)
from gaiavfx.application.harness.tools.insertion import InsertionInput, resolve_insertion
from gaiavfx.application.harness.tools.places import (
    EditPlace,  # noqa: F401 - public tool import retained
)
from gaiavfx.application.harness.world import direction
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


class PhaseText(StrictModel):
    description: str = Field(max_length=8000)
    screen_direction: str = Field(
        default="",
        max_length=8000,
        description="Motion: left to right; empty if stationary.",
    )
    sound: str = Field(default="", max_length=8000)


class PhaseDraft(PhaseText):
    start_frame: int = Field(ge=0)


class PlaceText(StrictModel):
    noun: str
    description: str = Field(max_length=8000)
    landmarks: str = Field(default="", max_length=8000)


def branches(model, specs):
    """Keep only each operation's fields, omitting unavailable operations entirely."""
    schema = inlined(model.model_json_schema())
    return branch_by_op(
        schema,
        {
            op: {"required": required, "drop": set(schema["properties"]) - {"op", *allowed}}
            for op, (required, allowed) in specs.items()
        },
    )


def require(value, *names):
    for name in names:
        if getattr(value, name) is None:
            raise ApplicationError(f"{name} is required.", 422)


def pin_existing(schema, name, values):
    if "oneOf" in schema or name in schema.get("properties", {}):
        return pin_enum(schema, name, values)
    return schema


def segment_in_scope(session, context, segment_id):
    scoped(context.state.segments, segment_id, "Segment")
    return direction.read_segment(session, segment_id)


class EditCut(TransactionTool):
    name = "edit_cut"
    description = "Cut create/remove: segment revision. Move(frame)/angles/relation/insertion: cut revision. Move carries its phase."

    class Input(StrictModel):
        op: Literal["relation", "insertion", "create", "angles", "move", "remove"]
        cut_id: str | None = None
        expected_revision: int = Field(ge=1)
        relation: Literal["same_direction", "side", "reverse"] | None = None
        crosses_barrier: bool | None = None
        insertion: InsertionInput | None = None
        segment_id: str | None = None
        frame: int | None = Field(default=None, gt=0)
        from_angle_id: str | None = None
        to_angle_id: str | None = None
        phase: PhaseText | None = None

    def narrow(self, context):
        from gaiavfx.application.harness.tools.preparation_schema import cut_insertion_branches

        if not granted(context, "edit"):
            return None
        ids = [cut["id"] for cut in context.state.cuts]
        segments = [s["id"] for s in context.state.segments]
        angles = [a["id"] for p in context.state.places for a in p["angles"]]
        specs = {}
        if ids:
            for op, extra in (
                ("relation", ["relation", "crosses_barrier"]),
                ("insertion", ["insertion"]),
                ("angles", ["from_angle_id", "to_angle_id"]),
                ("move", ["frame"]),
                ("remove", []),
            ):
                names = ["cut_id", "expected_revision", *extra]
                specs[op] = (names, names)
        if segments and angles:
            required = [
                "segment_id",
                "expected_revision",
                "frame",
                "from_angle_id",
                "to_angle_id",
                "phase",
                "relation",
                "crosses_barrier",
            ]
            specs["create"] = (required, required)
        if not specs:
            return None
        schema = branches(self.Input, specs)
        for branch in schema.get("oneOf", [schema]):
            if branch["properties"]["op"]["const"] in {"create", "relation"}:
                for name in ("relation", "crosses_barrier"):
                    branch["properties"][name] = branch["properties"][name]["anyOf"][0]
        for name, values in (
            ("cut_id", ids),
            ("segment_id", segments),
        ):
            if values:
                schema = pin_existing(schema, name, values)
        return cut_insertion_branches(schema, context)

    def execute(self, session, context, value):
        if value.op == "create":
            require(value, "segment_id", "frame", "from_angle_id", "to_angle_id", "phase")
            segment = segment_in_scope(session, context, value.segment_id)
            previous_segment = segment
            source_id, target_id = resolve_angles(context, value.from_angle_id, value.to_angle_id)
            existing = next((c for c in segment["cuts"] if c["frame"] == value.frame), None)
            if existing:
                op = (
                    "angles"
                    if (source_id, target_id)
                    != (existing["from_angle_id"], existing["to_angle_id"])
                    else "relation"
                )
                raise ApplicationError(
                    f"A cut already exists at f{value.frame} (id {existing['id']}); use "
                    f"op={op} or op=move with expected_revision {existing['revision']}; "
                    f"op=remove uses segment expected_revision {segment['revision']}",
                    409,
                )
            revision(segment, value.expected_revision, "Segment", self.name)
            if value.relation is not None and value.crosses_barrier is None:
                raise ApplicationError(
                    "A known relation requires crosses_barrier; " + CROSSES_BARRIER_GUIDANCE, 422
                )
            require_cut_timing(segment, value.frame, segment["cuts"])
            receipts = []
            if not any(p["start_frame"] == value.frame for p in segment["phases"]):
                require_motion_direction(value.phase.model_dump())
                phase = direction.add_phase(
                    session,
                    value.segment_id,
                    value.expected_revision,
                    start_frame=value.frame,
                    content=value.phase.model_dump(),
                )
                receipts.append(direction_receipt(phase))
                segment = direction.read_segment(session, value.segment_id)
            result = direction.create_cut(
                session,
                value.segment_id,
                segment["revision"],
                frame=value.frame,
                from_angle_id=source_id,
                to_angle_id=target_id,
                relation=value.relation,
                crosses_barrier=value.crosses_barrier,
                relation_source="authored" if value.relation else "unknown",
                receives_predecessor=True,
            )
            receipts.append(direction_receipt(result))
            require_assemblable(session, value.segment_id)
            require_segment_continuity(
                session,
                value.segment_id,
                context.latest_user_message,
                check_timing=True,
                previous_segment=previous_segment,
            )
            response = edited(context, result, structural=True, receipts=receipts)
            response.payload.update(cut_result(session, result))
            return response
        scoped(context.state.cuts, value.cut_id, "Cut")
        cut = direction.read_cut(session, value.cut_id)
        if value.op == "remove":
            segment = direction.read_segment(session, cut["segment_id"])
            revision(segment, value.expected_revision, "Segment", self.name)
            result = direction.remove_cut(session, value.cut_id, value.expected_revision)
            return edited(context, result, structural=True)
        revision(cut, value.expected_revision, "Cut", self.name)
        previous_segment = direction.read_segment(session, cut["segment_id"])
        if value.op != "angles":
            require_different_angles(cut["from_angle_id"], cut["to_angle_id"])
        if value.op == "move":
            require(value, "frame")
            segment = direction.read_segment(session, cut["segment_id"])
            require_cut_timing(segment, value.frame, segment["cuts"], cut["id"])
            changes = {"frame": value.frame}
        elif value.op == "angles":
            require(value, "from_angle_id", "to_angle_id")
            place_id = direction.read_angle(session, cut["from_angle_id"])["place_id"]
            source_id, target_id = resolve_angles(
                context, value.from_angle_id, value.to_angle_id, place_id=place_id
            )
            changes = {"from_angle_id": source_id, "to_angle_id": target_id}
        elif value.op == "relation":
            if value.relation is None or value.crosses_barrier is None:
                raise ApplicationError(
                    "relation and crosses_barrier are required; " + CROSSES_BARRIER_GUIDANCE, 422
                )
            changes = {
                "relation": value.relation,
                "crosses_barrier": value.crosses_barrier,
                "relation_source": "authored",
            }
        else:
            if value.insertion is None:
                raise ApplicationError("insertion is required.", 422)
            changes = {
                "insertion": resolve_insertion(
                    session, context, cut, value.insertion.model_dump(exclude_none=True)
                )
            }
        result = direction.update_cut(session, value.cut_id, value.expected_revision, **changes)
        require_segment_continuity(
            session,
            cut["segment_id"],
            context.latest_user_message,
            check_timing=value.op == "move",
            previous_segment=previous_segment,
        )
        payload = cut_result(session, result)
        if value.op == "angles" and result["relation"] is None:
            payload["message"] += (
                "; changing an angle clears the relation. Declare it again with op=relation."
            )
        elif value.op == "relation":
            payload["message"] += ". Declared. Offer the next step; do not prepare unless asked."
        return ToolResult(
            payload=payload,
            receipts=[direction_receipt(result)],
            changed=True,
        )


class EditSegment(TransactionTool):
    name = "edit_segment"
    description = "Add/move/remove: movie revision. Set: segment revision. Retains receipts."

    class Input(StrictModel):
        op: Literal["add", "set", "move", "remove"]
        expected_revision: int = Field(ge=1)
        segment_id: str | None = None
        subject_ids: list[str] | None = Field(default=None, min_length=1, max_length=24)
        place: PlaceText | None = None
        style: str | None = Field(default=None, max_length=8000)
        delivered_frames: int | None = Field(default=None, gt=0)
        continues: bool | None = None
        phases: list[PhaseDraft] | None = Field(default=None, min_length=1, max_length=24)
        at: int | None = Field(default=None, ge=0)
        ordinal: int | None = Field(default=None, ge=0)

    def narrow(self, context):
        if not granted(context, "edit"):
            return None
        specs = {}
        if context.state.cast:
            required = [
                "expected_revision",
                "subject_ids",
                "place",
                "delivered_frames",
                "continues",
                "phases",
            ]
            specs["add"] = (required, [*required, "style", "at"])
        ids = [s["id"] for s in context.state.segments]
        if ids:
            common = ["segment_id", "expected_revision"]
            specs |= {
                "set": (common, [*common, "place", "style", "delivered_frames", "continues"]),
                "move": ([*common, "ordinal"], [*common, "ordinal"]),
                "remove": (common, common),
            }
        if not specs:
            return None
        schema = inlined(branches(self.Input, specs))
        for branch in schema.get("oneOf", [schema]):
            if "phases" in branch["properties"]:
                phase = branch["properties"]["phases"]["anyOf"][0]["items"]
                phase.setdefault("required", []).append("screen_direction")
                if MOTION.search(context.latest_user_message):
                    phase["properties"]["screen_direction"] = {"type": "string", "minLength": 1}
            if "subject_ids" in branch["properties"]:
                branch["properties"]["subject_ids"] = {
                    "type": "array",
                    "minItems": 1,
                    "maxItems": 24,
                    "items": {"enum": [s["id"] for s in context.state.cast]},
                }
        return pin_existing(schema, "segment_id", ids) if ids else schema

    def execute(self, session, context, value):
        if value.op == "add":
            require(value, "subject_ids", "place", "delivered_frames", "continues", "phases")
            if not context.state.cast:
                raise ApplicationError("Add a cast member first.", 422)
            for subject in value.subject_ids:
                scoped(context.state.cast, subject, "Subject")
            revision(
                direction.read_movie(session, context.movie_id),
                value.expected_revision,
                "Movie",
                self.name,
            )
            if value.phases[0].start_frame != 0:
                raise ApplicationError("The first phase must start at 0.", 422)
            content = {"subjects": value.subject_ids, "place": value.place.model_dump()}
            if value.style is not None:
                content["style"] = value.style
            row = direction.add_segment(
                session,
                context.movie_id,
                value.expected_revision,
                content=content,
                delivered_frames=value.delivered_frames,
                continues=value.continues,
                phases=[
                    (p.start_frame, p.model_dump(exclude={"start_frame"})) for p in value.phases
                ],
                at=value.at,
            )
        else:
            segment = segment_in_scope(session, context, value.segment_id)
            if value.op == "set":
                revision(segment, value.expected_revision, "Segment", self.name)
                changes = value.model_dump(
                    exclude_none=True, include={"delivered_frames", "continues"}
                )
                if value.place is not None or value.style is not None:
                    content = dict(segment["content"])
                    if value.place is not None:
                        content["place"] = value.place.model_dump()
                    if value.style is not None:
                        content["style"] = value.style
                    changes["content"] = content
                row = direction.update_segment(
                    session, value.segment_id, value.expected_revision, **changes
                )
            else:
                revision(
                    direction.read_movie(session, context.movie_id),
                    value.expected_revision,
                    "Movie",
                    self.name,
                )
                if value.op == "move":
                    require(value, "ordinal")
                    row = direction.move_segment(
                        session, value.segment_id, value.expected_revision, ordinal=value.ordinal
                    )
                else:
                    if segment["cuts"]:
                        raise ApplicationError(
                            "Remove the segment's cuts first: "
                            + ", ".join(c["id"] for c in segment["cuts"])
                            + ". The user can undo their creation receipts.",
                            409,
                        )
                    row = direction.remove_segment(
                        session, value.segment_id, value.expected_revision
                    )
        if value.op in {"add", "set"}:
            require_assemblable(session, row["id"])
            require_segment_continuity(session, row["id"], context.latest_user_message)
        return edited(context, row, structural=value.op != "set")


class EditPhase(TransactionTool):
    name = "edit_phase"
    description = "Phases. Use edit_cut for cuts. Add/remove: segment rev; set: phase rev."

    class Input(StrictModel):
        op: Literal["add", "set", "remove"]
        expected_revision: int = Field(ge=1)
        segment_id: str | None = None
        phase_id: str | None = None
        start_frame: int | None = Field(default=None, ge=0)
        description: str | None = Field(default=None, max_length=8000)
        screen_direction: str | None = Field(default=None, max_length=8000)
        sound: str | None = Field(default=None, max_length=8000)

    def narrow(self, context):
        if not granted(context, "edit"):
            return None
        segments = [s["id"] for s in context.state.segments]
        phases = [p["id"] for s in context.state.segments for p in s["phases"]]
        specs = {}
        text = ["description", "screen_direction", "sound"]
        if segments:
            required = [
                "segment_id",
                "expected_revision",
                "start_frame",
                "description",
                "screen_direction",
            ]
            specs["add"] = (required, [*required, "sound"])
        if phases:
            common = ["phase_id", "expected_revision"]
            specs |= {"set": (common, [*common, *text, "start_frame"]), "remove": (common, common)}
        if not specs:
            return None
        schema = branches(self.Input, specs)
        for name, ids in (("segment_id", segments), ("phase_id", phases)):
            if ids:
                schema = pin_existing(schema, name, ids)
        return phase_direction_schema(schema, context)

    def execute(self, session, context, value):
        changes = value.model_dump(
            exclude_none=True, include={"description", "screen_direction", "sound"}
        )
        if value.op == "add":
            require(value, "segment_id", "start_frame", "description")
            require_motion_direction(changes)
            segment = segment_in_scope(session, context, value.segment_id)
            revision(segment, value.expected_revision, "Segment", self.name)
            row = direction.add_phase(
                session,
                segment["id"],
                value.expected_revision,
                start_frame=value.start_frame,
                content={"screen_direction": "", "sound": "", **changes},
            )
        else:
            summary = next(
                (
                    s
                    for s in context.state.segments
                    if any(p["id"] == value.phase_id for p in s["phases"])
                ),
                None,
            )
            if summary is None:
                raise ApplicationError("Phase is not in this session's scope.", 422)
            segment = direction.read_segment(session, summary["id"])
            phase = scoped(segment["phases"], value.phase_id, "Phase")
            revision(
                phase if value.op == "set" else segment,
                value.expected_revision,
                "Phase" if value.op == "set" else "Segment",
                self.name,
            )
            if value.op == "set":
                if not changes and value.start_frame is None:
                    raise ApplicationError("Specify at least one phase field.", 422)
                require_motion_direction({**phase["content"], **changes})
                row = direction.update_phase(
                    session,
                    value.phase_id,
                    value.expected_revision,
                    start_frame=value.start_frame,
                    content={**phase["content"], **changes},
                )
            else:
                row = direction.remove_phase(session, value.phase_id, value.expected_revision)
        require_assemblable(session, segment["id"])
        require_segment_continuity(
            session,
            segment["id"],
            context.latest_user_message,
            check_timing=value.op == "add" or (value.op == "set" and value.start_frame is not None),
            previous_segment=segment,
        )
        return edited(context, row, structural=value.op != "set")


class EditCast(TransactionTool):
    name = "edit_cast"
    description = "Add/remove with movie revision; set with member revision. identity_asset_id: current message's attached person image. keeps: things such as a satchel."

    class Input(StrictModel):
        op: Literal["add", "set", "remove"]
        expected_revision: int = Field(ge=1)
        subject_id: str | None = None
        name: str | None = Field(default=None, min_length=1, max_length=120)
        description: str | None = Field(default=None, max_length=8000)
        keeps: list[str] | None = Field(default=None, max_length=24)
        identity_asset_id: str | None = None

    def narrow(self, context):
        if not granted(context, "edit"):
            return None
        required = ["expected_revision", "name", "description", "keeps"]
        specs = {"add": (required, [*required, "identity_asset_id"])}
        ids = [s["id"] for s in context.state.cast]
        if ids:
            common = ["subject_id", "expected_revision"]
            specs |= {
                "set": (common, [*common, "name", "description", "keeps", "identity_asset_id"]),
                "remove": (common, common),
            }
        schema = branches(self.Input, specs)
        if ids:
            schema = pin_existing(schema, "subject_id", ids)
        images = list(
            dict.fromkeys(
                a["id"]
                for a in [*context.attachments, *context.state.assets]
                if a["kind"] == "image"
            )
        )
        if images:
            schema = pin_existing(schema, "identity_asset_id", images)
        else:
            for branch in schema.get("oneOf", [schema]):
                branch["properties"].pop("identity_asset_id", None)
        return schema

    def execute(self, session, context, value):
        if value.identity_asset_id is not None:
            scoped(
                [a for a in [*context.attachments, *context.state.assets] if a["kind"] == "image"],
                value.identity_asset_id,
                "Identity image",
            )
        changes = value.model_dump(
            exclude_none=True, include={"name", "description", "keeps", "identity_asset_id"}
        )
        if value.op == "add":
            require(value, "name", "description", "keeps")
            revision(
                direction.read_movie(session, context.movie_id),
                value.expected_revision,
                "Movie",
                self.name,
            )
            row = direction.add_subject(
                session,
                context.movie_id,
                value.expected_revision,
                **{"identity_asset_id": None, **changes},
            )
        else:
            scoped(context.state.cast, value.subject_id, "Subject")
            if value.op == "set":
                revision(
                    direction.read_subject(session, value.subject_id),
                    value.expected_revision,
                    "Subject",
                    self.name,
                )
                row = direction.update_subject(
                    session, value.subject_id, value.expected_revision, **changes
                )
            else:
                revision(
                    direction.read_movie(session, context.movie_id),
                    value.expected_revision,
                    "Movie",
                    self.name,
                )
                row = direction.remove_subject(session, value.subject_id, value.expected_revision)
        return edited(context, row, structural=value.op != "set")
