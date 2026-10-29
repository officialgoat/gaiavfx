# Vendored from the maintained engine implementation on branch worldbuilder (26 October 2026) for the worldbuilder engine worker
# (ADR 0018 §4); only the imports changed. The research module stays the reference; keep their numerics equal.
"""Worldbuilder v2 geometry. OpenCV world/cameras, pixel centres at i + 0.5.

Only numpy is required. Angles at the public panorama API are degrees; circle
azimuths are radians. Depth means camera z, distance means radial ERP distance.
"""

from __future__ import annotations

from itertools import combinations, pairwise

import numpy as np
from .erp import directions, rotation

F = np.diag([1.0, -1.0, 1.0])
WORLD = "opencv: x right, y down, z forward at yaw 0"


def unit(x):
    x = np.asarray(x, dtype=np.float64)
    norm = np.linalg.norm(x, axis=-1, keepdims=True)
    if np.any(norm < 1e-12):
        raise ValueError("Cannot normalize a zero vector")
    return x / norm


def intrinsics(hfov=75.0, width=1024, height=576):
    if not 0 < hfov < 180 or min(width, height) < 1:
        raise ValueError("Invalid pinhole dimensions or field of view")
    f = width / 2 / np.tan(np.radians(hfov) / 2)
    return np.array([[f, 0, width / 2], [0, f, height / 2], [0, 0, 1]])


def crop_camera(yaw, pitch=0.0, hfov=75.0, w=1024, h=576):
    view = np.eye(4)
    view[:3, :3] = (F @ rotation(yaw, pitch) @ F).T
    return view, intrinsics(hfov, w, h)


def camera_rays(k, width, height):
    y, x = np.mgrid[:height, :width]
    return np.stack(
        [(x + 0.5 - k[0, 2]) / k[0, 0], (y + 0.5 - k[1, 2]) / k[1, 1], np.ones_like(x)],
        -1,
    )


def camera_centres(views):
    return np.linalg.inv(np.asarray(views))[..., :3, 3]


def look_at(eye, target, up):
    eye = np.asarray(eye)
    forward = unit(np.asarray(target) - eye)
    right = unit(np.cross(forward, up))
    result = np.eye(4)
    result[:3, :3] = np.stack([right, np.cross(forward, right), forward])
    result[:3, 3] = -result[:3, :3] @ eye
    return result


def sample_float(image, u, v, wrap=True):
    """Bilinear sampling without ERP image sampler's uint8 clipping/rounding."""
    image = np.asarray(image)
    h, w = image.shape[:2]
    u, v = np.broadcast_arrays(u, v)
    x, y = np.floor(u).astype(int), np.floor(v).astype(int)
    dx, dy = u - x, v - y
    x0, x1 = (
        (x % w, (x + 1) % w) if wrap else (x.clip(0, w - 1), (x + 1).clip(0, w - 1))
    )
    y0, y1 = y.clip(0, h - 1), (y + 1).clip(0, h - 1)
    if image.ndim == 3:
        dx, dy = dx[..., None], dy[..., None]
    return ((1 - dx) * image[y0, x0] + dx * image[y0, x1]) * (1 - dy) + (
        (1 - dx) * image[y1, x0] + dx * image[y1, x1]
    ) * dy


def erp_coordinates(world_rays, width, height):
    rays = unit(world_rays)
    lon = np.arctan2(rays[..., 0], rays[..., 2])
    lat = np.arcsin(np.clip(-rays[..., 1], -1, 1))
    return (lon + np.pi) / (2 * np.pi) * width - 0.5, (
        np.pi / 2 - lat
    ) / np.pi * height - 0.5


def erp_view_depth(distance, viewmat, k, width, height):
    rays = camera_rays(k, width, height)
    world = rays @ viewmat[:3, :3]
    u, v = erp_coordinates(world, distance.shape[1], distance.shape[0])
    return sample_float(distance, u, v) / np.linalg.norm(rays, axis=-1)


def seam_weights(viewmat, k, width, height, degrees=8.5):
    if degrees == 0:
        return np.ones((height, width), dtype=np.float32)
    world = camera_rays(k, width, height) @ viewmat[:3, :3]
    lon = np.degrees(np.arctan2(world[..., 0], world[..., 2]))
    return (180 - np.abs(lon) > degrees).astype(np.float32)


def icosahedron_directions():
    phi = (1 + np.sqrt(5)) / 2
    vertices = np.array(
        [(0, a, b * phi) for a in (-1, 1) for b in (-1, 1)]
        + [(a, b * phi, 0) for a in (-1, 1) for b in (-1, 1)]
        + [(b * phi, 0, a) for a in (-1, 1) for b in (-1, 1)]
    )
    faces = [
        vertices[list(ids)].mean(0)
        for ids in combinations(range(12), 3)
        if all(
            np.isclose(np.linalg.norm(vertices[i] - vertices[j]), 2)
            for i, j in combinations(ids, 2)
        )
    ]
    return unit(faces)


def icosahedron_views():
    result = []
    for ray in icosahedron_directions():
        yaw = float(np.degrees(np.arctan2(ray[0], ray[2])))
        pitch = float(np.degrees(np.arcsin(-ray[1])))
        view, k = crop_camera(yaw, pitch, 80, 768, 768)
        result.append({"yaw": yaw, "pitch": pitch, "viewmat": view, "K": k})
    return result


def pano_seed(distance, mask, image):
    """One point map: stride 2 surfaces, stride 4 sky, no duplicate pixels."""
    distance = np.asarray(distance, dtype=float).copy()
    valid = np.isfinite(distance) & (distance > 0) & mask
    if not valid.any():
        raise ValueError("Panorama has no valid surface distances")
    sky_distance = float(np.percentile(distance[valid], 95) * 5)
    distance[~mask] = sky_distance
    distance[~np.isfinite(distance) | (distance <= 0)] = sky_distance
    logd = np.log(distance)
    dx = (np.roll(logd, -1, 1) - np.roll(logd, 1, 1)) / 2
    dy = np.gradient(logd, axis=0)
    y, x = np.indices(distance.shape)
    keep = valid & (np.hypot(dx, dy) <= 0.05) & (x % 2 == 0) & (y % 2 == 0)
    keep |= ~mask & (x % 4 == 0) & (y % 4 == 0)
    h, w = distance.shape
    rays = directions(w, h) @ F
    points = (rays * distance[..., None])[keep]
    sizes = distance[keep] * (2 * np.pi / w) * np.where(mask[keep], 2, 4)
    lat = np.degrees(np.arcsin(-rays[..., 1]))
    level = valid & (np.abs(lat) <= 15)
    if not level.any():
        raise ValueError("No valid distances within 15 degrees of the equator")
    return (
        points,
        image[keep] / 255.0,
        sizes,
        float(np.median(distance[level])),
        distance,
    )


def cosine(t):
    return (1 - np.cos(np.pi * np.asarray(t))) / 2


def petal_path(yaw, r, scale, distance, half_angle=25.0, anchor=None):
    anchor = crop_camera(yaw)[0] if anchor is None else np.asarray(anchor)
    # One constant radius capped over every move direction, including the arc.
    # At latitude zero the bilinear field is piecewise linear in longitude.
    # Its minimum is at an ERP column centre or an arc endpoint, so include
    # all knots rather than sampling an arbitrary number of directions.
    knots = (np.arange(distance.shape[1]) + 0.5) / distance.shape[1] * 360 - 180
    relative = (knots - yaw + 180) % 360 - 180
    angles = yaw + np.concatenate(
        ([-half_angle, half_angle], relative[np.abs(relative) <= half_angle])
    )
    move = np.stack(
        [np.sin(np.radians(angles)), np.zeros_like(angles), np.cos(np.radians(angles))],
        -1,
    )
    u, v = erp_coordinates(move, distance.shape[1], distance.shape[0])
    radius = min(r * scale, 0.5 * float(np.min(sample_float(distance, u, v))))
    if radius < 0 or not np.isfinite(radius):
        raise ValueError("Invalid petal radius")
    result = []
    for frame in range(73):
        if frame <= 24:
            a, rad = yaw - half_angle, radius * cosine(frame / 24)
        elif frame <= 48:
            a, rad = (
                yaw - half_angle + 2 * half_angle * cosine((frame - 24) / 24),
                radius,
            )
        else:
            a, rad = yaw + half_angle, radius * (1 - cosine((frame - 48) / 24))
        eye = rad * np.array([np.sin(np.radians(a)), 0, np.cos(np.radians(a))])
        view = anchor.copy()
        view[:3, 3] = -view[:3, :3] @ eye
        result.append(view)
    result[0], result[-1] = anchor.copy(), anchor.copy()
    return np.stack(result)


def pan_path(yaw_from, yaw_to, count=73):
    """A turn in place at the panorama centre, at constant speed from one crop view to another (R3: segments
    from anchor to anchor join into a 360 pan without stopping, and both ends are crops of the panorama)."""
    if not np.isfinite([yaw_from, yaw_to]).all() or abs(yaw_to - yaw_from) > 180:
        raise ValueError("A pan turns at most 180 degrees between finite yaws")
    path = np.stack([crop_camera(y)[0] for y in np.linspace(yaw_from, yaw_to, count)])
    path[0], path[-1] = crop_camera(yaw_from)[0], crop_camera(yaw_to)[0]
    return path


def walk_keys(keys):
    keys = np.asarray(keys, dtype=float)
    if (
        keys.ndim != 2
        or keys.shape[1] != 3
        or len(keys) < 2
        or not np.isfinite(keys).all()
    ):
        raise ValueError("A walk needs two or more finite [x, z, yaw] keys")
    return keys


def walk_shrink(keys, scale, distance, per_segment=256):
    """The factor (at most 1) that keeps a walk within half the panorama's distance in every direction it passes:
    densely along each straight segment between keys, plus every ERP column the segments cross, so a narrow near
    object between samples cannot slip through (the petal's cap, for any polyline)."""
    keys = walk_keys(keys)
    t = np.linspace(0, 1, per_segment)
    eyes = (
        np.concatenate(
            [a[:2] + (b[:2] - a[:2]) * t[:, None] for a, b in pairwise(keys)]
        )
        * scale
    )
    xz = np.stack([eyes[:, 0], np.zeros(len(eyes)), eyes[:, 1]], -1)
    radius = np.hypot(xz[:, 0], xz[:, 2])
    out = radius > 1e-12
    if not out.any():
        return 1.0
    directions = xz[out] / radius[out, None]
    u, v = erp_coordinates(directions, distance.shape[1], distance.shape[0])
    room = 0.5 * sample_float(distance, u, v)
    factor = float(np.min(room / radius[out]))
    # Columns crossed between consecutive samples: the field's minimum lies at a column centre or an endpoint.
    lon = np.degrees(np.arctan2(directions[:, 0], directions[:, 2]))
    knots = (np.arange(distance.shape[1]) + 0.5) / distance.shape[1] * 360 - 180
    r_out = radius[out]
    for a, b, ra, rb in zip(lon[:-1], lon[1:], r_out[:-1], r_out[1:], strict=True):
        step = (b - a + 180) % 360 - 180
        crossed = knots[((knots - a + 180) % 360 - 180) * np.sign(step) > 0]
        crossed = crossed[np.abs((crossed - a + 180) % 360 - 180) < abs(step)]
        if len(crossed):
            ray = np.stack(
                [
                    np.sin(np.radians(crossed)),
                    np.zeros(len(crossed)),
                    np.cos(np.radians(crossed)),
                ],
                -1,
            )
            cu, cv = erp_coordinates(ray, distance.shape[1], distance.shape[0])
            factor = min(
                factor,
                float(np.min(0.5 * sample_float(distance, cu, cv) / max(ra, rb))),
            )
    return min(1.0, factor)


def walk_path(keys, scale, distance, count=73, cap_keys=None):
    """Through keys [x, z, yaw]: x and z in units of the panorama's scale (yaw 0 looks along +z, yaw 90 along +x),
    yaw in degrees as given (unwrapped by the caller). Position and yaw are interpolated linearly at the centre's
    height. The walk shrinks toward the centre by `walk_shrink` of `cap_keys` (the keys of the whole chain a leg
    belongs to, so every leg of a loop shrinks alike and the legs still meet; this walk's own keys by default)."""
    keys = walk_keys(keys)
    factor = walk_shrink(keys if cap_keys is None else cap_keys, scale, distance)
    at = np.linspace(0, len(keys) - 1, count)
    index = np.arange(len(keys))
    x, z, yaw = (np.interp(at, index, keys[:, i]) for i in range(3))
    eyes = factor * scale * np.stack([x, np.zeros_like(x), z], -1)
    result = []
    for eye, angle in zip(eyes, yaw, strict=True):
        view = crop_camera(angle)[0]
        view[:3, 3] = -view[:3, :3] @ eye
        result.append(view)
    return np.stack(result)


def fit_circle(centres, up=None):
    centres = np.asarray(centres, dtype=float)
    origin = centres.mean(0)
    _, singular, vt = np.linalg.svd(centres - origin, full_matrices=False)
    if len(singular) < 3 or singular[1] < 1e-10:
        raise ValueError("Camera centres cannot define a circle")
    normal = vt[-1]
    reference = np.array([0, -1, 0]) if up is None else up
    if normal @ reference < 0:
        normal = -normal
    # Fixed basis avoids arbitrary SVD signs in symmetric rings.
    basis = np.eye(3)[np.argmin(np.abs(normal))]
    u = unit(basis - normal * (basis @ normal))
    v = np.cross(normal, u)
    xy = (centres - origin) @ np.stack([u, v]).T
    sol = np.linalg.lstsq(
        np.column_stack([2 * xy, np.ones(len(xy))]), (xy * xy).sum(1), rcond=None
    )[0]
    centre = origin + sol[0] * u + sol[1] * v
    radius = float(np.sqrt(max(0, sol[2] + sol[0] ** 2 + sol[1] ** 2)))
    if radius < 1e-10:
        raise ValueError("Degenerate circle")
    delta = centres - centre
    azimuth = np.unwrap(np.arctan2(delta @ v, delta @ u))
    radial = np.hypot(delta @ u, delta @ v)
    return {
        "centre": centre,
        "normal": normal,
        "radius": radius,
        "u": u,
        "v": v,
        "azimuth": azimuth,
        "height": delta @ normal,
        "rms": float(np.sqrt(np.mean((radial - radius) ** 2 + (delta @ normal) ** 2))),
    }


def azimuth_monotonicity(azimuth):
    steps = np.diff(azimuth)
    if not len(steps):
        return 0.0
    return float(max(np.mean(steps > 1e-10), np.mean(steps < -1e-10)))


def trajectory_check(viewmats):
    views = np.asarray(viewmats, dtype=float)
    failures = []
    if not np.isfinite(views).all():
        return {"trajectory": "failed", "failures": ["nonfinite_pose"]}, None
    try:
        c2w = np.linalg.inv(views)
        circle = fit_circle(c2w[:, :3, 3], -c2w[:, :3, 1].mean(0))
    except (ValueError, np.linalg.LinAlgError):
        return {"trajectory": "failed", "failures": ["degenerate_circle"]}, None
    azimuth = circle["azimuth"]
    span = float(np.degrees(np.ptp(azimuth)))
    monotonicity = azimuth_monotonicity(azimuth)
    if circle["rms"] > 0.1 * circle["radius"]:
        failures.append("circle_residual_3d")
    if span < 300:
        failures.append("azimuth_span")
    if monotonicity < 0.9:
        failures.append("azimuth_monotonicity")
    return {
        "trajectory": "failed" if failures else "passed",
        "failures": failures,
        "residual_rms_3d": circle["rms"],
        "span_degrees": span,
        "monotonicity": monotonicity,
    }, circle


def optical_target(views):
    c2w = np.linalg.inv(views)
    f, centres = c2w[:, :3, 2], c2w[:, :3, 3]
    a = np.eye(3)[None] - f[..., None] * f[:, None, :]
    return np.linalg.lstsq(
        a.sum(0), np.einsum("nij,nj->ni", a, centres).sum(0), rcond=None
    )[0]


def circle_position(circle, azimuth, radius=None, height=0.0):
    radius = circle["radius"] if radius is None else radius
    return (
        np.asarray(circle["centre"])
        + radius
        * (
            np.cos(azimuth) * np.asarray(circle["u"])
            + np.sin(azimuth) * np.asarray(circle["v"])
        )
        + height * np.asarray(circle["normal"])
    )


def arc_path(first, last, circle, target, inward=0.1, direction=1):
    eyes = camera_centres([first, last])
    delta = eyes - circle["centre"]
    a = np.arctan2(delta @ circle["v"], delta @ circle["u"])
    change = (direction * (a[1] - a[0])) % (2 * np.pi) * direction
    height = delta @ circle["normal"]
    radius = np.hypot(delta @ circle["u"], delta @ circle["v"])
    result = [
        look_at(
            circle_position(
                circle,
                a[0] + change * t,
                ((1 - t) * radius[0] + t * radius[1])
                * (1 - inward * np.sin(np.pi * t)),
                (1 - t) * height[0] + t * height[1],
            ),
            target,
            circle["normal"],
        )
        for t in np.linspace(0, 1, 73)
    ]
    result[0], result[-1] = np.asarray(first).copy(), np.asarray(last).copy()
    return np.stack(result).astype(np.asarray(first).dtype)


def deduplicate_seed(seed):
    """Deduplicate all accepted clips on a shared adaptive world voxel grid.

    Each depth/fx*2 footprint selects the nearest power-of-two grid level,
    within sqrt(2) of its size. Coarse occupied cells suppress finer samples
    inside them as well, so changing camera distance cannot create duplicates
    merely by selecting another level. Keep the first observation per cell.
    """
    points, colors, sizes = seed
    if not len(points):
        return seed
    if not np.isfinite(sizes).all() or np.any(sizes <= 0):
        raise ValueError("Hole voxel size must be finite and positive")
    base = float(np.median(sizes))
    levels = np.rint(np.log2(sizes / base)).astype(int)
    occupied, selected = {}, []

    def cell_keys(cells):
        return np.ascontiguousarray(cells).view(np.dtype((np.void, 3 * 8))).ravel()

    for level in sorted(np.unique(levels), reverse=True):
        indices = np.flatnonzero(levels == level)
        cells = np.floor(
            np.asarray(points[indices], dtype=float) / (base * 2.0**level)
        ).astype(np.int64)
        _, first = np.unique(cells, axis=0, return_index=True)
        indices, cells = indices[first], cells[first]
        keep = np.ones(len(indices), dtype=bool)
        for coarse, keys in occupied.items():
            ancestors = cells // (2 ** int(coarse - level))
            keep &= ~np.isin(cell_keys(ancestors), keys)
        selected.append(indices[keep])
        occupied[level] = cell_keys(cells[keep])
    indices = np.concatenate(selected)
    indices.sort()
    return points[indices], colors[indices], sizes[indices]


def slerp_pose(first, last, t):
    """SO(3) shortest-arc slerp via relative axis-angle, linear camera centre."""
    if t == 0:
        return np.asarray(first).copy()
    if t == 1:
        return np.asarray(last).copy()
    a, b = np.linalg.inv(first), np.linalg.inv(last)
    relative = a[:3, :3].T @ b[:3, :3]
    angle = np.arccos(np.clip((np.trace(relative) - 1) / 2, -1, 1))
    if angle < 1e-10:
        rot = np.eye(3)
    else:
        if np.pi - angle < 1e-6:
            values, vectors = np.linalg.eigh((relative + relative.T) / 2)
            axis = vectors[:, np.argmax(values)]
        else:
            axis = unit(
                [
                    relative[2, 1] - relative[1, 2],
                    relative[0, 2] - relative[2, 0],
                    relative[1, 0] - relative[0, 1],
                ]
            )
        x, y, z = axis
        cross = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
        rot = (
            np.eye(3)
            + np.sin(t * angle) * cross
            + (1 - np.cos(t * angle)) * cross @ cross
        )
    result = np.eye(4)
    result[:3, :3] = a[:3, :3] @ rot
    result[:3, 3] = (1 - t) * a[:3, 3] + t * b[:3, 3]
    return np.linalg.inv(result)


def pose_bank(kind, scale, circle=None, target=None):
    result = []
    if kind == "pano":
        for yaw in range(0, 360, 45):
            for r in (0.05, 0.1, 0.2):
                view = crop_camera(yaw)[0]
                eye = (
                    scale
                    * r
                    * np.array([np.sin(np.radians(yaw)), 0, np.cos(np.radians(yaw))])
                )
                view[:3, 3] = -view[:3, :3] @ eye
                result.append({"viewmat": view, "r": r, "yaw": yaw})
    elif kind == "orbit":
        for angle in np.linspace(0, 2 * np.pi, 12, endpoint=False):
            for inward in (0.1, 0.2):
                view = look_at(
                    circle_position(circle, angle, scale * (1 - inward)),
                    target,
                    circle["normal"],
                )
                result.append({"viewmat": view, "r": inward, "azimuth": angle})
    else:
        raise ValueError("Unknown scene kind")
    return result


def fill_hole_depth(depth, valid):
    depth = np.asarray(depth, dtype=float)
    valid = np.asarray(valid) & np.isfinite(depth) & (depth > 0)

    def horizontal(d, ok):
        h, w = d.shape
        x = np.broadcast_to(np.arange(w), (h, w))
        left = np.maximum.accumulate(np.where(ok, x, -1), axis=1)
        right = np.minimum.accumulate(np.where(ok, x, w)[:, ::-1], axis=1)[:, ::-1]
        rows = np.arange(h)[:, None]
        return np.maximum(
            np.where(left >= 0, d[rows, left.clip(0, w - 1)], 0),
            np.where(right < w, d[rows, right.clip(0, w - 1)], 0),
        )

    row = horizontal(depth, valid)
    column = horizontal(depth.T, valid.T).T
    filled = np.where(row > 0, row, column)
    return np.where(valid, depth, filled)


def depth_visualisation(depth, alpha):
    good = np.isfinite(depth) & (depth > 0) & (alpha >= 0.6)
    inverse = np.zeros_like(depth, dtype=float)
    np.divide(1, depth, out=inverse, where=good)
    if not good.any():
        return np.zeros_like(depth, dtype=np.uint8)
    low, high = np.percentile(inverse[good], [2, 98])
    normalized = np.clip((inverse - low) / max(high - low, 1e-12), 0, 1)
    if high == low:
        normalized[good] = 1
    return np.where(good, np.rint(normalized * 255), 0).astype(np.uint8)


def psnr(first, second, mask=None):
    error = (np.asarray(first, dtype=float) - second) ** 2
    if mask is not None:
        error = error[np.asarray(mask, dtype=bool)]
    if not error.size:
        return None
    # Finite, JSON-safe exact-match ceiling.
    return float(-10 * np.log10(max(float(error.mean()), 1e-12)))


def _blur(image):
    x = np.arange(-5, 6)
    kernel = np.exp(-x * x / (2 * 1.5**2))
    kernel /= kernel.sum()
    result = np.asarray(image, dtype=float)
    for axis in (0, 1):
        pad = [(0, 0)] * result.ndim
        pad[axis] = (5, 5)
        padded = np.pad(result, pad, mode="edge")
        result = sum(
            k * np.take(padded, np.arange(result.shape[axis]) + i, axis=axis)
            for i, k in enumerate(kernel)
        )
    return result


def ssim(first, second, mask=None):
    a, b = np.asarray(first, dtype=float), np.asarray(second, dtype=float)
    weight = np.ones(a.shape[:2]) if mask is None else np.asarray(mask, dtype=float)
    if not weight.any():
        return None
    if a.ndim == 3:
        weight = weight[..., None]
    norm = _blur(weight).clip(1e-12)
    mean_a, mean_b = _blur(a * weight) / norm, _blur(b * weight) / norm
    var_a = (_blur(a * a * weight) / norm - mean_a**2).clip(0)
    var_b = (_blur(b * b * weight) / norm - mean_b**2).clip(0)
    cov = _blur(a * b * weight) / norm - mean_a * mean_b
    score = (
        (2 * mean_a * mean_b + 0.01**2)
        * (2 * cov + 0.03**2)
        / ((mean_a**2 + mean_b**2 + 0.01**2) * (var_a + var_b + 0.03**2))
    )
    return float((score * weight).sum() / (np.broadcast_to(weight, score.shape).sum()))


def alignment_gate(generated, rendered, alpha):
    h, w = alpha.shape
    h, w = h // 4 * 4, w // 4 * 4
    if not h or not w:
        raise ValueError("Alignment images must be at least 4 by 4")

    def down(a):
        return a[:h, :w].reshape(h // 4, 4, w // 4, 4).mean((1, 3))

    gray = np.array([0.299, 0.587, 0.114])
    # Exclude a block if any of its pixels was outside the trusted region.
    mask = down((alpha >= 0.9).astype(float)) == 1
    score = ssim(down(generated @ gray), down(rendered @ gray), mask)
    return score is not None and score >= 0.50, score


def trust_weight(step, steps):
    if steps <= 0 or not 0 <= step <= steps:
        raise ValueError("Trust schedule requires 0 <= step <= steps and steps > 0")
    return 0.0 if step in (0, steps) else float(0.5 * np.sin(np.pi * step / steps))


def flat_trust_weight(step, steps):
    """Generated-slot ordinal and count; taper over the last 10% to exactly zero."""
    if steps <= 0 or not 0 <= step < steps:
        raise ValueError("Flat trust requires 0 <= step < steps and steps > 0")
    return 0.5 * min(1.0, (steps - 1 - step) / (0.1 * steps))


def depth_warp_flicker(images, depths, views, ks, alphas=None):
    """Forward-splat t+1 into t; nearest z wins collisions; reject occlusion."""
    total, count = 0.0, 0
    for i in range(len(images) - 1):
        h, w = depths[i + 1].shape
        rays = camera_rays(ks[i + 1], w, h)
        points = rays * depths[i + 1][..., None]
        transform = views[i] @ np.linalg.inv(views[i + 1])
        points = points @ transform[:3, :3].T + transform[:3, 3]
        z = points[..., 2]
        good = np.isfinite(points).all(-1) & (z > 0) & (depths[i + 1] > 0)
        if alphas is not None:
            good &= alphas[i + 1] >= 0.6
        projected = points @ ks[i].T
        uv = projected[..., :2] / np.where(good, z, 1)[..., None]
        xy = np.floor(np.where(np.isfinite(uv), uv, -1)).astype(int)
        ht, wt = depths[i].shape
        good &= (
            (xy[..., 0] >= 0)
            & (xy[..., 0] < wt)
            & (xy[..., 1] >= 0)
            & (xy[..., 1] < ht)
        )
        source = np.flatnonzero(good)
        dest = (xy[..., 1] * wt + xy[..., 0]).ravel()[source]
        order = np.argsort(z.ravel()[source], kind="stable")
        _, unique = np.unique(dest[order], return_index=True)
        source, dest = source[order[unique]], dest[order[unique]]
        target_depth = depths[i].ravel()[dest]
        valid = (target_depth > 0) & (
            np.abs(target_depth - z.ravel()[source]) <= 0.01 * target_depth
        )
        if alphas is not None:
            valid &= alphas[i].ravel()[dest] >= 0.6
        error = np.abs(
            images[i].reshape(-1, 3)[dest[valid]]
            - images[i + 1].reshape(-1, 3)[source[valid]]
        )
        total += float(error.sum())
        count += error.size
    return total / count if count else None


def move_path(start, end, count=73):
    """A straight move between two eye-level poses [x, y, z, yaw] in scene units (R4: from a stored view to a
    probe, both ends stills), eased in and out; the yaw turns the short way round. Both ends are exact."""
    start, end = np.asarray(start, dtype=float), np.asarray(end, dtype=float)
    if (
        start.shape != (4,)
        or end.shape != (4,)
        or not np.isfinite([*start, *end]).all()
    ):
        raise ValueError("A move needs two finite [x, y, z, yaw] poses")
    turn = (end[3] - start[3] + 180) % 360 - 180
    result = []
    for t in cosine(np.linspace(0, 1, count)):
        view = crop_camera(start[3] + t * turn)[0]
        view[:3, 3] = -view[:3, :3] @ (start[:3] + t * (end[:3] - start[:3]))
        result.append(view)
    return np.stack(result)


def tour_path(keys, per_segment=37):
    """A fly-through over several eye-level poses [x, y, z, yaw] (R5: through the keyframes of an explored
    place), each leg an eased `move_path`, the joins shared."""
    keys = [np.asarray(k, dtype=float) for k in keys]
    if len(keys) < 2:
        raise ValueError("A tour needs two or more [x, y, z, yaw] poses")
    legs = [move_path(a, b, per_segment) for a, b in pairwise(keys)]
    return np.concatenate([legs[0], *(leg[1:] for leg in legs[1:])])
