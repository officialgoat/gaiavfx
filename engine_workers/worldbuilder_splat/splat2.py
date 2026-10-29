# Vendored from the maintained engine implementation on branch worldbuilder (26 October 2026) for the worldbuilder engine worker
# (ADR 0018 §4); only the imports changed, and one rule the product needs: a view marked `fixed` keeps its pose in
# `fit` (ADR 0020 §3; no research view carries it). The research module stays the reference; keep their numerics
# equal.
"""Worldbuilder splat v2 research CLI. GPU imports are lazy; --help is CPU-only.

States contain portable view PNG/NPZ files, the immutable C1 seed, scene.json,
state.pt and SH1 splat.ply. Nothing in this module contacts ComfyUI.
"""

from __future__ import annotations

import argparse
import contextlib
import copy
import json
import math
import os
import shutil
import subprocess
import sys
import time
from functools import lru_cache
from pathlib import Path

import numpy as np
from . import geometry as geo
from PIL import Image, ImageFilter
from .erp import crop_view

def _ffmpeg():
    executable = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    configured = os.environ.get("GAIAVFX_MEDIA_DIR")
    directories = [Path(configured)] if configured else []
    directories.append(Path(__file__).resolve().parents[2] / ".runtime" / "media")
    for directory in directories:
        candidate = directory / executable
        if candidate.is_file():
            return str(candidate)
    return shutil.which("ffmpeg") or "ffmpeg"


FFMPEG = _ffmpeg()


class JsonArgumentParser(argparse.ArgumentParser):
    def parse_args(self, args=None, namespace=None):
        result = super().parse_args(args, namespace)
        if result.command in ("refit", "final") and result.steps is None:
            result.steps = (
                3000
                if result.command == "final" and result.schedule == "mixed"
                else 1500
            )
        return result

    def error(self, message):
        print(json.dumps({"status": "failed", "error": message}))
        self.exit(2)


def jsonable(value):
    if isinstance(value, np.ndarray):
        return value.tolist()
    if isinstance(value, np.generic):
        return value.item()
    if isinstance(value, Path):
        return str(value)
    raise TypeError(type(value).__name__)


def write_json(path, data):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text(
        json.dumps(data, default=jsonable, allow_nan=False, indent=2), encoding="utf-8"
    )


def read_scene(directory):
    scene = json.loads((Path(directory) / "scene.json").read_text(encoding="utf-8"))
    validate_scene(scene)
    return scene


def validate_scene(scene):
    if (
        scene.get("schema") != 1
        or scene.get("kind") not in ("pano", "orbit")
        or scene.get("world") != geo.WORLD
    ):
        raise ValueError("Unsupported scene schema or coordinate frame")
    if not np.isfinite(scene["scale"]) or scene["scale"] <= 0:
        raise ValueError("Scene scale must be positive and finite")
    names = set()
    for view in scene["views"]:
        if view["name"] in names:
            raise ValueError("Duplicate view name")
        names.add(view["name"])
        for key, shape in (("viewmat", (4, 4)), ("K", (3, 3))):
            array = np.asarray(view[key])
            if array.shape != shape or not np.isfinite(array).all():
                raise ValueError(f"Invalid {key}")
        if view["role"] not in ("anchor", "train", "heldout", "generated"):
            raise ValueError("Invalid view role")
        for field in ("image", "data"):
            if field in view:
                path = Path(view[field])
                if path.is_absolute() or ".." in path.parts or ":" in str(path):
                    raise ValueError("View assets must be relative to the state")


def write_scene(directory, scene):
    validate_scene(scene)
    write_json(Path(directory) / "scene.json", scene)


def parse_train(value):
    try:
        result = [int(x) for x in value.split(",")]
    except ValueError as exc:
        raise argparse.ArgumentTypeError(
            "Expected comma-separated frame indices"
        ) from exc
    expected = set(range(72)) - set(range(0, 72, 8))
    if len(result) != len(set(result)) or set(result) != expected:
        raise argparse.ArgumentTypeError(
            "Train must contain exactly frames 0..71 except 0,8,...,64, in DA3 order"
        )
    return result


def parse_pair(value):
    if "=" not in value:
        raise argparse.ArgumentTypeError("Pair must be pathdir=refined.mp4")
    directory, clip = value.split("=", 1)
    if not directory or not clip:
        raise argparse.ArgumentTypeError("Pair paths must be nonempty")
    return Path(directory), Path(clip)


def parse_spec(path):
    entries = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(entries, list) or not entries:
        raise ValueError("Path spec must be a nonempty JSON list")
    ids = set()
    for entry in entries:
        name = entry.get("id", "")
        if (
            not name
            or any(
                c
                not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-"
                for c in name
            )
            or name in ids
        ):
            raise ValueError("Path ids must be unique safe file names")
        ids.add(name)
        kind = entry.get("kind")
        if kind == "petal":
            values = [entry["yaw"], entry["r"], entry.get("half_angle", 25)]
            if (
                not np.isfinite(values).all()
                or entry["r"] < 0
                or not 0 <= values[2] < 90
            ):
                raise ValueError("Invalid petal parameters")
        elif kind == "arc":
            if any(
                type(entry[k]) is not int or not 0 <= entry[k] < 72
                for k in ("from", "to")
            ):
                raise ValueError("Arc endpoints must be frame indices 0..71")
            if not 0 <= entry.get("inward", 0.1) < 1:
                raise ValueError("Inward fraction must be in [0,1)")
        elif kind == "pan":
            values = [entry["yaw_from"], entry["yaw_to"]]
            if not np.isfinite(values).all() or abs(values[1] - values[0]) > 180:
                raise ValueError("A pan turns at most 180 degrees between finite yaws")
        elif kind == "walk":
            geo.walk_keys(entry["keys"])
            if "cap_keys" in entry:
                geo.walk_keys(entry["cap_keys"])
        elif kind == "move":
            geo.move_path(entry["from"], entry["to"], 2)
        elif kind == "tour":
            geo.tour_path(entry["keys"], 2)
        elif kind == "fly":
            points, look = np.asarray(entry["points"]), np.asarray(entry["look"])
            if (
                points.ndim != 2
                or points.shape[1] != 3
                or len(points) < 2
                or look.shape != (3,)
                or not np.isfinite(points).all()
                or not np.isfinite(look).all()
            ):
                raise ValueError("Fly requires finite Nx3 points and one look target")
        else:
            raise ValueError("Unknown path kind")
    return entries


def positive_int(value):
    result = int(value)
    if result <= 0:
        raise argparse.ArgumentTypeError("Must be positive")
    return result


def nonnegative_int(value):
    result = int(value)
    if result < 0:
        raise argparse.ArgumentTypeError("Must be nonnegative")
    return result


def parser():
    result = JsonArgumentParser(description=__doc__)
    subs = result.add_subparsers(dest="command", required=True)
    for name in (
        "init-pano",
        "init-orbit",
        "init-atlas",
        "extend",
        "atlas-metrics",
        "paths",
        "refit",
        "metrics",
        "final",
    ):
        sub = subs.add_parser(name)
        sub.add_argument("--out", type=Path, required=True)
        if name in ("paths", "refit", "metrics", "extend", "atlas-metrics"):
            sub.add_argument("--state", type=Path, required=True)
        if name in ("init-atlas", "extend", "atlas-metrics"):
            sub.add_argument("--atlas", type=Path, required=True)
        if name == "init-atlas":
            sub.add_argument("--seam-mask-deg", type=float, default=0.0)
        if name in (
            "init-pano",
            "init-orbit",
            "init-atlas",
            "extend",
            "refit",
            "final",
        ):
            sub.add_argument(
                "--steps",
                type=positive_int,
                default=None
                if name in ("refit", "final")
                else (1500 if name == "extend" else 3000),
                help=(
                    "Real-view budget in fair mode (default: 1500); total iterations "
                    "in mixed mode (default: refit 1500, final 3000)"
                    if name in ("refit", "final")
                    else None
                ),
            )
        if name in ("paths", "refit", "final"):
            sub.add_argument("--ffmpeg", default=FFMPEG)
        if name == "init-pano":
            sub.add_argument("--erp", type=Path, required=True)
            sub.add_argument("--moge", type=Path, required=True)
            sub.add_argument("--seam-mask-deg", type=float, default=8.5)
        elif name == "init-orbit":
            sub.add_argument("--frames", type=Path, required=True)
            sub.add_argument("--da3", type=Path, required=True)
            sub.add_argument("--train", type=parse_train, required=True)
        elif name == "paths":
            sub.add_argument("--spec", type=Path, required=True)
        elif name in ("refit", "final"):
            sub.add_argument("--pair", action="append", type=parse_pair, default=[])
            sub.add_argument("--schedule", choices=("mixed", "fair"), default="mixed")
            sub.add_argument(
                "--generated-steps",
                type=nonnegative_int,
                help="Extra generated iterations in fair mode (default: --steps)",
            )
            sub.add_argument(
                "--final-real-steps",
                type=nonnegative_int,
                help="Real-only tail inside --steps in fair mode (default: 300)",
            )
            sub.add_argument(
                "--holes-only",
                action="store_true",
                help="Supervise generated RGB only in alpha holes; omit its depth",
            )
            sub.add_argument("--trust", choices=("sine", "flat"), default="sine")
            if name == "refit":
                sub.add_argument("--null", action="store_true")
            else:
                sub.add_argument("--init", type=Path, required=True)
        elif name == "metrics":
            sub.add_argument("--flythrough", type=Path)
    return result


def new_scene(kind):
    return {
        "schema": 1,
        "kind": kind,
        "world": geo.WORLD,
        "scale": 1.0,
        "target": None,
        "circle": None,
        "views": [],
        "steps": 0,
        "gaussians": 0,
        "parent": None,
        "round": 0,
        "pairs": [],
        "gates": {},
    }


def add_view(root, scene, name, role, image, viewmat, k, **arrays):
    root = Path(root)
    (root / "views").mkdir(parents=True, exist_ok=True)
    image = np.asarray(image)
    if image.dtype != np.uint8:
        image = np.rint(np.clip(image, 0, 1) * 255).astype(np.uint8)
    Image.fromarray(image).save(root / "views" / f"{name}.png")
    h, w = image.shape[:2]
    view = {
        "name": name,
        "role": role,
        "viewmat": np.asarray(viewmat).tolist(),
        "K": np.asarray(k).tolist(),
        "width": w,
        "height": h,
        "image": f"views/{name}.png",
        "weight": 1.0,
    }
    if arrays:
        view["data"] = f"views/{name}.npz"
        np.savez_compressed(
            root / view["data"],
            **{
                key: np.asarray(value, dtype=np.float32)
                for key, value in arrays.items()
            },
        )
    scene["views"].append(view)
    return view


def prepare_pano(args):
    image = np.asarray(Image.open(args.erp).convert("RGB"))
    h, w = image.shape[:2]
    if w != 2 * h or h < 2 or not 0 <= args.seam_mask_deg < 180:
        raise ValueError("Expected 2:1 ERP and seam width in [0,180)")
    with np.load(args.moge) as data:
        # GaiaSaveMoGeGeometry keeps MoGe's own names and batch axis: for a panorama, `depth` (1, H, W) is the
        # radial distance along each ERP ray, and the mask is saved as 0/1 floats.
        key = "distance" if "distance" in data.files else "depth"
        distance = np.squeeze(data[key]).astype(np.float32)
        mask = np.squeeze(data["mask"]) > 0.5
    if distance.shape != mask.shape or distance.ndim != 2:
        raise ValueError("MoGe mask and distance must share an HxW grid")
    if distance.shape != (h, w):
        mh, mw = distance.shape
        y, x = np.mgrid[:h, :w]
        u, v = (x + 0.5) * mw / w - 0.5, (y + 0.5) * mh / h - 0.5
        distance = geo.sample_float(distance, u, v)
        mask = mask[
            np.floor(v + 0.5).astype(int).clip(0, mh - 1),
            np.floor(u + 0.5).astype(int) % mw,
        ]
    mask &= np.isfinite(distance) & (distance > 0)
    points, colors, sizes, scale, distance = geo.pano_seed(distance, mask, image)
    scene = new_scene("pano")
    scene.update(scale=scale, seam_mask_deg=args.seam_mask_deg, cap_max=len(points))
    Image.fromarray(image).save(args.out / "erp.png")
    np.savez_compressed(
        args.out / "panorama.npz", distance=distance.astype(np.float32), mask=mask
    )
    views = [
        (f"ico_{i:02d}", "train", v["yaw"], v["pitch"], 80, 768, 768)
        for i, v in enumerate(geo.icosahedron_views())
    ]
    views += [
        (f"anchor_yaw{yaw:03d}", "anchor", yaw, 0, 75, 1024, 576)
        for yaw in range(0, 360, 45)
    ]
    for name, role, yaw, pitch, fov, width, height in views:
        view, k = geo.crop_camera(yaw, pitch, fov, width, height)
        rgb = crop_view(image, yaw, fov, width, height, pitch)
        depth = geo.erp_view_depth(distance, view, k, width, height)
        u, v = geo.erp_coordinates(
            geo.camera_rays(k, width, height) @ view[:3, :3], w, h
        )
        valid = geo.sample_float(mask.astype(float), u, v) >= 0.999
        weight = geo.seam_weights(view, k, width, height, args.seam_mask_deg)
        retracted = getattr(args, "retracted", None)
        if (
            retracted is not None and retracted.any()
        ):  # R6: disowned panorama pixels teach no colour either
            weight = weight * (geo.sample_float(retracted.astype(float), u, v) < 0.001)
        add_view(
            args.out,
            scene,
            name,
            role,
            rgb,
            view,
            k,
            depth=depth,
            confidence=valid,
            rgb_weight=weight,
        )
    return scene, (points, colors, sizes)


def load_da3(path):
    with np.load(path) as data:
        images = data["image"].astype(np.float32)
        depth = data["depth"].astype(np.float32)
        confidence = (
            data["confidence"].astype(np.float32)
            if "confidence" in data
            else np.ones_like(depth)
        )
        extrinsics, k = (
            data["extrinsics"][0],
            data["intrinsics"][0].astype(float).copy(),
        )
    if (
        images.shape != depth.shape + (3,)
        or confidence.shape != depth.shape
        or len(k) != len(depth)
        or len(extrinsics) != len(depth)
    ):
        raise ValueError("DA3 arrays have incompatible dimensions")
    views = np.tile(np.eye(4), (len(depth), 1, 1))
    views[:, : extrinsics.shape[1], :4] = extrinsics
    h, w = depth.shape[1:]
    k[:, 0, :] *= w / (2 * k[:, 0, 2:3])
    k[:, 1, :] *= h / (2 * k[:, 1, 2:3])
    if not np.isfinite(k).all() or not np.isfinite(images).all():
        raise ValueError("Invalid DA3 intrinsics or images")
    confidence = np.where(np.isfinite(confidence), np.maximum(confidence, 0), 0)
    confidence /= max(float(confidence.max()), 1e-12)
    return images, depth, confidence, views, k


def backproject(depth, view, k, keep):
    h, w = depth.shape
    cam = geo.camera_rays(k, w, h)[keep] * depth[keep, None]
    c2w = np.linalg.inv(view)
    return cam @ c2w[:3, :3].T + c2w[:3, 3]


def orbit_poses(views, indices):
    order = np.argsort(indices)
    ordered = views[order]
    frames = np.asarray(indices)[order]
    gates, circle = geo.trajectory_check(ordered)
    if not np.isfinite(views).all():
        raise ValueError("DA3 has nonfinite poses; cannot render or seed them")
    target = geo.optical_target(ordered)
    if circle is None:
        raise ValueError(
            "DA3 trajectory is degenerate; circle radius/pose bank undefined"
        )
    passed = gates["trajectory"] == "passed"
    direction = 1 if np.median(np.diff(circle["azimuth"])) > 0 else -1
    circle["direction"] = direction
    snapped = (
        np.stack(
            [
                geo.look_at(geo.circle_position(circle, a), target, circle["normal"])
                for a in circle["azimuth"]
            ]
        )
        if passed
        else ordered.copy()
    )
    result = {int(frame): pose for frame, pose in zip(frames, snapped)}
    for frame in range(0, 72, 8):
        next_index = int(np.searchsorted(frames, frame)) % len(frames)
        prev_index = (next_index - 1) % len(frames)
        left, right = int(frames[prev_index]), int(frames[next_index])
        t = ((frame - left) % 72) / ((right - left) % 72)
        if passed:
            a = circle["azimuth"][prev_index]
            b = circle["azimuth"][next_index]
            change = (direction * (b - a)) % (2 * np.pi) * direction
            result[frame] = geo.look_at(
                geo.circle_position(circle, a + t * change), target, circle["normal"]
            )
        else:
            result[frame] = geo.slerp_pose(snapped[prev_index], snapped[next_index], t)
    return result, circle, target, gates


def prepare_orbit(args):
    images, depths, confidence, raw_views, ks = load_da3(args.da3)
    if len(images) != len(args.train):
        raise ValueError(
            "DA3 must contain exactly the training frames in --train order"
        )
    poses, circle, target, gates = orbit_poses(raw_views, args.train)
    scene = new_scene("orbit")
    scene.update(
        scale=circle["radius"],
        circle=circle,
        target=target,
        gates=gates,
        cap_max=500000,
    )
    points, colors, sizes = [], [], []
    for i, frame in enumerate(args.train):
        depth, c, k, pose = depths[i], confidence[i], ks[i], poses[frame]
        real = np.asarray(Image.open(args.frames / f"f{frame:03d}.png").convert("RGB"))
        h, w = depth.shape
        if real.shape[:2] != (h, w):
            # Preserve the real frame's native pixel grid. Adapt DA3 geometry
            # and intrinsics to it, never resize the training photograph.
            rh, rw = real.shape[:2]
            y, x = np.mgrid[:rh, :rw]
            u, v = (x + 0.5) * w / rw - 0.5, (y + 0.5) * h / rh - 0.5
            depth = geo.sample_float(depth, u, v, wrap=False)
            c = geo.sample_float(c, u, v, wrap=False)
            k = k.copy()
            k[0] *= rw / w
            k[1] *= rh / h
        view = add_view(
            args.out,
            scene,
            f"frame_{frame:03d}",
            "train",
            real,
            pose,
            k,
            depth=depth,
            confidence=c,
        )
        view.update(frame=frame, base_viewmat=np.asarray(pose).tolist())
        y, x = np.indices(depth.shape)
        keep = (
            (y % 4 == 0)
            & (x % 4 == 0)
            & np.isfinite(depth)
            & (depth > 0)
            & (c >= np.quantile(c, 0.5))
            & (c > 0)
        )
        points.append(backproject(depth, pose, k, keep))
        colors.append(real[keep] / 255.0)
        sizes.append(depth[keep] / k[0, 0] * 4)
    for frame in range(0, 72, 8):
        image = np.asarray(Image.open(args.frames / f"f{frame:03d}.png").convert("RGB"))
        # Interpolate neighbour intrinsics, rescaled to the held-out image size.
        ordered = sorted(args.train)
        next_frame = ordered[int(np.searchsorted(ordered, frame)) % len(ordered)]
        prev_frame = ordered[(int(np.searchsorted(ordered, frame)) - 1) % len(ordered)]
        t = ((frame - prev_frame) % 72) / ((next_frame - prev_frame) % 72)
        k = (1 - t) * ks[args.train.index(prev_frame)] + t * ks[
            args.train.index(next_frame)
        ]
        k = k.copy()
        k[0] *= image.shape[1] / depths.shape[2]
        k[1] *= image.shape[0] / depths.shape[1]
        view = add_view(
            args.out, scene, f"frame_{frame:03d}", "heldout", image, poses[frame], k
        )
        view["frame"] = frame
    seed = tuple(
        np.concatenate(value).astype(np.float32) for value in (points, colors, sizes)
    )
    if len(seed[0]) > 500000:
        select = np.random.default_rng(0).choice(len(seed[0]), 500000, replace=False)
        seed = tuple(value[select] for value in seed)
    return scene, seed


def se3_matrix(delta):
    """Differentiable true se(3) exponential; translation first, rotation last."""
    import torch

    t, w = delta[..., :3], delta[..., 3:]
    x, y, z = w.unbind(-1)
    zero = torch.zeros_like(x)
    skew = torch.stack([zero, -z, y, z, zero, -x, -y, x, zero], -1).reshape(
        *w.shape[:-1], 3, 3
    )
    theta2 = (w * w).sum(-1)[..., None, None]
    theta = theta2.clamp_min(1e-12).sqrt()
    a = torch.where(
        theta2 < 1e-6, 1 - theta2 / 6 + theta2**2 / 120, theta.sin() / theta
    )
    b = torch.where(
        theta2 < 1e-6,
        0.5 - theta2 / 24 + theta2**2 / 720,
        (1 - theta.cos()) / theta2.clamp_min(1e-12),
    )
    c = torch.where(
        theta2 < 1e-6,
        1 / 6 - theta2 / 120 + theta2**2 / 5040,
        (theta - theta.sin()) / (theta2.clamp_min(1e-12) * theta),
    )
    eye = torch.eye(3, dtype=delta.dtype, device=delta.device)
    rot = eye + a * skew + b * (skew @ skew)
    translation = ((eye + b * skew + c * (skew @ skew)) @ t[..., None])[..., 0]
    upper = torch.cat([rot, translation[..., None]], -1)
    bottom = torch.zeros(
        (*delta.shape[:-1], 1, 4), dtype=delta.dtype, device=delta.device
    )
    bottom[..., 0, 3] = 1
    return torch.cat([upper, bottom], -2)


def camera_opt_module(count, device="cpu"):
    import torch

    class CameraOptModule(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.deltas = torch.nn.Parameter(torch.zeros(count, 6, device=device))

        def forward(self, view, index):
            # Camera-local c2w correction, as in gsplat's camera optimizer.
            return torch.linalg.inv(
                torch.linalg.inv(view) @ se3_matrix(self.deltas[index])
            )

    return CameraOptModule()


def torch_ssim_map(first, second, mask):
    import torch
    from torch.nn import functional

    a, b = first.permute(2, 0, 1)[None], second.permute(2, 0, 1)[None]
    weight = mask[None, None]
    x = torch.arange(-5, 6, dtype=a.dtype, device=a.device)
    kernel = torch.exp(-x.square() / (2 * 1.5**2))
    kernel /= kernel.sum()
    kernel = (kernel[:, None] * kernel[None, :])[None, None]

    def blur(value):
        channels = value.shape[1]
        return functional.conv2d(
            functional.pad(value, (5, 5, 5, 5), mode="replicate"),
            kernel.expand(channels, 1, 11, 11),
            groups=channels,
        )

    norm = blur(weight).clamp_min(1e-12)
    ma, mb = blur(a * weight) / norm, blur(b * weight) / norm
    va = (blur(a * a * weight) / norm - ma.square()).clamp_min(0)
    vb = (blur(b * b * weight) / norm - mb.square()).clamp_min(0)
    cov = blur(a * b * weight) / norm - ma * mb
    score = (
        (2 * ma * mb + 0.01**2)
        * (2 * cov + 0.03**2)
        / ((ma.square() + mb.square() + 0.01**2) * (va + vb + 0.03**2))
    )
    return score[0].mean(0)


def make_params(seed, device="cuda"):
    import torch

    points, colors, sizes = seed
    if (
        not len(points)
        or not all(np.isfinite(a).all() for a in seed)
        or np.any(sizes <= 0)
    ):
        raise ValueError("Invalid or empty Gaussian seed")
    n = len(points)
    tensor = lambda x: torch.as_tensor(x, dtype=torch.float32, device=device)
    sh = torch.zeros(n, 4, 3, device=device)
    sh[:, 0] = (tensor(colors) - 0.5) / 0.28209479177387814
    values = {
        "means": tensor(points),
        "scales": tensor(sizes).clamp_min(1e-8).log()[:, None].repeat(1, 3),
        "quats": tensor([[1, 0, 0, 0]]).repeat(n, 1),
        "opacities": torch.zeros(n, device=device),
        "sh0": sh[:, :1].contiguous(),
        "shN": sh[:, 1:].contiguous(),
    }
    return torch.nn.ParameterDict(
        {key: torch.nn.Parameter(value) for key, value in values.items()}
    )


def raster(params, view, k, width, height):
    import torch
    from gsplat import rasterization

    device = params["means"].device
    view = torch.as_tensor(view, dtype=torch.float32, device=device)
    k = torch.as_tensor(k, dtype=torch.float32, device=device)
    return rasterization(
        params["means"],
        torch.nn.functional.normalize(params["quats"], dim=-1),
        params["scales"].exp(),
        params["opacities"].sigmoid(),
        torch.cat([params["sh0"], params["shN"]], dim=1),
        view[None],
        k[None],
        width,
        height,
        sh_degree=1,
        render_mode="RGB+ED",
        packed=False,
        absgrad=False,
    )


def render_numpy(params, view, k, width, height):
    import torch

    with torch.no_grad():
        image, alpha, _ = raster(params, view, k, width, height)
    return (
        image[0, ..., :3].clamp(0, 1).cpu().numpy(),
        image[0, ..., 3].cpu().numpy(),
        alpha[0, ..., 0].cpu().numpy(),
    )


def view_schedule(
    views,
    steps,
    seed=0,
    *,
    schedule="mixed",
    generated_steps=None,
    final_real_steps=300,
):
    """Legacy mixed sampling, or the null's real budget plus generated slots."""
    if schedule == "fair":
        generated_steps = steps if generated_steps is None else generated_steps
        if steps <= 0 or generated_steps < 0 or not 0 <= final_real_steps <= steps:
            raise ValueError("Fair schedule requires N > 0, G >= 0 and 0 <= R <= N")
        real = [i for i, v in enumerate(views) if v["role"] in ("anchor", "train")]
        generated = [
            i
            for i, v in enumerate(views)
            if v["role"] == "generated" and v.get("accepted", False)
        ]
        # Preserve the null's real-view sequence, including its anchor quota.
        real_slots = [
            real[i] for i in view_schedule([views[i] for i in real], steps, seed)
        ]
        if not generated or not generated_steps:
            return real_slots
        rng = np.random.default_rng(seed)
        slots = np.array(
            [False] * (steps - final_real_steps) + [True] * generated_steps
        )
        rng.shuffle(slots)
        real_iter = iter(real_slots)
        result = [
            int(rng.choice(generated)) if slot else next(real_iter) for slot in slots
        ]
        return result + list(real_iter)
    if schedule != "mixed":
        raise ValueError("Unknown view schedule")
    rng = np.random.default_rng(seed)
    real = [i for i, v in enumerate(views) if v["role"] in ("anchor", "train")]
    anchors = [i for i in real if views[i]["role"] == "anchor"]
    generated = [
        i
        for i, v in enumerate(views)
        if v["role"] == "generated" and v.get("accepted", False)
    ]
    if not real:
        raise ValueError("No real training views")
    # First ceil(30%) slots in each shuffled block of ten are protected.
    result = []
    for start in range(0, steps, 10):
        size = min(10, steps - start)
        protected = np.zeros(size, dtype=bool)
        protected[: math.ceil(0.3 * size)] = True
        rng.shuffle(protected)
        for fixed in protected:
            # Remaining slots mix all real views with generated views; anchors
            # retain their guaranteed quota even in panorama refits.
            pool = (
                (anchors or real)
                if fixed
                else (generated if generated and rng.random() < 0.8 else real)
            )
            result.append(int(rng.choice(pool)))
    return result


def learning_rates(scale):
    return {
        "means": 1.6e-4 * scale,
        "scales": 5e-3,
        "quats": 1e-3,
        "opacities": 5e-2,
        "sh0": 2.5e-3,
        "shN": 2.5e-3 / 20,
    }


def mcmc_noise_lr(scale):
    if not np.isfinite(scale) or scale <= 0:
        raise ValueError("MCMC requires positive finite scene scale")
    # gsplat multiplies noise by covariance (scale^2) and means lr (scale).
    # Dividing noise_lr by scale^2 leaves displacement proportional to scale.
    return 5e5 / scale**2


def depth_loss_weights(depth, confidence=None, alpha=None):
    """Normalise by supervised pixel count, preserving confidence/trust weights."""
    valid = np.isfinite(depth) & (depth > 0)
    confidence = np.ones_like(depth) if confidence is None else confidence
    weights = np.where(
        valid & np.isfinite(confidence) & (confidence > 0), confidence, 0
    )
    if alpha is not None:
        weights = weights * 0.5 * (alpha >= 0.9)
    return weights / max(int(np.count_nonzero(weights > 0)), 1)


def hole_rgb_weights(alpha):
    """Path-render alpha ramp: full RGB supervision at <=0.6, none at >=0.9."""
    return np.clip((0.9 - np.asarray(alpha)) / 0.3, 0, 1)


def weighted_pixel_loss(pixel_loss, weights):
    """Weighted mean for NumPy or Torch, including an empty supervision mask."""
    return (pixel_loss * weights).sum() / weights.sum().clip(1e-8, None)


def split_legacy_sh(values):
    """Read earlier v2 checkpoints without losing any SH coefficients."""
    values = dict(values)
    if "sh" in values:
        sh = values.pop("sh")
        values["sh0"], values["shN"] = sh[:, :1], sh[:, 1:]
    return values


def fit(root, scene, params, steps, previous=None):
    import torch
    from gsplat.strategy import MCMCStrategy

    torch.manual_seed(0)
    device = params["means"].device
    views = scene["views"]
    options = scene.get("refit", {})
    schedule = view_schedule(
        views,
        steps,
        schedule=options.get("schedule", "mixed"),
        generated_steps=options.get("generated_steps"),
        final_real_steps=options.get("final_real_steps", 300),
    )
    steps = len(schedule)
    generated_steps = sum(views[i]["role"] == "generated" for i in schedule)
    generated_step = 0
    training = [
        v["name"]
        for v in views
        if scene["kind"] == "orbit" and v["role"] == "train" and not v.get("fixed")
    ]
    generated = [v["name"] for v in views if v["role"] == "generated"]
    camera = camera_opt_module(len(training), device)
    affine = torch.nn.Parameter(
        torch.cat(
            [torch.eye(3, device=device), torch.zeros(3, 1, device=device)], 1
        ).repeat(len(generated), 1, 1)
    )
    if previous:
        with torch.no_grad():
            for i, name in enumerate(training):
                if name in previous.get("camera_deltas", {}):
                    camera.deltas[i].copy_(previous["camera_deltas"][name].to(device))
            for i, name in enumerate(generated):
                if name in previous.get("affine", {}):
                    affine[i].copy_(previous["affine"][name].to(device))
    rates = learning_rates(scene["scale"])
    optimizers = {
        key: torch.optim.Adam([params[key]], lr=value, eps=1e-15)
        for key, value in rates.items()
    }
    auxiliary = []
    if training:
        auxiliary.append(torch.optim.Adam(camera.parameters(), lr=1e-5))
    if generated:
        auxiliary.append(torch.optim.Adam([affine], lr=1e-3))
    strategy = MCMCStrategy(
        cap_max=scene["cap_max"],
        refine_start_iter=500,
        refine_stop_iter=int(0.8 * steps),
        refine_every=100,
        noise_lr=mcmc_noise_lr(scene["scale"]),
        min_opacity=0.005,
    )
    strategy.check_sanity(params, optimizers)
    state = previous.get("mcmc") if previous else None
    if state is None:
        state = strategy.initialize_state()

    @lru_cache(maxsize=4)
    def load_view(index):
        view = views[index]
        image = (
            np.asarray(
                Image.open(root / view["image"]).convert("RGB"), dtype=np.float32
            )
            / 255
        )
        arrays = {}
        if "data" in view:
            with np.load(root / view["data"]) as data:
                arrays = {key: data[key] for key in data.files}
        return image, arrays

    started = time.perf_counter()
    log = []
    for step, index in enumerate(schedule):
        view = views[index]
        image, arrays = load_view(index)
        tensor = lambda value: torch.as_tensor(
            value, dtype=torch.float32, device=device
        )
        target = tensor(image)
        vm = tensor(view.get("base_viewmat", view["viewmat"]))
        camera_index = (
            training.index(view["name"]) if view["name"] in training else None
        )
        if camera_index is not None:
            vm = camera(vm, camera_index)
        rendered, _, info = raster(params, vm, view["K"], view["width"], view["height"])
        rgb, depth = rendered[0, ..., :3], rendered[0, ..., 3]
        mask = tensor(arrays.get("rgb_weight", np.ones(image.shape[:2], np.float32)))
        temporal = 1.0
        spatial = torch.ones_like(mask)
        holes_only = view["role"] == "generated" and options.get("holes_only", False)
        if view["role"] == "generated":
            a = affine[generated.index(view["name"])]
            rgb = rgb @ a[:, :3].T + a[:, 3]
            if options.get("trust", "sine") == "flat":
                temporal = geo.flat_trust_weight(generated_step, generated_steps)
            else:
                temporal = geo.trust_weight(step, max(steps - 1, 1))
            generated_step += 1
            spatial = tensor(
                hole_rgb_weights(arrays["alpha"])
                if holes_only
                else np.where(arrays["alpha"] < 0.6, 1.0, 0.5)
            )
        if holes_only:
            weights = mask * spatial
            l1 = weighted_pixel_loss((rgb - target).abs().mean(-1), weights)
            structural = weighted_pixel_loss(
                1 - torch_ssim_map(rgb, target, weights), weights
            )
        else:
            denominator = mask.sum().clamp_min(1)
            l1 = ((rgb - target).abs().mean(-1) * mask * spatial).sum() / denominator
            structural = (
                (1 - torch_ssim_map(rgb, target, mask)) * mask * spatial
            ).sum() / denominator
        loss = temporal * (0.8 * l1 + 0.2 * structural)
        if "depth" in arrays and not holes_only:
            target_depth = arrays["depth"]
            valid = np.isfinite(target_depth) & (target_depth > 0)
            weight = depth_loss_weights(
                target_depth,
                arrays.get("confidence"),
                arrays["alpha"] if view["role"] == "generated" else None,
            )
            safe_target = tensor(np.where(valid, target_depth, 1))
            depth_weight = tensor(weight)
            loss = (
                loss
                + temporal
                * 0.1
                * (
                    (1 / depth.clamp_min(1e-6) - 1 / safe_target).abs() * depth_weight
                ).sum()
            )
        if view["role"] == "generated" and options.get("schedule") == "fair":
            loss = loss * view.get("weight", 1.0)
        loss = (
            loss
            + 0.01 * params["opacities"].sigmoid().mean()
            + 0.01 * params["scales"].exp().mean()
        )
        if training:
            loss = loss + 1e-6 * camera.deltas.square().mean()
        if not torch.isfinite(loss):
            raise ValueError(f"Nonfinite training loss at step {step}")
        loss.backward()
        for optimizer in [*optimizers.values(), *auxiliary]:
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
        strategy.step_post_backward(
            params, optimizers, state, step, info, lr=rates["means"]
        )
        if step % 250 == 0 or step == steps - 1:
            log.append(
                {
                    "step": step,
                    "loss": float(loss.detach()),
                    "gaussians": len(params["means"]),
                }
            )
    for view in views:
        if view["name"] in training:
            with torch.no_grad():
                view["viewmat"] = (
                    camera(
                        torch.tensor(
                            view["base_viewmat"], dtype=torch.float32, device=device
                        ),
                        training.index(view["name"]),
                    )
                    .cpu()
                    .tolist()
                )
    scene.update(
        steps=steps,
        gaussians=len(params["means"]),
        fit_seconds=time.perf_counter() - started,
    )
    return {
        "params": {k: v.detach().cpu() for k, v in params.items()},
        "mcmc": {k: v.cpu() if torch.is_tensor(v) else v for k, v in state.items()},
        "camera_deltas": {
            name: camera.deltas[i].detach().cpu() for i, name in enumerate(training)
        },
        "affine": {name: affine[i].detach().cpu() for i, name in enumerate(generated)},
        "log": log,
    }


def save_ply(params, path):
    from plyfile import PlyData, PlyElement

    arrays = {key: value.detach().cpu().numpy() for key, value in params.items()}
    names = (
        ["x", "y", "z"]
        + [f"f_dc_{i}" for i in range(3)]
        + [f"f_rest_{i}" for i in range(9)]
        + ["opacity"]
        + [f"scale_{i}" for i in range(3)]
        + [f"rot_{i}" for i in range(4)]
    )
    values = np.concatenate(
        [
            arrays["means"],
            arrays["sh0"][:, 0],
            arrays["shN"].transpose(0, 2, 1).reshape(-1, 9),
            arrays["opacities"][:, None],
            arrays["scales"],
            geo.unit(arrays["quats"]),
        ],
        1,
    )
    rows = np.empty(len(values), dtype=[(name, "f4") for name in names])
    for i, name in enumerate(names):
        rows[name] = values[:, i]
    PlyData([PlyElement.describe(rows, "vertex")]).write(str(path))


def load_state(root, device="cuda"):
    import torch

    # Only load locally produced trusted research states (torch 2.4's pickle format).
    state = torch.load(Path(root) / "state.pt", map_location="cpu", weights_only=False)
    params = torch.nn.ParameterDict(
        {
            key: torch.nn.Parameter(value.to(device).contiguous())
            for key, value in split_legacy_sh(state["params"]).items()
        }
    )
    return params, state


def save_state(root, scene, params, state):
    import torch

    torch.save(state, root / "state.pt")
    save_ply(params, root / "splat.ply")
    write_scene(root, scene)


def arc_endpoints(scene, entry):
    if scene["kind"] != "orbit":
        raise ValueError("Arcs require an orbit")
    views = {v["frame"]: v for v in scene["views"] if v["role"] == "train"}
    if entry["from"] not in views or entry["to"] not in views:
        raise ValueError("Arc endpoints must be training cameras")
    return views[entry["from"]], views[entry["to"]]


def path_calibration(scene, entry, count=73):
    if entry["kind"] != "arc":
        return np.repeat(geo.intrinsics()[None], count, axis=0), 1024, 576
    first, last = arc_endpoints(scene, entry)
    width, height = first["width"], first["height"]
    if (width, height) != (last["width"], last["height"]):
        raise ValueError(
            "Arc training frames must share a native image size for a fixed-size video"
        )
    a, b = np.asarray(first["K"]), np.asarray(last["K"])
    ks = np.stack([(1 - t) * a + t * b for t in np.linspace(0, 1, count)])
    ks[0], ks[-1] = a, b
    return ks, width, height


def path_poses(scene, root, entry):
    kind = entry["kind"]
    if kind == "petal":
        if scene["kind"] != "pano":
            raise ValueError("Petals require a panorama")
        with np.load(root / "panorama.npz") as data:
            distance = data["distance"]
        return geo.petal_path(
            entry["yaw"],
            entry["r"],
            scene["scale"],
            distance,
            entry.get("half_angle", 25),
        )
    if kind == "move":
        # R4: a probe's move in scene units, eased; any start kind (the poses are absolute).
        return geo.move_path(entry["from"], entry["to"])
    if kind == "tour":
        # R5: a fly-through over the keyframes of an explored place.
        return geo.tour_path(entry["keys"], entry.get("per_segment", 37))
    if kind in ("pan", "walk"):
        if scene["kind"] != "pano":
            raise ValueError("Pans and walks require a panorama")
        if kind == "pan":
            return geo.pan_path(entry["yaw_from"], entry["yaw_to"])
        with np.load(root / "panorama.npz") as data:
            distance = data["distance"]
        return geo.walk_path(
            entry["keys"], scene["scale"], distance, cap_keys=entry.get("cap_keys")
        )
    if kind == "arc":
        first, last = arc_endpoints(scene, entry)
        return geo.arc_path(
            np.asarray(first["viewmat"]),
            np.asarray(last["viewmat"]),
            scene["circle"],
            scene["target"],
            entry.get("inward", 0.1),
            scene["circle"]["direction"],
        )
    points = np.asarray(entry["points"], dtype=float)
    positions = np.column_stack(
        [
            np.interp(
                np.linspace(0, len(points) - 1, 73),
                np.arange(len(points)),
                points[:, i],
            )
            for i in range(3)
        ]
    )
    up = (
        np.array([0, -1, 0])
        if scene["kind"] == "pano"
        else np.asarray(scene["circle"]["normal"])
    )
    return np.stack([geo.look_at(p, entry["look"], up) for p in positions])


def encode_video(path, frames, ffmpeg, lossless=False):
    frames = np.asarray(frames)
    if frames.ndim == 3:
        frames = np.repeat(frames[..., None], 3, -1)
    h, w = frames.shape[1:3]
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-y",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgb24",
        "-s",
        f"{w}x{h}",
        "-r",
        "24",
        "-i",
        "pipe:0",
        "-an",
        "-c:v",
        "libx264rgb" if lossless else "libx264",
        "-crf",
        "0" if lossless else "12",
        "-pix_fmt",
        "rgb24" if lossless else "yuv420p",
        str(path),
    ]
    # stderr inherits the CLI's diagnostic stream; no pipe can deadlock.
    process = subprocess.Popen(command, stdin=subprocess.PIPE)
    try:
        for frame in frames:
            process.stdin.write(np.ascontiguousarray(frame, dtype=np.uint8).tobytes())
        process.stdin.close()
        if process.wait() != 0:
            raise RuntimeError(f"ffmpeg failed to encode {path}")
    finally:
        if process.poll() is None:
            process.kill()
            process.wait()


def decode_video(path, width, height, ffmpeg):
    command = [
        str(ffmpeg),
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        str(path),
        "-map",
        "0:v:0",
        "-fps_mode",
        "passthrough",
        "-vf",
        f"scale={width}:{height}:flags=lanczos",
        "-f",
        "image2pipe",
        "-c:v",
        "ppm",
        "-pix_fmt",
        "rgb24",
        "pipe:1",
    ]
    # Refined H3 clips may use a different size. Resample into the stored path
    # pixel grid before alignment, hole seeding and depth supervision.
    process = subprocess.Popen(command, stdout=subprocess.PIPE)
    size = width * height * 3
    try:
        for _ in range(74):
            magic = process.stdout.readline()
            if not magic:
                break
            dimensions = process.stdout.readline().split()
            maximum = process.stdout.readline()
            if (
                magic.strip() != b"P6"
                or dimensions != [str(width).encode(), str(height).encode()]
                or maximum.strip() != b"255"
            ):
                raise ValueError("Clip dimensions do not match path dimensions")
            frame = bytearray()
            while len(frame) < size:
                chunk = process.stdout.read(size - len(frame))
                if not chunk:
                    break
                frame.extend(chunk)
            if not frame:
                break
            if len(frame) != size:
                raise ValueError("Clip dimensions do not match path dimensions")
            yield np.frombuffer(frame, dtype=np.uint8).reshape(height, width, 3)
        else:
            raise ValueError("Expected exactly 73 video frames")
        if process.wait() != 0:
            raise RuntimeError(f"ffmpeg failed to decode {path}")
    finally:
        process.stdout.close()
        if process.poll() is None:
            process.kill()
            process.wait()


def walk_end_yaw(entry, last=False):
    """The yaw a walk starts or ends on when that end is the panorama centre (a real crop), else None."""
    x, z, yaw = entry["keys"][-1 if last else 0]
    return yaw if abs(x) < 1e-9 and abs(z) < 1e-9 else None


def real_path_anchor(scene, root, entry, last=False):
    if entry["kind"] in ("move", "tour"):
        x, y, z, yaw = (
            entry["to" if last else "from"]
            if entry["kind"] == "move"
            else entry["keys"][-1 if last else 0]
        )
        if scene["kind"] != "pano" or max(abs(x), abs(y), abs(z)) > 1e-9:
            return None  # only the panorama centre has a real view; write_paths keeps the render's frame
        erp = np.asarray(Image.open(root / "erp.png").convert("RGB"))
        return Image.fromarray(crop_view(erp, yaw, 75, 1024, 576))
    if entry["kind"] in ("petal", "pan", "walk"):
        if entry["kind"] == "petal":
            yaw = entry["yaw"]
        elif entry["kind"] == "pan":
            yaw = entry["yaw_to" if last else "yaw_from"]
        else:
            yaw = walk_end_yaw(entry, last)
            if yaw is None:
                return None  # no real view off the centre: write_paths keeps the render's frame
        erp = np.asarray(Image.open(root / "erp.png").convert("RGB"))
        return Image.fromarray(crop_view(erp, yaw, 75, 1024, 576))
    if entry["kind"] == "arc":
        view = arc_endpoints(scene, entry)[int(last)]
    else:
        # Fly-throughs are review-only; nearest real source is explicitly labelled.
        point = entry["points"][-1 if last else 0]
        real = [v for v in scene["views"] if v["role"] == "train"]
        view = min(
            real,
            key=lambda v: np.linalg.norm(geo.camera_centres([v["viewmat"]])[0] - point),
        )
    return Image.open(root / view["image"]).convert("RGB")


def write_paths(args, scene, params):
    outputs = []
    for entry in parse_spec(args.spec):
        views = path_poses(scene, args.state, entry)
        ks, width, height = path_calibration(scene, entry, len(views))
        out = args.out / entry["id"]
        out.mkdir(parents=True, exist_ok=False)
        rgb, depth, alpha = zip(
            *(
                render_numpy(params, view, k, width, height)
                for view, k in zip(views, ks)
            )
        )
        rgb, depth, alpha = np.asarray(rgb), np.asarray(depth), np.asarray(alpha)
        pixels = np.rint(rgb * 255).astype(np.uint8)
        masks = np.stack(
            [
                np.asarray(
                    Image.fromarray((a < 0.6).astype(np.uint8) * 255).filter(
                        ImageFilter.MaxFilter(17)
                    )
                )
                for a in alpha
            ]
        )
        encode_video(out / "render.mp4", pixels, args.ffmpeg)
        encode_video(out / "mask.mp4", masks, args.ffmpeg, True)
        encode_video(
            out / "depthvis.mp4",
            geo.depth_visualisation(depth, alpha),
            args.ffmpeg,
            True,
        )
        np.savez_compressed(
            out / "depth.npz",
            depth=depth.astype(np.float16),
            alpha=alpha.astype(np.float16),
        )
        # Exact references for gate evaluation, without lossy video round trips.
        (out / "reference").mkdir()
        for i, frame in enumerate(pixels):
            Image.fromarray(frame).save(out / "reference" / f"f{i:03d}.png")
        write_json(
            out / "poses.json",
            [
                {"frame": i, "viewmat": view, "K": k, "width": width, "height": height}
                for i, (view, k) in enumerate(zip(views, ks))
            ],
        )
        sources = {}
        for end, last in (("first", False), ("last", True)):
            anchor = real_path_anchor(scene, args.state, entry, last)
            if anchor is None:
                anchor, sources[end] = (
                    Image.fromarray(pixels[-1 if last else 0]),
                    "render",
                )
            else:
                sources[end] = "real"
            anchor.save(out / f"anchor_{end}.png")
        write_json(
            out / "meta.json",
            {
                "schema": 1,
                "spec": entry,
                "state": str(args.state.resolve()),
                "frames": len(views),
                "fps": 24,
                "width": width,
                "height": height,
                "anchor_source": "nearest_real_view"
                if entry["kind"] == "fly"
                else "real_anchor",
                "anchor_ends": sources,
            },
        )
        outputs.append(str(out))
    return {"paths": outputs}


def copy_initial_assets(source, out, scene):
    scene["views"] = [v for v in scene["views"] if v["role"] != "generated"]
    (out / "views").mkdir(parents=True, exist_ok=True)
    for view in scene["views"]:
        for key in ("image", "data"):
            if key in view:
                destination = out / view[key]
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(source / view[key], destination)
    for name in ("seed.npz", "erp.png", "panorama.npz"):
        if (source / name).exists():
            shutil.copy2(source / name, out / name)


def accept_pairs(pairs, out, scene, ffmpeg):
    points, colors, sizes, records = [], [], [], []
    passed, total = 0, 0
    for pair_index, (directory, clip) in enumerate(pairs):
        poses = json.loads((directory / "poses.json").read_text(encoding="utf-8"))
        if len(poses) != 73:
            raise ValueError("Pair path must contain 73 poses")
        with np.load(directory / "depth.npz") as data:
            depths, alphas = (
                data["depth"].astype(np.float32),
                data["alpha"].astype(np.float32),
            )
        if len(depths) != 73 or alphas.shape != depths.shape:
            raise ValueError("Pair depth and alpha must have 73 matching frames")
        h, w = depths.shape[1:]
        accepted = []
        count = 0
        for i, image in enumerate(decode_video(clip, w, h, ffmpeg)):
            if i >= 73:
                raise ValueError("Refined clip must contain exactly 73 frames")
            pose, depth, alpha = poses[i], depths[i], alphas[i]
            if pose["width"] != w or pose["height"] != h:
                raise ValueError("Pair pose dimensions do not match depth")
            reference = (
                np.asarray(
                    Image.open(directory / "reference" / f"f{i:03d}.png").convert(
                        "RGB"
                    ),
                    dtype=float,
                )
                / 255
            )
            good, score = geo.alignment_gate(image / 255.0, reference, alpha)
            view = add_view(
                out,
                scene,
                f"generated_r{scene['round'] + 1:03d}_{pair_index:03d}_{i:03d}",
                "generated",
                image,
                np.asarray(pose["viewmat"]),
                np.asarray(pose["K"]),
                depth=depth,
                alpha=alpha,
            )
            view.update(
                accepted=good,
                alignment_ssim=score,
                pair=pair_index,
                frame=i,
                weight=0.0 if not good else 0.5,
            )
            passed += int(good)
            total += 1
            count += 1
            accepted.append(good)
            if good:
                filled = geo.fill_hole_depth(depth, alpha >= 0.6)
                y, x = np.indices(depth.shape)
                keep = (
                    (alpha < 0.6)
                    & (y % 2 == 0)
                    & (x % 2 == 0)
                    & (filled > 0)
                    & np.isfinite(filled)
                )
                points.append(
                    backproject(
                        filled, np.asarray(pose["viewmat"]), np.asarray(pose["K"]), keep
                    )
                )
                colors.append(image[keep] / 255)
                sizes.append(filled[keep] / pose["K"][0][0] * 2)
        if count != 73:
            raise ValueError(f"Refined clip has {count} frames; expected 73")
        records.append(
            {
                "path": str(directory.resolve()),
                "refined": str(clip.resolve()),
                "accepted": accepted,
            }
        )
    scene["pairs"] = records
    scene["alignment_pass_fraction"] = passed / total if total else None
    scene["gates"]["alignment_failed_frames"] = total - passed
    if points:
        return geo.deduplicate_seed(
            tuple(
                np.concatenate(value).astype(np.float32)
                for value in (points, colors, sizes)
            )
        )
    return (
        np.empty((0, 3), np.float32),
        np.empty((0, 3), np.float32),
        np.empty(0, np.float32),
    )


def append_seed(params, seed):
    import torch

    if len(seed[0]):
        addition = make_params(seed, params["means"].device)
        params = torch.nn.ParameterDict(
            {
                key: torch.nn.Parameter(
                    torch.cat([params[key].detach(), addition[key].detach()])
                )
                for key in params
            }
        )
    return params


def strip(images, path):
    height, gap = 192, 6
    images = [
        Image.fromarray(np.rint(np.clip(image, 0, 1) * 255).astype(np.uint8))
        for image in images
    ]
    images = [
        image.resize((round(image.width * height / image.height), height))
        for image in images
    ]
    canvas = Image.new(
        "RGB",
        (sum(image.width for image in images) + gap * (len(images) - 1), height),
        (128, 128, 128),
    )
    x = 0
    for image in images:
        canvas.paste(image, (x, 0))
        x += image.width + gap
    canvas.save(path)


def heldout_metrics(root, scene, params):
    import torch

    before, after = [], []
    # Only the temporary held-out camera gets gradients. Neither state nor
    # training deltas are changed by test-time evaluation.
    frozen = {key: value.detach() for key, value in params.items()}
    for view in scene["views"]:
        if view["role"] != "heldout":
            continue
        device = params["means"].device
        gt = (
            np.asarray(
                Image.open(root / view["image"]).convert("RGB"), dtype=np.float32
            )
            / 255
        )
        target = torch.tensor(gt, device=device)
        base = torch.tensor(view["viewmat"], dtype=torch.float32, device=device)
        camera = camera_opt_module(1, device)
        optimizer = torch.optim.Adam(camera.parameters(), lr=1e-4)
        rgb, _, _ = render_numpy(frozen, base, view["K"], view["width"], view["height"])
        before.append(geo.psnr(rgb, gt))
        for _ in range(200):
            rendered, _, _ = raster(
                frozen, camera(base, 0), view["K"], view["width"], view["height"]
            )
            loss = (rendered[0, ..., :3] - target).square().mean()
            loss.backward()
            optimizer.step()
            optimizer.zero_grad(set_to_none=True)
        rgb, _, _ = render_numpy(
            frozen, camera(base, 0).detach(), view["K"], view["width"], view["height"]
        )
        after.append(geo.psnr(rgb, gt))
    return float(np.mean(before)) if before else None, float(
        np.mean(after)
    ) if after else None


def compute_metrics(root, scene, params, out, flythrough=None):
    out.parent.mkdir(parents=True, exist_ok=True)
    anchors, anchor_scores, agreement = [], [], []
    for view in scene["views"]:
        is_anchor = view["role"] == ("anchor" if scene["kind"] == "pano" else "train")
        is_generated = view["role"] == "generated" and 1 <= view["frame"] <= 71
        if not is_anchor and not is_generated:
            continue
        rgb, _, _ = render_numpy(
            params, view["viewmat"], view["K"], view["width"], view["height"]
        )
        gt = (
            np.asarray(
                Image.open(root / view["image"]).convert("RGB"), dtype=np.float32
            )
            / 255
        )
        mask = None
        if is_anchor:
            if scene["kind"] == "pano":
                mask = (
                    geo.seam_weights(
                        np.asarray(view["viewmat"]),
                        np.asarray(view["K"]),
                        view["width"],
                        view["height"],
                        scene["seam_mask_deg"],
                    )
                    > 0
                )
            anchor_scores.append(geo.psnr(rgb, gt, mask))
            anchors.append(rgb)
        else:
            agreement.append(geo.psnr(rgb, gt))
    strip(anchors, out.parent / "anchors_strip.jpg")
    bank = geo.pose_bank(
        scene["kind"], scene["scale"], scene["circle"], scene["target"]
    )
    bank_images, holes = [], []
    for pose in bank:
        rgb, _, alpha = render_numpy(
            params, pose["viewmat"], geo.intrinsics(), 1024, 576
        )
        holes.append(float((alpha < 0.6).mean()))
        if pose["r"] == 0.1:
            bank_images.append(rgb)
    select = np.linspace(0, len(bank_images) - 1, 8).round().astype(int)
    strip([bank_images[i] for i in select], out.parent / "bank_strip.jpg")
    flicker = []
    if flythrough:
        for entry in parse_spec(flythrough):
            views = path_poses(scene, root, entry)
            ks, width, height = path_calibration(scene, entry, len(views))
            images, depths, alphas = zip(
                *(render_numpy(params, v, k, width, height) for v, k in zip(views, ks))
            )
            value = geo.depth_warp_flicker(images, depths, views, ks, alphas)
            if value is not None:
                flicker.append(value)
    heldout, refined = (
        heldout_metrics(root, scene, params)
        if any(v["role"] == "heldout" for v in scene["views"])
        else (None, None)
    )
    result = {
        "anchor_psnr": float(np.mean([v for v in anchor_scores if v is not None])),
        "hole_bank": float(np.mean(holes)),
        "flicker": float(np.mean(flicker)) if flicker else None,
        "heldout_psnr": heldout,
        "heldout_psnr_refined": refined,
        "agreement": float(np.mean(agreement)) if agreement else None,
        "alignment_pass_fraction": scene.get("alignment_pass_fraction"),
        "gaussians": scene["gaussians"],
        "steps": scene["steps"],
        "fit_seconds": scene["fit_seconds"],
        "gates": scene["gates"],
    }
    write_json(out, result)
    return result


def heldout_keys(records):
    """R5's withheld keyframes (r5-rules.json): every third perspective geometry keyframe in atlas order
    (indices 2, 5, 8, ...) and the second and fifth trajectory frames, left out of S-inc and S-scratch."""
    geometry = [
        r["id"]
        for r in records
        if r.get("kind") == "pinhole" and r.get("role") == "generated"
    ]
    trajectory = [r["id"] for r in records if r.get("role") == "trajectory"]
    return {k for i, k in enumerate(geometry) if i % 3 == 2} | {
        k for i, k in enumerate(trajectory) if i in (1, 4)
    }


def atlas_views(scene, out, atlas_path, heldout_only=False):
    """Add an atlas's accepted stills and trajectory frames that `scene` lacks as views (R5). An accepted still
    trains with its consolidated depth (confidence: trusted pixels), never its glass or edge colours (weight 0
    where solid but untrusted); a trajectory frame trains on colour, with depth only where its memory was
    protected. Withheld keyframes (`heldout_keys`) become "heldout" views. With `heldout_only`, only those are
    added (to measure a splat that never saw the atlas). Returns the seed for the new geometry keyframes'
    trusted points."""
    from . import memory as mem

    atlas_path = Path(atlas_path)
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    records = [
        r
        for r in mem.current(atlas)
        if r.get("kind") == "pinhole" and r.get("role") in ("generated", mem.TRAJECTORY)
    ]
    withheld = heldout_keys(records)
    have = {v["name"] for v in scene["views"]}
    points, colors, sizes = [], [], []
    for record in records:
        name = f"kf_{record['id']}"
        if name in have:
            continue
        held = record["id"] in withheld
        if heldout_only and not held:
            continue
        key = mem.load_keyframe(record, atlas_path.parent)
        geometry = record["role"] == "generated"
        surface = key.valid & (key.solid if geometry else key.trust)
        depth = np.where(surface, key.depth, np.nan).astype(np.float32)
        rgb_weight = (
            np.where(key.solid & ~key.trust, 0.0, 1.0)
            if geometry
            else np.ones(depth.shape)
        ).astype(np.float32)
        rgb_weight[key.retracted] = (
            0.0  # R6: what a later version disowned teaches nothing
        )
        view = add_view(
            out,
            scene,
            name,
            "heldout" if held else "train",
            key.image,
            key.viewmat,
            key.k,
            depth=depth,
            confidence=key.trust.astype(np.float32),
            rgb_weight=rgb_weight,
        )
        view.update(keyframe=record["id"], atlas_role=record["role"])
        if geometry and not held:
            y, x = np.indices(key.depth.shape)
            keep = key.trust & (y % 4 == 0) & (x % 4 == 0)
            points.append(backproject(key.depth, key.viewmat, key.k, keep))
            colors.append(key.image[keep] / 255.0)
            sizes.append(key.depth[keep] / key.k[0, 0] * 4)
    if not points:
        empty = np.zeros((0, 3), np.float32)
        return empty, empty, np.zeros(0, np.float32)
    return tuple(np.concatenate(v).astype(np.float32) for v in (points, colors, sizes))


def prepare_atlas(args):
    """S-scratch (R5): a panorama scene from the atlas's own panorama keyframe (its image and MoGe distance), plus
    every accepted still and trajectory frame, seeded from all of them at once."""
    from . import memory as mem

    atlas = json.loads(Path(args.atlas).read_text(encoding="utf-8"))
    record = next(r for r in mem.current(atlas) if r.get("kind") == "erp")
    key = mem.load_keyframe(record, Path(args.atlas).parent)
    image = np.asarray(key.image)
    erp = args.out / "atlas_erp.png"
    Image.fromarray(image).save(erp)
    moge = args.out / "atlas_moge.npz"
    np.savez_compressed(
        moge, distance=key.depth, mask=(key.valid & ~key.retracted).astype(np.float32)
    )
    pano = argparse.Namespace(
        erp=erp,
        moge=moge,
        out=args.out,
        seam_mask_deg=args.seam_mask_deg,
        retracted=key.retracted,
    )
    scene, seed = prepare_pano(pano)
    extra = atlas_views(scene, args.out, args.atlas)
    seed = tuple(np.concatenate([a, b]).astype(np.float32) for a, b in zip(seed, extra))
    scene.update(cap_max=len(seed[0]), atlas=str(Path(args.atlas).resolve()))
    return scene, seed


def atlas_metrics(args):
    """What a splat knows of an explored place (R5): the withheld keyframes' views (PSNR before and after the
    200-step camera refinement), its depth against their consolidated depth, and holes at every geometry
    keyframe's pose. The atlas's withheld views are added for the measurement only."""
    import torch
    from . import memory as mem

    scene = copy.deepcopy(read_scene(args.state))
    params, _ = load_state(args.state)
    scratch = args.out / "views"
    atlas_views(scene, args.out, args.atlas, heldout_only=True)
    for view in scene["views"]:
        if view["role"] != "heldout":
            continue
        for field in ("image", "data"):
            if field in view and not (args.out / view[field]).exists():
                source = Path(args.state) / view[field]
                if source.exists():
                    (args.out / view[field]).parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(source, args.out / view[field])
    heldout, refined = heldout_metrics(args.out, scene, params)
    depth_errors = []
    for view in scene["views"]:
        if view["role"] != "heldout" or "data" not in view:
            continue
        with np.load(args.out / view["data"]) as data:
            target = data["depth"]
        _, depth, alpha = render_numpy(
            params, view["viewmat"], view["K"], view["width"], view["height"]
        )
        valid = np.isfinite(target) & (target > 0) & (alpha > 0.6)
        if valid.any():
            depth_errors.append(
                float(np.median(np.abs(depth[valid] - target[valid]) / target[valid]))
            )
    atlas = json.loads(Path(args.atlas).read_text(encoding="utf-8"))
    holes = []
    for record in mem.current(atlas):
        if record.get("role") != "generated" or record.get("kind") != "pinhole":
            continue
        _, _, alpha = render_numpy(
            params, record["viewmat"], np.asarray(record["K"]), 1024, 576
        )
        holes.append(float((alpha < 0.6).mean()))
    with torch.no_grad():
        torch.cuda.empty_cache()
    result = {
        "state": str(Path(args.state).resolve()),
        "heldout_views": sum(v["role"] == "heldout" for v in scene["views"]),
        "heldout_psnr": heldout,
        "heldout_psnr_refined": refined,
        "heldout_depth_error": float(np.mean(depth_errors)) if depth_errors else None,
        "keyframe_holes": float(np.mean(holes)) if holes else None,
        "gaussians": len(params["means"]),
    }
    write_json(args.out / "atlas_metrics.json", result)
    if scratch.exists():
        shutil.rmtree(scratch)
    return result


def extend(args):
    """S-inc (R5): the previous state plus the atlas's new stills and trajectory frames as views and their
    trusted points as seeds, refitted for `steps` (the mixed schedule keeps the panorama anchors' quota)."""
    scene = copy.deepcopy(read_scene(args.state))
    copy_initial_assets(args.state, args.out, scene)
    params, previous = load_state(args.state)
    seed = atlas_views(scene, args.out, args.atlas)
    params = append_seed(params, seed)
    scene.update(
        parent=str(Path(args.state).resolve()),
        round=scene["round"] + 1,
        cap_max=scene["cap_max"] + len(seed[0]),
        atlas=str(Path(args.atlas).resolve()),
        seeded_keyframes=len(seed[0]),
    )
    return scene, params, previous


def run(args):
    import torch

    if not torch.cuda.is_available():
        raise RuntimeError(
            "Fitting and rendering require CUDA; CLI parsing and geometry are CPU-only"
        )
    torch.manual_seed(0)
    if args.command == "atlas-metrics":
        args.out.mkdir(parents=True, exist_ok=True)
        return atlas_metrics(args)
    if args.command in ("paths", "metrics"):
        scene = read_scene(args.state)
        params, _ = load_state(args.state)
        if args.command == "paths":
            return write_paths(args, scene, params)
        return compute_metrics(args.state, scene, params, args.out, args.flythrough)
    if args.out.exists() and any(args.out.iterdir()):
        raise ValueError("Output state directory must be empty")
    args.out.mkdir(parents=True, exist_ok=True)
    previous = None
    if args.command in ("init-pano", "init-orbit", "init-atlas"):
        scene, seed = {
            "init-pano": prepare_pano,
            "init-orbit": prepare_orbit,
            "init-atlas": prepare_atlas,
        }[args.command](args)
        np.savez_compressed(
            args.out / "seed.npz", points=seed[0], colors=seed[1], sizes=seed[2]
        )
        params = make_params(seed)
    elif args.command == "extend":
        scene, params, previous = extend(args)
    else:
        source = args.init if args.command == "final" else args.state
        scene = copy.deepcopy(read_scene(source))
        if args.command == "final" and scene["round"] != 0:
            raise ValueError("--init must be the C1 initial state (round 0)")
        if args.command == "refit" and args.null and args.pair:
            raise ValueError("--null cannot be combined with --pair")
        if not args.pair and not (args.command == "refit" and args.null):
            raise ValueError("Provide at least one --pair, or use refit --null")
        copy_initial_assets(source, args.out, scene)
        if args.command == "final":
            with np.load(source / "seed.npz") as seed:
                params = make_params((seed["points"], seed["colors"], seed["sizes"]))
            for view in scene["views"]:
                if "base_viewmat" in view:
                    view["viewmat"] = copy.deepcopy(view["base_viewmat"])
        else:
            params, previous = load_state(source)
        hole_seed = accept_pairs(args.pair, args.out, scene, args.ffmpeg)
        params = append_seed(params, hole_seed)
        scene.update(
            parent=str(source.resolve()),
            round=scene["round"] + 1,
            cap_max=scene["cap_max"] + len(hole_seed[0]),
            seeded_holes=len(hole_seed[0]),
            final=args.command == "final",
            null=args.command == "refit" and args.null,
        )
        fair = args.schedule == "fair"
        generated_steps = (
            args.steps if args.generated_steps is None else args.generated_steps
        )
        if not fair or not any(
            v["role"] == "generated" and v.get("accepted", False)
            for v in scene["views"]
        ):
            generated_steps = 0
        scene["refit"] = {
            "schedule": args.schedule,
            "generated_steps": generated_steps,
            "final_real_steps": (
                300 if args.final_real_steps is None else args.final_real_steps
            )
            if fair
            else 0,
            "holes_only": args.holes_only,
            "trust": args.trust,
        }
    state = fit(args.out, scene, params, args.steps, previous)
    save_state(args.out, scene, params, state)
    metrics = compute_metrics(args.out, scene, params, args.out / "metrics.json")
    summary = dict(state=str(args.out), **metrics)
    if "refit" in scene:
        summary["refit"] = scene["refit"]
    return summary


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        # Third-party progress stays off the machine-readable summary stream.
        with contextlib.redirect_stdout(sys.stderr):
            summary = run(args)
        print(
            json.dumps(
                dict(status="done", command=args.command, **summary),
                default=jsonable,
                allow_nan=False,
            )
        )
        return 0
    except Exception as exc:  # noqa: BLE001 - CLI boundary must emit one JSON failure
        print(
            json.dumps({"status": "failed", "command": args.command, "error": str(exc)})
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
