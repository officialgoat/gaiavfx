"""A textured box room whose geometry is exact, for the take-gate tests and the browser service's fake ComfyUI
(ADR 0019 §4): pinhole frames with their z-depth and poses, and the `geometry.npz` GaiaSaveSetGeometry would save."""

import io
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).parents[2] / "engine_workers"))

from worldbuilder_splat import geometry as geo

W, H, FOV = 128, 72, 70.0


def colour(hit, phase):
    """A pattern fixed to the world, so every view of a surface shows the same texture."""
    x, y, z = hit[..., 0], hit[..., 1], hit[..., 2]
    base = np.sin(9 * x + phase) * np.sin(11 * y) + np.sin(7 * z - phase) * np.cos(5 * x + 13 * y)
    checker = ((np.floor(4 * x) + np.floor(4 * y) + np.floor(4 * z)) % 2) * 50
    value = np.clip(110 + 45 * base + checker, 0, 255)
    return np.stack([value, value * 0.9, value * 0.8], -1).astype(np.uint8)


def render(position, yaw, room=(2.0, 1.2, 0.8, 2.0), phase=0.0, pitch=0.0):
    """One pinhole frame from inside a box room (x and z half sizes, the ceiling above and the floor below the
    eye, y down): its image and exact z-depth, and its pose."""
    view, k = geo.crop_camera(yaw, pitch, FOV, W, H)
    view = view.copy()
    view[:3, 3] = -view[:3, :3] @ np.asarray(position, float)
    image, depth = seen(view, k, W, H, room, phase)
    return image, depth, view, k


def seen(view, k, width, height, room=(2.0, 1.2, 0.8, 2.0), phase=0.0):
    """What any pinhole camera (world-to-camera `view`, intrinsics `k`) sees of the room: its image and z-depth."""
    view = np.asarray(view, float)
    position = -view[:3, :3].T @ view[:3, 3]
    rays = (
        geo.camera_rays(np.asarray(k, float), width, height) @ view[:3, :3]
    )  # world directions, camera z = 1
    half_x, ceiling, floor, half_z = room
    low = np.array([-half_x, -ceiling, -half_z])
    high = np.array([half_x, floor, half_z])
    bound = np.where(rays > 0, high, low)
    with np.errstate(divide="ignore", invalid="ignore"):
        t = np.where(np.abs(rays) > 1e-9, (bound - position) / rays, np.inf)
    depth = t.min(-1)
    hit = position + rays * depth[..., None]
    return colour(hit, phase), depth.astype(np.float32)


def truck(count=8, **room):
    """A camera moving sideways through the room: a take a set can be built from."""
    return [render([-0.3 + 0.6 * s, 0, 0], 0.0, **room) for s in np.linspace(0, 1, count)]


def still(count=8):
    """A camera that stands still: a take the gate refuses."""
    return [render([0, 0, 0], 0.0) for _ in range(count)]


def geometry_npz(shots, people=None) -> bytes:
    """The shots as GaiaSaveSetGeometry's `geometry.npz`."""
    images, depths, views, ks = zip(*shots)
    count = len(shots)
    stream = io.BytesIO()
    np.savez_compressed(
        stream,
        image=np.stack(images),
        depth=np.stack(depths),
        confidence=np.ones((count, H, W), np.float32),
        extrinsics=np.stack(views).astype(np.float32),
        intrinsics=np.stack(ks).astype(np.float32),
        people=np.zeros((count, H, W), bool) if people is None else people,
        processed=np.asarray([H, W], np.int32),
    )
    return stream.getvalue()
