"""Deterministic identity linking, average linkage, and unknown tracks."""

import time

import numpy as np
import pytest
from PIL import Image

from gaiavfx.application.recast_steps import (
    agglomerate,
    cluster_faces,
    crop_stream_size,
    json_bytes,
    square_crop,
)


def fixture_faces():
    a, b, look = np.eye(512)[:3]
    look = 0.4 * a + np.sqrt(0.84) * look
    rows = []
    for frame in [0, 4, 8, 12, 16, 20, 24, 28]:
        rows.append((frame, [10, 10, 60, 70], a))
        if frame < 12:
            rows.append((frame, [100, 10, 150, 70], b))
        if frame >= 20:
            rows.append((frame, [200, 10, 250, 70], look))
    rows += [(0, [300, 10, 310, 20], b), (4, [300, 10, 310, 20], b)]
    rows += [(0, [400, 10, 450, 70], b)]
    rows.sort(key=lambda r: r[0])
    n = len(rows)
    return {
        "frame": np.array([r[0] for r in rows], np.int32),
        "box": np.array([r[1] for r in rows], np.float32),
        "score": np.full(n, 0.9, np.float32),
        "kps": np.zeros((n, 5, 2), np.float32),
        "embedding": np.array([r[2] for r in rows], np.float16),
    }


SHOTS = [
    {"index": i, "start": a, "end": b} for i, (a, b) in enumerate([(0, 12), (12, 20), (20, 32)])
]
PARAMS = {"tau": 0.45, "min_faces": 2, "min_face_px": 40, "suggest_min": 0.5}


def test_cast_is_byte_identical_unknown_rules_and_screen_time():
    faces = fixture_faces()
    report = cluster_faces(faces, SHOTS, PARAMS)
    assert json_bytes(report) == json_bytes(cluster_faces(faces, SHOTS, PARAMS))
    assert len(report["clusters"]) == 3  # two people plus the lookalike
    assert report["unknown_track_ids"] == ["t0003", "t0004"]
    assert report["clusters"][0] == {
        "id": "c01",
        "track_ids": ["t0001", "t0005", "t0006"],
        "screen_time_frames": 23,
        "shot_count": 3,
        "presence": [[0, 9], [12, 17], [20, 29]],
        "face_artifact": None,
    }
    assert report["clusters"][1]["presence"] == [[0, 9]]  # equal screen time, first frame wins
    assert report["clusters"][2]["presence"] == [[20, 29]]
    assert report["tracks"][0]["quality"] == pytest.approx(0.9 * np.sqrt(3000))
    for track in report["tracks"]:
        assert len(track["frames"]) == len(set(track["frames"]))
        assert track["boxes"] == faces["box"][track["faces"]].tolist()
        assert track["frames"] == faces["frame"][track["faces"]].tolist()


def test_iou_and_identity_both_required_and_no_same_frame_reuse():
    faces = fixture_faces()
    faces["embedding"][[2, 6]] = faces["embedding"][0]
    report = cluster_faces(faces, SHOTS, PARAMS)
    assert len(report["tracks"]) == 7
    faces["embedding"][5] = np.eye(512, dtype=np.float16)[5]
    changed = cluster_faces(faces, SHOTS, PARAMS)
    assert len(changed["tracks"]) > len(report["tracks"])


def test_empty_faces_and_invalid_embeddings():
    faces = fixture_faces()
    empty = {k: v[:0] for k, v in faces.items()}
    assert cluster_faces(empty, SHOTS, PARAMS) == {
        "tracks": [],
        "clusters": [],
        "unknown_track_ids": [],
        "suggestions": [],
    }
    faces["embedding"][0] = 0
    with pytest.raises(ValueError, match="norm"):
        cluster_faces(faces, SHOTS, PARAMS)


def test_average_linkage_does_not_chain_similar_neighbors():
    # AB = BC = .6, AC = -.28: after AB merges, mean similarity to C is .16.
    vectors = np.zeros((6, 512), np.float16)
    vectors[:2, :2] = [0.6, -0.8]
    vectors[2:4, :2] = [1, 0]
    vectors[4:, :2] = [0.6, 0.8]
    faces = {
        "frame": np.array([0, 4, 12, 16, 20, 24], np.int32),
        "box": np.tile(np.array([0, 0, 60, 80], np.float32), (6, 1)),
        "score": np.ones(6, np.float32),
        "kps": np.zeros((6, 5, 2), np.float32),
        "embedding": vectors,
    }
    result = cluster_faces(faces, SHOTS, PARAMS)
    assert [c["track_ids"] for c in result["clusters"]] == [["t0001", "t0002"], ["t0003"]]


def test_track_embedding_uses_only_five_best_faces_and_presence_is_union():
    faces = fixture_faces()
    faces = {k: v[:8].copy() for k, v in faces.items()}
    faces["frame"] = np.array([0, 1, 2, 3, 4, 5, 12, 16], np.int32)
    faces["box"][:] = [0, 0, 60, 80]
    faces["embedding"][:] = np.eye(512, dtype=np.float16)[0]
    faces["embedding"][5, :2] = [0.6, 0.8]
    faces["score"][5] = 0.1
    result = cluster_faces(faces, SHOTS, {**PARAMS, "tau": 0.999})
    assert result["clusters"][0]["track_ids"] == ["t0001", "t0002"]
    # Two simultaneous tracks of one identity must not double their screen time.
    faces["frame"] = np.array([0, 0, 4, 4, 8, 8, 12, 16], np.int32)
    faces["embedding"][:] = np.eye(512, dtype=np.float16)[0]
    result = cluster_faces(faces, SHOTS, PARAMS)
    assert result["clusters"][0]["screen_time_frames"] == 14
    assert result["clusters"][0]["presence"] == [[0, 9], [12, 17]]


def test_square_crop_preserves_aspect_and_pads_black():
    pixels = np.zeros((100, 100, 3), np.uint8)
    pixels[0:60, 0:20] = 255
    image = square_crop(Image.fromarray(pixels), [0, 0, 20, 60], 216)
    assert image.size == (216, 216)
    mask = np.asarray(image).min(axis=2) > 128
    ys, xs = np.nonzero(mask)
    assert (ys.max() - ys.min() + 1) / (xs.max() - xs.min() + 1) == pytest.approx(3, abs=0.1)
    assert np.asarray(image)[0, 0].tolist() == [0, 0, 0]


def test_crop_pass_reduces_only_when_256_pixel_squares_survive():
    large = np.array([[0, 0, 400, 600]], np.float32)
    width, height = crop_stream_size(1920, 1080, large)
    assert width < 1920 and width * 1080 == height * 1920
    assert 1.8 * 600 * width / 1920 >= 256
    assert crop_stream_size(1920, 1080, np.array([[0, 0, 40, 80]])) == (1920, 1080)


def test_suggestions_use_maximum_not_average_similarity():
    # Frontal tracks merge at .6. Back-profile mean is .27, but its maximum is .57.
    vectors = np.zeros((6, 512), np.float16)
    vectors[:2, 0] = 1
    vectors[2:4, :2] = [0.6, 0.8]
    vectors[4:, :3] = [0.57, -0.465, np.sqrt(1 - 0.57**2 - 0.465**2)]
    faces = {
        "frame": np.array([0, 4, 12, 16, 20, 24], np.int32),
        "box": np.tile(np.array([0, 0, 60, 80], np.float32), (6, 1)),
        "score": np.ones(6, np.float32),
        "kps": np.zeros((6, 5, 2), np.float32),
        "embedding": vectors,
    }
    result = cluster_faces(faces, SHOTS, PARAMS)
    assert len(result["clusters"]) == 2
    assert result["suggestions"] == [
        {"a": "c01", "b": "c02", "max_similarity": pytest.approx(0.57, abs=0.001)}
    ]
    assert json_bytes(result) == json_bytes(cluster_faces(faces, SHOTS, PARAMS))


def test_cached_agglomeration_matches_exhaustive_pairs_and_ties():
    rng = np.random.default_rng(32)
    for size in (4, 30):
        values = rng.integers(-2, 5, (size, size)).astype(np.float64) / 4
        values = (values + values.T) / 2
        matrix = values.copy()
        np.fill_diagonal(matrix, -np.inf)
        groups = [[i] for i in range(size)]
        for _ in range(size - 1):
            i, j = np.unravel_index(matrix.argmax(), matrix.shape)
            if matrix[i, j] < 0.25:
                break
            row = (matrix[i] * len(groups[i]) + matrix[j] * len(groups[j])) / (
                len(groups[i]) + len(groups[j])
            )
            matrix[i] = matrix[:, i] = row
            matrix[j] = matrix[:, j] = -np.inf
            matrix[i, i] = -np.inf
            groups[i] = sorted(groups[i] + groups[j])
            groups[j] = []
        assert agglomerate(values, [[i] for i in range(size)], 0.25) == groups


def test_3000_tracks_cluster_in_under_30_seconds():
    n = 3000
    rows = np.repeat(np.arange(n), 2)
    embeddings = np.zeros((2 * n, 512), np.float16)
    embeddings[np.arange(2 * n), rows % 30] = 1
    faces = {
        "frame": np.arange(2 * n, dtype=np.int32),
        "box": np.tile(np.array([0, 0, 60, 80], np.float32), (2 * n, 1)),
        "kps": np.zeros((2 * n, 5, 2), np.float32),
        "score": np.ones(2 * n, np.float32),
        "embedding": embeddings,
    }
    shots = [{"index": i, "start": 2 * i, "end": 2 * i + 2} for i in range(n)]
    start = time.perf_counter()
    result = cluster_faces(faces, shots, PARAMS)
    elapsed = time.perf_counter() - start
    assert len(result["tracks"]) == 3000 and len(result["clusters"]) == 30
    assert elapsed < 30, elapsed
