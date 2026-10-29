# Vendored from the maintained engine implementation on branch worldbuilder (26 October 2026) for the worldbuilder engine worker
# (ADR 0018 §4); only the imports changed. The research module stays the reference; keep their numerics equal.
"""Worldbuilder explore (R4): a place's free-space map, built from what has been observed, and where to look next.

Everything is derived from the observations themselves, so the same code serves a tiny shack and a dune ridge:
- a voxel grid sized to the observed extent, carved along every observed ray (free up to the surface, occupied at
  it; sky rays are free to the grid's edge and never a wall; unknown is everything never crossed);
- clearance and visibility by ray casting through that grid (no distance transform needed);
- candidate probes in free space near the camera's height, their translation scaled to the local clearance, scored
  by how much unknown they would reveal while keeping a known share of the view in band, and how far they are
  from the nearest stored keyframe.

Frame: the splat's (OpenCV: x right, y down, z forward at yaw 0). Units: the panorama's (MoGe-2 metric).
Pure numpy plus worldbuilder_geometry; no GPU, no runtime.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
from . import geometry as geo
from .erp import directions

UNKNOWN, FREE, OCCUPIED = 0, 1, 2


# ---------------------------------------------------------------- observations


def pano_rays(distance, mask, stride=4, centre=(0.0, 0.0, 0.0)):
    """Rays of an equirectangular depth map seen from `centre`: (origins, unit directions, distances, sky).
    `mask` false is sky (or no geometry): such rays are free all the way and end nowhere."""
    distance = np.asarray(distance, dtype=float)
    h, w = distance.shape
    rays = directions(w, h) @ geo.F
    pick = (slice(stride // 2, None, stride), slice(stride // 2, None, stride))
    dirs = rays[pick].reshape(-1, 3)
    dist = distance[pick].reshape(-1)
    sky = (
        ~np.asarray(mask, dtype=bool)[pick].reshape(-1)
        | ~np.isfinite(dist)
        | (dist <= 0)
    )
    dist = np.where(sky, np.inf, dist)
    origins = np.broadcast_to(np.asarray(centre, dtype=float), dirs.shape)
    return origins, geo.unit(dirs), dist, sky


def view_rays(viewmat, k, depth, mask=None, stride=4):
    """Rays of a perspective keyframe (z-depth map) as for `pano_rays`; `mask` false pixels are sky."""
    depth = np.asarray(depth, dtype=float)
    h, w = depth.shape
    local = geo.camera_rays(np.asarray(k), w, h)
    c2w = np.linalg.inv(np.asarray(viewmat, dtype=float))
    pick = (slice(stride // 2, None, stride), slice(stride // 2, None, stride))
    local = local[pick].reshape(-1, 3)
    z = depth[pick].reshape(-1)
    world = local @ c2w[:3, :3].T
    norm = np.linalg.norm(world, axis=1)
    dirs = world / norm[:, None]
    dist = z * norm  # z-depth along the ray's own length
    sky = ~np.isfinite(dist) | (dist <= 0)
    if mask is not None:
        sky |= ~np.asarray(mask, dtype=bool)[pick].reshape(-1)
    dist = np.where(sky, np.inf, dist)
    origins = np.broadcast_to(c2w[:3, 3], dirs.shape)
    return origins, dirs, dist, sky


# ---------------------------------------------------------------- the free-space map


@dataclass
class FreeSpace:
    """A voxel grid of UNKNOWN / FREE / OCCUPIED over the observed extent."""

    lower: np.ndarray
    voxel: float
    state: np.ndarray = field(repr=False)

    @classmethod
    def around(
        cls,
        points,
        pad=0.15,
        cells=160,
        min_voxel=None,
        centre=None,
        near=0.75,
        max_voxel=None,
        centres=None,
        voxel=None,
        max_cells=320,
    ):
        """A grid covering `points` (the observed surfaces) with `pad` of the extent on each side; its longest
        side gets `cells` voxels. With `centre`, the grid covers only the near field: points within 2.5 x the
        `near` quantile of their distance from it (a street's far facades and a dune's horizon would otherwise make
        voxels a metre wide; what lies beyond is simply far). With `max_voxel`, voxels never grow past it and the
        grid shrinks around `centre` (or its middle) to `cells` voxels instead: on open ground the nearest surface,
        the ground below the camera, sets the voxel, not the horizon.

        With `centres` (every stored keyframe's), the near field is the union of theirs, so the map grows as the
        place is explored. A fixed `voxel` (the first round's) keeps one resolution across rounds; the grid then
        spans the observed extent, at most `max_cells` voxels a side around the centres' middle."""
        points = np.asarray(points, dtype=float)
        if centres is None and centre is not None:
            centres = [centre]
        middle = None
        if centres is not None:
            centres = np.atleast_2d(np.asarray(centres, dtype=float))
            middle = centres.mean(0)
            keep = np.zeros(len(points), dtype=bool)
            for c in centres:
                radius = np.linalg.norm(points - c, axis=1)
                keep |= radius <= 2.5 * np.quantile(radius, near)
            points = points[keep]
        lo, hi = points.min(0), points.max(0)
        extent = np.maximum(hi - lo, 1e-6)
        lo, hi = lo - pad * extent.max(), hi + pad * extent.max()
        if middle is None:
            middle = (lo + hi) / 2
        if voxel is not None:
            voxel = float(voxel)
            half = max_cells * voxel / 2
            lo, hi = np.maximum(lo, middle - half), np.minimum(hi, middle + half)
        else:
            voxel = float((hi - lo).max() / cells)
            if max_voxel and voxel > max_voxel:
                voxel = float(max_voxel)
                half = cells * voxel / 2
                lo, hi = np.maximum(lo, middle - half), np.minimum(hi, middle + half)
        if min_voxel:
            voxel = max(voxel, min_voxel)
        shape = np.maximum(np.ceil((hi - lo) / voxel).astype(int), 1)
        return cls(lo, voxel, np.zeros(tuple(shape), dtype=np.int8))

    @property
    def upper(self):
        return self.lower + self.voxel * np.array(self.state.shape)

    def index(self, points):
        return np.floor((np.asarray(points) - self.lower) / self.voxel).astype(int)

    def inside(self, idx):
        return np.all((idx >= 0) & (idx < np.array(self.state.shape)), axis=-1)

    def carve(self, origins, dirs, dist, sky, chunk=20000):
        """Mark voxels free along each ray up to its surface (a voxel short of it), and the surface occupied.
        Sky rays run free to the grid's edge. Occupied wins over free where rays disagree, so a thin wall seen
        from one side is not erased by a grazing ray from another."""
        origins, dirs = np.asarray(origins, float), np.asarray(dirs, float)
        dist, sky = np.asarray(dist, float), np.asarray(sky, bool)
        span = float(np.linalg.norm(self.upper - self.lower))
        step = self.voxel * 0.5
        occupied = []
        for start in range(0, len(dirs), chunk):
            o, d = origins[start : start + chunk], dirs[start : start + chunk]
            reach = np.where(
                sky[start : start + chunk],
                span,
                dist[start : start + chunk] - self.voxel,
            )
            n = int(np.ceil(max(float(reach.max()), 0) / step)) + 1
            t = np.arange(n) * step
            pts = o[:, None, :] + d[:, None, :] * t[None, :, None]
            valid = t[None, :] < reach[:, None]
            idx = self.index(pts[valid])
            idx = idx[self.inside(idx)]
            self.state[idx[:, 0], idx[:, 1], idx[:, 2]] = np.maximum(
                self.state[idx[:, 0], idx[:, 1], idx[:, 2]], FREE
            )
            hit = ~sky[start : start + chunk]
            if hit.any():
                ends = o[hit] + d[hit] * dist[start : start + chunk][hit][:, None]
                occupied.append(self.index(ends))
        for idx in occupied:
            idx = idx[self.inside(idx)]
            self.state[idx[:, 0], idx[:, 1], idx[:, 2]] = OCCUPIED
        return self

    def cast(self, origin, dirs, max_dist=None):
        """March rays from `origin`: (distance to the first non-free voxel, what it was: UNKNOWN, OCCUPIED, or
        FREE when the ray left the grid through free space)."""
        dirs = geo.unit(np.atleast_2d(np.asarray(dirs, float)))
        span = (
            float(np.linalg.norm(self.upper - self.lower))
            if max_dist is None
            else max_dist
        )
        step = self.voxel * 0.5
        t = np.arange(int(np.ceil(span / step)) + 1) * step
        pts = (
            np.asarray(origin, float)[None, None, :]
            + dirs[:, None, :] * t[None, :, None]
        )
        idx = self.index(pts)
        inside = self.inside(idx)
        state = np.full(inside.shape, -1, dtype=np.int8)
        ok = inside
        state[ok] = self.state[idx[ok][:, 0], idx[ok][:, 1], idx[ok][:, 2]]
        blocked = (state == UNKNOWN) | (state == OCCUPIED)
        first = np.where(blocked.any(1), blocked.argmax(1), len(t) - 1)
        what = np.where(blocked.any(1), state[np.arange(len(dirs)), first], FREE)
        return t[first], what

    def frontier(self):
        """Free voxels with an unknown 6-neighbour (indices)."""
        free = self.state == FREE
        unknown = self.state == UNKNOWN
        near = np.zeros_like(free)
        for axis in range(3):
            for shift in (1, -1):
                near |= np.roll(unknown, shift, axis=axis)
        return np.argwhere(free & near)


def observed_space(keyframes, cells=160, stride=4, voxel=None):
    """The free-space map of stored keyframes (worldbuilder_memory.Keyframe): every keyframe's rays carved into
    one grid over the union of their near fields. Without `voxel` (the first round), voxels are at most a third
    of the nearest surfaces' distance (open ground would otherwise make metre voxels); later rounds pass the
    first round's voxel so the map keeps one resolution while it grows.

    Sky rays (not `valid`) run free. Rays through geometry a keyframe does not hold as solid (glass or a mirror
    without a fitted plane, an appearance-only still) carve nothing: a reflection says nothing about the space
    behind the window. Depth-edge rays still carve: dropping them left holes along every silhouette (probe views
    60% unknown on 25 Oct)."""
    rays = []
    pick = (slice(stride // 2, None, stride), slice(stride // 2, None, stride))
    for key in keyframes:
        if key.kind == "erp":
            o, d, dist, sky = pano_rays(key.depth, key.valid, stride, key.centre)
            d = d @ key.rotation  # camera to world (rows)
        else:
            o, d, dist, sky = view_rays(
                key.viewmat, key.k, key.depth, key.valid, stride
            )
        keep = ~(key.valid & ~key.solid)[pick].reshape(-1)
        rays.append((o[keep], d[keep], dist[keep], sky[keep]))
    o, d, dist, sky = (np.concatenate(part) for part in zip(*rays))
    points = o[~sky] + d[~sky] * dist[~sky, None]
    return FreeSpace.around(
        points,
        cells=cells,
        centres=[key.centre for key in keyframes],
        max_voxel=None
        if voxel is not None
        else float(np.quantile(dist[~sky], 0.02)) / 3,
        voxel=voxel,
    ).carve(o, d, dist, sky)


def free_core(space: FreeSpace):
    """Free voxels whose six neighbours are free too: space observed to be empty, well inside, not at a surface."""
    free = space.state == FREE
    core = free.copy()
    for axis in range(3):
        for shift in (1, -1):
            core &= np.roll(free, shift, axis=axis)
    return core


def sphere_directions(n=64, max_elevation=None):
    """Roughly even unit directions (a Fibonacci sphere); with `max_elevation` (degrees), only those within that
    angle of the horizontal plane (y is down)."""
    i = np.arange(n) + 0.5
    phi = np.arccos(1 - 2 * i / n)
    theta = math.pi * (1 + 5**0.5) * i
    dirs = np.stack(
        [np.cos(theta) * np.sin(phi), np.cos(phi), np.sin(theta) * np.sin(phi)], -1
    )
    if max_elevation is not None:
        dirs = dirs[np.abs(dirs[:, 1]) <= math.sin(math.radians(max_elevation))]
    return dirs


def clearance(space: FreeSpace, point, n=64, horizontal=False, quantile=None):
    """Distance from `point` to the nearest non-free voxel (occupied or unknown) over `n` directions; with
    `horizontal`, only directions within 25 degrees of level (the room to move in, not the floor below). With
    `quantile`, that quantile of the distances instead of the minimum: the typical room around a point, which a
    camera standing beside a post or a cabinet would otherwise shrink to the post."""
    dirs = sphere_directions(4 * n, 25) if horizontal else sphere_directions(n)
    dist, _ = space.cast(point, dirs)
    return float(dist.min() if quantile is None else np.quantile(dist, quantile))


def ground_below(space: FreeSpace, point, reach, spread=0.0):
    """The height (y, down) of the ground under `point`: the first occupied voxel straight down within `reach`,
    looking from the point and from four points `spread` around it, and the median of those (a table under the
    point is not the floor). None when nothing below was observed."""
    point = np.asarray(point, dtype=float)
    found = []
    for dx, dz in ((0, 0), (1, 0), (-1, 0), (0, 1), (0, -1)) if spread else ((0, 0),):
        top = point + spread * np.array([dx, 0.0, dz])
        idx = space.index(top[None])
        if (dx or dz) and (
            not space.inside(idx)[0] or space.state[tuple(idx[0])] != FREE
        ):
            continue
        dist, what = space.cast(top, np.array([[0.0, 1.0, 0.0]]), max_dist=reach)
        if what[0] == OCCUPIED:
            found.append(float(top[1] + dist[0]))
    return float(np.median(found)) if found else None


def _on_ground(space: FreeSpace, start, position, eye, parts=4):
    """Walk from `start` (`eye` above its ground) to `position`'s x and z in `parts` sub-steps, keeping `eye`
    above the observed ground. Each sub-step's ground may rise or fall by at most a fifth of the eye height or
    three quarters of the sub-step's length (stairs and slopes, not a table's edge). Returns the end at eye height,
    or None where the ground is unobserved or breaks."""
    start, position = np.asarray(start, float), np.asarray(position, float)
    sub = float(np.hypot(*(position - start)[[0, 2]])) / parts
    rise = max(0.2 * eye, 0.75 * sub)
    height = start[1] + eye  # the ground, y down
    for i in range(1, parts + 1):
        here = start + (position - start) * i / parts
        ground = None
        for lift in (rise, 0.0):
            top = np.array([here[0], height - eye - lift, here[2]])
            idx = space.index(top[None])
            if space.inside(idx)[0] and space.state[tuple(idx[0])] == FREE:
                ground = ground_below(space, top, eye + 2 * rise)
                break
        if ground is None or abs(ground - height) > rise:
            return None
        height = ground
    return np.array([position[0], height - eye, position[2]])


def path_is_free(space: FreeSpace, start, end):
    """The straight move from `start` to `end` crosses only free voxels."""
    start, end = np.asarray(start, float), np.asarray(end, float)
    length = float(np.linalg.norm(end - start))
    if length < 1e-9:
        return True
    dist, _ = space.cast(start, (end - start)[None], max_dist=length)
    return bool(dist[0] >= length - space.voxel)


# ---------------------------------------------------------------- probes


@dataclass
class Probe:
    position: np.ndarray
    yaw: float
    known: float
    unknown: float
    clearance: float
    step: float
    score: float
    parent: str

    def viewmat(self):
        view = geo.crop_camera(self.yaw)[0]
        view[:3, 3] = -view[:3, :3] @ self.position
        return view

    def record(self):
        return {
            "position": [round(float(v), 4) for v in self.position],
            "yaw": round(float(self.yaw), 2),
            "known": round(self.known, 3),
            "unknown": round(self.unknown, 3),
            "clearance": round(self.clearance, 4),
            "step": round(self.step, 4),
            "score": round(self.score, 4),
            "parent": self.parent,
        }


def view_fractions(space: FreeSpace, position, yaw, hfov=75.0, grid=(16, 9)):
    """Shares of a view's rays that end on known surface (OCCUPIED), on unknown space, or leave through free
    space (sky, open ground beyond the grid)."""
    view = geo.crop_camera(yaw)[0]
    k = geo.intrinsics(hfov, *grid)
    local = geo.camera_rays(k, *grid).reshape(-1, 3)
    world = geo.unit(local @ view[:3, :3])  # crop_camera's rotation is world-to-camera
    _, what = space.cast(position, world)
    return (
        float(np.mean(what == OCCUPIED)),
        float(np.mean(what == UNKNOWN)),
        float(np.mean(what == FREE)),
    )


def plan_probes(
    space: FreeSpace,
    keyframes: list[dict],
    count=3,
    min_known=0.4,
    step_range=(0.25, 0.6),
    yaws=tuple(range(0, 360, 45)),
    tries=400,
    seed=0,
    follow_ground=True,
    targets=(),
    explored=None,
    toward=None,
):
    """The next `count` probes: positions sampled around each keyframe's centre at the camera's height, at a
    distance of `step_range` x the horizontal clearance there (so a shack gets centimetres and a dune metres), kept
    where the probe itself has room; every yaw scored by the unknown share it would reveal, as long as at least
    `min_known` of the view is known (the anchor for adhering to the place). Diverse picks: no two within half a
    step looking the same way.

    With `follow_ground` and observed ground below the keyframe, a probe keeps the keyframe's height above the
    ground (up or down a stair or a slope, at most one step's rise per step), and a probe over ground never
    observed is dropped: the camera walks, it does not float off a ridge.

    Later rounds (R5, the roomba loop) add three things. `targets` are frontier clusters (`frontier_targets`):
    probes stand short of each, facing it, walked to from the nearest keyframe. `explored` (the keyframes'
    centroid) gives a reach bonus, so exploration spreads instead of circling the start. `toward` (a heading in
    degrees) steers: positions and views that face it weigh more ("explore toward the door"). A probe never
    repeats a stored keyframe's view (within half a step, facing within 45 degrees)."""
    rng = np.random.default_rng(seed)
    candidates = []
    stored = [_key_pose(key) for key in keyframes]

    def consider(position, yaws_here, centre, room, step, parent):
        if not space.inside(space.index(position[None]))[0]:
            return
        idx = space.index(position[None])[0]
        if space.state[tuple(idx)] != FREE or not path_is_free(space, centre, position):
            return
        here = clearance(space, position, horizontal=True)
        if here < 0.1 * room:
            return
        for yaw in yaws_here:
            if any(
                np.linalg.norm(position - p) < 0.5 * step
                and abs((yaw - y + 180) % 360 - 180) < 45
                for p, y in stored
            ):
                continue
            surface, unknown, open_ = view_fractions(space, position, yaw)
            known = surface + open_  # observed sky and open ground are known too
            if known < min_known or unknown <= 0:
                continue
            score = (
                unknown * min(1.0, step / (0.5 * room)) * min(1.0, here / (0.2 * room))
            )
            if explored is not None:
                reach = np.linalg.norm((position - np.asarray(explored, float))[[0, 2]])
                score *= 1.0 + 0.5 * min(reach / room, 2.0)
            if toward is not None:
                score *= steering_weight(position, yaw, toward, explored)
            candidates.append(
                Probe(position, float(yaw), known, unknown, here, step, score, parent)
            )

    for key in keyframes:
        centre = np.asarray(key["position"], float)
        room = clearance(space, centre, horizontal=True, quantile=0.35)
        eye = _eye_height(space, centre, room) if follow_ground else None
        for _ in range(max(1, tries // max(len(keyframes), 1))):
            angle = rng.uniform(0, 2 * math.pi)
            step = rng.uniform(*step_range) * room
            position = centre + step * np.array([math.sin(angle), 0.0, math.cos(angle)])
            if eye is not None:
                position = _on_ground(space, centre, position, eye)
                if position is None:
                    continue
            consider(position, yaws, centre, room, step, key["id"])
    for target in targets:
        target = np.asarray(target, float)
        key = min(
            keyframes,
            key=lambda k: np.linalg.norm((np.asarray(k["position"]) - target)[[0, 2]]),
        )
        centre = np.asarray(key["position"], float)
        room = clearance(space, centre, horizontal=True, quantile=0.35)
        eye = _eye_height(space, centre, room) if follow_ground else None
        flat = (target - centre) * np.array([1.0, 0.0, 1.0])
        if np.linalg.norm(flat) < 1e-9:
            continue
        heading = math.degrees(math.atan2(flat[0], flat[2]))
        direction = flat / np.linalg.norm(flat)
        for _ in range(max(1, tries // max(4 * len(targets), 1))):
            short = rng.uniform(*step_range) * room
            position = np.array([target[0], centre[1], target[2]]) - short * direction
            step = float(np.linalg.norm((position - centre)[[0, 2]]))
            if step < 0.1 * room:
                continue
            if eye is not None:
                position = _on_ground(space, centre, position, eye)
                if position is None:
                    continue
            yaws_here = [round((heading + d) / 15) * 15 % 360 for d in (-30, 0, 30)]
            consider(position, yaws_here, centre, room, step, key["id"])
    candidates.sort(key=lambda p: -p.score)
    chosen = []
    for probe in candidates:
        if all(
            np.linalg.norm(probe.position - c.position) >= probe.step
            and abs((probe.yaw - c.yaw + 180) % 360 - 180) >= 60
            for c in chosen
        ):
            chosen.append(probe)
        if len(chosen) == count:
            break
    return chosen


def _key_pose(key: dict):
    """A keyframe record's position and heading (an equirectangular keyframe looks every way: no heading)."""
    position = np.asarray(key["position"], float)
    if key.get("kind") != "pinhole" or "viewmat" not in key:
        return position, float("nan")
    ahead = np.asarray(key["viewmat"], float)[2, :3]
    return position, math.degrees(math.atan2(ahead[0], ahead[2])) % 360


def _eye_height(space, centre, room):
    ground = ground_below(space, centre, None, spread=0.25 * room)
    return None if ground is None else ground - centre[1]


def steering_weight(position, yaw, toward, origin=None):
    """How much a probe goes the way the user asked: its view facing `toward` (a heading in degrees), squared,
    times how far round its position lies in that direction from `origin` (0.5 to 1)."""
    facing = max(0.0, math.cos(math.radians(yaw - toward))) ** 2
    if origin is None:
        return facing
    flat = (np.asarray(position, float) - np.asarray(origin, float))[[0, 2]]
    if np.linalg.norm(flat) < 1e-9:
        return facing
    bearing = math.degrees(math.atan2(flat[0], flat[1]))
    return facing * (0.75 + 0.25 * math.cos(math.radians(bearing - toward)))


# ---------------------------------------------------------------- the roomba map (R5)


def eye_plane(space: FreeSpace, height, band):
    """The map at the camera's height, seen from above: per (x, z) column FREE where a free voxel lies within
    `band` of `height` (y), OCCUPIED where only occupied ones do, UNKNOWN otherwise (indexed [x, z])."""
    j0 = int(
        np.clip(
            (height - band - space.lower[1]) / space.voxel, 0, space.state.shape[1] - 1
        )
    )
    j1 = int(
        np.clip(
            (height + band - space.lower[1]) / space.voxel, 0, space.state.shape[1] - 1
        )
    )
    slab = space.state[:, j0 : j1 + 1, :]
    free, occupied = (slab == FREE).any(1), (slab == OCCUPIED).any(1)
    return np.where(free, FREE, np.where(occupied, OCCUPIED, UNKNOWN)).astype(np.int8)


def frontier_cells(plane):
    """Free cells of the map next to unknown ones (4-neighbours): where the robot has not looked yet."""
    padded = np.pad(plane, 1, constant_values=UNKNOWN)
    unknown_near = np.zeros(plane.shape, bool)
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        unknown_near |= (
            padded[1 + dx : 1 + dx + plane.shape[0], 1 + dz : 1 + dz + plane.shape[1]]
            == UNKNOWN
        )
    return (plane == FREE) & unknown_near


def frontier_targets(space: FreeSpace, plane, height, min_cells=4):
    """Frontier clusters (8-connected) of at least `min_cells` cells, as world points at `height`, largest
    first, with their sizes in cells."""
    cells = frontier_cells(plane)
    seen = np.zeros(cells.shape, bool)
    clusters = []
    for i0, k0 in zip(*np.nonzero(cells)):
        if seen[i0, k0]:
            continue
        stack, members = [(i0, k0)], []
        seen[i0, k0] = True
        while stack:
            i, k = stack.pop()
            members.append((i, k))
            for di in (-1, 0, 1):
                for dk in (-1, 0, 1):
                    ii, kk = i + di, k + dk
                    if (
                        0 <= ii < cells.shape[0]
                        and 0 <= kk < cells.shape[1]
                        and cells[ii, kk]
                        and not seen[ii, kk]
                    ):
                        seen[ii, kk] = True
                        stack.append((ii, kk))
        if len(members) >= min_cells:
            i, k = np.mean(members, axis=0)
            point = np.array(
                [
                    space.lower[0] + (i + 0.5) * space.voxel,
                    height,
                    space.lower[2] + (k + 0.5) * space.voxel,
                ]
            )
            clusters.append((point, len(members)))
    clusters.sort(key=lambda c: -c[1])
    return clusters


def coverage(space: FreeSpace, plane, room):
    """How far the roomba has got, in units of the room: the explored floor (free cells at eye height), the
    frontier still open, the boundary of what is explored, and closure (1 - frontier / boundary: a shack closes,
    a dune never does)."""
    free = plane == FREE
    frontier = frontier_cells(plane)
    padded = np.pad(free, 1, constant_values=False)
    edge = np.zeros(free.shape, bool)
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        edge |= ~padded[
            1 + dx : 1 + dx + free.shape[0], 1 + dz : 1 + dz + free.shape[1]
        ]
    boundary = free & edge
    cell = space.voxel / room
    return {
        "area": round(float(free.sum()) * cell**2, 4),
        "frontier": round(float(frontier.sum()) * cell, 4),
        "boundary": round(float(boundary.sum()) * cell, 4),
        "closure": round(
            1.0 - float(frontier.sum()) / max(float(boundary.sum()), 1.0), 4
        ),
    }


# ---------------------------------------------------------------- the atlas


def new_atlas(place: str, centre_view: dict) -> dict:
    """A place's atlas at version 1: the panorama centre as the first keyframe set (its crops)."""
    return {
        "schema": 1,
        "place": place,
        "version": 1,
        "keyframes": [centre_view],
        "decisions": [],
    }


def add_keyframe(atlas: dict, keyframe: dict, decision: str) -> dict:
    """A new version with one more keyframe; earlier entries are never changed (immutable observations)."""
    if any(k["id"] == keyframe["id"] for k in atlas["keyframes"]):
        raise ValueError(f"keyframe {keyframe['id']} already in the atlas")
    return {
        **atlas,
        "version": atlas["version"] + 1,
        "keyframes": [*atlas["keyframes"], keyframe],
        "decisions": [
            *atlas["decisions"],
            {
                "version": atlas["version"] + 1,
                "keyframe": keyframe["id"],
                "decision": decision,
            },
        ],
    }


def save_atlas(atlas: dict, path: Path):
    path.write_text(json.dumps(atlas, indent=1), encoding="utf-8")


def load_atlas(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------- a picture of it


def top_down(space: FreeSpace, height: float, keyframes=(), probes=(), scale=4):
    """A plan view slice at `height` (y): unknown dark grey, free light, occupied black; keyframes blue, probes
    orange with their view direction. Returns an RGB array (x to the right, z upward)."""
    j = int(
        np.clip((height - space.lower[1]) / space.voxel, 0, space.state.shape[1] - 1)
    )
    plane = space.state[:, j, :].T[::-1]  # rows: z descending, cols: x
    colours = np.array([[70, 70, 70], [225, 225, 215], [15, 15, 15]], dtype=np.uint8)
    image = np.repeat(np.repeat(colours[plane], scale, 0), scale, 1)

    def pixel(p):
        i = (np.asarray(p) - space.lower) / space.voxel
        return int(i[0] * scale), int((space.state.shape[2] - i[2]) * scale)

    def dot(p, colour, r=3):
        x, y = pixel(p)
        image[max(0, y - r) : y + r, max(0, x - r) : x + r] = colour

    for key in keyframes:
        dot(key["position"], (40, 110, 230), 4)
    for probe in probes:
        dot(probe.position, (240, 140, 30), 4)
        for s in np.linspace(0, 3 * space.voxel * 4, 8):
            tip = probe.position + s * np.array(
                [
                    math.sin(math.radians(probe.yaw)),
                    0,
                    math.cos(math.radians(probe.yaw)),
                ]
            )
            dot(tip, (240, 140, 30), 1)
    return image
