"""Timestamp-faithful, bounded frame sampling for assistant observations."""

import base64
import hashlib
import json
import subprocess
import tempfile
from pathlib import Path

from gaiavfx.shared.errors import ApplicationError, ObservationReadError

SAMPLING_VERSION = "source-pts-1fps-boundaries-1"
VIDEO_VERSION = "silent-mp4-crop-768-4fps-2"


def native_video_parts(
    media, source, folder, start_us, end_us, crop, cancelled, maximum_seconds=12
):
    """Private, bounded video payloads with portable, source-relative provenance."""
    if maximum_seconds < 2 or not 0 <= start_us < end_us:
        raise ApplicationError(
            "The selected native video cannot fit this connection's context.", 422
        )
    folder.mkdir(parents=True, exist_ok=True)
    offset = start_us
    while offset < end_us:
        if cancelled():
            raise ApplicationError("Reference analysis stopped.")
        stop = min(end_us, offset + maximum_seconds * 1000000)
        name = f"{VIDEO_VERSION}-{offset}-{stop}.mp4"
        target = folder / name
        if not target.is_file():
            filters = []
            if crop:
                filters.append(
                    f"crop=iw*{crop['width']}:ih*{crop['height']}:iw*{crop['x']}:ih*{crop['y']}"
                )
            filters.append(
                "scale=768:768:force_original_aspect_ratio=decrease:force_divisible_by=2"
            )
            filters.append("fps=4")
            temporary = target.with_suffix(".tmp.mp4")
            command = [
                str(media.ffmpeg),
                "-v",
                "error",
                "-y",
                "-i",
                str(source),
                "-ss",
                str(offset / 1e6),
                "-t",
                str((stop - offset) / 1e6),
                "-map",
                "0:v:0",
                "-an",
                "-vf",
                ",".join(filters),
                "-fps_mode",
                "vfr",
                "-c:v",
                "libx264",
                "-preset",
                "fast",
                "-crf",
                "20",
                "-pix_fmt",
                "yuv420p",
                "-movflags",
                "+faststart",
                str(temporary),
            ]
            media.run(media.ffmpeg, command[1:], cancelled=cancelled)
            temporary.replace(target)
        data = target.read_bytes()
        if len(data) > 20 * 1024 * 1024:
            raise ApplicationError(
                "The native video clip exceeds the 20 MB request limit. Choose a shorter range or sampled frames.",
                422,
            )
        metadata = {
            "version": VIDEO_VERSION,
            "artifact": name,
            "sha256": hashlib.sha256(data).hexdigest(),
            "source_start_us": offset,
            "source_end_us": stop,
            "crop": crop,
            "duration_seconds": (stop - offset) / 1e6,
            "audio_included": False,
            "sample_rate_fps": 4,
        }
        yield {
            "type": "video_url",
            "video_url": {"url": "data:video/mp4;base64," + base64.b64encode(data).decode()},
            "_gaiavfx_media": metadata,
        }
        if stop == end_us:
            break
        offset = stop - 1000000


def sample_video(media, source: Path, folder: Path, start_us, end_us, cancelled, target_us=()):
    if not 0 <= start_us < end_us:
        raise ApplicationError("Choose a nonempty source range for observation.", 422)
    folder.mkdir(parents=True, exist_ok=True)
    selected, last, next_us = [], None, start_us
    targets = sorted({at for at in target_us if start_us <= at < end_us})
    target_index = 0
    # A bounded interval: the bundled ffprobe intermittently printed no frames at all
    # (exit 0, empty stderr) for an open-ended "start%" seek, about 1 run in 25.
    # The end is one second past the range; frames at or after end_us are skipped below.
    command = [
        str(media.ffprobe),
        "-v",
        "error",
        "-select_streams",
        "v:0",
        "-read_intervals",
        f"{start_us / 1e6}%{end_us / 1e6 + 1}",
        "-show_frames",
        "-show_entries",
        "frame=best_effort_timestamp,best_effort_timestamp_time",
        "-of",
        "compact=p=0",
        str(source),
    ]
    with (
        tempfile.TemporaryFile(mode="w+t", encoding="utf-8") as errors,
        media.process(command, stdout=subprocess.PIPE, stderr=errors, text=True) as process,
    ):
        try:
            for line in process.stdout:
                if cancelled():
                    raise ApplicationError("Reference analysis stopped.")
                fields = dict(x.split("=", 1) for x in line.strip().split("|") if "=" in x)
                if "best_effort_timestamp_time" not in fields:
                    continue
                at = round(float(fields["best_effort_timestamp_time"]) * 1e6)
                if at >= end_us:
                    break
                if at < start_us:
                    continue
                frame = {
                    "pts": int(fields["best_effort_timestamp"]),
                    "pts_seconds": fields["best_effort_timestamp_time"],
                    "source_us": at,
                }
                last = frame
                targeted = target_index < len(targets) and at >= targets[target_index]
                if at >= next_us or targeted:
                    selected.append(frame)
                    if at >= next_us:
                        next_us = start_us + ((at - start_us) // 1000000 + 1) * 1000000
                while target_index < len(targets) and at >= targets[target_index]:
                    target_index += 1
        finally:
            if process.poll() is None:
                process.terminate()
            process.wait(timeout=10)
        errors.seek(0)
        probe_error = errors.read()[-1200:].strip()
    if last is None:
        if probe_error:
            raise ApplicationError("The selected video could not be decoded: " + probe_error, 422)
        raise ObservationReadError("No decodable video frame lies inside the selected range.", 422)
    if not selected or selected[-1]["pts"] != last["pts"]:
        selected.append(last)
    selected = list({f["pts"]: f for f in selected}.values())
    for offset in range(0, len(selected), 32):
        if cancelled():
            raise ApplicationError("Reference analysis stopped.")
        batch = selected[offset : offset + 32]
        expression = "+".join(f"eq(pts\\,{f['pts']})" for f in batch)
        # Input seek keeps native timestamps; each bounded expression selects
        # exact probed PTS rather than assuming the movie's 24 fps source rate.
        media.run(
            media.ffmpeg,
            [
                "-v",
                "error",
                "-y",
                "-ss",
                str(max(0, float(batch[0]["pts_seconds"]) - 1)),
                "-copyts",
                "-i",
                str(source),
                "-vf",
                "select=" + expression,
                "-fps_mode",
                "passthrough",
                "-frames:v",
                str(len(batch)),
                "-start_number",
                str(offset),
                "-q:v",
                "2",
                str(folder / "raw-%d.jpg"),
            ],
        )
        if any(not (folder / f"raw-{offset + i}.jpg").is_file() for i in range(len(batch))):
            raise ApplicationError(
                "The selected source frames could not all be decoded. No incomplete observation was accepted.",
                422,
            )
    return [
        {**frame, "index": i, "path": str(folder / f"raw-{i}.jpg")}
        for i, frame in enumerate(selected)
    ]


def overlapping_batches(frames, maximum):
    if maximum < 1 or (len(frames) > 1 and maximum < 2):
        raise ApplicationError(
            "Increase the observation context to fit at least two ordered frames.", 422
        )
    offset = 0
    while offset < len(frames):
        batch = frames[offset : offset + maximum]
        yield batch
        if offset + maximum >= len(frames):
            break
        offset += maximum - 1


def cached_samples(media, source, folder, start_us, end_us, cancelled, target_us=()):
    suffix = (
        "-" + hashlib.sha256(json.dumps(sorted(set(target_us))).encode()).hexdigest()[:16]
        if target_us
        else ""
    )
    # A distinct directory keeps the raw frame filenames independent as well.
    folder = folder / ("targeted" + suffix) if suffix else folder
    record = folder / "samples.json"
    if record.is_file():
        frames = json.loads(record.read_text(encoding="utf-8"))
        frames = [{**f, "path": str(folder / f"raw-{f['index']}.jpg")} for f in frames]
        if all(Path(f["path"]).is_file() for f in frames):
            return frames
    frames = sample_video(media, source, folder, start_us, end_us, cancelled, target_us)
    temporary = folder / "samples.tmp"
    temporary.write_text(
        json.dumps([{k: v for k, v in f.items() if k != "path"} for f in frames]), encoding="utf-8"
    )
    temporary.replace(record)
    return frames
