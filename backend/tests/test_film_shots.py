"""CPU-only decoded frame boundaries, including the qualified hard-cut excerpt."""

import json
import os
from importlib import resources
from pathlib import Path

import numpy as np
import pytest

from gaiavfx.application.recast_steps import detect_shots, scan_frames
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.direction.film import parse_film_policy


def policy():
    return parse_film_policy(
        resources.files("gaiavfx.modules.direction").joinpath("policy/film.toml").read_text()
    )


@pytest.mark.parametrize("fps", ["24", "25", "24000/1001"])
def test_hard_cuts_tile_decoded_video(tmp_path, fps):
    media = MediaTools()
    path = tmp_path / "cuts.mkv"
    media.run(
        media.ffmpeg,
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"color=black:s=160x96:r={fps}:d=1",
            "-f",
            "lavfi",
            "-i",
            f"color=white:s=160x96:r={fps}:d=1",
            "-f",
            "lavfi",
            "-i",
            f"color=red:s=160x96:r={fps}:d=1",
            "-filter_complex",
            "[0:v]trim=end_frame=12,setpts=PTS-STARTPTS[a];[1:v]trim=end_frame=12,setpts=PTS-STARTPTS[b];[2:v]trim=end_frame=12,setpts=PTS-STARTPTS[c];[a][b][c]concat=n=3:v=1:a=0[v]",
            "-map",
            "[v]",
            "-fps_mode",
            "passthrough",
            "-c:v",
            "ffv1",
            str(path),
        ],
    )
    shots = detect_shots(media.stream_frames(path, width=40, height=24), policy()["detector"])
    assert [(s["start"], s["end"]) for s in shots] == [(0, 12), (12, 24), (24, 36)]
    assert scan_frames(shots, 4) == sorted(set(range(0, 36, 4)) | {11, 23, 35})
    selected = list(media.stream_frames(path, width=40, height=24, start=11, end=14))
    assert [i for i, _ in selected] == [11, 12, 13]
    assert all(a.dtype == np.uint8 and a.shape == (24, 40, 3) for _, a in selected)
    assert not media._processes
    frames = media.stream_frames(path, width=40, height=24)
    next(frames)
    frames.close()
    assert not media._processes


def test_real_excerpt_cpu():
    root = os.environ.get("GAIAVFX_FILM_CUT_FIXTURE")
    if not root:
        pytest.skip("Set GAIAVFX_FILM_CUT_FIXTURE to the local replacement-2026-10-02 directory")
    root = Path(root)
    receipt = json.loads((root / "long60-source-v2/source.json").read_text())
    assert (receipt["start"], receipt["count"]) == (6480, 1440)
    verified = json.loads((root / "cut-qualification/verified.json").read_text())
    assert verified["exact_boundary_matches"] == verified["long_hard_cuts"] == 15
    expected = json.loads((root / "cut-qualification/long60/cuts.json").read_text())["shots"]
    assert expected[0]["start"] == 0 and expected[-1]["end"] == 1440
    media = MediaTools()
    source = root / "long60-source-v2/source.mkv"
    info = media.film_info(source)
    actual = detect_shots(
        media.stream_frames(source, width=info["width"] // 4, height=info["height"] // 4),
        policy()["detector"],
    )
    assert [(s["start"], s["end"]) for s in actual] == [(s["start"], s["end"]) for s in expected]


def test_detector_rejects_empty_or_discontinuous_stream():
    with pytest.raises(ValueError, match="no decoded"):
        detect_shots([], policy()["detector"])
    with pytest.raises(ValueError, match="contiguous"):
        detect_shots([(2, np.zeros((4, 4, 3), np.uint8))], policy()["detector"])


@pytest.mark.parametrize("cuts", [[10, 13], [3], [4]])
def test_min_scene_len_matches_pyscenedetect_071(cuts):
    frames = []
    for index in range(24):
        value = 255 if sum(cut <= index for cut in cuts) % 2 else 0
        frames.append((index, np.full((16, 16, 3), value, np.uint8)))
    shots = detect_shots(frames, policy()["detector"])
    assert [s["start"] for s in shots][1:] == cuts


@pytest.mark.parametrize(
    "text",
    ["stride = true", "tau = nan", "min_faces = 0", "min_face_px = -1", "stride = 0", "tau = 1.1"],
)
def test_policy_rejects_invalid_values(text):
    source = resources.files("gaiavfx.modules.direction").joinpath("policy/film.toml").read_text()
    key = text.split(" = ")[0]
    source = "\n".join(
        text if line.startswith(key + " =") else line for line in source.splitlines()
    )
    with pytest.raises(ValueError):
        parse_film_policy(source)
