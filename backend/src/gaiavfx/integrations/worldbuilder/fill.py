"""Filling a stage's unseen space (ADR 0020 §4): the frozen settings (`knowledge/fill.toml`) and the still-geometry
graph, pure graph lowering with no runtime imports.

One ComfyUI job per still: MoGe-2 on the probe's still at the probe's own horizontal field of view, SAM 3.1's window
and water masks on the same picture, and the product node `GaiaSaveStillGeometry` (`comfy_nodes/gaiavfx`),
which saves them as one `geometry.npz`: MoGe's z-depth and mask, and the glass the two masks cover.
"""

import tomllib
from importlib import resources


def settings() -> dict:
    text = (
        resources.files(__package__).joinpath("knowledge", "fill.toml").read_text(encoding="utf-8")
    )
    return tomllib.loads(text)


def still_geometry_graph(still_file: str, *, hfov: float, still: dict, prefix: str) -> dict:
    """The graph for one probe still: `still` is the `[still]` settings the build froze."""
    graph = {}

    def add(key, kind, **inputs):
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    picture = add("still", "LoadImage", image=still_file)
    geometry = add(
        "moge",
        "MoGeInference",
        moge_model=add("moge_model", "LoadMoGeModel", model_name=still["moge"]),
        image=picture,
        resolution_level=int(still["resolution_level"]),
        fov_x_degrees=round(float(hfov), 3),
        batch_size=1,
        force_projection=True,
        apply_mask=True,
        refine_steps=0,
    )
    sam = add("sam", "CheckpointLoaderSimple", ckpt_name=still["sam"])
    glass = {}
    for n, concept in enumerate(still["glass"]):
        glass[f"glass_{n}"] = add(
            f"glass_{n}",
            "SAM3_Detect",
            model=sam,
            image=picture,
            threshold=float(still["threshold"]),
            refine_iterations=int(still["refine_iterations"]),
            individual_masks=False,
            conditioning=add(f"glass_{n}_text", "CLIPTextEncode", clip=["sam", 1], text=concept),
        )
    add(
        "save",
        "GaiaSaveStillGeometry",
        moge_geometry=geometry,
        filename_prefix=f"{prefix}/still",
        **glass,
    )
    return graph
