"""Six-section replacement wording and reference-only graph lowering. No execution."""

import math
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources
from string import Formatter

from gaiavfx.integrations.minimax_h3 import capabilities
from gaiavfx.integrations.minimax_h3.graph import RenderPresets, render_presets
from gaiavfx.integrations.minimax_h3.prompt import H3Prompt
from gaiavfx.modules.direction.capabilities import CapabilityGap
from gaiavfx.modules.direction.replacement import Arm, ReplacementFacts

# Templates have closed names AND closed parameters; knowledge files are data.
PARAMETERS = {
    "sections": {
        "summary": {"replacements", "including"},
        "soundscape": set(),
        "music": set(),
    },
    "lines": {
        "target_definition": {"target", "picture", "features"},
        "source_definition": {"subject", "selector", "treatment"},
        "target_retention": {"target", "source", "keep"},
        "source_retention": {"source", "target", "inverted"},
        "inverted": {"picture"},
        "bystander_retention": {"subject", "selector", "items"},
        "scene_retention": {"items", "bars"},
        "appearance": {"target", "selector"},
        "occluder": {"name", "owner", "covers"},
        "hands": set(),
        "quiet_single": set(),
        "quiet_joint": set(),
        "silent_interval": {"start", "end", "whose"},
        "colors": set(),
        "shot": {"description"},
    },
    "words": {
        "subject": {"number"},
        "picture": {"number"},
        "features": {"text"},
        "selector": {"role", "position"},
        "inverted": set(),
        "replace": {"source", "target"},
        "including_single": set(),
        "including_joint": set(),
        "keep": set(),
        "list_separator": set(),
        "list_last": set(),
        "list_serial_last": set(),
        "bystander_items": {"items"},
        "scene_items": {"items"},
        "letterbox": set(),
        "owner_scene": set(),
        "owner_performer": set(),
        "whose_single": set(),
        "whose_joint": set(),
    },
}
GENERIC = ("small head", "small eye", "hand movements", "face movements", "head movements")
RETIRED = (
    *GENERIC,
    "if the guide contains photographic-negative colors",
    "keep the sunlight on the performer consistent with",
)


@dataclass(frozen=True)
class ReplacementWording:
    sections: dict[str, str]
    lines: dict[str, str]
    words: dict[str, str]


def parse_replacement_wording(text: str) -> ReplacementWording:
    data = tomllib.loads(text)
    if (
        set(data) != {"schema", *PARAMETERS}
        or type(data["schema"]) is not int
        or data["schema"] != 1
    ):
        raise ValueError("replacement wording needs schema = 1, sections, lines and words")
    for table, expected in PARAMETERS.items():
        if not isinstance(data[table], dict) or set(data[table]) != set(expected):
            raise ValueError(f"invalid {table} keys")
        for name, fields in expected.items():
            value = data[table][name]
            if (
                not isinstance(value, str)
                or not value.strip()
                or any(p in value.lower() for p in RETIRED)
            ):
                raise ValueError(f"invalid or retired wording: {name}")
            parsed = tuple(Formatter().parse(value))
            if {field for _, field, _, _ in parsed if field is not None} != fields or any(
                spec or conv for _, _, spec, conv in parsed
            ):
                raise ValueError(f"invalid template parameters: {name}")
    return ReplacementWording(data["sections"], data["lines"], data["words"])


@cache
def replacement_wording() -> ReplacementWording:
    return parse_replacement_wording(
        resources.files(__package__).joinpath("knowledge/replacement.toml").read_text("utf-8")
    )


@cache
def head_wording() -> ReplacementWording:
    return parse_replacement_wording(
        resources.files(__package__).joinpath("knowledge/head.toml").read_text("utf-8")
    )


@dataclass(frozen=True)
class CompiledReplacement:
    prompt: H3Prompt
    text: str
    slots: tuple[str, ...]
    window_frames: int
    canvas: tuple[int, int]
    audio_lock: bool = False
    context_frames: int = 0
    faceswap: bool = False
    handoff: str = "none"
    save_latent: bool = False

    def __post_init__(self):
        if self.handoff not in {"none", "latent", "bridge"}:
            raise ValueError("invalid handoff")
        if self.handoff != "none" and self.context_frames:
            raise ValueError("handoff cannot also use pixel context")
        if type(self.save_latent) is not bool:
            raise ValueError("save_latent must be boolean")
        if (
            type(self.context_frames) is not int
            or not 0 <= self.context_frames < self.window_frames
        ):
            raise ValueError("context_frames must be inside the render window")
        if type(self.faceswap) is not bool:
            raise ValueError("faceswap must be boolean")
        if type(self.audio_lock) is not bool:
            raise ValueError("audio_lock must be boolean")
        if not isinstance(self.prompt, H3Prompt) or not isinstance(self.text, str):
            raise TypeError("replacement must contain a typed prompt and rendered text")
        if not isinstance(self.slots, tuple) or not 1 <= len(self.slots) <= 2:
            raise ValueError("replacement needs one or two target slots")
        if self.faceswap and len(self.slots) != 1:
            raise ValueError("faceswap requires one target slot")
        support = capabilities().replacement
        if (
            not isinstance(self.canvas, tuple)
            or len(self.canvas) != 2
            or any(
                type(v) is not int
                or not support.canvas_low <= v <= support.canvas_high
                or v % support.canvas_step
                for v in self.canvas
            )
            or math.prod(self.canvas) > support.canvas_budget_pixels
            or not support.canvas_aspect[0]
            <= self.canvas[0] / self.canvas[1]
            <= support.canvas_aspect[1]
        ):
            raise ValueError("canvas must fit the replacement capability")
        if not capabilities().frame_grid.valid_window(self.window_frames):
            raise ValueError("invalid replacement frame window")


def compile_replacement(
    facts: ReplacementFacts,
    arm: Arm,
    words: ReplacementWording | None = None,
    *,
    presets: RenderPresets | None = None,
    save_latent: bool = False,
) -> CompiledReplacement:
    presets = presets or render_presets()
    if arm.faceswap and presets.faceswap is None:
        raise CapabilityGap("faceswap_unsupported", "Render knowledge has no FaceSwap LoRA")
    cap = capabilities()
    support = cap.replacement
    if len(facts.performers) > support.max_performers:
        raise CapabilityGap("too_many_performers", "at most two performers can be lowered")
    if arm.grouping != ("joint" if len(facts.performers) == 2 else "single"):
        raise ValueError("arm grouping must match performers")
    if facts.fps > 30:
        raise CapabilityGap("unsupported_fps", "replacement source frame rate exceeds 30 fps")
    window = cap.frame_grid.window_for(facts.delivered_frames, 0)
    if facts.handoff == "bridge":
        window = 73
    if window > max(support.tested_frames):
        raise CapabilityGap("untested_length", "replacement window exceeds tested lengths")
    if arm.crop and (
        arm.crop.box.x0 < 0
        or arm.crop.box.y0 < 0
        or arm.crop.box.x1 > facts.native[0]
        or arm.crop.box.y1 > facts.native[1]
    ):
        raise ValueError("crop must be inside native frame")
    words = words or (head_wording() if arm.framing == "head" else replacement_wording())
    w, lines = words.words, words.lines

    def join(items):
        return (
            "".join(items)
            if len(items) < 2
            else w["list_separator"].join(items[:-1])
            + (w["list_last"] if len(items) == 2 else w["list_serial_last"])
            + items[-1]
        )

    def subject(number):
        return w["subject"].format(number=number)

    def inside(track):
        if arm.crop is None:
            return True
        a, b = track.box, arm.crop.box
        return a.x0 < b.x1 and a.x1 > b.x0 and a.y0 < b.y1 and a.y1 > b.y0

    tracks = {t.id: t for t in facts.tracks}
    bystanders = tuple(b for b in facts.bystanders if inside(tracks[b.track_id]))
    only_track = arm.crop is not None and sum(inside(t) for t in facts.tracks) == 1
    targets, sources, retained, descriptions, replacements = [], [], [], [], []
    n = len(facts.performers)
    for i, p in enumerate(facts.performers, 1):
        target, source = subject(i), subject(n + i)
        picture = w["picture"].format(number=i)
        selector = p.role if only_track else w["selector"].format(role=p.role, position=p.position)
        targets.append(
            lines["target_definition"].format(
                target=target,
                picture=picture,
                features=w["features"].format(text=p.target_text) if p.target_text else "",
            )
        )
        sources.append(
            lines["source_definition"].format(
                subject=source,
                selector=selector,
                treatment=w["inverted"] if arm.treatment == "inverted" else "",
            )
        )
        retained.append(
            lines["target_retention"].format(
                target=target, source=source, keep=w["keep"] if p.target_text else ""
            )
        )
        retained.append(
            lines["source_retention"].format(
                source=source,
                target=target,
                inverted=lines["inverted"].format(picture=picture)
                if arm.treatment == "inverted"
                else "",
            )
        )
        descriptions.append(lines["appearance"].format(target=target, selector=selector))
        if p.action:
            descriptions.append(p.action)
        replacements.append(w["replace"].format(source=source, target=target))
    for i, b in enumerate(bystanders, 2 * n + 1):
        selector = w["selector"].format(role=b.role, position=b.position)
        sources.append(
            lines["source_definition"].format(subject=subject(i), selector=selector, treatment="")
        )
        retained.append(
            lines["bystander_retention"].format(
                subject=subject(i),
                selector=selector,
                items=w["bystander_items"].format(items=join(b.items)) if b.items else "",
            )
        )
    retained.append(
        lines["scene_retention"].format(
            items=w["scene_items"].format(items=join(facts.scene_items))
            if facts.scene_items
            else "",
            bars=w["letterbox"] if arm.framing == "full" and facts.letterbox else "",
        )
    )
    for occluder in facts.occluders:
        descriptions.append(
            lines["occluder"].format(
                name=occluder.name, owner=w[f"owner_{occluder.owner}"], covers=occluder.covers
            )
        )
    if facts.performer_hands_visible is False:
        descriptions.append(lines["hands"])
    if facts.quiet:
        descriptions.append(lines[f"quiet_{arm.grouping}"])
    for a, b in facts.silent_intervals:
        descriptions.append(
            lines["silent_interval"].format(
                start=format(float(a / cap.frame_grid.fps), ".12g"),
                end=format(float(b / cap.frame_grid.fps), ".12g"),
                whose=w[f"whose_{arm.grouping}"],
            )
        )
    descriptions.append(lines["colors"])
    prompt = H3Prompt(
        definitions=tuple(targets + sources),
        summary=words.sections["summary"].format(
            replacements=join(replacements), including=w[f"including_{arm.grouping}"]
        ),
        retention=tuple(retained),
        description=(),
        shots=(lines["shot"].format(description=" ".join(descriptions)),),
        soundscape=words.sections["soundscape"],
        music=words.sections["music"],
    )
    text = prompt.render()
    if arm.faceswap:
        text = presets.faceswap["trigger"] + ". " + text
    if any(p in text.lower() for p in RETIRED) or (
        arm.framing != "full" and "letterbox" in text.lower()
    ):
        raise ValueError("retired or out-of-frame wording in replacement facts")
    allowed = {
        "<Video 1>",
        *(subject(i) for i in range(1, 2 * n + len(bystanders) + 1)),
        *(w["picture"].format(number=i) for i in range(1, n + 1)),
    }
    if set(re.findall(r"<[^>]*>", text)) - allowed or "[Shot 2" in text:
        raise ValueError("unsupported replacement prompt token")
    return CompiledReplacement(
        prompt,
        text,
        tuple(p.target_asset for p in facts.performers),
        window,
        arm.crop.canvas if arm.crop else tuple(presets.replacement["full_canvas"]),
        audio_lock=arm.audio_lock,
        context_frames=facts.context_frames,
        faceswap=arm.faceswap,
        handoff=facts.handoff,
        save_latent=save_latent,
    )


def latent_steps(frames: int) -> int:
    """H3's 17n + 5 video frames occupy 5n + 2 latent steps."""
    if type(frames) is not int or frames < 5 or (frames - 5) % 17:
        raise ValueError("frames must be on the H3 video grid")
    return 2 + 5 * ((frames - 5) // 17)


def build_replacement_graph(
    compiled: CompiledReplacement,
    *,
    guide_file: str,
    picture_files: Mapping[str, str],
    seed: int,
    canvas: tuple[int, int] | None = None,
    output_prefix: str,
    presets: RenderPresets | None = None,
    context_file: str | None = None,
    latent_file: str | None = None,
    bridge_file: str | None = None,
) -> dict:
    """Lower uploaded filenames to a graph; never read, submit or persist them."""
    presets = presets or render_presets()
    if (context_file is not None) != (compiled.context_frames > 0) or context_file == "":
        raise ValueError("context_file is required exactly when context_frames is positive")
    for name, value, kind in (
        ("latent_file", latent_file, "latent"),
        ("bridge_file", bridge_file, "bridge"),
    ):
        if (value is not None) != (compiled.handoff == kind) or value == "":
            raise ValueError(f"{name} is required exactly for the {kind} handoff")
    models, repl = presets.models, presets.replacement
    if compiled.faceswap and presets.faceswap is None:
        raise CapabilityGap("faceswap_unsupported", "Render knowledge has no FaceSwap LoRA")
    if compiled.audio_lock and repl["denoise"] != 1.0:
        raise CapabilityGap("audio_lock_denoise", "The audio lock needs denoise 1.0")
    support = capabilities().replacement
    if canvas is not None and canvas != compiled.canvas:
        raise ValueError("explicit canvas must match the compiled replacement canvas")
    canvas = compiled.canvas
    if type(seed) is not int or not 0 <= seed < 2**64:
        raise ValueError("seed must be an unsigned 64-bit integer")
    if (
        not guide_file
        or not output_prefix
        or any(not picture_files.get(asset) for asset in compiled.slots)
    ):
        raise ValueError("guide, targets and output prefix are required")
    if compiled.window_frames > max(support.tested_frames):
        raise CapabilityGap("untested_length", "replacement window exceeds tested lengths")
    graph = {}

    def add(key, kind, **inputs):
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    model = add("model", "UNETLoader", unet_name=models["unet"], weight_dtype="default")
    extra = (
        {"expected_skipped": repl["lora_expected_skipped"]}
        if repl["lora_loader"] == "GaiaH3LoraLoader"
        else {}
    )
    adapter = add(
        "adapter",
        repl["lora_loader"],
        model=model,
        lora_name=repl["lora"],
        strength_model=repl["lora_strength"],
        **extra,
    )
    if compiled.faceswap:
        adapter = add(
            "faceswap",
            "LoraLoaderModelOnly",
            model=adapter,
            lora_name=presets.faceswap["lora_name"],
            strength_model=presets.faceswap["strength"],
        )
    clip = add("encoder", "CLIPLoader", clip_name=models["clip"], type="minimax", device="default")
    vae = add("video_vae", "VAELoader", vae_name=models["video_vae"])
    audio = add(
        "audio_guard",
        "GaiaH3AudioVAE",
        audio_vae=add("audio_vae", "VAELoader", vae_name=models["audio_vae"]),
    )
    video = add(
        "components", "GetVideoComponents", video=add("source", "LoadVideo", file=guide_file)
    )
    references = {
        f"ref_images.ref_image_{i}": add(
            "reference" if i == 0 else f"reference_{i}", "LoadImage", image=picture_files[asset]
        )
        for i, asset in enumerate(compiled.slots)
    }
    cond = add(
        "conditioning",
        "MiniMaxH3ReferenceToVideo",
        clip=clip,
        vae=vae,
        audio_vae=audio,
        prompt=compiled.text,
        width=canvas[0],
        height=canvas[1],
        length=compiled.window_frames,
        ref_image_size=presets.conditioning["ref_image_size"],
        **{"ref_videos.ref_video_0": video},
        **references,
    )
    latent = ["conditioning", 1]
    if compiled.audio_lock:
        adapter = add(
            "audio_lock",
            "MiniMaxH3NativeAudioLock",
            model=adapter,
            av_latent=latent,
            audio_vae=audio,
            audio=["components", 1],
        )
        latent = ["audio_lock", 1]
    if compiled.context_frames:
        context = add(
            "context_components",
            "GetVideoComponents",
            video=add("context_video", "LoadVideo", file=context_file),
        )
        cond = add(
            "motion_context",
            "MiniMaxH3MotionContext",
            conditioning=cond,
            vae=vae,
            latent=["conditioning", 1],
            context_length=str(compiled.context_frames),
            audio_context_length=presets.conditioning["audio_context_frames"],
            context_frames=context,
        )
    if compiled.handoff != "none":
        steps = latent_steps(capabilities().frame_grid.context_default)
        if compiled.handoff == "latent":
            source = add(
                "load_prev",
                "MiniMaxH3MotionContextLoadLatent",
                latent_path=latent_file,
                clip_index=1,
            )
        else:
            pixels = add(
                "bridge_components",
                "GetVideoComponents",
                video=add("bridge_video", "LoadVideo", file=bridge_file),
            )
            source = add("bridge_encode", "VAEEncode", pixels=pixels, vae=vae)
        latent = add(
            "span",
            "GaiaH3MaskedSpan",
            latent=latent,
            source=source,
            mode="tail_to_head" if compiled.handoff == "latent" else "aligned",
            head_steps=steps,
            tail_steps=steps if compiled.handoff == "bridge" else 0,
        )
    sampled = add(
        "sample",
        "SamplerCustomAdvanced",
        noise=add("noise", "RandomNoise", noise_seed=seed),
        guider=add("guider", "BasicGuider", model=adapter, conditioning=cond),
        sampler=add("sampler", "KSamplerSelect", sampler_name=repl["sampler"]),
        sigmas=add(
            "sigmas",
            "BasicScheduler",
            model=adapter,
            scheduler=repl["scheduler"],
            steps=repl["steps"],
            denoise=repl["denoise"],
        ),
        latent_image=latent,
    )
    if compiled.save_latent:
        add(
            "save_latent",
            "MiniMaxH3MotionContextSaveLatent",
            latent=sampled,
            filename_prefix=f"{output_prefix}/context",
            clip_index=1,
        )
    released = add(
        "release_sampling_weights",
        "VRAM_Debug",
        any_input=sampled,
        empty_cache=True,
        gc_collect=True,
        unload_all_models=True,
    )
    images = add("decode_video", "VAEDecodeTiled", samples=released, vae=vae, **presets.decode)
    sound = add("decode_audio", "VAEDecodeAudio", samples=released, vae=audio)
    raw = add(
        "raw_video",
        "CreateVideo",
        images=images,
        audio=sound,
        fps=float(capabilities().frame_grid.fps),
    )
    add(
        "save_video",
        "SaveVideo",
        video=raw,
        filename_prefix=f"{output_prefix}/raw",
        format="mp4",
        codec="h264",
        **{"format.codec.encoding": "re-encode", "format.codec.encoding.crf": repl["save_crf"]},
    )
    return graph
