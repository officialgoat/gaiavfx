"""Retained v11 timed clauses, on delivered, window and shot-relative clocks."""

from dataclasses import replace

import pytest

from gaiavfx.integrations.minimax_h3 import capabilities
from gaiavfx.integrations.minimax_h3.prompt import compile_prompt, gaps
from gaiavfx.modules.direction.capabilities import CapabilityGap
from gaiavfx.modules.direction.segment import (
    Event,
    Phase,
    Picture,
    PictureRole,
    PlaceText,
    Segment,
    Shot,
    Speech,
    State,
    Subject,
)

GRID = capabilities().frame_grid


def direction():
    return Segment(
        (Subject("mara", "Mara", "a walker", ("coat",)), Subject("tomas", "Tomas", "a sailor")),
        PlaceText("pier", "An empty pier.", ""),
        (Shot("She walks. Camera position: On the pier.", "", "", ("mara",)),),
        (),
        120,
        False,
    )


def compile(segment, context=0, pictures=()):
    return compile_prompt(replace(segment, continues=context > 0), pictures, (), GRID, context)


@pytest.mark.parametrize(("context", "first_seconds"), [(0, "0.500"), (22, "1.417")])
def test_dialogue_speaker_order_offscreen_and_two_clocks(context, first_seconds):
    segment = replace(
        direction(),
        shots=(
            Shot("They meet.", "", "", ("mara", "tomas")),
            Shot("He waits.", "", "", ("tomas",)),
        ),
        cut_frames=(60,),
        speech=(
            Speech("mara", 12, "You came back.", "English", "quietly"),
            Speech("tomas", 72, "I said I would.", "English"),
            Speech("mara", 100, "Then stay.", "English", off_screen=True),
        ),
    )
    shots = compile(segment, context).prompt.shots
    assert shots[0].endswith(
        "Subjects in frame: Mara, Tomas. "
        f"Mara (S1) says at {first_seconds} seconds into the shot, quietly: "
        "<d>[English] You came back.</d>"
    )
    assert shots[1].endswith(
        "Subjects in frame: Tomas. Tomas (S2) says at 0.500 seconds into the shot: "
        "<d>[English] I said I would.</d> Mara (S1) says in an off-screen voiceover at 1.667 "
        "seconds into the shot: <d>[English] Then stay.</d> while the corresponding "
        "on-screen character's lips remain closed."
    )


def test_bound_speaker_and_protected_speech_text():
    segment = replace(direction(), speech=(Speech("mara", 0, "@Tomas  wait ", "English"),))
    text = compile(segment, 22, (Picture(PictureRole.IDENTITY, "identity", "mara"),)).text
    assert (
        "<Subject 1> (S1) says at 0.917 seconds into the shot: <d>[English] @Tomas  wait </d>"
        in text
    )


@pytest.mark.parametrize(("context", "seconds"), [(0, "2.000"), (22, "2.917")])
def test_event_after_sound_before_cast(context, seconds):
    segment = replace(
        direction(),
        shots=(Shot("She walks.", "", "Waves.", ("mara",)),),
        events=(Event(48, "A gull lands on the railing."),),
    )
    assert (
        compile(segment, context)
        .prompt.shots[0]
        .endswith(
            f"Sound: Waves. At {seconds} seconds into this shot, A gull lands on the railing. "
            "Subjects in frame: Mara."
        )
    )


@pytest.mark.parametrize("context", [0, 22])
def test_items_on_cuts_belong_to_new_shot(context):
    segment = replace(
        direction(),
        shots=(Shot("Wait.", "", ""),) * 3,
        cut_frames=(36, 72),
        events=(Event(36, "Bell."), Event(72, "Gull.")),
        speech=(Speech("tomas", 36, "Come.", "English"), Speech("mara", 72, "Yes.", "English")),
    )
    shots = compile(segment, context).prompt.shots
    assert "seconds into" not in shots[0]
    assert "At 0.000 seconds into this shot, Bell." in shots[1]
    assert "Tomas (S1) says at 0.000 seconds into the shot" in shots[1]
    assert "At 0.000 seconds into this shot, Gull." in shots[2]
    assert "Mara (S2) says at 0.000 seconds into the shot" in shots[2]


@pytest.mark.parametrize(("context", "timecode"), [(0, "00:01.500"), (22, "00:02.417")])
def test_cut_states_follow_cut_before_description(context, timecode):
    segment = replace(
        direction(),
        shots=(Shot("Wait.", "", ""), Shot("She turns.", "", "")),
        cut_frames=(36,),
        states=(State(36, "mara", "hood", "up"), State(36, "tomas", "left hand", "raised")),
    )
    assert compile(segment, context).prompt.shots[1] == (
        f"[Shot 2] At {timecode}, the shot cuts. Current state: Mara hood: up. "
        "Tomas left hand: raised. She turns."
    )
    bound = compile(segment, context, (Picture(PictureRole.IDENTITY, "identity", "mara"),))
    assert "Current state: <Subject 1> hood: up." in bound.text


@pytest.mark.parametrize("frame", [0, 40])
def test_state_off_cut_is_listable_typed_gap(frame):
    segment = replace(direction(), states=(State(frame, "mara", "hood", "up"),))
    assert [gap.code for gap in gaps(segment, (), ())] == ["state_off_cut"]
    with pytest.raises(CapabilityGap, match="no tested H3 wording") as error:
        compile(segment)
    assert error.value.code == "state_off_cut"


@pytest.mark.parametrize(
    ("context", "start", "end"), [(0, "2.500", "5.000"), (22, "3.417", "5.917")]
)
def test_phase_uses_window_clock_before_cast(context, start, end):
    segment = replace(
        direction(), phases=(Phase(60, 120, "She slows and looks out over the water."),)
    )
    assert (
        compile(segment, context)
        .prompt.shots[0]
        .endswith(
            f"Camera position: On the pier. From {start}s to {end}s: "
            "She slows and looks out over the water. Subjects in frame: Mara."
        )
    )


def test_phases_then_events_then_cast_then_speech():
    segment = replace(
        direction(),
        shots=(Shot("Wait.", "", ""), Shot("Walk.", "", "Waves.", ("mara",))),
        cut_frames=(36,),
        phases=(Phase(60, 90, "Slow.", "Left.", "Wind."), Phase(90, 120, "Stop.")),
        events=(Event(48, "Bell."), Event(100, "Gull.")),
        speech=(Speech("mara", 36, "Hi.", "English"),),
    )
    assert compile(segment, 22).prompt.shots[1] == (
        "[Shot 2] At 00:02.417, the shot cuts. Walk. Sound: Waves. "
        "From 3.417s to 4.667s: Slow. Screen direction: Left. Sound: Wind. "
        "From 4.667s to 5.917s: Stop. At 0.500 seconds into this shot, Bell. "
        "At 2.667 seconds into this shot, Gull. Subjects in frame: Mara. "
        "Mara (S1) says at 0.000 seconds into the shot: <d>[English] Hi.</d>"
    )
