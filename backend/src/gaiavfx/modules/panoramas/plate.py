"""Growing a picture into a panorama (Worldbuilder R1c "f-out"): the picture is placed at heading 0 on a green 2:1
canvas at its own horizontal field of view, an image editor fills the green, and the picture's own pixels are put back
inside its footprint so the real part of the place stays exactly what the user gave.

The field of view comes from MoGe's normalised intrinsics when they are believable, otherwise from a default; a person
may always override it."""

from __future__ import annotations

import math

import numpy as np
from PIL import Image

from . import erp

DEFAULT_HFOV = 72.0  # the research plates' field of view
HFOV_RANGE = (30.0, 120.0)


def crop_letterbox(image: np.ndarray, threshold: int = 18) -> tuple[np.ndarray, dict]:
    """Crop black bars: rows and columns whose brightest pixel stays under `threshold`."""
    image = _rgb(image)
    bright = image.max(-1)
    rows = np.where(bright.max(1) >= threshold)[0]
    cols = np.where(bright.max(0) >= threshold)[0]
    if not rows.size or not cols.size:
        raise ValueError("the picture is black")
    cropped = image[rows[0] : rows[-1] + 1, cols[0] : cols[-1] + 1]
    report = {
        "source": [int(image.shape[0]), int(image.shape[1])],
        "kept_rows": [int(rows[0]), int(rows[-1])],
        "kept_cols": [int(cols[0]), int(cols[-1])],
    }
    return np.ascontiguousarray(cropped), report


def hfov_from_intrinsics(intrinsics) -> float | None:
    """The horizontal field of view (degrees) from MoGe's normalised intrinsics, or None when it is not believable."""
    matrix = np.asarray(intrinsics, dtype=np.float64).reshape(-1, 3, 3)[0]
    fx = float(matrix[0, 0])
    if not math.isfinite(fx) or fx <= 0:
        return None
    hfov = math.degrees(2 * math.atan(0.5 / fx))
    return round(hfov, 2) if HFOV_RANGE[0] <= hfov <= HFOV_RANGE[1] else None


def footprint(hfov: float, width: int, height: int) -> dict:
    """Where the picture sits in the panorama: heading 0, its horizontal and vertical fields of view (degrees)."""
    vfov = math.degrees(2 * math.atan(math.tan(math.radians(hfov) / 2) * height / width))
    return {"heading": 0.0, "hfov": round(hfov, 2), "vfov": round(vfov, 2)}


def canvas(plate: np.ndarray, hfov: float, width: int = 2048, height: int = 1024) -> np.ndarray:
    """The editor's input: the picture at heading 0 on a green panorama canvas."""
    _check_hfov(hfov)
    return erp.project_to_erp(_rgb(plate), hfov, width=width, height=height)


def restore(
    filled: np.ndarray,
    plate: np.ndarray,
    hfov: float,
    width: int = 2048,
    height: int = 1024,
    erode: int = 8,
    feather: int = 12,
) -> tuple[np.ndarray, dict]:
    """The editor's fill at the panorama's size with the picture's own pixels put back inside its footprint (eroded
    by `erode` pixels, blended in over `feather` more). The report says how far the editor drifted on the picture
    (`plate_psnr`, dB) and how much fill green it left anywhere."""
    _check_hfov(hfov)
    plate = _rgb(plate)
    filled = np.asarray(Image.fromarray(_rgb(filled)).resize((width, height), Image.LANCZOS))
    projected = erp.project_to_erp(plate, hfov, width=width, height=height)
    inside = erp.plate_mask(width, height, hfov, plate.shape[1], plate.shape[0], erode=erode)
    drift = erp.masked_psnr(filled, projected, inside)
    # A ramp from the footprint's edge inward: each erosion the picture survives weighs it more.
    alpha = np.zeros(inside.shape, np.float32)
    kept = inside
    for step in range(feather + 1):
        alpha[kept] = (step + 1) / (feather + 1)
        kept = (
            kept
            & np.roll(kept, 1, 0)
            & np.roll(kept, -1, 0)
            & np.roll(kept, 1, 1)
            & np.roll(kept, -1, 1)
        )
    alpha = alpha[..., None]
    mixed = filled.astype(np.float32) * (1 - alpha) + projected.astype(np.float32) * alpha
    out = np.clip(np.rint(mixed), 0, 255).astype(np.uint8)
    report = {
        "plate_psnr": round(float(drift), 2),
        "green": round(erp.green_fraction(out), 5),
        "plate_pixels": int(inside.sum()),
        **footprint(hfov, plate.shape[1], plate.shape[0]),
    }
    return out, report


def _check_hfov(hfov: float):
    if not HFOV_RANGE[0] <= hfov <= HFOV_RANGE[1]:
        raise ValueError(
            f"a picture's field of view is {HFOV_RANGE[0]:g} to {HFOV_RANGE[1]:g} degrees"
        )


def _rgb(image) -> np.ndarray:
    array = np.asarray(image)
    if array.ndim != 3 or array.shape[2] < 3 or array.dtype != np.uint8:
        raise ValueError("a picture is an 8-bit RGB image")
    return np.ascontiguousarray(array[..., :3])
