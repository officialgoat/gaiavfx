"""Pure graph lowering for shot-local SAM 3.1 analysis; no runtime imports."""

import json
import tomllib
from importlib import resources


def build_analysis_graph(source_file, concepts=("person",), points=None, *, frames=None):
    """SAM 3.1 tracks per concept; with ``frames``, also a 106-point face scan of every frame
    (the source mouth series behind the quiet-mouth facts)."""
    settings = tomllib.loads(
        resources.files(__package__).joinpath("knowledge/render.toml").read_text("utf-8")
    )
    graph = {}

    def add(key, kind, **inputs):
        graph[key] = {"class_type": kind, "inputs": inputs}
        return [key, 0]

    source = add("source", "LoadVideo", file=source_file)
    components = add("components", "GetVideoComponents", video=source)
    first = add("first", "ImageFromBatch", image=components, batch_index=0, length=1)
    model = add("model", "CheckpointLoaderSimple", ckpt_name=settings["checkpoint"])
    for index, concept in enumerate(concepts):
        prefix = f"concept_{index}"
        point = (points or {}).get(concept)
        if point:
            detected = add(
                prefix + "_detect",
                "SAM3_Detect",
                model=model,
                image=first,
                threshold=settings["threshold"],
                refine_iterations=2,
                individual_masks=False,
                positive_coords=json.dumps(point["positive"]),
                negative_coords=json.dumps(point["negative"]),
            )
            prompts = {"initial_mask": detected}
        else:
            prompts = {
                "conditioning": add(
                    prefix + "_text", "CLIPTextEncode", clip=["model", 1], text=concept
                )
            }
        track = add(
            prefix + "_track",
            "SAM3_VideoTrack",
            images=components,
            model=model,
            **prompts,
            detection_threshold=settings["threshold"],
            max_objects=1 if point else settings["max_instances"],
            detect_interval=1,
        )
        for instance in range(1 if point else settings["max_instances"]):
            key = f"{prefix}_instance_{instance}"
            mask = add(
                key + "_mask", "SAM3_TrackToMask", track_data=track, object_indices=str(instance)
            )
            pixels = add(key + "_image", "MaskToImage", mask=mask)
            add(key, "SaveImage", images=pixels, filename_prefix=f"gaiavfx/analysis/{key}")
    if frames is not None:
        add(
            "window_faces",
            "GaiaFaceScan",
            video=source_file,
            frames_json=json.dumps(list(range(frames))),
            models_root="",
            det_size=640,
            landmarks=True,
        )
    return graph
