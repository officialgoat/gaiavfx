"""The agent's continuation handoff (docs/CONTINUATION.md): offered when asked, refused with Python's reason, and
named in the world view so the agent follows the rule otherwise."""

import json
from dataclasses import replace

import harness_support
from test_harness_tools import context_for

from gaiavfx.application.harness.actions import parse_action
from gaiavfx.application.harness.tools.draft import DraftStoryline
from gaiavfx.application.harness.tools.edits import EditSegment
from gaiavfx.modules.direction.service import DirectionService

world = harness_support.world
ASKED = "Use the masked handoff into the second segment, with 39 frames of context."


def continuing_pair(work, setup):
    direction = DirectionService()
    with work.db.transaction() as session:
        movie = direction.read_movie(session, setup["movie_id"])
        subject = movie["subjects"][0]["id"]
        rows = []
        for continues in (False, True):
            row = direction.add_segment(
                session,
                movie["id"],
                movie["revision"],
                content={
                    "subjects": [subject],
                    "place": {"noun": "pier", "description": "A wet pier.", "landmarks": ""},
                },
                delivered_frames=72,
                continues=continues,
                phases=[
                    (
                        0,
                        {
                            "description": "Walk.",
                            "screen_direction": "left to right",
                            "sound": "",
                        },
                    )
                ],
            )
            rows.append(row)
            movie = direction.read_movie(session, movie["id"])
    return rows


def run(tool, context, value):
    schema = tool.narrow(context)
    _, parsed = parse_action({"tool": tool.name, "thought": "", "input": value}, [(tool, schema)])
    return tool.run(context, parsed)


def stored(work, segment_id):
    with work.db.transaction() as session:
        row = DirectionService().read_segment(session, segment_id)
    return row["continuation_method"], row["context_frames"], row["revision"]


def test_the_handoff_is_offered_when_asked_and_null_returns_it_to_the_rule(world):
    work, setup = world
    first, second = continuing_pair(work, setup)
    context = context_for(work, setup)
    edit, draft = EditSegment(), DraftStoryline()
    # Unasked, the rule decides: neither tool spends schema on the handoff.
    for tool in (edit, draft):
        assert "context_frames" not in json.dumps(tool.narrow(context))
    view = next(s for s in context.state.segments if s["id"] == second["id"])
    assert view["continuation"] == {
        "method": "masked",
        "recommended": "masked",
        "context_frames": 22,
        "chosen": False,
    }
    assert "continuation" not in next(s for s in context.state.segments if s["id"] == first["id"])

    asked = replace(context, latest_user_message=ASKED)
    for tool in (edit, draft):
        schema = json.dumps(tool.narrow(asked))
        assert '"enum": ["paired", "masked", null]' in schema
        assert '"enum": [5, 22, 39, 56, null]' in schema
    result = run(
        edit,
        asked,
        {
            "op": "set",
            "segment_id": second["id"],
            "expected_revision": second["revision"],
            "continuation_method": "masked",
            "context_frames": 39,
        },
    )
    assert result.ok, result.discrepancy
    method, frames, revision = stored(work, second["id"])
    assert (method, frames) == ("masked", 39)
    assert [r["command"] for r in result.receipts] == ["update_segment"]
    # An explicit null follows the rule again; an omitted field is left as it is.
    result = run(
        edit,
        asked,
        {
            "op": "set",
            "segment_id": second["id"],
            "expected_revision": revision,
            "continuation_method": None,
        },
    )
    assert result.ok, result.discrepancy
    assert stored(work, second["id"])[:2] == (None, 39)


def test_a_handoff_the_segment_cannot_take_is_refused_with_the_reason(world):
    work, setup = world
    first, second = continuing_pair(work, setup)
    asked = replace(context_for(work, setup), latest_user_message=ASKED)
    refused = run(
        EditSegment(),
        asked,
        {
            "op": "set",
            "segment_id": first["id"],
            "expected_revision": first["revision"],
            "continuation_method": "masked",
        },
    )
    assert not refused.ok
    assert "Only a continuing segment hands over motion" in refused.discrepancy
    assert stored(work, first["id"])[:2] == (None, None)
    # Place and handoff in one call: two receipts, both revision-checked.
    both = run(
        EditSegment(),
        asked,
        {
            "op": "set",
            "segment_id": second["id"],
            "expected_revision": second["revision"],
            "style": "Grey dusk.",
            "continuation_method": "paired",
        },
    )
    assert both.ok, both.discrepancy
    assert [r["command"] for r in both.receipts] == ["update_segment", "update_segment"]
    assert stored(work, second["id"])[:2] == ("paired", None)
