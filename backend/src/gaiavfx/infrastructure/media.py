from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from array import array
from contextlib import contextmanager
from fractions import Fraction
from pathlib import Path

import numpy as np
from PIL import Image, ImageOps

from gaiavfx.shared.errors import ApplicationError
from gaiavfx.shared.telemetry import current_observation, observed


class MediaTools:
    def conform_patches(
        self, source, patches, output, *, codec, cancelled, progress, frame_range=None
    ):
        from gaiavfx.infrastructure.media_conform import conform_patches

        return conform_patches(
            self,
            source,
            patches,
            output,
            codec=codec,
            cancelled=cancelled,
            progress=progress,
            frame_range=frame_range,
        )

    def constant_frame_rate(self, source, start, end, fps):
        """Check frame timestamps in a source range, allowing time-base quantisation."""
        fps = Fraction(fps)
        seek = max(0, int(start / fps) - 1)

        def scan(origin=Fraction(0)):
            return json.loads(
                self.run(
                    self.ffprobe,
                    [
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-read_intervals",
                        f"{float(origin + seek):.12f}%{float(origin + end / fps) + 1:.12f}",
                        "-show_frames",
                        "-show_streams",
                        "-show_entries",
                        "frame=best_effort_timestamp_time:stream=time_base,start_time",
                        "-of",
                        "json",
                        str(source),
                    ],
                )
            )

        data = scan()
        stream = data["streams"][0]
        origin = Fraction(stream.get("start_time", "0"))
        if origin:
            # ffprobe intervals use stream timestamps; frame indices start at zero.
            data = scan(origin)
        tolerance = max(Fraction(stream["time_base"]) * 2, Fraction(1, 1000000))
        times = [
            Fraction(f["best_effort_timestamp_time"]) - origin
            for f in data["frames"]
            if "best_effort_timestamp_time" in f
        ]
        selected = [
            t
            for t in times
            if (Fraction(start) - Fraction(1, 2)) / fps
            <= t
            < (Fraction(end) - Fraction(1, 2)) / fps
        ]
        return len(selected) == end - start and all(
            abs(t - Fraction(frame, 1) / fps) <= tolerance
            for frame, t in zip(range(start, end), selected, strict=True)
        )

    def __init__(self, directory: Path | None = None):
        suffix = ".exe" if os.name == "nt" else ""
        runtime = (
            Path(sys.executable).parent.parent / "media"
            if getattr(sys, "frozen", False)
            else Path(__file__).resolve().parents[4] / ".runtime" / "media"
        )
        directories = (
            [directory]
            if directory is not None
            else [
                *(
                    [Path(os.environ["GAIAVFX_MEDIA_DIR"])]
                    if os.environ.get("GAIAVFX_MEDIA_DIR")
                    else []
                ),
                runtime,
            ]
        )

        def locate(name):
            for folder in directories:
                candidate = folder / (name + suffix)
                if candidate.is_file():
                    return candidate
            # An explicit directory is used by callers to select a particular tool pair.
            found = shutil.which(name) if directory is None else None
            return Path(found) if found else directories[0] / (name + suffix)

        self.ffmpeg, self.ffprobe = locate("ffmpeg"), locate("ffprobe")
        self._processes = set()
        self._process_lock = threading.Lock()
        self._closing = False

    @contextmanager
    def process(self, command, **kwargs):
        kwargs.setdefault("stdin", subprocess.DEVNULL)
        with self._process_lock:
            if self._closing:
                raise ApplicationError("Media processing is stopping.")
            child = subprocess.Popen(
                command,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
                **kwargs,
            )
            self._processes.add(child)
        try:
            with child:
                yield child
        finally:
            with self._process_lock:
                self._processes.discard(child)

    def stop(self):
        with self._process_lock:
            self._closing = True
            processes = list(self._processes)
        for child in processes:
            if child.poll() is None:
                child.terminate()

    @observed("media.run", media_inputs=(), media_outputs=())
    def run(
        self,
        executable: Path,
        args: list[str],
        timeout: int = 180,
        return_log: bool = False,
        cancelled=None,
    ) -> str:
        if not executable.is_file():
            raise ApplicationError(
                "Media tools are missing. Run python -m gaiavfx.setup media install (or npm run setup:media) before importing.",
                503,
            )
        try:
            with self.process(
                [
                    str(executable),
                    *(["-nostdin"] if executable == self.ffmpeg else []),
                    "-protocol_whitelist",
                    "file,pipe",
                    *args,
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            ) as child:
                try:
                    if cancelled is None:
                        stdout, stderr = child.communicate(timeout=timeout)
                    else:
                        deadline = time.monotonic() + timeout
                        while True:
                            if cancelled():
                                child.terminate()
                                child.communicate(timeout=10)
                                raise ApplicationError(
                                    "Media task cancelled; prepared work was retained."
                                )
                            try:
                                stdout, stderr = child.communicate(
                                    timeout=min(0.5, max(0.01, deadline - time.monotonic()))
                                )
                                break
                            except subprocess.TimeoutExpired:
                                if time.monotonic() >= deadline:
                                    raise
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.communicate()
                    raise
                except BaseException:
                    # A durable local runner can raise its shutdown sentinel from
                    # cancelled(). Do not wait for the encoder to finish on unwind.
                    child.kill()
                    child.communicate()
                    raise
                return_code = child.returncode
        except subprocess.TimeoutExpired as exc:
            raise ApplicationError(
                "Media processing timed out. Try a shorter reference range."
            ) from exc
        if return_code:
            current_observation().update(
                output={
                    "return_code": return_code,
                    "stderr": stderr.decode("utf-8", errors="replace"),
                    "stdout": stdout.decode("utf-8", errors="replace"),
                }
            )
            detail = stderr.decode("utf-8", errors="replace")[-1200:]
            raise ApplicationError("This media could not be processed: " + detail, 422)
        return (stderr if return_log else stdout).decode("utf-8", errors="replace")

    @observed("media.sample_frame", media_inputs=("source",), media_outputs=("output",))
    def sample_frame(
        self, source: Path, output: Path, seconds: float, thumbnail_width=None
    ) -> dict:
        log = self.run(
            self.ffmpeg,
            [
                "-v",
                "info",
                "-y",
                *(["-ss", str(max(0, seconds - 1)), "-copyts"] if thumbnail_width else []),
                "-i",
                str(source),
                "-vf",
                f"select=gte(t\\,{seconds}),showinfo"
                + (
                    f",scale={thumbnail_width}:{round(thumbnail_width * 9 / 16)}:force_original_aspect_ratio=decrease,pad={thumbnail_width}:{round(thumbnail_width * 9 / 16)}:(ow-iw)/2:(oh-ih)/2"
                    if thumbnail_width
                    else ""
                ),
                "-frames:v",
                "1",
                str(output),
            ],
            return_log=True,
        )
        match = re.search(r"n:\s*0\s+pts:\s*(-?\d+)\s+pts_time:([\d.e+\-]+)", log)
        if not match:
            raise ApplicationError("The requested video sample could not be identified.")
        return {
            "pts": int(match.group(1)),
            "pts_seconds": match.group(2),
            "requested_seconds": seconds,
        }

    @observed("media.probe", media_inputs=(), media_outputs=())
    def probe(self, source: Path, *, frame_range=None) -> dict:
        try:
            with Image.open(source) as image:
                if image.format in {"PNG", "JPEG", "WEBP", "TIFF", "BMP"}:
                    image = ImageOps.exif_transpose(image)
                    return {
                        "kind": "image",
                        "width": image.width,
                        "height": image.height,
                        "duration": 0,
                        "has_audio": False,
                    }
        except (OSError, ValueError):
            pass
        data = json.loads(
            self.run(
                self.ffprobe,
                ["-v", "error", "-show_streams", "-show_format", "-of", "json", str(source)],
            )
        )
        streams = data.get("streams", [])
        video = next(
            (
                stream
                for stream in streams
                if stream["codec_type"] == "video"
                and not stream.get("disposition", {}).get("attached_pic")
            ),
            None,
        )
        audio = next((stream for stream in streams if stream["codec_type"] == "audio"), None)
        if not video and not audio:
            raise ApplicationError("Choose an image, video or audio file.", 422)
        duration = float(
            (video or audio).get("duration") or data.get("format", {}).get("duration", 0)
        )
        if duration <= 0:
            raise ApplicationError("The source duration could not be determined.", 422)
        fps = (video or {}).get("avg_frame_rate", "0/1")
        result = {
            "kind": "video" if video else "audio",
            "width": (video or {}).get("width", 0),
            "height": (video or {}).get("height", 0),
            "duration": duration,
            "has_audio": bool(audio),
            "frame_rate": fps,
            "video_start_time": (video or {}).get("start_time", "0"),
            "audio_start_time": (audio or {}).get("start_time", "0"),
            "format_start_time": data.get("format", {}).get("start_time", "0"),
            "time_base": (video or audio).get("time_base", "1/1000"),
            "video_codec": (video or {}).get("codec_name"),
            "audio_codec": (audio or {}).get("codec_name"),
            "audio_sample_rate": int((audio or {}).get("sample_rate", 0)),
            "audio_channels": int((audio or {}).get("channels", 0)),
            "audio_duration": float((audio or {}).get("duration") or 0),
            "video_frames": int(video["nb_frames"])
            if video and str(video.get("nb_frames", "")).isdigit()
            else None,
            "size": source.stat().st_size,
        }
        if video and frame_range is not None:
            result["variable_frame_rate"] = not self.constant_frame_rate(source, *frame_range, fps)
        return result

    def film_info(self, path):
        """Validate upright CFR packet timing before any pixel decode (including B-frame order)."""
        data = json.loads(
            self.run(
                self.ffprobe,
                [
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-show_streams",
                    "-show_packets",
                    "-show_entries",
                    "stream=avg_frame_rate,r_frame_rate,time_base:stream_tags=rotate:stream_side_data=rotation:packet=pts",
                    "-of",
                    "json",
                    str(path),
                ],
                timeout=1800,
            )
        )
        stream = next(iter(data.get("streams", [])), {})
        rotations = [
            stream.get("tags", {}).get("rotate", 0),
            *(s.get("rotation", 0) for s in stream.get("side_data_list", [])),
        ]
        if any(float(rotation) % 360 for rotation in rotations):
            raise ApplicationError("rotated video: re-export upright", 422)
        try:
            fps = Fraction(stream["avg_frame_rate"])
            nominal = Fraction(stream["r_frame_rate"])
            tick = Fraction(stream["time_base"])
            if min(fps, nominal, tick) <= 0:
                raise ValueError
        except (KeyError, ValueError, ZeroDivisionError) as exc:
            raise ApplicationError(
                "invalid video frame rate: re-export at a constant frame rate", 422
            ) from exc
        if fps != nominal:
            raise ApplicationError("variable frame rate: re-export at a constant frame rate", 422)
        packets = data.get("packets", [])
        if not packets or any("pts" not in p for p in packets):
            raise ApplicationError(
                "video timestamps unavailable: re-export at a constant frame rate", 422
            )
        timestamps = sorted(int(p["pts"]) for p in packets)
        # One time-base tick permits normal Matroska quantisation at 23.976/24 fps.
        for index, pts in enumerate(timestamps):
            if (index and pts <= timestamps[index - 1]) or abs(
                (pts - timestamps[0]) * tick - index / fps
            ) > tick:
                raise ApplicationError(
                    "variable frame rate: re-export at a constant frame rate", 422
                )
        info = self.probe(path)
        info["frame_rate"] = str(fps)
        return info

    def sampled_proxy(self, source, output, frames, *, width, height, fps):
        """Encode only the selected frames, in order, as a small CFR proxy.

        Frames are chosen in Python from stream_frames (the same decoded-order indices the shot
        detector uses) and piped to the encoder. An ffmpeg select expression cannot hold a
        chunk's ~1200 frame terms: its evaluator runs out of memory.
        """
        del fps  # proxy timing is synthetic (24 fps); map.json carries the film frames
        wanted = set(frames)
        if len(wanted) != len(frames) or frames != sorted(frames):
            raise ValueError("Proxy frames must be sorted and unique")
        output.parent.mkdir(parents=True, exist_ok=True)
        temp = output.with_name(output.stem + ".processing" + output.suffix)
        command = [
            str(self.ffmpeg),
            "-y",
            "-v",
            "error",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "-s",
            f"{width}x{height}",
            "-r",
            "24",
            "-i",
            "pipe:0",
            "-c:v",
            "libx264",
            "-crf",
            "18",
            "-pix_fmt",
            "yuv420p",
            str(temp),
        ]
        import tempfile

        written = 0
        with (
            tempfile.TemporaryFile() as errors,
            self.process(command, stdin=subprocess.PIPE, stderr=errors) as encoder,
        ):
            try:
                for index, frame in self.stream_frames(
                    source, width=width, height=height, start=frames[0], end=frames[-1] + 1
                ):
                    if index in wanted:
                        encoder.stdin.write(frame.tobytes())
                        written += 1
                encoder.stdin.close()
                if encoder.wait(timeout=1800) != 0:
                    errors.seek(0)
                    raise ApplicationError(
                        "Proxy encode failed: " + errors.read()[-1200:].decode("utf-8", "replace"),
                        422,
                    )
            finally:
                if encoder.poll() is None:
                    encoder.kill()
        if written != len(frames):
            temp.unlink(missing_ok=True)
            raise ApplicationError(
                f"Proxy holds {written} of {len(frames)} requested frames: the film ended early.",
                422,
            )
        temp.replace(output)

    def stream_frames(self, path, *, width, height, start=None, end=None):
        """Yield decoded RGB frames in [start, end), with native frame indices and bounded memory."""
        start = 0 if start is None else start
        if (
            type(width) is not int
            or type(height) is not int
            or min(width, height) < 1
            or type(start) is not int
            or start < 0
            or (end is not None and (type(end) is not int or end <= start))
        ):
            raise ValueError("Invalid frame stream dimensions or range")
        trim = f"trim=start_frame={start}" + (f":end_frame={end}" if end is not None else "")
        command = [
            str(self.ffmpeg),
            "-nostdin",
            "-v",
            "error",
            "-protocol_whitelist",
            "file,pipe",
            "-i",
            str(path),
            "-map",
            "0:v:0",
            "-vf",
            f"{trim},scale={width}:{height}:flags=bilinear",
            "-fps_mode",
            "passthrough",
            "-f",
            "rawvideo",
            "-pix_fmt",
            "rgb24",
            "pipe:1",
        ]
        # stderr goes to a file so an error cannot deadlock the stdout pipe.
        import tempfile

        with (
            tempfile.TemporaryFile() as errors,
            self.process(command, stdout=subprocess.PIPE, stderr=errors) as child,
        ):
            try:
                size, index = width * height * 3, start
                while True:
                    data = child.stdout.read(size)
                    if not data:
                        break
                    if len(data) != size:
                        raise ApplicationError("Truncated raw video frame.", 422)
                    yield index, np.frombuffer(data, np.uint8).reshape(height, width, 3)
                    index += 1
                if child.wait() != 0:
                    errors.seek(0)
                    raise ApplicationError(
                        "Frame decode failed: " + errors.read()[-1200:].decode("utf-8", "replace"),
                        422,
                    )
            finally:
                if child.poll() is None:
                    child.terminate()
                    try:
                        child.wait(timeout=10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()

    def scan_video(self, path: Path, *, fps, black=None, frozen=None) -> dict:
        """Count decoded frames and measure detector ranges in one additional decode pass.

        Application supplies the versioned detector thresholds. Defaults match ffmpeg's
        documented parameters; no verdict is inferred here. Duration is picture-only.
        """
        black = black if black is not None else {"d": 0.1, "pic_th": 0.98, "pix_th": 0.1}
        frozen = frozen if frozen is not None else {"n": 0.001, "d": 2.0}
        data = json.loads(
            self.run(
                self.ffprobe,
                ["-v", "error", "-count_frames", "-show_streams", "-of", "json", str(path)],
            )
        )
        streams = data.get("streams", [])
        video = next(
            (
                s
                for s in streams
                if s.get("codec_type") == "video"
                and not s.get("disposition", {}).get("attached_pic")
            ),
            None,
        )
        if video is None:
            raise ApplicationError("No video stream could be decoded.", 422)

        def count(key):
            value = video.get(key)
            return int(value) if str(value).isdigit() else None

        duration = float(video["duration"]) if video.get("duration") not in (None, "N/A") else None
        filters = ",".join(
            name + "=" + ":".join(f"{k}={v}" for k, v in values.items())
            for name, values in (("blackdetect", black), ("freezedetect", frozen))
        )
        log = self.run(
            self.ffmpeg,
            [
                "-v",
                "info",
                "-i",
                str(path),
                "-map",
                "0:v:0",
                "-vf",
                filters,
                "-an",
                "-f",
                "null",
                "-",
            ],
            return_log=True,
        )
        ranges = {}
        for check, prefix in (("black_frames", "black"), ("frozen_frames", "freeze")):
            found, start = [], None
            for event, value in re.findall(prefix + r"_(start|end):\s*([0-9.e+\-]+)", log):
                if event == "start":
                    start = float(value)
                elif start is not None:
                    found.append([start, float(value)])
                    start = None
            if start is not None and duration is not None:
                found.append([start, duration])
            ranges[check] = found
        return {
            "kind": "video",
            "frames_counted": count("nb_read_frames"),
            "frames_declared": count("nb_frames"),
            "duration": duration,
            "width": video.get("width"),
            "height": video.get("height"),
            "has_audio": any(s.get("codec_type") == "audio" for s in streams),
            **ranges,
        }

    @observed("media.thumbnail", media_inputs=("source",), media_outputs=("output",))
    def thumbnail(self, source: Path, output: Path, info: dict):
        output.parent.mkdir(parents=True, exist_ok=True)
        if info["kind"] == "image":
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                image.thumbnail((768, 768))
                image.save(output, "JPEG", quality=88)
        elif info["kind"] == "video":
            self.run(
                self.ffmpeg,
                [
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(source),
                    "-vf",
                    "scale=768:768:force_original_aspect_ratio=decrease",
                    "-frames:v",
                    "1",
                    str(output),
                ],
            )

    @observed("media.preview", media_inputs=("source",), media_outputs=("output",))
    def preview(self, source: Path, output: Path, kind: str):
        output.parent.mkdir(parents=True, exist_ok=True)
        options = ["-v", "error", "-y", "-i", str(source)]
        if kind == "video":
            options += [
                "-vf",
                "scale=1280:720:force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "23",
                "-pix_fmt",
                "yuv420p",
                "-c:a",
                "aac",
                "-movflags",
                "+faststart",
            ]
        else:
            options += ["-vn", "-c:a", "aac", "-movflags", "+faststart"]
        self.run(self.ffmpeg, options + [str(output)], timeout=1800)

    @observed("media.ending_frame", media_inputs=("source",), media_outputs=("output",))
    def ending_frame(self, source: Path, output: Path, start_us: int, end_us: int) -> dict:
        """Capture the last decoded presentation frame inside a visible half-open range."""
        if not 0 <= start_us < end_us:
            raise ApplicationError("Choose a nonempty visible source range.", 422)
        selected, index = None, 0
        command = [
            str(self.ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp_time",
            "-of",
            "compact=p=0",
            str(source),
        ]
        with self.process(
            command, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True
        ) as process:
            for line in process.stdout:
                fields = dict(item.split("=", 1) for item in line.strip().split("|") if "=" in item)
                if "best_effort_timestamp_time" not in fields:
                    continue
                at = round(float(fields["best_effort_timestamp_time"]) * 1e6)
                if at >= end_us:
                    break
                if at >= start_us:
                    selected = index
                index += 1
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
        if selected is None:
            raise ApplicationError("No decoded frame lies inside the visible source range.", 422)
        return {
            **self.frame(source, output, selected),
            "source_start_us": start_us,
            "source_end_us": end_us,
        }

    @observed("media.frame", media_inputs=("source",), media_outputs=("output",))
    def frame(self, source: Path, output: Path, ordinal: int) -> dict:
        if ordinal < 0:
            raise ApplicationError("Frame ordinal must be non-negative.", 422)
        # Select by decoded ordinal, not a keyframe-seek approximation; preserve the original PTS.
        command = [
            str(self.ffprobe),
            "-v",
            "error",
            "-select_streams",
            "v:0",
            "-show_frames",
            "-show_entries",
            "frame=best_effort_timestamp,best_effort_timestamp_time",
            "-of",
            "compact=p=0",
            str(source),
        ]
        selected = None
        with self.process(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        ) as process:
            index = 0
            for line in process.stdout:
                fields = dict(item.split("=", 1) for item in line.strip().split("|") if "=" in item)
                if "best_effort_timestamp" not in fields:
                    continue
                if index == ordinal:
                    selected = fields
                    break
                index += 1
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
        if selected is None:
            raise ApplicationError("The selected frame is outside this source.", 422)
        self.run(
            self.ffmpeg,
            [
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-vf",
                f"select=eq(n\\,{ordinal})",
                "-frames:v",
                "1",
                str(output),
            ],
        )
        return {
            "frame_ordinal": ordinal,
            "pts": int(selected["best_effort_timestamp"]),
            "pts_seconds": selected.get("best_effort_timestamp_time"),
        }

    @observed("media.prepare_reference", media_inputs=("source",), media_outputs=("output",))
    def prepare_reference(
        self,
        source: Path,
        output: Path,
        kind: str,
        start: float,
        end: float | None,
        include_audio: bool,
        crop: dict | None = None,
        audio_rate: int = 48000,
    ):
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.exists():
            return
        staged = output.with_name(output.stem + ".processing" + output.suffix)
        if kind == "image":
            with Image.open(source) as image:
                image = ImageOps.exif_transpose(image).convert("RGB")
                if crop:
                    w, h = image.size
                    box = (
                        round(crop["x"] * w),
                        round(crop["y"] * h),
                        round((crop["x"] + crop["width"]) * w),
                        round((crop["y"] + crop["height"]) * h),
                    )
                    if not (0 <= box[0] < box[2] <= w and 0 <= box[1] < box[3] <= h):
                        raise ApplicationError(
                            "The crop must contain pixels inside the original image.", 422
                        )
                    image = image.crop(box)
                image.save(staged, "PNG")
                staged.replace(output)
            return
        options = ["-v", "error", "-y", "-i", str(source), "-ss", str(start)]
        if end is not None:
            options += ["-t", str(end - start)]
        if kind == "video":
            options += [
                "-vf",
                "fps=24,scale=1024:1024:force_original_aspect_ratio=decrease:force_divisible_by=2",
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-pix_fmt",
                "yuv420p",
            ]
            options += ["-c:a", "aac"] if include_audio else ["-an"]
        else:
            options += ["-vn", "-ar", str(audio_rate), "-ac", "2", "-c:a", "pcm_s16le"]
        self.run(self.ffmpeg, options + [str(staged)], timeout=600)
        staged.replace(output)

    def _wav_samples(self, path: Path, sample_rate: int) -> int:
        stream = json.loads(
            self.run(
                self.ffprobe,
                [
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_streams",
                    "-of",
                    "json",
                    str(path),
                ],
            )
        )["streams"][0]
        if str(stream.get("duration_ts", "")).isdigit():
            return int(stream["duration_ts"])
        return round(Fraction(stream.get("duration") or "0") * sample_rate)

    @observed("media.extract_audio", media_inputs=(), media_outputs=())
    def extract_audio(
        self, source: Path, output: Path, *, sample_rate=48000, offset: Fraction, channels: int
    ) -> dict:
        """The film's first audio stream as float PCM on the picture's clock (ADR 0016): sample 0 is the instant of
        video frame 0. A positive `offset` (audio starts after the picture) prepends silence; a negative one trims
        the audio's head. Mono and stereo keep their channels; anything else is refused."""
        if channels not in (1, 2):
            raise ApplicationError(
                f"This film's audio has {channels} channels; dialogue analysis supports mono and stereo for now.",
                422,
            )
        shift = Fraction(offset) * sample_rate + Fraction(1, 2)
        shift = shift.numerator // shift.denominator
        layout = "mono" if channels == 1 else "stereo"
        chain = (
            f"aformat=sample_fmts=flt:channel_layouts={layout},"
            f"aresample={sample_rate},asetpts=PTS-STARTPTS"
        )
        if shift > 0:
            chain += f",adelay={shift}S:all=1"
        elif shift < 0:
            chain += f",atrim=start_sample={-shift},asetpts=PTS-STARTPTS"
        staged = output.with_name(output.stem + ".writing.wav")
        self.run(
            self.ffmpeg,
            [
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-map",
                "0:a:0",
                "-af",
                chain,
                "-c:a",
                "pcm_f32le",
                "-rf64",
                "auto",
                str(staged),
            ],
            timeout=3600,
        )
        samples = self._wav_samples(staged, sample_rate)
        staged.replace(output)
        return {
            "samples": samples,
            "channels": channels,
            "offset_samples": shift,
            "sample_rate": sample_rate,
        }

    @observed("media.decode_audio", media_inputs=(), media_outputs=())
    def decode_audio(self, source: Path, output: Path, *, sample_rate=48000, mono=True) -> dict:
        """Raw little-endian float32 PCM of the first audio stream; `mono` averages the channels (an equal-weight
        mean, as a worker's `.mean(axis=1)`, not FFmpeg's -ac 1 downmix, which scales by 1/sqrt(2))."""
        args = ["-v", "error", "-y", "-i", str(source), "-map", "0:a:0"]
        channels = self.probe(source).get("audio_channels") or 1
        if mono and channels > 1:
            weight = f"{1 / channels:.10f}"
            mix = "+".join(f"{weight}*c{i}" for i in range(channels))
            args += ["-af", f"pan=mono|c0={mix}"]
        channels = 1 if mono else channels
        args += ["-ar", str(sample_rate), "-c:a", "pcm_f32le", "-f", "f32le"]
        staged = output.with_name(output.name + ".writing")
        self.run(self.ffmpeg, args + [str(staged)], timeout=3600)
        staged.replace(output)
        return {"samples": output.stat().st_size // (4 * channels), "channels": channels}

    @observed("media.compose_audio", media_inputs=("video", "source"), media_outputs=("output",))
    def compose_audio(self, video, source, output, policy, frames, cancelled):
        """Preserve picture packets; assemble a documented source soundtrack outside diffusion."""
        info = self.probe(source)
        duration = frames / 24
        start = policy.get("start_us", 0) / 1e6
        end = (policy.get("end_us") or round(info["duration"] * 1e6)) / 1e6
        offset = policy.get("offset_us", 0) / 1e6
        with source.open("rb") as stream:
            source_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        direct = (
            policy["mode"] == "replace"
            and start == 0
            and policy.get("end_us") is None
            and offset == 0
            and policy.get("gain_db", 0) == 0
            and abs(info["duration"] - duration) < 1 / max(info.get("audio_sample_rate", 48000), 1)
            and info.get("audio_codec") in {"aac", "alac", "mp3"}
        )
        staged = output.with_name(output.stem + ".processing.mp4")
        if direct:
            self.run(
                self.ffmpeg,
                [
                    "-v",
                    "error",
                    "-y",
                    "-i",
                    str(video),
                    "-i",
                    str(source),
                    "-map",
                    "0:v:0",
                    "-map",
                    "1:a:0",
                    "-c:v",
                    "copy",
                    "-c:a",
                    "copy",
                    "-movflags",
                    "+faststart",
                    str(staged),
                ],
                cancelled=cancelled,
                timeout=600,
            )
            staged.replace(output)
            return {
                "mode": policy["mode"],
                "operation": "stream_copy",
                "source_sha256": source_hash,
                "policy": policy,
            }
        master = output.with_name("audio-master.f32le")
        desired = frames * 2000
        source_filter = f"atrim=start={start}:end={end},asetpts=PTS-STARTPTS,aresample=48000,aformat=channel_layouts=stereo,volume={policy.get('gain_db', 0)}dB,adelay={round(offset * 48000)}S:all=1,apad,atrim=end_sample={desired}"
        options = ["-v", "error", "-y", "-i", str(video), "-i", str(source)]
        if policy["mode"] == "mix":
            filters = f"[1:a:0]{source_filter}[source];[0:a:0]aresample=48000,aformat=channel_layouts=stereo,volume={policy.get('generated_gain_db', 0)}dB,apad,atrim=end_sample={desired}[native];[native][source]amix=inputs=2:normalize=0:duration=longest,atrim=end_sample={desired}[out]"
        else:
            filters = f"[1:a:0]{source_filter}[out]"
        self.run(
            self.ffmpeg,
            options
            + [
                "-filter_complex",
                filters,
                "-map",
                "[out]",
                "-c:a",
                "pcm_f32le",
                "-f",
                "f32le",
                str(master),
            ],
            timeout=600,
            cancelled=cancelled,
        )
        data = array("f")
        with master.open("rb") as stream:
            data.fromfile(stream, master.stat().st_size // 4)
        if sys.byteorder != "little":
            data.byteswap()
        if len(data) != desired * 2:
            raise ApplicationError("The soundtrack did not match the planned stereo sample count.")
        peak = max((abs(v) for v in data), default=0)
        if peak > 1.001:
            raise ApplicationError(
                "This audio mix would clip. Lower the source or generated-audio gain, then create a revised candidate reusing these child takes."
            )
        with master.open("rb") as stream:
            pcm_hash = hashlib.file_digest(stream, "sha256").hexdigest()
        wav = master.with_suffix(".wav")
        self.run(
            self.ffmpeg,
            [
                "-v",
                "error",
                "-y",
                "-f",
                "f32le",
                "-ar",
                "48000",
                "-ac",
                "2",
                "-i",
                str(master),
                "-c:a",
                "pcm_f32le",
                str(wav),
            ],
            timeout=600,
            cancelled=cancelled,
        )
        self.run(
            self.ffmpeg,
            [
                "-v",
                "error",
                "-y",
                "-i",
                str(video),
                "-i",
                str(wav),
                "-map",
                "0:v:0",
                "-map",
                "1:a:0",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(staged),
            ],
            timeout=600,
            cancelled=cancelled,
        )
        staged.replace(output)
        return {
            "mode": policy["mode"],
            "operation": "pcm_mix" if policy["mode"] == "mix" else "pcm_replace",
            "source_sha256": source_hash,
            "pcm_sha256": pcm_hash,
            "sample_rate": 48000,
            "samples": desired,
            "channels": 2,
            "peak": peak,
            "silence_padding_samples": max(0, desired - round((end - start + offset) * 48000)),
            "master_path": str(wav),
            "policy": policy,
        }

    @observed("media.prepare_continuation", media_inputs=("source",), media_outputs=("output",))
    def prepare_continuation(
        self,
        source: Path,
        output: Path,
        start_us: int,
        end_us: int,
        width: int,
        height: int,
        context_frames: int,
        audio_frames: int,
    ):
        output.parent.mkdir(parents=True, exist_ok=True)
        if output.is_file():
            return
        # Normalize on a frame grid before selecting the tail. A container's
        # rounded 5.167 s duration must not drop frame 114 of a 124-frame clip.
        end_frame = round(end_us * 24 / 1e6)
        start_frame = max(round(start_us * 24 / 1e6), end_frame - max(context_frames, audio_frames))
        staged = output.with_name(output.stem + ".processing.mkv")
        video_filter = (
            f"fps=24,trim=start_frame={start_frame}:end_frame={end_frame},setpts=PTS-STARTPTS,"
            f"scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2"
        )
        audio_filter = f"aresample=48000,apad,atrim=start_sample={start_frame * 2000}:end_sample={end_frame * 2000},asetpts=N/SR/TB"
        self.run(
            self.ffmpeg,
            [
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-vf",
                video_filter,
                "-af",
                audio_filter,
                "-c:v",
                "ffv1",
                "-pix_fmt",
                "yuv444p",
                "-c:a",
                "pcm_s16le",
                "-ar",
                "48000",
                str(staged),
            ],
            timeout=600,
        )
        counted = json.loads(
            self.run(
                self.ffprobe,
                [
                    "-v",
                    "error",
                    "-select_streams",
                    "v:0",
                    "-count_frames",
                    "-show_entries",
                    "stream=nb_read_frames",
                    "-of",
                    "json",
                    str(staged),
                ],
            )
        )
        if int(counted["streams"][0]["nb_read_frames"]) < context_frames:
            raise ApplicationError(
                "The prepared source tail has too few frames for this context window."
            )
        staged.replace(output)

    @observed("media.export_sequence", media_inputs=(), media_outputs=("output",))
    def export_sequence(self, items, output: Path, width: int, height: int, cancelled, progress):
        output.parent.mkdir(parents=True, exist_ok=True)
        parts, total_frames = [], 0
        prepared_parts = {}
        for index, item in enumerate(items):
            if cancelled():
                raise ApplicationError("Movie export stopped.")
            target = output.parent / f"part-av2-{index:05d}.mkv"
            frames = max(1, round((item["end_us"] - item["start_us"]) * 24 / 1e6))
            start_frame = round(item["start_us"] * 24 / 1e6)
            duration = frames / 24
            total_frames += frames
            identity = (
                str(item["path"]),
                item["start_us"],
                item["end_us"],
                item.get("volume_db", 0),
                item.get("muted", False),
            )
            if identity in prepared_parts:
                parts.append((prepared_parts[identity], frames))
                progress((index + 1) / (len(items) + 1))
                continue
            if not target.exists():
                temp = target.with_name(target.stem + ".processing.mkv")
                audio_start = start_frame * 2000 if item["has_audio"] else 0
                audio_filter = (
                    f"aresample=48000,atrim=start_sample={audio_start}:end_sample={audio_start + frames * 2000},asetpts=N/SR/TB,"
                    + ("volume=0" if item.get("muted") else f"volume={item.get('volume_db', 0)}dB")
                    + f",apad,atrim=end_sample={frames * 2000},asetpts=N/SR/TB"
                )
                if item.get("kind") == "gap":
                    options = [
                        "-v",
                        "error",
                        "-y",
                        "-f",
                        "lavfi",
                        "-i",
                        f"color=c=black:s={width}x{height}:r=24",
                    ]
                elif item.get("kind") == "image":
                    options = [
                        "-v",
                        "error",
                        "-y",
                        "-loop",
                        "1",
                        "-framerate",
                        "24",
                        "-i",
                        str(item["path"]),
                    ]
                else:
                    options = ["-v", "error", "-y", "-i", str(item["path"])]
                if not item["has_audio"]:
                    options += ["-f", "lavfi", "-i", "anullsrc=r=48000:cl=stereo"]
                options += [
                    "-t",
                    str(duration),
                    "-map",
                    "0:v:0",
                    "-map",
                    "0:a:0" if item["has_audio"] else "1:a:0",
                    "-vf",
                    f"fps=24,trim=start_frame={start_frame}:end_frame={start_frame + frames},setpts=N/(24*TB),scale={width}:{height}:force_original_aspect_ratio=decrease,pad={width}:{height}:(ow-iw)/2:(oh-ih)/2,setsar=1",
                    "-af",
                    audio_filter,
                    "-frames:v",
                    str(frames),
                    "-c:v",
                    "libx264",
                    "-preset",
                    "ultrafast",
                    "-crf",
                    "0",
                    "-pix_fmt",
                    "yuv420p",
                    "-c:a",
                    "pcm_s16le",
                    "-ar",
                    "48000",
                    "-ac",
                    "2",
                    str(temp),
                ]
                self.run(self.ffmpeg, options, timeout=1800, cancelled=cancelled)
                temp.replace(target)
            parts.append((target, frames))
            prepared_parts[identity] = target
            progress((index + 1) / (len(items) + 1))
        # Paths in this generated list are fixed local part names, never user text.
        listing = output.parent / "sequence.txt"
        listing.write_text(
            "\n".join(f"file '{part.name}'\nduration {frames / 24:.12f}" for part, frames in parts),
            encoding="utf-8",
        )
        temporary = output.with_name(output.stem + ".processing.mp4")
        # Encode AAC once across the entire sequence so encoder priming cannot
        # accumulate at every segment boundary. Rebuild both clocks from counts.
        self.run(
            self.ffmpeg,
            [
                "-v",
                "error",
                "-y",
                "-f",
                "concat",
                "-safe",
                "1",
                "-i",
                str(listing),
                "-vf",
                "fps=24,setpts=N/(24*TB)",
                "-af",
                f"asetpts=N/SR/TB,apad,atrim=end_sample={total_frames * 2000}",
                "-frames:v",
                str(total_frames),
                "-t",
                str(total_frames / 24),
                "-c:v",
                "libx264",
                "-preset",
                "veryfast",
                "-crf",
                "18",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-movflags",
                "+faststart",
                str(temporary),
            ],
            timeout=3600,
            cancelled=cancelled,
        )
        if cancelled():
            raise ApplicationError("Movie export stopped; prepared parts are retained.")
        temporary.replace(output)
        for part, _frames in set(parts):
            part.unlink(missing_ok=True)
