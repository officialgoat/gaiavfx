import json

import pytest
from pydantic import Field, ValidationError

from gaiavfx.application.harness.actions import (
    Tool,
    action_schema,
    branch_by_op,
    parse_action,
    pin_const,
    pin_enum,
)
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


class Pick(Tool):
    name = "pick"
    description = "Pick a segment."

    class Input(StrictModel):
        segment_id: str = Field(min_length=1)
        locked: bool = False

    def narrow(self, context):
        schema = pin_enum(self.Input.model_json_schema(), "segment_id", context["segments"])
        return pin_const(schema, "locked", False)

    def run(self, context, value):
        return value


def applicable(context):
    return [(Pick(), Pick().narrow(context))]


def test_narrow_pins_existing_ids_and_protected_values():
    schema = Pick().narrow({"segments": ["one", "two"]})
    assert schema["properties"]["segment_id"] == {"enum": ["one", "two"]}
    assert schema["properties"]["locked"] == {"const": False}
    assert Pick.Input.model_json_schema()["properties"]["segment_id"]["minLength"] == 1


def test_action_schema_binds_each_tool_to_its_input():
    branch = action_schema(applicable({"segments": ["one"]}))
    assert branch["properties"]["tool"] == {"const": "pick"}
    assert branch["properties"]["input"]["properties"]["segment_id"] == {"enum": ["one"]}
    assert branch["properties"]["input"]["additionalProperties"] is False
    # The discriminator is first so the grammar picks the branch on its constant rather than
    # carrying every alternative through the free-text thought.
    assert branch["required"] == ["tool", "thought", "input"]
    assert list(branch["properties"]) == ["tool", "thought", "input"]


def test_grammar_keeps_small_length_bounds_and_strips_large_ones():
    from gaiavfx.infrastructure.assistant import grammar_schema

    schema = grammar_schema(
        {
            "properties": {
                "thought": {"type": "string", "maxLength": 300, "title": "Thought"},
                "text": {"type": "string", "maxLength": 8000},
                "items": {"type": "array", "maxItems": 64},
                "value": {"type": "integer", "maximum": 43200},
            }
        }
    )
    assert schema["properties"]["thought"]["maxLength"] == 300
    assert "title" not in schema["properties"]["thought"]
    assert "maxLength" not in schema["properties"]["text"]
    assert "maxItems" not in schema["properties"]["items"]
    assert "maximum" not in schema["properties"]["value"]


def test_action_schema_uses_one_branch_per_tool():
    first = Pick()
    second = Pick()
    second.name = "other"
    schema = action_schema(
        [
            (first, first.narrow({"segments": ["one"]})),
            (second, second.narrow({"segments": ["one"]})),
        ]
    )
    assert [branch["properties"]["tool"] for branch in schema["oneOf"]] == [
        {"const": "pick"},
        {"const": "other"},
    ]


def test_parse_action_accepts_a_registered_tool():
    tool, value = parse_action(
        {"thought": "pick", "tool": "pick", "input": {"segment_id": "one"}},
        applicable({"segments": ["one"]}),
    )
    assert tool.name == "pick"
    assert value.segment_id == "one"


def test_parse_action_rejects_unavailable_and_malformed_input():
    with pytest.raises(ApplicationError, match="not available"):
        parse_action(
            {"thought": "edit", "tool": "edit", "input": {}},
            applicable({"segments": ["one"]}),
        )
    with pytest.raises(ApplicationError, match="Invalid input for pick"):
        parse_action(
            {"thought": "pick", "tool": "pick", "input": {"segment_id": "one", "extra": 1}},
            applicable({"segments": ["one"]}),
        )
    with pytest.raises(ApplicationError, match="Malformed action"):
        parse_action(
            {"thought": "x" * 400, "tool": "pick", "input": {"segment_id": "one"}},
            applicable({"segments": ["one"]}),
        )


def test_a_pinned_enum_is_enforced_when_the_provider_is_not():
    """The grammar offers the enum; a schema-compiling provider is not always strict.

    One cloud run sent define_subject kind="person", which passed the plain-string field
    and wrote a document that failed validation on the next read. The pinned values are
    the contract, so parse_action checks them in Python.
    """
    with pytest.raises(ApplicationError, match="segment_id must be one of one, two"):
        parse_action(
            {"thought": "pick", "tool": "pick", "input": {"segment_id": "three"}},
            applicable({"segments": ["one", "two"]}),
        )
    with pytest.raises(ApplicationError, match="locked must be False"):
        parse_action(
            {"thought": "pick", "tool": "pick", "input": {"segment_id": "one", "locked": True}},
            applicable({"segments": ["one", "two"]}),
        )


def test_edit_cut_refuses_an_unknown_relation():
    from gaiavfx.application.harness.tools.edits import EditCut

    with pytest.raises(ValidationError):
        EditCut.Input(op="relation", cut_id="cut", expected_revision=1, relation="behind")


def test_the_thought_bound_reaches_the_grammar():
    from gaiavfx.application.harness.actions import THOUGHT_LIMIT
    from gaiavfx.infrastructure.assistant import grammar_schema

    schema = action_schema(applicable({"segments": ["one"]}))
    assert schema["properties"]["thought"]["maxLength"] == THOUGHT_LIMIT
    # grammar_schema drops large bounds; a small one must survive so an overlong
    # thought cannot cost a rejected turn.
    assert grammar_schema(schema)["properties"]["thought"]["maxLength"] == THOUGHT_LIMIT


def test_a_rejection_names_the_field_and_the_tool_requirements():
    """ "Field required" without the field cost a real model a turn, three times out of three.

    Pydantic reports the field in `loc`; parse_action kept only `msg`. qwen3.8-27b opened
    every recorded cross-segment session emitting no `input` at all and was told only that
    something was required.
    """
    from gaiavfx.application.harness.actions import parse_action
    from gaiavfx.application.harness.tools.reads import InspectCut
    from gaiavfx.shared.errors import ApplicationError

    tool = InspectCut()
    applicable = [(tool, tool.Input.model_json_schema())]

    with pytest.raises(ApplicationError) as missing:
        parse_action({"thought": "look", "tool": "inspect_cut", "input": {}}, applicable)
    assert "cut_id" in str(missing.value)
    assert "inspect_cut requires: cut_id" in str(missing.value)

    with pytest.raises(ApplicationError) as malformed:
        parse_action({"thought": "look", "tool": "inspect_cut"}, applicable)
    assert "input" in str(malformed.value)


# ---------------------------------------------------------- per-operation branches


class Record(Tool):
    name = "record"
    description = "Write a record."

    class Input(StrictModel):
        op: str = Field(min_length=1)
        segment_id: str = Field(min_length=1)
        record_id: str | None = None
        at_frame: int | None = None
        entity_id: str | None = None
        value: str | None = None

    def narrow(self, context):
        return branch_by_op(
            self.Input.model_json_schema(),
            {
                "add": {
                    "required": ["segment_id", "at_frame", "entity_id", "value"],
                    "drop": ["record_id"],
                },
                "remove": {
                    "required": ["segment_id", "record_id"],
                    "drop": ["at_frame", "entity_id", "value"],
                },
            },
        )


def branches(schema):
    return {b["properties"]["op"]["const"]: b for b in schema["oneOf"]}


def test_each_operation_requires_only_the_fields_it_uses():
    """One shape for every operation had to leave them all optional.

    The command layer then reported them missing one per round trip, so learning a
    three-field record cost three rejected turns against a guard that yields at two.
    """
    offered = branches(Record().narrow({}))
    assert offered["add"]["required"] == ["op", "segment_id", "at_frame", "entity_id", "value"]
    assert offered["remove"]["required"] == ["op", "segment_id", "record_id"]
    # A field the operation cannot use is absent, not merely optional.
    assert "record_id" not in offered["add"]["properties"]
    assert "entity_id" not in offered["remove"]["properties"]


def test_pins_reach_into_every_branch_that_carries_the_field():
    pinned = pin_enum(Record().narrow({}), "entity_id", ["clerk"])
    offered = branches(pinned)
    assert offered["add"]["properties"]["entity_id"] == {"enum": ["clerk"]}
    assert "entity_id" not in offered["remove"]["properties"]


def test_extra_properties_are_forbidden_per_branch_not_across_them():
    """additionalProperties only sees the properties beside it.

    Set next to a bare oneOf it forbids every field rather than the unlisted ones, so
    the grammar would accept no input at all.
    """
    schema = action_schema([(Record(), Record().narrow({}))])
    shape = schema["properties"]["input"]
    assert "additionalProperties" not in shape
    assert all(b["additionalProperties"] is False for b in shape["oneOf"])
    assert all("segment_id" in b["properties"] for b in shape["oneOf"])


def test_invalid_input_names_what_this_operation_requires():
    """The reported list came from the unnarrowed model, where everything is optional."""
    with pytest.raises(ApplicationError) as error:
        parse_action(
            {"tool": "record", "thought": "", "input": {"op": "add", "segment_id": 5}},
            [(Record(), Record().narrow({}))],
        )
    assert "at_frame, entity_id, value" in str(error.value)


def test_a_nested_input_model_survives_being_nested_under_the_action():
    """A $ref is resolved from the document root, and a tool's shape is not the root.

    plan_segments takes a list of entries, which Pydantic renders as $defs plus
    "#/$defs/Entry". Nested under the action's `input` that pointer resolves to nothing,
    and because the action schema carries every applicable tool at once, one such tool
    made the provider reject **every** request with HTTP 400: a 126-run campaign scored
    18, and the only tasks that passed were the two where the tool narrowed away.
    """
    from pydantic import BaseModel

    class Entry(BaseModel):
        label: str

    class Plan(Tool):
        name = "plan"
        description = "Plan."

        class Input(StrictModel):
            entries: list[Entry] = Field(min_length=1)

    built = action_schema([(Plan(), Plan().narrow(None))])
    text = json.dumps(built)
    assert "$ref" not in text and "$defs" not in text, text
    entry = built["properties"]["input"]["properties"]["entries"]["items"]
    assert entry["properties"]["label"]["type"] == "string"


def test_a_tool_that_takes_no_arguments_does_not_demand_an_empty_object():
    """Requiring it left whitespace as the only legal next token, and a model looped on it.

    read_movie's input is an empty object. With `input` required, a constrained decoder
    that would not commit to the key had nothing else permitted, and gemma-4-31b spent its
    whole allowance emitting "\n  " after a perfectly good tool and thought. It was four
    failures a campaign for six campaigns, reported only as "exhausted its output
    allowance" with no record of what came back.
    """

    class NoArguments(Tool):
        name = "look"
        description = "Look."

        class Input(StrictModel):
            pass

    built = action_schema([(NoArguments(), NoArguments().narrow(None))])
    assert built["required"] == ["tool", "thought"]
    # Still accepted when sent, because Action.input defaults to an empty dict.
    tool, value = parse_action(
        {"tool": "look", "thought": ""}, [(NoArguments(), NoArguments().narrow(None))]
    )
    assert tool.name == "look" and value.model_dump() == {}


def test_repeated_shapes_are_root_resolvable_and_keep_provider_constraints():
    from gaiavfx.application.harness.actions import inlined, shared_shapes
    from gaiavfx.infrastructure.assistant import grammar_schema

    shape = {
        "type": "object",
        "additionalProperties": False,
        "required": ["kind"],
        "properties": {
            "kind": {"enum": ["one", "two", "three"]},
            "text": {"type": "string", "maxLength": 120},
        },
    }
    original = {"type": "object", "properties": {f"entry{i}": shape for i in range(8)}}
    compact = shared_shapes(original)
    assert len(json.dumps(compact)) < len(json.dumps(original))
    assert "$defs" in compact
    assert inlined(compact) == original
    assert inlined(grammar_schema(compact)) == grammar_schema(original)


def test_place_without_angles_has_a_usable_schema_and_rejects_invented_angles():
    from types import SimpleNamespace

    from gaiavfx.application.harness.tools.edits import EditPlace

    context = SimpleNamespace(
        document=None, state=SimpleNamespace(places=[{"id": "place", "angles": []}])
    )
    tool = EditPlace()
    schema = tool.narrow(context)
    assert '"enum": []' not in json.dumps(action_schema([(tool, schema)]))
    raw = {
        "tool": "edit_place",
        "thought": "",
        "input": {
            "op": "set",
            "place_id": "place",
            "expected_revision": 1,
            "description": "A courtyard",
        },
    }
    assert parse_action(raw, [(tool, schema)])[1].description == "A courtyard"
    raw["input"]["angles"] = [
        {"angle_id": "invented", "expected_revision": 1, "description": "View"}
    ]
    with pytest.raises(ApplicationError):
        parse_action(raw, [(tool, schema)])


def test_place_add_and_set_have_independent_angle_shapes():
    from types import SimpleNamespace

    from gaiavfx.application.harness.tools import compact_schema
    from gaiavfx.application.harness.tools.places import EditPlace

    tool = EditPlace()
    context = SimpleNamespace(
        document=None, state=SimpleNamespace(places=[{"id": "place", "angles": [{"id": "angle"}]}])
    )
    schema = compact_schema(tool.narrow(context))
    adding = {
        "op": "add_place",
        "name": "Courtyard",
        "description": "Empty courtyard",
        "sound": "Wind",
        "overhead": "Straight down on paving",
        "angles": [{"name": "Gate", "description": "Through the gate"}],
    }
    setting = {
        "op": "set",
        "place_id": "place",
        "expected_revision": 1,
        "angles": [{"angle_id": "angle", "expected_revision": 1, "description": "Gate"}],
    }
    for value in (adding, setting):
        _, parsed = parse_action({"tool": "edit_place", "input": value}, [(tool, schema)])
        assert parsed.op == value["op"]
    for branch in schema["oneOf"]:
        if "angles" not in branch["properties"]:
            continue
        shape = branch["properties"]["angles"]["items"]
        assert set(shape["required"]) <= set(shape["properties"])
    adding["angles"] = setting["angles"]
    with pytest.raises(ApplicationError):
        parse_action({"tool": "edit_place", "input": adding}, [(tool, schema)])


def test_bounded_id_enums_preserve_literal_choices_and_original_validation():
    from gaiavfx.application.harness.actions import bounded_enums

    ids = [f"asset-{i}" for i in range(12)]
    literals = [f"kind-{i}" for i in range(12)]
    schema = {
        "properties": {
            "asset_id": {"enum": ids},
            "target": {"enum": ids},
            "subject_ids": {"type": "array", "items": {"enum": ids}},
            "kind": {"enum": literals},
            "angle_id": {"const": "source"},
        }
    }
    wire = bounded_enums(schema)
    assert wire["properties"]["asset_id"] == {"type": "string"}
    assert wire["properties"]["target"] == {"type": "string"}
    assert wire["properties"]["subject_ids"]["items"] == {"type": "string"}
    assert wire["properties"]["kind"] == {"enum": literals}
    assert wire["properties"]["angle_id"] == {"const": "source"}
    assert schema["properties"]["asset_id"] == {"enum": ids}
