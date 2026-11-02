"""Closing a panorama's seam: the joined left and right edges (heading 180) of a generated panorama rarely meet. The
research's band fill (Worldbuilder R2, gate G7): roll the panorama half way so the seam sits in the centre, paint a
narrow central band green, let an image editor fill only that band, then composite the band (with a short feather)
back onto the untouched panorama and roll back. Every pixel outside the band and its feather stays the generated
panorama's, and `close` proves it or refuses."""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from PIL import Image

from . import erp


class SeamChanged(ValueError):
    """The merge touched pixels outside the band: the result is refused, never kept."""


@dataclass(frozen=True)
class SeamBand:
    """The band as shares of the panorama's width: 96 of 2048 pixels (the pytorch360convert example), a ramp of
    `feather` on each side, the fill's colour matched over a `ring` just outside it, and at most `green_max` of fill
    green left for the band to count as clean."""

    fraction: float = 96 / 2048
    feather: float = 24 / 2048
    ring: float = 0.02
    green_max: float = 0.005

    def __post_init__(self):
        if not 0 < self.fraction < 0.5 or not 0 <= self.feather < 0.25 or not 0 < self.ring < 0.25:
            raise ValueError("a seam band is a narrow central strip")


RESEARCH = SeamBand()


def band_input(raw: np.ndarray, band: SeamBand = RESEARCH) -> np.ndarray:
    """The editor's input: the panorama rolled so its seam is centred, the band painted fill green."""
    return erp.paint_band(erp.roll_half(_rgb(raw)), band.fraction)


def edge_step(image: np.ndarray) -> float:
    """How far apart the joined edges are: the mean absolute difference of the first and last columns (0-255)."""
    image = _rgb(image).astype(np.float32)
    return float(np.abs(image[:, 0] - image[:, -1]).mean())


def close(raw: np.ndarray, fill: np.ndarray, band: SeamBand = RESEARCH) -> tuple[np.ndarray, dict]:
    """`fill` is the editor's result for `band_input(raw)` at any size. Returns the closed panorama and its report;
    raises `SeamChanged` when anything outside the band moved."""
    raw = _rgb(raw)
    h, w = raw.shape[:2]
    fill = np.asarray(Image.fromarray(_rgb(fill)).resize((w, h), Image.LANCZOS))
    rolled = erp.roll_half(raw)
    fill = erp.match_band_colour(rolled, fill, band.fraction, band.feather, band.ring)
    merged = erp.roll_half(erp.composite_band(rolled, fill, band.fraction, band.feather))
    outside = erp.roll_half(erp.band_mask(w, h, band.fraction, band.feather)) == 0
    if not np.array_equal(merged[outside], raw[outside]):
        raise SeamChanged("the seam merge changed pixels outside the band")
    green = erp.green_fraction(merged[~outside].reshape(1, -1, 3))
    report = {
        "outside_identical": True,
        "green_in_band": round(green, 5),
        "clean": green <= band.green_max,
        "band_px": round(band.fraction * w),
        "feather_px": round(band.feather * w),
        "edge_step_before": round(edge_step(raw), 2),
        "edge_step_after": round(edge_step(merged), 2),
    }
    return merged, report


def _rgb(image) -> np.ndarray:
    array = np.asarray(image)
    if array.ndim != 3 or array.shape[2] < 3 or array.dtype != np.uint8:
        raise ValueError("a panorama is an 8-bit RGB image")
    return np.ascontiguousarray(array[..., :3])
