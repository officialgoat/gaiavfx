"""Write a small synthetic set bundle (ADR 0018 §3) for the browser suite: a panorama keyframe, one still, a map
and a splat shaped like a room's walls, with one Judge issue. Prints the bundle folder.

    python tests/browser-set-bundle.py <folder> [<set name>]
"""

import hashlib
import json
import math
import sys
from pathlib import Path

import numpy as np
from PIL import Image

PROPERTIES = (
    ["x", "y", "z", "f_dc_0", "f_dc_1", "f_dc_2"]
    + [f"f_rest_{n}" for n in range(9)]
    + ["opacity", "scale_0", "scale_1", "scale_2", "rot_0", "rot_1", "rot_2", "rot_3"]
)


def entry(bundle: Path, relative: str) -> dict:
    return {
        "path": relative,
        "sha256": hashlib.sha256((bundle / relative).read_bytes()).hexdigest(),
    }


def splat(path: Path):
    """Four walls of a 2 m room around the origin (OpenCV world: y down), one colour each."""
    rows = []
    colours = [(0.9, 0.5, 0.2), (0.2, 0.6, 0.9), (0.3, 0.8, 0.4), (0.8, 0.8, 0.8)]
    for wall, (r, g, b) in enumerate(colours):
        for a in np.linspace(-1, 1, 24):
            for h in np.linspace(-0.8, 0.6, 12):
                x, z = [(a, 1.0), (1.0, -a), (-a, -1.0), (-1.0, a)][wall]
                dc = [(c - 0.5) / 0.28209479 for c in (r, g, b)]
                rows.append(
                    [
                        x,
                        h,
                        z,
                        *dc,
                        *([0.0] * 9),
                        4.0,
                        *([math.log(0.05)] * 3),
                        1.0,
                        0.0,
                        0.0,
                        0.0,
                    ]
                )
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {len(rows)}\n"
        + "".join(f"property float {name}\n" for name in PROPERTIES)
        + "end_header\n"
    ).encode()
    path.write_bytes(header + np.asarray(rows, np.float32).tobytes())


def keyframe(bundle: Path, name, kind, width, height, viewmat, colour, label, role):
    Image.new("RGB", (width, height), colour).save(bundle / f"keyframes/{name}.png")
    ones = np.ones((height, width), bool)
    np.savez_compressed(
        bundle / f"keyframes/{name}.npz",
        depth=np.full((height, width), 1.0, np.float32),
        valid=ones,
        trust=ones,
        solid=ones,
    )
    return {
        "key": name,
        "label": label,
        "kind": kind,
        "role": role,
        "viewmat": viewmat,
        "intrinsics": None
        if kind == "erp"
        else [[60.0, 0, 64], [0, 60.0, 36], [0, 0, 1]],
        "width": width,
        "height": height,
        "heading": None if kind == "erp" else 90.0,
        "image": entry(bundle, f"keyframes/{name}.png"),
        "depth": entry(bundle, f"keyframes/{name}.npz"),
        "info": {},
    }


def main(folder: Path, name: str = "Test room") -> Path:
    bundle = folder.resolve()
    (bundle / "keyframes").mkdir(parents=True, exist_ok=True)
    eye = np.eye(4).tolist()
    turn = [
        [0.0, 0.0, -1.0, 0.0],
        [0.0, 1.0, 0.0, 0.0],
        [1.0, 0.0, 0.0, 0.0],
        [0, 0, 0, 1.0],
    ]
    keys = [
        keyframe(bundle, "pano", "erp", 256, 128, eye, (120, 90, 70), "K0", "observed"),
        keyframe(
            bundle, "r0p0", "pinhole", 128, 72, turn, (60, 110, 160), "K1", "generated"
        ),
    ]
    plane = np.zeros((24, 24), np.int8)
    plane[2:22, 2:22] = 2
    plane[3:21, 3:21] = 1
    frontier = np.zeros_like(plane, bool)
    frontier[20, 3:21] = True
    np.savez_compressed(
        bundle / "map.npz",
        plane=plane,
        frontier=frontier,
        lower=np.array([-1.2, -1.0, -1.2]),
        voxel=np.float32(0.1),
    )
    splat(bundle / "splat.ply")
    (bundle / "atlas.json").write_text(
        json.dumps({"schema": 1, "version": 1, "keyframes": []}), encoding="utf-8"
    )
    grid = {
        "cols": 5,
        "rows": 5,
        "cell_m": 0.5,
        "i0": 2,
        "k0": 2,
        "size": 5,
        "voxel": 0.1,
        "lower": [-1.2, -1.0, -1.2],
    }
    document = {
        "schema": 1,
        "name": name,
        "place_text": "A small square room with four coloured walls.",
        "seed": 1,
        "provenance": {"source": "browser suite"},
        "version": {
            "origin": "import",
            "note": "Synthetic bundle for the browser suite.",
            "atlas": entry(bundle, "atlas.json"),
            "map": entry(bundle, "map.npz"),
            "splat": entry(bundle, "splat.ply"),
            "map_meta": {
                "voxel": 0.1,
                "lower": [-1.2, -1.0, -1.2],
                "shape": [24, 24],
                "room": 2.0,
                "grid": grid,
                "coverage": {
                    "area": 3.24,
                    "frontier": 1.8,
                    "boundary": 7.2,
                    "closure": 0.75,
                },
                "cameras": [
                    {
                        "label": "K0",
                        "id": "pano",
                        "kind": "panorama",
                        "position": [0, 0, 0],
                        "heading": None,
                    },
                    {
                        "label": "K1",
                        "id": "r0p0",
                        "kind": "view",
                        "position": [0, 0, 0],
                        "heading": 90.0,
                    },
                ],
            },
            "metrics": {"uncalibrated": True, "gaussians": 1152},
            "keyframes": keys,
        },
        "issues": [
            {
                "kind": "floaters",
                "why": "A dark smear hangs in front of the orange wall.",
                "cells": ["C1"],
                "cameras": ["K0"],
                "note": "Synthetic judge issue.",
            }
        ],
    }
    (bundle / "set.json").write_text(json.dumps(document), encoding="utf-8")
    return bundle


if __name__ == "__main__":
    print(main(Path(sys.argv[1]), *sys.argv[2:3]))
