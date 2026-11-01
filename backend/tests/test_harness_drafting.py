"""Atomic beat sheets and reusable place creation through public commands."""

from copy import deepcopy

import harness_support
from harness_kernel_support import draft_context
from harness_support import action

from gaiavfx.application.harness.actions import parse_action
from gaiavfx.application.harness.tools.common import tool_schema
from gaiavfx.application.harness.tools.draft import DraftStoryline
from gaiavfx.application.harness.tools.places import EditPlace
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.shared.errors import ApplicationError

kernel = harness_support.kernel
direction = DirectionService()


def test_storyline_refusals_roll_back_all_segments_receipts_and_board(kernel):
    context, subject = draft_context(kernel)
    tool = DraftStoryline()
    beats = [
        {
            "title": str(i),
            "place": {"noun": "courtyard", "description": "Rain"},
            "subject_ids": [subject["id"]],
            "continues": i > 0,
            "phases": [
                {"start_frame": 0, "description": "Walk", "screen_direction": "left to right"}
            ],
        }
        for i in range(3)
    ]
    schema = tool.narrow(context)
    assert schema["properties"]["beats"]["items"]["properties"]["subject_ids"]["items"]["enum"] == [
        subject["id"]
    ]
    before = deepcopy(context.board.model_dump())
    invalid = deepcopy(beats)
    invalid[1]["phases"][0]["start_frame"] = 2
    refused = tool.run(context, tool.Input(expected_revision=2, beats=invalid))
    assert not refused.ok and "beat 2" in refused.discrepancy and not refused.receipts
    with kernel.db.transaction() as session:
        movie = direction.read_movie(session, kernel.movie["id"])
        assert movie["revision"] == 2 and not movie["segments"]
    assert context.board.model_dump() == before
    stale = tool.run(context, tool.Input(expected_revision=1, beats=beats)).discrepancy
    assert stale.startswith("Movie revision is 2. Call draft_storyline again")
    context.document.plan.grant.step_kinds = ["answer"]
    assert tool_schema(context, tool) is None
    assert (
        "Not in the approved plan"
        in tool.run(context, tool.Input(expected_revision=2, beats=beats)).discrepancy
    )
    context.document.plan_mode = False
    result = tool.run(context, tool.Input(expected_revision=2, beats=beats))
    # Three segments, one draft: one Receipt, one board decision naming it.
    assert result.ok and len(result.receipts) == 1 and len(result.payload["segments"]) == 3
    assert len(context.board.decisions) == 1
    with kernel.db.transaction() as session:
        movie = direction.read_movie(session, kernel.movie["id"])
        assert len(movie["segments"]) == 3 and movie["revision"] == 5
        for segment in movie["segments"]:
            direction.assemble_segment(session, segment["id"])


def test_place_add_set_grant_and_late_failure_rollback(kernel, monkeypatch):
    context, _ = draft_context(kernel)
    tool = EditPlace()
    value = tool.Input(
        op="add_place",
        name="Storm courtyard",
        description="Tall walls",
        sound="Rain",
        overhead="Rectangular",
        angles=[
            {"name": "gate", "description": "Toward the walls"},
            {"name": "reverse", "description": "Back toward the gate"},
        ],
    )
    parse_action(
        action("edit_place", **value.model_dump(exclude_none=True)), [(tool, tool.narrow(context))]
    )
    add_angle = direction.add_angle
    calls = []

    def fail_late(*args, **kwargs):
        calls.append(1)
        if len(calls) == 2:
            raise ApplicationError("second angle refused", 422)
        return add_angle(*args, **kwargs)

    monkeypatch.setattr("gaiavfx.application.harness.tools.places.direction.add_angle", fail_late)
    result = tool.run(context, value)
    assert not result.ok and not context.board.decisions
    with kernel.db.transaction() as session:
        assert not direction.places(session)
    monkeypatch.setattr("gaiavfx.application.harness.tools.places.direction.add_angle", add_angle)
    result = tool.run(context, value)
    assert result.ok and result.changed and not result.receipts
    place, angles = result.payload["place"], result.payload["angles"]
    assert place["description"] == "Tall walls" and place["sound"] == "Rain"
    assert angles[0]["description"] == "Toward the walls" and len(angles) == 2
    assert "no receipt; delete in the Place panel" in context.board.decisions[0].text
    # Refresh the view; newly created reusable places/angles are available next turn.
    context = kernel.harness.context(kernel.row, context.document, [])
    assert place["id"] in {p["id"] for p in context.state.places}
    change = tool.Input(
        op="set", place_id=place["id"], expected_revision=place["revision"], sound="Wind"
    )
    assert tool.run(context, change).ok
    context.document.plan.grant.step_kinds = ["answer"]
    assert tool_schema(context, tool) is None
    assert not tool.run(context, value).ok
