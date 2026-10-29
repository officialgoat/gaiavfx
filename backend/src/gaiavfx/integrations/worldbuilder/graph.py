"""Pure graph lowering for a set seeded from a take (ADR 0019 §4); no runtime imports.

One ComfyUI job: the take's frames inside one shot, sampled evenly; SAM 3.1 people tracks on those frames, wired as
the people analysis wires them (integrations do not import each other, so the settings are this integration's own);
Depth Anything 3 multiview geometry (Base) on the same frames; and the product node `GaiaSaveSetGeometry`
(`comfy_nodes/gaiavfx`), which saves them as one `geometry.npz`.
"""

import tomllib
from importlib import resources


def settings() -> dict:
    text = (
        resources.files(__package__).joinpath("knowledge", "take.toml").read_text(encoding="utf-8")
    )
    return tomllib.loads(text)


def sampling(start: int, end: int, count: int) -> tuple[int, int, int]:
    """`count` frames spread evenly over [start, end): the first frame, the range's length and the stride."""
    length = end - start
    if length < 1 or count < 1:
        raise ValueError("A take's shot needs at least one frame.")
    stride = max(1, length // count)
    return start, min(length, stride * count), stride


def take_geometry_graph(source_file: str, *, start: int, end: int, take: dict, prefix: str) -> dict:
    """The graph for `source_file`'s frames [start, end): every `stride`th of them, at most `take["frames"]`, with
    the take settings `take` (a seed passes the ones its request froze)."""
    knobs = take
    graph = {}

    def add(key, kind, **inputs):
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    first, length, stride = sampling(start, end, take["frames"])
    source = add("source", "LoadVideo", file=source_file)
    components = add("components", "GetVideoComponents", video=source)
    shot = add("shot", "ImageFromBatch", image=components, batch_index=first, length=length)
    frames = add(
        "frames",
        "VHS_SelectEveryNthImage",
        images=shot,
        select_every_nth=stride,
        skip_first_images=0,
    )
    people = knobs["people"]
    model = add("people_model", "CheckpointLoaderSimple", ckpt_name=people["checkpoint"])
    text = add("people_text", "CLIPTextEncode", clip=["people_model", 1], text=people["concept"])
    track = add(
        "people_track",
        "SAM3_VideoTrack",
        images=frames,
        model=model,
        conditioning=text,
        detection_threshold=people["threshold"],
        max_objects=people["max_objects"],
        detect_interval=1,
    )
    # Empty object indices: every person SAM found, one mask per frame.
    masks = add("people_masks", "SAM3_TrackToMask", track_data=track, object_indices="")
    geometry = knobs["geometry"]
    da3 = add("da3_model", "LoadDA3Model", model_name=geometry["model"], weight_dtype="default")
    found = add(
        "da3",
        "DA3Inference",
        da3_model=da3,
        image=frames,
        resolution=geometry["resolution"],
        resize_method="upper_bound_resize",
        **{
            "mode": "multiview",
            "mode.ref_view_strategy": geometry["ref_view"],
            "mode.pose_method": geometry["pose_method"],
        },
    )
    add(
        "save",
        "GaiaSaveSetGeometry",
        da3_geometry=found,
        # The grid DA3 ran on, so the node puts its intrinsics on the frames' grid.
        resolution=geometry["resolution"],
        resize_method="upper_bound_resize",
        people=masks,
        filename_prefix=f"{prefix}/geometry",
    )
    return graph
