"""Parity of the new H3 prompt model and graph lowering with the research runs that won (rebuild S2.4).

Each fixture in fixtures/parity is a submitted research graph. The authored content (subject, place, shot texts,
the sheet's photographs, the cut and the pin) is read back out of that prompt; everything else must come from the
new compiler: the rendered prompt byte for byte, and the graph's inference structure (node ids and research-only
instrumentation ignored).
"""

import json
import re
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from parity_graph import node_of, signature
from prompt_invariants import assert_prompt_invariants

from gaiavfx.application import render_cut
from gaiavfx.integrations import minimax_h3
from gaiavfx.integrations.minimax_h3.graph import build_graph
from gaiavfx.integrations.minimax_h3.prompt import (
    compile_prompt,
    gaps,
    parse_wording,
    timecode,
    wording,
)
from gaiavfx.modules.direction.capabilities import CapabilityGap
from gaiavfx.modules.direction.segment import (
    Keyframe,
    MediaGuide,
    Picture,
    PictureRole,
    PlaceText,
    Segment,
    Shot,
    Subject,
)
from gaiavfx.modules.direction.techniques import Block
from gaiavfx.shared.errors import ApplicationError

FIXTURES = Path(__file__).parent / "fixtures" / "parity"
ARMS = ("DZ", "EH", "EG", "TC", "TW", "TS", "TP")
GRID = minimax_h3.capabilities().frame_grid
CONTEXT = 22


def fixture(arm):
    return json.loads((FIXTURES / f"{arm}.json").read_text(encoding="utf-8"))


def split_shot(body):
    description, _, tail = body.partition(" Screen direction: ")
    screen, _, sound = tail.partition(" Sound: ")
    return description, screen, sound


def authored(arm):
    """Read the authored direction and conditioning back out of a research prompt and graph."""
    data = fixture(arm)
    graph = data["graph"]
    text = node_of(graph, "MiniMaxH3ReferenceToVideo")["inputs"]["prompt"]
    sections = dict(part.split(":\n", 1) for part in text.split("\n\n"))
    definition = sections["subject_definitions"].splitlines()
    name, description = re.match(
        r"<Subject 1> is ([^,]+), (.*)\. Source: <Picture 1>\.", definition[0]
    ).groups()
    details = sections["detailed_description"].splitlines()
    keeps = re.match(
        rf"Use <Picture 1> .* Preserve {name}'s identity, (.*); the background", details[0]
    ).group(1)
    keeps = tuple(keeps.split(" and "))  # The frozen arms each name two contribution items.
    production = next(line for line in details if line.startswith("Production context: "))
    shot_lines = [line for line in details if line.startswith("[Shot ")]

    sheet = None
    noun, landmarks = "place", ""
    if len(definition) > 1:
        count, noun_d, separators, origin = re.match(
            r"<Picture 2> is a reference sheet of this one (\w+) in (\w+) panels separated by grey (\w+): \w+ "
            r"photographs of the same empty \w+ (from .+?)\. ",
            definition[1],
        ).group(2, 1, 3, 4)
        noun = noun_d
        landmarks = re.search(
            r"from the sheet: (.*)\. Never show", sections["retention_analysis"]
        ).group(1)
        numbers = wording().words["numbers"]
        sheet = Picture(
            PictureRole.PLACE_VIEWS,
            "sheet",
            first_shot=2,
            panels=numbers.index(count),
            blank_panels=1 if separators == "areas" else 0,
            origin="survey" if origin == "from different places in it" else "walk_around",
        )

    first = shot_lines[0].removeprefix(
        "[Shot 1] This shot follows the referenced identities of <Subject 1>. "
    )
    d1, s1, sound1 = split_shot(first)
    second = re.sub(r"^\[Shot 2\] At (\d\d:\d\d\.\d\d\d), the shot cuts\. ", "", shot_lines[1])
    stamp = re.match(r"^\[Shot 2\] At (\d\d:\d\d\.\d\d\d),", shot_lines[1]).group(1)
    d2, s2, sound2 = split_shot(second)
    s2 = s2.removesuffix(f" The {noun} after the cut is the one in <Picture 2>.")
    window_cut = round(float(stamp.split(":")[1]) * GRID.fps)
    window = node_of(graph, "MiniMaxH3ReferenceToVideo")["inputs"]["length"]

    segment = Segment(
        subjects=(Subject("s1", name, description, keeps),),
        place=PlaceText(noun, production.removeprefix("Production context: "), landmarks),
        shots=(
            Shot(d1, s1, sound1.removesuffix(" Subjects in frame: <Subject 1>.")),
            Shot(
                d2,
                s2,
                sound2.removesuffix(" This shot follows the referenced identities of <Subject 1>."),
            ),
        ),
        cut_frames=(window_cut - CONTEXT,),
        delivered_frames=window - CONTEXT,
        continues=True,
    )
    identity = node_of(graph, "LoadImage")["inputs"]["image"]
    files = {"identity": identity}
    pictures = [Picture(PictureRole.IDENTITY, "identity", subject_id="s1")]
    if sheet:
        pictures.append(sheet)
        files["sheet"] = graph["picture2"]["inputs"]["image"]
    keyframes = []
    guides = [n for n in graph.values() if n["class_type"] == "MiniMaxH3AddGuide"]
    for guide in guides:
        image = graph[guide["inputs"]["image"][0]]["inputs"]["image"]
        files[image] = image
        keyframes.append(Keyframe(image, 1, guide["inputs"]["frame_idx"] - window_cut))
    return data, text, segment, tuple(pictures), tuple(keyframes), files


# Documented deviations from the research prompts, each with its reason.
DEVIATIONS = {
    "EH": "the research prompt file (scratch indoor_views_pin.py) ended with a newline; the model saw one trailing "
    "newline that the new compiler does not emit",
}


@pytest.mark.parametrize("arm", ARMS)
def test_the_prompt_matches_the_research_run_byte_for_byte(arm):
    _, text, segment, pictures, keyframes, _ = authored(arm)
    compiled = compile_prompt(segment, pictures, keyframes, GRID, CONTEXT)
    expected = text.removesuffix("\n") if arm in DEVIATIONS else text
    assert compiled.text == expected
    assert_prompt_invariants(compiled.text, segment, GRID, CONTEXT)


@pytest.mark.parametrize("arm", ARMS)
def test_the_graph_matches_the_research_inference(arm):
    data, _, segment, pictures, keyframes, files = authored(arm)
    graph = data["graph"]
    compiled = compile_prompt(segment, pictures, keyframes, GRID, CONTEXT)
    ours = build_graph(
        compiled,
        files=files,
        seed=node_of(graph, "RandomNoise")["inputs"]["noise_seed"],
        width=768,
        height=448,
        fps=GRID.fps,
        context_latent=node_of(graph, "MiniMaxH3MotionContextLoadLatent")["inputs"]["latent_path"],
        output_prefix="gaiavfx/test",
    )
    if arm in DEVIATIONS:
        graph = json.loads(json.dumps(graph))
        prompt = node_of(graph, "MiniMaxH3ReferenceToVideo")["inputs"]
        prompt["prompt"] = prompt["prompt"].removesuffix("\n")
    assert signature(ours) == signature(graph)


def test_the_pin_lands_twelve_frames_after_the_cut():
    for arm, window in (("DZ", 70), ("EH", 70), ("EG", 46)):
        *_, keyframes, _ = authored(arm)
        assert [k.offset_frames for k in keyframes] == [
            minimax_h3.knowledge_pack().pin_offset_frames
        ]
        _, _, segment, pictures, keyframes, _ = authored(arm)
        assert compile_prompt(segment, pictures, keyframes, GRID, CONTEXT).keyframes[0][1] == window


def test_timecodes_follow_the_model_window():
    assert timecode(58, 24) == "00:02.417"
    assert timecode(34, 24) == "00:01.417"
    assert timecode(24 * 75 + 12, 24) == "01:15.500"


def test_the_wording_file_is_strict():
    text = (Path(minimax_h3.__file__).parent / "knowledge" / "wording.toml").read_text(
        encoding="utf-8"
    )
    parse_wording(text)
    with pytest.raises(ValueError):
        parse_wording(text.replace('music = "N/A"', 'music = "N/A"\nextra = "x"'))


def test_untested_wording_is_refused_rather_than_guessed():
    _, _, segment, pictures, keyframes, _ = authored("DZ")
    fresh = Segment(
        segment.subjects,
        segment.place,
        segment.shots,
        segment.cut_frames,
        segment.delivered_frames,
        continues=False,
    )
    with pytest.raises(CapabilityGap, match="no tested H3 wording") as error:
        compile_prompt(fresh, pictures, keyframes, GRID, 0)
    assert error.value.code == "keyframe_without_context"
    late = Keyframe("pin", 1, segment.delivered_frames)
    with pytest.raises(ValueError, match="inside the delivered clip"):
        compile_prompt(segment, pictures, (late,), GRID, CONTEXT)


@pytest.mark.parametrize("table", ["sections", "lines", "words"])
def test_wording_refuses_extra_keys_in_every_table(table):
    text = (Path(minimax_h3.__file__).parent / "knowledge" / "wording.toml").read_text("utf-8")
    with pytest.raises(ValueError, match=rf"\[{table}\]"):
        parse_wording(text.replace(f"[{table}]", f'[{table}]\nextra = "x"'))


def test_wording_requires_the_fresh_summary():
    text = (Path(minimax_h3.__file__).parent / "knowledge" / "wording.toml").read_text("utf-8")
    without_fresh = "\n".join(
        line for line in text.splitlines() if not line.startswith("summary_fresh =")
    )
    with pytest.raises(ValueError, match=r"\[sections\]"):
        parse_wording(without_fresh)


@pytest.mark.parametrize(
    ("old", "new"),
    [
        ("[lines]", '[lines]\ncontinuing_shot = "obsolete"'),
        ("[lines]", '[lines]\ncut_shot = "obsolete"'),
        ("[lines]", '[lines]\nplace_sentence = "obsolete"'),
        ("summary_fresh =", "unknown_summary ="),
        ("schema = 1", 'schema = 1\nextra = "x"'),
        ("schema = 1", "schema = 2"),
        ("[sections]", "[unknown]\n[sections]"),
    ],
)
def test_wording_refuses_obsolete_keys_missing_fresh_summary_and_extra_tables(old, new):
    text = (Path(minimax_h3.__file__).parent / "knowledge" / "wording.toml").read_text("utf-8")
    with pytest.raises(ValueError):
        parse_wording(text.replace(old, new))


def plain_segment(**changes):
    base = Segment(
        (Subject("mara", "Mara", "a walker in a coat", ("coat",)),),
        PlaceText("pier", "An empty pier.", "the railings"),
        (Shot("She walks.", "", ""), Shot("She stops.", "", "")),
        (36,),
        120,
        False,
    )
    return replace(base, **changes)


def identity(subject="mara", asset="portrait"):
    return Picture(PictureRole.IDENTITY, asset, subject_id=subject)


def place_sheet(asset="sheet", first_shot=2):
    return Picture(PictureRole.PLACE_VIEWS, asset, first_shot=first_shot, panels=2, origin="survey")


@pytest.mark.parametrize("cast", [None, ("mara", "tomas")])
def test_unbound_subjects_use_names_and_need_no_identity_wording(cast):
    segment = plain_segment(
        subjects=(Subject("mara", "Mara", "a walker"), Subject("tomas", "Tomas", "a sailor")),
        shots=(Shot("They meet.", "", "", cast),),
        cut_frames=(),
    )
    compiled = compile_prompt(segment, (), (), GRID, 0)
    assert compiled.prompt.definitions == ("No reference subjects.",)
    assert compiled.prompt.summary.startswith("[reference generation] ")
    assert compiled.prompt.retention == ("Follow the authored direction.",)
    assert "Mara: a walker." in compiled.prompt.description
    assert "Tomas: a sailor." in compiled.prompt.description
    assert "<Subject" not in compiled.text
    assert "Use <Picture" not in compiled.text
    assert "referenced identities" not in compiled.text
    assert ("Subjects in frame: Mara, Tomas." in compiled.text) is (cast is not None)
    assert compiled.slots == ()


@pytest.mark.parametrize("cast", [None, ()])
def test_a_segment_with_no_cast_compiles_without_subjects(cast):
    segment = plain_segment(
        subjects=(), shots=(Shot("A ship passes under the bridge.", "", "", cast),), cut_frames=()
    )
    compiled = compile_prompt(segment, (), (), GRID, 0)
    assert compiled.prompt.definitions == ("No reference subjects.",)
    assert compiled.prompt.retention == ("Follow the authored direction.",)
    assert "Production context: An empty pier." in compiled.text
    assert "A ship passes under the bridge." in compiled.text
    assert "Subjects in frame" not in compiled.text
    assert "<Subject" not in compiled.text
    assert compiled.slots == ()


@pytest.mark.parametrize("landmarks", ["", "the railings"])
def test_a_stage_sheet_needs_no_cast(landmarks):
    """Use a stage for this scene: a segment with no actors keeps the sheet, without an only-person sentence."""
    segment = plain_segment(
        subjects=(),
        place=PlaceText("pier", "An empty pier.", landmarks),
        shots=(Shot("A ship passes under the bridge.", "", "", ()),),
        cut_frames=(),
    )
    sheet = place_sheet(first_shot=1)
    assert gaps(segment, (sheet,), ()) == ()
    compiled = compile_prompt(segment, (sheet,), (), GRID, 0)
    assert compiled.slots == ("sheet",)
    (retention,) = compiled.prompt.retention
    assert "only person" not in compiled.text
    assert retention.endswith("or several panels of the sheet.")
    assert ("from the sheet: the railings." in retention) is bool(landmarks)
    assert "from the sheet: ." not in compiled.text
    assert_prompt_invariants(compiled.text, segment, GRID, 0)


def test_bound_and_unbound_subjects_share_casts_but_only_bound_subjects_have_labels():
    segment = plain_segment(
        subjects=(
            Subject("mara", "Mara", "a walker", ("coat",)),
            Subject("tomas", "Tomas", "a sailor"),
        ),
        shots=(Shot("They meet.", "", ""), Shot("He waits.", "", "", ("tomas",))),
    )
    compiled = compile_prompt(segment, (identity(),), (), GRID, 0)
    assert compiled.prompt.definitions == (
        "<Subject 1> is Mara, a walker. Source: <Picture 1>. <Picture 1> supplies identity only.",
    )
    assert "Tomas: a sailor." in compiled.prompt.description
    assert "(appears in [Shot 1]):" in compiled.prompt.retention[0]
    assert "[Shot 2]" not in compiled.prompt.retention[0]
    assert compiled.prompt.shots == (
        (
            "[Shot 1] This shot follows the referenced identities of <Subject 1>. They meet. "
            "Subjects in frame: <Subject 1>."
        ),
        "[Shot 2] At 00:01.500, the shot cuts. He waits. Subjects in frame: Tomas.",
    )


def test_bound_labels_follow_segment_order_slots_follow_picture_order_and_cast_follows_author():
    segment = plain_segment(
        subjects=(
            Subject("guide", "Guide", "a guide"),
            Subject("mara", "Mara", "a walker", ("coat",)),
            Subject("tomas", "Tomas", "a sailor", ("cap",)),
        ),
        shots=(Shot("They meet.", "", "", ("tomas", "guide", "mara")),),
        cut_frames=(),
    )
    pictures = (identity("tomas", "cap"), identity("mara", "coat"))
    compiled = compile_prompt(segment, pictures, (), GRID, 0)
    assert compiled.slots == ("cap", "coat")
    assert compiled.prompt.definitions == (
        "<Subject 1> is Mara, a walker. Source: <Picture 2>. <Picture 2> supplies identity only.",
        "<Subject 2> is Tomas, a sailor. Source: <Picture 1>. <Picture 1> supplies identity only.",
    )
    assert "referenced identities of <Subject 1>, <Subject 2>." in compiled.prompt.shots[0]
    assert compiled.prompt.shots[0].endswith("Subjects in frame: <Subject 2>, Guide, <Subject 1>.")


@pytest.mark.parametrize("continues", [False, True])
def test_one_shot_context_and_graph_follow_continuation(continues):
    segment = plain_segment(shots=(Shot("She walks.", "", ""),), cut_frames=(), continues=continues)
    context = CONTEXT if continues else 0
    compiled = compile_prompt(segment, (identity(),), (), GRID, context)
    assert (
        compiled.prompt.summary
        == wording().sections["summary_continuation" if continues else "summary_fresh"]
    )
    assert compiled.prompt.description[1] == wording().lines["single_take"]
    assert (wording().lines["continuation"] in compiled.prompt.description) is continues
    assert compiled.context_frames == context
    assert compiled.window_frames == GRID.window_for(segment.delivered_frames, context)
    graph = build_graph(
        compiled,
        files={"portrait": "portrait.png"},
        seed=7,
        width=768,
        height=448,
        fps=GRID.fps,
        context_latent="context.safetensors" if continues else None,
        output_prefix="gaiavfx/test",
    )
    for node in ("motion_context", "load_context", "trim"):
        assert (node in graph) is continues


@pytest.mark.parametrize("continues, context", [(False, 22), (False, 1), (True, 0), (True, -1)])
def test_context_must_agree_with_continuation(continues, context):
    with pytest.raises(ValueError, match="context_frames") as error:
        compile_prompt(plain_segment(continues=continues), (), (), GRID, context)
    assert type(error.value) is ValueError


@pytest.mark.parametrize("cast", [None, ()])
@pytest.mark.parametrize("screen", ["", "Left to right."])
@pytest.mark.parametrize("sound", ["", "Footsteps."])
def test_optional_clauses_leave_no_empty_labels_or_glue(cast, screen, sound):
    segment = plain_segment(shots=(Shot("She walks.", screen, sound, cast),) * 2)
    compiled = compile_prompt(segment, (identity(),), (), GRID, 0)
    assert ("Screen direction:" in compiled.text) is bool(screen)
    assert ("Sound:" in compiled.text) is bool(sound)
    assert "  " not in compiled.text
    assert compiled.text.count("Subjects in frame:") == (1 if cast is None else 0)
    # Empty cast still keeps the identity visible in both shots (v5's rule).
    assert "(appears in [Shot 1], [Shot 2]):" in compiled.prompt.retention[0]
    assert compiled.prompt.shots[0].startswith("[Shot 1] This shot follows")
    assert compiled.prompt.shots[1].startswith("[Shot 2] At 00:01.500, the shot cuts.")
    assert compiled.prompt.shots[1].endswith(
        "This shot follows the referenced identities of <Subject 1>."
    )
    assert wording().lines["single_take"] not in compiled.text


@pytest.mark.parametrize("first_shot", [1, 2, 3])
def test_sheet_begins_at_its_first_shot_and_style_reaches_production(first_shot):
    segment = plain_segment(
        shots=(Shot("She walks.", "", ""),) * 3,
        cut_frames=(36, 72),
        style="Handheld super 16.",
    )
    # Even when the sheet comes first in the input, identities take their slots first.
    compiled = compile_prompt(
        segment, (place_sheet(first_shot=first_shot), identity()), (), GRID, 0
    )
    assert compiled.slots == ("portrait", "sheet")
    assert "Production context: An empty pier. Handheld super 16." in compiled.prompt.description
    for index, shot in enumerate(compiled.prompt.shots, start=1):
        assert ("The pier after the cut is the one in <Picture 2>." in shot) is (
            index >= first_shot
        )
    expected_shots = ", ".join(f"[Shot {i}]" for i in range(first_shot, 4))
    assert f"(appears in {expected_shots}):" in compiled.prompt.retention[1]
    assert "  " not in compiled.text


@pytest.mark.parametrize("kind", ["video", "audio"])
def test_media_guides_are_typed_listable_gaps(kind):
    segment = plain_segment()
    guides = (MediaGuide("source", kind, 12),)
    found = gaps(segment, (), (), guides)
    assert [gap.code for gap in found] == ["media_guide"]
    assert kind in found[0].message and "source" in found[0].message
    assert "no tested H3 wording" in found[0].message
    assert isinstance(found[0], ValueError)
    assert str(found[0]) == found[0].message
    with pytest.raises(CapabilityGap) as error:
        compile_prompt(segment, (), (), GRID, 0, guides=guides)
    assert error.value.code == "media_guide"
    assert minimax_h3.capabilities().media_guides == frozenset()


@pytest.mark.parametrize(
    ("subjects", "pictures", "code"),
    [
        (
            (Subject("mara", "Mara", "a walker", ("coat",)),),
            (identity(), place_sheet("sheet-a"), place_sheet("sheet-b")),
            "several_sheets",
        ),
        (
            (Subject("mara", "Mara", "a walker", ("coat",)), Subject("tomas", "Tomas", "a sailor")),
            (identity(), place_sheet()),
            "sheet_several_subjects",
        ),
        ((Subject("mara", "Mara", "a walker"),), (place_sheet(),), "sheet_unbound_subject"),
    ],
)
def test_each_sheet_gap_is_raised_with_its_own_code(subjects, pictures, code):
    segment = plain_segment(subjects=subjects)
    assert [gap.code for gap in gaps(segment, pictures, ())] == [code]
    with pytest.raises(CapabilityGap, match="no tested H3 wording") as error:
        compile_prompt(segment, pictures, (), GRID, 0)
    assert error.value.code == code


@pytest.mark.parametrize("bound", [False, True])
def test_all_simultaneous_gaps_are_listed_without_rendering(bound, monkeypatch):
    segment = plain_segment()
    pictures = (place_sheet("sheet-a"), place_sheet("sheet-b"))
    if bound:
        segment = replace(
            segment, subjects=segment.subjects + (Subject("tomas", "Tomas", "a sailor"),)
        )
        pictures += (identity(),)
    keyframes = (Keyframe("pin", 1, 12),)
    guides = (MediaGuide("video-source", "video", 0), MediaGuide("audio-source", "audio", 12))
    # Capability inspection is independent of both wording and rendering.
    monkeypatch.setattr(
        "gaiavfx.integrations.minimax_h3.prompt.wording",
        lambda: pytest.fail("capability gaps must not load wording"),
    )
    found = gaps(segment, pictures, keyframes, guides)
    assert [gap.code for gap in found] == [
        "media_guide",
        "media_guide",
        "several_sheets",
        "sheet_several_subjects" if bound else "sheet_unbound_subject",
        "keyframe_without_context",
    ]
    assert all("no tested H3 wording" in gap.message for gap in found)
    with pytest.raises(CapabilityGap) as error:
        compile_prompt(segment, pictures, keyframes, GRID, 0, guides=guides)
    assert error.value.code == found[0].code
    assert error.value.message == found[0].message


def test_two_subjects_can_share_an_identity_slot():
    segment = plain_segment(
        subjects=(
            Subject("mara", "Mara", "a walker", ("coat",)),
            Subject("tomas", "Tomas", "a sailor", ("cap",)),
        )
    )
    compiled = compile_prompt(segment, (identity(), identity("tomas")), (), GRID, 0)
    assert compiled.slots == ("portrait",)
    assert len(compiled.prompt.definitions) == 2
    assert all("Source: <Picture 1>." in line for line in compiled.prompt.definitions)
    graph = build_graph(
        compiled,
        files={"portrait": "shared.png"},
        seed=1,
        width=768,
        height=448,
        fps=GRID.fps,
        context_latent=None,
        output_prefix="gaiavfx/test",
    )
    inputs = node_of(graph, "MiniMaxH3ReferenceToVideo")["inputs"]
    assert inputs["ref_images.ref_image_0"] == ["picture_0", 0]
    assert "ref_images.ref_image_1" not in inputs


@pytest.mark.parametrize(
    ("pictures", "keyframes", "message"),
    [
        ((identity(), place_sheet("portrait")), (), "share an identity"),
        ((identity(),), (Keyframe("portrait", 1, 12),), "share a picture slot"),
        ((identity(), place_sheet()), (Keyframe("sheet", 1, 12),), "share a picture slot"),
        ((identity(), identity(asset="another")), (), "second identity"),
        ((identity(), identity()), (), "second identity"),
        ((identity("nobody"),), (), "unknown subject nobody"),
        ((identity(),), (Keyframe("pin", 2, 12),), "cut 2, which does not exist"),
        ((identity(),), (Keyframe("pin", 1, 84),), "inside the delivered clip"),
    ],
)
def test_reference_and_keyframe_structure_is_refused(pictures, keyframes, message):
    segment = plain_segment(continues=True)
    assert gaps(segment, pictures, keyframes) == ()
    with pytest.raises(ValueError, match=message) as error:
        compile_prompt(segment, pictures, keyframes, GRID, CONTEXT)
    assert type(error.value) is ValueError


def test_identity_can_preserve_identity_alone():
    segment = plain_segment(subjects=(Subject("mara", "Mara", "a walker"),))
    assert compile_prompt(segment, (), (), GRID, 0).prompt.definitions == (
        "No reference subjects.",
    )
    compiled = compile_prompt(segment, (identity(),), (), GRID, 0)
    assert "Preserve Mara's identity; the background follows the scene." in compiled.text


@pytest.mark.parametrize(
    "items, expected",
    [
        ((), "identity"),
        (("ochre raincoat",), "identity and ochre raincoat"),
        (("plaid shirt", "satchel"), "identity, plaid shirt and satchel"),
        (("coat and scarf",), "identity and coat and scarf"),
    ],
)
def test_identity_contributions_render_as_items(items, expected):
    segment = plain_segment(subjects=(Subject("mara", "Mara", "a walker", items),))
    compiled = compile_prompt(segment, (identity(),), (), GRID, 0)
    assert f"Preserve Mara's {expected}; the background follows the scene." in compiled.text


@pytest.mark.parametrize("key", ["list_separator", "list_last", "identity_item"])
def test_wording_requires_identity_list_words(key):
    text = (Path(minimax_h3.__file__).parent / "knowledge" / "wording.toml").read_text("utf-8")
    with pytest.raises(ValueError, match=r"\[words\]"):
        parse_wording(
            "\n".join(line for line in text.splitlines() if not line.startswith(f"{key} ="))
        )


def render_setup(monkeypatch):
    """Isolate storage and planning while exercising the actual render compiler and graph."""
    from gaiavfx.application import direction_plans

    monkeypatch.setattr(direction_plans, "direction", render_cut.direction)
    choice = SimpleNamespace(
        ready=True, blocks=(Block.DIRECTION_TEXT,), technique_id="text", title="Text"
    )
    monkeypatch.setattr(
        direction_plans,
        "plan_for_cut",
        lambda *args: SimpleNamespace(recommended=choice, cut_id="cut", bindings=()),
    )
    # The segment's first shot is planned too (ADR 0019 §1); here it adds nothing to the window.
    monkeypatch.setattr(direction_plans, "plan_for_opening", lambda *args: None)
    monkeypatch.setattr(
        render_cut.direction,
        "read_cut",
        lambda *args: {
            "frame": 36,
            "to_angle_id": "angle",
            "movie_id": "movie",
            "segment_id": "segment",
        },
    )
    monkeypatch.setattr(render_cut.direction, "read_angle", lambda *args: {"place_id": "pier"})
    monkeypatch.setattr(
        render_cut.direction,
        "read_segment",
        lambda *args: {
            "movie_id": "movie",
            "continues": False,
            "revision": 1,
            "phases": [],
            "cuts": [{"id": "cut", "frame": 36, "to_angle_id": "angle"}],
        },
    )
    monkeypatch.setattr(render_cut.direction, "read_subject", lambda *args: {"revision": 1})
    # A cut's sheet binding reads its suitability judgments (none here).
    monkeypatch.setattr(render_cut.direction, "suitability", lambda *args: ())
    monkeypatch.setattr(render_cut.MediaLibrary, "asset_info", lambda *args: {"sha256": "hash"})


def test_start_render_translates_capability_gaps_to_422(monkeypatch):
    render_setup(monkeypatch)
    monkeypatch.setattr(
        render_cut.direction,
        "assemble_segment",
        lambda *args: (
            plain_segment(),
            (identity(), place_sheet("sheet-a"), place_sheet("sheet-b")),
        ),
    )
    with pytest.raises(ApplicationError) as error:
        render_cut.start_render(
            None,
            cut_id="cut",
            seeds=(7,),
        )
    assert error.value.status == 422
    assert str(error.value).startswith("several_sheets:")


def test_start_render_fresh_segment_uses_no_context(monkeypatch):
    render_setup(monkeypatch)
    monkeypatch.setattr(
        render_cut.MediaLibrary, "asset_info", lambda *args: {"sha256": "portrait-hash"}
    )
    monkeypatch.setattr(
        render_cut.MediaLibrary,
        "artifact",
        lambda *args: pytest.fail("a fresh segment must not load motion context"),
    )
    monkeypatch.setattr(render_cut, "create_operation", lambda *args, **kwargs: kwargs["jobs"])
    monkeypatch.setattr(
        render_cut.direction, "assemble_segment", lambda *args: (plain_segment(), (identity(),))
    )
    jobs = render_cut.start_render(
        None,
        cut_id="cut",
        seeds=(7,),
        context_artifact="unused",
    )
    assert len(jobs) == 1
    assert jobs[0].snapshot["context_frames"] == 0
    assert set(jobs[0].inputs) == {"portrait"}
    assert not {"motion_context", "load_context", "trim"} & jobs[0].graph.keys()
    assert (
        "[Shot 2] At 00:01.500, the shot cuts."
        in node_of(jobs[0].graph, "MiniMaxH3ReferenceToVideo")["inputs"]["prompt"]
    )
