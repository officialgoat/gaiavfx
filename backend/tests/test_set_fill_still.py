"""A fill probe's still geometry (ADR 0020 §4): the ComfyUI graph and what its product node saves."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from gaiavfx.integrations.worldbuilder.fill import settings, still_geometry_graph

_spec = importlib.util.spec_from_file_location(
    "gaiavfx_still", Path(__file__).parents[2] / "comfy_nodes/gaiavfx/still.py"
)
still = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(still)


def test_the_still_graph_runs_moge_at_the_probes_lens_and_sam_on_glass():
    graph = still_geometry_graph(
        "fill/p0/still.png", hfov=64.7349, still=settings()["still"], prefix="fill/p0"
    )
    kinds = sorted(node["class_type"] for node in graph.values())
    assert kinds.count("SAM3_Detect") == 2 and "GaiaSaveStillGeometry" in kinds
    moge = next(n for n in graph.values() if n["class_type"] == "MoGeInference")["inputs"]
    assert moge["fov_x_degrees"] == 64.735 and moge["resolution_level"] == 9
    texts = sorted(
        n["inputs"]["text"] for n in graph.values() if n["class_type"] == "CLIPTextEncode"
    )
    assert texts == ["water", "window"]
    save = next(n for n in graph.values() if n["class_type"] == "GaiaSaveStillGeometry")["inputs"]
    assert {"glass_0", "glass_1", "moge_geometry"} <= set(save)


def test_the_node_keeps_valid_depth_and_the_union_of_the_glass():
    depth = np.full((1, 4, 6), 2.0, np.float32)
    depth[0, 0, 0] = np.inf  # sky, as MoGe's apply_mask leaves it
    mask = np.ones((1, 4, 6), np.float32)
    window = np.zeros((1, 4, 6), np.float32)
    window[0, 1, 1] = 1
    water = np.zeros((2, 4, 6), np.float32)  # two separate detections
    water[1, 3, 5] = 1
    arrays = still.still_arrays(depth, mask, [window, water, np.zeros((0, 4, 6))])
    assert arrays["depth"].shape == arrays["mask"].shape == (1, 4, 6)
    assert arrays["mask"][0, 0, 0] == 0 and arrays["depth"][0, 0, 0] == 0
    assert arrays["glass"].dtype == bool and arrays["glass"].sum() == 2
    assert arrays["glass"][1, 1] and arrays["glass"][3, 5]


def test_glass_of_another_size_is_refused():
    with pytest.raises(ValueError, match="differ in size"):
        still.still_arrays(np.ones((4, 6)), np.ones((4, 6)), [np.ones((5, 6))])
