"""S7r: run 9 turn 20 cannot introduce slivers or axial phases after a reverse."""

from copy import deepcopy

import harness_support
import pytest
from harness_support import action
from test_harness_round6 import courtyard, drafted

from gaiavfx.application.harness.actions import parse_action
from gaiavfx.application.harness.tools.draft import DraftStoryline
from gaiavfx.application.harness.tools.edits import EditCut, EditPhase
from gaiavfx.application.harness.world import direction

kernel = harness_support.kernel
RUN9 = (
    "Arin's back now toward the lens as he walks past, the open courtyard with its "
    "arched colonnade and potted olive trees revealed behind him"
)
LATERAL = "Arin walks in profile beyond the railing"


def movie(kernel):
    with kernel.db.transaction() as session:
        return direction.read_movie(session, kernel.movie["id"])


def invoke(kernel, ctx, tool, **values):
    before = movie(kernel)
    board = deepcopy(ctx.board.model_dump())
    result = tool.run(ctx, tool.Input(**values))
    if not result.ok:
        assert not result.receipts and not result.changed
        assert movie(kernel) == before
        assert ctx.board.model_dump() == board
    return result


@pytest.mark.parametrize(
    "frame,text,length_error,axial_error",
    [
        (37, RUN9, True, True),
        (48, RUN9, False, True),
        (37, LATERAL, True, False),
        (48, LATERAL, False, False),
        (90, LATERAL, False, False),
        (91, LATERAL, True, False),
    ],
)
def test_run9_draft(kernel, frame, text, length_error, axial_error):
    ctx, beats = courtyard(kernel)
    beats[1]["phases"].append(
        {"start_frame": frame, "description": text, "screen_direction": "right to left"}
    )
    result = invoke(kernel, ctx, DraftStoryline(), expected_revision=2, beats=beats)
    assert result.ok == (not length_error and not axial_error), result.discrepancy
    if length_error:
        assert "12 frames" in result.discrepancy
        assert "Merge the text into the neighbouring phase" in result.discrepancy
    if axial_error:
        assert '"back now toward the lens"' in result.discrepancy
        assert '"A barrier reverse keeps the walker lateral' in result.discrepancy


@pytest.mark.parametrize("frame", [1, 11, 12, 90, 91, 101])
def test_draft_phases_without_cuts_also_have_minimum_length(kernel, frame):
    ctx, beats = courtyard(kernel)
    beat = beats[0]
    beat["phases"].append(
        {"start_frame": frame, "description": LATERAL, "screen_direction": "left to right"}
    )
    result = invoke(kernel, ctx, DraftStoryline(), expected_revision=2, beats=[beat])
    assert result.ok == (12 <= frame <= 90), result.discrepancy
    if not result.ok:
        assert "12 frames" in result.discrepancy


@pytest.mark.parametrize(
    "frame,text,ok",
    [
        (37, RUN9, False),
        (48, RUN9, False),
        (47, LATERAL, False),
        (48, LATERAL, True),
        (90, LATERAL, True),
        (91, LATERAL, False),
    ],
)
def test_edit_phase_add(kernel, frame, text, ok):
    ctx, segment = drafted(kernel)
    result = invoke(
        kernel,
        ctx,
        EditPhase(),
        op="add",
        segment_id=segment["id"],
        expected_revision=segment["revision"],
        start_frame=frame,
        description=text,
        screen_direction="right to left",
    )
    assert result.ok == ok, result.discrepancy
    if frame == 37:
        assert (
            "12 frames" in result.discrepancy and "back now toward the lens" in result.discrepancy
        )


@pytest.mark.parametrize("field", ["description", "screen_direction"])
@pytest.mark.parametrize("at_cut", [True, False])
def test_edit_phase_set_rejects_run9_text(kernel, field, at_cut):
    ctx, segment = drafted(kernel)
    if not at_cut:
        with kernel.db.transaction() as session:
            direction.add_phase(
                session,
                segment["id"],
                segment["revision"],
                start_frame=48,
                content={"description": LATERAL, "screen_direction": "right to left", "sound": ""},
            )
        ctx = kernel.harness.context(kernel.row, ctx.document, [])
        segment = movie(kernel)["segments"][1]
    phase = segment["phases"][-1]
    result = invoke(
        kernel,
        ctx,
        EditPhase(),
        op="set",
        phase_id=phase["id"],
        expected_revision=phase["revision"],
        **{field: RUN9},
    )
    assert not result.ok and "back now toward the lens" in result.discrepancy


@pytest.mark.parametrize("frame", [37, 47, 48, 90, 91])
def test_edit_phase_move_checks_both_neighbours_and_end(kernel, frame):
    ctx, segment = drafted(kernel)
    with kernel.db.transaction() as session:
        phase = direction.add_phase(
            session,
            segment["id"],
            segment["revision"],
            start_frame=60,
            content={"description": LATERAL, "screen_direction": "right to left", "sound": ""},
        )
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    tool = EditPhase()
    values = {
        "op": "set",
        "phase_id": phase["id"],
        "expected_revision": phase["revision"],
        "start_frame": frame,
    }
    parse_action(action(tool.name, **values), [(tool, tool.narrow(ctx))])
    result = invoke(kernel, ctx, tool, **values)
    assert result.ok == (48 <= frame <= 90), result.discrepancy
    if not result.ok:
        assert "12 frames" in result.discrepancy


@pytest.mark.parametrize("op", ["create", "move"])
@pytest.mark.parametrize("frame", [47, 48, 49])
def test_cut_phase_timing(kernel, op, frame):
    ctx, segment = drafted(kernel)
    cut = segment["cuts"][0]
    with kernel.db.transaction() as session:
        # A separate phase at 60 leaves the proposed shot 13, 12, or 11 frames.
        direction.add_phase(
            session,
            segment["id"],
            segment["revision"],
            start_frame=60,
            content={"description": LATERAL, "screen_direction": "right to left", "sound": ""},
        )
        segment = direction.read_segment(session, segment["id"])
        if op == "create":
            direction.remove_cut(session, cut["id"], segment["revision"])
            segment = direction.read_segment(session, segment["id"])
            direction.remove_phase(session, segment["phases"][1]["id"], segment["revision"])
            segment = direction.read_segment(session, segment["id"])
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    values = {"op": op, "frame": frame}
    if op == "create":
        values.update(
            segment_id=segment["id"],
            expected_revision=segment["revision"],
            from_angle_id=cut["from_angle_id"],
            to_angle_id=cut["to_angle_id"],
            relation="reverse",
            crosses_barrier=True,
            phase={"description": LATERAL, "screen_direction": "right to left"},
        )
    else:
        values.update(cut_id=cut["id"], expected_revision=cut["revision"])
    result = invoke(kernel, ctx, EditCut(), **values)
    assert result.ok == (frame <= 48), result.discrepancy
    if not result.ok:
        assert "12 frames" in result.discrepancy


@pytest.mark.parametrize(
    "user_message", [RUN9, "Arin faces the camera", "Arin walks toward the camera"]
)
def test_explicit_user_exception(kernel, user_message):
    ctx, segment = drafted(kernel)
    ctx.latest_user_message = user_message
    phase = segment["phases"][-1]
    result = invoke(
        kernel,
        ctx,
        EditPhase(),
        op="set",
        phase_id=phase["id"],
        expected_revision=phase["revision"],
        description=RUN9,
    )
    assert result.ok, result.discrepancy


def test_existing_cut_request_allows_later_phase(kernel):
    ctx, segment = drafted(kernel)
    with kernel.db.transaction() as session:
        phase = segment["phases"][-1]
        direction.update_phase(
            session,
            phase["id"],
            phase["revision"],
            content={
                "description": "Arin faces the camera",
                "sound": "",
                "screen_direction": "toward the camera",
            },
        )
    ctx = kernel.harness.context(kernel.row, ctx.document, [])
    segment = movie(kernel)["segments"][1]
    result = invoke(
        kernel,
        ctx,
        EditPhase(),
        op="add",
        segment_id=segment["id"],
        expected_revision=segment["revision"],
        start_frame=48,
        description=RUN9,
        screen_direction="right to left",
    )
    assert result.ok, result.discrepancy


@pytest.mark.parametrize(
    "barrier,relation,ok",
    [(True, "reverse", False), (False, "reverse", True), (True, "side", True)],
)
def test_later_cut_does_not_erase_barrier_reverse(kernel, barrier, relation, ok):
    ctx, beats = courtyard(kernel)
    cut = beats[1]["cuts"][0]
    cut.update(crosses_barrier=barrier, relation=relation)
    beats[1]["cuts"].append(
        dict(
            cut,
            frame=60,
            from_angle=cut["to_angle"],
            to_angle=cut["from_angle"],
            relation="side",
            crosses_barrier=False,
        )
    )
    beats[1]["phases"].append(
        {"start_frame": 72, "description": RUN9, "screen_direction": "right to left"}
    )
    result = invoke(kernel, ctx, DraftStoryline(), expected_revision=2, beats=beats)
    assert result.ok == ok, result.discrepancy


def test_negated_request_does_not_authorize_axial_but_negated_phase_passes(kernel):
    ctx, segment = drafted(kernel)
    ctx.latest_user_message = "Do not face the camera."
    phase = segment["phases"][-1]
    tool = EditPhase()
    values = {"op": "set", "phase_id": phase["id"], "expected_revision": phase["revision"]}
    assert not invoke(kernel, ctx, tool, **values, description=RUN9).ok
    result = invoke(
        kernel, ctx, tool, **values, description="Arin walks laterally; never facing the camera."
    )
    assert result.ok, result.discrepancy
