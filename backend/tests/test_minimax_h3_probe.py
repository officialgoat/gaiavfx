"""The fill probe (ADR 0020 §4) compiles to the H3 memory probe the research submitted (R5, 25 October)."""

import json
from pathlib import Path

import pytest
from parity_graph import node_of, signature

from gaiavfx.integrations.minimax_h3.probe import build_probe_graph, compile_probe
from gaiavfx.modules.sets.probe import ProbeRequest

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "parity" / "FILL-R5-H10-P0.json").read_text(
        encoding="utf-8"
    )
)["graph"]


def research_files():
    def file_of(key):
        node = FIXTURE[key]["inputs"]
        return node.get("file") or node["image"]

    return {
        "first_frame_file": file_of("first"),
        "memory_file": file_of("source_file"),
        "regen_file": file_of("holes_file"),
        "depth_file": file_of("control_file"),
    }


def request(**changes):
    values = {
        "place": "Wet neighbourhood shop street at dusk",
        "move": "step",
        "turn": 0.0,
        "width": 1024,
        "height": 576,
        "length": 73,
        "seed": 7101,
    }
    return ProbeRequest(**(values | changes))


def test_a_step_is_the_probe_the_research_submitted():
    probe = request()
    prompt = compile_probe(probe)
    assert prompt == node_of(FIXTURE, "MiniMaxH3ImageToVideo")["inputs"]["prompt"]
    graph = build_probe_graph(probe, prompt, fps=24, output_prefix="probe", **research_files())
    assert signature(graph) == signature(FIXTURE)


def test_a_turn_and_a_hold_reword_only_the_cameras_sentence():
    step = compile_probe(request())
    left = compile_probe(request(move="turn", turn=-30.0))
    right = compile_probe(request(move="turn", turn=45.0))
    hold = compile_probe(request(move="hold"))
    assert "pans slowly and steadily to the left without leaving its spot" in left
    assert "to the right" in right and "to the right" not in left
    assert "stays perfectly still" in hold
    camera = "The camera moves slowly and steadily a short distance through the place at eye level"
    for prompt in (left, right, hold):
        assert camera not in prompt
        before, after = step.split(camera)[0], step.split("all belonging to the same place")[1]
        assert prompt.startswith(before) and prompt.endswith(after)


def test_the_graph_takes_the_probes_frame_and_length():
    probe = request(move="turn", turn=30.0, width=768, height=448, length=73, seed=3)
    graph = build_probe_graph(
        probe, compile_probe(probe), fps=24, output_prefix="p", **research_files()
    )
    conditioning = node_of(graph, "MiniMaxH3ImageToVideo")["inputs"]
    assert (conditioning["width"], conditioning["height"], conditioning["length"]) == (768, 448, 73)
    assert {
        n["inputs"]["length"] for n in graph.values() if n["class_type"] == "ImageFromBatch"
    } == {73}
    assert node_of(graph, "RandomNoise")["inputs"]["noise_seed"] == 3


@pytest.mark.parametrize(
    "changes",
    [
        {"move": "orbit"},
        {"place": "  "},
        {"move": "turn", "turn": 0.0},
        {"width": 1000},
        {"length": 72},
    ],
)
def test_a_probe_request_is_checked(changes):
    with pytest.raises(ValueError):
        request(**changes)
