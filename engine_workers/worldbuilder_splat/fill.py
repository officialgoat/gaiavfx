# Product code (ADR 0020 §4), not vendored research: the rest of this package is the research's own, unchanged.
"""Filling a take's unseen space: where the next probes stand and look, and what they may keep of the stage.

R5's planner (`explore.plan_probes`) serves a panorama: it sizes every move by the clearance around the camera, reads
a ray that leaves its grid as open space, and builds level cameras. A take's seed sees only what lies ahead, and its
camera may pitch. So here:
- the free-space map's grid reaches a room beyond every camera, so a view turned toward what the take never saw meets
  unknown space, not the grid's edge (`observed`);
- the room is the depth of what the take saw (`frames.frames_room`), and every view is the seed's own camera: its
  intrinsics, its frame size, its pitch (`turned` turns a camera about the world's vertical);
- a round turns in place first, as long as a turn would still reveal something, keeping enough of the view known
  and enough of it in the memory for a still's depth to be aligned to the stage; only then does it step toward the
  frontier (`plan`). R5's 60 degree start turn is a ceiling, not a target;
- a camera that holds still reveals what people hid: a take's people are never carved, so the place behind them is
  unknown, and a turn toward the side an actor fills keeps too little known. A `hold` probe regenerates only those
  pixels (its first frame is the memory, where the person is not), so the next round can turn there (the spike on
  the shack's still takes, 27 October: five of eleven could turn one way only).
Pure numpy (the free-space map and the memory are the research's own).
"""

from __future__ import annotations

import math
from dataclasses import asdict, dataclass

import numpy as np

from . import explore as ex
from . import geometry as geo
from . import memory as mem

UP = np.array([0.0, -1.0, 0.0])  # the levelled world's up (y is down)


@dataclass(frozen=True)
class Rules:
    """A round's planning rules (integrations/worldbuilder/knowledge/rules.toml [fill])."""

    probes: int = 3  # probes a round
    min_known: float = 0.4  # of a probe's last view: known surface or open sky (R5)
    min_protected: float = 0.25  # of it: pixels the memory keeps, which its still's depth is aligned on
    min_reveal: float = 0.1  # of it: unknown space, or the probe is not worth its render
    turns: tuple = (15.0, 30.0, 45.0, 60.0)  # turns in place to consider, each way
    near: float = 0.25  # two turns from cameras this many rooms apart are taken from one place
    reveal: float = 0.2  # a turn revealing this share of its view or more keeps the round turning
    steps: tuple = (0.25, 0.6)  # a step's length, in rooms (R5)
    margin: float = 1.5  # the map's grid reaches this many rooms beyond every camera
    grid: tuple = (32, 18)  # rays a view is measured with

    @classmethod
    def of(cls, settings: dict | None) -> Rules:
        known = cls.__dataclass_fields__
        values = {k: v for k, v in (settings or {}).items() if k in known}
        return cls(**{k: tuple(v) if isinstance(v, list) else v for k, v in values.items()})


@dataclass
class Probe:
    """One planned probe: a move from a stored keyframe's camera (`start`) to its end, turned `turn` degrees about the
    vertical (positive: to the right) and moved to `position`; what its last view would see."""

    start: str
    kind: str  # "hold", "turn" or "step"
    turn: float
    position: list
    heading: float  # where its last view looks, in degrees
    step: float  # in rooms
    known: float
    surface: float
    unknown: float
    score: float
    protected: float | None = None  # of its last view, checked by reprojecting the memory

    def record(self) -> dict:
        out = asdict(self)
        out["position"] = [round(float(v), 6) for v in self.position]
        for key in ("turn", "heading", "step", "known", "surface", "unknown", "score", "protected"):
            if out[key] is not None:
                out[key] = round(float(out[key]), 4)
        return out


def heading(viewmat) -> float:
    """Where a camera looks, seen from above, in degrees (0 is +z, growing to the right)."""
    ahead = np.asarray(viewmat, float)[2, :3]
    return math.degrees(math.atan2(ahead[0], ahead[2])) % 360


def turned(viewmat, degrees: float, position=None) -> np.ndarray:
    """A camera turned `degrees` about the world's vertical (its heading grows: to the right), keeping its pitch and
    roll, and moved to `position` (its own centre by default)."""
    viewmat = np.asarray(viewmat, float)
    a = math.radians(degrees)
    q = np.array([[math.cos(a), 0, math.sin(a)], [0, 1, 0], [-math.sin(a), 0, math.cos(a)]])
    rotation = viewmat[:3, :3] @ q.T
    centre = -viewmat[:3, :3].T @ viewmat[:3, 3] if position is None else np.asarray(position, float)
    out = np.eye(4)
    out[:3, :3] = rotation
    out[:3, 3] = -rotation @ centre
    return out


def move(viewmat, turn: float, position, count: int) -> np.ndarray:
    """A probe's camera along its move: from the start camera to its end, eased in and out, the turn and the centre
    interpolated together. Both ends are exact."""
    viewmat = np.asarray(viewmat, float)
    start = -viewmat[:3, :3].T @ viewmat[:3, 3]
    end = np.asarray(position, float)
    return np.stack(
        [turned(viewmat, t * turn, start + t * (end - start)) for t in geo.cosine(np.linspace(0, 1, count))]
    )


def observed(keyframes, room: float, margin: float, voxel: float | None = None, stride: int | None = None):
    """`explore.observed_space`, its grid reaching `margin` rooms beyond every camera: the space around a take's camera
    that it never saw stays unknown instead of lying past the grid's edge, where a ray counts as open sky. Everything
    else is the research's: the same rays, carving and voxel rule. The rays are every `stride`th pixel, by default
    about 192 across the narrowest frame (the research's 4 at 768 px), so carving stays as dense whatever the take's
    size."""
    if stride is None:
        stride = max(1, min(key.depth.shape[1] for key in keyframes) // 192)
    rays = []
    pick = (slice(stride // 2, None, stride), slice(stride // 2, None, stride))
    for key in keyframes:
        if key.kind == "erp":
            o, d, dist, sky = ex.pano_rays(key.depth, key.valid, stride, key.centre)
            d = d @ key.rotation
        else:
            o, d, dist, sky = ex.view_rays(key.viewmat, key.k, key.depth, key.valid, stride)
        keep = ~(key.valid & ~key.solid)[pick].reshape(-1)
        rays.append((o[keep], d[keep], dist[keep], sky[keep]))
    o, d, dist, sky = (np.concatenate(part) for part in zip(*rays, strict=True))
    corners = np.array([[x, y, z] for x in (-1, 1) for y in (-1, 1) for z in (-1, 1)], float)
    reach = np.concatenate([k.centre + margin * room * corners for k in keyframes])
    points = np.concatenate([o[~sky] + d[~sky] * dist[~sky, None], reach])
    space = ex.FreeSpace.around(
        points,
        cells=160,
        centres=[key.centre for key in keyframes],
        max_voxel=None if voxel is not None else float(np.quantile(dist[~sky], 0.02)) / 3,
        voxel=voxel,
    ).carve(o, d, dist, sky)
    # A camera stands in free space. Its rays carve forward from its centre, so a centre on a voxel boundary left the
    # voxels behind it unknown, and a ray cast from a hair's breadth away began blocked (27 October).
    for key in keyframes:
        i = space.index(key.centre[None])[0]
        block = tuple(slice(max(0, v - 1), v + 2) for v in i)
        space.state[block] = np.where(space.state[block] == ex.UNKNOWN, ex.FREE, space.state[block])
    return space


def fractions(space: ex.FreeSpace, viewmat, k, width: int, height: int, grid=(32, 18)):
    """Shares of a view's rays that end on known surface, on unknown space, or leave through free space (sky)."""
    small = np.asarray(k, float).copy()
    small[0] *= grid[0] / width
    small[1] *= grid[1] / height
    viewmat = np.asarray(viewmat, float)
    rays = geo.unit(geo.camera_rays(small, *grid).reshape(-1, 3) @ viewmat[:3, :3])
    centre = -viewmat[:3, :3].T @ viewmat[:3, 3]
    _, what = space.cast(centre, rays)
    return (
        float(np.mean(what == ex.OCCUPIED)),
        float(np.mean(what == ex.UNKNOWN)),
        float(np.mean(what == ex.FREE)),
    )


def seen_headings(keyframes) -> float:
    """How much of the horizon the geometry keyframes saw, in degrees: the union of their horizontal fields of view."""
    if any(key.kind == "erp" for key in keyframes):
        return 360.0  # a panorama saw the whole horizon
    covered = np.zeros(360, bool)
    for key in keyframes:
        looks = heading(key.viewmat)
        half = math.degrees(math.atan(key.depth.shape[1] / (2 * key.k[0, 0])))
        for d in range(int(math.floor(looks - half)), int(math.ceil(looks + half))):
            covered[d % 360] = True
    return float(covered.sum())


def plan(
    space, keyframes, room: float, rules: Rules, seed: int = 0, toward: float | None = None
) -> list[Probe]:
    """The next round's probes from the geometry keyframes (the take's frames and the stills accepted so far).

    A probe's last view must keep `rules.min_known` of itself known (surface or open sky, R5) and reveal at least
    `rules.min_reveal`; the memory must keep `rules.min_protected` of its pixels (checked by reprojecting, since a
    voxel map counts surface the memory cannot supply).

    Turns in place first: from every keyframe, a hold (what its people hid) and the turn of `rules.turns` each way
    that reveals most. While the best of these reveals `rules.reveal` or more, the round only turns. Then steps toward the frontier, R5's way: standing
    `rules.steps` rooms short of a frontier cluster, walked to through free space from the nearest keyframe, facing
    it. No two probes look within a quarter of the field of view of each other from one place, or within 60 degrees
    from within half a step. With `toward` (a heading in degrees, an extension the user asked for), every score is
    weighted as R5 steered: by how squarely the probe faces it and how far round it stands that way
    (`explore.steering_weight`)."""
    rng = np.random.default_rng(seed)
    explored = np.mean([k.centre for k in keyframes], axis=0)

    def steered(score, position, looks):
        return score if toward is None else score * ex.steering_weight(position, looks, toward, explored)
    checked = {}

    def keeps(probe, key):
        if id(probe) not in checked:
            h, w = key.depth.shape
            small = key.k.copy()
            small[:2] *= 0.25
            view = turned(key.viewmat, probe.turn, probe.position)
            memory = mem.reproject(keyframes, view, small, w // 4, h // 4)
            probe.protected = float(memory.protected.mean())
            checked[id(probe)] = probe.protected >= rules.min_protected
        return checked[id(probe)]

    def candidate(key, kind, turn, position, step, score_of):
        h, w = key.depth.shape
        view = turned(key.viewmat, turn, position)
        surface, unknown, free = fractions(space, view, key.k, w, h, rules.grid)
        if surface + free < rules.min_known or unknown < rules.min_reveal:
            return None
        looks = heading(view)
        return Probe(
            key.id, kind, turn, list(map(float, position)), looks, step,
            surface + free, surface, unknown, steered(score_of(unknown), position, looks),
        )  # fmt: skip

    # Probes start from a perspective keyframe's own camera; a panorama's crops are not starts yet.
    starts = [key for key in keyframes if key.kind == "pinhole"]
    by_id = {key.id: key for key in starts}
    turns = [
        p
        for key in starts
        for kind, degrees in [("hold", 0.0)] + [("turn", s * d) for d in rules.turns for s in (-1, 1)]
        if (p := candidate(key, kind, degrees, key.centre, 0.0, lambda u: u)) is not None
    ]
    chosen = []
    for probe in sorted(turns, key=lambda p: -p.score):
        if len(chosen) == rules.probes:
            break
        key = by_id[probe.start]
        spread = math.degrees(2 * math.atan(key.depth.shape[1] / (2 * key.k[0, 0]))) / 4
        if any(probe.start == c.start and np.sign(probe.turn) == np.sign(c.turn) for c in chosen):
            continue  # one hold and one turn each way from a keyframe: the one that reveals most
        if any(_alike(probe, c, spread, room, rules.near) for c in chosen) or not keeps(probe, key):
            continue
        chosen.append(probe)
    if chosen and max(p.unknown for p in chosen) >= rules.reveal:
        return chosen
    height = float(np.mean([k.centre[1] for k in keyframes]))
    plane = ex.eye_plane(space, height, max(2 * space.voxel, 0.15 * room))
    steps = []
    for target, _ in ex.frontier_targets(space, plane, height)[:8] if starts else []:
        key = min(starts, key=lambda k: np.linalg.norm((k.centre - target)[[0, 2]]))
        flat = (target - key.centre) * np.array([1.0, 0.0, 1.0])
        if np.linalg.norm(flat) < 1e-9:
            continue
        facing = math.degrees(math.atan2(flat[0], flat[2]))
        for _ in range(12):
            short = rng.uniform(*rules.steps) * room
            position = np.array([target[0], key.centre[1], target[2]]) - short * geo.unit(flat)
            step = float(np.linalg.norm((position - key.centre)[[0, 2]]))
            idx = space.index(position[None])
            if step < 0.1 * room or not space.inside(idx)[0]:
                continue
            if space.state[tuple(idx[0])] != ex.FREE or not ex.path_is_free(space, key.centre, position):
                continue
            for offset in (-30.0, 0.0, 30.0):
                turn = (facing + offset - heading(key.viewmat) + 180) % 360 - 180
                if abs(turn) > max(rules.turns):
                    continue
                probe = candidate(
                    key, "step", turn, position, step / room, lambda u, s=step: u * min(1.0, s / (0.5 * room))
                )
                if probe is not None:
                    steps.append(probe)
    for probe in sorted(steps, key=lambda p: -p.score):
        if len(chosen) == rules.probes:
            break
        key = by_id[probe.start]
        spread = math.degrees(2 * math.atan(key.depth.shape[1] / (2 * key.k[0, 0]))) / 4
        if any(_alike(probe, c, spread, room, rules.near) for c in chosen) or not keeps(probe, key):
            continue
        chosen.append(probe)
    return chosen


def _alike(probe: Probe, other: Probe, spread: float, room: float, near: float) -> bool:
    """Two probes that would show nearly the same: turns or holds from cameras within `near` rooms of each other,
    looking within `spread` degrees of each other; anything with a step, within half the longer step (R5) and 60
    degrees."""
    apart = float(np.linalg.norm(np.subtract(probe.position, other.position)))
    turn = abs((probe.heading - other.heading + 180) % 360 - 180)
    if "step" not in (probe.kind, other.kind):
        return apart < near * room and turn < spread
    return apart < 0.5 * max(probe.step, other.step, near) * room and turn < 60


def coverage(space, keyframes, room: float) -> dict:
    """How far the filling has got: the explored floor and its closure (`explore.coverage`) and the horizon seen."""
    height = float(np.mean([k.centre[1] for k in keyframes]))
    plane = ex.eye_plane(space, height, max(2 * space.voxel, 0.15 * room))
    return {**ex.coverage(space, plane, room), "headings": seen_headings(keyframes)}


def gain(before: dict, after: dict) -> float:
    """A round's gain: the explored floor or the horizon seen, whichever grew more, relative to before (ADR 0020 §4),
    so turning in place is not stopped by a rule meant for walking."""
    area = (after["area"] - before["area"]) / max(before["area"], 1e-9)
    seen = (after["headings"] - before["headings"]) / max(before["headings"], 1e-9)
    return float(max(area, seen))


# ---------------------------------------------------------------- a probe's memory and still (R4, R5)


def probe_camera(key) -> tuple[np.ndarray, int, int]:
    """A probe's frame: the start keyframe's own camera, its size cut down to H3's multiple of 32, the intrinsics
    scaled with it."""
    h, w = key.depth.shape
    width, height = (w // 32) * 32, (h // 32) * 32
    if not width or not height:
        raise ValueError("A keyframe smaller than 32 px cannot start a probe.")
    k = np.asarray(key.k, float).copy()
    k[0] *= width / w
    k[1] *= height / h
    return k, width, height


def _memory_frames(atlas_path, ids, views, k, width, height, grow, indices):
    """Worker for `memory_path` (a separate process on Windows: module level, plain arguments): the memory of some of
    a move's frames, its unknown filled from its surroundings, and each frame's regenerate patches (lab._memory_chunk
    without its files)."""
    from . import lab
    from . import memory as mem

    keyframes = [key for key in mem.load_keyframes(atlas_path) if key.id in ids]
    out = []
    for n, view in zip(indices, views, strict=True):
        memory = mem.reproject(keyframes, np.asarray(view), k, width, height)
        region = lab.patch_holes((~mem.edit_region(memory, grow)).astype(np.float32))
        out.append((n, lab.fill_holes(memory.rgb, memory.known), region, memory.fractions()))
    return out


def memory_path(atlas_path, keyframes, start, probe: Probe, count: int = 73, grow: int = 8, workers: int = 6):
    """The memory along a probe's move (R4's ControlNet use 1, R5's retrieval by overlap): the move's cameras, every
    frame's memory (its unknown filled, so the Fun ControlNet's source carries no flat patch), its regenerate mask in
    whole H3 latent patches held for each latent frame's group (`lab.latent_frame_or`), and the shares per frame. The
    first frame is the memory of the start camera: where a person stood in the take, it is filled, not kept."""
    from . import lab
    from . import memory as mem

    k, width, height = probe_camera(start)
    views = move(start.viewmat, probe.turn, probe.position, count)
    ids = sorted(
        {
            key.id
            for view in (views[0], views[-1])
            for key in mem.select_keyframes(keyframes, view, k, width, height)
        }
    )
    chunks = [list(range(i, count, workers)) for i in range(min(workers, count))]
    if workers > 1:
        from concurrent.futures import ProcessPoolExecutor

        with ProcessPoolExecutor(max_workers=len(chunks)) as pool:
            futures = [
                pool.submit(
                    _memory_frames, str(atlas_path), ids, [views[n] for n in chunk], k, width, height, grow, chunk
                )
                for chunk in chunks
            ]
            results = [row for future in futures for row in future.result()]
    else:
        results = _memory_frames(atlas_path, ids, list(views), k, width, height, grow, list(range(count)))
    results.sort(key=lambda row: row[0])
    frames = np.stack([row[1] for row in results])
    regions = lab.latent_frame_or([row[2] for row in results])
    return {
        "views": views,
        "k": k,
        "size": (width, height),
        "frames": frames,
        "regions": regions,
        "report": {
            "keyframes": ids,
            "regen_mean": round(float(np.mean(regions)), 4),
            "regen_last": round(float(np.mean(regions[-1])), 4),
            "frames": [row[3] for row in results],
        },
    }


def refine(keyframes, frame, viewmat, k, step: float, lateral=0.3, forward=0.3, turn=6.0):
    """`memory.pose_refine` for a camera that may pitch: where a generated frame was really seen from. The memory is
    reprojected at offsets around the planned camera (left and right, forward and back by up to `lateral` and `forward`
    x `step`, along the ground; turned up to `turn` degrees about the vertical), coarse to fine, and the offset whose
    memory best matches the frame's colours on protected pixels wins. The research's version rebuilds a level camera,
    which would drop a take's pitch. Returns the refined camera and the research's report."""
    from PIL import Image

    from . import memory as mem

    height, width = np.asarray(frame).shape[:2]
    w = 256
    h = max(1, round(w * height / width))
    small_frame = np.asarray(
        Image.fromarray(np.asarray(frame, dtype=np.uint8)).resize((w, h), Image.BILINEAR), dtype=np.float32
    )
    small = np.asarray(k, float).copy()
    small[0] *= w / width
    small[1] *= h / height
    base = np.asarray(viewmat, float)
    centre = -base[:3, :3].T @ base[:3, 3]
    flat = np.array([1.0, 0.0, 1.0])
    right, ahead = geo.unit(base[0, :3] * flat), geo.unit(base[2, :3] * flat)

    def pose(dx, dz, dyaw):
        return turned(base, dyaw, centre + step * (dx * right + dz * ahead))

    def error(offset):
        memory = mem.reproject(keyframes, pose(*offset), small, w, h)
        if memory.protected.mean() < 0.05:
            return np.inf
        diff = np.abs(small_frame - memory.rgb.astype(np.float32)).mean(-1) / 255
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
                        for c, s, d in zip(centre_offset, spans, (dx, dz, dyaw), strict=True)
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


def probe_still(keyframes, frame, end, k, step: float, grow: int = 8) -> dict:
    """A probe's destination still (R5 A1 step 5a): the video's last frame, where it was really seen from (`refine`),
    the memory reprojected there and every protected pixel restored exactly (the protected merge over the region a
    generator may change)."""
    from PIL import Image

    from . import lab
    from . import memory as mem

    width, height = int(round(2 * k[0, 2])), int(round(2 * k[1, 2]))
    frame = np.asarray(Image.fromarray(np.asarray(frame, np.uint8)).resize((width, height), Image.LANCZOS))
    near = mem.select_keyframes(keyframes, end, k, width, height)
    refined, pose = refine(near, frame, end, k, step)
    memory = mem.reproject(near, refined, k, width, height)
    region = mem.edit_region(memory, grow)
    filled = lab.fill_holes(memory.rgb, memory.known)
    merged, merge = mem.protected_merge(filled, region, frame)
    return {
        "merged": merged,
        "memory": filled,
        "protected": memory.protected,
        "region": region,
        "view": refined,
        "pose": pose,
        "merge": {**merge, **memory.fractions()},
    }


def consolidate(atlas_path, folder, stills: list, rules: dict, decode) -> tuple[dict, dict]:
    """One round's stills into the next atlas version (R5 A1 steps 5-6), one at a time in order, each against the atlas
    and the stills accepted before it this round, so two probes that disagree cannot both enter.

    Each still (`stills`: dicts with `key`, `probe`, `move`, `base`, the folder holding its merged.png, view.json,
    protected.png, region.png and pose.json, `geometry`, MoGe's depth and mask with SAM 3's glass, `video` and
    `poses`, its move's cameras) passes R5's geometric acceptance (`lab._consolidate_one`: the residual, the
    contradiction and the anchor agreement, waived where it supplies under 0.5% of an anchor) or enters as appearance
    only. An accepted probe also gives trajectory frames (`rules["trajectory"]`, at their cameras, kept where their
    protected pixels agree within `rules["agree"]`): splat supervision only, never protected memory; the second and
    fifth of them are withheld, as R5 withheld them, to measure the fit. `decode(video, width, height)` reads a
    video's frames. Returns the atlas and the round's report."""
    import json
    from pathlib import Path

    from PIL import Image

    from . import lab
    from . import memory as mem

    atlas_path, folder = Path(atlas_path), Path(folder)
    atlas = mem.rebase(ex.load_atlas(atlas_path), atlas_path.parent, folder)
    keyframes = mem.load_keyframes(atlas_path)
    trajectories = sum(r.get("role") == mem.TRAJECTORY for r in atlas["keyframes"])
    reports = []
    for item in stills:
        base = Path(item["base"])
        image = np.asarray(Image.open(base / "merged.png").convert("RGB"))
        h, w = image.shape[:2]
        with np.load(item["geometry"]) as data:
            glass = np.squeeze(data["glass"]) > 0.5 if "glass" in data.files else np.zeros((h, w), bool)
        if glass.shape != (h, w):
            glass = np.asarray(Image.fromarray(glass).resize((w, h), Image.NEAREST))
        glass = glass if glass.mean() < 0.5 else np.zeros_like(glass)
        try:
            candidate, report, accepted = lab._consolidate_one(
                image,
                base,
                item["geometry"],
                keyframes,
                item["key"],
                glass=mem.grow(glass, 4),
                tolerance=rules["residual_max"],
                contradiction=rules["contradiction_max"],
                anchor_psnr=rules["anchor_psnr_min"],
                voxel=rules.get("voxel"),
                trusted_anchor=True,
            )
        except RuntimeError as exc:  # too few kept pixels to align: nothing to consolidate
            reports.append({"key": item["key"], "probe": item["probe"], "accepted": False, "error": str(exc)})
            continue
        report["pose"] = json.loads((base / "pose.json").read_text(encoding="utf-8"))
        report["probe"] = item["probe"]
        record = mem.keyframe_record(
            candidate,
            folder / "keyframes",
            folder,
            role="generated" if accepted else mem.APPEARANCE_ONLY,
            source=item["probe"],
            move=item["move"],
            consolidation=report,
        )
        atlas = ex.add_keyframe(atlas, record, "accepted" if accepted else mem.APPEARANCE_ONLY)
        if accepted:
            keyframes = [*keyframes, candidate]
            report["trajectory"] = []
            poses = json.loads(Path(item["poses"]).read_text(encoding="utf-8"))
            frames = decode(item["video"], w, h)
            for f in rules["trajectory"]:
                if f >= min(len(frames), len(poses)):
                    continue
                view, k = np.asarray(poses[f]["viewmat"]), np.asarray(poses[f]["K"])
                memory = mem.reproject(keyframes, view, k, w, h)
                if memory.protected.mean() < 0.2:
                    continue
                error = float((np.abs(frames[f].astype(np.float32) - memory.rgb).mean(-1) / 255)[memory.protected].mean())
                report["trajectory"].append({"frame": f, "error": round(error, 4)})
                if error > rules["agree"]:
                    continue
                frame_key = mem.Keyframe(
                    f"{item['key']}_t{f}",
                    "pinhole",
                    frames[f],
                    np.where(np.isfinite(memory.depth), memory.depth, 0.0),
                    memory.known & np.isfinite(memory.depth),
                    memory.protected,
                    view,
                    k,
                    solid=np.zeros(memory.known.shape, bool),
                )
                extra = {"heldout": True} if trajectories in (1, 4) else {}
                trajectories += 1
                atlas = ex.add_keyframe(
                    atlas,
                    mem.keyframe_record(
                        frame_key,
                        folder / "keyframes",
                        folder,
                        role=mem.TRAJECTORY,
                        source=f"{item['probe']}#{f}",
                        agreement=round(error, 4),
                        **extra,
                    ),
                    mem.TRAJECTORY,
                )
        reports.append(report)
    return atlas, {
        "accepted": [r["key"] for r in reports if r.get("accepted")],
        "planned": len(stills),
        "stills": reports,
    }
