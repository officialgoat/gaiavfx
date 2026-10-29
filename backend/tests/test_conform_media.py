"""CPU FFmpeg evidence: native pixels, compressed packet identity and excerpt samples."""

import hashlib
import json
import subprocess
from fractions import Fraction

import numpy as np
import pytest

from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.infrastructure.media_conform import frames, pipe, streams
from gaiavfx.shared.errors import ApplicationError


def fixture_clip(tmp_path, fps, fmt="yuv444p"):
    media = MediaTools()
    source = tmp_path / "source.mkv"
    media.run(
        media.ffmpeg,
        [
            "-y",
            "-f",
            "lavfi",
            "-i",
            f"testsrc2=s=64x48:r={fps}:d=1",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=733:sample_rate=48000:duration=1",
            "-vf",
            "drawbox=x=0:y=0:w=iw:h=6:color=black:t=fill,drawbox=x=0:y=42:w=iw:h=6:color=black:t=fill,setsar=4/3",
            "-c:v",
            "ffv1",
            "-pix_fmt",
            fmt,
            "-colorspace",
            "bt709",
            "-color_range",
            "tv",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-c:a",
            "aac",
            str(source),
        ],
    )
    return media, source


def make_patch(media, tmp_path, fps, rect, start, end, *, source_asset="film"):
    x0, y0, x1, y1 = rect
    spec = {
        "rect": rect,
        "start": start,
        "end": end,
        "fps": [fps.numerator, fps.denominator],
        "source_asset": source_asset,
    }
    attachment = tmp_path / f"patch-{start}.json"
    attachment.write_text(json.dumps(spec))
    path = tmp_path / f"patch-{start}.mkv"
    rgba = np.zeros((y1 - y0, x1 - x0, 4), np.uint8)
    rgba[:, :, :3] = [255, 0, 0]
    rgba[:, :, 3] = 128
    rgba[0, :, 3] = 0
    rgba[-1, :, 3] = 255
    with pipe(
        media,
        [
            "-y",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgba",
            "-s",
            f"{x1 - x0}x{y1 - y0}",
            "-r",
            str(fps),
            "-i",
            "pipe:0",
            "-c:v",
            "ffv1",
            "-pix_fmt",
            "bgra",
            "-attach",
            str(attachment),
            "-metadata:s:t:0",
            "filename=patch.json",
            "-metadata:s:t:0",
            "mimetype=application/json",
            str(path),
        ],
        writing=True,
    ) as writer:
        for _ in range(start, end):
            writer.write(rgba.tobytes())
    return {"path": path, "source_asset": source_asset, "start": start, "end": end}, rgba


def audio_hashes(media, path):
    data = json.loads(
        media.run(
            media.ffprobe,
            [
                "-v",
                "error",
                "-select_streams",
                "a",
                "-show_packets",
                "-show_data_hash",
                "sha256",
                "-of",
                "json",
                str(path),
            ],
        )
    )
    return [p["data_hash"] for p in data["packets"]]


@pytest.mark.parametrize("fps", [Fraction(24), Fraction(25), Fraction(24000, 1001)])
def test_native_frames_alpha_audio_packets_and_tags(tmp_path, fps, monkeypatch):
    from gaiavfx.infrastructure import media_conform

    # Cross several staged-part boundaries, including through the second patch.
    monkeypatch.setattr(media_conform, "PART_FRAMES", 7)
    media, source = fixture_clip(tmp_path, fps)
    before_hash = hashlib.sha256(source.read_bytes()).hexdigest()
    rects = [[0, 8, 12, 24], [40, 0, 64, 12]]
    entries = [
        make_patch(media, tmp_path, fps, rect, start, start + 3)
        for rect, start in zip(rects, [2, 12], strict=True)
    ]
    output = tmp_path / "conform.mkv"
    media.conform_patches(
        source,
        [e[0] for e in entries],
        output,
        codec="ffv1",
        cancelled=lambda: False,
        progress=lambda _: None,
    )
    old = list(frames(media, source, "yuv444p", 64 * 48 * 3))
    new = list(frames(media, output, "yuv444p", 64 * 48 * 3))
    assert len(old) == len(new)
    assert audio_hashes(media, source) == audio_hashes(media, output)
    for index, (a, b) in enumerate(zip(old, new, strict=True)):
        active = next(
            (
                (e, rgba, rect)
                for (e, rgba), rect in zip(entries, rects, strict=True)
                if e["start"] <= index < e["end"]
            ),
            None,
        )
        if active is None:
            assert a == b
            continue
        _, rgba, (x0, y0, x1, y1) = active
        a = np.frombuffer(a, np.uint8).reshape(3, 48, 64)
        b = np.frombuffer(b, np.uint8).reshape(3, 48, 64)
        covered = np.zeros((48, 64), bool)
        covered[y0:y1, x0:x1] = rgba[:, :, 3] > 0
        assert np.array_equal(a[:, ~covered], b[:, ~covered])
        alpha = rgba[:, :, 3] / 255
        red_yuv = np.array([16 + 219 * 0.2126, 128 - 224 * 0.2126 / (2 * (1 - 0.0722)), 240])
        expected = np.rint(
            a[:, y0:y1, x0:x1] * (1 - alpha) + red_yuv[:, None, None] * alpha
        ).astype(np.uint8)
        assert np.array_equal(b[:, y0:y1, x0:x1], expected)
    old_video = next(s for s in streams(media, source)["streams"] if s["codec_type"] == "video")
    new_video = next(s for s in streams(media, output)["streams"] if s["codec_type"] == "video")
    for key in (
        "width",
        "height",
        "sample_aspect_ratio",
        "color_space",
        "color_range",
        "color_transfer",
        "color_primaries",
    ):
        assert new_video.get(key) == old_video.get(key)
    assert Fraction(new_video["avg_frame_rate"]) == fps
    assert hashlib.sha256(source.read_bytes()).hexdigest() == before_hash


def test_excerpt_pcm_sample_boundaries_and_lossless_resume(tmp_path, monkeypatch):
    from gaiavfx.infrastructure import media_conform

    media, source = fixture_clip(tmp_path, Fraction(25))
    monkeypatch.setattr(media_conform, "PART_FRAMES", 2)
    output = tmp_path / "excerpt.mkv"
    values = []
    with pytest.raises(ApplicationError, match="cancelled"):
        media.conform_patches(
            source,
            [],
            output,
            codec="ffv1",
            frame_range={"start": 1, "end": 6},
            cancelled=lambda: bool(values),
            progress=values.append,
        )
    part = tmp_path / "excerpt-parts" / "part-000000001.mkv"
    assert part.exists() and not output.exists()
    stamp = part.stat().st_mtime_ns
    media.conform_patches(
        source,
        [],
        output,
        codec="ffv1",
        frame_range={"start": 1, "end": 6},
        cancelled=lambda: False,
        progress=values.append,
    )
    assert part.stat().st_mtime_ns == stamp
    assert len(list(frames(media, output, "yuv444p", 64 * 48 * 3))) == 5
    audio = next(s for s in streams(media, output)["streams"] if s["codec_type"] == "audio")
    assert audio["codec_name"] == "pcm_f32le"
    pcm = subprocess.check_output(
        [str(media.ffmpeg), "-v", "error", "-i", str(output), "-map", "0:a:0", "-f", "s32le", "-"]
    )
    assert len(pcm) // 4 == 9600
    # Compare with a direct trim in source timestamps, including AAC preroll.
    origin = next(s for s in streams(media, source)["streams"] if s["codec_type"] == "video")[
        "start_time"
    ]
    expected = subprocess.check_output(
        [
            str(media.ffmpeg),
            "-v",
            "error",
            "-i",
            str(source),
            "-map",
            "0:a:0",
            "-af",
            f"atrim=start={float(origin) + 0.04}:end={float(origin) + 0.24},asetpts=PTS-STARTPTS",
            "-f",
            "s32le",
            "-",
        ]
    )
    assert pcm == expected


@pytest.mark.parametrize("codec", ["h264", "prores"])
def test_delivery_codecs_keep_audio_packets(tmp_path, codec):
    media, source = fixture_clip(tmp_path, Fraction(24), "yuv420p")
    output = tmp_path / "delivery.mkv"
    media.conform_patches(
        source, [], output, codec=codec, cancelled=lambda: False, progress=lambda _: None
    )
    assert audio_hashes(media, source) == audio_hashes(media, output)
    assert len(list(frames(media, output, "rgb24", 64 * 48 * 3))) == 24


@pytest.mark.parametrize("fmt", ["yuv420p", "yuv422p10le", "yuv444p10le"])
def test_native_subsampling_and_ten_bit_untouched_frames(tmp_path, fmt):
    media, source = fixture_clip(tmp_path, Fraction(25), fmt)
    patch, _ = make_patch(media, tmp_path, Fraction(25), [0, 8, 12, 24], 2, 5)
    output = tmp_path / "native.mkv"
    media.conform_patches(
        source, [patch], output, codec="ffv1", cancelled=lambda: False, progress=lambda _: None
    )
    from gaiavfx.infrastructure.conform_pixels import NativePixels

    layout = NativePixels(streams(media, source)["streams"][0])
    before = list(frames(media, source, fmt, layout.size))
    after = list(frames(media, output, fmt, layout.size))
    assert len(before) == len(after) == 25
    assert all(a == b for i, (a, b) in enumerate(zip(before, after, strict=True)) if not 2 <= i < 5)
    for i in range(2, 5):
        a, b = (np.frombuffer(data, layout.dtype) for data in (before[i], after[i]))
        assert not np.array_equal(a, b)
        mask = np.zeros(len(a), bool)
        offset = 0
        for w, h, sx, sy in [
            (64, 48, 1, 1),
            (layout.cw, layout.ch, layout.sx, layout.sy),
            (layout.cw, layout.ch, layout.sx, layout.sy),
        ]:
            mask[offset : offset + w * h].reshape(h, w)[8 // sy : 24 // sy, : 12 // sx] = True
            offset += w * h
        assert np.array_equal(a[~mask], b[~mask])


def test_wrong_patch_identity_refuses_and_leaves_source_intact(tmp_path):
    media, source = fixture_clip(tmp_path, Fraction(24))
    patch, _ = make_patch(media, tmp_path, Fraction(24), [0, 8, 12, 24], 2, 5)
    patch["source_asset"] = "other-film"
    with pytest.raises(ApplicationError, match="belong"):
        media.conform_patches(
            source,
            [patch],
            tmp_path / "wrong.mkv",
            codec="ffv1",
            cancelled=lambda: False,
            progress=lambda _: None,
        )
    assert not (tmp_path / "wrong.mkv").exists()
    with pytest.raises(ApplicationError, match="overwrite"):
        media.conform_patches(
            source, [], source, codec="ffv1", cancelled=lambda: False, progress=lambda _: None
        )


def test_full_audio_offset_and_colour_tags(tmp_path):
    media, original = fixture_clip(tmp_path, Fraction(25))
    source = tmp_path / "offset.mkv"
    media.run(
        media.ffmpeg,
        [
            "-y",
            "-i",
            str(original),
            "-vf",
            "setpts=PTS+0.2/TB,setparams=color_primaries=bt709:color_trc=bt709:colorspace=bt709:range=limited",
            "-c:v",
            "libx264",
            "-crf",
            "0",
            "-colorspace",
            "bt709",
            "-color_primaries",
            "bt709",
            "-color_trc",
            "bt709",
            "-color_range",
            "tv",
            "-c:a",
            "copy",
            "-fps_mode",
            "passthrough",
            str(source),
        ],
    )
    output = tmp_path / "master.mkv"
    media.conform_patches(
        source, [], output, codec="h264", cancelled=lambda: False, progress=lambda _: None
    )
    before, after = (streams(media, p)["streams"] for p in (source, output))
    assert audio_hashes(media, source) == audio_hashes(media, output)
    assert len(list(frames(media, output, "rgb24", 64 * 48 * 3))) == 25
    for key in (
        "color_range",
        "color_space",
        "color_transfer",
        "color_primaries",
        "sample_aspect_ratio",
    ):
        assert before[0][key] == after[0][key]
    offset = lambda s: float(s[0]["start_time"]) - float(s[1]["start_time"])
    assert abs(offset(before) - offset(after)) < 0.002


def test_mp4_aac_priming_packets_are_copied(tmp_path):
    media, original = fixture_clip(tmp_path, Fraction(24000, 1001))
    source = tmp_path / "source.mp4"
    media.run(
        media.ffmpeg, ["-y", "-i", str(original), "-c:v", "libx264", "-c:a", "aac", str(source)]
    )
    output = tmp_path / "master.mkv"
    media.conform_patches(
        source, [], output, codec="h264", cancelled=lambda: False, progress=lambda _: None
    )
    assert audio_hashes(media, source) == audio_hashes(media, output)


def test_shutdown_sentinel_terminates_media_child(monkeypatch):
    from contextlib import contextmanager
    from unittest.mock import Mock

    media = MediaTools()
    child = Mock()

    @contextmanager
    def process(*args, **kwargs):
        yield child

    monkeypatch.setattr(media, "process", process)

    def stopped():
        raise RuntimeError("runner stopping")

    with pytest.raises(RuntimeError, match="runner stopping"):
        media.run(media.ffmpeg, ["-version"], cancelled=stopped)
    child.kill.assert_called_once()
    child.communicate.assert_called_once()


def test_excerpt_retains_audio_that_starts_after_picture(tmp_path):
    media, original = fixture_clip(tmp_path, Fraction(25))
    source = tmp_path / "late-audio.mkv"
    media.run(
        media.ffmpeg,
        [
            "-y",
            "-i",
            str(original),
            "-itsoffset",
            "0.2",
            "-i",
            str(original),
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c",
            "copy",
            str(source),
        ],
    )
    output = tmp_path / "excerpt.mkv"
    media.conform_patches(
        source,
        [],
        output,
        codec="ffv1",
        frame_range={"start": 0, "end": 15},
        cancelled=lambda: False,
        progress=lambda _: None,
    )
    audio = next(s for s in streams(media, output)["streams"] if s["codec_type"] == "audio")
    assert abs(float(audio["start_time"]) - 0.2) < 0.002
