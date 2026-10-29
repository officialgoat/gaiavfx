"""Selected-face diagnostics; numpy only, independent of ComfyUI and InsightFace.

A duplicate source performer rendered beside the target is outside this selected-face
measurement. Detecting that failure requires a later person-count check.
"""

import numpy as np


def cosine(a, b):
    if a is None or b is None:
        return None
    a, b = np.asarray(a, dtype=float), np.asarray(b, dtype=float)
    if (
        a.ndim != 1
        or a.shape != b.shape
        or not np.isfinite(a).all()
        or not np.isfinite(b).all()
    ):
        return None
    norm = np.linalg.norm(a) * np.linalg.norm(b)
    return float(np.clip(a @ b / norm, -1, 1)) if norm > 0 else None


def embedding(face):
    if face is None:
        return None
    value = face.get("embedding")
    if cosine(value, value) is None:
        return None
    value = np.asarray(value, dtype=float)
    return value / np.linalg.norm(value)


def face_box(face):
    value = np.asarray(face.get("bbox", ()), dtype=float)
    if (
        value.shape != (4,)
        or not np.isfinite(value).all()
        or np.any(value[2:] <= value[:2])
    ):
        return None
    return value


def choose_face(faces, mask, min_face_height_fraction=0.0):
    """Largest fractional overlap, at least 20% of the face box (not mask area)."""
    mask = np.asarray(mask)
    if mask.ndim != 2:
        raise ValueError("performer mask must be two dimensional")
    best, score = None, 0.0
    h, w = mask.shape
    for face in faces:
        box = face_box(face)
        if (
            box is None
            or embedding(face) is None
            or box[3] - box[1] < h * min_face_height_fraction
        ):
            continue
        x0, y0, x1, y1 = np.rint(box).astype(int)
        x0, y0, x1, y1 = max(0, x0), max(0, y0), min(w, x1), min(h, y1)
        if x1 <= x0 or y1 <= y0:
            continue
        overlap = float(np.mean(mask[y0:y1, x0:x1] > 0.5))
        if overlap > score:
            best, score = face, overlap
    return best if score >= 0.2 else None


def front_face(faces, min_height=0.0):
    usable = [
        f
        for f in faces
        if (box := face_box(f)) is not None
        and embedding(f) is not None
        and box[3] - box[1] >= min_height
    ]
    return (
        max(usable, key=lambda f: (f.get("det_score", 0), f["bbox"][2] - f["bbox"][0]))
        if usable
        else None
    )


def mouth_aperture(landmarks, bbox):
    """Inner-lip centre gap (106-point indices 62, 60), divided by face height.

    Index layout: insightface/alignment/coordinate_reg/README.md, 2d106 markup.
    Euclidean gap remains valid when the face is rolled in the image plane.
    """
    if landmarks is None or bbox is None:
        return None
    points, box = np.asarray(landmarks, dtype=float), np.asarray(bbox, dtype=float)
    if (
        points.shape != (106, 2)
        or box.shape != (4,)
        or not np.isfinite(points).all()
        or not np.isfinite(box).all()
    ):
        return None
    height = box[3] - box[1]
    return (
        float(np.linalg.norm(points[62] - points[60]) / height) if height > 0 else None
    )


def summary(values):
    values = [v for v in values if v is not None and np.isfinite(v)]
    if not values:
        return {"frames": 0, "median": None, "p25": None, "p75": None}
    # Match statistics.quantiles' exclusive quartiles used in the research metric.
    ordered = np.sort(values)

    def quantile(p):
        position = (len(ordered) + 1) * p - 1
        lo = int(np.clip(np.floor(position), 0, len(ordered) - 2))
        return (
            float(ordered[lo] + (position - lo) * (ordered[lo + 1] - ordered[lo]))
            if len(ordered) > 1
            else float(ordered[0])
        )

    return {
        "frames": len(values),
        "median": float(np.median(values)),
        "p25": quantile(0.25),
        "p75": quantile(0.75),
    }


def mouth_agreement(candidate, source):
    if len(candidate) != len(source):
        raise ValueError("mouth series must be aligned")
    pairs = [
        (a, b)
        for a, b in zip(candidate, source)
        if a is not None and b is not None and np.isfinite(a) and np.isfinite(b)
    ]
    r = None
    if len(pairs) < max(8, len(candidate) / 2):
        return {
            "pearson_r": None,
            "max_ratio": None,
            "paired_frames": len(pairs),
            "reason": "too few paired mouth frames",
        }
    a, b = np.asarray(pairs).T
    if np.ptp(a) > 0 and np.ptp(b) > 0:
        r = float(np.clip(np.corrcoef(a, b)[0, 1], -1, 1))
    ratios = [a / max(b, 0.002) for a, b in pairs]
    return {
        "pearson_r": r,
        "max_ratio": float(max(ratios)) if ratios else None,
        "paired_frames": len(pairs),
        "reason": "" if r is not None else "constant mouth aperture series",
    }


def measure(
    candidate_faces,
    source_faces,
    target_faces,
    masks,
    *,
    min_face_height_fraction=0.0,
    min_face_frames_fraction=0.0,
    target_frame_height=None,
):
    if len(candidate_faces) != len(source_faces) or len(masks) != len(source_faces):
        raise ValueError("candidate, source and masks must have equal frame counts")
    selected_source = [
        choose_face(faces, mask, min_face_height_fraction)
        for faces, mask in zip(source_faces, masks)
    ]
    selected_candidate = [
        choose_face(faces, mask, min_face_height_fraction)
        for faces, mask in zip(candidate_faces, masks)
    ]
    vectors = [embedding(f) for f in selected_source if f is not None]
    source = np.mean(vectors, axis=0) if vectors else None
    target = embedding(
        front_face(target_faces, (target_frame_height or 0) * min_face_height_fraction)
    )

    def frame(face):
        if face is None:
            return None
        return {
            "bbox": face_box(face).tolist(),
            "to_target": cosine(embedding(face), target),
            "to_source": cosine(embedding(face), source),
            "mouth_aperture": mouth_aperture(face.get("landmark_2d_106"), face["bbox"]),
        }

    rows = [
        {"candidate": frame(c), "source": frame(s)}
        for c, s in zip(selected_candidate, selected_source)
    ]

    def series(side, key):
        return [None if row[side] is None else row[side][key] for row in rows]

    summaries = {
        side: {
            "frames_with_face": sum(row[side] is not None for row in rows),
            **{
                key: summary(series(side, key))
                for key in ("to_target", "to_source", "mouth_aperture")
            },
        }
        for side in ("candidate", "source")
    }
    counts = [summaries[side]["frames_with_face"] for side in ("candidate", "source")]
    reason = (
        "no usable face"
        if not min(counts)
        else "too few usable face frames"
        if min(counts) < len(rows) * min_face_frames_fraction
        else ""
    )
    return {
        "schema": 1,
        "reason": reason,
        "frames": rows,
        "summary": summaries,
        "mouth": mouth_agreement(
            series("candidate", "mouth_aperture"), series("source", "mouth_aperture")
        ),
    }
