"""Streaming film shots and deterministic face-track/cast evidence."""

import json
from collections import deque
from contextlib import closing
from fractions import Fraction
from math import ceil, gcd

import numpy as np
from PIL import Image

from gaiavfx.application.replacement_steps import detect_letterbox
from gaiavfx.shared.errors import ApplicationError


def hsv_channels(rgb):
    """OpenCV-compatible 8-bit HSV (H: 0..179); integer lookup-table conversion."""
    rgb = rgb.astype(np.int32)
    r, g, b = np.moveaxis(rgb, -1, 0)
    v, low = rgb.max(axis=-1), rgb.min(axis=-1)
    diff = v - low
    sdiv, hdiv = np.zeros(256, np.int32), np.zeros(256, np.int32)
    values = np.arange(1, 256)
    sdiv[1:] = np.rint((255 << 12) / values).astype(np.int32)
    hdiv[1:] = np.rint((180 << 12) / (6 * values)).astype(np.int32)
    s = (diff * sdiv[v] + (1 << 11)) >> 12
    h = np.where(v == r, g - b, np.where(v == g, b - r + 2 * diff, r - g + 4 * diff))
    h = (h * hdiv[diff] + (1 << 11)) >> 12
    h = np.where(h < 0, h + 180, h)
    return np.stack((h, s, v), axis=-1)


def detect_shots(frames, detector):
    """AdaptiveDetector rolling neighbourhood; only 2w+1 scores and two frames live."""
    window = detector["window_width"]
    scores = deque(maxlen=2 * window + 1)
    previous, count, last_cut = None, 0, 0
    cuts = [0]
    for index, rgb in frames:
        if index != count:
            raise ValueError("Shot detection requires contiguous frames starting at zero")
        hsv = hsv_channels(rgb)
        score = 0.0 if previous is None else float(np.abs(hsv - previous).mean())
        previous, count = hsv, count + 1
        scores.append((index, score))
        if len(scores) < scores.maxlen:
            continue
        target, value = scores[window]
        average = sum(v for i, v in scores if i != target) / (2 * window)
        ratio = (
            min(value / average, 255.0)
            if average >= 0.00001
            else (255.0 if value >= detector["min_content_val"] else 0.0)
        )
        if (
            ratio >= detector["adaptive_threshold"]
            and value >= detector["min_content_val"]
            and target + window - last_cut >= detector["min_scene_len"]
        ):
            cuts.append(target)
            last_cut = target
    if not count:
        raise ValueError("The film contains no decoded frames")
    return [
        {"index": i, "start": a, "end": b, "poster_artifact": None}
        for i, (a, b) in enumerate(zip(cuts, [*cuts[1:], count], strict=True))
    ]


def scan_frames(shots, stride):
    count = shots[-1]["end"]
    return sorted(
        set(range(0, count, stride)) | {v for s in shots for v in (s["start"], s["end"] - 1)}
    )


def json_bytes(value):
    return (
        json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False) + "\n"
    ).encode()


def _artifact(context, path):
    prepared = context.library.prepare_blob(path, "visual_evidence", {})
    with context.library.database.transaction() as session:
        return context.library.register_artifact(session, prepared)["id"]


def film_shots(context):
    spec = context.snapshot["step_spec"]
    source, media = context.paths[spec["source_asset"]], context.library.media
    info = media.film_info(source)
    width, height = info["width"], info["height"]
    detector = spec["params"]["detector"]
    scale = detector["downscale"]
    frames = media.stream_frames(
        source, width=max(1, width // scale), height=max(1, height // scale)
    )
    sampled = None

    def sample_frames():
        nonlocal sampled
        for index, pixels in frames:
            if index % max(1, round(float(Fraction(info["frame_rate"])))) == 0:
                sampled = pixels.copy() if sampled is None else np.maximum(sampled, pixels)
            yield index, pixels

    try:
        shots = detect_shots(sample_frames(), detector)
    finally:
        frames.close()
    context.staging.mkdir(parents=True, exist_ok=True)
    outputs = []
    posters = {(s["start"] + s["end"]) // 2: s for s in shots}
    # Posters never cross the pipe at native size; bar rows came from the shot pass.
    poster_width = min(480, width)
    poster_height = max(1, round(height * poster_width / width))
    with closing(
        media.stream_frames(source, width=poster_width, height=poster_height, end=max(posters) + 1)
    ) as frames:
        for index, pixels in frames:
            if index not in posters:
                continue
            shot = posters[index]
            path = context.staging / f"shot-{shot['index']:05d}.png"
            image = Image.fromarray(pixels)
            image.thumbnail((480, max(1, height)), Image.Resampling.LANCZOS)
            image.save(path)
            shot["poster_artifact"] = _artifact(context, path)
            outputs.append((f"poster:{shot['index']}", path, "visual_evidence"))
    if len(outputs) != len(shots):
        raise ValueError("Film ended before all shot posters could be decoded")
    bars = detect_letterbox(sampled[None])
    top = bars.offset[1] * scale if bars else 0
    bottom = (sampled.shape[0] - bars.offset[1] - bars.fit[1]) * scale if bars else 0
    fps = Fraction(info["frame_rate"])
    report = {
        "schema": 1,
        "source_asset": spec["source_asset"],
        "fps": [fps.numerator, fps.denominator],
        "frame_count": shots[-1]["end"],
        "width": width,
        "height": height,
        "letterbox": [top, bottom] if top or bottom else None,
        "shots": shots,
        "params": spec["params"],
    }
    path = context.staging / "shots.json"
    path.write_bytes(json_bytes(report))
    return [("shots", path, "analysis_report"), *outputs]


def validate_faces(faces, frame_count):
    types = {
        "frame": np.int32,
        "box": np.float32,
        "score": np.float32,
        "kps": np.float32,
        "embedding": np.float16,
    }
    if set(faces) != set(types):
        raise ValueError("Invalid faces.npz arrays")
    n = len(faces["frame"])
    shapes = {"frame": (n,), "box": (n, 4), "score": (n,), "kps": (n, 5, 2), "embedding": (n, 512)}
    for key, dtype in types.items():
        if (
            faces[key].dtype != dtype
            or faces[key].shape != shapes[key]
            or not np.isfinite(faces[key]).all()
        ):
            raise ValueError(f"Invalid face {key}")
    if n and (
        faces["frame"].min() < 0
        or faces["frame"].max() >= frame_count
        or np.any(np.diff(faces["frame"].astype(np.int64)) < 0)
        or np.any(faces["box"][:, 2:] <= faces["box"][:, :2])
        or np.any((faces["score"] < 0) | (faces["score"] > 1))
        or np.any(np.abs(np.linalg.norm(faces["embedding"].astype(np.float64), axis=1) - 1) > 0.002)
    ):
        raise ValueError("Invalid face geometry, frame index, score or embedding norm")


def _iou(a, b):
    overlap = np.maximum(0, np.minimum(a[2:], b[2:]) - np.maximum(a[:2], b[:2])).prod()
    return float(overlap / ((a[2:] - a[:2]).prod() + (b[2:] - b[:2]).prod() - overlap))


def _union(spans):
    result = []
    for start, end in sorted(spans):
        if result and start <= result[-1][1]:
            result[-1][1] = max(end, result[-1][1])
        else:
            result.append([start, end])
    return result


def cluster_faces(faces, shots, params):
    validate_faces(faces, shots[-1]["end"])
    embeddings = faces["embedding"].astype(np.float64)
    norms = np.linalg.norm(embeddings, axis=1, keepdims=True)
    embeddings = embeddings / norms
    boxes = faces["box"].astype(np.float64)
    quality = faces["score"].astype(np.float64) * np.sqrt(
        (boxes[:, 2:] - boxes[:, :2]).prod(axis=1)
    )
    tracks, vectors = [], []
    for shot in shots:
        local = []
        rows = np.flatnonzero((faces["frame"] >= shot["start"]) & (faces["frame"] < shot["end"]))
        for row in rows:
            frame = int(faces["frame"][row])
            matches = []
            for track in local:
                last = track["faces"][-1]
                if track["frames"][-1] == frame:
                    continue
                overlap = _iou(boxes[row], boxes[last])
                if overlap >= 0.3 and float(embeddings[row] @ embeddings[last]) >= 0.5:
                    matches.append((overlap, -int(track["id"][1:]), track))
            if matches:
                track = max(matches, key=lambda m: m[:2])[2]
            else:
                track = {
                    "id": f"t{len(tracks) + 1:04d}",
                    "shot": shot["index"],
                    "frames": [],
                    "faces": [],
                    "boxes": [],
                    "face_count": 0,
                    "quality": 0.0,
                    "crop_artifact": None,
                }
                tracks.append(track)
                local.append(track)
            track["faces"].append(int(row))
            track["frames"].append(frame)
            track["boxes"].append(boxes[row].tolist())
    eligible, unknown = [], []
    for i, track in enumerate(tracks):
        rows = track["faces"]
        best = sorted(rows, key=lambda row: (-quality[row], row))[:5]
        vector = embeddings[best].mean(axis=0)
        norm = np.linalg.norm(vector)
        vectors.append(vector / norm if norm else vector)
        track["face_count"] = len(rows)
        track["quality"] = float(quality[rows].mean())
        heights = boxes[rows, 3] - boxes[rows, 1]
        if len(rows) < params["min_faces"] or float(np.median(heights)) < params["min_face_px"]:
            unknown.append(track["id"])
        else:
            eligible.append([i])
    # Size-weighted average linkage, with symmetric float64 input and cached row maxima.
    eligible_vectors = np.asarray([vectors[g[0]] for g in eligible], dtype=np.float64)
    similarities = eligible_vectors @ eligible_vectors.T if eligible else np.empty((0, 0))
    similarities = (similarities + similarities.T) / 2
    groups = agglomerate(similarities, eligible, params["tau"])
    clusters = []
    for group in groups:
        if not group:
            continue
        presence = _union((tracks[i]["frames"][0], tracks[i]["frames"][-1] + 1) for i in group)
        clusters.append(
            {
                "id": "",
                "track_ids": [tracks[i]["id"] for i in group],
                "screen_time_frames": sum(b - a for a, b in presence),
                "shot_count": len({tracks[i]["shot"] for i in group}),
                "presence": presence,
                "face_artifact": None,
            }
        )
    clusters.sort(key=lambda c: (-c["screen_time_frames"], c["presence"][0][0], c["track_ids"]))
    for i, cluster in enumerate(clusters):
        cluster["id"] = f"c{i + 1:02d}"
    track_index = {t["id"]: i for i, t in enumerate(tracks)}
    eligible_index = {group[0]: i for i, group in enumerate(eligible)}
    members = [[eligible_index[track_index[t]] for t in c["track_ids"]] for c in clusters]
    owners = np.empty(len(eligible), dtype=np.int32)
    for cluster, group in enumerate(members):
        owners[group] = cluster
    maxima = {}
    for i in range(len(eligible)):
        matches = np.flatnonzero((owners != owners[i]) & (similarities[i] >= params["suggest_min"]))
        for j in matches[matches > i]:
            pair = tuple(sorted((int(owners[i]), int(owners[j]))))
            maxima[pair] = max(maxima.get(pair, -1.0), float(similarities[i, j]))
    suggestions = [
        {"a": clusters[a]["id"], "b": clusters[b]["id"], "max_similarity": value}
        for (a, b), value in sorted(maxima.items())
    ]
    return {
        "tracks": tracks,
        "clusters": clusters,
        "unknown_track_ids": unknown,
        "suggestions": suggestions,
    }


def agglomerate(similarities, eligible, tau):
    """Cache best partners. Ties follow the same row-major (earliest pair) order."""
    groups = [list(g) for g in eligible]
    if len(groups) < 2:
        return groups
    matrix = similarities.copy()
    np.fill_diagonal(matrix, -np.inf)
    n = len(groups)
    active = np.ones(n, dtype=bool)
    partner = matrix.argmax(axis=1)
    best = matrix[np.arange(n), partner]
    for _ in range(n - 1):
        i = int(best.argmax())
        j = int(partner[i])
        if best[i] < tau:
            break
        # Symmetry guarantees i < j for a global maximum, including exact ties.
        merged = (matrix[i] * len(groups[i]) + matrix[j] * len(groups[j])) / (
            len(groups[i]) + len(groups[j])
        )
        matrix[i] = merged
        matrix[:, i] = merged
        matrix[j, :] = matrix[:, j] = -np.inf
        matrix[i, i] = -np.inf
        active[j] = False
        groups[i] = sorted(groups[i] + groups[j])
        groups[j] = []
        # Only changed columns can displace a cached partner. Invalidated winners rescan their row.
        invalid = active & ((partner == j) | ((partner == i) & (matrix[:, i] < best)))
        invalid[i] = True
        rows = np.flatnonzero(invalid)
        partner[rows] = matrix[rows].argmax(axis=1)
        best[rows] = matrix[rows, partner[rows]]
        other = active & ~invalid
        better = other & ((matrix[:, i] > best) | ((matrix[:, i] == best) & (i < partner)))
        partner[better] = i
        best[better] = matrix[better, i]
        best[j] = -np.inf
    return groups


def proxy_size(width, height):
    if min(width, height) < 2:
        raise ApplicationError("Face proxy needs at least two pixels on each axis.", 422)
    ratio = min(1, 1280 / width)
    return max(2, int(width * ratio) // 2 * 2), max(2, int(height * ratio) // 2 * 2)


def film_proxy(context):
    spec = context.snapshot["step_spec"]
    frames = spec["frames"]
    film = spec["film"]
    width, height = proxy_size(film["width"], film["height"])
    context.staging.mkdir(parents=True, exist_ok=True)
    proxy = context.staging / "proxy.mp4"
    mapping = context.staging / "map.json"
    document = {
        "frames": {str(i): frame for i, frame in enumerate(frames)},
        "scale": [width / film["width"], height / film["height"]],
        "width": width,
        "height": height,
    }
    # Completed chunks survive shutdown; the sidecar is written only after encode/size validation.
    if not (mapping.is_file() and proxy.is_file() and json.loads(mapping.read_text()) == document):
        mapping.unlink(missing_ok=True)
        context.library.media.sampled_proxy(
            context.paths[spec["source_asset"]],
            proxy,
            frames,
            width=width,
            height=height,
            fps=Fraction(*film["fps"]),
        )
    if proxy.stat().st_size >= spec["params"]["max_upload_bytes"]:
        raise ApplicationError(
            "Face proxy exceeds max_upload_bytes; reduce scan_chunk_frames.", 422
        )
    temporary = mapping.with_suffix(".writing.json")
    temporary.write_bytes(json_bytes(document))
    temporary.replace(mapping)
    return [("proxy", proxy, "analysis_report"), ("map", mapping, "analysis_report")]


def merge_faces(chunks, frame_count):
    collected = []
    for data, mapping in chunks:
        count = len(mapping["frames"])
        validate_faces(data, count)
        indices = np.array([mapping["frames"][str(i)] for i in range(count)], dtype=np.int32)
        if np.any(np.diff(indices.astype(np.int64)) <= 0):
            raise ValueError("Proxy frame map must be strictly increasing")
        sx, sy = mapping["scale"]
        if not (0 < sx <= 1 and 0 < sy <= 1):
            raise ValueError("Invalid proxy scale")
        native = {key: value.copy() for key, value in data.items()}
        native["frame"] = indices[data["frame"]]
        native["box"] /= np.array([sx, sy, sx, sy], dtype=np.float32)
        native["kps"] /= np.array([sx, sy], dtype=np.float32)
        collected.append(native)
    result = {key: np.concatenate([data[key] for data in collected]) for key in collected[0]}
    order = np.argsort(result["frame"], kind="stable")
    result = {key: value[order] for key, value in result.items()}
    validate_faces(result, frame_count)
    return result


def square_crop(image, box, size):
    """Pad outside the picture with black; preserve the face's aspect ratio."""
    x0, y0, x1, y1 = map(float, box)
    side = max(1, int(np.ceil(1.8 * max(x1 - x0, y1 - y0))))
    left = int(np.floor((x0 + x1 - side) / 2))
    top = int(np.floor((y0 + y1 - side) / 2))
    return image.crop((left, top, left + side, top + side)).resize(
        (size, size), Image.Resampling.LANCZOS
    )


def crop_stream_size(width, height, boxes):
    """Smallest integer canvas at the exact source aspect retaining 256px crop squares."""
    sides = 1.8 * (boxes[:, 2:] - boxes[:, :2]).max(axis=1)
    ratio = min(1.0, 256 / float(sides.min()))
    divisor = gcd(width, height)
    units = max(1, ceil(ratio * divisor))
    return width // divisor * units, height // divisor * units


def film_cast(context):
    spec = context.snapshot["step_spec"]
    shots = json.loads(context.paths[spec["shots_artifact"]].read_text("utf-8"))
    chunks = []
    for chunk in spec["chunks"]:
        with np.load(context.paths[chunk["faces"]], allow_pickle=False) as data:
            arrays = {key: data[key] for key in data.files}
        chunks.append((arrays, json.loads(context.paths[chunk["map"]].read_text("utf-8"))))
    faces = merge_faces(chunks, shots["frame_count"])
    report = cluster_faces(faces, shots["shots"], spec["params"])
    report["params"] = {
        **spec["params"],
        "detection": {
            "source": "sampled_proxy",
            "codec": "h264",
            "crf": 18,
            "pixel_format": "yuv420p",
            "width": chunks[0][1]["width"],
            "height": chunks[0][1]["height"],
            "scale": chunks[0][1]["scale"],
            "chunks": len(chunks),
        },
    }
    context.staging.mkdir(parents=True, exist_ok=True)
    merged = context.staging / "faces.npz"
    np.savez_compressed(merged, **faces)
    quality = faces["score"].astype(np.float64) * np.sqrt(
        (faces["box"][:, 2:] - faces["box"][:, :2]).astype(np.float64).prod(axis=1)
    )
    by_id = {t["id"]: t for t in report["tracks"]}
    requests = [(t, "crop_artifact", t["faces"], 128) for t in report["tracks"]]
    requests += [
        (c, "face_artifact", [row for tid in c["track_ids"] for row in by_id[tid]["faces"]], 256)
        for c in report["clusters"]
    ]
    by_frame = {}
    for item, key, rows, size in requests:
        row = min(rows, key=lambda r: (-quality[r], r))
        by_frame.setdefault(int(faces["frame"][row]), []).append((item, key, row, size))
    outputs = [("faces", merged, "analysis_report")]
    if by_frame:
        rows = [row for requests in by_frame.values() for _, _, row, _ in requests]
        width, height = crop_stream_size(shots["width"], shots["height"], faces["box"][rows])
        scale = width / shots["width"]
        # One pass serves every crop; small faces keep native pixels, large faces allow reduction.
        with closing(
            context.library.media.stream_frames(
                context.paths[spec["source_asset"]],
                width=width,
                height=height,
                end=max(by_frame) + 1,
            )
        ) as stream:
            completed = 0
            for frame, pixels in stream:
                if frame not in by_frame:
                    continue
                image = Image.fromarray(pixels)
                for item, key, row, size in by_frame[frame]:
                    path = context.staging / f"{item['id']}.png"
                    square_crop(image, faces["box"][row] * scale, size).save(path)
                    item[key] = _artifact(context, path)
                    outputs.append((item["id"], path, "visual_evidence"))
                completed += 1
            if completed != len(by_frame):
                raise ValueError("Film ended before all face crops could be decoded")
    path = context.staging / "cast.json"
    path.write_bytes(json_bytes(report))
    return [("cast", path, "analysis_report"), *outputs]
