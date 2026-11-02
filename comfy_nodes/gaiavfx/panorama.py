"""MoGe's geometry of a panorama or of one picture, with SAM 3's sky and glass finds, as one npz from plain arrays: no
ComfyUI, torch or GAIAVFX imports, so it is tested on the CPU.

From MoGePanoramaInference, `depth` is the radial distance along each equirectangular pixel's ray (1, H, W); from
MoGeInference it is one picture's z-depth and `intrinsics` (1, 3, 3, normalised) carry its field of view. `mask` marks
MoGe's valid pixels. SAM 3's detections are masks (B, H, W), (H, W) or empty when nothing was found, on the image's
own grid or any other (they are resampled, nearest, onto the depth's). The npz holds `depth` (float32, (H, W), 0 where
invalid), `mask`, `sky` and `glass` (bool, (H, W): the raw finds, judged later by the stage build) and, for a picture,
`intrinsics` (float32, (3, 3))."""

import numpy as np


def _finds(masks, shape) -> np.ndarray:
    union = np.zeros(shape, bool)
    for found in masks:
        array = np.asarray(found, dtype=np.float32)
        if array.size == 0:
            continue
        array = np.squeeze(array)
        if array.ndim == 3:  # several detections: their union
            array = array.max(0)
        if array.ndim != 2:
            raise ValueError("A SAM mask must be one picture's.")
        if array.shape != shape:
            rows = (np.arange(shape[0]) + 0.5) * array.shape[0] / shape[0]
            cols = (np.arange(shape[1]) + 0.5) * array.shape[1] / shape[1]
            array = array[rows.astype(int)[:, None], cols.astype(int)[None, :]]
        union |= array > 0.5
    return union


def moge_arrays(depth, mask, intrinsics=None, sky=(), glass=()) -> dict:
    depth = np.squeeze(np.asarray(depth, dtype=np.float32))
    mask = np.squeeze(np.asarray(mask, dtype=np.float32))
    if depth.ndim != 2 or mask.shape != depth.shape:
        raise ValueError("MoGe's depth and mask must be one image's, of one size.")
    valid = (mask > 0.5) & np.isfinite(depth) & (depth > 0)
    arrays = {
        "depth": np.where(valid, depth, 0.0).astype(np.float32),
        "mask": valid,
        "sky": _finds(sky, depth.shape),
        "glass": _finds(glass, depth.shape),
    }
    if intrinsics is not None:
        matrix = np.asarray(intrinsics, dtype=np.float32).reshape(-1, 3, 3)
        arrays["intrinsics"] = matrix[0]
    return arrays
