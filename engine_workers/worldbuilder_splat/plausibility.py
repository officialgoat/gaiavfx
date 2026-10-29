# Vendored from the maintained engine implementation on branch worldbuilder (27 October 2026) for the worldbuilder
# engine worker (ADR 0018 §4); only the imports changed. The research module stays the reference; keep their
# numerics equal.
"""Worldbuilder plausibility (R6): what the judge sees, and the model-free hints it may confirm or dismiss.

The user's R5 ranking found the stills realistic one by one while the place as a whole was not: a room that reads
as rectangular on a fan-shaped map, a rack facing two walls, stairs rising on both sides, an island in the middle
of a street. A judge (the local Qwen 3.8, `worldbuilder_judge_plausibility.py`) sees:
- the map board: the eye-level roomba map cropped to what is explored, with a lettered and numbered grid, the
  cameras numbered with their view directions, landmark footprints, the hints as numbered markers, a compass and a
  scale in metres;
- the view tiles: the panorama's eight crops and every geometry still, each labelled with its camera and heading.

The hints are candidates, never verdicts:
- squareness: how much of the wall boundary follows two perpendicular directions, and where it bends;
- islands: unseen or occupied specks enclosed by explored floor;
- pockets: unseen notches and wedges cut into the explored floor (a morphological closing of it);
- landmark footprints: SAM3 masks of the place's landmarks back-projected through each keyframe's depth, so one
  object placed twice shows as two footprints.

Map arrays are indexed [x, z] as in `worldbuilder_explore.eye_plane`; the board draws x to the right and z upward
(heading 0 up, 90 right, as the user reads clock positions). Pure numpy and PIL.
"""

from __future__ import annotations

import math
import string
from dataclasses import dataclass
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFont
from .explore import FREE, OCCUPIED, UNKNOWN
from .memory import _components, grow

FONT_FILE = Path("C:/Windows/Fonts/arial.ttf")
BOLD_FILE = Path("C:/Windows/Fonts/arialbd.ttf")
LANDMARK_COLOURS = [
    (214, 39, 40),
    (44, 160, 44),
    (148, 103, 189),
    (23, 190, 207),
    (227, 119, 194),
    (188, 189, 34),
    (140, 86, 75),
    (255, 127, 14),
]


def font(size: int, bold=False):
    path = BOLD_FILE if bold else FONT_FILE
    if path.exists():
        return ImageFont.truetype(str(path), size)
    return ImageFont.load_default(size)


# ---------------------------------------------------------------- the grid


@dataclass
class Grid:
    """A coarse lettered and numbered grid over the explored part of a map: columns A, B, ... left to right (x),
    rows 1, 2, ... top to bottom (z descending). `i0`, `k0` is the map cell at the grid's x and z minimum."""

    i0: int
    k0: int
    size: int  # map cells per grid cell
    cols: int
    rows: int
    voxel: float
    lower: tuple  # the map's world origin (x, y, z)

    def name(self, i, k) -> str:
        col = int(np.clip((i - self.i0) // self.size, 0, self.cols - 1))
        row = int(np.clip(self.rows - 1 - (k - self.k0) // self.size, 0, self.rows - 1))
        return f"{string.ascii_uppercase[col]}{row + 1}"

    def name_of(self, point) -> str:
        i, k = self.cell(point)
        return self.name(i, k)

    def cell(self, point):
        """The map cell of a world point (x, _, z)."""
        return (
            math.floor((point[0] - self.lower[0]) / self.voxel),
            math.floor((point[2] - self.lower[2]) / self.voxel),
        )

    def centre(self, name: str):
        """A grid cell's centre as a world (x, z)."""
        col = string.ascii_uppercase.index(name[0].upper())
        row = int(name[1:]) - 1
        i = self.i0 + (col + 0.5) * self.size
        k = self.k0 + (self.rows - 1 - row + 0.5) * self.size
        return (
            self.lower[0] + i * self.voxel,
            self.lower[2] + k * self.voxel,
        )

    def names(self, mask) -> list[str]:
        """The grid cells a map mask touches, in reading order."""
        cells = {self.name(i, k) for i, k in zip(*np.nonzero(mask))}
        return sorted(cells, key=lambda n: (int(n[1:]), n[0]))

    def mask(self, names, shape) -> np.ndarray:
        """The map cells under grid cells `names`."""
        out = np.zeros(shape, bool)
        for name in names:
            try:
                col = string.ascii_uppercase.index(name[0].upper())
                row = int(name[1:]) - 1
            except (ValueError, IndexError):
                continue
            if not (0 <= col < self.cols and 0 <= row < self.rows):
                continue
            i = self.i0 + col * self.size
            k = self.k0 + (self.rows - 1 - row) * self.size
            out[
                max(i, 0) : max(i + self.size, 0), max(k, 0) : max(k + self.size, 0)
            ] = True
        return out

    def record(self) -> dict:
        return {
            "cols": self.cols,
            "rows": self.rows,
            "cell_m": round(self.size * self.voxel, 3),
            "i0": self.i0,
            "k0": self.k0,
            "size": self.size,
            "voxel": self.voxel,
            "lower": [float(v) for v in self.lower],
        }

    @classmethod
    def from_record(cls, record: dict) -> Grid:
        return cls(
            record["i0"],
            record["k0"],
            record["size"],
            record["cols"],
            record["rows"],
            record["voxel"],
            tuple(record["lower"]),
        )


def grid_for(plane, voxel, lower, target=14, margin=1) -> Grid:
    """A grid of about `target` cells along the explored area's longer side, with `margin` grid cells around it."""
    explored = plane == FREE
    if not explored.any():
        explored = plane != UNKNOWN
    xs, zs = np.nonzero(explored)
    span = max(xs.max() - xs.min() + 1, zs.max() - zs.min() + 1)
    size = max(1, math.ceil(span / target))
    i0 = int(xs.min()) - margin * size
    k0 = int(zs.min()) - margin * size
    cols = math.ceil((xs.max() + 1 - i0) / size) + margin
    rows = math.ceil((zs.max() + 1 - k0) / size) + margin
    return Grid(
        i0, k0, size, min(cols, 26), rows, float(voxel), tuple(float(v) for v in lower)
    )


# ---------------------------------------------------------------- hints


def _neighbour_any(mask):
    """Cells with at least one 4-neighbour in `mask`."""
    padded = np.pad(mask, 1, constant_values=False)
    near = np.zeros(mask.shape, bool)
    for dx, dz in ((1, 0), (-1, 0), (0, 1), (0, -1)):
        near |= padded[1 + dx : 1 + dx + mask.shape[0], 1 + dz : 1 + dz + mask.shape[1]]
    return near


def wall_cells(plane):
    """Occupied cells next to explored floor: the walls and objects the explored floor runs into."""
    return (plane == OCCUPIED) & _neighbour_any(plane == FREE)


def _box_blur(values, radius):
    """A (2 radius + 1) box mean, edges extended."""
    out = values.astype(float)
    for axis in (0, 1):
        pad = [(radius + 1, radius) if a == axis else (0, 0) for a in (0, 1)]
        total = np.cumsum(np.pad(out, pad, mode="edge"), axis=axis)
        n = out.shape[axis]
        upper = np.take(total, np.arange(2 * radius + 1, 2 * radius + 1 + n), axis=axis)
        lower = np.take(total, np.arange(n), axis=axis)
        out = (upper - lower) / (2 * radius + 1)
    return out


def _turn(a, b):
    """The angle between two line headings (degrees, modulo 180)."""
    return np.abs((np.asarray(a) - np.asarray(b) + 90.0) % 180.0 - 90.0)


def wall_runs(plane, voxel, min_length, tolerance=12.0, smooth=2):
    """Straight runs of wall: wall cells grown into regions whose line heading (modulo 180; 0 runs along +z, up
    the board) stays within `tolerance` of the region's mean, kept when at least `min_length` long. The heading
    comes from the gradient of the explored floor, blurred so a grid staircase reads as one straight wall.
    Returns dicts with heading, length_m, the map mask and the midpoint cell, longest first."""
    walls = wall_cells(plane)
    free = (plane == FREE).astype(float)
    gx, gz = np.gradient(_box_blur(_box_blur(free, smooth), smooth))
    heading = np.degrees(np.arctan2(-gz, gx)) % 180.0
    label = np.zeros(plane.shape, bool)
    runs = []
    for i0, k0 in zip(*np.nonzero(walls)):
        if label[i0, k0]:
            continue
        label[i0, k0] = True
        doubled = np.radians(2 * heading[i0, k0])
        sums = [math.cos(doubled), math.sin(doubled)]
        stack, members = [(i0, k0)], [(i0, k0)]
        while stack:
            i, k = stack.pop()
            mean = math.degrees(math.atan2(sums[1], sums[0])) / 2 % 180.0
            for di in (-1, 0, 1):
                for dk in (-1, 0, 1):
                    ii, kk = i + di, k + dk
                    if not (0 <= ii < plane.shape[0] and 0 <= kk < plane.shape[1]):
                        continue
                    if label[ii, kk] or not walls[ii, kk]:
                        continue
                    if _turn(heading[ii, kk], mean) > tolerance:
                        continue
                    label[ii, kk] = True
                    doubled = np.radians(2 * heading[ii, kk])
                    sums[0] += math.cos(doubled)
                    sums[1] += math.sin(doubled)
                    stack.append((ii, kk))
                    members.append((ii, kk))
        mean = math.degrees(math.atan2(sums[1], sums[0])) / 2 % 180.0
        cells = np.array(members, float)
        along = np.array([math.sin(math.radians(mean)), math.cos(math.radians(mean))])
        reach = cells @ along
        length = (reach.max() - reach.min() + 1) * voxel
        if length < min_length:
            continue
        region = np.zeros(plane.shape, bool)
        region[tuple(np.array(members).T)] = True
        middle = members[int(np.argmin(np.abs(reach - np.median(reach))))]
        runs.append(
            {
                "heading": round(mean, 1),
                "length_m": round(float(length), 2),
                "region": region,
                "middle": middle,
            }
        )
    runs.sort(key=lambda r: -r["length_m"])
    return runs


def rectilinear(runs):
    """How well the wall runs follow two perpendicular directions: the length-weighted share within 12 degrees of
    the best pair of axes, and the axes (degrees)."""
    total = sum(r["length_m"] for r in runs)
    if not total:
        return None, None
    best = (0.0, 0.0)
    for run in runs:
        theta = run["heading"] % 90.0
        share = (
            sum(
                r["length_m"]
                for r in runs
                if min(_turn(r["heading"], theta), _turn(r["heading"], theta + 90))
                <= 12
            )
            / total
        )
        if share > best[0]:
            best = (share, theta)
    return round(best[0], 3), [round(best[1], 1), round(best[1] + 90.0, 1)]


def islands(plane, max_cells=None):
    """Unseen or occupied regions enclosed by explored floor (they never reach the unknown beyond it): a pillar,
    a table, or a speck no camera saw. Returns masks with at least 3 cells (and at most `max_cells`)."""
    nonfree = np.pad(plane != FREE, 1, constant_values=True)
    found = []
    for region in _components(nonfree):
        if (
            region[0].any()
            or region[-1].any()
            or region[:, 0].any()
            or region[:, -1].any()
        ):
            continue  # the unknown beyond the explored floor
        region = region[1:-1, 1:-1]
        cells = int(region.sum())
        if cells >= 3 and (max_cells is None or cells <= max_cells):
            found.append(region)
    return found


def pockets(plane, radius, min_cells=4, exclude=None):
    """Unseen notches and wedges cut into the explored floor: what a closing of the floor by `radius` cells adds
    that is not floor (and not in `exclude`, the islands). The wedge behind a pillar and an unexplored slice of a
    round room are pockets; a building block beside a street is too wide to close."""
    free = plane == FREE
    pad = radius + 1  # beyond the map is not floor: a closing must not run off its edge
    closed = ~grow(~grow(np.pad(free, pad), radius), radius)[pad:-pad, pad:-pad]
    notch = closed & ~free & (plane == UNKNOWN)
    if exclude is not None:
        notch &= ~exclude
    return [r for r in _components(notch) if r.sum() >= min_cells]


def _shape(region):
    xs, zs = np.nonzero(region)
    if len(xs) < 3:
        return 1.0
    spread = np.cov(np.stack([xs, zs]).astype(float)) + 1e-6 * np.eye(2)
    small, large = np.linalg.eigvalsh(spread)
    return float(math.sqrt(large / small))


def map_hints(plane, voxel, room, grid: Grid, cameras=(), limit=8, far_rooms=5.0):
    """What the judge is told about the map besides the picture: the long wall runs with their directions, how
    rectilinear they are, and numbered hints (islands and pockets, largest first, at most `limit`). Hints farther
    than `far_rooms` rooms from every camera are dropped: at the explored limit the rays fan out and the gaps
    between them are aliasing, not places."""
    room_cells = max(room / voxel, 1.0)
    runs = wall_runs(plane, voxel, min_length=0.4 * room)
    share, axes = rectilinear(runs)
    centres = (
        np.array([[c["position"][0], c["position"][2]] for c in cameras])
        if cameras
        else None
    )
    found = []
    enclosed = islands(plane, max_cells=round(1.5 * room_cells**2))
    for region in enclosed:
        occupied = float((plane[region] == OCCUPIED).mean())
        what = "something seen standing" if occupied > 0.5 else "never seen"
        found.append(
            (
                "island",
                region,
                f"{what}, enclosed by explored floor, elongation {_shape(region):.1f}",
            )
        )
    union = np.zeros(plane.shape, bool)
    for region in enclosed:
        union |= region
    for region in pockets(
        plane,
        max(2, round(0.3 * room_cells)),
        min_cells=max(4, round(0.02 * room_cells**2)),
        exclude=union,
    ):
        found.append(
            (
                "pocket",
                region,
                "never seen, a notch or wedge cut into the explored floor",
            )
        )
    found.sort(key=lambda item: -int(item[1].sum()))
    hints = []
    for kind, region, text in found:
        if len(hints) >= limit:
            break
        xs, zs = np.nonzero(region)
        centre = np.array(
            [
                grid.lower[0] + (xs.mean() + 0.5) * voxel,
                grid.lower[2] + (zs.mean() + 0.5) * voxel,
            ]
        )
        if (
            centres is not None
            and np.linalg.norm(centres - centre, axis=1).min() > far_rooms * room
        ):
            continue
        hints.append(
            {
                "id": len(hints) + 1,
                "kind": kind,
                "cells": grid.names(region),
                "area_m2": round(float(region.sum()) * voxel**2, 2),
                "centre": [round(float(c), 4) for c in centre],
                "text": text,
                "region": region,
            }
        )
    walls = []
    for n, run in enumerate(runs[:8], start=1):
        i, k = run["middle"]
        off = (
            None
            if axes is None
            else min(_turn(run["heading"], axes[0]), _turn(run["heading"], axes[1]))
            > 12
        )
        walls.append(
            {
                "label": f"W{n}",
                "heading": run["heading"],
                "length_m": run["length_m"],
                "cell": grid.name(i, k),
                "off_axes": bool(off),
                "middle": [
                    round(grid.lower[0] + (i + 0.5) * voxel, 4),
                    round(grid.lower[2] + (k + 0.5) * voxel, 4),
                ],
                "region": run["region"],
            }
        )
    longest = walls[:5]
    angles = []
    for a in range(len(longest)):
        for b in range(a + 1, len(longest)):
            turn = float(_turn(longest[a]["heading"], longest[b]["heading"]))
            off = min(turn, 90.0 - turn)  # from parallel or perpendicular
            angles.append(
                {
                    "walls": [longest[a]["label"], longest[b]["label"]],
                    "angle": round(turn, 1),
                    "off": round(off, 1),
                }
            )
    return {
        "rectilinear": share,
        "axes": axes,
        "walls": walls,
        "angles": angles,
        "hints": hints,
    }


# ---------------------------------------------------------------- landmark footprints


def footprint(key, mask, plane_shape, voxel, lower, stride=2):
    """Where a keyframe's masked pixels land on the map: the mask (any size; white = the landmark) resized to the
    keyframe's grid, its pixels back-projected through the stored depth (every height: the map is seen from
    above). Returns a map mask."""
    h, w = key.depth.shape
    mask = (
        np.asarray(
            Image.fromarray(np.asarray(mask, np.uint8) * 255).resize(
                (w, h), Image.NEAREST
            )
        )
        > 127
    )
    pick = (slice(stride // 2, None, stride), slice(stride // 2, None, stride))
    keep = (mask & key.valid & key.solid)[pick]
    out = np.zeros(plane_shape, bool)
    if not keep.any():
        return out
    local = key.local_rays()[pick][keep] * key.depth[pick][keep, None]
    world = local @ key.rotation + key.centre
    i = np.floor((world[:, 0] - lower[0]) / voxel).astype(int)
    k = np.floor((world[:, 2] - lower[2]) / voxel).astype(int)
    ok = (i >= 0) & (i < plane_shape[0]) & (k >= 0) & (k < plane_shape[1])
    out[i[ok], k[ok]] = True
    return out


def landmark_sightings(footprints, grid: Grid, room, voxel):
    """Per landmark, each camera's footprint (its grid cells and centre) and whether two cameras put it more than
    a third of the room apart: footprints is {(landmark, camera label): map mask}."""
    out = {}
    for (landmark, camera), region in footprints.items():
        if region.sum() < 2:
            continue
        xs, zs = np.nonzero(region)
        centre = (
            grid.lower[0] + (np.median(xs) + 0.5) * voxel,
            grid.lower[2] + (np.median(zs) + 0.5) * voxel,
        )
        out.setdefault(landmark, []).append(
            {
                "camera": camera,
                "cells": grid.names(region),
                "centre": [round(float(c), 4) for c in centre],
            }
        )
    apart = {}
    for landmark, seen in out.items():
        far = 0.0
        for a in seen:
            for b in seen:
                far = max(far, math.dist(a["centre"], b["centre"]))
        apart[landmark] = far > room / 3
    return out, apart


# ---------------------------------------------------------------- a place's map and cameras


def place_map(atlas_path, voxel=None, room=None, band_rooms=0.15):
    """The roomba map of an atlas as R5's explore round carves it (`step_explore_round`): the free-space grid of
    its geometry keyframes, the eye-level plane, its frontier, the room size and the eye height."""
    from . import explore as ex
    from . import memory as mem

    keyframes = mem.load_keyframes(atlas_path)
    space = ex.observed_space(keyframes, voxel=voxel)
    pano = next(k for k in keyframes if k.kind == "erp")
    room = room or ex.clearance(space, pano.centre, horizontal=True, quantile=0.35)
    height = float(np.mean([k.centre[1] for k in keyframes]))
    plane = ex.eye_plane(space, height, max(2 * space.voxel, band_rooms * room))
    return {
        "keyframes": keyframes,
        "space": space,
        "plane": plane,
        "frontier": ex.frontier_cells(plane),
        "room": float(room),
        "height": height,
    }


def cameras_of(atlas: dict) -> list[dict]:
    """The board's cameras: K0 the panorama, K1, K2, ... every geometry still, numbered in the order it first
    entered the atlas (trajectory frames and rejected stills are not cameras). A keyframe keeps its number when a
    retraction supersedes it; one retracted whole leaves its number unused."""
    from . import memory as mem

    numbers, n = {}, 1
    for record in atlas["keyframes"]:
        root = record["id"].split("~")[0]
        if root in numbers or record.get("role") in mem.NOT_GEOMETRY:
            continue
        if record.get("kind") == "erp":
            numbers[root] = 0
        else:
            numbers[root] = n
            n += 1
    out = []
    for record in mem.current(atlas):
        root = record["id"].split("~")[0]
        if record.get("role") in mem.NOT_GEOMETRY or root not in numbers:
            continue
        camera = {
            "label": f"K{numbers[root]}",
            "id": record["id"],
            "kind": "panorama" if record.get("kind") == "erp" else "view",
            "position": record["position"],
            "heading": None,
        }
        if record.get("kind") != "erp":
            ahead = np.asarray(record["viewmat"], float)[2, :3]
            camera["heading"] = round(
                math.degrees(math.atan2(ahead[0], ahead[2])) % 360, 1
            )
        out.append(camera)
    return sorted(out, key=lambda c: int(c["label"][1:]))


PANO_HEADINGS = tuple(range(0, 360, 45))


def view_tiles(keyframes, cameras, width=768, height=432):
    """The judge's views: the panorama's eight crops (75 degrees) and every geometry still, labelled. Returns
    [(label text, camera label, heading, RGB array)]."""
    from .erp import crop_view

    by_id = {k.id: k for k in keyframes}
    tiles = []
    for camera in cameras:
        key = by_id.get(camera["id"])
        if key is None:
            continue
        if camera["kind"] == "panorama":
            for yaw in PANO_HEADINGS:
                text = f"K0 · heading {yaw}°"
                tiles.append(
                    (
                        text,
                        "K0",
                        yaw,
                        label_tile(
                            crop_view(key.image, yaw, 75.0, 1024, 576),
                            text,
                            width,
                            height,
                        ),
                    )
                )
        else:
            text = f"{camera['label']} · heading {camera['heading']:.0f}°"
            tiles.append(
                (
                    text,
                    camera["label"],
                    camera["heading"],
                    label_tile(key.image, text, width, height),
                )
            )
    return tiles


# ---------------------------------------------------------------- the board


MAP_COLOURS = {
    UNKNOWN: (64, 66, 72),
    FREE: (228, 226, 216),
    OCCUPIED: (18, 18, 20),
}


def _heading_vector(yaw):
    return math.sin(math.radians(yaw)), math.cos(math.radians(yaw))


def board_image(
    plane,
    frontier,
    grid: Grid,
    cameras,
    hints=None,
    landmarks=None,
    footprints=None,
    side=1024,
    title="",
):
    """The judge's map board as an RGB array. `cameras` are dicts with label, position (world), kind and heading
    (None for the panorama); `hints` from `map_hints` (numbered hints and labelled wall runs); `footprints`
    {(landmark index, camera label): map mask} and `landmarks` the place's landmark list (for the legend)."""
    hints = hints or {"hints": [], "walls": []}
    crop_i = slice(max(grid.i0, 0), grid.i0 + grid.cols * grid.size)
    crop_k = slice(max(grid.k0, 0), grid.k0 + grid.rows * grid.size)
    shape = (grid.cols * grid.size, grid.rows * grid.size)
    di, dk = max(grid.i0, 0) - grid.i0, max(grid.k0, 0) - grid.k0

    def placed(array, fill):
        out = np.full(shape, fill, dtype=array.dtype)
        part = array[crop_i, crop_k]
        out[di : di + part.shape[0], dk : dk + part.shape[1]] = part
        return out

    full = placed(plane, UNKNOWN)
    edge = placed(frontier, False)
    landmarks = landmarks or []
    margin, header = 36, 72
    footer = 100 + 24 * len(landmarks)
    cells = max(shape)
    scale = max(1, (side - 2 * margin) // cells)
    width, height = shape[0] * scale, shape[1] * scale
    rgb = np.zeros((*shape, 3), np.uint8)
    for state, colour in MAP_COLOURS.items():
        rgb[full == state] = colour
    rgb[edge] = (236, 176, 64)
    for (landmark, _camera), region in (footprints or {}).items():
        sub = placed(region, False)
        colour = np.array(LANDMARK_COLOURS[landmark % len(LANDMARK_COLOURS)])
        rgb[sub] = (0.35 * rgb[sub] + 0.65 * colour).astype(np.uint8)
    image = rgb.transpose(1, 0, 2)[::-1]  # rows: z descending, cols: x
    image = np.repeat(np.repeat(image, scale, 0), scale, 1)

    canvas = Image.new(
        "RGB", (max(width + 2 * margin, 760), height + header + footer), (250, 250, 247)
    )
    canvas.paste(Image.fromarray(np.ascontiguousarray(image)), (margin, header))
    draw = ImageDraw.Draw(canvas)
    small, label, big = font(15), font(17, bold=True), font(20, bold=True)

    def pixel(x, z):
        return (
            margin + ((x - grid.lower[0]) / grid.voxel - grid.i0) * scale,
            header + height - ((z - grid.lower[2]) / grid.voxel - grid.k0) * scale,
        )

    def text(xy, value, colour, face, anchor="la"):
        draw.text(
            xy,
            value,
            fill=colour,
            font=face,
            anchor=anchor,
            stroke_width=2,
            stroke_fill=(255, 255, 255),
        )

    step = grid.size * scale
    for c in range(grid.cols + 1):
        x = margin + c * step
        draw.line([(x, header), (x, header + height)], fill=(150, 150, 160), width=1)
    for r in range(grid.rows + 1):
        y = header + r * step
        draw.line([(margin, y), (margin + width, y)], fill=(150, 150, 160), width=1)
    for c in range(grid.cols):
        name = string.ascii_uppercase[c]
        draw.text(
            (margin + (c + 0.5) * step, header - 6),
            name,
            fill=(30, 30, 30),
            font=label,
            anchor="ms",
        )
        draw.text(
            (margin + (c + 0.5) * step, header + height + 20),
            name,
            fill=(30, 30, 30),
            font=label,
            anchor="ms",
        )
    for r in range(grid.rows):
        draw.text(
            (margin - 6, header + (r + 0.5) * step),
            str(r + 1),
            fill=(30, 30, 30),
            font=label,
            anchor="rm",
        )
        draw.text(
            (margin + width + 6, header + (r + 0.5) * step),
            str(r + 1),
            fill=(30, 30, 30),
            font=label,
            anchor="lm",
        )

    draw.text((margin, 10), title, fill=(20, 20, 20), font=big)
    cx, cy = canvas.width - margin - 8, 26
    draw.polygon([(cx, cy - 14), (cx - 8, cy + 7), (cx + 8, cy + 7)], fill=(20, 20, 20))
    draw.text((cx - 14, cy), "heading 0°", fill=(20, 20, 20), font=small, anchor="rm")

    for wall in hints.get("walls", []):
        x, y = pixel(*wall["middle"])
        text(
            (x, y - 10),
            f"{wall['label']} {wall['heading']:.0f}°",
            (120, 60, 0),
            small,
            "ms",
        )

    arrow = max(0.6 * step, 28)
    for camera in cameras:
        x, y = pixel(camera["position"][0], camera["position"][2])
        colour = (40, 100, 220)
        if camera.get("heading") is None:
            draw.ellipse([x - 9, y - 9, x + 9, y + 9], outline=colour, width=3)
        else:
            dx, dz = _heading_vector(camera["heading"])
            tip = (x + dx * arrow, y - dz * arrow)
            draw.line([(x, y), tip], fill=colour, width=3)
            left = (tip[0] - dx * 9 + dz * 6, tip[1] + dz * 9 + dx * 6)
            right = (tip[0] - dx * 9 - dz * 6, tip[1] + dz * 9 - dx * 6)
            draw.polygon([tip, left, right], fill=colour)
            draw.ellipse([x - 5, y - 5, x + 5, y + 5], fill=colour)
        text((x + 8, y + 8), camera["label"], colour, label)

    for hint in hints.get("hints", []):
        x, y = pixel(hint["centre"][0], hint["centre"][1])
        draw.ellipse(
            [x - 12, y - 12, x + 12, y + 12],
            fill=(220, 40, 40),
            outline=(255, 255, 255),
            width=2,
        )
        draw.text(
            (x, y), str(hint["id"]), fill=(255, 255, 255), font=label, anchor="mm"
        )

    for (landmark, camera), region in (footprints or {}).items():
        if region.sum() < 2:
            continue
        xs, zs = np.nonzero(region)
        x, y = pixel(
            grid.lower[0] + (np.median(xs) + 0.5) * grid.voxel,
            grid.lower[2] + (np.median(zs) + 0.5) * grid.voxel,
        )
        colour = LANDMARK_COLOURS[landmark % len(LANDMARK_COLOURS)]
        text((x, y), f"L{landmark + 1}·{camera}", colour, small, "mm")

    y0 = header + height + 40
    draw.line([(margin, y0), (margin + step, y0)], fill=(20, 20, 20), width=4)
    draw.text(
        (margin + step + 8, y0),
        f"one grid cell = {grid.size * grid.voxel:.2f} m",
        fill=(20, 20, 20),
        font=small,
        anchor="lm",
    )
    draw.text(
        (margin, y0 + 22),
        "light: explored floor · black: surfaces seen · grey: never seen · amber: edge of the explored · "
        "blue: cameras · red: numbered hints",
        fill=(60, 60, 60),
        font=small,
        anchor="lm",
    )
    for n, item in enumerate(landmarks):
        y = y0 + 46 + 24 * n
        colour = LANDMARK_COLOURS[n % len(LANDMARK_COLOURS)]
        draw.rectangle([margin, y - 7, margin + 14, y + 7], fill=colour)
        draw.text(
            (margin + 22, y),
            f"L{n + 1} {item['what']}",
            fill=(30, 30, 30),
            font=small,
            anchor="lm",
        )
    return np.asarray(canvas)


def label_tile(image, text, width=768, height=432):
    """A view resized for the judge, its camera and heading burned into the top-left corner."""
    picture = (
        Image.fromarray(np.asarray(image, np.uint8))
        .convert("RGB")
        .resize((width, height), Image.LANCZOS)
    )
    draw = ImageDraw.Draw(picture)
    face = font(22, bold=True)
    box = draw.textbbox((10, 8), text, font=face)
    draw.rectangle([box[0] - 6, box[1] - 4, box[2] + 6, box[3] + 4], fill=(0, 0, 0))
    draw.text((10, 8), text, fill=(255, 255, 255), font=face)
    return np.asarray(picture)
