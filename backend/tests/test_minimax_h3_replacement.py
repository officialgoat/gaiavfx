"""Golden wording and static graph parity with submitted research replacement arms."""

import ast
import json
import os
import re
from copy import deepcopy
from dataclasses import replace
from importlib import resources
from pathlib import Path

import pytest
from parity_graph import signature
from test_replacement_contract import CAP, joint_facts, mug_facts, plan

from gaiavfx.integrations import minimax_h3
from gaiavfx.integrations.minimax_h3.graph import parse_render, render_presets
from gaiavfx.integrations.minimax_h3.prompt import H3Prompt
from gaiavfx.integrations.minimax_h3.replacement import (
    GENERIC,
    RETIRED,
    build_replacement_graph,
    compile_replacement,
    parse_replacement_wording,
    replacement_wording,
)
from gaiavfx.modules.direction.capabilities import CapabilityGap
from gaiavfx.modules.direction.replacement import Arm, Box, Occluder

FIXTURES = Path(__file__).parent / "fixtures" / "parity"
GOLDEN = Path(__file__).parent / "golden" / "minimax_h3" / "replacement"
CASES = ("mug-full", "mug-crop", "mug-inverted-crop", "mug-quiet", "mug-joint-pair")


@pytest.mark.parametrize("locked", [False, True])
@pytest.mark.parametrize("handoff", ["latent", "bridge"])
def test_masked_graph_only_changes_named_nodes_and_edges(locked, handoff):
    _, plain = product_graph("CROP", audio_lock=locked)
    _, graph = product_graph("CROP", audio_lock=locked, handoff=handoff)
    suffix = ("-AUDIO-LOCK" if locked else "") + "-" + handoff.upper()
    snapshot(GOLDEN / f"REPL-MUG-CROP{suffix}.graph.json", json.dumps(graph, indent=2) + "\n")
    origin = ["audio_lock", 1] if locked else ["conditioning", 1]
    assert graph.pop("span") == {
        "class_type": "GaiaH3MaskedSpan",
        "inputs": {
            "latent": origin,
            "source": ["load_prev" if handoff == "latent" else "bridge_encode", 0],
            "mode": "tail_to_head" if handoff == "latent" else "aligned",
            "head_steps": 7,
            "tail_steps": 0 if handoff == "latent" else 7,
        },
    }
    if handoff == "latent":
        assert graph.pop("load_prev") == {
            "class_type": "MiniMaxH3MotionContextLoadLatent",
            "inputs": {"latent_path": "context.safetensors", "clip_index": 1},
        }
    else:
        assert graph.pop("bridge_video") == {
            "class_type": "LoadVideo",
            "inputs": {"file": "bridge.mkv"},
        }
        assert graph.pop("bridge_components") == {
            "class_type": "GetVideoComponents",
            "inputs": {"video": ["bridge_video", 0]},
        }
        assert graph.pop("bridge_encode") == {
            "class_type": "VAEEncode",
            "inputs": {"pixels": ["bridge_components", 0], "vae": ["video_vae", 0]},
        }
    assert graph["sample"]["inputs"]["latent_image"] == ["span", 0]
    graph["sample"]["inputs"]["latent_image"] = origin
    assert graph == plain


def test_saved_latent_only_adds_save_node():
    _, plain = product_graph("CROP")
    _, graph = product_graph("CROP", save_latent=True)
    snapshot(GOLDEN / "REPL-MUG-CROP-LATENT-SAVE.graph.json", json.dumps(graph, indent=2) + "\n")
    assert graph.pop("save_latent") == {
        "class_type": "MiniMaxH3MotionContextSaveLatent",
        "inputs": {
            "latent": ["sample", 0],
            "filename_prefix": f"replacement/{fixture('CROP')['id']}/context",
            "clip_index": 1,
        },
    }
    assert graph == plain


def test_middle_window_loads_and_saves_latent_in_one_graph():
    _, graph = product_graph("CROP", handoff="latent", save_latent=True)
    assert graph["load_prev"]["inputs"]["latent_path"] == "context.safetensors"
    assert graph["save_latent"]["inputs"]["latent"] == ["sample", 0]
    assert graph["save_latent"]["inputs"]["filename_prefix"].endswith("/context")
    assert graph["span"]["inputs"]["mode"] == "tail_to_head"
    assert graph["span"]["inputs"]["head_steps"] == 7


@pytest.mark.parametrize("handoff", ["none", "latent", "bridge"])
@pytest.mark.parametrize(
    "latent,bridge", [(None, None), ("a", None), (None, "b"), ("a", "b"), ("", None), (None, "")]
)
def test_handoff_files_required_exactly(handoff, latent, bridge):
    f, arm, _ = case("mug-crop")
    compiled = compile_replacement(replace(f, handoff=handoff), arm)
    args = {
        "guide_file": "guide.mkv",
        "picture_files": {"target": "target.png"},
        "seed": 1,
        "output_prefix": "unique",
        "latent_file": latent,
        "bridge_file": bridge,
    }
    valid = (handoff, latent, bridge) in {
        ("none", None, None),
        ("latent", "a", None),
        ("bridge", None, "b"),
    }
    if valid:
        build_replacement_graph(compiled, **args)
    else:
        with pytest.raises(ValueError):
            build_replacement_graph(compiled, **args)


def fixture(name):
    return json.loads((FIXTURES / f"REPL-MUG-{name}.json").read_text(encoding="utf-8"))


def case(name):
    f = joint_facts() if name == "mug-joint-pair" else mug_facts()
    arm = plan(f).arm
    if name == "mug-full":
        arm = replace(arm, framing="full", crop=None)
    if name == "mug-inverted-crop":
        arm = replace(arm, treatment="inverted")
    if name == "mug-quiet":
        f = replace(f, quiet=True)
    return f, arm, compile_replacement(f, arm)


def snapshot(path, text):
    if os.environ.get("UPDATE_REPLACEMENT_GOLDEN") == "1":
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(text.encode("utf-8"))
    assert path.read_bytes() == text.encode("utf-8")


@pytest.mark.parametrize("name", CASES)
def test_prompt_goldens(name):
    f, arm, compiled = case(name)
    assert isinstance(compiled.prompt, H3Prompt)
    assert compiled.text == compiled.prompt.render()
    assert compiled.window_frames == CAP.frame_grid.window_for(f.delivered_frames, 0)
    assert compiled.slots == tuple(p.target_asset for p in f.performers)
    snapshot(GOLDEN / f"{name}.txt", compiled.text)
    allowed = {"<Video 1>", "<Picture 1>", "<Picture 2>", *(f"<Subject {i}>" for i in range(1, 5))}
    assert set(re.findall(r"<[^>]+>", compiled.text)) <= allowed
    assert not any(t in compiled.text for t in ("<Audio", "<d>", "[Shot 2"))
    assert not any(t in compiled.text.lower() for t in RETIRED)
    if arm.framing != "full":
        assert "letterbox" not in compiled.text.lower()


# The sole intentional guide_v1 difference for its exact authored facts:
# v2 retains source framing and (only for full) the source bars explicitly.
GUIDE_SENTENCE_CHANGES = (
    (
        "<Video 1> (camera and surroundings): fully_preserved - Preserve the yellow mug, tableware and furniture.",
        "<Video 1> (camera and surroundings): fully_preserved - Preserve the yellow mug, tableware and furniture, original framing and letterbox.",
    ),
)


def test_guide_v1_byte_parity_except_documented_sentence_changes():
    expected = fixture("GUIDE")["graph"]["conditioning"]["inputs"]["prompt"]
    _f, _arm, compiled = case("mug-full")
    # No normalization of whitespace, section separators, punctuation or remaining wording.
    for removed, added in GUIDE_SENTENCE_CHANGES:
        assert expected.count(removed) == 1
        expected = expected.replace(removed, added)
    assert compiled.text == expected
    # Existing renderer reproduces the actual research section layout byte for byte.
    sections = dict(
        p.split(":\n", 1)
        for p in fixture("GUIDE")["graph"]["conditioning"]["inputs"]["prompt"].split("\n\n")
    )
    prompt = H3Prompt(
        tuple(sections["subject_definitions"].splitlines()),
        sections["summary"],
        tuple(sections["retention_analysis"].splitlines()),
        (),
        (sections["detailed_description"],),
        sections["overall_soundscape"],
        sections["non_diegetic_music"],
    )
    assert prompt.render() == fixture("GUIDE")["graph"]["conditioning"]["inputs"]["prompt"]


def test_compiler_calls_the_single_renderer_once(monkeypatch):
    original, calls = H3Prompt.render, []

    def counted(self):
        calls.append(self)
        return original(self)

    monkeypatch.setattr(H3Prompt, "render", counted)
    f = mug_facts()
    compiled = compile_replacement(f, plan(f).arm)
    assert calls == [compiled.prompt]


def test_crop_regrounds_definitions_but_never_rewrites_authored_action():
    f, arm, compiled = case("mug-crop")
    assert len(compiled.prompt.definitions) == 2
    assert compiled.prompt.definitions[1] == "<Subject 2> is the standing performer in <Video 1>."
    assert not any("seated woman" in line for line in compiled.prompt.retention)
    assert f.performers[0].action in compiled.text
    # The research crop_action is not silently substituted for the authored action.
    assert "off screen" not in compiled.text
    bystander = replace(f.tracks[1], box=Box(680, 100, 700, 300))
    intersecting = replace(f, tracks=(f.tracks[0], bystander))
    other = compile_replacement(intersecting, arm)
    assert len(other.prompt.definitions) == 3
    assert "standing performer at screen right" in other.prompt.definitions[1]
    boundary = replace(bystander, box=Box(600, 100, arm.crop.box.x0, 300))
    assert (
        len(compile_replacement(replace(f, tracks=(f.tracks[0], boundary)), arm).prompt.definitions)
        == 2
    )


def test_optional_target_text_and_inversion_follow_picture_order():
    f = joint_facts()
    f = replace(f, performers=tuple(replace(p, target_text=None) for p in f.performers))
    arm = replace(plan(f).arm, treatment="inverted")
    compiled = compile_replacement(f, arm)
    assert compiled.slots == ("target", "target-two")
    assert "Keep those target features" not in compiled.text
    assert ", with " not in compiled.prompt.definitions[0]
    assert (
        "<Subject 3> with <Subject 1> and <Subject 4> with <Subject 2>, each including"
        in compiled.text
    )
    for i in (1, 2):
        assert f"take their appearance from <Picture {i}>." in compiled.text
    assert "The performers keep lips gently closed" in compiled.text
    assert compiled.text.index(f.performers[0].action) < compiled.text.index(f.performers[1].action)


def test_ownership_intervals_and_bar_free_active_framing():
    f = replace(
        mug_facts(),
        occluders=(
            Occluder("The mechanical hand", "scene", "the lower face"),
            Occluder("The raised hand", "performer", "one eye"),
        ),
        performer_hands_visible=False,
        silent_intervals=((36, 60),),
    )
    arm = Arm("rgb", "single", "active", None, 2, 0)
    text = compile_replacement(f, arm).text
    assert "The mechanical hand belongs to the scene and covers the lower face." in text
    assert "The raised hand belongs to the performer and covers one eye." in text
    assert "The performer's own hands stay outside the frame throughout." in text
    assert (
        "From 1.5 to 2.5 seconds the performer's lips stay gently closed; elsewhere the mouth follows the source."
        in text
    )
    assert "letterbox" not in text
    assert (
        "letterbox"
        not in compile_replacement(replace(f, letterbox=None), replace(arm, framing="full")).text
    )


def test_window_padding_and_scope_refusals():
    f = mug_facts(55)
    assert compile_replacement(f, plan(f).arm).window_frames == 56
    f = mug_facts(72)
    assert compile_replacement(f, plan(f).arm).window_frames == 73
    with pytest.raises(CapabilityGap, match="tested"):
        compile_replacement(mug_facts(90), Arm("rgb", "single", "active", None, 1, 0))
    with pytest.raises(ValueError, match="grouping"):
        compile_replacement(joint_facts(), Arm("rgb", "single", "active", None, 1, 0))


def wording_text():
    return resources.files(minimax_h3).joinpath("knowledge/replacement.toml").read_text("utf-8")


def test_pack_is_data_with_no_retired_phrases_or_pilot_vocabulary():
    text = wording_text().lower()
    assert not any(phrase in text for phrase in RETIRED)
    assert not any(word in text for word in ("mug", "burgundy", "chair", "woman", "blue cup"))
    parsed = replacement_wording()
    assert all(
        isinstance(value, str)
        for table in (parsed.sections, parsed.lines, parsed.words)
        for value in table.values()
    )


@pytest.mark.parametrize(
    "old,new",
    [
        ("schema = 1", "schema = 1\nunknown = 1"),
        ("[sections]", "[missing]"),
        ('music = "N/A"', 'music = "N/A"\nunknown = "x"'),
        ('music = "N/A"', "music = 4"),
        ("{target}", "{target.__class__}"),
        ("{target}", "{target!r}"),
        ("Render natural scene colors.", "Render natural scene colors. small head"),
        ("Render natural scene colors.", "If the guide contains photographic-negative colors"),
    ],
)
def test_wording_refuses_unknown_keys_values_placeholders_and_retired_wording(old, new):
    with pytest.raises(ValueError):
        parse_replacement_wording(wording_text().replace(old, new))


@pytest.mark.parametrize("phrase", GENERIC)
def test_pack_cannot_emit_generic_motion(phrase):
    with pytest.raises(ValueError):
        parse_replacement_wording(wording_text().replace("Render natural scene colors.", phrase))


def product_graph(
    name, *, audio_lock=False, context_frames=0, faceswap=False, handoff="none", save_latent=False
):
    research = fixture(name)
    _f, arm, compiled = case("mug-full" if name == "FULL" else "mug-crop")
    compiled = compile_replacement(
        replace(_f, context_frames=context_frames, handoff=handoff),
        replace(arm, audio_lock=audio_lock, faceswap=faceswap),
        save_latent=save_latent,
    )
    graph = build_replacement_graph(
        compiled,
        guide_file=research["inputs"]["source"]["remote_name"],
        picture_files={"target": research["inputs"]["reference"]["remote_name"]},
        seed=research["seed"],
        canvas=arm.crop.canvas if arm.crop else tuple(render_presets().replacement["full_canvas"]),
        output_prefix=f"replacement/{research['id']}",
        context_file="context.mkv" if context_frames else None,
        latent_file="context.safetensors" if handoff == "latent" else None,
        bridge_file="bridge.mkv" if handoff == "bridge" else None,
    )
    return research, graph


@pytest.mark.parametrize("audio_lock", [False, True])
@pytest.mark.parametrize("context_frames", [0, 22])
def test_faceswap_only_adds_the_stacked_lora_edges_and_trigger(audio_lock, context_frames):
    _, baseline = product_graph("CROP", audio_lock=audio_lock, context_frames=context_frames)
    _, stacked = product_graph(
        "CROP", audio_lock=audio_lock, context_frames=context_frames, faceswap=True
    )
    suffix = "-AUDIO-LOCK" if audio_lock else ""
    suffix += "-CHAINED" if context_frames else ""
    snapshot(
        GOLDEN / f"REPL-MUG-CROP-FACESWAP{suffix}.graph.json",
        json.dumps(stacked, indent=2) + "\n",
    )
    if context_frames:
        assert stacked["guider"]["inputs"]["conditioning"] == ["motion_context", 0]
        assert stacked["motion_context"]["inputs"]["conditioning"] == ["conditioning", 0]
        assert stacked["motion_context"]["inputs"]["context_length"] == str(context_frames)
        assert stacked["context_video"]["inputs"]["file"] == "context.mkv"
    if audio_lock:
        assert stacked["guider"]["inputs"]["model"] == ["audio_lock", 0]
        assert stacked["sigmas"]["inputs"]["model"] == ["audio_lock", 0]
    assert stacked.pop("faceswap") == {
        "class_type": "LoraLoaderModelOnly",
        "inputs": {
            "model": ["adapter", 0],
            "lora_name": "SS_FaceSwap_MiniMax_H3_REF2VA.safetensors",
            "strength_model": 1.0,
        },
    }
    consumers = ("audio_lock",) if audio_lock else ("guider", "sigmas")
    for key in consumers:
        assert stacked[key]["inputs"]["model"] == ["faceswap", 0]
        stacked[key]["inputs"]["model"] = ["adapter", 0]
    assert stacked["conditioning"]["inputs"]["prompt"] == (
        "Faceswap. " + baseline["conditioning"]["inputs"]["prompt"]
    )
    stacked["conditioning"]["inputs"]["prompt"] = baseline["conditioning"]["inputs"]["prompt"]
    assert stacked == baseline


@pytest.mark.parametrize("joint", [False, True])
def test_locked_graph_only_adds_the_native_audio_lock_and_three_edges(joint):
    if joint:
        _, _, compiled = case("mug-joint-pair")
        args = {
            "guide_file": "guide.mkv",
            "picture_files": {"target": "first.png", "target-two": "second.png"},
            "seed": 7,
            "output_prefix": "replacement/pair",
        }
        unlocked = build_replacement_graph(compiled, **args)
        locked = build_replacement_graph(replace(compiled, audio_lock=True), **args)
    else:
        _, unlocked = product_graph("CROP")
        _, locked = product_graph("CROP", audio_lock=True)
    framing = "PAIR" if joint else "CROP"
    snapshot(
        GOLDEN / f"REPL-MUG-{framing}-AUDIO-LOCK.graph.json", json.dumps(locked, indent=2) + "\n"
    )
    assert locked.pop("audio_lock") == {
        "class_type": "MiniMaxH3NativeAudioLock",
        "inputs": {
            "model": ["adapter", 0],
            "av_latent": ["conditioning", 1],
            "audio_vae": ["audio_guard", 0],
            "audio": ["components", 1],
        },
    }
    for node, port, expected in (
        ("sample", "latent_image", ["audio_lock", 1]),
        ("guider", "model", ["audio_lock", 0]),
        ("sigmas", "model", ["audio_lock", 0]),
    ):
        assert locked[node]["inputs"][port] == expected
        locked[node]["inputs"][port] = unlocked[node]["inputs"][port]
    assert locked == unlocked
    assert locked["sigmas"]["inputs"]["denoise"] == 1.0


@pytest.mark.parametrize("denoise", [0.5, 0.99])
def test_only_locked_graphs_require_full_denoise(denoise):
    _, _, compiled = case("mug-crop")
    text = resources.files(minimax_h3).joinpath("knowledge/render.toml").read_text("utf-8")
    presets = parse_render(text.replace("denoise = 1.0", f"denoise = {denoise}"))
    args = {
        "guide_file": "guide.mkv",
        "picture_files": {"target": "target.png"},
        "seed": 7,
        "output_prefix": "replacement/test",
        "presets": presets,
    }
    graph = build_replacement_graph(compiled, **args)
    assert graph["sigmas"]["inputs"]["denoise"] == denoise
    with pytest.raises(CapabilityGap, match="The audio lock needs denoise 1.0"):
        build_replacement_graph(replace(compiled, audio_lock=True), **args)


def test_old_request_and_plan_records_advance_and_lower_without_a_lock():
    from test_replacement_contract import POLICY, attempt, result

    from gaiavfx.application.replace_character import _plan, round_render
    from gaiavfx.modules.direction.application import replacement_policy_text
    from gaiavfx.modules.direction.replacement import (
        ReplacementFacts,
        ReplacementPlan,
        from_record,
        to_record,
    )

    facts = mug_facts()
    record = to_record(plan(facts))
    del record["arm"]["audio_lock"]
    fact_record = to_record(facts)
    del fact_record["speaking"]
    del fact_record["context_frames"]
    # The snapshot uses historical policy text and knowledge, not current defaults.
    policy = (
        replacement_policy_text()
        .replace('audio_lock = "speaking"\n', "")
        .replace("speaking_min_open_frames = 12\n", "")
    )
    request = {
        "facts": fact_record,
        "policy": policy,
        "knowledge": {
            "replacement.toml": wording_text(),
            "render.toml": resources.files(minimax_h3)
            .joinpath("knowledge/render.toml")
            .read_text("utf-8"),
        },
    }
    loaded = from_record(ReplacementPlan, record)
    loaded_facts = from_record(ReplacementFacts, request["facts"])
    assert not loaded.arm.audio_lock and not loaded_facts.speaking
    # Even today's enabled policy cannot switch on an existing arm during a retry.
    prior = (attempt(loaded.arm, (result("background_geometry"),)),)
    advanced = _plan(loaded_facts, policy, prior)
    assert advanced.arm.round == 1 and advanced.arm.framing == "active"
    assert advanced.arm.audio_lock is False
    assert plan(loaded_facts, prior=prior, policy=POLICY).arm.audio_lock is False
    for arm in (loaded.arm, advanced.arm):
        _, presets, compiled, _ = round_render(request, arm)
        args = {
            "guide_file": "guide.mkv",
            "picture_files": {"target": "picture.png"},
            "seed": 7,
            "output_prefix": "replacement/legacy",
            "presets": presets,
        }
        graph = build_replacement_graph(compiled, **args)
        assert "audio_lock" not in graph
        assert graph == build_replacement_graph(compile_replacement(facts, arm), **args)


@pytest.mark.parametrize("name", ["FULL", "CROP"])
def test_graph_golden_and_research_inference_parity(name):
    research, graph = product_graph(name)
    snapshot(GOLDEN / f"REPL-MUG-{name}.graph.json", json.dumps(graph, indent=2) + "\n")
    expected = deepcopy(research["graph"])
    actual = deepcopy(graph)
    # Wording shape is intentionally new and covered separately by guide_v1 parity.
    actual["conditioning"]["inputs"]["prompt"] = expected["conditioning"]["inputs"]["prompt"]
    assert actual["adapter"]["class_type"] == "GaiaH3LoraLoader"
    assert actual["adapter"]["inputs"].pop("expected_skipped") == 0
    actual["adapter"]["class_type"] = "LoraLoaderModelOnly"
    release = actual.pop("release_sampling_weights")
    assert release == {
        "class_type": "VRAM_Debug",
        "inputs": {
            "any_input": ["sample", 0],
            "empty_cache": True,
            "gc_collect": True,
            "unload_all_models": True,
        },
    }
    for key in ("decode_video", "decode_audio"):
        assert actual[key]["inputs"]["samples"] == ["release_sampling_weights", 0]
        actual[key]["inputs"]["samples"] = ["sample", 0]
    for key, node in tuple(expected.items()):
        if node["class_type"] in {"SaveLatent", "LTXVSeparateAVLatent"}:
            del expected[key]
    assert signature(actual) == signature(expected)
    assert actual["conditioning"]["inputs"] == expected["conditioning"]["inputs"]
    for key in ("sigmas", "sampler", "save_video", "decode_video", "decode_audio"):
        assert actual[key] == expected[key]
    forbidden = {
        "SaveLatent",
        "GaiaH3MaskedSpan",
        "VAEEncode",
        "MiniMaxH3MotionContextLoadLatent",
        "LTXVSeparateAVLatent",
        "MiniMaxH3AddGuide",
        "SetLatentNoiseMask",
        "MiniMaxH3MotionContext",
        "MiniMaxH3MotionContextSaveLatent",
        "MiniMaxH3SigmaShift",
        "GaiaH3PowerShift",
    }
    assert not {n["class_type"] for n in graph.values()} & forbidden


def test_joint_reference_slots_and_core_loader_switch():
    _f, arm, compiled = case("mug-joint-pair")
    presets = render_presets()
    core = replace(
        presets, replacement={**presets.replacement, "lora_loader": "LoraLoaderModelOnly"}
    )
    g = build_replacement_graph(
        compiled,
        guide_file="guide.mkv",
        picture_files={"target": "first.png", "target-two": "second.png"},
        seed=314159,
        canvas=arm.crop.canvas,
        output_prefix="replacement/test",
        presets=core,
    )
    assert g["reference"]["inputs"]["image"] == "first.png"
    assert g["reference_1"]["inputs"]["image"] == "second.png"
    assert g["conditioning"]["inputs"]["ref_images.ref_image_1"] == ["reference_1", 0]
    assert g["conditioning"]["inputs"]["ref_videos.ref_video_0"] == ["components", 0]
    assert g["adapter"]["class_type"] == "LoraLoaderModelOnly"
    assert "expected_skipped" not in g["adapter"]["inputs"]


def test_render_presets_are_extended_without_changing_segment_graph():
    text = resources.files(minimax_h3).joinpath("knowledge/render.toml").read_text("utf-8")
    parsed = parse_render(text)
    assert parsed.delivery == {"width": 768, "height": 448}
    # Pinned in A3 after the production parity render (.cache/replacement-gate).
    assert (
        parsed.replacement["lora_sha256"]
        == "4b2a3f420ae804c0aa3422761ff84dbd1bf52eef6900ffab6d2e66df63cb4e79"
    )
    assert parsed.replacement["lora_expected_skipped"] == 0
    with pytest.raises(ValueError):
        parse_render(text + "\nunknown = true\n")


def test_new_direction_files_reject_every_forbidden_dependency(tmp_path):
    # Exercise the actual allowlist rule with representative injected imports.
    from test_boundaries import DIRECTION_STDLIB, imported_names

    for module in ("numpy", "gaiavfx.integrations.minimax_h3", "gaiavfx.application"):
        probe = tmp_path / "replacement.py"
        probe.write_text(f"import {module}\n", encoding="utf-8")
        assert not all(
            name.split(".")[0] in DIRECTION_STDLIB or name.startswith("gaiavfx.modules.direction.")
            for name in imported_names(probe)
        )
    source = Path(__file__).parents[1] / "src" / "gaiavfx" / "modules" / "direction"
    for name in ("replacement", "person_crop"):
        text = (source / f"{name}.py").read_text(encoding="utf-8")
        assert not re.search(r"\b(?:H3|MiniMax)\b|<Video|<Picture", text, re.IGNORECASE)
        assert not any(
            isinstance(n, ast.ImportFrom) and n.module and n.module.startswith("scripts")
            for n in ast.walk(ast.parse(text))
        )


@pytest.mark.parametrize(
    "old,new",
    [
        ('lora_loader = "GaiaH3LoraLoader"', 'lora_loader = "UnknownLoader"'),
        (
            'lora_sha256 = "4b2a3f420ae804c0aa3422761ff84dbd1bf52eef6900ffab6d2e66df63cb4e79"',
            'lora_sha256 = "bad"',
        ),
        ("steps = 20", "steps = 0"),
        ("denoise = 1.0", "denoise = nan"),
        ("save_crf = 15.0", "save_crf = true"),
    ],
)
def test_replacement_render_presets_refuse_invalid_values(old, new):
    text = resources.files(minimax_h3).joinpath("knowledge/render.toml").read_text("utf-8")
    with pytest.raises(ValueError):
        parse_render(text.replace(old, new))


def test_knowledge_snapshot_includes_replacement_wording():
    from gaiavfx.application.operations import knowledge_snapshot

    assert knowledge_snapshot(minimax_h3)["replacement.toml"] == wording_text()


def test_pack_refuses_the_research_lighting_sentence():
    sentence = "Keep the sunlight on the performer consistent with <Video 1>, with the same sunlit highlights and shadows on face, hair and clothing as in the source."
    with pytest.raises(ValueError):
        parse_replacement_wording(wording_text().replace("Render natural scene colors.", sentence))


@pytest.mark.parametrize(
    "changes",
    [
        {"canvas": (609, 736)},
        {"canvas": (896, 896)},
        {"seed": -1},
        {"seed": True},
        {"picture_files": {}},
        {"guide_file": ""},
    ],
)
def test_graph_rejects_invalid_inputs(changes):
    _f, arm, compiled = case("mug-crop")
    args = {
        "guide_file": "guide.mkv",
        "picture_files": {"target": "target.png"},
        "seed": 1,
        "canvas": arm.crop.canvas,
        "output_prefix": "replacement/test",
    }
    with pytest.raises(ValueError):
        build_replacement_graph(compiled, **{**args, **changes})


@pytest.mark.parametrize("name", CASES)
def test_compiled_canvas_is_authoritative(name):
    _f, arm, compiled = case(name)
    expected = arm.crop.canvas if arm.crop else tuple(render_presets().replacement["full_canvas"])
    assert compiled.canvas == expected
    args = {
        "guide_file": "guide.mkv",
        "picture_files": {slot: f"{slot}.png" for slot in compiled.slots},
        "seed": 271828,
        "output_prefix": "replacement/canvas",
    }
    graph = build_replacement_graph(compiled, **args)
    assert (
        graph["conditioning"]["inputs"]["width"],
        graph["conditioning"]["inputs"]["height"],
    ) == expected
    assert build_replacement_graph(compiled, canvas=expected, **args) == graph
    alternate = (896, 512) if expected != (896, 512) else (608, 736)
    with pytest.raises(ValueError, match="match the compiled"):
        build_replacement_graph(compiled, canvas=alternate, **args)


def test_active_canvas_and_serial_comma_match_research_wording():
    f = joint_facts()
    arm = Arm("rgb", "joint", "active", None, 1, 0)
    compiled = compile_replacement(f, arm)
    assert compiled.canvas == tuple(render_presets().replacement["full_canvas"])
    assert "the yellow mug, the blue cup, and tableware and furniture" in compiled.text
    assert "letterbox" not in compiled.text


@pytest.mark.parametrize("locked", [False, True])
def test_chained_graph_only_adds_pixel_context_and_conditioning(locked):
    _, original = product_graph("CROP", audio_lock=locked)
    _, chained = product_graph("CROP", audio_lock=locked, context_frames=22)
    name = "REPL-MUG-CROP" + ("-AUDIO-LOCK" if locked else "") + "-CHAIN.graph.json"
    snapshot(GOLDEN / name, json.dumps(chained, indent=2) + "\n")
    assert chained.pop("context_video") == {
        "class_type": "LoadVideo",
        "inputs": {"file": "context.mkv"},
    }
    assert chained.pop("context_components") == {
        "class_type": "GetVideoComponents",
        "inputs": {"video": ["context_video", 0]},
    }
    assert chained.pop("motion_context") == {
        "class_type": "MiniMaxH3MotionContext",
        "inputs": {
            "conditioning": ["conditioning", 0],
            "vae": ["video_vae", 0],
            "latent": ["conditioning", 1],
            "context_length": "22",
            "audio_context_length": 24,
            "context_frames": ["context_components", 0],
        },
    }
    assert chained["guider"]["inputs"]["conditioning"] == ["motion_context", 0]
    chained["guider"]["inputs"]["conditioning"] = ["conditioning", 0]
    assert chained == original
    if locked:
        assert chained["guider"]["inputs"]["model"] == ["audio_lock", 0]
        assert chained["sample"]["inputs"]["latent_image"] == ["audio_lock", 1]


def test_context_file_and_compiled_frames_must_agree():
    _, _, compiled = case("mug-crop")
    args = {
        "guide_file": "guide.mkv",
        "picture_files": {"target": "target.png"},
        "seed": 7,
        "output_prefix": "out",
    }
    with pytest.raises(ValueError, match="context_file"):
        build_replacement_graph(compiled, context_file="context.mkv", **args)
    with pytest.raises(ValueError, match="context_file"):
        build_replacement_graph(replace(compiled, context_frames=22), **args)
    for value in (-1, 73, True):
        with pytest.raises(ValueError, match="context_frames"):
            replace(compiled, context_frames=value)
