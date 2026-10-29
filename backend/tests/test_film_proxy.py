"""CPU proxy encoding, frame identity, native coordinate restoration, and preflight."""

import json
from fractions import Fraction
from types import SimpleNamespace

import numpy as np
import pytest
from fake_comfy import FakeComfy

from gaiavfx.application.recast_steps import film_proxy, merge_faces, proxy_size
from gaiavfx.application.replacement_steps import lossless_video
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.shared.errors import ApplicationError


@pytest.mark.parametrize("fps", [Fraction(24), Fraction(25), Fraction(24000, 1001)])
@pytest.mark.parametrize("offset", [0, 2])
def test_proxy_contains_only_selected_frames_and_fits_upload(tmp_path, fps, offset):
    media = MediaTools()
    source = tmp_path / "source.mkv"
    pixels = np.broadcast_to(
        np.arange(40, dtype=np.uint8)[:, None, None, None] * 5, (40, 96, 160, 3)
    ).copy()
    lossless_video(media, pixels, source, fps)
    if offset:
        shifted = tmp_path / "offset.mkv"
        media.run(
            media.ffmpeg,
            [
                "-y",
                "-itsoffset",
                str(offset),
                "-i",
                str(source),
                "-copyts",
                "-c",
                "copy",
                str(shifted),
            ],
        )
        source = shifted
    assert Fraction(media.film_info(source)["frame_rate"]) == fps
    frames = [9, 13, 20, 31, 39]
    context = SimpleNamespace(
        library=SimpleNamespace(media=media),
        staging=tmp_path / "chunk",
        paths={"source": source},
        snapshot={
            "step_spec": {
                "source_asset": "source",
                "frames": frames,
                "film": {"width": 160, "height": 96, "fps": [fps.numerator, fps.denominator]},
                "params": {"max_upload_bytes": 100000000},
            }
        },
    )
    outputs = film_proxy(context)
    proxy = outputs[0][1]
    actual = list(media.stream_frames(proxy, width=160, height=96))
    assert len(actual) == len(frames)
    assert [round(float(p.mean())) for _, p in actual] == pytest.approx(
        [i * 5 for i in frames], abs=2
    )
    assert json.loads(outputs[1][1].read_text())["frames"] == {
        str(i): f for i, f in enumerate(frames)
    }
    assert proxy.stat().st_size < 100000000
    rotated = tmp_path / "rotated.mp4"
    media.run(
        media.ffmpeg,
        ["-y", "-display_rotation", "90", "-i", str(proxy), "-c", "copy", str(rotated)],
    )
    with pytest.raises(ApplicationError, match="rotated video: re-export upright"):
        media.film_info(rotated)
    fake = FakeComfy()
    fake.upload("fake", proxy, "chunk/proxy.mp4")
    assert fake.upload_sizes == [proxy.stat().st_size]
    # A completed chunk is reused without decoding again.
    media.sampled_proxy = lambda *args, **kwargs: pytest.fail("completed proxy re-encoded")
    assert film_proxy(context) == outputs
    fake.max_upload_bytes = proxy.stat().st_size
    with pytest.raises(ValueError, match="max_upload_bytes"):
        fake.upload("fake", proxy, "chunk/proxy.mp4")
    context.snapshot["step_spec"]["params"]["max_upload_bytes"] = 1
    with pytest.raises(ApplicationError, match="max_upload_bytes"):
        film_proxy(context)


def test_proxy_dimensions_and_native_coordinate_mapping():
    assert proxy_size(1921, 1081) == (1280, 720)
    assert proxy_size(511, 319) == (510, 318)
    embedding = np.zeros((1, 512), np.float16)
    embedding[:, 0] = 1
    faces = {
        "frame": np.array([1], np.int32),
        "box": np.array([[10, 20, 30, 40]], np.float32),
        "kps": np.ones((1, 5, 2), np.float32),
        "score": np.ones(1, np.float32),
        "embedding": embedding,
    }
    mapping = {"frames": {"0": 4, "1": 8}, "scale": [0.5, 0.25]}
    second = {"frames": {"0": 12, "1": 16}, "scale": [0.5, 0.25]}
    result = merge_faces([(faces, mapping), (faces, second)], 20)
    assert result["frame"].tolist() == [8, 16]
    assert result["box"].tolist() == [[20, 80, 60, 160]] * 2
    assert result["kps"].tolist() == [[[2, 4]] * 5] * 2
    np.testing.assert_array_equal(result["embedding"], np.repeat(embedding, 2, axis=0))


@pytest.mark.parametrize(
    "change,message",
    [
        ({"avg_frame_rate": "0/0"}, "invalid video frame rate"),
        ({"avg_frame_rate": "0/1"}, "invalid video frame rate"),
        ({"avg_frame_rate": "25/1"}, "variable frame rate"),
        ({"side_data_list": [{"rotation": -90}]}, "rotated video: re-export upright"),
    ],
)
def test_preflight_rejects_invalid_metadata_without_pixel_decode(monkeypatch, change, message):
    media = MediaTools()
    stream = {"avg_frame_rate": "24/1", "r_frame_rate": "24/1", "time_base": "1/24", **change}
    monkeypatch.setattr(
        media,
        "run",
        lambda executable, *args, **kwargs: json.dumps(
            {"streams": [stream], "packets": [{"pts": 0}, {"pts": 1}]}
        ),
    )
    monkeypatch.setattr(
        media, "probe", lambda *args: pytest.fail("preflight must reject before further processing")
    )
    with pytest.raises(ApplicationError, match=message) as error:
        media.film_info("unused")
    assert error.value.status == 422


def test_vfr_with_matching_average_and_nominal_rate_is_rejected(monkeypatch):
    media = MediaTools()
    data = {
        "streams": [{"avg_frame_rate": "24/1", "r_frame_rate": "24/1", "time_base": "1/24000"}],
        "packets": [{"pts": v} for v in (0, 1000, 2500, 3000)],
    }
    monkeypatch.setattr(media, "run", lambda *args, **kwargs: json.dumps(data))
    with pytest.raises(ApplicationError, match="variable frame rate"):
        media.film_info("unused")


def test_proxy_selects_a_full_chunk_of_frames(tmp_path):
    # Regression (8 Oct real-film trial): an ffmpeg select expression with ~1200 eq() terms
    # ran out of memory. Selection is now done on decoded frames in Python.
    media = MediaTools()
    source = tmp_path / "long.mkv"
    count = 5000
    pixels = np.zeros((count, 16, 32, 3), np.uint8)
    pixels[:, :, :, 0] = (np.arange(count) % 251)[:, None, None]
    lossless_video(media, pixels, source, Fraction(24))
    frames = sorted(set(range(0, 4800, 4)) | {401, 402, 2999})
    assert len(frames) >= 1200
    proxy = tmp_path / "proxy.mp4"
    media.sampled_proxy(source, proxy, frames, width=32, height=16, fps=Fraction(24))
    decoded = list(media.stream_frames(proxy, width=32, height=16))
    assert len(decoded) == len(frames)
    reds = [int(np.median(frame[:, :, 0])) for _, frame in decoded]
    expected = [f % 251 for f in frames]
    assert max(abs(a - b) for a, b in zip(reds, expected, strict=True)) <= 3
