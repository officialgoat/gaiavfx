"""Pins every raw-frame decoder and writer: the FFmpeg arguments it passes and the bytes it produces.

Recorded before the decoders and writers moved onto one frame stream, so a refactor must reproduce both.
The argument lists (golden/frame_stream/arguments.json) hold on any machine. The output hashes hold for
the bundled FFmpeg build they were recorded with; another build skips them, never the argument pins.
"""

import hashlib
import json
import os
import subprocess
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from gaiavfx.application.replacement_steps import decode_rgb, iter_rgb, lossless_video
from gaiavfx.application.shot_assembly import shot_assembly
from gaiavfx.infrastructure import media_conform
from gaiavfx.infrastructure.media import MediaTools

GOLDEN = Path(__file__).parent / "golden" / "frame_stream" / "arguments.json"
BUILD = "ffmpeg version 9.0.1-essentials_build-www.gyan.dev"
# x264 picks its own thread count from the cores when a caller does not pin one.
PROXY_CORES = 16
HASHES = {
    "decode_rgb rgb source": "66ba057d08d0cd24209eebe768286a15ccc1f6732fd4711a0e48f0613a0ffbd3",
    "decode_rgb bt709 seek": "1b85757c8a277d3cd6eaf02cc855cbacd65a4740be65ac037ae40c8c49ffa7ac",
    "iter_rgb smpte170m full range origin": (
        "87c9504441a75204c004120812e890e60bd0ab6885591ddaac7c45be5109903c"
    ),
    "iter_rgb rgba open end": "5f0cc9d9be5a8e6b7dc845aa294e0691df419f66c68de711e18deffb34b68613",
    "stream_frames scaled range": "00b8df965c75ad598b4a0c58fb6e8fcc6392167a4f228dbf808a00f17f706140",
    # An RGB source scaled to its own size reads exactly as decode_rgb reads it.
    "stream_frames rgb source": "66ba057d08d0cd24209eebe768286a15ccc1f6732fd4711a0e48f0613a0ffbd3",
    "conform frames native range": "aa92bb35e3a33b8afa48be0c44163a119c0236e8a44ba24cae265011fe932e50",
    "conform frames rgba": "ac2a317a0c58e5005b2b8d27c93da73cc8fa75f273e3c41e409926a4c22eca53",
    "lossless_video rgb": "3b34945114abdadfd44659c6403cca4d54cfa46a0997613e9b0fcee63b317bf5",
    "lossless_video alpha attachment": (
        "a6d0339b6fd3595448db02166c7a5227f03836902b4a12563ab48c0d39b8a49c"
    ),
    "lossless_video audio tempo pad": (
        "1c85368e0fa8c8083f66657d727dcef9bba879020450471ee7c98eaccf94c853"
    ),
    "sampled_proxy": "8d2701f6db889e4cc0aaee6135954da8503fa82f7ade6747f1f0b0db5596d1af",
    "shot_assembly patch": "1da960672060b24197c0ff9bfcdaca5f918b9e50044a5bfb670ced69d4bf1eec",
    "shot_assembly preview": "c4cdeb42265876bd490cada3a63407100f6d74ccf2170d5e544ad21acae24f68",
    "conform_patches master": "14fdefa16969236cccbd9d18d47b30a3a939ee0938bdaa7e02ae0c444073c15f",
}


class Recorder:
    """A MediaTools whose every child process is recorded, by label, with portable paths."""

    def __init__(self, root):
        self.media = MediaTools()
        self.root = str(root)
        self.calls = {}
        self.label = None
        spawn = self.media.process

        @contextmanager
        def process(command, **kwargs):
            if self.label is not None:
                self.calls.setdefault(self.label, []).append(self.portable(command))
            with spawn(command, **kwargs) as child:
                yield child

        self.media.process = process

    def portable(self, command):
        names = {str(self.media.ffmpeg): "ffmpeg", str(self.media.ffprobe): "ffprobe"}
        return [
            names.get(str(arg))
            or (
                str(arg).replace(self.root, "<tmp>").replace("\\", "/")
                if self.root in str(arg)
                else str(arg)
            )
            for arg in command
        ]

    @contextmanager
    def recording(self, label):
        assert label not in self.calls, label
        self.label = label
        try:
            yield self.media
        finally:
            self.label = None


def ffmpeg(media, *args):
    media.run(media.ffmpeg, ["-y", *map(str, args)])


def make_inputs(media, root):
    """Small deterministic clips: x264 runs on one thread so the bitstream is the same on any core count."""
    rgb, bframes, full = root / "rgb.mkv", root / "bframes.mp4", root / "full.mkv"
    ffmpeg(
        media,
        *("-f", "lavfi", "-i", "testsrc2=s=64x48:r=24:d=0.5"),
        *("-c:v", "ffv1", "-pix_fmt", "bgr0", "-fflags", "+bitexact", "-flags:v", "+bitexact"),
        rgb,
    )
    ffmpeg(
        media,
        *("-f", "lavfi", "-i", "testsrc2=s=64x48:r=25:d=3"),
        *("-c:v", "libx264", "-threads", "1", "-bf", "2", "-g", "12", "-crf", "20"),
        *("-pix_fmt", "yuv420p", "-colorspace", "bt709", "-color_primaries", "bt709"),
        *("-color_trc", "bt709", "-color_range", "tv", "-fflags", "+bitexact"),
        bframes,
    )
    # The picture starts half a second after the audio: a non-zero picture origin.
    ffmpeg(
        media,
        *("-f", "lavfi", "-i", "sine=frequency=500:sample_rate=48000:duration=1.5"),
        *("-itsoffset", "0.5", "-f", "lavfi", "-i", "testsrc2=s=64x48:r=30000/1001:d=0.7"),
        *("-map", "1:v:0", "-map", "0:a:0", "-c:v", "ffv1", "-pix_fmt", "yuv444p"),
        *("-colorspace", "smpte170m", "-color_range", "pc", "-c:a", "pcm_s16le"),
        *("-fflags", "+bitexact"),
        full,
    )
    return rgb, bframes, full


def digest(*chunks):
    value = hashlib.sha256()
    for chunk in chunks:
        value.update(bytes(chunk) if isinstance(chunk, bytes | bytearray) else chunk.tobytes())
    return value.hexdigest()


def file_digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def packet_digest(media, path):
    """Packet payloads only: an unflagged Matroska muxer writes random UIDs into the container."""
    data = media.run(
        media.ffprobe,
        ["-v", "error", "-show_packets", "-show_data_hash", "sha256", "-of", "json", str(path)],
    )
    packets = json.loads(data)["packets"]
    return digest(json.dumps([[p["stream_index"], p["data_hash"]] for p in packets]).encode())


def scenario(root):
    """Run every decoder and writer once on small inputs; return their arguments and output hashes."""
    plain = MediaTools()
    rgb, bframes, full = make_inputs(plain, root)
    recorder = Recorder(root)
    hashes = {}

    with recorder.recording("decode_rgb rgb source") as media:
        hashes["decode_rgb rgb source"] = digest(decode_rgb(media, rgb))
    with recorder.recording("decode_rgb bt709 seek") as media:
        hashes["decode_rgb bt709 seek"] = digest(decode_rgb(media, bframes, 60, 70))
    with recorder.recording("iter_rgb smpte170m full range origin") as media:
        hashes["iter_rgb smpte170m full range origin"] = digest(*iter_rgb(media, full, 3, 9))
    with recorder.recording("iter_rgb rgba open end") as media:
        hashes["iter_rgb rgba open end"] = digest(*iter_rgb(media, rgb, 2, None, alpha=True))
    with recorder.recording("stream_frames scaled range") as media:
        frames = media.stream_frames(bframes, width=32, height=24, start=5, end=15)
        hashes["stream_frames scaled range"] = digest(*(f for _, f in frames))
    with recorder.recording("stream_frames rgb source") as media:
        frames = media.stream_frames(rgb, width=64, height=48)
        hashes["stream_frames rgb source"] = digest(*(f for _, f in frames))
    with recorder.recording("conform frames native range") as media:
        frames = media_conform.frames(media, full, "yuv444p", 64 * 48 * 3, 2, 8)
        hashes["conform frames native range"] = digest(*frames)
    with recorder.recording("conform frames rgba") as media:
        hashes["conform frames rgba"] = digest(
            *media_conform.frames(media, rgb, "rgba", 64 * 48 * 4)
        )

    rng = np.random.default_rng(7)
    target = root / "lossless" / "rgb.mkv"
    with recorder.recording("lossless_video rgb") as media:
        lossless_video(media, rng.integers(0, 256, (3, 48, 64, 3), np.uint8), target, 24)
    hashes["lossless_video rgb"] = file_digest(target)
    attachment = root / "patch.json"
    attachment.write_text('{"pin": true}', "utf-8")
    rgba = rng.integers(0, 256, (4, 24, 32, 4), np.uint8)
    target = root / "lossless" / "alpha.mkv"
    with recorder.recording("lossless_video alpha attachment") as media:
        lossless_video(
            media,
            iter(rgba),
            target,
            Fraction(25),
            size=(32, 24),
            frame_count=4,
            alpha=True,
            attachment=attachment,
            identity="pin",
        )
    hashes["lossless_video alpha attachment"] = file_digest(target)
    target = root / "lossless" / "audio.mkv"
    with recorder.recording("lossless_video audio tempo pad") as media:
        lossless_video(
            media,
            rng.integers(0, 256, (6, 48, 64, 3), np.uint8),
            target,
            Fraction(24),
            full,
            3,
            6,
            "audio",
            source_fps=Fraction(30000, 1001),
            audio_pad_duration=Fraction(1, 2),
        )
    hashes["lossless_video audio tempo pad"] = file_digest(target)

    target = root / "proxy" / "proxy.mp4"
    with recorder.recording("sampled_proxy") as media:
        media.sampled_proxy(bframes, target, [0, 3, 4, 10, 30], width=32, height=24, fps=25)
    hashes["sampled_proxy"] = file_digest(target)

    # Two overlapping windows: the delivery plan crossfades frames 9-12 between them.
    fps = Fraction(30000, 1001)
    windows = []
    for name, start, end, value in (("w0", 0, 13, 200), ("w1", 9, 21, 60)):
        pixels = decode_rgb(plain, full, start, end).copy()
        pixels[:, 10:20, 20:40] = value
        lossless_video(plain, pixels, root / f"{name}.mkv", fps)
        windows.append({"start": start, "end": end, "asset": name})
    staging = root / "assembly"
    context = SimpleNamespace(
        library=SimpleNamespace(media=recorder.media),
        staging=staging,
        paths={"film": full, **{w["asset"]: root / f"{w['asset']}.mkv" for w in windows}},
        snapshot={
            "step_spec": {
                "source_asset": "film",
                "start": 0,
                "end": 21,
                "fps": str(fps),
                "windows": windows,
            }
        },
    )
    with recorder.recording("shot_assembly"):
        shot_assembly(context)
    hashes["shot_assembly patch"] = file_digest(staging / "patch.mkv")
    hashes["shot_assembly preview"] = file_digest(staging / "preview.mp4")

    master = root / "conform" / "master.mkv"
    patch = {"path": staging / "patch.mkv", "source_asset": "film", "start": 0, "end": 21}
    with pytest.MonkeyPatch.context() as patched, recorder.recording("conform_patches") as media:
        # Three staged parts, the second and third read through the patch.
        patched.setattr(media_conform, "PART_FRAMES", 8)
        media.conform_patches(
            full, [patch], master, codec="ffv1", cancelled=lambda: False, progress=lambda _: None
        )
    hashes["conform_patches master"] = packet_digest(plain, master)
    return recorder.calls, hashes


@pytest.fixture(scope="module")
def recorded(tmp_path_factory):
    return scenario(tmp_path_factory.mktemp("frame-pins"))


def test_decoders_and_writers_pass_their_pinned_arguments(recorded):
    calls, _ = recorded
    pinned = json.loads(GOLDEN.read_text("utf-8"))
    assert list(calls) == list(pinned)
    for label, commands in pinned.items():
        assert calls[label] == commands, label


def test_decoders_and_writers_reproduce_their_pinned_bytes(recorded):
    media = MediaTools()
    version = subprocess.run(
        [str(media.ffmpeg), "-hide_banner", "-version"], capture_output=True, check=True
    ).stdout.decode("utf-8", "replace")
    if not version.startswith(BUILD + " "):
        pytest.skip(f"output hashes were recorded with {BUILD}")
    _, hashes = recorded
    expected = dict(HASHES)
    if os.cpu_count() != PROXY_CORES:
        hashes, expected = (
            {k: v for k, v in d.items() if k != "sampled_proxy"} for d in (hashes, expected)
        )
    assert hashes == expected
