"""Pure pixel-edge crop geometry ported from the research person-crop pilot."""

import math

EPS = 1e-6


def to_native(box, fit, offset, native):
    """Map a letterbox pixel-edge box to native pixel edges."""
    sx, sy = fit[0] / native[0], fit[1] / native[1]
    return (
        (box[0] - offset[0]) / sx,
        (box[1] - offset[1]) / sy,
        (box[2] - offset[0]) / sx,
        (box[3] - offset[1]) / sy,
    )


def to_letterbox(box, fit, offset, native):
    sx, sy = fit[0] / native[0], fit[1] / native[1]
    return (
        box[0] * sx + offset[0],
        box[1] * sy + offset[1],
        box[2] * sx + offset[0],
        box[3] * sy + offset[1],
    )


def grow(box, pad, frame):
    return (
        max(0.0, box[0] - pad),
        max(0.0, box[1] - pad),
        min(float(frame[0]), box[2] + pad),
        min(float(frame[1]), box[3] + pad),
    )


def union(boxes):
    boxes = list(boxes)
    return (
        min(b[0] for b in boxes),
        min(b[1] for b in boxes),
        max(b[2] for b in boxes),
        max(b[3] for b in boxes),
    )


def canvas_candidates(budget, step, low, high, aspect):
    """Canvases on the supplied grid within the supplied area and aspect limits."""
    if type(step) is not int or step < 1 or budget <= 0 or low <= 0 or high < low:
        raise ValueError("invalid canvas limits")
    start = math.ceil(low / step) * step
    return [
        (w, h)
        for w in range(start, high + 1, step)
        for h in range(start, high + 1, step)
        if w * h <= budget and aspect[0] <= w / h <= aspect[1]
    ]


def limits(required, avoid, frame):
    """Keep every avoided region outside the crop; it must lie beside the subject."""
    lo_x, lo_y, hi_x, hi_y = 0.0, 0.0, float(frame[0]), float(frame[1])
    for name, box in avoid:
        if box[2] <= required[0]:
            lo_x = max(lo_x, box[2])
        elif box[0] >= required[2]:
            hi_x = min(hi_x, box[0])
        elif box[3] <= required[1]:
            lo_y = max(lo_y, box[3])
        elif box[1] >= required[3]:
            hi_y = min(hi_y, box[1])
        else:
            raise ValueError(f"Avoided region overlaps the required subject: {name}")
    return lo_x, lo_y, hi_x, hi_y


def _sizes(r0, r1, full, margin):
    """Lower bounds of every placement family on one axis."""
    return {full, r1 + margin, full - (r0 - margin), r1 - r0 + 2 * margin}


def _placements(size, full, lo, hi, r0, r1, margin):
    """Valid spans for one axis. Interior edges keep the margin; frame edges need none."""
    starts = {0.0, full - size}
    a, b = max(lo, r1 + margin - size), min(hi - size, r0 - margin)
    if a <= b + EPS:
        starts.add(min(max((r0 + r1 - size) / 2, a), b))
    found = []
    for x0 in sorted(starts):
        x1 = x0 + size
        if x0 < lo - EPS or x1 > hi + EPS or x0 < -EPS or x1 > full + EPS:
            continue
        low_edge, high_edge = abs(x0) < EPS, abs(x1 - full) < EPS
        if not low_edge and x0 > r0 - margin + EPS:
            continue
        if not high_edge and x1 < r1 + margin - EPS:
            continue
        clearances = ([] if low_edge else [r0 - x0]) + ([] if high_edge else [x1 - r1])
        found.append(
            (
                min(clearances, default=math.inf),
                0.0 if low_edge else x0,
                float(full) if high_edge else x1,
                not low_edge,
                not high_edge,
            )
        )
    return found


def fit(required, canvas, frame, margin, bounds):
    """Smallest exact-aspect box inside the frame and bounds; None if impossible."""
    w, h = canvas
    aspect = w / h
    xs = _sizes(required[0], required[2], frame[0], margin)
    ys = _sizes(required[1], required[3], frame[1], margin)
    for bh in sorted({*ys, *(s / aspect for s in xs)}):
        bw = bh * aspect
        if bh <= 0 or bw > frame[0] + EPS or bh > frame[1] + EPS:
            continue
        px = _placements(bw, frame[0], bounds[0], bounds[2], required[0], required[2], margin)
        py = _placements(bh, frame[1], bounds[1], bounds[3], required[1], required[3], margin)
        if px and py:
            # Prefer spans that end on the frame border (fewer seams), then clearance.
            cx = max(px, key=lambda p: (-(p[3] + p[4]), p[0]))
            cy = max(py, key=lambda p: (-(p[3] + p[4]), p[0]))
            return {
                "canvas": [w, h],
                "box": [cx[1], cy[1], cx[2], cy[2]],
                "scale": w / bw,
                "interior_edges": {
                    "left": cx[3],
                    "top": cy[3],
                    "right": cx[4],
                    "bottom": cy[4],
                },
                "clearance": min(cx[0], cy[0]),
            }
    return None


def choose(required, frame, margin, bounds, candidates):
    """Largest subject scale wins; a tie goes to the cheaper canvas."""
    rows, best = [], None
    for canvas in candidates:
        result = fit(required, canvas, frame, margin, bounds)
        if result is None:
            rows.append(
                {
                    "canvas": list(canvas),
                    "scale": None,
                    "reason": "no exact-aspect box inside the frame and avoided regions "
                    "keeps the required content with interior margins",
                }
            )
            continue
        rows.append({"canvas": list(canvas), "scale": result["scale"], "reason": None})
        key = (round(result["scale"], 9), -canvas[0] * canvas[1])
        if best is None or key > best[0]:
            best = (key, result)
    if best is None:
        raise ValueError("No supported canvas fits the required subject region")
    chosen = best[1]
    for row in rows:
        if row["reason"] is None and row["canvas"] != chosen["canvas"]:
            row["reason"] = "smaller subject scale or larger area than the chosen canvas"
    return chosen, rows


def forward_matrix(box, scale):
    """Native -> canvas with pixel centres: u + 0.5 = s * (x + 0.5 - x0)."""
    return [
        [scale, 0.0, scale * (0.5 - box[0]) - 0.5],
        [0.0, scale, scale * (0.5 - box[1]) - 0.5],
    ]


def to_canvas(point, box, scale):
    return (
        scale * (point[0] + 0.5 - box[0]) - 0.5,
        scale * (point[1] + 0.5 - box[1]) - 0.5,
    )


def to_source(point, box, scale):
    return (
        (point[0] + 0.5) / scale - 0.5 + box[0],
        (point[1] + 0.5) / scale - 0.5 + box[1],
    )


def ramp(distance, feather):
    """Scalar cosine feather; pixel arrays belong to the application layer."""
    if feather <= 0:
        raise ValueError("feather must be positive")
    t = min(max(distance / feather, 0.0), 1.0)
    return 0.5 - 0.5 * math.cos(math.pi * t)


def core_box(box, edges, feather):
    return tuple(
        value + (feather if i < 2 else -feather) * edges[side]
        for i, (side, value) in enumerate(zip(("left", "top", "right", "bottom"), box))
    )


def interior_margins(required, box, edges, feather):
    core = core_box(box, edges, feather)
    return {
        side: (required[i] - core[i]) * (1 if i < 2 else -1)
        for i, side in enumerate(("left", "top", "right", "bottom"))
        if edges[side]
    }
