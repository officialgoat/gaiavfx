"""The frame stream's runner keeps each family of callers' failure wording, status and timeout."""

import io
from contextlib import contextmanager
from dataclasses import replace

import numpy as np
import pytest

from gaiavfx.infrastructure import frame_stream
from gaiavfx.infrastructure.frame_stream import (
    BUFFERED,
    CONFORM,
    PROXY,
    SCAN,
    STREAMED,
    FrameRange,
    raw_input,
)
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.shared.errors import ApplicationError


@pytest.mark.parametrize(
    "runner,prefix,status",
    [(BUFFERED, "", 409), (STREAMED, "", 409), (SCAN, "Frame decode failed: ", 422)]
    + [(CONFORM, "Conform media failed: ", 409), (PROXY, "Proxy encode failed: ", 422)],
)
def test_a_failed_ffmpeg_reports_its_stderr_with_the_familys_wording(
    tmp_path, runner, prefix, status
):
    media = MediaTools()
    args = ["-i", str(tmp_path / "missing.mkv"), "-f", "rawvideo", "pipe:1"]
    with pytest.raises(ApplicationError) as failed, runner.reading(media, args) as output:
        assert output.read() == b""
    assert str(failed.value).startswith(prefix) and "missing.mkv" in str(failed.value)
    assert failed.value.status == status
    with pytest.raises(ApplicationError) as failed:
        runner.run(media, args)
    assert str(failed.value).startswith(prefix) and failed.value.status == status


@pytest.mark.parametrize(
    "runner,message,status",
    [
        (STREAMED, "Incomplete decoded frame.", 409),
        (SCAN, "Truncated raw video frame.", 422),
        (CONFORM, "Incomplete conform frame.", 422),
    ],
)
def test_a_short_last_frame_is_refused(runner, message, status):
    frames = runner.frames(io.BytesIO(b"abcdefghij"), 4)
    assert next(frames) == b"abcd" and next(frames) == b"efgh"
    with pytest.raises(ApplicationError, match=message) as refused:
        next(frames)
    assert refused.value.status == status


def test_a_buffered_call_that_outlasts_its_timeout_names_the_replacement_media():
    slow = replace(BUFFERED, timeout=0.5)
    endless = ["-f", "lavfi", "-i", "testsrc2=s=64x48:d=3600", "-f", "null", "-"]
    with pytest.raises(ApplicationError, match="^Replacement media processing timed out.$"):
        slow.run(MediaTools(), endless)


@pytest.mark.parametrize("runner", [STREAMED, CONFORM])
def test_an_encoder_that_exits_mid_write_is_explained_only_where_it_always_was(tmp_path, runner):
    """video_writer's family reports the encoder's stderr; conform keeps the bare OSError."""
    media = MediaTools()
    args = ["-y", *raw_input("rgb24", (64, 48), 24), "-c:v", "no_such_encoder"]
    frame = np.zeros((48, 64, 3), np.uint8).tobytes()
    target = str(tmp_path / "out.mkv")
    with (
        pytest.raises((ApplicationError, OSError)) as failed,
        runner.writing(media, [*args, target]) as stdin,
    ):
        for _ in range(600):
            stdin.write(frame)
    if runner.broken_pipe:
        assert isinstance(failed.value, ApplicationError)
        assert "no_such_encoder" in str(failed.value)
    else:
        assert isinstance(failed.value, OSError)


@pytest.mark.parametrize("runner", [STREAMED, SCAN, CONFORM])
def test_an_abandoned_read_stops_its_ffmpeg(runner):
    media = MediaTools()
    children = []
    spawn = media.process

    @contextmanager
    def process(command, **kwargs):
        with spawn(command, **kwargs) as child:
            children.append(child)
            yield child

    media.process = process
    endless = ["-f", "lavfi", "-i", "testsrc2=s=64x48:d=3600", "-f", "rawvideo", "pipe:1"]

    def read():
        with runner.reading(media, endless) as output:
            yield from runner.frames(output, 64 * 48 * 3)

    frames = read()
    assert len(next(frames)) == 64 * 48 * 3
    frames.close()
    assert len(children) == 1 and children[0].poll() is not None


def test_frame_ranges_and_colour_modes_are_closed():
    with pytest.raises(ValueError):
        FrameRange(0, 3, "index")
    with pytest.raises(ValueError):
        frame_stream.decode_args(
            "clip.mkv", FrameRange(0, 3, "decode"), "native", "rgb24", size=(8, 8)
        )
    with pytest.raises(ValueError):
        frame_stream.decode_args("clip.mkv", FrameRange(0, 3, "decode"), "vivid", "rgb24")
    assert (
        frame_stream.matrix(None) == frame_stream.matrix("bt709") == frame_stream.matrix("unknown")
    )
    assert frame_stream.matrix("smpte170m").name == frame_stream.matrix("bt470bg").name == "bt601"
