# Vendored from the maintained engine implementation on branch worldbuilder (26 October 2026) for the worldbuilder engine worker
# (ADR 0018 §4); only the imports changed. The research module stays the reference; keep their numerics equal.
"""Worldbuilder memory (R4): what a new camera may keep of a place's stored keyframes, and what it must invent.

A stored keyframe (the panorama first, later accepted probe stills) is an image with depth and a pose. `reproject`
looks at the keyframes from a new camera in two passes:
1. a z-buffer of their surface points (each splatted over its own footprint) gives the new view's depth;
2. every pixel with depth is looked up in each keyframe and kept only where that keyframe's own stored depth agrees
   (an occlusion test, so a surface the keyframe never saw is never painted with whatever stood in front of it).
Colours come from a bilinear lookup in the keyframe, not from warped splats, so nothing is smeared into rubber
(Lyra 2.0's warning about warped RGB); what no keyframe saw stays unknown, for a generator to fill. Rays that meet
no surface but leave through a keyframe's sky keep that sky.

Per pixel:
- protected: seen by a keyframe whose depth there is trustworthy (no depth edge, not excluded as glass or a mirror),
  away from depth edges in the new view, and magnified by at most `max_magnification` new pixels per stored
  pixel. Generators must keep it.
- unknown: no keyframe saw it.
- uncertain: seen, but not protected. Generators may redraw it.

Frame and units as worldbuilder_explore (OpenCV world, MoGe-2 metric). Pure numpy and Pillow.
"""

from __future__ import annotations

import hashlib
import json
import math
import os
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from . import geometry as geo
from PIL import Image, ImageFilter
from .erp import directions

TAU = 2 * math.pi
GREEN = (0, 255, 0)


# ---------------------------------------------------------------- keyframes


@dataclass
class Keyframe:
    """One stored observation. `depth` is the distance along each ray for an equirectangular keyframe and the
    z-depth for a pinhole one; `valid` false is sky or no geometry; `trust` marks the depth worth relying on for
    protected memory; `solid` marks geometry that carves and occupies the free-space map (all `valid` by default;
    a glass pane with a fitted plane is solid but never trusted, one without a plane is neither)."""

    id: str
    kind: str  # "erp" or "pinhole"
    image: np.ndarray
    depth: np.ndarray
    valid: np.ndarray
    trust: np.ndarray
    viewmat: np.ndarray  # world to camera
    k: np.ndarray | None = None
    solid: np.ndarray | None = None
    retracted: np.ndarray | None = (
        None  # R6: pixels a later atlas version disowned (never seen, carved or trusted)
    )

    def __post_init__(self):
        if self.kind not in ("erp", "pinhole"):
            raise ValueError(f"unknown keyframe kind {self.kind!r}")
        if self.kind == "pinhole" and self.k is None:
            raise ValueError("a pinhole keyframe needs intrinsics")
        self.viewmat = np.asarray(self.viewmat, dtype=float)
        self.depth = np.asarray(self.depth, dtype=np.float32)
        self.valid = (
            np.asarray(self.valid, dtype=bool)
            & np.isfinite(self.depth)
            & (self.depth > 0)
        )
        self.trust = np.asarray(self.trust, dtype=bool) & self.valid
        self.solid = (
            self.valid.copy()
            if self.solid is None
            else np.asarray(self.solid, dtype=bool) & self.valid
        )
        # Product change (ADR 0019 §4): retraction no longer depends on depth, so a person whose pixels have no
        # depth still teaches no colour. Every research retraction lay on valid depth, so their numerics are equal.
        self.retracted = (
            np.zeros(self.valid.shape, bool)
            if self.retracted is None
            else np.asarray(self.retracted, dtype=bool)
        )
        self.solid &= ~self.retracted
        self.trust &= self.solid
        shape = self.depth.shape
        if (
            self.image.shape[:2] != shape
            or self.valid.shape != shape
            or self.trust.shape != shape
            or self.solid.shape != shape
            or self.retracted.shape != shape
        ):
            raise ValueError("a keyframe's image, depth and masks must share one grid")
        if self.k is not None:
            self.k = np.asarray(self.k, dtype=float)
        # Bilinear lookups need finite depth everywhere; sky takes the farthest surface (never trusted there).
        far = float(self.depth[self.valid].max()) if self.valid.any() else 1.0
        self.surface = np.where(self.valid, self.depth, far).astype(np.float32)

    @property
    def rotation(self):
        return self.viewmat[:3, :3]

    @property
    def centre(self):
        return -self.rotation.T @ self.viewmat[:3, 3]

    @property
    def wrap(self):
        return self.kind == "erp"

    def pixel_angle(self):
        """The angle one stored pixel spans (radians), at the image centre for a pinhole keyframe."""
        return TAU / self.depth.shape[1] if self.kind == "erp" else 1.0 / self.k[0, 0]

    def local_rays(self):
        h, w = self.depth.shape
        if self.kind == "erp":
            return directions(w, h) @ geo.F  # unit rays
        return geo.camera_rays(self.k, w, h)  # z = 1

    def points(self, stride=2):
        """World points of the valid pixels on a `stride` grid, and each one's footprint (the world size of one
        stride cell there)."""
        pick = (slice(stride // 2, None, stride), slice(stride // 2, None, stride))
        depth = self.depth[pick]
        keep = (self.valid & ~self.retracted)[pick]
        local = self.local_rays()[pick][keep] * depth[keep, None]
        return local @ self.rotation + self.centre, depth[
            keep
        ] * self.pixel_angle() * stride

    def locate(self, world):
        """Where world points fall in this keyframe: pixel coordinates (u, v), the depth this keyframe would store
        for them, and whether they are in its frame."""
        local = (np.asarray(world, dtype=float) - self.centre) @ self.rotation.T
        h, w = self.depth.shape
        if self.kind == "erp":
            along = np.linalg.norm(local, axis=-1)
            inside = along > 1e-9
            local = np.where(inside[:, None], local, [0.0, 0.0, 1.0])
            u, v = geo.erp_coordinates(local, w, h)
            return u, v, along, inside
        return self._pinhole(local)

    def locate_direction(self, dirs):
        """Where directions (points at infinity) fall in this keyframe: (u, v, inside)."""
        local = np.asarray(dirs, dtype=float) @ self.rotation.T
        h, w = self.depth.shape
        if self.kind == "erp":
            u, v = geo.erp_coordinates(local, w, h)
            return u, v, np.ones(len(local), dtype=bool)
        u, v, _, inside = self._pinhole(local)
        return u, v, inside

    def _pinhole(self, local):
        h, w = self.depth.shape
        z = local[:, 2]
        safe = np.where(z > 1e-9, z, 1.0)
        u = self.k[0, 0] * local[:, 0] / safe + self.k[0, 2] - 0.5
        v = self.k[1, 1] * local[:, 1] / safe + self.k[1, 2] - 0.5
        inside = (
            (z > 1e-9) & (u >= -0.5) & (u <= w - 0.5) & (v >= -0.5) & (v <= h - 0.5)
        )
        return u, v, z, inside

    def sample(self, array, u, v):
        return geo.sample_float(array, u, v, wrap=self.wrap)

    def nearest(self, array, u, v):
        h, w = array.shape[:2]
        x = np.floor(np.asarray(u) + 0.5).astype(np.int64)
        y = np.floor(np.asarray(v) + 0.5).astype(np.int64).clip(0, h - 1)
        return array[y, x % w if self.wrap else x.clip(0, w - 1)]


def depth_edges(depth, valid, threshold=0.05, wrap=False):
    """Pixels on a depth discontinuity, grown by one pixel: the log depth jumps by more than `threshold` to a
    neighbour (MoGe's soft edges leave flying pixels there), or the neighbour has no depth at all."""
    depth = np.asarray(depth, dtype=float)
    valid = np.asarray(valid, dtype=bool) & np.isfinite(depth) & (depth > 0)
    logd = np.log(np.where(valid, depth, 1.0))
    edge = np.zeros_like(valid)
    for axis in (0, 1):
        if axis == 1 and wrap:
            a, b = logd, np.roll(logd, -1, 1)
            va, vb = valid, np.roll(valid, -1, 1)
        else:
            cut = [slice(None)] * 2
            a = logd[tuple(cut[:axis] + [slice(None, -1)])]
            b = logd[tuple(cut[:axis] + [slice(1, None)])]
            va = valid[tuple(cut[:axis] + [slice(None, -1)])]
            vb = valid[tuple(cut[:axis] + [slice(1, None)])]
        jump = (va & vb & (np.abs(a - b) > threshold)) | (va != vb)
        if axis == 1 and wrap:
            edge |= jump | np.roll(jump, 1, 1)
        else:
            lead = [slice(None)] * 2
            lead[axis] = slice(None, -1)
            trail = [slice(None)] * 2
            trail[axis] = slice(1, None)
            edge[tuple(lead)] |= jump
            edge[tuple(trail)] |= jump
    return grow(edge & valid, 1)


def _components(mask):
    """Connected regions of a boolean mask (8-neighbours), as a list of boolean masks."""
    mask = np.asarray(mask, dtype=bool)
    h, w = mask.shape
    label = np.zeros((h, w), np.int32)
    regions = []
    for y0, x0 in zip(*np.nonzero(mask)):
        if label[y0, x0]:
            continue
        n = len(regions) + 1
        stack, pixels = [(y0, x0)], []
        label[y0, x0] = n
        while stack:
            y, x = stack.pop()
            pixels.append((y, x))
            for dy in (-1, 0, 1):
                for dx in (-1, 0, 1):
                    yy, xx = y + dy, x + dx
                    if (
                        0 <= yy < h
                        and 0 <= xx < w
                        and mask[yy, xx]
                        and not label[yy, xx]
                    ):
                        label[yy, xx] = n
                        stack.append((yy, xx))
        region = np.zeros((h, w), bool)
        ys, xs = zip(*pixels)
        region[list(ys), list(xs)] = True
        regions.append(region)
    return regions


def fit_glass(rays, depth, valid, glass, ring=6, tries=200, seed=0, min_inliers=0.3):
    """Depth for every pane of `glass` (a mask of windows, glass, mirrors or water): the plane through the
    observed surface around the pane (RANSAC over a `ring`-pixel band outside it, then least squares on the
    inliers), met by each pane pixel's ray (`rays`: H x W x 3, scaled so depth x ray is the point). A reflection
    or the view outside then stops at the pane instead of becoming a room behind it (the user's R4 note on H1).
    Returns the new depth and the pane pixels that got a plane."""
    rays, depth = np.asarray(rays, dtype=float), np.asarray(depth, dtype=float)
    out, planed = depth.copy(), np.zeros(depth.shape, bool)
    rng = np.random.default_rng(seed)
    for pane in _components(glass):
        band = (
            grow(pane, ring)
            & ~pane
            & np.asarray(valid, bool)
            & ~np.asarray(glass, bool)
        )
        points = rays[band] * depth[band][:, None]
        if len(points) < 50:
            continue
        scale = float(np.median(np.linalg.norm(points, axis=1)))
        best, normal, offset = 0, None, 0.0
        for _ in range(tries):
            a, b, c = points[rng.choice(len(points), 3, replace=False)]
            n = np.cross(b - a, c - a)
            if np.linalg.norm(n) < 1e-12:
                continue
            n /= np.linalg.norm(n)
            count = int(np.sum(np.abs(points @ n - n @ a) < 0.02 * scale))
            if count > best:
                best, normal, offset = count, n, float(n @ a)
        if normal is None or best < min_inliers * len(points):
            continue
        inliers = points[np.abs(points @ normal - offset) < 0.02 * scale]
        centroid = inliers.mean(0)
        normal = np.linalg.svd(inliers - centroid, full_matrices=False)[2][-1]
        offset = float(normal @ centroid)
        facing = rays[pane] @ normal
        t = np.where(
            np.abs(facing) > 1e-9, offset / np.where(facing == 0, 1, facing), -1
        )
        ok = (t > 0) & (t < 5 * scale)
        ys, xs = np.nonzero(pane)
        out[ys[ok], xs[ok]] = t[ok]
        planed[ys[ok], xs[ok]] = True
    return out.astype(np.float32), planed


def pano_keyframe(
    id,
    image,
    distance,
    mask,
    centre=(0.0, 0.0, 0.0),
    exclude=None,
    edge=0.05,
    glass=None,
):
    """The panorama as a keyframe: MoGe-2's distance along each ray, its mask (false: sky). `exclude` is never
    trusted (an unrepaired seam, say). `glass` panes get a fitted plane (`fit_glass`): solid, never trusted; a
    pane no plane fits is neither."""
    distance = np.asarray(distance, dtype=np.float32)
    valid = np.asarray(mask, dtype=bool) & np.isfinite(distance) & (distance > 0)
    solid = valid.copy()
    trust = valid & ~depth_edges(distance, valid, edge, wrap=True)
    if glass is not None:
        glass = np.asarray(glass, dtype=bool)
        h, w = distance.shape
        distance, planed = fit_glass(directions(w, h) @ geo.F, distance, valid, glass)
        valid |= planed
        solid = valid & ~(glass & ~planed)
        trust &= ~glass
    if exclude is not None:
        trust &= ~np.asarray(exclude, dtype=bool)
    view = np.eye(4)
    view[:3, 3] = -np.asarray(centre, dtype=float)
    return Keyframe(
        id, "erp", np.asarray(image), distance, valid, trust, view, solid=solid
    )


def view_keyframe(
    id, image, viewmat, k, depth, valid=None, exclude=None, edge=0.05, glass=None
):
    """A perspective still as a keyframe: its pose, intrinsics and z-depth (`valid` false: sky); `exclude` and
    `glass` as for `pano_keyframe`."""
    depth = np.asarray(depth, dtype=np.float32)
    valid = (
        (np.isfinite(depth) & (depth > 0))
        if valid is None
        else np.asarray(valid, dtype=bool)
    )
    solid = valid.copy()
    trust = valid & ~depth_edges(depth, valid, edge)
    if glass is not None:
        glass = np.asarray(glass, dtype=bool)
        h, w = depth.shape
        depth, planed = fit_glass(
            geo.camera_rays(np.asarray(k), w, h), depth, valid, glass
        )
        valid |= planed
        solid = valid & ~(glass & ~planed)
        trust &= ~glass
    if exclude is not None:
        trust &= ~np.asarray(exclude, dtype=bool)
    return Keyframe(
        id, "pinhole", np.asarray(image), depth, valid, trust, viewmat, k, solid=solid
    )


def seam_band(width, height, degrees=8.5):
    """The pixels within `degrees` of an equirectangular panorama's joined edges (heading 180)."""
    lon = (np.arange(width) + 0.5) / width * 360.0 - 180.0
    return np.broadcast_to(180.0 - np.abs(lon) <= degrees, (height, width)).copy()


# ---------------------------------------------------------------- reprojection


@dataclass
class Memory:
    rgb: np.ndarray  # uint8; zero where unknown
    known: np.ndarray
    protected: np.ndarray
    depth: np.ndarray  # the new view's z-depth; inf where no surface (sky or unknown)
    source: np.ndarray  # index of the keyframe each pixel came from, -1 where unknown
    magnification: np.ndarray  # new pixels per stored pixel; inf where unknown

    @property
    def unknown(self):
        return ~self.known

    @property
    def uncertain(self):
        return self.known & ~self.protected

    def fractions(self):
        return {
            "protected": round(float(self.protected.mean()), 4),
            "uncertain": round(float(self.uncertain.mean()), 4),
            "unknown": round(float(self.unknown.mean()), 4),
            "magnification_median": round(
                float(np.median(self.magnification[self.known])), 3
            )
            if self.known.any()
            else None,
        }


def zbuffer(keyframes, viewmat, k, width, height, stride=2, max_radius=8):
    """The new view's z-depth from every keyframe's surface points, each splatted as a square over its projected
    footprint (at most `max_radius` pixels from its centre; closer than that leaves cracks, which stay unknown).
    Empty pixels are inf."""
    viewmat, k = np.asarray(viewmat, dtype=float), np.asarray(k, dtype=float)
    flat = np.full(width * height, np.inf)
    for key in keyframes:
        world, footprint = key.points(stride)
        cam = world @ viewmat[:3, :3].T + viewmat[:3, 3]
        front = cam[:, 2] > 1e-6
        cam, footprint = cam[front], footprint[front]
        z = cam[:, 2]
        x = np.floor(k[0, 0] * cam[:, 0] / z + k[0, 2]).astype(np.int64)
        y = np.floor(k[1, 1] * cam[:, 1] / z + k[1, 2]).astype(np.int64)
        # Off the optical axis a footprint projects larger (radially by distance / z^2, not 1 / z), plus a quarter
        # for surfaces the keyframe saw more obliquely than the new view does.
        size = footprint * k[0, 0] * np.linalg.norm(cam, axis=1) / z**2
        radius = np.clip(np.ceil(0.625 * size - 0.5), 0, max_radius).astype(np.int64)
        near = (
            (x >= -radius)
            & (x < width + radius)
            & (y >= -radius)
            & (y < height + radius)
        )
        x, y, z, radius = x[near], y[near], z[near], radius[near]
        for r in np.unique(radius):
            pick = radius == r
            xs, ys, zs = x[pick], y[pick], z[pick]
            for dy in range(-r, r + 1):
                for dx in range(-r, r + 1):
                    xx, yy = xs + dx, ys + dy
                    ok = (xx >= 0) & (xx < width) & (yy >= 0) & (yy < height)
                    np.minimum.at(flat, yy[ok] * width + xx[ok], zs[ok])
    return flat.reshape(height, width)


def _meet(key, centre, rays, start, iterations=4):
    """Each ray's distance (in camera-z units: `rays` have z = 1) to where it meets `key`'s stored surface,
    refined by secant steps from the z-buffer's `start` (blocky, since its splats are flat squares). Returns the
    distance, the residual (stored depth minus the point's depth in the keyframe) and the lookup there."""

    def residual(t):
        u, v, along, inside = key.locate(centre + rays * t[:, None])
        return key.sample(key.surface, u, v) - along, u, v, along, inside

    a, (fa, *_) = start, residual(start)
    b = start * 1.02
    fb, u, v, along, inside = residual(b)
    for _ in range(iterations):
        slope = fb - fa
        ok = np.abs(slope) > 1e-12
        step = np.where(ok, b - fb * (b - a) / np.where(ok, slope, 1.0), b)
        a, fa = b, fb
        b = np.clip(step, 0.5 * start, 1.5 * start)
        fb, u, v, along, inside = residual(b)
    return b, fb, u, v, along, inside


def reproject(
    keyframes,
    viewmat,
    k,
    width,
    height,
    *,
    stride=2,
    tolerance=0.01,
    reach=0.25,
    max_magnification=5.0,
    edge=0.05,
    edge_px=2,
):
    """The memory of a new view (see the module docstring). A keyframe sees a pixel when the pixel's ray meets
    the keyframe's stored surface within `tolerance` (relative) and within `reach` of the z-buffer's nearest
    surface. Of the keyframes that see the nearest surface (within 3% of the nearest meeting), the one that saw it
    at the finest resolution (least magnified) supplies the pixel."""
    viewmat, k = np.asarray(viewmat, dtype=float), np.asarray(k, dtype=float)
    rotation = viewmat[:3, :3]
    centre = -rotation.T @ viewmat[:3, 3]
    z = zbuffer(keyframes, viewmat, k, width, height, stride)
    rays = (
        geo.camera_rays(k, width, height) @ rotation
    )  # world directions, camera z = 1
    rgb = np.zeros((height, width, 3), np.float32)
    best = np.full((height, width), np.inf)
    source = np.full((height, width), -1, np.int16)
    trusted = np.zeros((height, width), bool)
    depth = np.full((height, width), np.inf)

    hit = np.isfinite(z)
    rows, cols = np.nonzero(hit)
    start = z[rows, cols]
    met = []
    for key in keyframes:
        t, res, u, v, along, inside = _meet(key, centre, rays[rows, cols], start)
        seen = (
            inside
            & (
                key.sample((key.valid & ~key.retracted).astype(np.float32), u, v)
                >= 0.999
            )
            & (np.abs(res) <= tolerance * along)
            & (np.abs(t - start) <= reach * start)
        )
        met.append(
            (np.where(seen, t, np.inf), u, v, k[0, 0] * along * key.pixel_angle() / t)
        )
    if met:
        nearest = np.min([t for t, *_ in met], axis=0)
        chosen = np.full(len(rows), np.inf)
        for index, (key, (t, u, v, magnification)) in enumerate(zip(keyframes, met)):
            better = np.isfinite(t) & (t <= 1.03 * nearest) & (magnification < chosen)
            if not better.any():
                continue
            chosen[better] = magnification[better]
            r, c = rows[better], cols[better]
            rgb[r, c] = key.sample(key.image.astype(np.float32), u[better], v[better])
            trusted[r, c] = (
                key.sample(key.trust.astype(np.float32), u[better], v[better]) >= 0.999
            )
            best[r, c] = magnification[better]
            depth[r, c] = t[better]
            source[r, c] = index

    # Sky: rays that meet no stored surface but leave through a keyframe's sky (directions only, no parallax).
    rows, cols = np.nonzero(~hit)
    dirs = geo.unit(rays[rows, cols]) if len(rows) else np.zeros((0, 3))
    for index, key in enumerate(keyframes):
        pending = source[rows, cols] < 0
        if not pending.any():
            break
        u, v, inside = key.locate_direction(dirs[pending])
        sky = inside & (key.sample(key.valid.astype(np.float32), u, v) <= 0.001)
        r, c = rows[pending][sky], cols[pending][sky]
        rgb[r, c] = key.sample(key.image.astype(np.float32), u[sky], v[sky])
        trusted[r, c] = True
        best[r, c] = k[0, 0] * key.pixel_angle()
        source[r, c] = index

    known = source >= 0
    surface = np.isfinite(depth)
    edges = grow(depth_edges(depth, surface, edge), edge_px) & surface
    protected = known & trusted & (best <= max_magnification) & ~edges
    return Memory(
        np.rint(np.clip(rgb, 0, 255)).astype(np.uint8),
        known,
        protected,
        depth,
        source,
        best,
    )


# ---------------------------------------------------------------- masks, fills and merges


def grow(mask, pixels):
    """A boolean mask grown by `pixels` (a square max filter, in steps PIL can take)."""
    mask = np.asarray(mask, dtype=bool)
    if pixels <= 0 or not mask.any():
        return mask.copy()
    image = Image.fromarray(mask.astype(np.uint8) * 255)
    left = int(pixels)
    while left > 0:
        step = min(left, 8)
        image = image.filter(ImageFilter.MaxFilter(2 * step + 1))
        left -= step
    return np.asarray(image) > 0


def edit_region(memory: Memory, grow_px=8):
    """What a generator may change: everything not protected, grown by `grow_px` so its fill can blend."""
    return grow(~memory.protected, grow_px)


def paint(rgb, region, colour=GREEN):
    """The editor's input: the memory with the region to fill painted one flat colour."""
    out = np.array(rgb, dtype=np.uint8, copy=True)
    out[np.asarray(region, dtype=bool)] = colour
    return out


def feather_alpha(region, pixels):
    """1 inside `region`, falling linearly to 0 over `pixels` outside it."""
    region = np.asarray(region, dtype=bool)
    alpha = region.astype(np.float32)
    grown = region
    for i in range(1, int(pixels) + 1):
        grown = grow(grown, 1)
        alpha = np.maximum(alpha, (1 - i / (pixels + 1)) * grown)
    return alpha


def protected_merge(memory_rgb, region, edited, feather=8, ring=16):
    """The editor's output inside `region` (what it was asked to fill, `edit_region`), the memory everywhere else,
    exactly (the seam merge pattern, for any mask): the fill is colour matched to the memory in a ring of kept
    pixels around the region, then blended in over the region's first `feather` pixels, so no pixel outside the
    region changes. Returns the merged image and a report (`boundary_error` is the mean colour difference of the
    matched fill against the memory in the ring, 0..1: how well the editor kept what it was given)."""
    memory_rgb = np.asarray(memory_rgb, dtype=np.uint8)
    region = np.asarray(region, dtype=bool)
    edited = np.asarray(edited, dtype=np.float32)
    if edited.shape != memory_rgb.shape:
        raise ValueError("resize the edited image to the memory's size first")
    alpha = 1 - feather_alpha(
        ~region, feather
    )  # 0 on kept pixels, 1 from `feather` px inside the region
    ring_mask = grow(region, ring) & ~region
    report = {"region": round(float(region.mean()), 4)}
    matched = edited
    if ring_mask.sum() >= 64:
        e, m = edited[ring_mask], memory_rgb[ring_mask].astype(np.float32)
        scale = np.clip(m.std(0) / np.maximum(e.std(0), 1.0), 0.7, 1.4)
        matched = (edited - e.mean(0)) * scale + m.mean(0)
        report["colour_shift"] = round(
            float(np.abs(e.mean(0) - m.mean(0)).mean() / 255), 4
        )
        report["boundary_error"] = round(
            float(np.abs(np.clip(matched[ring_mask], 0, 255) - m).mean() / 255), 4
        )
    merged = (
        memory_rgb * (1 - alpha[..., None])
        + np.clip(matched, 0, 255) * alpha[..., None]
    )
    merged = np.rint(merged).astype(np.uint8)
    keep = ~region
    report["kept_identical"] = bool(np.array_equal(merged[keep], memory_rgb[keep]))
    report["kept"] = round(float(keep.mean()), 4)
    return merged, report


def disagreement(first, second, region=None, blur=4):
    """Where two fills of the same view disagree (DualDiff3D's variance across samples, with two): the mean
    absolute colour difference after a `blur` px box blur, 0..1 per pixel, and its mean over `region`."""

    def soft(image):
        return np.asarray(
            Image.fromarray(np.asarray(image, dtype=np.uint8)).filter(
                ImageFilter.BoxBlur(blur)
            ),
            dtype=np.float32,
        )

    diff = np.abs(soft(first) - soft(second)).mean(-1) / 255
    pick = (
        np.ones(diff.shape, bool) if region is None else np.asarray(region, dtype=bool)
    )
    return diff, round(float(diff[pick].mean()) if pick.any() else 0.0, 4)


# ---------------------------------------------------------------- atlas files


def _relative(path: Path, base: Path) -> str:
    return Path(os.path.relpath(path, base)).as_posix()


def _sha256(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


APPEARANCE_ONLY = (
    "appearance-only"  # a record's role when consolidation rejected its geometry
)
TRAJECTORY = "trajectory"  # a probe video's in-between frame: splat supervision only, never protected memory
NOT_GEOMETRY = (APPEARANCE_ONLY, TRAJECTORY)


def keyframe_record(key: Keyframe, folder: Path, base: Path, **extra) -> dict:
    """Write a keyframe's image and arrays into `folder` and describe it for an atlas stored in `base`."""
    folder.mkdir(parents=True, exist_ok=True)
    image = folder / f"{key.id}.png"
    data = folder / f"{key.id}.npz"
    Image.fromarray(np.asarray(key.image, dtype=np.uint8)).save(image)
    arrays = {
        "depth": key.depth,
        "valid": key.valid,
        "trust": key.trust,
        "solid": key.solid,
    }
    if key.retracted.any():
        arrays["retracted"] = key.retracted
    np.savez_compressed(data, **arrays)
    record = {
        "id": key.id,
        "kind": key.kind,
        "image": _relative(image, base),
        "data": _relative(data, base),
        # The atlas file is what jobs fingerprint: the files' hashes make it change whenever a keyframe does.
        "sha256": {"image": _sha256(image), "data": _sha256(data)},
        "viewmat": key.viewmat.tolist(),
        "position": [round(float(v), 6) for v in key.centre],
        **extra,
    }
    if key.k is not None:
        record["K"] = key.k.tolist()
    return record


def load_keyframe(record: dict, base: Path) -> Keyframe:
    with np.load(base / record["data"]) as data:
        depth, valid, trust = data["depth"], data["valid"], data["trust"]
        solid = data["solid"] if "solid" in data.files else None
        retracted = data["retracted"] if "retracted" in data.files else None
    image = np.asarray(Image.open(base / record["image"]).convert("RGB"))
    return Keyframe(
        record["id"],
        record["kind"],
        image,
        depth,
        valid,
        trust,
        record["viewmat"],
        record.get("K"),
        solid=solid,
        retracted=retracted,
    )


def load_keyframes(atlas_path: Path, appearance=False) -> list[Keyframe]:
    """An atlas's geometry keyframes; with `appearance` also those that are appearance only (rejected stills and
    trajectory frames, whose surfaces must never occlude or carve anything)."""
    atlas_path = Path(atlas_path)
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    return [
        load_keyframe(record, atlas_path.parent)
        for record in current(atlas)
        if appearance or record.get("role") not in NOT_GEOMETRY
    ]


def current(atlas: dict) -> list[dict]:
    """An atlas's keyframe records that no later record supersedes (R6 retraction); earlier versions keep theirs."""
    superseded = {r["supersedes"] for r in atlas["keyframes"] if r.get("supersedes")}
    return [r for r in atlas["keyframes"] if r["id"] not in superseded]


def world_points(key: Keyframe) -> np.ndarray:
    """Every pixel's stored surface point (H x W x 3, world frame; meaningless where not valid)."""
    local = key.local_rays() * key.depth[..., None]
    return local @ key.rotation + key.centre


def retraction_mask(key: Keyframe, region, lower, voxel) -> np.ndarray:
    """The pixels whose stored surface lands in `region` (a map mask over (x, z) cells from `lower` with `voxel`,
    every height: the map is seen from above)."""
    world = world_points(key)
    i = np.floor((world[..., 0] - lower[0]) / voxel).astype(np.int64)
    k = np.floor((world[..., 2] - lower[2]) / voxel).astype(np.int64)
    inside = (
        key.valid & (i >= 0) & (i < region.shape[0]) & (k >= 0) & (k < region.shape[1])
    )
    out = np.zeros(key.valid.shape, bool)
    out[inside] = region[i[inside], k[inside]]
    return out


def retract(
    atlas: dict, base: Path, folder: Path, record: dict, mask=None, reason: str = ""
) -> dict:
    """A new atlas version in which `record` is superseded: with `mask`, by a copy whose pixels in `mask` are
    retracted (its arrays written into `folder`); without, by a copy that is appearance only (the whole
    keyframe). The old record stays in this atlas and in every earlier version. Returns the new atlas."""
    from . import explore as ex

    root = record["id"].split("~")[0]
    n = 1 + sum(1 for r in atlas["keyframes"] if r["id"].startswith(root + "~"))
    new_id = f"{root}~{n}"
    if mask is None:
        new = {
            **record,
            "id": new_id,
            "supersedes": record["id"],
            "role": APPEARANCE_ONLY,
            "retraction": {"whole": True, "reason": reason},
        }
    else:
        key = load_keyframe(record, base)
        key.id = new_id
        key.retracted = key.retracted | (np.asarray(mask, bool) & key.valid)
        key.solid &= ~key.retracted
        key.trust &= ~key.retracted
        kept = ("id", "image", "data", "sha256", "viewmat", "position", "K")
        extra = {k: v for k, v in record.items() if k not in kept}
        extra["supersedes"] = record["id"]
        extra["retraction"] = {
            "whole": False,
            "pixels": round(float(key.retracted.mean()), 4),
            "reason": reason,
        }
        new = keyframe_record(key, folder, base, **extra)
    return ex.add_keyframe(atlas, new, "retracted")


def select_keyframes(keyframes, viewmat, k, width, height, limit=6, stride=16):
    """The keyframes that see most of a view (Lyra 2.0's retrieval by 3D overlap): each keyframe's points on a
    coarse grid projected into the view, ranked by how many land in it; an equirectangular keyframe always stays
    (it anchors the place). Reprojection cost grows with every keyframe, so a long exploration needs this."""
    viewmat, k = np.asarray(viewmat, dtype=float), np.asarray(k, dtype=float)
    scores = []
    for key in keyframes:
        world, _ = key.points(stride)
        cam = world @ viewmat[:3, :3].T + viewmat[:3, 3]
        z = cam[:, 2]
        front = z > 1e-6
        u = k[0, 0] * cam[front, 0] / z[front] + k[0, 2]
        v = k[1, 1] * cam[front, 1] / z[front] + k[1, 2]
        inside = (u >= 0) & (u < width) & (v >= 0) & (v < height)
        scores.append(int(inside.sum()))
    order = sorted(range(len(keyframes)), key=lambda i: -scores[i])
    keep = [i for i in order if keyframes[i].kind == "erp"]
    keep += [i for i in order if keyframes[i].kind != "erp" and scores[i] > 0]
    return [keyframes[i] for i in sorted(keep[:limit] if len(keep) > limit else keep)]


def pose_refine(
    keyframes,
    frame,
    viewmat,
    k,
    step,
    size=(256, 144),
    lateral=0.3,
    forward=0.3,
    turn=6.0,
):
    """Where a generated frame was actually seen from (the user's R4 note: a still "only logical if this view is
    taken further from the right"). The memory is reprojected at camera offsets around the planned pose (left and
    right and forward and back by up to `lateral` and `forward` x `step`, turned by up to `turn` degrees), coarse
    to fine, and the offset whose memory best matches the frame's colours on protected pixels wins. Returns the
    refined world-to-camera matrix and a report (the offset in steps and degrees, the error at the planned and at
    the refined pose)."""
    from PIL import Image as _Image

    w, h = size
    frame = np.asarray(
        _Image.fromarray(np.asarray(frame, dtype=np.uint8)).resize(
            (w, h), _Image.BILINEAR
        ),
        dtype=np.float32,
    )
    k = np.asarray(k, dtype=float).copy()
    scale = w / (2 * k[0, 2])
    small = k.copy()
    small[:2] *= scale
    base = np.asarray(viewmat, dtype=float)
    rotation = base[:3, :3]
    centre = -rotation.T @ base[:3, 3]
    right, ahead = (
        rotation[0],
        rotation[2],
    )  # camera axes in world (rows of world-to-camera)
    yaw0 = float(np.degrees(np.arctan2(ahead[0], ahead[2])))

    def pose(dx, dz, dyaw):
        view = geo.crop_camera(yaw0 + dyaw)[0]
        position = centre + step * (dx * right + dz * ahead)
        view[:3, 3] = -view[:3, :3] @ position
        return view

    def error(offset):
        memory = reproject(keyframes, pose(*offset), small, w, h)
        if memory.protected.mean() < 0.05:
            return np.inf
        diff = np.abs(frame - memory.rgb.astype(np.float32)).mean(-1) / 255
        return float(diff[memory.protected].mean())

    best, cost = (0.0, 0.0, 0.0), error((0.0, 0.0, 0.0))
    planned = cost
    spans = (lateral, forward, turn)
    for level in (1.0, 0.4, 0.15):
        centre_offset = best
        for dx in (-1, 0, 1):
            for dz in (-1, 0, 1):
                for dyaw in (-1, 0, 1):
                    offset = tuple(
                        float(np.clip(c + s * level * d, -s, s))
                        for c, s, d in zip(centre_offset, spans, (dx, dz, dyaw))
                    )
                    value = error(offset)
                    if value < cost:
                        best, cost = offset, value
    report = {
        "lateral_steps": round(best[0], 3),
        "forward_steps": round(best[1], 3),
        "turn_degrees": round(best[2], 2),
        "error_planned": None if not np.isfinite(planned) else round(planned, 4),
        "error_refined": None if not np.isfinite(cost) else round(cost, 4),
    }
    return pose(*best), report


def rebase(atlas: dict, old: Path, new: Path) -> dict:
    """An atlas read from folder `old` with its keyframe files addressed from folder `new` (a later version lives
    in another job's folder; the earlier keyframes' files stay where they were written). Only the files a job laid
    out are paths: a superseded record keeps the content names Library stores (the live trial's Fill more after Keep
    this still stopped here)."""

    def moved(value):
        return _relative(Path(old) / value, Path(new)) if isinstance(value, str) else value

    keyframes = [
        {**record, "image": moved(record["image"]), "data": moved(record["data"])}
        for record in atlas["keyframes"]
    ]
    return {**atlas, "keyframes": keyframes}


def probe_camera(position, yaw, hfov=75.0, width=1024, height=576):
    """An eye-level camera at `position` looking along `yaw` (the panorama crops' convention)."""
    view, k = geo.crop_camera(yaw, 0.0, hfov, width, height)
    view[:3, 3] = -view[:3, :3] @ np.asarray(position, dtype=float)
    return view, k
