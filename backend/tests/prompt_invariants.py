"""Structural prompt checks shared by retained goldens and frozen research arms."""

import re
from collections import Counter

from gaiavfx.integrations.minimax_h3.prompt import timecode, wording
from gaiavfx.modules.direction.segment import Segment
from gaiavfx.modules.direction.timebase import FrameGrid


def assert_prompt_invariants(text: str, segment: Segment, grid: FrameGrid, context_frames: int):
    definitions, body = text.split("\n\nsummary:\n", 1)
    bound = re.findall(r"^(<Subject \d+>) is (.*?), ", definitions, re.MULTILINE)
    by_name = {name: label for label, name in bound}
    labels = {subject.id: by_name.get(subject.name, subject.name) for subject in segment.subjects}
    shot_lines = re.findall(r"^\[Shot (\d+)\] (.*)$", text, re.MULTILINE)
    assert [int(i) for i, _ in shot_lines] == list(range(1, len(segment.shots) + 1))
    assert text.count("the shot cuts.") == len(segment.cut_frames)
    for (_, line), frame in zip(shot_lines[1:], segment.cut_frames, strict=True):
        clock = timecode(grid.window_frame(frame, context_frames), grid.fps)
        assert line.startswith(f"At {clock}, the shot cuts.")
    for description, count in Counter(
        item.description for item in (*segment.shots, *segment.phases)
    ).items():
        assert text.count(description) == count, f"Action occurrence: {description}"
    expected_speech = [f"[{item.language}] {item.text}" for item in segment.speech]
    assert re.findall(r"<d>(.*?)</d>", text, re.DOTALL) == expected_speech
    words = wording()
    assert text.count(words.lines["continuation"]) == int(segment.continues)
    assert text.count(words.lines["single_take"]) == int(len(segment.shots) == 1)
    defined_subjects = set(re.findall(r"^(<Subject \d+>) is ", definitions, re.MULTILINE))
    defined_pictures = set(
        re.findall(r"Source: (<Picture \d+>)|^(<Picture \d+>) is ", definitions, re.MULTILINE)
    )
    defined_pictures = {label for pair in defined_pictures for label in pair if label}
    assert set(re.findall(r"<Subject \d+>", body)) <= defined_subjects
    assert set(re.findall(r"<Picture \d+>", body)) <= defined_pictures
    for index, ((_, line), shot) in enumerate(zip(shot_lines, segment.shots, strict=True)):
        cast = (
            [labels[s] for s in shot.cast]
            if shot.cast is not None
            else [label for label, _ in bound]
            if index == 0
            else []
        )
        assert line.count("Subjects in frame:") == int(bool(cast))
        if cast:
            assert f"Subjects in frame: {', '.join(cast)}." in line
    assert "@" not in text
    assert "  " not in text, "Doubled space"
    for glue in (",:", ",.", "..", " .", " ,", "Sound: ."):
        assert glue not in text, f"Glued punctuation: {glue}"
