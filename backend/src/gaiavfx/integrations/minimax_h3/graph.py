"""Lowering a compiled H3 prompt to a ComfyUI graph (ADR 0010).

Pictures take reference slots at `ref_image_size=match`; each keyframe is a `MiniMaxH3AddGuide` at its model-window
frame, chained before the motion context; a continuation loads the predecessor's context latent, hands it over by
paired latents (`MiniMaxH3MotionContext`) or masked handoff (`GaiaH3ContinuityMasked`), and trims the context
frames from the delivered clip. Model files and sampling come from `knowledge/render.toml`.
"""

import math
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from functools import cache
from importlib import resources

from gaiavfx.integrations.minimax_h3.continuation import MASKED, METHODS, PAIRED, Handoff
from gaiavfx.integrations.minimax_h3.prompt import CompiledH3

RENDER_KEYS = {
    "models": {"unet", "unet_first_frame", "clip", "video_vae", "audio_vae"},
    "sampling": {"sampler", "steps", "power", "midpoint_shift", "shift_video", "shift_audio"},
    "conditioning": {"ref_image_size", "audio_context_frames"},
    "decode": {"tile_size", "overlap", "temporal_size", "temporal_overlap"},
    "delivery": {"width", "height"},
    "replacement": {
        "lora_loader",
        "lora",
        "lora_sha256",
        "lora_strength",
        "lora_expected_skipped",
        "sampler",
        "scheduler",
        "steps",
        "denoise",
        "full_canvas",
        "save_crf",
    },
}


@dataclass(frozen=True)
class RenderPresets:
    models: dict
    sampling: dict
    conditioning: dict
    decode: dict
    delivery: dict
    replacement: dict
    faceswap: dict | None = None


def parse_render(text: str) -> RenderPresets:
    data = tomllib.loads(text)
    if data.get("schema") != 1 or (set(data) - {"faceswap"}) != {"schema", *RENDER_KEYS}:
        raise ValueError(
            "render presets have schema = 1 and [models], [sampling], [conditioning], [decode], [delivery], [replacement]"
        )
    for table, keys in RENDER_KEYS.items():
        if not isinstance(data[table], dict) or set(data[table]) != keys:
            raise ValueError(f"[{table}] must hold exactly {sorted(keys)}")
    faceswap = data.get("faceswap")
    if faceswap is not None:
        if not isinstance(faceswap, dict) or set(faceswap) - {"sha256"} != {
            "lora_name",
            "strength",
            "trigger",
        }:
            raise ValueError("faceswap needs lora_name, strength and trigger")
        for name in ("lora_name", "trigger"):
            value = faceswap[name]
            if not isinstance(value, str) or not value.strip() or any(c in value for c in "<>\r\n"):
                raise ValueError(f"invalid faceswap {name}")
        sha = faceswap.get("sha256")
        if "sha256" in faceswap and (
            not isinstance(sha, str) or not re.fullmatch(r"[0-9a-f]{64}", sha)
        ):
            raise ValueError("faceswap sha256 must be a sha256")
        strength = faceswap["strength"]
        if (
            type(strength) not in {int, float}
            or not math.isfinite(strength)
            or not 0 < strength <= 1
        ):
            raise ValueError("invalid faceswap strength")
    replacement = data["replacement"]
    if replacement["lora_loader"] not in {"GaiaH3LoraLoader", "LoraLoaderModelOnly"}:
        raise ValueError("unsupported replacement LoRA loader")
    if replacement["sampler"] != "res_multistep" or replacement["scheduler"] != "simple":
        raise ValueError("unsupported replacement sampling")
    if not isinstance(replacement["lora"], str) or not replacement["lora"].strip():
        raise ValueError("replacement needs a LoRA filename")
    sha = replacement["lora_sha256"]
    if not isinstance(sha, str) or (sha and not re.fullmatch(r"[0-9a-f]{64}", sha)):
        raise ValueError("replacement LoRA hash must be empty or a sha256")
    for name, minimum in (("steps", 1), ("lora_expected_skipped", -1)):
        if type(replacement[name]) is not int or replacement[name] < minimum:
            raise ValueError(f"invalid replacement {name}")
    for name, low, high in (("lora_strength", 0, 1), ("denoise", 0, 1), ("save_crf", 0, 51)):
        value = replacement[name]
        if type(value) not in {int, float} or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"invalid replacement {name}")
    canvas = replacement["full_canvas"]
    if (
        not isinstance(canvas, list)
        or len(canvas) != 2
        or any(type(v) is not int or v < 1 for v in canvas)
    ):
        raise ValueError("replacement full_canvas needs two positive dimensions")
    return RenderPresets(
        data["models"],
        data["sampling"],
        data["conditioning"],
        data["decode"],
        data["delivery"],
        data["replacement"],
        faceswap,
    )


def context_descriptor(output_prefix: str) -> dict[str, str]:
    return {
        "output_key": "context",
        "filename": "context_00001.safetensors",
        "subfolder": output_prefix,
        "type": "output",
    }


@cache
def render_presets() -> RenderPresets:
    return parse_render(
        resources.files(__package__).joinpath("knowledge/render.toml").read_text("utf-8")
    )


def build_graph(
    compiled: CompiledH3,
    *,
    files: Mapping[str, str],
    seed: int,
    width: int,
    height: int,
    fps: int,
    context_latent: str | None,
    output_prefix: str,
    capture_context: bool = True,
    presets: RenderPresets | None = None,
    handoff: Handoff | None = None,
) -> dict:
    """`files` maps each asset id to the file name the ComfyUI server loads it by.

    A continuation hands over as `handoff` says (continuation.py); without one it uses paired latents with the
    presets' independent audio window.
    """
    presets = presets or render_presets()
    models, sampling, conditioning_presets, decode = (
        presets.models,
        presets.sampling,
        presets.conditioning,
        presets.decode,
    )
    method = handoff.method if handoff else PAIRED
    audio_context = (
        handoff.audio_context_frames if handoff else conditioning_presets["audio_context_frames"]
    )
    if context_latent is not None:
        if method not in METHODS:
            raise ValueError(f"unknown continuation method {method!r}")
        if handoff and handoff.context_frames != compiled.context_frames:
            raise ValueError("the handoff's context differs from the compiled window's")
        # The audio coupling is part of the method: masked re-encodes exactly the picture context's audio.
        if method == MASKED and audio_context != compiled.context_frames:
            raise ValueError("masked handoff ties the audio history to the picture context")
        if method == MASKED and compiled.keyframes:
            raise ValueError("masked handoff does not combine with pinned keyframes")
    graph: dict = {}

    def add(key, kind, **inputs):
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    model = add("model", "UNETLoader", unet_name=models["unet"], weight_dtype="default")
    clip = add("encoder", "CLIPLoader", clip_name=models["clip"], type="minimax", device="default")
    vae = add("video_vae", "VAELoader", vae_name=models["video_vae"])
    audio_vae = add(
        "audio_vae_guard",
        "GaiaH3AudioVAE",
        audio_vae=add("audio_vae", "VAELoader", vae_name=models["audio_vae"]),
    )
    references = {
        f"ref_images.ref_image_{slot}": add(f"picture_{slot}", "LoadImage", image=files[asset_id])
        for slot, asset_id in enumerate(compiled.slots)
    }
    conditioning = add(
        "conditioning",
        "MiniMaxH3ReferenceToVideo",
        clip=clip,
        vae=vae,
        prompt=compiled.text,
        width=width,
        height=height,
        length=compiled.window_frames,
        audio_vae=audio_vae,
        ref_image_size=conditioning_presets["ref_image_size"],
        **references,
    )
    latent = ["conditioning", 1]
    for index, (asset_id, frame) in enumerate(compiled.keyframes):
        image = add(f"keyframe_image_{index}", "LoadImage", image=files[asset_id])
        conditioning = add(
            f"keyframe_{index}",
            "MiniMaxH3AddGuide",
            positive=conditioning,
            latent=latent,
            vae=vae,
            image=image,
            frame_idx=frame,
        )
    trim_frames = compiled.context_frames
    if context_latent is not None:
        loaded = add(
            "load_context",
            "MiniMaxH3MotionContextLoadLatent",
            latent_path=context_latent,
            clip_index=1,
        )
        if method == MASKED:
            # The node protects the source's latent tail in the target and patches the model's audio positions;
            # its four outputs replace the model, the conditioning, the target latent and the trim.
            add(
                "masked_context",
                "GaiaH3ContinuityMasked",
                model=model,
                positive=conditioning,
                latent=latent,
                context_latent=loaded,
                audio_vae=audio_vae,
                context_length=compiled.context_frames,
                fps=float(fps),
            )
            model, conditioning, latent = (["masked_context", n] for n in range(3))
            trim_frames = ["masked_context", 3]
        else:
            conditioning = add(
                "motion_context",
                "MiniMaxH3MotionContext",
                conditioning=conditioning,
                latent=latent,
                vae=vae,
                audio_vae=audio_vae,
                context_latent=loaded,
                context_length=str(compiled.context_frames),
                audio_context_length=audio_context,
            )
    shifted = add(
        "shift",
        "MiniMaxH3SigmaShift",
        model=model,
        shift_video=sampling["shift_video"],
        shift_audio=sampling["shift_audio"],
    )
    sampled = add(
        "sample",
        "SamplerCustomAdvanced",
        noise=add("noise", "RandomNoise", noise_seed=seed),
        guider=add("guider", "BasicGuider", model=shifted, conditioning=conditioning),
        sampler=add("sampler", "KSamplerSelect", sampler_name=sampling["sampler"]),
        sigmas=add(
            "sigmas",
            "GaiaH3PowerShift",
            model=shifted,
            steps=sampling["steps"],
            power=sampling["power"],
            midpoint_shift=sampling["midpoint_shift"],
        ),
        latent_image=latent,
    )
    if capture_context:
        add(
            "save_context",
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
    images = add("decode_video", "VAEDecodeTiled", samples=released, vae=vae, **decode)
    audio = add("decode_audio", "VAEDecodeAudio", samples=released, vae=audio_vae)
    if context_latent is not None:
        add(
            "trim",
            "GaiaH3ContinuityTrim",
            images=images,
            audio=audio,
            trim_frames=trim_frames,
            fps=float(fps),
        )
        images, audio = ["trim", 0], ["trim", 1]
    video = add("video", "CreateVideo", images=images, audio=audio, fps=float(fps))
    add(
        "save_video",
        "SaveVideo",
        video=video,
        filename_prefix=f"{output_prefix}/delivered",
        format="mp4",
        codec="h264",
    )
    return graph
