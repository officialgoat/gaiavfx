"""The survey take matches the four submitted research prompts and inference graphs."""

import json
import re
from dataclasses import replace
from pathlib import Path

import pytest
from parity_graph import node_of, signature

from gaiavfx.integrations import minimax_h3
from gaiavfx.integrations.minimax_h3.survey import (
    build_survey_graph,
    compile_survey,
    parse_survey_wording,
)
from gaiavfx.modules.direction.survey import SurveyPlan

FIXTURES = Path(__file__).parent / "fixtures" / "parity"
ARMS = ("SV-C2B", "SV-STR", "SV-HAR", "SV-CYD")
GRID = minimax_h3.capabilities().frame_grid
DEVIATIONS: dict[str, str] = {}


def authored(arm):
    """Recover place and angle prose from the submitted prompt, leaving structure to the compiler."""
    graph = json.loads((FIXTURES / f"{arm}.json").read_text(encoding="utf-8"))["graph"]
    text = node_of(graph, "MiniMaxH3ImageToVideo")["inputs"]["prompt"]
    sections = dict(part.split(":\n", 1) for part in text.split("\n\n"))
    what = (
        sections["subject_definitions"]
        .removeprefix("<Location 1> is the empty place in the first frame: ")
        .removesuffix(".")
    )
    shot_lines = sections["detailed_description"].splitlines()[1:]
    first_view = shot_lines[0].removeprefix("[Shot 1] The first frame's view: ")
    views = tuple(
        (
            f"angle-{index}",
            re.fullmatch(r"\[Shot \d+\] At \d\d:\d\d\.\d\d\d, hard cut: (.*)\.", line).group(1),
        )
        for index, line in enumerate(shot_lines[1:-1], start=2)
    )
    overhead = re.fullmatch(
        r"\[Shot \d+\] At \d\d:\d\d\.\d\d\d, hard cut to (.*)\. Hold to the end\. "
        r"Each cut changes the camera instantly; the architecture stays fixed\.",
        shot_lines[-1],
    ).group(1)
    first_file = node_of(graph, "LoadImage")["inputs"]["image"]
    plan = SurveyPlan(
        what=what,
        sound=sections["overall_soundscape"],
        first_view=first_view,
        views=views,
        overhead=overhead,
        first_angle_id="opening",
        first_frame_asset="opening-asset",
    )
    return graph, text, plan, first_file


@pytest.mark.parametrize("arm", ARMS)
def test_survey_prompt_matches_research_bytes(arm):
    _, text, plan, _ = authored(arm)
    compiled = compile_survey(plan, GRID)
    expected = text.removesuffix("\n") if arm in DEVIATIONS else text
    assert compiled.text == expected


@pytest.mark.parametrize("arm", ARMS)
def test_survey_graph_matches_research_inference(arm):
    graph, _, plan, first_file = authored(arm)
    compiled = compile_survey(plan, GRID)
    ours = build_survey_graph(
        compiled,
        first_frame_file=first_file,
        seed=node_of(graph, "RandomNoise")["inputs"]["noise_seed"],
        width=768,
        height=448,
        fps=GRID.fps,
        output_prefix="gaiavfx/test",
    )
    assert signature(ours) == signature(graph)


def test_survey_frame_picks_are_midpoints_of_the_delivered_spans():
    _, _, plan, _ = authored("SV-C2B")
    compiled = compile_survey(plan, GRID)
    assert compiled.window_frames == 124
    assert compiled.frames == (
        ("opening", 12),
        ("angle-2", 36),
        ("angle-3", 60),
        ("angle-4", 84),
        ("overhead", 110),
    )


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"views": ()}, "at least one"),
        ({"views": (("opening", "another angle"),)}, "unique"),
        ({"views": (("overhead", "another angle"),)}, "unique"),
        ({"views": (("angle-2", "first"), ("angle-2", "second"))}, "unique"),
        ({"overhead": ""}, "nonempty"),
        ({"first_frame_asset": ""}, "nonempty"),
    ],
)
def test_invalid_survey_plans_are_refused(changes, message):
    _, _, plan, _ = authored("SV-C2B")
    with pytest.raises(ValueError, match=message):
        replace(plan, **changes)


def test_survey_wording_file_is_strict():
    path = Path(minimax_h3.__file__).parent / "knowledge" / "survey.toml"
    text = path.read_text(encoding="utf-8")
    parse_survey_wording(text)
    with pytest.raises(ValueError, match="exactly"):
        parse_survey_wording(text.replace('music = "N/A"', 'music = "N/A"\nextra = "x"'))


def test_portrait_sheet_layout():
    from gaiavfx.integrations.minimax_h3.sheet import portrait_sheet_layout

    for size in ((768, 1024), (1920, 1080)):
        target, front, quarter, profile = portrait_sheet_layout(size, 768, 1024, 6)
        assert front == (262, 0, 506, 506)
        assert quarter == (262, 512, 250, 250)
        assert profile == (518, 512, 250, 250)
        assert target[0] >= 0 and target[0] + target[2] <= 256
        assert abs(target[2] / target[3] - size[0] / size[1]) < 0.01
        assert target[1] > 0
        for x, y, w, h in (target, front, quarter, profile):
            assert 0 <= x < x + w <= 768 and 0 <= y < y + h <= 1024
