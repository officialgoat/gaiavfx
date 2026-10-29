"""Lower Qwen image edits to the tested ComfyUI graph."""

import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from functools import cache
from importlib import resources


@dataclass(frozen=True)
class RenderPresets:
    models: dict
    image: dict
    sampling: dict


RENDER_KEYS = {
    "models": {"unet", "lora", "lora_strength", "clip", "clip_type", "vae"},
    "image": {"upscale_method", "megapixels", "resolution_steps", "width", "height"},
    "sampling": {"shift", "sampler", "scheduler", "steps", "cfg", "denoise"},
}


def parse_render(text: str) -> RenderPresets:
    data = tomllib.loads(text)
    if data.get("schema") != 1 or set(data) != {"schema", *RENDER_KEYS}:
        raise ValueError("render presets require schema = 1 and [models], [image], [sampling]")
    for table, keys in RENDER_KEYS.items():
        if set(data[table]) != keys:
            raise ValueError(f"[{table}] must hold exactly {sorted(keys)}")
    return RenderPresets(data["models"], data["image"], data["sampling"])


@cache
def render_presets() -> RenderPresets:
    return parse_render(
        resources.files(__package__).joinpath("knowledge/render.toml").read_text("utf-8")
    )


def build_edit_graph(
    prompt: str,
    *,
    image_files: Sequence[str],
    seed: int,
    width: int,
    height: int,
    output_prefix: str,
    presets: RenderPresets | None = None,
) -> dict:
    """`image_files` is view first and subject still second for insertion."""
    if not 1 <= len(image_files) <= 3 or not all(image_files):
        raise ValueError("Qwen edits require one to three image files")
    if width <= 0 or height <= 0:
        raise ValueError("image dimensions must be positive")
    presets = presets or render_presets()
    models, image, sampling = presets.models, presets.image, presets.sampling
    graph: dict = {}

    def add(key, kind, **inputs):
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    model = add("model", "UNETLoader", unet_name=models["unet"], weight_dtype="default")
    lora = add(
        "lora_0",
        "LoraLoaderModelOnly",
        model=model,
        lora_name=models["lora"],
        strength_model=models["lora_strength"],
    )
    encoder = add(
        "encoder",
        "CLIPLoader",
        clip_name=models["clip"],
        type=models["clip_type"],
        device="default",
    )
    vae = add("vae", "VAELoader", vae_name=models["vae"])
    shifted = add("shift", "ModelSamplingAuraFlow", model=lora, shift=sampling["shift"])
    refs = {}
    for index, file in enumerate(image_files, 1):
        loaded = add(f"image_{index}", "LoadImage", image=file)
        refs[f"image{index}"] = add(
            f"scaled_{index}",
            "ImageScaleToTotalPixels",
            image=loaded,
            upscale_method=image["upscale_method"],
            megapixels=image["megapixels"],
            resolution_steps=image["resolution_steps"],
        )
    positive = add(
        "positive", "TextEncodeQwenImageEditPlus", clip=encoder, prompt=prompt, vae=vae, **refs
    )
    negative = add(
        "negative", "TextEncodeQwenImageEditPlus", clip=encoder, prompt="", vae=vae, **refs
    )
    latent = add("latent", "EmptySD3LatentImage", width=width, height=height, batch_size=1)
    sampled = add(
        "sample",
        "KSampler",
        model=shifted,
        seed=seed,
        steps=sampling["steps"],
        cfg=sampling["cfg"],
        sampler_name=sampling["sampler"],
        scheduler=sampling["scheduler"],
        positive=positive,
        negative=negative,
        latent_image=latent,
        denoise=sampling["denoise"],
    )
    decoded = add("decode", "VAEDecode", samples=sampled, vae=vae)
    add("save", "SaveImage", images=decoded, filename_prefix=output_prefix)
    return graph
