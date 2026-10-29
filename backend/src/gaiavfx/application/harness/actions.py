"""One grammar-constrained tool action per agent turn."""

import json
from collections import Counter
from collections.abc import Callable
from copy import deepcopy
from dataclasses import dataclass, field
from typing import Any, ClassVar

from pydantic import Field, ValidationError

from gaiavfx.application.harness.context import SCHEMA_CEILING, estimate
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError

THOUGHT_LIMIT = 300
PINNED_ENUM_LIMIT = 8
CROSSES_BARRIER_GUIDANCE = (
    "crosses_barrier: true when the camera moves to the other side of the barrier "
    "(a reverse across the railing), otherwise false"
)


class Action(StrictModel):
    thought: str = Field(default="", max_length=THOUGHT_LIMIT)
    tool: str = Field(min_length=1, max_length=60)
    input: dict[str, Any] = Field(default_factory=dict)


class ToolResult(StrictModel):
    ok: bool = True
    payload: dict[str, Any] = Field(default_factory=dict)
    discrepancy: str = ""
    changed: bool = False
    progress: bool = False
    receipts: list[dict] = Field(default_factory=list)
    waits_on: str = ""
    focus: dict | None = None


@dataclass
class ToolContext:
    db: Any
    session_id: str
    movie_id: str
    scope_kind: str
    scope_id: str
    board: Any
    view: str
    turn: int
    turn_id: str = ""
    state: Any = None
    settings: dict = field(default_factory=dict)
    autonomy: str = "off"
    jobs_reserved: int = 0
    jobs_limit: int = 6
    cancelled: Callable[[], bool] = lambda: False
    document: Any = None
    library: Any = None
    attachments: list = field(default_factory=list)
    unread_attachments: list[str] = field(default_factory=list)
    unread_renders: list[str] = field(default_factory=list)
    latest_user_message: str = ""
    work_performed: bool = False
    say_attempted: bool = False
    reference_notes: dict = field(default_factory=dict)
    recent_user_words: list[str] = field(default_factory=list)
    plate_rejections: dict[str, str] = field(default_factory=dict)
    report_turns: list[dict] = field(default_factory=list)
    correction_attempted: bool = False
    plate_review_attempted: bool = False


class Tool:
    name: ClassVar[str] = ""
    description: ClassVar[str] = ""
    terminal: ClassVar[bool] = False
    Input: ClassVar[type[StrictModel]] = StrictModel

    def describe(self, context) -> str:
        """The catalog's text for this tool; a tool worded differently in a film overrides it."""
        return self.description

    def narrow(self, context):
        """Return this tool's JSON schema under the current state, or None when inapplicable."""
        return self.Input.model_json_schema()

    def run(self, context, value):
        from gaiavfx.application.harness.tools.common import truthful_refusal

        if reason := self.reading_refusal(context):
            return ToolResult(ok=False, discrepancy=reason)
        result = self._run(context, value)
        if not result.ok:
            result.discrepancy = truthful_refusal(context, result.discrepancy)
        return result

    def reading_refusal(self, context):
        """The catalog and direct execution share the same read-before-acting gate."""
        if context.unread_attachments and self.name not in {
            "describe_reference",
            "analyze_reference_video",
            "analyze_people",
            "read_recast",
            "reference_notes",
            "ask_human",
        }:
            return (
                "Read the latest message's attachments before acting or discussing them: "
                + ", ".join(context.unread_attachments)
            )
        if context.unread_renders and self.name in {"say", "propose_plan"}:
            return (
                "Use read_candidates before reporting this message's completed renders: "
                + ", ".join(context.unread_renders)
            )
        return ""

    def _run(self, context, value):
        raise NotImplementedError


def pin_property(schema, property_name, replacement):
    """Replace one property, on the shape itself or on every alternative that carries it.

    Pins are applied after a tool has split its shape per operation, so a pin that reached
    only the top level would silently miss the branch the model is about to use.
    """
    result = deepcopy(schema)
    if "oneOf" in result:
        result["oneOf"] = [
            pin_property(branch, property_name, replacement)
            if property_name in branch.get("properties", {})
            else branch
            for branch in result["oneOf"]
        ]
        return result
    result["properties"][property_name] = deepcopy(replacement)
    return result


def pin_enum(schema, property_name, values):
    return pin_property(schema, property_name, {"enum": list(values)})


def pin_const(schema, property_name, value):
    return pin_property(schema, property_name, {"const": value})


def inlined(schema, depth=8):
    """Substitute every ``$ref`` and drop the ``$defs`` it pointed into.

    A nested input model renders as ``$defs`` plus ``#/$defs/Name``, which is resolved
    from the document root. Each tool's shape is then nested under the action's ``input``,
    where that pointer resolves to nothing: one tool with a nested model made the provider
    reject **every** request with HTTP 400, because the action schema carries them all.
    Inlining keeps a nested model usable instead of forbidding one.
    """
    definitions = schema.get("$defs") or {}
    if not definitions:
        return schema

    def resolve(node, budget):
        if isinstance(node, list):
            return [resolve(item, budget) for item in node]
        if not isinstance(node, dict):
            return node
        reference = node.get("$ref")
        if isinstance(reference, str) and reference.startswith("#/$defs/") and budget > 0:
            target = definitions.get(reference.rsplit("/", 1)[1], {})
            beside = {key: value for key, value in node.items() if key != "$ref"}
            return {**resolve(target, budget - 1), **beside}
        return {key: resolve(value, budget) for key, value in node.items() if key != "$defs"}

    return resolve(schema, depth)


def closed(schema):
    """Forbid extra properties on the shape itself, or on each alternative it offers.

    ``additionalProperties`` only sees the ``properties`` beside it, so setting it next to
    a bare ``oneOf`` forbids every field rather than the unlisted ones.
    """
    if "oneOf" in schema:
        return {**schema, "oneOf": [closed(branch) for branch in schema["oneOf"]]}
    return {**schema, "additionalProperties": False}


def branch_by_op(schema, spec):
    """One alternative per operation, each carrying only the fields that operation uses.

    A record tool needs different fields to add than to update, so a single shape had to
    leave every one of them optional and the command layer reported them missing one per
    round trip. Learning a three-field record cost three rejected turns against a
    stagnation guard that yields at two: ``edit_state`` was rejected on half of all calls
    while no other edit tool passed ten percent.
    """
    branches = []
    for op, rule in spec.items():
        drop = set(rule.get("drop", ()))
        properties = {
            name: value for name, value in schema["properties"].items() if name not in drop
        }
        properties["op"] = {"const": op}
        branch = {
            "type": "object",
            "properties": properties,
            "required": ["op", *rule["required"]],
        }
        # No tool input references one today, but a branch that dropped them would
        # produce a schema whose $refs resolve to nothing.
        if "$defs" in schema:
            branch["$defs"] = schema["$defs"]
        branches.append(branch)
    return branches[0] if len(branches) == 1 else {"oneOf": branches}


def _takes_arguments(schema):
    if "oneOf" in schema:
        return any(_takes_arguments(branch) for branch in schema["oneOf"])
    return bool(schema.get("properties"))


def action_schema(applicable):
    """Bind each tool name to its own input shape so the grammar cannot mismatch them."""
    branches = []
    for tool, schema in applicable:
        branches.append(
            {
                "type": "object",
                # `tool` comes first so the grammar resolves the branch on its constant rather
                # than carrying every alternative through the free-text thought.
                "properties": {
                    "tool": {"const": tool.name},
                    # grammar_schema keeps maxLength <= 400, so the bound reaches the
                    # sampler and an overlong thought cannot cost a rejected turn.
                    "thought": {"type": "string", "maxLength": THOUGHT_LIMIT},
                    "input": closed(inlined(schema)),
                },
                # A tool that takes no arguments has an input object with nothing legal
                # inside it. Requiring it anyway left the grammar permitting only
                # whitespace at that point, and gemma-4-31b filled its entire allowance
                # with it rather than closing the object: four failures a campaign, for
                # six campaigns, reported only as "exhausted its output allowance".
                # Action.input already defaults to {} when it is absent.
                "required": ["tool", "thought", "input"]
                if _takes_arguments(schema)
                else ["tool", "thought"],
            }
        )
        if not _takes_arguments(schema):
            branches[-1]["properties"].pop("input")
    schema = branches[0] if len(branches) == 1 else {"oneOf": branches}
    # Keep the widest catalog inside the same ceiling as ordinary turns. Large
    # scoped id sets use strings; execution still validates the original schema.
    for cap in (PINNED_ENUM_LIMIT, 4, 2, 1):
        result = shared_shapes(bounded_enums(schema, cap))
        if estimate(json.dumps(result, separators=(",", ":"))) <= SCHEMA_CEILING:
            break
    else:
        result = shared_shapes(wire_compact(bounded_enums(schema, 1)))
    return result


def wire_compact(value, field=""):
    """Trim annotations and bounds only when the combined catalog is too wide.

    Required fields, alternatives, ids and enum choices remain intact. Parsing
    still enforces the full narrowed schema and model bounds. Retain place-text
    meanings at creation and the private thought bound in the provider grammar.
    """
    if isinstance(value, list):
        return [wire_compact(item, field) for item in value]
    if not isinstance(value, dict):
        return value
    # Keep the meanings that prevent expensive preparation mistakes, using short
    # annotations when the full catalog exceeds its ceiling. Python still checks
    # every original bound and description-dependent authoring rule.
    if "description" in value and field in {"sound", "overhead", "size"}:
        value = {
            **value,
            "description": {
                "sound": "Empty-place ambience; no footsteps, voices, breathing or fabric.",
                "overhead": "Final view looking straight down.",
                "size": "Image-height fraction; beyond barrier: 1/5 to 1/3.",
            }[field],
        }
    if len(value.get("enum", [])) == 1:
        return {"const": value["enum"][0]}
    return {
        key: {name: wire_compact(rule, name) for name, rule in item.items()}
        if key == "properties"
        else wire_compact(item, field)
        for key, item in value.items()
        if not (key == "description" and field not in {"sound", "overhead", "size"})
        and not (
            key in {"minLength", "maxLength", "minItems", "maxItems"}
            and field not in {"thought", "from_angle", "screen_direction"}
        )
        # The local grammar already drops numeric repetition/range bounds.
        # Python's strict input models enforce them on every execution path.
        and key not in {"minimum", "maximum", "exclusiveMinimum", "exclusiveMaximum", "multipleOf"}
    }


def bounded_enums(value, cap=PINNED_ENUM_LIMIT, field=""):
    """Keep the provider grammar bounded; parse_action retains the full legal set.

    Tools already scope their ids to the active movie/segment/cut, attachments
    and recent operations. Large sets become strings on the wire, never a
    truncated enum that makes a valid id unreachable. Python still checks the
    original schema and returns candidate ids on refusal.
    """
    if isinstance(value, list):
        return [bounded_enums(item, cap, field) for item in value]
    if not isinstance(value, dict):
        return value
    values = value.get("enum", [])
    is_id = field == "target" or field.endswith(("_id", "_ids", "_angle"))
    if is_id and len(values) > cap and all(isinstance(v, str) for v in values):
        return {"type": "string"}
    return {
        key: {name: bounded_enums(rule, cap, name) for name, rule in item.items()}
        if key == "properties"
        else bounded_enums(item, cap, field)
        for key, item in value.items()
    }


def shared_shapes(schema):
    """Hoist repeated input shapes to root definitions, with root-resolvable refs.

    A beat sheet repeats phase/place shapes already present in the edit tools.
    Share only repetitions that save space; validation still uses each tool's
    full narrowed schema. Unlike nested Pydantic definitions, every ref here is
    defined at the action document root and survives the provider grammar pass.
    """
    counts = Counter()
    names = {}
    used = set()

    def key(node):
        return json.dumps(node, sort_keys=True, separators=(",", ":"))

    def visit(node, count=False):
        if isinstance(node, list):
            return [visit(item, count) for item in node]
        if not isinstance(node, dict):
            return node
        shape = any(k in node for k in ("type", "enum", "const", "anyOf"))
        encoded = key(node) if shape else ""
        if count and shape:
            counts[encoded] += 1
        elif encoded in names:
            used.add(encoded)
            return {"$ref": "#/$defs/" + names[encoded]}
        return children(node, count)

    def children(node, count=False):
        return {
            k: {name: visit(rule, count) for name, rule in v.items()}
            if k in {"properties", "$defs"}
            else visit(v, count)
            for k, v in node.items()
        }

    visit(schema, True)
    repeated = [
        encoded
        for encoded, count in counts.items()
        # A two-digit reference costs 21 bytes, a definition key/separator 6.
        # Use the compact wire sizes, retaining a small margin for longer keys.
        if count > 1 and (len(encoded) - 22) * count > len(encoded) + 8
    ]
    # Most catalogs have a few dozen shapes. One-character JSON Pointer names
    # save a byte at every use without dropping fields, bounds or annotations.
    alphabet = "0123456789abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ"
    names = {
        encoded: alphabet[i] if i < len(alphabet) else f"_{i}" for i, encoded in enumerate(repeated)
    }
    if not names:
        return schema
    result = visit(schema)
    # A shared shape can reuse smaller shared children. Strict subtree size
    # makes these references acyclic, without repeating their prose metadata.
    definitions = {}
    while pending := [encoded for encoded in used if names[encoded] not in definitions]:
        for encoded in pending:
            definitions[names[encoded]] = children(json.loads(encoded))
    result["$defs"] = definitions
    return result


def _detail(error, fallback):
    """Name the field, not just the rule.

    Pydantic reports "Field required" with the field in ``loc``; keeping only ``msg`` told
    the model something was wrong without telling it what. qwen3.8-27b opened three of three
    cross-segment sessions emitting no ``input`` at all and spent a turn on that message.
    """
    first = error.errors()[0]
    where = ".".join(str(part) for part in first.get("loc", ()))
    if where.split(".")[-1] == "crosses_barrier":
        return f"{where} needs a boolean; {CROSSES_BARRIER_GUIDANCE}"
    if first["type"] in {"too_short", "too_long"} and isinstance(first.get("input"), list):
        bound = first.get("ctx", {})
        return collection_guidance(where, bound.get("min_length"), bound.get("max_length"))
    message = first.get("msg", fallback)
    return f"{where}: {message}" if where else message


def collection_guidance(name, minimum=None, maximum=None, op=""):
    if name == "angles" and minimum == 1 and op == "add_place":
        return (
            "add_place needs at least one angle: {name, description of the empty place from "
            'that camera}, for example {"name": "Side-on", "description": '
            '"Empty paving facing the railing and colonnade"}'
        )
    examples = {
        "angles": "name and description of the empty camera view",
        "phases": "start_frame (first 0), description and screen_direction",
        "subject_ids": "an existing cast id from read_movie",
        "beats": "title, place, subject_ids, continues, phases and cuts",
        "steps": "kind and title, with target and jobs for preparation/rendering",
        "keeps": "one thing to preserve about the subject",
        "shots": "from_frame, to_frame sample indices and observations for a visible shot",
        "seeds": "integer seed values (omit to use the maintained defaults)",
    }
    bound = f"at least {minimum}" if minimum is not None else f"at most {maximum}"
    example = examples.get(name.split(".")[-1], "items matching the field's schema")
    return f"{name} needs {bound} items: {example}"


def _required_for(narrowed, supplied):
    """What this state actually requires, not what the unnarrowed model would accept.

    The reported list came from ``Input.model_json_schema()``, where every operation's
    fields are optional because one shape had to serve them all. Reading it back told a
    model adding a state that the tool required nothing beyond a segment.
    """
    branches = narrowed.get("oneOf")
    if branches:
        op = supplied.get("op") if isinstance(supplied, dict) else None
        match = next(
            (b for b in branches if b.get("properties", {}).get("op", {}).get("const") == op),
            None,
        )
        narrowed = match or branches[0]
    return list(narrowed.get("required", ()))


def _enforced(narrowed, supplied):
    """Check the narrowed schema's fixed values where the legal set is known now.

    A schema-compiling provider is not uniformly strict about the schema it was sent:
    one recorded run supplied ``define_subject kind="person"``, which passed a plain
    string field and wrote a document that failed validation on the next read. The
    pinned enums and consts are the contract; enforce them in Python rather than trust
    the provider to have done it.
    """
    narrowed = inlined(narrowed)
    branches = narrowed.get("oneOf")
    if branches and isinstance(supplied, dict):
        selectors = {"op", "cut_id", "phase_id", "segment_id", "source_asset", "empty"}
        matching = [
            branch
            for branch in branches
            if all(
                ("const" not in rule or supplied[name] == rule["const"])
                and ("enum" not in rule or supplied[name] in rule["enum"])
                for name, rule in branch.get("properties", {}).items()
                if name in selectors and name in supplied
            )
        ]
        # Prefer the requested operation/record/source. Otherwise a missing
        # field could misleadingly suggest switching to a different target.
        results = [_enforced(branch, supplied) for branch in matching or branches]
        return min(results, key=len) if results else ["No input alternative is available"]
    problems = []
    for name in narrowed.get("required", ()):
        if name not in supplied or (name == "crosses_barrier" and supplied[name] is None):
            problems.append(
                f"{name} is required; {CROSSES_BARRIER_GUIDANCE}"
                if name == "crosses_barrier"
                else f"{name} is required"
            )
    if narrowed.get("additionalProperties") is False:
        problems.extend(
            f"{name} is not allowed"
            for name in supplied
            if name not in narrowed.get("properties", {})
        )
    for name, rule in (narrowed.get("properties") or {}).items():
        if name not in supplied:
            continue
        value = supplied[name]
        # A list-valued field carries its own item semantics; an explicit null means the
        # caller is omitting an optional field.
        if value is None:
            continue
        if isinstance(value, str) and len(value) < rule.get("minLength", 0):
            problems.append(f"{name} must not be empty")
        for variant in rule.get("anyOf", ()):
            if isinstance(value, dict) and variant.get("type") == "object":
                rule = variant
                break
        if isinstance(value, dict):
            problems.extend(f"{name}.{p}" for p in _enforced(rule, value))
        if isinstance(value, list):
            array = next((v for v in rule.get("anyOf", ()) if v.get("type") == "array"), rule)
            if len(value) < array.get("minItems", 0):
                problems.append(
                    collection_guidance(name, array["minItems"], op=supplied.get("op", ""))
                )
            if len(value) > array.get("maxItems", len(value)):
                problems.append(collection_guidance(name, maximum=array["maxItems"]))
            for item in value:
                if isinstance(item, dict):
                    problems.extend(f"{name}.{p}" for p in _enforced(array.get("items", {}), item))
                elif "enum" in array.get("items", {}) and item not in array["items"]["enum"]:
                    problems.append(f"{name} has an item outside its allowed ids")
            continue
        allowed = rule.get("enum")
        if allowed is not None and value not in allowed:
            problems.append(f"{name} must be one of " + ", ".join(map(str, allowed)))
        elif "const" in rule and value != rule["const"]:
            problems.append(f"{name} must be {rule['const']!r}")
    return problems


def parse_action(raw, applicable, context=None):
    try:
        action = Action.model_validate(raw)
    except ValidationError as error:
        raise ApplicationError(
            f"Malformed action: {_detail(error, 'invalid action')}.", 422
        ) from error
    found = next((item for item in applicable if item[0].name == action.tool), None)
    if found is None:
        available = ", ".join(item.name for item, _ in applicable)
        raise ApplicationError(
            f"Tool {action.tool!r} is not available in this state. Available tools: {available}.",
            422,
        )
    tool, narrowed = found
    if context is not None:
        from gaiavfx.application.harness.tools.step_targets import require_target

        require_target(context, action.tool, action.input)
    try:
        value = tool.Input.model_validate(action.input)
    except ValidationError as error:
        required = ", ".join(_required_for(narrowed, action.input))
        detail = _detail(error, "invalid input")
        suffix = f" {tool.name} requires: {required}." if required else ""
        raise ApplicationError(f"Invalid input for {tool.name}: {detail}.{suffix}", 422) from error
    problems = _enforced(closed(inlined(narrowed)), action.input)
    if problems:
        raise ApplicationError(f"Invalid input for {tool.name}: " + "; ".join(problems) + ".", 422)
    return tool, value
