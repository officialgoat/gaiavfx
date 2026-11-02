"""One frame stream: raw video frames read from and written to FFmpeg.

A read names its frames with a FrameRange, picked by presentation time or counted in decode order, and a
colour mode:

- "pinned": YUV becomes full-range RGB through the stream's tagged matrix and range (MATRICES), BT.709
  limited range when untagged; an RGB source passes unconverted.
- "ffmpeg_default": no colour arguments, so FFmpeg converts as it chooses (BT.601 for an untagged source);
  frames may be scaled to a size.
- "native": the stored samples: no rotation and no colour arguments.

A Runner spawns FFmpeg with one family of callers' global flags, timeout and error wording. The bytes every
reader and writer produces are pinned by tests/test_frame_stream_pins.py.
"""

import subprocess
import tempfile
from contextlib import contextmanager, suppress
from dataclasses import dataclass
from fractions import Fraction
from typing import Literal

from gaiavfx.shared.errors import ApplicationError

Colour = Literal["pinned", "ffmpeg_default", "native"]


@dataclass(frozen=True)
class Matrix:
    """A YUV matrix: the name FFmpeg's scale filter takes and its luma weights Kr and Kb."""

    name: str
    kr: float
    kb: float


# The one YUV matrix table, by a stream's color_space tag; an untagged or unknown stream is BT.709.
MATRICES = {
    "bt709": Matrix("bt709", 0.2126, 0.0722),
    "bt470bg": Matrix("bt601", 0.299, 0.114),
    "smpte170m": Matrix("bt601", 0.299, 0.114),
    "bt2020nc": Matrix("bt2020", 0.2627, 0.0593),
}


def matrix(color_space) -> Matrix:
    return MATRICES.get(color_space, MATRICES["bt709"])


@dataclass(frozen=True)
class FrameRange:
    """Frames [start, end) of a video's picture; end None reads to its last frame.

    "time" picks frames by presentation time on the stream's constant frame grid, from the picture's own
    origin: FFmpeg seeks at least a second early, keeps original timestamps, and takes a half-frame window
    around each frame, which tolerates container timestamp rounding. It leaves the choice of video stream
    to FFmpeg. "decode" counts the first video stream's frames as the decoder emits them.
    """

    start: int = 0
    end: int | None = None
    addressing: Literal["time", "decode"] = "time"

    def __post_init__(self):
        if self.addressing not in ("time", "decode"):
            raise ValueError(f"Unknown frame addressing: {self.addressing}")

    @property
    def count(self):
        return None if self.end is None else self.end - self.start


@dataclass(frozen=True)
class Source:
    """What time addressing and the pinned colour read from a video before decoding it."""

    width: int
    height: int
    fps: Fraction
    origin: Fraction  # the picture's start after the container's start
    stream: dict  # ffprobe's pix_fmt, color_space, color_range and start_time


def probe(media, path) -> Source:
    data = media.document(path)
    info = media.probe(path, document=data)
    video = next(s for s in data["streams"] if s["codec_type"] == "video")
    stream = {
        key: video[key]
        for key in ("pix_fmt", "color_space", "color_range", "start_time")
        if key in video
    }
    origin = Fraction(stream.get("start_time", "0")) - Fraction(
        data.get("format", {}).get("start_time", "0")
    )
    return Source(info["width"], info["height"], Fraction(info["frame_rate"]), origin, stream)


def _colour_filter(colour, source, size):
    if size is not None and colour != "ffmpeg_default":
        raise ValueError("Only ffmpeg_default frames are scaled")
    if colour == "pinned":
        stream = source.stream
        if stream.get("pix_fmt", "").startswith(("rgb", "bgr", "gbr")):
            return ""
        level = "full" if stream.get("color_range") == "pc" else "limited"
        name = matrix(stream.get("color_space")).name
        return f",scale=in_color_matrix={name}:in_range={level}:out_range=full"
    if colour == "ffmpeg_default" and size is not None:
        return f",scale={size[0]}:{size[1]}:flags=bilinear"
    if colour not in ("ffmpeg_default", "native"):
        raise ValueError(f"Unknown colour mode: {colour}")
    return ""


# Each colour mode keeps the output options its callers always passed, in their order (the byte pins hold what
# they produce). {} stands for the pixel format.
_OUTPUT = {
    "pinned": "-an -fps_mode passthrough -threads:v 1 -pix_fmt {} -f rawvideo",
    "native": "-an -fps_mode passthrough -pix_fmt {} -threads:v 1 -f rawvideo",
    "ffmpeg_default": "-fps_mode passthrough -f rawvideo -pix_fmt {}",
}


def decode_args(path, frames: FrameRange, colour: Colour, pix_fmt, *, source=None, size=None):
    """Arguments that decode `frames` of `path` to stdout as raw `pix_fmt` frames.

    Time addressing and the pinned colour need the probed `source`; `size` (width, height) scales
    ffmpeg_default frames.
    """
    convert = _colour_filter(colour, source, size)
    if frames.addressing == "time":
        fps = source.fps
        seek = max(0, int(frames.start / fps) - 1)
        lower = float(source.origin + (Fraction(frames.start) - Fraction(1, 2)) / fps)
        pick = f"select='gte(t,{lower:.12f})"
        if frames.end is not None:
            upper = float(source.origin + (Fraction(frames.end) - Fraction(1, 2)) / fps)
            pick += f"*lt(t,{upper:.12f})"
        pick += "'"
        inputs = ["-copyts", "-start_at_zero", *(["-ss", str(seek)] if seek else [])]
        mapping, cap = [], ["-frames:v", str(frames.count)] if frames.end is not None else []
    else:
        pick = f"trim=start_frame={frames.start}"
        if frames.end is not None:
            pick += f":end_frame={frames.end}"
        inputs, mapping, cap = [], ["-map", "0:v:0"], []
    if colour == "native":
        inputs.append("-noautorotate")
    output = [pix_fmt if option == "{}" else option for option in _OUTPUT[colour].split()]
    return [*inputs, "-i", str(path), *mapping, "-vf", pick + convert, *cap, *output, "pipe:1"]


def raw_input(pix_fmt, size, fps, *options):
    """Input arguments for raw `pix_fmt` frames of `size` (width, height) at `fps` on stdin; `options`
    (such as colour tags) apply to that input."""
    width, height = size
    return [
        "-f",
        "rawvideo",
        "-pix_fmt",
        pix_fmt,
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        *options,
        "-i",
        "pipe:0",
    ]


@dataclass(frozen=True)
class Runner:
    """How one family of callers runs FFmpeg.

    flags open the command, before the call's own arguments. A failed FFmpeg raises
    ApplicationError(failure + the last `tail` characters of its stderr, status). timeout bounds the wait
    for FFmpeg to exit once its pipe is done (None waits as long as it takes); run() raises timed_out when
    a whole call outlasts it. A last frame shorter than its size raises short_frame with short_status.
    terminate gives an abandoned child ten seconds to exit before it is killed. broken_pipe reports the
    encoder's stderr when a write fails because the encoder has exited.
    """

    flags: tuple[str, ...]
    failure: str = ""
    status: int = 409
    tail: int = 1200
    timeout: float | None = 180
    timed_out: str = "Media processing timed out."
    short_frame: str = "Incomplete decoded frame."
    short_status: int = 409
    terminate: bool = False
    broken_pipe: bool = False

    def command(self, media, args):
        return [str(media.ffmpeg), *self.flags, *args]

    def _detail(self, stderr):
        return stderr.decode("utf-8", errors="replace")[-self.tail :]

    def _failed(self, errors):
        errors.seek(0)
        return ApplicationError(self.failure + self._detail(errors.read()), self.status)

    def _stop(self, child):
        if child.poll() is not None:
            return
        if self.terminate:
            child.terminate()
            try:
                child.wait(timeout=10)
                return
            except subprocess.TimeoutExpired:
                pass
        child.kill()
        child.wait()

    def run(self, media, args, data=None):
        """Run FFmpeg to completion with `data` on stdin; return its stdout."""
        with media.process(
            self.command(media, args),
            stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        ) as child:
            try:
                out, error = child.communicate(data, timeout=self.timeout)
            except subprocess.TimeoutExpired:
                child.kill()
                child.communicate()
                raise ApplicationError(self.timed_out) from None
            if child.returncode:
                raise ApplicationError(self.failure + self._detail(error), self.status)
            return out

    @contextmanager
    def reading(self, media, args):
        """FFmpeg's stdout for the caller to read; its exit is checked once the caller is done.

        stderr goes to a file, so an error cannot deadlock the stdout pipe.
        """
        with (
            tempfile.TemporaryFile() as errors,
            media.process(
                self.command(media, args), stdout=subprocess.PIPE, stderr=errors
            ) as child,
        ):
            try:
                yield child.stdout
                if child.wait(timeout=self.timeout):
                    raise self._failed(errors)
            finally:
                self._stop(child)

    def frames(self, stdout, size):
        """Each `size`-byte frame until the stream ends; a shorter last frame is refused."""
        while data := stdout.read(size):
            if len(data) != size:
                raise ApplicationError(self.short_frame, self.short_status)
            yield data

    @contextmanager
    def writing(self, media, args):
        """FFmpeg's stdin for the caller to write, without retaining frames or filling an unread
        stderr pipe; closed and checked once the caller is done."""
        with (
            tempfile.TemporaryFile() as errors,
            media.process(
                self.command(media, args),
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=errors,
            ) as child,
        ):
            try:
                yield child.stdin
                child.stdin.close()
                if child.wait(timeout=self.timeout):
                    raise self._failed(errors)
            except OSError:
                if not self.broken_pipe:
                    raise
                errors.seek(0)
                detail = self._detail(errors.read())
                if not detail:
                    raise
                raise ApplicationError(self.failure + detail, self.status) from None
            finally:
                self._stop(child)
                # Popen.__exit__ closes stdin too; suppress a second Windows pipe flush error so it
                # cannot hide the encoder's actual failure.
                with suppress(OSError):
                    child.stdin.close()


# Each family keeps the flags and wording it always had.
# Replacement steps: a whole shot decoded into memory (decode_rgb) and other one-shot calls.
BUFFERED = Runner(
    ("-v", "error", "-nostdin", "-protocol_whitelist", "file,pipe", "-threads", "2"),
    timed_out="Replacement media processing timed out.",
)
# Replacement steps and shot assembly: a frame at a time with bounded buffers (iter_rgb, video_writer).
STREAMED = Runner(
    ("-v", "error", "-nostdin", "-threads", "1", "-filter_threads", "1", "-thread_queue_size", "1"),
    broken_pipe=True,
)
# Film scans: decode-order frames at a reduced size (MediaTools.stream_frames).
SCAN = Runner(
    ("-nostdin", "-v", "error", "-protocol_whitelist", "file,pipe"),
    failure="Frame decode failed: ",
    status=422,
    timeout=None,
    short_frame="Truncated raw video frame.",
    short_status=422,
    terminate=True,
)
# Conform: native samples in and out (media_conform).
CONFORM = Runner(
    (
        "-nostdin",
        "-v",
        "error",
        "-threads",
        "1",
        "-filter_threads",
        "1",
        "-protocol_whitelist",
        "file,pipe",
    ),
    failure="Conform media failed: ",
    tail=1600,
    short_frame="Incomplete conform frame.",
    short_status=422,
)
# Face-scan proxies: chosen frames encoded as a small H.264 file (MediaTools.sampled_proxy).
PROXY = Runner(("-y", "-v", "error"), failure="Proxy encode failed: ", status=422, timeout=1800)
