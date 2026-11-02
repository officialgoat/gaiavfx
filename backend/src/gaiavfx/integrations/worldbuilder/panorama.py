"""A stage built from a panorama: the frozen settings (`knowledge/panorama.toml`) and the panorama-geometry graph,
pure graph lowering with no runtime imports.

One ComfyUI job: MoGe on the whole panorama (twelve perspective views merged into one radial distance per pixel), SAM
3.1's sky and glass finds on the same picture, and the product node `GaiaSaveMoGeGeometry` (`comfy_nodes/gaiavfx`),
which saves them as one `geometry.npz`. The stage engine judges the finds (`worldbuilder_splat/panorama.py`)."""

import tomllib
from importlib import resources


def settings() -> dict:
    text = (
        resources.files(__package__)
        .joinpath("knowledge", "panorama.toml")
        .read_text(encoding="utf-8")
    )
    return tomllib.loads(text)


def panorama_geometry_graph(image_file: str, *, settings: dict, prefix: str) -> dict:
    """The graph for one panorama: `settings` is the `knowledge/panorama.toml` the build froze."""
    geometry_settings, masks = settings["geometry"], settings["masks"]
    graph = {}

    def add(key, kind, **inputs):
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    picture = add("panorama", "LoadImage", image=image_file)
    geometry = add(
        "moge",
        "MoGePanoramaInference",
        moge_model=add("moge_model", "LoadMoGeModel", model_name=geometry_settings["moge"]),
        image=picture,
        resolution_level=int(geometry_settings["resolution_level"]),
        split_resolution=int(geometry_settings["split_resolution"]),
        merge_resolution=int(geometry_settings["merge_resolution"]),
        batch_size=int(geometry_settings["batch_size"]),
        refine_steps=int(geometry_settings["refine_steps"]),
    )
    sam = add("sam", "CheckpointLoaderSimple", ckpt_name=masks["sam"])
    finds = {}
    for kind in ("sky", "glass"):
        for n, concept in enumerate(masks[kind]):
            finds[f"{kind}_{n}"] = add(
                f"{kind}_{n}",
                "SAM3_Detect",
                model=sam,
                image=picture,
                threshold=float(masks["threshold"]),
                refine_iterations=int(masks["refine_iterations"]),
                individual_masks=False,
                conditioning=add(
                    f"{kind}_{n}_text", "CLIPTextEncode", clip=["sam", 1], text=concept
                ),
            )
    add(
        "save",
        "GaiaSaveMoGeGeometry",
        moge_geometry=geometry,
        filename_prefix=f"{prefix}/panorama",
        **finds,
    )
    return graph
