"""Time-addressed and decode-order frame reading pick the same frames.

decode_rgb and iter_rgb pick frames by presentation time from the picture's origin; media_conform.frames and
MediaTools.stream_frames count them as the decoder emits them. Every source frame shows its own index as eight
bright or dark blocks, so a frame read either way is identified by what it shows, whatever its colour handling.
Where two readers convert colour the same way they must also agree byte for byte.
"""

import json
from fractions import Fraction

import numpy as np
import pytest

from gaiavfx.application import replacement_steps as steps
from gaiavfx.infrastructure import media_conform
from gaiavfx.infrastructure.media import MediaTools

COUNT, WIDTH, HEIGHT = 60, 64, 48
RANGES = [(0, 6), (23, 37), (49, 60), (52, None)]


def indexed_frames():
    frames = np.empty((COUNT, HEIGHT, WIDTH, 3), np.uint8)
    for index in range(COUNT):
        for bit in range(8):
            y, x = divmod(bit, 4)
            frames[index, y * 24 : y * 24 + 24, x * 16 : x * 16 + 16] = (
                225 if index >> bit & 1 else 30
            )
    return frames


def index_of(frame):
    blocks = frame.reshape(2, 24, 4, 16, 3).mean(axis=(1, 3, 4)).ravel()
    return sum(1 << bit for bit, value in enumerate(blocks) if value > 127)


def make_source(media, tmp_path, fps, container):
    lossless = steps.lossless_video(media, indexed_frames(), tmp_path / "indexed.mkv", fps)
    if container == "ffv1":
        return lossless
    # One thread keeps the x264 bitstream independent of the machine's cores; b-adapt=0 always
    # places three B-frames, so presentation order differs from decode order throughout.
    x264 = ["-c:v", "libx264", "-threads", "1", "-bf", "3", "-x264-params", "b-adapt=0"]
    x264 += ["-g", "15", "-crf", "12", "-pix_fmt", "yuv420p"]
    target = tmp_path / ("source.mp4" if container == "mp4" else "source.mkv")
    if container == "mkv audio first":
        # The picture starts after the audio: a non-zero picture origin.
        inputs = ["-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=4"]
        inputs += ["-itsoffset", "0.3", "-i", str(lossless), "-map", "1:v:0", "-map", "0:a:0"]
        x264 += ["-c:a", "pcm_s16le"]
    else:
        inputs = ["-i", str(lossless)]
    media.run(media.ffmpeg, ["-y", *inputs, *x264, str(target)])
    kinds = json.loads(
        media.run(
            media.ffprobe,
            [
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "frame=pict_type",
                "-of",
                "json",
                str(target),
            ],
        )
    )["frames"]
    assert "B" in {f["pict_type"] for f in kinds}
    return target


def decode_order_pinned(media, path, start, end):
    """decode_rgb's own colour conversion, with frames counted in decode order instead of picked by time."""
    info, args = steps._decode_spec(media, path, start, end)
    picked = args[args.index("-vf") + 1]
    colour = picked[picked.index("'", picked.index("'") + 1) + 1 :]
    trim = f"trim=start_frame={start}" + (f":end_frame={end}" if end is not None else "")
    data = steps._pipe(
        media,
        ["-i", str(path), "-map", "0:v:0", "-vf", trim + colour, "-an", "-fps_mode", "passthrough"]
        + ["-pix_fmt", "rgb24", "-f", "rawvideo", "pipe:1"],
    )
    return np.frombuffer(data, np.uint8).reshape(-1, info["height"], info["width"], 3)


@pytest.mark.parametrize("fps", [Fraction(24), Fraction(25), Fraction(30000, 1001)])
@pytest.mark.parametrize("container", ["ffv1", "mp4", "mkv", "mkv audio first"])
def test_time_and_decode_order_read_the_same_frames(tmp_path, fps, container):
    media = MediaTools()
    source = make_source(media, tmp_path, fps, container)
    info = media.probe(source)
    origin = Fraction(info["video_start_time"]) - Fraction(info["format_start_time"])
    assert (origin > 0) == (container == "mkv audio first")
    sequential = steps.decode_rgb(media, source)
    assert [index_of(f) for f in sequential] == list(range(COUNT))
    for start, end in RANGES:
        expected = list(range(start, COUNT if end is None else end))
        by_time = steps.decode_rgb(media, source, start, end)
        streamed = np.stack(list(steps.iter_rgb(media, source, start, end)))
        counted = decode_order_pinned(media, source, start, end)
        native = np.stack(
            [
                np.frombuffer(data, np.uint8).reshape(HEIGHT, WIDTH, 3)
                for data in media_conform.frames(
                    media, source, "rgb24", WIDTH * HEIGHT * 3, start, end
                )
            ]
        )
        scanned = list(
            media.stream_frames(source, width=WIDTH, height=HEIGHT, start=start, end=end)
        )
        assert [index for index, _ in scanned] == expected
        scanned = np.stack([frame for _, frame in scanned])
        for frames in (by_time, streamed, counted, native, scanned):
            assert [index_of(f) for f in frames] == expected
        # Same colour conversion, so the same bytes: buffered or streamed, by time or by count, a
        # range or the whole clip.
        assert np.array_equal(by_time, sequential[start:end])
        assert np.array_equal(streamed, by_time)
        assert np.array_equal(counted, by_time)
        if container == "ffv1":
            # An RGB source passes every reader without a colour conversion.
            assert np.array_equal(native, by_time)
            assert np.array_equal(scanned, by_time)


@pytest.mark.parametrize(
    "tags,same",
    [
        ([], False),
        (["-colorspace", "bt709", "-color_range", "tv"], True),
        (["-colorspace", "smpte170m", "-color_range", "tv"], True),
        (["-colorspace", "bt709", "-color_range", "pc"], True),
    ],
)
def test_ffmpeg_default_colour_matches_the_pinned_matrix_only_on_tagged_sources(
    tmp_path, tags, same
):
    """Why the colour mode stays explicit: an untagged YUV source reads as BT.709 limited range in
    decode_rgb but as FFmpeg's own default (BT.601) in stream_frames and conform's RGB frames."""
    media = MediaTools()
    source = tmp_path / "colour.mp4"
    media.run(
        media.ffmpeg,
        ["-y", "-f", "lavfi", "-i", "testsrc2=s=64x48:r=24:d=0.5", "-c:v", "libx264"]
        + ["-threads", "1", "-pix_fmt", "yuv420p", *tags, str(source)],
    )
    pinned = steps.decode_rgb(media, source)
    native = np.stack(
        [
            np.frombuffer(data, np.uint8).reshape(HEIGHT, WIDTH, 3)
            for data in media_conform.frames(media, source, "rgb24", WIDTH * HEIGHT * 3)
        ]
    )
    scanned = np.stack([f for _, f in media.stream_frames(source, width=WIDTH, height=HEIGHT)])
    assert np.array_equal(native, scanned)
    assert np.array_equal(native, pinned) == same
