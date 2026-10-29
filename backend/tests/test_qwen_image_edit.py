"""Research insertion parity and the model-free request's failure boundaries."""

import json
import re
from pathlib import Path

import pytest
from parity_graph import signature

from gaiavfx.integrations import qwen_image_edit
from gaiavfx.integrations.qwen_image_edit.graph import (
    build_edit_graph,
    parse_render,
    render_presets,
)
from gaiavfx.integrations.qwen_image_edit.prompt import (
    compile_insertion,
    compile_removal,
    parse_wording,
    wording,
)
from gaiavfx.modules.direction.insertion import Insertion, Removal

FIXTURES = Path(__file__).parent / "fixtures" / "parity"
ARMS = ("IN-DZ", "IN-EH", "IN-EG", "IN-KV", "IN-LV", "IN-VT")
DEVIATIONS = {}


def captures(pattern, text):
    match = re.fullmatch(pattern, text)
    assert match, text
    return match.groupdict()


def authored(arm):
    """Recover the authored facts from a submitted prompt, not from the research script."""
    graph = json.loads((FIXTURES / f"{arm}.json").read_text("utf-8"))["graph"]
    prompt = graph["positive"]["inputs"]["prompt"]
    paragraphs = prompt.split("\n\n")
    assert len(paragraphs) == (5 if arm in ARMS[:3] else 4)
    first = captures(
        r"Edit image 1, (?P<plate>.*?)\. Preserve its camera position, perspective, framing, "
        r"(?P<keep>.*?) and lighting\.",
        paragraphs[0],
    )
    second = captures(
        r"Insert exactly one person: (?P<who>.*?) from image 2\. Use image 2 only for "
        r"(?P<pos>his|her) identity, (?P<looks>.*?)(?: and (?P<direction>left|right)-facing "
        r"walking pose)?\. Exclude its grey background\.",
        paragraphs[1],
    )
    common = dict(
        view_asset="view",
        subject_asset="subject",
        who=second["who"],
        looks=second["looks"],
        pronouns="he" if second["pos"] == "his" else "she",
        **first,
    )
    if second["direction"]:
        third = captures(
            rf"Place {re.escape(second['who'])} (?P<where>.*?), in the "
            r"(?P<starting_half>right|left) half of the frame with room to walk toward screen "
            r"(?P<walk>left|right)\. (?:He|She) is a (?P<distance_description>.*?) figure, "
            r"approximately (?P<size>.*?) of the image height from head to feet\. "
            r"(?:His|Her) full body fits inside the frame\.",
            paragraphs[2],
        )
        fourth = captures(
            r"(?:His|Her) face, torso and leading step point toward the (?:LEFT|RIGHT) edge of the "
            r"image, matching image 2\. (?:He|She) walks (?:left|right) (?P<along>.*?), "
            r"parallel to (?P<barrier>.*?)\. (?P<feet_placement>Both feet .*?\.) "
            r"(?P<occlude>.*)",
            paragraphs[3],
        )
        assert second["direction"] == third["walk"]
        request = Insertion(
            **common,
            facing=f"screen_{second['direction']}",
            where=third["where"],
            size=third["size"],
            distance_description=third["distance_description"],
            **fourth,
        )
    else:
        third = captures(
            rf"Place {re.escape(second['who'])} (?P<where>.*?)\. (?:He|She) faces the camera "
            r"and walks straight toward it, mid-stride: (?:his|her) face, chest and leading foot "
            r"point at the camera\. (?:He|She) is approximately (?P<size>.*?) of the image height "
            r"from head to feet, and (?:his|her) full body fits inside the frame\.",
            paragraphs[2],
        )
        fourth = captures(
            r"Match the plate's perspective, light and ground-contact shadows\. Keep every existing "
            r"feature in place: (?P<behind>.*?)\. The only person is .*?\. Add no other figures, "
            r"reflections of people or background pedestrians\. Output one photographic frame\.",
            paragraphs[3],
        )
        request = Insertion(**common, facing="toward_camera", **third, **fourth)
    images = tuple(graph[f"image_{index}"]["inputs"]["image"] for index in (1, 2))
    return graph, prompt, request, images


@pytest.mark.parametrize("arm", ARMS)
def test_insertion_prompt_matches_submitted_research_bytes(arm):
    _, prompt, request, _ = authored(arm)
    assert arm not in DEVIATIONS
    assert compile_insertion(request) == prompt


@pytest.mark.parametrize("arm", ARMS)
def test_edit_graph_matches_submitted_research_signature(arm):
    graph, _, request, images = authored(arm)
    ours = build_edit_graph(
        compile_insertion(request),
        image_files=images,
        seed=graph["sample"]["inputs"]["seed"],
        width=graph["latent"]["inputs"]["width"],
        height=graph["latent"]["inputs"]["height"],
        output_prefix="gaiavfx/test",
    )
    assert signature(ours) == signature(graph)
    assert ours["negative"]["inputs"]["prompt"] == ""
    assert ours["negative"]["inputs"]["image1"] == ours["positive"]["inputs"]["image1"]
    assert ours["negative"]["inputs"]["image2"] == ours["positive"]["inputs"]["image2"]


def test_removal_uses_the_research_template():
    request = Removal("view", "woman", "her", "the empty paving")
    assert compile_removal(request) == (
        "Remove the woman completely and fill in what was behind her: the empty paving. "
        "Keep everything else exactly the same: framing, perspective, light and colours. No people."
    )


def test_invalid_requests_are_refused():
    _, _, lateral, _ = authored("IN-DZ")
    data = vars(lateral)
    with pytest.raises(ValueError, match="barrier"):
        Insertion(**{**data, "barrier": None})
    for distance in (0, -0.5, float("nan"), float("inf")):
        with pytest.raises(ValueError, match="distance to barrier"):
            Insertion(**{**data, "distance_to_barrier_m": distance})
    with pytest.raises(ValueError, match="unknown facing"):
        Insertion(**{**data, "facing": "diagonal"})
    assert compile_insertion(
        Insertion(**{**data, "distance_to_barrier_m": 2.0})
    ) == compile_insertion(lateral)


def test_right_facing_mirrors_the_lateral_instructions():
    _, _, lateral, _ = authored("IN-DZ")
    text = compile_insertion(Insertion(**{**vars(lateral), "facing": "screen_right"}))
    assert "right-facing walking pose" in text
    assert "left half of the frame with room to walk toward screen right" in text
    assert "toward the RIGHT edge" in text


def test_knowledge_files_are_strict_and_packaged():
    files = qwen_image_edit.knowledge()
    assert set(files) == {"knowledge/wording.toml", "knowledge/render.toml"}
    assert wording() == parse_wording(files["knowledge/wording.toml"])
    assert render_presets() == parse_render(files["knowledge/render.toml"])
    with pytest.raises(ValueError):
        parse_wording(files["knowledge/wording.toml"] + '\nextra = "x"\n')
    with pytest.raises(ValueError):
        parse_render(files["knowledge/render.toml"] + '\nextra = "x"\n')


def test_portrait_wording_is_generic_and_closed():
    from gaiavfx.integrations.qwen_image_edit.prompt import compile_portrait
    from gaiavfx.modules.direction.portrait import VIEWS, Portrait

    prompts = [compile_portrait(Portrait("target", "crop", view)) for view in VIEWS]
    assert len(set(prompts)) == 3
    assert prompts == [compile_portrait(Portrait("other", "another", view)) for view in VIEWS]
    assert not {"curly", "maroon", "fleece", "jacket", "woman", "man"} & set(
        re.findall(r"[a-z]+", " ".join(prompts).lower())
    )
    with pytest.raises(ValueError, match="view"):
        Portrait("target", "crop", "expression")
    text = (Path(qwen_image_edit.__file__).parent / "knowledge/wording.toml").read_text("utf-8")
    with pytest.raises(ValueError, match="portrait"):
        parse_wording(text.split("[portrait]")[0])
    with pytest.raises(ValueError, match="portrait"):
        parse_wording(text + '\nextra = "forbidden"')
