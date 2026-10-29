"""O12/O13: local-e10 hard-cut/2, stale-revision/1 and drafts-structure/1."""

from copy import deepcopy

import harness_support
import pytest
from harness_support import action
from test_harness_round6 import courtyard, drafted

from gaiavfx.application.harness.actions import (
    action_schema,
    inlined,
    parse_action,
    wire_compact,
)
from gaiavfx.application.harness.tools.cut_angles import barrier_hint
from gaiavfx.application.harness.tools.cut_continuity import require_predecessor
from gaiavfx.application.harness.tools.draft import DraftStoryline
from gaiavfx.application.harness.tools.edits import EditCut
from gaiavfx.application.harness.world import direction
from gaiavfx.shared.errors import ApplicationError

kernel = harness_support.kernel
REQUEST = (
    "In the courtyard, the walker opens in one continuous shot. "
    "Continue into a reverse at the railing."
)
TARGET = (
    "from the shade under the arcade, close behind the railing, "
    "looking straight out across the open courtyard at its far side"
)


def test_o12_required_pair_survives_narrowing_and_wire_compaction(kernel):
    ctx, _ = drafted(kernel)
    tool = EditCut()
    for schema in (
        tool.narrow(ctx),
        inlined(action_schema([(tool, tool.narrow(ctx))]))["properties"]["input"],
        wire_compact(tool.narrow(ctx)),
    ):
        for branch in schema["oneOf"]:
            if branch["properties"]["op"]["const"] in {"create", "relation"}:
                assert {"relation", "crosses_barrier"} <= set(branch["required"])
                assert branch["properties"]["crosses_barrier"] == {"type": "boolean"}
    cut = DraftStoryline().narrow(ctx)["properties"]["beats"]["items"]["properties"]["cuts"][
        "items"
    ]
    assert {"relation", "crosses_barrier"} <= set(cut["required"])
    assert {"relation", "crosses_barrier"} <= set(wire_compact(cut)["required"])


@pytest.mark.parametrize("op", ["create", "relation"])
@pytest.mark.parametrize("crossing", ["missing", None])
def test_o12_missing_crossing_explains_field_and_boolean_meaning(kernel, op, crossing):
    ctx, segment = drafted(kernel)
    cut = segment["cuts"][0]
    values = {
        "op": op,
        "expected_revision": cut["revision"] if op == "relation" else segment["revision"],
        "relation": "reverse",
    }
    if op == "relation":
        values["cut_id"] = cut["id"]
    else:
        values.update(
            segment_id=segment["id"],
            frame=72,
            from_angle_id=cut["to_angle_id"],
            to_angle_id=cut["from_angle_id"],
            phase={"description": "Arin walks on paving", "screen_direction": "left to right"},
        )
    if crossing != "missing":
        values["crosses_barrier"] = crossing
    tool = EditCut()
    with pytest.raises(ApplicationError) as error:
        parse_action(action(tool.name, **values), [(tool, tool.narrow(ctx))])
    result = tool.run(ctx, tool.Input(**values))
    for reason in (str(error.value), result.discrepancy):
        assert "crosses_barrier: true when the camera moves to the other side" in reason
        assert "otherwise false" in reason
    assert not result.ok and not result.receipts


def test_o12_missing_draft_crossing_names_nested_field_and_meaning(kernel):
    ctx, beats = courtyard(kernel)
    del beats[1]["cuts"][0]["crosses_barrier"]
    tool = DraftStoryline()
    with pytest.raises(ApplicationError) as error:
        parse_action(
            action(tool.name, expected_revision=2, beats=beats), [(tool, tool.narrow(ctx))]
        )
    assert "crosses_barrier: true when the camera moves to the other side" in str(error.value)


def test_o12_result_quotes_camera_record_without_overwriting_choice(kernel):
    ctx, segment = drafted(kernel)
    cut = segment["cuts"][0]
    with kernel.db.transaction() as session:
        place = direction.read_place(
            session, direction.read_angle(session, cut["to_angle_id"])["place_id"]
        )
        direction.update_place(
            session,
            place["id"],
            place["revision"],
            description="A black iron railing in front of the arcade.",
        )
        target = direction.read_angle(session, cut["to_angle_id"])
        direction.update_angle(session, target["id"], target["revision"], description=TARGET)
    tool = EditCut()
    result = tool.run(
        ctx,
        tool.Input(
            op="relation",
            cut_id=cut["id"],
            expected_revision=cut["revision"],
            relation="reverse",
            crosses_barrier=False,
        ),
    )
    assert result.ok, result.discrepancy
    assert TARGET in result.payload["message"]
    assert "crosses_barrier: true when" in result.payload["message"]
    with kernel.db.transaction() as session:
        assert direction.read_cut(session, cut["id"])["crosses_barrier"] is False


@pytest.mark.parametrize(
    "description,place_text,expected",
    [
        (TARGET, "The black iron railing.", True),
        ("Camera stands on the other side of the railing.", "A railing.", True),
        ("From across the fence, looking out.", "A fence.", True),
        (TARGET, "Open paving and trees.", False),
        ("Arin stands behind the railing.", "A railing.", False),
        ("From the paving, looking across the railing.", "A railing.", False),
        ("Camera is not behind the railing.", "A railing.", False),
    ],
)
def test_o12_camera_hint_requires_grounded_position(description, place_text, expected):
    hint = barrier_hint(
        {"description": place_text}, {"name": "Reverse", "description": description}
    )
    assert bool(hint) is expected


def test_o12_draft_result_also_reports_camera_evidence(kernel):
    ctx, beats = courtyard(kernel)
    with kernel.db.transaction() as session:
        target = direction.read_angle(session, beats[1]["cuts"][0]["to_angle"])
        place = direction.read_place(session, target["place_id"])
        direction.update_place(
            session, place["id"], place["revision"], description="A black iron railing."
        )
        direction.update_angle(session, target["id"], target["revision"], description=TARGET)
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=2, beats=beats))
    assert result.ok, result.discrepancy
    assert TARGET in result.payload["segments"][1]["hints"][0]


@pytest.mark.parametrize(
    "message", [REQUEST, "He reverses at the railing.", "Cut to the arcade.", "Add a hard cut."]
)
def test_o13_cutless_draft_quotes_requested_cut_and_rolls_back(kernel, message):
    ctx, beats = courtyard(kernel)
    ctx.latest_user_message = message
    beats[1]["cuts"] = []
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=2, beats=beats))
    assert not result.ok and "no cut" in result.discrepancy
    assert message in result.discrepancy
    with kernel.db.transaction() as session:
        movie = direction.read_movie(session, kernel.movie["id"])
        assert not movie["segments"] and movie["revision"] == 2
    assert not result.receipts and not ctx.board.decisions


def test_o13_first_beat_cannot_continue_without_predecessor(kernel):
    ctx, beats = courtyard(kernel)
    beats[0]["continues"] = True
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=2, beats=beats))
    assert not result.ok and "beat 1" in result.discrepancy
    assert "continues: true" in result.discrepancy and "no predecessor" in result.discrepancy
    with kernel.db.transaction() as session:
        assert not direction.read_movie(session, kernel.movie["id"])["segments"]


def test_o13_missing_previous_segment_is_not_replaced_by_an_earlier_one():
    with pytest.raises(ApplicationError, match="no predecessor"):
        require_predecessor(True, 2, [{"ordinal": 0}])
    require_predecessor(True, 2, [{"ordinal": 1}])
    require_predecessor(False, 2, [{"ordinal": 0}])


def test_o13_corrected_record_and_append_with_predecessor_pass(kernel):
    ctx, beats = courtyard(kernel)
    ctx.latest_user_message = REQUEST
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=2, beats=beats))
    assert result.ok, result.discrepancy
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    beat = deepcopy(beats[0])
    beat["continues"] = True
    beat["phases"][0]["screen_direction"] = "right to left"
    appended = tool.run(ctx, tool.Input(expected_revision=result.payload["revision"], beats=[beat]))
    assert appended.ok, appended.discrepancy


@pytest.mark.parametrize(
    "message",
    [
        "One continuous shot; no reverse.",
        "Do not cut to the arcade.",
        "Maybe a hard cut?",
        "Keep the reverse angle from the existing shot.",
    ],
)
def test_o13_negated_uncertain_or_existing_cut_is_not_invented(kernel, message):
    ctx, beats = courtyard(kernel)
    ctx.latest_user_message = message
    tool = DraftStoryline()
    result = tool.run(ctx, tool.Input(expected_revision=2, beats=beats[:1]))
    assert result.ok, result.discrepancy
