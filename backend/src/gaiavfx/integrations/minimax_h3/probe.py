"""Compile a fill probe (ADR 0020 §4) and lower it to the research's H3 I2VA memory-probe graph: FL2VA with the first
frame only, the Fun ControlNet Union over the reprojected memory, its regenerate mask and the splat's depth."""

import tomllib
from dataclasses import dataclass
from functools import cache
from importlib import resources

from gaiavfx.integrations.minimax_h3.graph import render_presets
from gaiavfx.modules.sets.probe import ProbeRequest

WORDING_KEYS = {
    "reference",
    "opening",
    "step",
    "turn",
    "hold",
    "continuity",
    "place",
    "sound",
    "music",
}
SAMPLING_KEYS = {"steps", "strength", "start_percent", "end_percent", "patch"}


@dataclass(frozen=True)
class ProbeKnowledge:
    sampling: dict
    wording: dict


def parse_probe_knowledge(text: str) -> ProbeKnowledge:
    data = tomllib.loads(text)
    if data.get("schema") != 1 or set(data) != {"schema", "sampling", "wording"}:
        raise ValueError("probe knowledge has schema = 1, [sampling] and [wording] only")
    if set(data["wording"]) != WORDING_KEYS or set(data["sampling"]) != SAMPLING_KEYS:
        raise ValueError(
            f"[wording] holds {sorted(WORDING_KEYS)}; [sampling] holds {sorted(SAMPLING_KEYS)}"
        )
    return ProbeKnowledge(data["sampling"], data["wording"])


@cache
def probe_knowledge() -> ProbeKnowledge:
    return parse_probe_knowledge(
        resources.files(__package__).joinpath("knowledge/probe.toml").read_text("utf-8")
    )


def compile_probe(request: ProbeRequest, knowledge: ProbeKnowledge | None = None) -> str:
    """The probe's prompt: the research's memory-probe form, its camera sentence the request's move."""
    words = (knowledge or probe_knowledge()).wording
    camera = (
        words["turn"].format(side="right" if request.turn > 0 else "left")
        if request.move == "turn"
        else words[request.move]
    )
    place = request.place.strip().rstrip(".")
    description = " ".join(
        (words["opening"], camera, words["continuity"], words["place"].format(place=place))
    )
    return (
        f"{words['reference']}\n\n"
        f"integrated_multimodal_description: {description}\n\n"
        f"overall_soundscape: {words['sound']}\n\n"
        f"non_diegetic_music: {words['music']}"
    )


def build_probe_graph(
    request: ProbeRequest,
    prompt: str,
    *,
    first_frame_file: str,
    memory_file: str,
    regen_file: str,
    depth_file: str,
    fps: int,
    output_prefix: str,
    knowledge: ProbeKnowledge | None = None,
) -> dict:
    """The memory probe as the research submitted it: research-only instrumentation omitted, nothing else changed."""
    presets = render_presets()
    models, sampling, decode = presets.models, presets.sampling, presets.decode
    probe = (knowledge or probe_knowledge()).sampling
    graph: dict = {}

    def add(key, kind, **inputs):
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    def frames(key, file):
        parts = add(
            f"{key}_parts", "GetVideoComponents", video=add(f"{key}_file", "LoadVideo", file=file)
        )
        return add(key, "ImageFromBatch", image=parts, batch_index=0, length=request.length)

    model = add("model", "UNETLoader", unet_name=models["unet_first_frame"], weight_dtype="default")
    clip = add("encoder", "CLIPLoader", clip_name=models["clip"], type="minimax", device="default")
    vae = add("video_vae", "VAELoader", vae_name=models["video_vae"])
    audio_vae = add(
        "audio_vae_guard",
        "GaiaH3AudioVAE",
        audio_vae=add("audio_vae", "VAELoader", vae_name=models["audio_vae"]),
    )
    conditioning = add(
        "conditioning",
        "MiniMaxH3ImageToVideo",
        clip=clip,
        vae=vae,
        prompt=prompt,
        width=request.width,
        height=request.height,
        length=request.length,
        first_frame=add("first", "LoadImage", image=first_frame_file),
    )
    shifted = add(
        "shift",
        "MiniMaxH3SigmaShift",
        model=model,
        shift_video=sampling["shift_video"],
        shift_audio=sampling["shift_audio"],
    )
    fun = add(
        "fun",
        "MiniMaxH3FunControlNetApply",
        model=shifted,
        model_patch=add("fun_patch", "ModelPatchLoader", name=probe["patch"]),
        vae=vae,
        strength=float(probe["strength"]),
        start_percent=float(probe["start_percent"]),
        end_percent=float(probe["end_percent"]),
        control_video=frames("control", depth_file),
        mask=add("holes_mask", "ImageToMask", image=frames("holes", regen_file), channel="red"),
        source_video=frames("source", memory_file),
    )
    sampled = add(
        "sample",
        "SamplerCustomAdvanced",
        noise=add("noise", "RandomNoise", noise_seed=request.seed),
        guider=add("guider", "BasicGuider", model=fun, conditioning=conditioning),
        sampler=add("sampler", "KSamplerSelect", sampler_name=sampling["sampler"]),
        sigmas=add(
            "sigmas",
            "GaiaH3PowerShift",
            model=shifted,
            steps=int(probe["steps"]),
            power=sampling["power"],
            midpoint_shift=sampling["midpoint_shift"],
        ),
        latent_image=["conditioning", 1],
    )
    released = add(
        "release",
        "VRAM_Debug",
        any_input=sampled,
        empty_cache=True,
        gc_collect=True,
        unload_all_models=True,
    )
    images = add("decode_video", "VAEDecodeTiled", samples=released, vae=vae, **decode)
    audio = add("decode_audio", "VAEDecodeAudio", samples=released, vae=audio_vae)
    video = add("video", "CreateVideo", images=images, audio=audio, fps=float(fps))
    add(
        "save_video",
        "SaveVideo",
        video=video,
        filename_prefix=f"{output_prefix}/video",
        format="mp4",
        codec="h264",
    )
    return graph
