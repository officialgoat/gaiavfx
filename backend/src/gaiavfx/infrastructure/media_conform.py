"""Streaming, resumable film conform. No inference or application dependencies."""

import hashlib
import json
import subprocess
import tempfile
from contextlib import ExitStack, closing, contextmanager, suppress
from fractions import Fraction
from itertools import pairwise
from pathlib import Path

import numpy as np

from gaiavfx.infrastructure.conform_pixels import NativePixels
from gaiavfx.shared.errors import ApplicationError

PART_FRAMES = 240


def streams(media, path):
    return json.loads(
        media.run(
            media.ffprobe,
            ["-v", "error", "-show_streams", "-show_format", "-of", "json", str(path)],
        )
    )


def packet_count(media, path):
    data = json.loads(
        media.run(
            media.ffprobe,
            [
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_packets",
                "-show_entries",
                "stream=nb_read_packets",
                "-of",
                "json",
                str(path),
            ],
            timeout=1800,
        )
    )
    return int(data["streams"][0]["nb_read_packets"])


def check_cancel(cancelled):
    if cancelled():
        raise ApplicationError("Conform cancelled; completed parts were retained.")


@contextmanager
def pipe(media, args, *, writing=False):
    with (
        tempfile.TemporaryFile() as errors,
        media.process(
            [
                str(media.ffmpeg),
                "-nostdin",
                "-v",
                "error",
                "-threads",
                "1",
                "-filter_threads",
                "1",
                "-protocol_whitelist",
                "file,pipe",
                *args,
            ],
            stdin=subprocess.PIPE if writing else subprocess.DEVNULL,
            stdout=subprocess.DEVNULL if writing else subprocess.PIPE,
            stderr=errors,
        ) as child,
    ):
        try:
            yield child.stdin if writing else child.stdout
            if writing:
                child.stdin.close()
            if child.wait(timeout=180):
                errors.seek(0)
                raise ApplicationError(
                    "Conform media failed: " + errors.read()[-1600:].decode("utf-8", "replace")
                )
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            if writing:
                with suppress(OSError):
                    child.stdin.close()


def frames(media, path, pix_fmt, size, start=0, end=None):
    trim = f"trim=start_frame={start}" + (f":end_frame={end}" if end is not None else "")
    with pipe(
        media,
        [
            "-noautorotate",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vf",
            trim,
            "-an",
            "-fps_mode",
            "passthrough",
            "-pix_fmt",
            pix_fmt,
            "-threads:v",
            "1",
            "-f",
            "rawvideo",
            "pipe:1",
        ],
    ) as output:
        count = 0
        while data := output.read(size):
            if len(data) != size:
                raise ApplicationError("Incomplete conform frame.", 422)
            count += 1
            yield data
        if end is not None and count != end - start:
            raise ApplicationError("Conform source or patch is shorter than its frozen range.", 422)


def patch_info(media, entry, staging, index, native, fps):
    path = Path(entry["path"])
    probe = streams(media, path)
    attachments = [s for s in probe["streams"] if s["codec_type"] == "attachment"]
    if len(attachments) != 1 or attachments[0].get("tags", {}).get("filename") != "patch.json":
        raise ApplicationError("Replacement patch needs its patch.json attachment.", 422)
    metadata = staging / f"patch-{index}.json"
    media.run(
        media.ffmpeg,
        [
            "-y",
            "-dump_attachment:t:0",
            str(metadata),
            "-i",
            str(path),
            "-t",
            "0",
            "-f",
            "null",
            "-",
        ],
    )
    spec = json.loads(metadata.read_text(encoding="utf-8"))
    rect = spec.get("rect", [])
    if (
        len(rect) != 4
        or any(type(x) is not int for x in rect)
        or not 0 <= rect[0] < rect[2] <= native.width
        or not 0 <= rect[1] < rect[3] <= native.height
        or type(spec.get("start")) is not int
        or type(spec.get("end")) is not int
        or not 0 <= spec["start"] < spec["end"]
        or spec.get("fps") != [fps.numerator, fps.denominator]
    ):
        raise ApplicationError("Replacement patch geometry or timing does not match the film.", 422)
    for key in ("source_asset", "start", "end"):
        if key in entry and entry[key] != spec.get(key):
            raise ApplicationError("Replacement patch does not belong to this source shot.", 422)
    video = next(s for s in probe["streams"] if s["codec_type"] == "video")
    if (
        video["codec_name"] != "ffv1"
        or video["pix_fmt"] != "bgra"
        or (video["width"], video["height"]) != (rect[2] - rect[0], rect[3] - rect[1])
    ):
        raise ApplicationError(
            "Replacement patch must be lossless FFV1 BGRA at its rectangle size.", 422
        )
    return {**spec, "path": path}


def color_args(stream):
    result = []
    for key, option in (
        ("color_space", "colorspace"),
        ("color_range", "color_range"),
        ("color_transfer", "color_trc"),
        ("color_primaries", "color_primaries"),
        ("chroma_location", "chroma_sample_location"),
    ):
        if stream.get(key) not in {None, "unknown", "unspecified"}:
            result += ["-" + option, stream[key]]
    return result


def conform_patches(
    media, source, patches, output, *, codec, cancelled, progress, frame_range=None
):
    """Patches are paths with optional expected source_asset/start/end identities.

    FFV1 is an internal lossless verification codec, not a public API choice.
    Completed native lossless parts survive cancellation/restart. Only atomically
    renamed parts are reusable; an identity manifest prevents stale part reuse.
    """
    if codec not in {"h264", "prores", "ffv1"}:
        raise ApplicationError("Unknown conform codec.", 422)
    source, output = Path(source), Path(output)
    if source.resolve() == output.resolve():
        raise ApplicationError("Conform cannot overwrite its source.", 422)
    check_cancel(cancelled)
    probe = streams(media, source)
    stream = next(s for s in probe["streams"] if s["codec_type"] == "video")
    native = NativePixels(stream)
    fps = Fraction(stream["avg_frame_rate"])
    # Packet inspection establishes exact frame count without another pixel decode.
    media.film_info(source)
    count = packet_count(media, source)
    start, end = (0, count) if frame_range is None else (frame_range["start"], frame_range["end"])
    if type(start) is not int or type(end) is not int or not 0 <= start < end <= count:
        raise ApplicationError("Conform range is outside the source film.", 422)
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = output.parent / (output.stem + "-parts")
    staging.mkdir(exist_ok=True)
    entries = [e if isinstance(e, dict) else {"path": e} for e in patches]
    identity = {
        "schema": 1,
        "range": [start, end],
        "codec": codec,
        "parts": PART_FRAMES,
        "files": [
            [str(p.resolve()), p.stat().st_size, p.stat().st_mtime_ns]
            for p in [source, *(Path(e["path"]) for e in entries)]
        ],
    }
    digest = hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()
    manifest = staging / "identity"
    if manifest.exists() and manifest.read_text() != digest:
        raise ApplicationError("Conform staging belongs to different frozen inputs.", 409)
    manifest.write_text(digest)
    specs = [patch_info(media, e, staging, i, native, fps) for i, e in enumerate(entries)]
    specs.sort(key=lambda s: s["start"])
    if any(s["end"] > count for s in specs) or any(
        a["end"] > b["start"] for a, b in pairwise(specs)
    ):
        raise ApplicationError("Conform patches overlap or exceed the film.", 422)
    parts = [
        (a, min(a + PART_FRAMES, end), staging / f"part-{a:09d}.mkv")
        for a in range(start, end, PART_FRAMES)
    ]
    # Decode once per invocation, only when parts remain. Recovery can skip the
    # completed prefix while retaining frame-index (rather than timestamp) trims.
    missing = next((a for a, _, p in parts if not p.exists()), None)
    if missing is not None:
        with closing(frames(media, source, native.format, native.size, missing, end)) as original:
            for a, b, target in parts:
                if a < missing:
                    continue
                check_cancel(cancelled)
                if target.exists():
                    for _ in range(a, b):
                        next(original)
                    continue
                temp = target.with_suffix(".writing.mkv")
                sar = stream.get("sample_aspect_ratio", "1:1").replace(":", "/")
                if sar in {"N/A", "0/1"}:
                    sar = "1/1"
                with ExitStack() as stack:
                    active = []
                    for spec in specs:
                        lo, hi = max(a, spec["start"]), min(b, spec["end"])
                        if lo < hi:
                            rect = spec["rect"]
                            size = (rect[2] - rect[0]) * (rect[3] - rect[1]) * 4
                            reader = stack.enter_context(
                                closing(
                                    frames(
                                        media,
                                        spec["path"],
                                        "rgba",
                                        size,
                                        lo - spec["start"],
                                        hi - spec["start"],
                                    )
                                )
                            )
                            active.append((spec, lo, hi, reader))
                    writer = stack.enter_context(
                        pipe(
                            media,
                            [
                                "-y",
                                "-f",
                                "rawvideo",
                                "-pix_fmt",
                                native.format,
                                "-s",
                                f"{native.width}x{native.height}",
                                "-r",
                                str(fps),
                                *color_args(stream),
                                "-i",
                                "pipe:0",
                                "-vf",
                                f"setsar={sar}",
                                "-c:v",
                                "ffv1",
                                "-level",
                                "3",
                                "-threads:v",
                                "1",
                                *color_args(stream),
                                str(temp),
                            ],
                            writing=True,
                        )
                    )
                    for index in range(a, b):
                        check_cancel(cancelled)
                        data = next(original)
                        for spec, lo, hi, reader in active:
                            if lo <= index < hi:
                                rect = spec["rect"]
                                rgba = np.frombuffer(next(reader), np.uint8).reshape(
                                    rect[3] - rect[1], rect[2] - rect[0], 4
                                )
                                data = native.paste(data, rgba, rect)
                        writer.write(data)
                    # Exhaust generators to check decoder exit status/count.
                    for _, _, _, reader in active:
                        if next(reader, None) is not None:
                            raise ApplicationError("Unexpected patch frames.")
                temp.replace(target)
                progress((b - start) / (end - start) * 0.85)
            if next(original, None) is not None:
                raise ApplicationError("Unexpected source frames.")
    check_cancel(cancelled)
    listing = staging / "parts.txt"
    listing.write_text(
        "".join(
            f"file '{p.name}'\nduration {float(Fraction(b - a) / fps):.12f}\n" for a, b, p in parts
        )
    )
    origin = Fraction(stream.get("start_time", "0")) - Fraction(
        probe.get("format", {}).get("start_time", "0")
    )
    args = [
        "-y",
        *(["-itsoffset", f"{float(origin):.12f}"] if frame_range is None and origin else []),
        "-f",
        "concat",
        "-safe",
        "1",
        "-i",
        str(listing),
        "-i",
        str(source),
        "-map",
        "0:v:0",
        "-map",
        "1:a?",
        "-map_metadata",
        "1",
        "-fps_mode",
        "passthrough",
    ]
    if codec == "ffv1":
        args += ["-c:v", "ffv1", "-level", "3"]
    elif codec == "prores":
        args += ["-c:v", "prores_ks", "-profile:v", "3", "-pix_fmt", "yuv422p10le"]
    else:
        fmt = native.format
        supported = {
            "yuv420p": "high",
            "yuv420p10le": "high10",
            "yuv422p": "high422",
            "yuv422p10le": "high422",
            "yuv444p": "high444",
            "yuv444p10le": "high444",
        }
        args += [
            "-c:v",
            "libx264",
            "-crf",
            "14",
            "-profile:v",
            supported.get(fmt, "high"),
            "-pix_fmt",
            fmt if fmt in supported else "yuv420p",
        ]
    if frame_range is not None:
        # Trim in the source container timeline, rounded by atrim to sample boundaries.
        args += [
            "-af",
            f"atrim=start={float(origin + start / fps):.12f}:end={float(origin + end / fps):.12f},asetpts=PTS-({float(origin + start / fps):.12f})/TB",
        ]
        for index, audio in enumerate(s for s in probe["streams"] if s["codec_type"] == "audio"):
            fmt = audio.get("sample_fmt", "")
            pcm = (
                "pcm_f64le"
                if fmt.startswith("dbl")
                else "pcm_f32le"
                if fmt.startswith("flt")
                else "pcm_s32le"
            )
            args += [f"-c:a:{index}", pcm]
    else:
        args += ["-c:a", "copy"]
    temp = output.with_suffix(".writing.mkv")
    media.run(
        media.ffmpeg, [*args, *color_args(stream), str(temp)], timeout=86400, cancelled=cancelled
    )
    if packet_count(media, temp) != end - start:
        raise ApplicationError("Conform output frame count differs from its frozen range.", 422)
    check_cancel(cancelled)
    temp.replace(output)
    progress(1)
