# Product code, not vendored research: the rest of this package is the research's own, unchanged.
"""A stage from one panorama: MoGe's radial distance and SAM 3's finds (the npz `GaiaSaveMoGeGeometry` saves), judged
as the research judged them (R4-R5), become the atlas's one keyframe, K0, the stage's ground truth.

- Sky: SAM 3 finds "sky" on indoor walls too, so a find is sky only where MoGe also puts it at least `sky_factor`
  times the place's typical distance (R4: real sky lay at 4.9x or more, false finds within 2.6x). Sky leaves the
  mask: it is kept by direction only, never a wall.
- Glass: windows and water carry reflections; each pane gets a fitted plane (R5), solid but never trusted. With no
  window in view SAM 3 can return the whole frame, so a find covering `glass_max` of the panorama is none.
- Gate: a panorama with less than `min_surface` of measured surface (open sea, a sky dome) cannot hold a stage.
The seam needs no exclusion: the panorama's was closed, and checked, before it got here.
Pure numpy and PIL."""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image

from . import explore as ex
from . import memory as mem

RECORD = "K0"


def load(path: Path) -> dict:
    with np.load(path, allow_pickle=False) as data:
        arrays = {name: data[name] for name in ("depth", "mask", "sky", "glass")}
    return arrays


def _onto(mask: np.ndarray, shape) -> np.ndarray:
    if mask.shape == shape:
        return mask
    rows = ((np.arange(shape[0]) + 0.5) * mask.shape[0] / shape[0]).astype(int)
    cols = ((np.arange(shape[1]) + 0.5) * mask.shape[1] / shape[1]).astype(int)
    return mask[rows[:, None], cols[None, :]]


def judge(arrays: dict, rules: dict) -> dict:
    """The panorama's sky, glass and measured surface by the research's rules, with the numbers that decided them."""
    distance = np.asarray(arrays["depth"], dtype=np.float32)
    mask = np.asarray(arrays["mask"], dtype=bool) & np.isfinite(distance) & (distance > 0)
    found = _onto(np.asarray(arrays["sky"], dtype=bool), distance.shape)
    rest = mask & ~found
    typical = float(np.median(distance[rest if rest.any() else mask])) if mask.any() else 0.0
    sky = found & mask & (distance >= float(rules["sky_factor"]) * typical)
    glass = _onto(np.asarray(arrays["glass"], dtype=bool), distance.shape)
    too_much = bool(glass.mean() >= float(rules["glass_max"]))
    glass = np.zeros_like(glass) if too_much else mem.grow(glass, 4)
    surface = float((mask & ~sky).mean())
    return {
        "distance": distance,
        "mask": mask & ~sky,
        "glass": glass,
        "record": {
            "surface": round(surface, 4),
            "sky_found": round(float(found.mean()), 4),
            "sky": round(float(sky.mean()), 4),
            "glass": round(float(glass.mean()), 4),
            "glass_ignored": too_much,
            "typical_distance": round(typical, 4),
            "passed": surface >= float(rules["min_surface"]),
        },
    }


def build_atlas(image_path: Path, geometry_path: Path, folder: Path, place: str, rules: dict) -> tuple[dict | None, dict]:
    """The atlas (version 1, K0 the panorama) written into `folder`, or None when the gate refuses; and the verdict."""
    judged = judge(load(geometry_path), rules)
    if not judged["record"]["passed"]:
        return None, judged["record"]
    image = np.asarray(Image.open(image_path).convert("RGB"))
    distance = judged["distance"]
    if image.shape[:2] != distance.shape:
        image = np.asarray(Image.fromarray(image).resize(distance.shape[::-1], Image.LANCZOS))
    key = mem.pano_keyframe(RECORD, image, distance, judged["mask"], glass=judged["glass"])
    record = mem.keyframe_record(key, folder / "keyframes", folder, role="observed", source="panorama")
    atlas = ex.new_atlas(place, record)
    ex.save_atlas(atlas, folder / "atlas.json")
    return atlas, judged["record"]
