"""A fill probe's still geometry (ADR 0020 §4) as one npz, from plain arrays: no ComfyUI, torch or GAIAVFX imports,
so it is tested on the CPU.

MoGe's geometry for one image carries `depth` (z-depth, (1, H, W) or (H, W); inf where `apply_mask` masked it),
`mask` (its valid pixels) and more; SAM 3's detections are masks (B, H, W), (H, W) or empty when nothing was found.
The npz holds `depth` and `mask` as `lab._consolidate_one` reads them (float32, (1, H, W)) and `glass`, the union of
the detections (bool, (H, W)): windows and water carry reflections, never geometry."""

import numpy as np


def _plane(value, shape) -> np.ndarray:
    array = np.asarray(value, dtype=np.float32)
    if array.size == 0:
        return np.zeros(shape, np.float32)
    array = np.squeeze(array)
    if array.ndim == 3:  # several detections: their union
        array = array.max(0)
    if array.shape != shape:
        raise ValueError("A glass mask and the still differ in size.")
    return array


def still_arrays(depth, mask, glass=()) -> dict:
    depth = np.squeeze(np.asarray(depth, dtype=np.float32))
    mask = np.squeeze(np.asarray(mask, dtype=np.float32))
    if depth.ndim != 2 or mask.shape != depth.shape:
        raise ValueError("MoGe's depth and mask must be one image's, of one size.")
    valid = (mask > 0.5) & np.isfinite(depth) & (depth > 0)
    union = np.zeros(depth.shape, bool)
    for found in glass:
        union |= _plane(found, depth.shape) > 0.5
    return {
        "depth": np.where(valid, depth, 0.0).astype(np.float32)[None],
        "mask": valid.astype(np.float32)[None],
        "glass": union,
    }
