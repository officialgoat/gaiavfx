"""Lower panorama work to the tested ComfyUI graphs: pure graph construction with no runtime imports. `render` is the
`knowledge/render.toml` a request froze."""

import tomllib
from functools import cache
from importlib import resources


def parse_render(text: str) -> dict:
    data = tomllib.loads(text)
    tables = {"krea", "fibo", "plate", "candidates", "seam", "estimate"}
    if data.get("schema") != 1 or set(data) != {"schema", *tables}:
        raise ValueError("render settings require schema = 1 and " + ", ".join(sorted(tables)))
    seeds = data["candidates"].get("seeds")
    if not isinstance(seeds, list) or len(seeds) != 2 or len(set(seeds)) != 2:
        raise ValueError("[candidates] seeds holds two different seeds")
    return data


@cache
def _render_text() -> str:
    return resources.files(__package__).joinpath("knowledge/render.toml").read_text("utf-8")


def render() -> dict:
    return parse_render(_render_text())


def _graph():
    graph: dict = {}

    def add(key, kind, **inputs):
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    return graph, add


def text_to_panorama_graph(prompt: str, *, seed: int, prefix: str, render: dict) -> dict:
    """Krea 2 Turbo with the 360 text-to-image LoRA: one 2:1 panorama."""
    krea = render["krea"]
    graph, add = _graph()
    model = add("model", "UNETLoader", unet_name=krea["unet"], weight_dtype="default")
    model = add(
        "lora_0",
        "LoraLoaderModelOnly",
        model=model,
        lora_name=krea["lora"],
        strength_model=float(krea["lora_strength"]),
    )
    clip = add(
        "clip", "CLIPLoader", clip_name=krea["clip"], type=krea["clip_type"], device="default"
    )
    positive = add("positive", "CLIPTextEncode", clip=clip, text=prompt)
    negative = add("negative", "ConditioningZeroOut", conditioning=positive)
    latent = add(
        "latent",
        "EmptyLatentImage",
        width=int(krea["width"]),
        height=int(krea["height"]),
        batch_size=1,
    )
    sampled = add(
        "sample",
        "KSampler",
        model=model,
        seed=int(seed),
        steps=int(krea["steps"]),
        cfg=float(krea["cfg"]),
        sampler_name=krea["sampler"],
        scheduler=krea["scheduler"],
        positive=positive,
        negative=negative,
        latent_image=latent,
        denoise=1.0,
    )
    vae = add("vae", "VAELoader", vae_name=krea["vae"])
    decoded = add("decode", "VAEDecode", samples=sampled, vae=vae)
    add("save", "SaveImage", images=decoded, filename_prefix=f"{prefix}/panorama")
    return graph


def fibo_edit_graph(prompt: str, *, image_file: str, seed: int, prefix: str, render: dict) -> dict:
    """Fibo 2.0 Edit Turbo on one picture named <image_1> in the instruction (a seam band or a picture's canvas)."""
    fibo = render["fibo"]
    graph, add = _graph()
    model = add("model", "UNETLoader", unet_name=fibo["unet"], weight_dtype="default")
    clip = add(
        "clip", "CLIPLoader", clip_name=fibo["clip"], type=fibo["clip_type"], device="default"
    )
    vae = add("vae", "VAELoader", vae_name=fibo["vae"])
    image = add("image_1", "LoadImage", image=image_file)
    add(
        "encode",
        "TextEncodeFibo2",
        clip=clip,
        prompt=prompt,
        negative_prompt="",
        vae=vae,
        width=int(fibo["width"]),
        height=int(fibo["height"]),
        batch_size=1,
        **{"images.image_1": image},
    )
    sampled = add(
        "sample",
        "KSampler",
        model=model,
        seed=int(seed),
        steps=int(fibo["steps"]),
        cfg=float(fibo["cfg"]),
        sampler_name=fibo["sampler"],
        scheduler=fibo["scheduler"],
        positive=["encode", 0],
        negative=["encode", 1],
        latent_image=["encode", 2],
        denoise=1.0,
    )
    decoded = add("decode", "VAEDecode", samples=sampled, vae=vae)
    add("save", "SaveImage", images=decoded, filename_prefix=f"{prefix}/fill")
    return graph


def plate_geometry_graph(image_file: str, *, prefix: str, render: dict) -> dict:
    """MoGe-3 on one picture with its field of view left to estimate, saved by `GaiaSaveMoGeGeometry`."""
    plate = render["plate"]
    graph, add = _graph()
    picture = add("picture", "LoadImage", image=image_file)
    geometry = add(
        "moge",
        "MoGeInference",
        moge_model=add("moge_model", "LoadMoGeModel", model_name=plate["moge"]),
        image=picture,
        resolution_level=int(plate["resolution_level"]),
        fov_x_degrees=0.0,
        batch_size=1,
        force_projection=True,
        apply_mask=True,
        refine_steps=int(plate["refine_steps"]),
    )
    add("save", "GaiaSaveMoGeGeometry", moge_geometry=geometry, filename_prefix=f"{prefix}/plate")
    return graph
