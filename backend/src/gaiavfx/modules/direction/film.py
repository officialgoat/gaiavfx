"""Strict, model-free parsing of film analysis policy."""

import math
import tomllib


def parse_film_policy(text: str) -> dict:
    data = tomllib.loads(text)
    if set(data) != {
        "stride",
        "tau",
        "min_faces",
        "min_face_px",
        "detector",
        "scan_chunk_frames",
        "max_upload_bytes",
        "suggest_min",
    }:
        raise ValueError("Invalid film policy keys")
    detector = data["detector"]
    if not isinstance(detector, dict) or set(detector) != {
        "adaptive_threshold",
        "min_scene_len",
        "window_width",
        "min_content_val",
        "downscale",
    }:
        raise ValueError("Invalid film detector keys")
    for values, keys in (
        (data, ("stride", "min_faces", "min_face_px", "scan_chunk_frames", "max_upload_bytes")),
        (detector, ("min_scene_len", "window_width", "downscale")),
    ):
        for key in keys:
            if type(values[key]) is not int or values[key] < 1:
                raise ValueError(f"{key} must be a positive integer")
    for values, key, low, high in (
        (data, "tau", -1, 1),
        (data, "suggest_min", -1, 1),
        (detector, "adaptive_threshold", 0, math.inf),
        (detector, "min_content_val", 0, 255),
    ):
        value = values[key]
        if type(value) not in {float, int} or not math.isfinite(value) or not low <= value <= high:
            raise ValueError(f"Invalid {key}")
    if data["scan_chunk_frames"] > 1200 or data["max_upload_bytes"] > 100000000:
        raise ValueError("Film scan exceeds the chunk or upload limit")
    return data
