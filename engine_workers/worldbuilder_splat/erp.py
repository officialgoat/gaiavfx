# Vendored from the maintained engine implementation on branch worldbuilder (26 October 2026) for the worldbuilder engine worker
# (ADR 0018 §4); only the imports changed. The research module stays the reference; keep their numerics equal.
"""Equirectangular (ERP) panorama geometry for Worldbuilder, numpy only.

Moved here from `the maintained engine implementation` (whose module imports the private-worker campaign stack). Conventions:
2:1 ERP, longitude -180..180 left to right, yaw 0 at the centre, positive yaw turns right, positive pitch tilts up;
`fov` is the horizontal field of view in degrees.
"""

from __future__ import annotations

import numpy as np


def directions(width, height):
    """Unit view rays for every pixel of an equirectangular image."""
    lon = (np.arange(width) + 0.5) / width * 2 * np.pi - np.pi
    lat = np.pi / 2 - (np.arange(height) + 0.5) / height * np.pi
    lon, lat = np.meshgrid(lon, lat)
    return np.stack(
        [np.cos(lat) * np.sin(lon), np.sin(lat), np.cos(lat) * np.cos(lon)], -1
    )


def rotation(yaw, pitch=0.0):
    """Camera-to-world rotation: yaw turns right, pitch tilts up (degrees)."""
    y, p = np.radians(yaw), np.radians(pitch)
    turn = np.array([[np.cos(y), 0, np.sin(y)], [0, 1, 0], [-np.sin(y), 0, np.cos(y)]])
    tilt = np.array([[1, 0, 0], [0, np.cos(p), np.sin(p)], [0, -np.sin(p), np.cos(p)]])
    return turn @ tilt


def bilinear(image, u, v, wrap=False):
    h, w = image.shape[:2]
    u0, v0 = np.floor(u).astype(int), np.floor(v).astype(int)
    du, dv = (u - u0)[..., None], (v - v0)[..., None]
    if wrap:
        u1 = (u0 + 1) % w
        u0 = u0 % w
    else:
        u0, u1 = np.clip(u0, 0, w - 1), np.clip(u0 + 1, 0, w - 1)
    v0, v1 = np.clip(v0, 0, h - 1), np.clip(v0 + 1, 0, h - 1)
    img = image.astype(np.float32)
    top = img[v0, u0] * (1 - du) + img[v0, u1] * du
    bottom = img[v1, u0] * (1 - du) + img[v1, u1] * du
    return np.clip(top * (1 - dv) + bottom * dv, 0, 255).astype(np.uint8)


def project_to_erp(
    image, fov, width=2048, height=1024, yaw=0.0, pitch=0.0, fill=(0, 255, 0)
):
    """Place a pinhole frame on a green equirectangular canvas (the panorama outpaint input)."""
    h, w = image.shape[:2]
    focal = (w / 2) / np.tan(np.radians(fov) / 2)
    # World rays into camera space.
    rays = directions(width, height) @ rotation(yaw, pitch)
    z = rays[..., 2]
    # Pixel centres sit at integer indices for `bilinear`, as in `crop_view` (the asset lab's version was
    # half a pixel off, which cost 17 dB on a noise round trip).
    with np.errstate(divide="ignore", invalid="ignore"):
        u = focal * rays[..., 0] / z + w / 2 - 0.5
        v = -focal * rays[..., 1] / z + h / 2 - 0.5
    inside = (z > 0) & (u >= 0) & (u < w - 1) & (v >= 0) & (v < h - 1)
    canvas = np.empty((height, width, 3), np.uint8)
    canvas[:] = fill
    canvas[inside] = bilinear(image, u[inside], v[inside])
    return canvas


def roll_half(erp):
    """Move the seam (the joined left and right edges, heading 180) to the centre; applying it twice restores."""
    return np.roll(erp, erp.shape[1] // 2, axis=1)


def band_mask(width, height, fraction=96 / 2048, feather=24 / 2048):
    """A vertical band at the image centre: 1 inside, a linear ramp over `feather` (fraction of the width) on each
    side, 0 elsewhere. The default band is 96 px of a 2048 px panorama (the pytorch360convert seam example)."""
    x = np.arange(width) + 0.5 - width / 2
    half, ramp = fraction * width / 2, max(feather * width, 1e-6)
    alpha = np.clip((half + ramp - np.abs(x)) / ramp, 0.0, 1.0)
    return np.broadcast_to(alpha, (height, width)).astype(np.float32)


def paint_band(image, fraction=96 / 2048, color=(0, 255, 0)):
    """The image with its central band painted flat (no feather): the input of an editor's band fill."""
    out = image.copy()
    inside = band_mask(image.shape[1], image.shape[0], fraction, feather=0) > 0.5
    out[inside] = color
    return out


def composite_band(base, repaint, fraction=96 / 2048, feather=24 / 2048):
    """`repaint` inside the central band (with a feather), `base` exactly everywhere else."""
    alpha = band_mask(base.shape[1], base.shape[0], fraction, feather)[..., None]
    mixed = base.astype(np.float32) * (1 - alpha) + repaint.astype(np.float32) * alpha
    out = np.clip(np.rint(mixed), 0, 255).astype(np.uint8)
    out[alpha[..., 0] == 0] = base[alpha[..., 0] == 0]
    return out


def match_band_colour(base, repaint, fraction=96 / 2048, feather=24 / 2048, ring=0.02):
    """Shift the repaint's colour so its mean and spread match the base just outside the band (a cheap global
    stand-in for a Laplacian blend; an editor at a different resolution drifts in tone)."""
    w = base.shape[1]
    x = np.abs(np.arange(w) + 0.5 - w / 2)
    edge = fraction * w / 2 + feather * w
    near = (x >= edge) & (x < edge + ring * w)
    src = repaint[:, near].reshape(-1, 3).astype(np.float32)
    ref = base[:, near].reshape(-1, 3).astype(np.float32)
    scale = ref.std(0) / np.maximum(src.std(0), 1.0)
    shifted = (repaint.astype(np.float32) - src.mean(0)) * scale + ref.mean(0)
    return np.clip(np.rint(shifted), 0, 255).astype(np.uint8)


def green_fraction(image, tolerance=40):
    """Share of pixels that are still the fill green (0, 255, 0) within `tolerance`."""
    diff = np.abs(image.astype(np.int16) - np.array([0, 255, 0], np.int16))
    return float((diff.max(-1) <= tolerance).mean())


def plate_mask(width, height, fov, plate_w, plate_h, erode=8):
    """Where a pinhole plate of horizontal FOV `fov` lands on a `width` x `height` ERP canvas (yaw 0), eroded by
    `erode` canvas pixels so resampled edges do not count."""
    ones = np.full((plate_h, plate_w, 3), 255, np.uint8)
    canvas = project_to_erp(ones, fov, width=width, height=height, fill=(0, 0, 0))
    inside = canvas[..., 0] > 250
    for _ in range(erode):
        inside = (
            inside
            & np.roll(inside, 1, 0)
            & np.roll(inside, -1, 0)
            & np.roll(inside, 1, 1)
            & np.roll(inside, -1, 1)
        )
    return inside


def masked_psnr(a, b, mask):
    diff = a.astype(np.float64)[mask] - b.astype(np.float64)[mask]
    mse = float(np.mean(diff**2)) if diff.size else 0.0
    return 99.0 if mse == 0 else 10 * np.log10(255.0**2 / mse)


def crop_view(erp, yaw, fov=70.0, width=1024, height=576, pitch=0.0):
    """A pinhole view out of an equirectangular panorama; the seam wraps."""
    ph, pw = erp.shape[:2]
    focal = (width / 2) / np.tan(np.radians(fov) / 2)
    x, y = np.meshgrid(
        np.arange(width) + 0.5 - width / 2, -(np.arange(height) + 0.5 - height / 2)
    )
    rays = np.stack([x, y, np.full_like(x, focal)], -1)
    rays = rays / np.linalg.norm(rays, axis=-1, keepdims=True)
    world = rays @ rotation(yaw, pitch).T
    lon = np.arctan2(world[..., 0], world[..., 2])
    lat = np.arcsin(np.clip(world[..., 1], -1, 1))
    u = (lon + np.pi) / (2 * np.pi) * pw - 0.5
    v = (np.pi / 2 - lat) / np.pi * ph - 0.5
    return bilinear(erp, u, v, wrap=True).reshape(height, width, 3)
