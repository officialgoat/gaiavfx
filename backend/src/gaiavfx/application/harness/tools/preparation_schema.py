"""Require producer inputs in the exact state where records cannot supply them."""

import json
from copy import deepcopy

from gaiavfx.application.harness.actions import inlined, pin_enum
from gaiavfx.application.harness.preparation import VIEW_ACTIONS
from gaiavfx.application.harness.shots import shot_plan, shots
from gaiavfx.application.harness.tools.common import permitted
from gaiavfx.application.harness.tools.input_hints import known_empty_image, removal_requested
from gaiavfx.application.harness.tools.insertion import insertion_defaults
from gaiavfx.application.harness.tools.step_targets import target_ids
from gaiavfx.application.harness.world import direction
from gaiavfx.application.prepare_references import view_producer
from gaiavfx.modules.direction.insertion import PLACEMENT_FIELDS


def required(shape, *names):
    shape["required"] = list(dict.fromkeys([*shape.get("required", []), *names]))


def plate_shape(shape, assets, source_angle, context=None):
    """Images can be fitted empty; removal needs prose, and videos also a frame."""
    branches = []
    removal = context is not None and bool(removal_requested(context))
    for kind, empty in (("image", True), ("image", False), ("video", False)):
        if empty and removal:
            continue
        ids = list(
            dict.fromkeys(
                a["id"]
                for a in assets
                if a["kind"] == kind
                and (empty or context is None or not known_empty_image(context, a["id"]))
            )
        )
        if not ids:
            continue
        template = next(
            b for b in shape["anyOf"] if ("empty" if empty else "whom") in b.get("properties", {})
        )
        branch = pin_enum(deepcopy(template), "source_asset", ids)
        branch["properties"]["angle_id"] = {"const": source_angle}
        if not empty:
            if kind == "video":
                required(branch, "frame")
            else:
                branch["properties"].pop("frame")
        branches.append(branch)
    return {"type": "object", "oneOf": branches}


def insertion_shape(shape, context, cut_id):
    """Both authoring and preparation require exactly what records cannot supply."""
    with context.db.transaction() as session:
        record = direction.read_cut(session, cut_id)
        defaults, _, front = insertion_defaults(session, context, record)
    needed = ["who", "looks", "plate", "keep", "where", "size"]
    needed += ["behind"] if defaults.get("facing") == "toward_camera" else list(PLACEMENT_FIELDS)
    required(shape, *(field for field in needed if not defaults.get(field)))
    if not defaults.get("facing"):
        required(shape, "facing")
    shape["properties"]["facing"] = {
        "enum": ["screen_left", "screen_right", *(["toward_camera"] if front else [])]
    }
    return shape


def cut_insertion_branches(schema, context):
    branches = []
    for branch in schema.get("oneOf", [schema]):
        if branch["properties"]["op"]["const"] != "insertion":
            branches.append(branch)
            continue
        grouped = {}
        for cut_id in branch["properties"]["cut_id"]["enum"]:
            shape = deepcopy(branch)
            insertion = shape["properties"]["insertion"]
            insertion = insertion.get("anyOf", [insertion])[0]
            shape["properties"]["insertion"] = insertion_shape(insertion, context, cut_id)
            key = json.dumps(shape, sort_keys=True)
            grouped.setdefault(key, (shape, []))[1].append(cut_id)
        branches.extend(pin_enum(shape, "cut_id", ids) for shape, ids in grouped.values())
    return branches[0] if len(branches) == 1 else {"oneOf": branches}


def prepare_schema(model, context):
    from gaiavfx.application.harness.tools.prepare import preparable

    if not permitted(context, "prepare"):
        return None
    targets = target_ids(context, "prepare_cut")
    chosen = (
        [s for s in shots(context.state) if s["id"] in targets]
        if targets is not None
        else [s for s in shots(context.state) if preparable(context, s)]
    )
    if not chosen:
        return None
    base = inlined(model.model_json_schema())
    places = {p["id"]: p for p in context.state.places}
    assets = [*context.attachments, *context.state.assets]
    grouped = {}
    for shot in chosen:
        shape = deepcopy(base)
        props = shape["properties"]
        choice = shot_plan(context.state, shot)["recommended"]
        actions = {a["action"] for a in choice["actions"]} if choice else set()
        plate = places[shot["place_id"]]["plate"]
        missing_plate = (
            actions & VIEW_ACTIONS
            and view_producer(places[shot["place_id"]]).plate
            and (not plate or plate["angle_id"] != shot["plate_angle_id"])
        )
        if missing_plate and assets:
            props["plate"] = plate_shape(props["plate"], assets, shot["plate_angle_id"], context)
            required(shape, "plate")
        else:
            props.pop("plate")
        if "insert_subject" in actions:
            props["insertion"] = insertion_shape(
                props["insertion"]["anyOf"][0], context, shot["id"]
            )
            if not shot["cut"]["insertion"]:
                required(shape, "insertion")
        else:
            props.pop("insertion")
        key = json.dumps(shape, sort_keys=True)
        grouped.setdefault(key, (shape, []))[1].append(shot["id"])
    branches = [pin_enum(shape, "cut_id", ids) for shape, ids in grouped.values()]
    return branches[0] if len(branches) == 1 else {"oneOf": branches}
