# Product code (ADR 0019 §4), not vendored research: the rest of this package is the research's own, unchanged.
"""A set seeded from a take: the take's frames as pinhole keyframes, and the gate that decides whether their geometry
can be trusted.

The frames come from `geometry.npz` (comfy_nodes/gaiavfx GaiaSaveSetGeometry): Depth Anything 3 multiview's
depth (z-depth), confidence, world-to-camera poses and intrinsics on the frames' own grid, and the people SAM 3 found.
DA3 Base's scale is arbitrary; nothing here claims metres.

The gate's measurements (`measure`) are independent of what the depth network makes agree by construction:
- overlap: how much of each frame's static, trusted surface another frame sees unoccluded. A take with a cut, or one
  that drifts into another place, splits the overlap graph;
- photometric agreement: patches carried from one frame into another by the depth and poses keep their pattern
  (normalised cross-correlation). Depth agreement alone proves little, since DA3 multiview outputs jointly consistent
  depth;
- parallax: the angle between the two cameras' rays to each supported point. A pan or a static shot has none, so no
  depth can be told from it beyond what one frame's network guess says.
Pure numpy.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from . import geometry as geo


@dataclass
class Frames:
    image: np.ndarray  # (B, H, W, 3) uint8
    depth: np.ndarray  # (B, H, W) z-depth
    confidence: np.ndarray  # (B, H, W) DA3's raw confidence (1 and up)
    viewmat: np.ndarray  # (B, 4, 4) world to camera, OpenCV
    k: np.ndarray  # (B, 3, 3) on the frames' grid
    people: np.ndarray  # (B, H, W) bool

    @property
    def count(self) -> int:
        return len(self.depth)

    @property
    def centres(self) -> np.ndarray:
        return geo.camera_centres(self.viewmat)


def load(path) -> Frames:
    with np.load(path) as data:
        frames = Frames(
            image=data["image"],
            depth=data["depth"].astype(np.float32),
            confidence=data["confidence"].astype(np.float32),
            viewmat=data["extrinsics"].astype(float),
            k=data["intrinsics"].astype(float),
            people=data["people"].astype(bool),
        )
    count, height, width = frames.depth.shape
    if frames.image.shape != (count, height, width, 3) or frames.people.shape != (
        count,
        height,
        width,
    ):
        raise ValueError(
            "The geometry's frames, depth and people masks differ in size."
        )
    if frames.viewmat.shape != (count, 4, 4) or frames.k.shape != (count, 3, 3):
        raise ValueError("The geometry's poses or intrinsics do not match its frames.")
    if not (np.isfinite(frames.viewmat).all() and np.isfinite(frames.k).all()):
        raise ValueError("The geometry has poses or intrinsics that are not numbers.")
    return frames


def dilate(mask: np.ndarray, radius: int) -> np.ndarray:
    """A boolean mask grown by `radius` pixels (a square neighbourhood), per frame, never across the image edge."""
    out = mask.copy()
    for axis in (-1, -2):
        grown = out.copy()
        n = out.shape[axis]
        for shift in range(1, min(radius, n - 1) + 1):
            ahead = [slice(None)] * out.ndim
            behind = [slice(None)] * out.ndim
            ahead[axis], behind[axis] = slice(shift, None), slice(None, n - shift)
            grown[tuple(ahead)] |= out[tuple(behind)]
            grown[tuple(behind)] |= out[tuple(ahead)]
        out = grown
    return out


def depth_edges(depth: np.ndarray, ratio: float) -> np.ndarray:
    """Pixels where depth jumps by more than `ratio` to a neighbour: their depth mixes two surfaces."""
    edge = np.zeros(depth.shape, bool)
    for axis in (-1, -2):
        a = np.take(depth, range(depth.shape[axis] - 1), axis)
        b = np.take(depth, range(1, depth.shape[axis]), axis)
        jump = np.abs(a - b) > ratio * np.minimum(a, b)
        pad = [(0, 0)] * depth.ndim
        pad[axis] = (0, 1)
        edge |= np.pad(jump, pad)
        pad[axis] = (1, 0)
        edge |= np.pad(jump, pad)
    return edge


def flags(frames: Frames, *, people_margin: int, edge_ratio: float):
    """Per pixel: `valid` (finite positive depth), `people` (excluded whatever their depth), `solid` (valid and
    outside people), `trust` (solid away from depth edges, where a pixel mixes two surfaces).

    DA3's confidence decides nothing here: it is not comparable between takes (a take whose geometry passes every
    test can read 1.0 everywhere), so the gate judges the geometry as a whole instead."""
    valid = np.isfinite(frames.depth) & (frames.depth > 0)
    people = dilate(frames.people, people_margin)
    solid = valid & ~people
    trust = solid & ~depth_edges(np.where(valid, frames.depth, 0.0), edge_ratio)
    return valid, people, solid, trust


def _gray(image: np.ndarray, blur: int = 0) -> np.ndarray:
    """Luma, box-blurred over (2 * blur + 1) pixels when `blur`."""
    gray = image.astype(np.float32) @ np.array([0.299, 0.587, 0.114], np.float32)
    for axis in (0, 1):
        if blur:
            padded = np.pad(
                gray,
                [(blur, blur) if a == axis else (0, 0) for a in (0, 1)],
                mode="edge",
            )
            total = np.cumsum(padded, axis, dtype=np.float64)
            total = np.concatenate(
                [np.zeros_like(np.take(total, [0], axis)), total], axis
            )
            n = gray.shape[axis]
            gray = (
                (
                    np.take(total, range(2 * blur + 1, 2 * blur + 1 + n), axis)
                    - np.take(total, range(n), axis)
                )
                / (2 * blur + 1)
            ).astype(np.float32)
    return gray


def _ncc(a: np.ndarray, b: np.ndarray) -> np.ndarray:
    a = a - a.mean(-1, keepdims=True)
    b = b - b.mean(-1, keepdims=True)
    denominator = np.sqrt((a * a).sum(-1) * (b * b).sum(-1))
    return np.where(
        denominator > 1e-3, (a * b).sum(-1) / np.maximum(denominator, 1e-6), np.nan
    )


@dataclass
class Pair:
    overlap: float  # fraction of i's sampled trusted surface seen unoccluded by j
    points: int  # how many sampled points that is
    ncc: float  # median patch correlation of those points (nan when too few)
    parallax: float  # fraction of those points seen by the two cameras along rays more than `min_angle` apart
    depth_error: float  # median relative z disagreement of those points


def _project(viewmat, k, world):
    """Pixel coordinates (sample_float's: pixel centres at integers) and z-depth of world points in a camera."""
    cam = world @ viewmat[:3, :3].T + viewmat[:3, 3]
    z = cam[..., 2]
    safe = np.where(z > 1e-6, z, 1.0)
    u = np.where(z > 1e-6, k[0, 0] * cam[..., 0] / safe + k[0, 2] - 0.5, -1e9)
    v = np.where(z > 1e-6, k[1, 1] * cam[..., 1] / safe + k[1, 2] - 0.5, -1e9)
    return u, v, z


def _backproject(viewmat, k, u, v, z):
    rays = np.stack(
        [(u + 0.5 - k[0, 2]) / k[0, 0], (v + 0.5 - k[1, 2]) / k[1, 1], np.ones_like(z)],
        -1,
    )
    c2w = np.linalg.inv(viewmat)
    return (rays * z[..., None]) @ c2w[:3, :3].T + c2w[:3, 3]


def measure(
    frames: Frames,
    trust: np.ndarray,
    solid: np.ndarray,
    *,
    stride=8,
    patch=3,
    min_angle=1.0,
    occlusion=0.1,
    min_texture=6.0,
    spacing=1,
):
    """Every ordered pair of frames: what `i` shows of its trusted surface that `j` sees too (`Pair`). Each patch is
    carried into `j` pixel by pixel through its own depth, so the comparison holds under rotation and foreshortening;
    only wrong depth or poses (or a different place) make it disagree. Only textured patches are compared (a grey
    standard deviation of `min_texture` levels or more): in fog or on a blank wall correlation is noise. With
    `spacing` above 1 the patches are compared at a coarser scale (samples that far apart on images blurred to match),
    which forgives the few pixels of error a long lens magnifies but still tells one room from another."""
    count, height, width = frames.depth.shape
    gray = np.stack([_gray(im, spacing // 2) for im in frames.image])
    reach = patch * spacing
    ys, xs = np.mgrid[reach : height - reach : stride, reach : width - reach : stride]
    offsets = spacing * np.stack(
        np.mgrid[-patch : patch + 1, -patch : patch + 1], -1
    ).reshape(-1, 2)
    centres = frames.centres
    pairs: dict[tuple[int, int], Pair] = {}
    for i in range(count):
        keep = trust[i, ys, xs]
        u, v = xs[keep], ys[keep]
        z = frames.depth[i, v, u]
        world = _backproject(frames.viewmat[i], frames.k[i], u, v, z)
        pu, pv = u[:, None] + offsets[None, :, 1], v[:, None] + offsets[None, :, 0]
        pz = frames.depth[i, pv, pu]
        pz = np.where(
            np.isfinite(pz) & (pz > 0), pz, z[:, None]
        )  # a pixel without depth lies on the centre plane
        patch_world = _backproject(frames.viewmat[i], frames.k[i], pu, pv, pz)
        source = gray[i][pv, pu]
        for j in range(count):
            if j == i:
                continue
            uj, vj, zj = _project(frames.viewmat[j], frames.k[j], world)
            inside = (
                (zj > 1e-6)
                & (uj >= 0)
                & (uj <= width - 1)
                & (vj >= 0)
                & (vj <= height - 1)
            )
            if not inside.any():
                pairs[i, j] = Pair(0.0, 0, math.nan, 0.0, math.nan)
                continue
            seen = geo.sample_float(frames.depth[j], uj[inside], vj[inside], wrap=False)
            usable = (
                geo.sample_float(
                    solid[j].astype(np.float32), uj[inside], vj[inside], wrap=False
                )
                > 0.5
            )
            error = np.zeros(len(u))
            error[inside] = np.abs(zj[inside] - seen) / np.maximum(seen, 1e-6)
            visible = np.zeros(len(u), bool)
            visible[np.flatnonzero(inside)] = usable & (error[inside] < occlusion)
            n = int(visible.sum())
            if n < 20:
                pairs[i, j] = Pair(n / max(len(u), 1), n, math.nan, 0.0, math.nan)
                continue
            tu, tv, _ = _project(frames.viewmat[j], frames.k[j], patch_world[visible])
            within = (
                (tu >= 0) & (tu <= width - 1) & (tv >= 0) & (tv <= height - 1)
            ).all(-1)
            target = geo.sample_float(
                gray[j], tu.clip(0, width - 1), tv.clip(0, height - 1), wrap=False
            )
            textured = source[visible].std(-1) >= min_texture
            ncc = np.where(within & textured, _ncc(source[visible], target), np.nan)
            to_i = centres[i] - world[visible]
            to_j = centres[j] - world[visible]
            cosine = (to_i * to_j).sum(-1) / np.maximum(
                np.linalg.norm(to_i, axis=-1) * np.linalg.norm(to_j, axis=-1), 1e-9
            )
            angle = np.degrees(np.arccos(np.clip(cosine, -1, 1)))
            pairs[i, j] = Pair(
                overlap=n / max(len(u), 1),
                points=n,
                ncc=float(np.nanmedian(ncc)) if np.isfinite(ncc).any() else math.nan,
                parallax=float((angle > min_angle).mean()),
                depth_error=float(np.median(error[visible])),
            )
    return pairs


def components(count: int, edges) -> list[list[int]]:
    """The connected groups of frames under undirected `edges`, largest first."""
    parent = list(range(count))

    def find(a):
        while parent[a] != a:
            parent[a] = parent[parent[a]]
            a = parent[a]
        return a

    for a, b in edges:
        parent[find(a)] = find(b)
    groups: dict[int, list[int]] = {}
    for a in range(count):
        groups.setdefault(find(a), []).append(a)
    return sorted(groups.values(), key=len, reverse=True)


def up_from_cameras(viewmat: np.ndarray) -> np.ndarray:
    """The world direction the cameras call up, averaged (OpenCV cameras look along +z with y down)."""
    rotations = viewmat[:, :3, :3]
    up = -rotations[:, 1, :]  # each camera's -y axis in world coordinates
    return geo.unit(up.mean(0))


def fit_floor(
    points: np.ndarray,
    up: np.ndarray,
    *,
    band=0.15,
    max_tilt=25.0,
    min_support=0.5,
    seed=0,
):
    """A floor plane among the lowest points along `up`: (normal pointing up, offset, support) or None. The lowest
    `band` of the points' height range is searched with RANSAC; a plane tilted more than `max_tilt` degrees from the
    prior, or holding less than `min_support` of that band, is no floor."""
    height = points @ up
    low = points[
        height
        <= np.quantile(height, 0.02)
        + band * (np.quantile(height, 0.98) - np.quantile(height, 0.02))
    ]
    if len(low) < 200:
        return None
    rng = np.random.default_rng(seed)
    scale = float(np.quantile(height, 0.98) - np.quantile(height, 0.02))
    tolerance = 0.01 * scale
    best = None
    for _ in range(300):
        a, b, c = low[rng.choice(len(low), 3, replace=False)]
        normal = np.cross(b - a, c - a)
        if np.linalg.norm(normal) < 1e-9:
            continue
        normal = geo.unit(normal)
        if normal @ up < 0:
            normal = -normal
        if math.degrees(math.acos(min(1.0, float(normal @ up)))) > max_tilt:
            continue
        inliers = np.abs((low - a) @ normal) < tolerance
        if best is None or inliers.sum() > best[2].sum():
            best = (normal, float(a @ normal), inliers)
    if best is None or best[2].mean() < min_support:
        return None
    inliers = low[best[2]]
    centre = inliers.mean(0)
    _, _, vt = np.linalg.svd(inliers - centre)
    normal = vt[2] if vt[2] @ up > 0 else -vt[2]
    return normal, float(centre @ normal), float(best[2].mean())


def yaw_span(viewmat: np.ndarray, up: np.ndarray) -> float:
    """Degrees the viewing direction turns about `up` across the frames: 360 less the widest gap between the
    directions, so an orbit reads 360 and a pan its sweep."""
    forward = viewmat[:, 2, :3]
    flat = forward - (forward @ up)[:, None] * up
    first = geo.unit(flat[0])
    angles = np.sort(
        np.degrees(np.arctan2(flat @ np.cross(up, first), flat @ first)) % 360
    )
    gaps = np.diff(np.concatenate([angles, angles[:1] + 360]))
    return float(360 - gaps.max())


# ---------------------------------------------------------------- the gate


@dataclass(frozen=True)
class Gate:
    """The reconstruction gate's settings (integrations/worldbuilder/knowledge/take.toml [gate])."""

    overlap: float = 0.2  # an edge: this much of a frame's trusted surface seen unoccluded by the other
    agreement: float = 0.3  # and patches that correlate this well at the coarse scale
    spacing: int = 4
    patch: int = 3
    min_texture: float = 6.0
    min_angle: float = 1.0
    coverage: float = (
        2 / 3
    )  # the largest connected group must hold this share of the frames
    parallax: float = 0.2  # the median edge must see this share of its points from directions min_angle apart
    min_trust: float = 0.3  # this share of the pixels must be trusted static surface
    stride: int = 8
    people_margin: int = 6
    edge_ratio: float = 0.08

    @classmethod
    def of(cls, settings: dict | None) -> Gate:
        known = cls.__dataclass_fields__
        return cls(**{k: v for k, v in (settings or {}).items() if k in known})


def verdict(frames: Frames, gate: Gate) -> tuple[dict, tuple]:
    """Whether the frames hold reliable geometry, with every number behind it, and the per-pixel flags.

    The reason, when it fails: `little_surface` (too little static surface outside people), `inconsistent` (the
    frames do not join into one place: a cut, a jump or geometry the patches contradict) or `no_parallax` (the
    camera turns or stands still, so depth is one frame's guess). `kept` names the frames of the largest group."""
    valid, people, solid, trust = flags(
        frames, people_margin=gate.people_margin, edge_ratio=gate.edge_ratio
    )
    n = frames.count
    numbers = {
        "frames": n,
        "people": round(float(people.mean()), 4),
        "trust": round(float(trust.mean()), 4),
    }
    if trust.mean() < gate.min_trust:
        return {**numbers, "passed": False, "reason": "little_surface", "kept": []}, (
            valid,
            people,
            solid,
            trust,
        )
    pairs = measure(
        frames,
        trust,
        solid,
        stride=gate.stride,
        patch=gate.patch,
        min_angle=gate.min_angle,
        min_texture=gate.min_texture,
        spacing=gate.spacing,
    )

    def edge(p: Pair) -> bool:
        return (
            p.overlap >= gate.overlap
            and math.isfinite(p.ncc)
            and p.ncc >= gate.agreement
        )

    edges = [(i, j) for (i, j), p in pairs.items() if edge(p)]
    groups = components(n, edges)
    kept = sorted(groups[0])
    inside = [pairs[i, j] for i, j in edges if i in kept and j in kept]
    centres = frames.centres[kept]
    depth = (
        float(np.median(frames.depth[kept][trust[kept]])) if trust[kept].any() else 1.0
    )
    up = up_from_cameras(frames.viewmat[kept])
    numbers.update(
        groups=[len(g) for g in groups],
        edges=len(edges),
        overlap=round(float(np.median([p.overlap for p in inside])), 4)
        if inside
        else 0.0,
        agreement=round(float(np.median([p.ncc for p in inside])), 4)
        if inside
        else 0.0,
        parallax=round(float(np.median([p.parallax for p in inside])), 4)
        if inside
        else 0.0,
        depth_error=round(float(np.nanmedian([p.depth_error for p in inside])), 4)
        if inside
        else None,
        travel=round(
            float(np.linalg.norm(np.diff(centres, axis=0), axis=1).sum()) / depth, 4
        ),
        turn=round(yaw_span(frames.viewmat[kept], up), 1),
        kept=kept,
    )
    if len(kept) < max(2, math.ceil(gate.coverage * n)):
        return {**numbers, "passed": False, "reason": "inconsistent"}, (
            valid,
            people,
            solid,
            trust,
        )
    if numbers["parallax"] < gate.parallax:
        return {**numbers, "passed": False, "reason": "no_parallax"}, (
            valid,
            people,
            solid,
            trust,
        )
    return {**numbers, "passed": True, "reason": None}, (valid, people, solid, trust)


# ---------------------------------------------------------------- the atlas


def world_points(
    frames: Frames, trust: np.ndarray, kept, stride: int = 6
) -> np.ndarray:
    """Trusted surface points of the kept frames on a coarse grid, in the frames' world."""
    _, height, width = frames.depth.shape
    ys, xs = np.mgrid[0:height:stride, 0:width:stride]
    out = []
    for i in kept:
        keep = trust[i, ys, xs]
        out.append(
            _backproject(
                frames.viewmat[i],
                frames.k[i],
                xs[keep],
                ys[keep],
                frames.depth[i, ys[keep], xs[keep]],
            )
        )
    return np.concatenate(out) if out else np.zeros((0, 3))


def level(frames: Frames, trust: np.ndarray, kept) -> tuple[np.ndarray, dict]:
    """The kept frames' poses in a levelled world: up is -y (the keyframe convention), the first kept frame looks
    along +z at yaw 0, and the origin is the median camera centre. Up is the floor's normal when a floor is found
    among the lowest trusted points, the cameras' mean up otherwise; the result says which."""
    prior = up_from_cameras(frames.viewmat[kept])
    floor = fit_floor(world_points(frames, trust, kept), prior)
    up = floor[0] if floor is not None else prior
    forward = frames.viewmat[kept[0], 2, :3]
    ahead = geo.unit(forward - (forward @ up) * up)
    down = -up
    q = np.stack(
        [np.cross(down, ahead), down, ahead]
    )  # rows: the new x (right), y (down), z (ahead)
    centre = np.median(frames.centres[kept], 0)
    viewmat = frames.viewmat.copy()
    viewmat[:, :3, :3] = frames.viewmat[:, :3, :3] @ q.T
    viewmat[:, :3, 3] = (frames.viewmat[:, :3, :3] @ centre) + frames.viewmat[:, :3, 3]
    info = {
        "up": "floor" if floor is not None else "cameras",
        "tilt_from_cameras": round(
            math.degrees(math.acos(min(1.0, float(up @ prior)))), 2
        ),
        "floor_support": None if floor is None else round(floor[2], 3),
    }
    return viewmat, info


def heldout(count: int) -> set[int]:
    """The keyframes a fit withholds to measure itself: interior ones whose neighbours both train, never the first or
    the last (on a take that moves, its ends see what no other frame sees)."""
    return set(range(3, count - 1, 4))


def layout(record: dict, fill: bool) -> str | None:
    """How a take's frames become a set (ADR 0020 §2), from the gate's `verdict`: `moving` when the gate passed (every
    kept frame where it was seen), `centre` when the camera only stood still or turned and the unseen space will be
    filled (the place seen from about one point), None when the take cannot make a set.

    A `centre` take's frames passed the gate's other tests: they join into one place with the poses DA3 gave them.
    Those poses stay as they are. Moving the centres to one point broke a pure rotation's agreement on 27 October (a
    46 degree pan of the shack splat: 0.61 with DA3's poses, 0.03 with its centres snapped), because DA3 trades some
    rotation for translation where nothing parallax-like constrains it."""
    if record["passed"]:
        return "moving"
    if fill and record.get("reason") == "no_parallax":
        return "centre"
    return None


def heading(viewmat) -> float:
    """A levelled camera's heading in degrees: where its optical axis points, seen from above (0 is +z)."""
    ahead = np.asarray(viewmat, float)[2, :3]
    return math.degrees(math.atan2(ahead[0], ahead[2])) % 360


def hfov(k, width: int) -> float:
    """A pinhole camera's horizontal field of view in degrees."""
    return math.degrees(2 * math.atan(width / (2 * float(np.asarray(k)[0, 0]))))


def distinct_views(
    viewmat, k, kept, trust, spread: float, zoom: float = 1.2, reveal: float = 0.05
) -> list[int]:
    """The frames a one-centre atlas keeps, the frame with the most trusted surface first: one per distinct view, and
    any frame of a view already kept that trusts `reveal` or more of the picture that view's kept frames do not (what
    a person walking through hid in them). A view is distinct when its optical axis turns `spread` degrees or more
    from every view kept (a pan, a tilt) or its focal length differs by the factor `zoom` (a zoom). A still camera
    with nobody moving keeps one frame. In frame order."""
    axes = {i: geo.unit(np.asarray(viewmat[i], float)[2, :3]) for i in kept}

    def same(i, j):
        turn = math.degrees(math.acos(float(np.clip(axes[i] @ axes[j], -1, 1))))
        scale = max(k[i][0, 0], k[j][0, 0]) / min(k[i][0, 0], k[j][0, 0])
        return turn < spread and scale < zoom

    chosen = []
    for i in sorted(kept, key=lambda i: -float(trust[i].mean())):
        alike = [j for j in chosen if same(i, j)]
        if not alike or (trust[i] & ~np.logical_or.reduce([trust[j] for j in alike])).mean() >= reveal:
            chosen.append(i)
    return sorted(chosen)


def build_atlas(
    frames: Frames, folder, place: str, gate: Gate, record: dict, layout: str = "moving"
) -> dict:
    """The atlas of a seed (`record` is `verdict`'s, `layout` what `layout` chose): pinhole keyframes, role
    `generated`, people retracted, in a levelled world, files under `folder/keyframes`. Returns the atlas.

    `moving`: one keyframe per kept frame, every fourth interior one withheld to measure the fit. `centre`: the camera
    stood still or only turned, so the place is seen from about one point (the levelled world's origin is the median
    centre): one keyframe per distinct view (a quarter of the field of view apart, or zoomed) and the frames that see
    what people hid, with the poses DA3 gave them, and none withheld: there is no second viewpoint to measure from."""
    from pathlib import Path

    from . import explore as ex
    from . import memory as mem

    if layout not in ("moving", "centre"):
        raise ValueError(f"Unknown layout {layout!r}.")
    folder = Path(folder)
    valid, people, solid, trust = flags(
        frames, people_margin=gate.people_margin, edge_ratio=gate.edge_ratio
    )
    kept = record["kept"]
    viewmat, info = level(frames, trust, kept)
    if layout == "centre":
        spread = hfov(frames.k[kept[0]], frames.depth.shape[2]) / 4
        kept = distinct_views(viewmat, frames.k, kept, trust, spread)
        withheld = set()
    else:
        withheld = heldout(len(kept))
    atlas = {
        "schema": 1,
        "place": place,
        "version": 0,
        "layout": layout,
        "keyframes": [],
        "decisions": [],
    }
    for n, i in enumerate(kept):
        key = mem.Keyframe(
            f"k{n + 1:02d}",
            "pinhole",
            frames.image[i],
            frames.depth[i],
            valid[i],
            trust[i],
            viewmat[i],
            frames.k[i],
            solid=solid[i],
            retracted=people[i],
        )
        # `people`: the share of the frame a person covers, so a set's poster can show the place, not its cast.
        extra = {"role": "generated", "frame": int(i), "people": round(float(people[i].mean()), 4)}
        if n in withheld:
            extra["heldout"] = True
        atlas = ex.add_keyframe(
            atlas,
            mem.keyframe_record(key, folder / "keyframes", folder, **extra),
            "seed",
        )
    atlas["levelling"] = info
    return atlas


def frames_centre(keyframes) -> np.ndarray:
    """Where a frames atlas is measured from, as a panorama atlas is from its panorama: the median camera centre."""
    return np.median(np.stack([k.centre for k in keyframes]), 0)


def frames_room(keyframes) -> float:
    """A frames atlas's room, the unit its map is measured in: the median distance to the surfaces its frames trust.
    A panorama's room is its clearance all around its centre; a take sees only what lies ahead, so measuring
    clearance in every direction would take the unseen space behind the camera for a wall."""
    depths = [k.depth[k.trust] for k in keyframes if k.trust.any()]
    if not depths:
        raise ValueError("The keyframes trust no surface to measure the room by.")
    return float(np.median(np.concatenate(depths)))


# ---------------------------------------------------------------- the splat


def _keyframe_view(out, scene, record, key, role, **extra):
    """A geometry or trajectory keyframe as a splat2 view, weighted as `splat2.atlas_views` weights it: a geometry
    keyframe trains with its solid depth and never its glass or edge colours; a trajectory frame trains on colour,
    with depth only where its memory was protected; what a later version disowned teaches nothing. Returns the view
    and the seed points of its trusted pixels (none for a withheld or trajectory one)."""
    from . import memory as mem
    from . import splat2

    geometry = record.get("role") != mem.TRAJECTORY
    surface = key.valid & (key.solid if geometry else key.trust)
    depth = np.where(surface, key.depth, np.nan).astype(np.float32)
    rgb_weight = (
        np.where(key.solid & ~key.trust, 0.0, 1.0) if geometry else np.ones(depth.shape)
    ).astype(np.float32)
    rgb_weight[key.retracted] = 0.0
    view = splat2.add_view(
        out,
        scene,
        f"kf_{record['id']}",
        role,
        key.image,
        key.viewmat,
        key.k,
        depth=depth,
        confidence=key.trust.astype(np.float32),
        rgb_weight=rgb_weight,
    )
    view.update(keyframe=record["id"], atlas_role=record["role"], **extra)
    if not geometry or role == "heldout":
        return view, None
    y, x = np.indices(key.depth.shape)
    keep = key.trust & (y % 4 == 0) & (x % 4 == 0)
    return view, (
        splat2.backproject(key.depth, key.viewmat, key.k, keep),
        key.image[keep] / 255.0,
        key.depth[keep] / key.k[0, 0] * 4,
    )


def _seed(parts) -> tuple:
    parts = [p for p in parts if p is not None]
    if not parts:
        empty = np.zeros((0, 3), np.float32)
        return empty, empty, np.zeros(0, np.float32)
    return tuple(np.concatenate(v).astype(np.float32) for v in zip(*parts))


def prepare_frames(out, atlas_path):
    """splat2's starting scene for an atlas without a panorama (the counterpart of `splat2.prepare_atlas`, which
    needs one): every current geometry keyframe of the take as a view, then the stills and trajectory frames earlier
    fill rounds consolidated (`frame_views`), the splat seeded from all their trusted pixels; scale is the median
    depth of the take's keyframes (DA3's units are arbitrary).

    Each view says how the fit treats its pose, whatever the scene's kind (an orbit: real frames, no panorama). A
    `moving` take's frames train with their estimated poses, which the fit refines (splat2's camera optimiser refines
    every `train` view not marked `fixed`), or are withheld as the atlas says. A `centre` take's frames are `anchor`
    views: around one centre nothing could refine their poses, and, as a panorama's anchors are, they keep their
    guaranteed share of the fit's steps once filled stills join. A take's keyframe is told by its record (the frame
    it came from), not its role: a filled still is `generated` too, and a repair rebuilding a filled version used to
    fit its stills as the take's own frames and leave its trajectory frames out (ADR 0020 §8)."""
    import json
    from pathlib import Path

    from . import memory as mem
    from . import splat2

    out, atlas_path = Path(out), Path(atlas_path)
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    layout = atlas.get("layout", "moving")
    records = [
        r
        for r in mem.current(atlas)
        if r.get("kind") == "pinhole" and r.get("role") == "generated" and "frame" in r
    ]
    if not records:
        raise ValueError("The atlas has no keyframes to fit a splat to.")
    scene = splat2.new_scene("orbit")
    seeds, depths = [], []
    for record in records:
        key = mem.load_keyframe(record, atlas_path.parent)
        role = (
            "anchor"
            if layout == "centre"
            else ("heldout" if record.get("heldout") else "train")
        )
        # As splat2.prepare_orbit records it: the pose the fit starts from, which it refines into `viewmat`.
        _, seed = _keyframe_view(
            out, scene, record, key, role, source="take", base_viewmat=key.viewmat.tolist()
        )
        seeds.append(seed)
        depths.append(key.depth[key.trust])
    seed = _seed([*seeds, frame_views(scene, out, atlas_path)])
    scene.update(
        layout=layout,
        scale=float(np.median(np.concatenate(depths))),
        cap_max=len(seed[0]),
        atlas=str(atlas_path.resolve()),
    )
    return scene, seed


def frame_views(scene, out, atlas_path):
    """Add the atlas's keyframes that `scene` lacks as views: the accepted stills and trajectory frames a fill round
    consolidated (ADR 0020 §4). They train, or are withheld when their record says so, with the poses consolidation
    found: `fixed`, so the camera optimiser never moves them. Returns the seed of the new stills' trusted points."""
    import json
    from pathlib import Path

    from . import memory as mem

    atlas_path = Path(atlas_path)
    atlas = json.loads(atlas_path.read_text(encoding="utf-8"))
    have = {v["name"] for v in scene["views"]}
    seeds = []
    for record in mem.current(atlas):
        if record.get("kind") != "pinhole" or record.get("role") not in ("generated", mem.TRAJECTORY):
            continue
        if f"kf_{record['id']}" in have:
            continue
        key = mem.load_keyframe(record, atlas_path.parent)
        role = "heldout" if record.get("heldout") else "train"
        _, seed = _keyframe_view(out, scene, record, key, role, source="fill", fixed=True)
        seeds.append(seed)
    return _seed(seeds)


def fit_frames(out, atlas_path, steps: int) -> dict:
    """Fit a splat to a frames atlas as `splat2.run` fits an `init-atlas` scene: the same seeding, fit and state,
    from `prepare_frames`, and the metrics a take has (`frames_metrics`)."""
    from pathlib import Path

    import torch

    from . import splat2

    if not torch.cuda.is_available():
        raise RuntimeError("Fitting requires CUDA.")
    torch.manual_seed(0)
    out = Path(out)
    if out.exists() and any(out.iterdir()):
        raise ValueError("Output state directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    scene, seed = prepare_frames(out, atlas_path)
    np.savez_compressed(out / "seed.npz", points=seed[0], colors=seed[1], sizes=seed[2])
    params = splat2.make_params(seed)
    state = splat2.fit(out, scene, params, steps, None)
    splat2.save_state(out, scene, params, state)
    return dict(state=str(out), **frames_metrics(out, scene, params))


def extend_frames(out, source, atlas_path, steps: int) -> dict:
    """Extend a take's fit (the state in `source`) into `out` with the atlas's new stills and trajectory frames, as
    `splat2.extend` extends a panorama's (S-inc, R5): the previous state, its camera corrections and its views, plus
    the new views and their trusted points as seeds, refitted for `steps`."""
    import copy
    from pathlib import Path

    import torch

    from . import splat2

    if not torch.cuda.is_available():
        raise RuntimeError("Fitting requires CUDA.")
    torch.manual_seed(0)
    out, source = Path(out), Path(source)
    if out.exists() and any(out.iterdir()):
        raise ValueError("Output state directory must be empty")
    out.mkdir(parents=True, exist_ok=True)
    scene = copy.deepcopy(splat2.read_scene(source))
    splat2.copy_initial_assets(source, out, scene)
    params, previous = splat2.load_state(source)
    seed = frame_views(scene, out, atlas_path)
    params = splat2.append_seed(params, seed)
    scene.update(
        parent=str(source.resolve()),
        round=scene["round"] + 1,
        cap_max=scene["cap_max"] + len(seed[0]),
        atlas=str(Path(atlas_path).resolve()),
        seeded_keyframes=len(seed[0]),
    )
    state = splat2.fit(out, scene, params, steps, previous)
    splat2.save_state(out, scene, params, state)
    return dict(state=str(out), **frames_metrics(out, scene, params))


def frames_metrics(out, scene, params) -> dict:
    """What a fit of a take's frames reports: the take's own views rendered back where the take is trusted (anchor
    PSNR; people and depth edges train nothing, and a filled stage draws the place where an actor stood), the stills
    a fill added (fill PSNR) and splat2's own held-out measure. A take seen from one centre withholds none of its
    frames, so its held-out measure exists only once filling has withheld some. splat2's pose bank and fly-through
    belong to a panorama's centre or an orbit's circle, which a take does not have, so they are not measured."""
    import json
    from pathlib import Path

    from PIL import Image

    from . import splat2

    out = Path(out)
    scores = {"take": [], "fill": []}
    for view in scene["views"]:
        # A state fitted before filling existed holds only the take's own views, unlabelled.
        source = view.get("source", "take")
        if view["role"] not in ("train", "anchor") or source not in scores:
            continue
        rgb, _, _ = splat2.render_numpy(params, view["viewmat"], view["K"], view["width"], view["height"])
        truth = np.asarray(Image.open(out / view["image"]).convert("RGB"), dtype=np.float32) / 255
        trusted = None
        if source == "take" and view.get("data"):
            with np.load(out / view["data"]) as data:
                trusted = data["confidence"] > 0.5
        score = geo.psnr(rgb, truth, trusted)
        if score is not None:
            scores[source].append(score)
    heldout, refined = (
        splat2.heldout_metrics(out, scene, params)
        if any(v["role"] == "heldout" for v in scene["views"])
        else (None, None)
    )
    result = {
        "layout": scene.get("layout", "moving"),
        "anchor_psnr": float(np.mean(scores["take"])) if scores["take"] else None,
        "fill_psnr": float(np.mean(scores["fill"])) if scores["fill"] else None,
        "heldout_psnr": heldout,
        "heldout_psnr_refined": refined,
        "gaussians": scene["gaussians"],
        "steps": scene["steps"],
        "fit_seconds": scene["fit_seconds"],
    }
    (out / "metrics.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return result
