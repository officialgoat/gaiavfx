"""Panoramas' compiled prompts and ComfyUI graphs, and what the product node saves."""

import importlib.util
from pathlib import Path

import numpy as np
import pytest

from gaiavfx.integrations.panorama import graph
from gaiavfx.integrations.panorama.prompt import (
    compile_description,
    outpaint_prompt,
    parse_wording,
    seam_prompt,
)
from gaiavfx.integrations.worldbuilder.panorama import panorama_geometry_graph, settings
from gaiavfx.modules.panoramas.ask import DescribedPlace, Landmark

_spec = importlib.util.spec_from_file_location(
    "gaiavfx_panorama_node", Path(__file__).parents[2] / "comfy_nodes/gaiavfx/panorama.py"
)
node = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(node)

HALL = DescribedPlace(
    "An ordinary city flat's narrow hall with worn parquet",
    (Landmark("Entrance door", 180), Landmark(" Open living-room  doorway", 0)),
    "Soft overcast daylight at 16:30",
)


def kinds(nodes):
    return sorted(n["class_type"] for n in nodes.values())


def test_a_description_compiles_to_the_research_panorama_prompt():
    prompt = compile_description(HALL)
    assert prompt.startswith("img-txt-2-360, Photographic full 360-degree equirectangular panorama")
    assert "heading 180 at the joined edges." in prompt
    assert (
        "worn parquet. From an eye-level viewpoint: Open living-room doorway at 0 degrees, "
        in prompt
    )
    assert "Entrance door at 180 degrees. Soft overcast daylight at 16:30." in prompt
    assert prompt.endswith("Continuous geometry, no people, no labels.")
    plain = compile_description(DescribedPlace("A dune sea at noon."))
    assert "viewpoint" not in plain and "noon. Continuous" in plain


def test_the_edits_use_the_research_words_verbatim():
    assert outpaint_prompt().startswith("<image_1> is a 360-degree equirectangular panorama canvas")
    assert seam_prompt().startswith(
        "Replace the bright green vertical band in the middle of <image_1>"
    )
    with pytest.raises(ValueError, match="description"):
        parse_wording('schema = 1\n[edit]\noutpaint = "x"\nseam = "y"\n')


def test_krea_draws_two_to_one_with_the_360_lora():
    render = graph.render()
    nodes = graph.text_to_panorama_graph(
        "img-txt-2-360, a hall", seed=7101, prefix="p/a", render=render
    )
    assert kinds(nodes) == sorted(
        [
            "UNETLoader",
            "LoraLoaderModelOnly",
            "CLIPLoader",
            "CLIPTextEncode",
            "ConditioningZeroOut",
            "EmptyLatentImage",
            "KSampler",
            "VAELoader",
            "VAEDecode",
            "SaveImage",
        ]
    )
    assert nodes["lora_0"]["inputs"]["lora_name"] == "krea2_t2i_360_erp_lora_v1.safetensors"
    assert nodes["clip"]["inputs"]["type"] == "krea2"
    assert (nodes["latent"]["inputs"]["width"], nodes["latent"]["inputs"]["height"]) == (2048, 1024)
    sample = nodes["sample"]["inputs"]
    assert (sample["steps"], sample["cfg"], sample["seed"]) == (8, 1.0, 7101)
    assert render["candidates"]["seeds"] == [7101, 7102]


def test_fibo_edits_one_named_picture():
    nodes = graph.fibo_edit_graph(
        seam_prompt(), image_file="p/a/band.png", seed=7101, prefix="p/a", render=graph.render()
    )
    encode = nodes["encode"]["inputs"]
    assert encode["images.image_1"] == ["image_1", 0] and encode["negative_prompt"] == ""
    assert (encode["width"], encode["height"]) == (1536, 768)
    assert nodes["clip"]["inputs"]["type"] == "fibo2"
    assert nodes["sample"]["inputs"]["latent_image"] == ["encode", 2]
    assert nodes["sample"]["inputs"]["steps"] == 4


def test_moge3_measures_a_picture_and_the_panorama():
    plate = graph.plate_geometry_graph("p/a/plate.png", prefix="p/a", render=graph.render())
    moge = plate["moge"]["inputs"]
    assert plate["moge_model"]["inputs"]["model_name"] == "moge_3_vitg_fp16.safetensors"
    assert moge["fov_x_degrees"] == 0.0 and moge["refine_steps"] == 3
    pano = panorama_geometry_graph("s/panorama.png", settings=settings(), prefix="s")
    assert pano["moge"]["class_type"] == "MoGePanoramaInference"
    assert pano["moge"]["inputs"]["merge_resolution"] == 1920
    texts = sorted(
        n["inputs"]["text"] for n in pano.values() if n["class_type"] == "CLIPTextEncode"
    )
    assert texts == ["sky", "water", "window"]
    assert {"sky_0", "glass_0", "glass_1", "moge_geometry"} <= set(pano["save"]["inputs"])


def test_the_node_saves_distance_and_raw_finds_on_one_grid():
    depth = np.full((1, 4, 8), 3.0, np.float32)
    depth[0, 0, :] = 40.0
    mask = np.ones((1, 4, 8), bool)
    mask[0, 3, 7] = False
    sky = np.zeros((1, 2, 4), np.float32)  # SAM answered on a smaller grid
    sky[0, 0, :] = 1
    window = np.zeros((2, 4, 8), np.float32)
    window[1, 2, 2] = 1
    arrays = node.moge_arrays(depth, mask, sky=[sky], glass=[window, np.zeros((0, 4, 8))])
    assert arrays["depth"].shape == (4, 8) and arrays["depth"][3, 7] == 0
    assert arrays["sky"][0].all() and arrays["sky"][1].all() and not arrays["sky"][2:].any()
    assert arrays["glass"].sum() == 1 and "intrinsics" not in arrays
    picture = node.moge_arrays(depth, mask, intrinsics=np.eye(3)[None])
    assert picture["intrinsics"].shape == (3, 3)
