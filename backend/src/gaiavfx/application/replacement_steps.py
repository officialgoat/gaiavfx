"""Deterministic shot analysis and lossless replacement pixel steps (ADR 0011)."""

import hashlib
import io
import json
import math
import subprocess
import tempfile
from contextlib import closing, contextmanager, suppress
from fractions import Fraction

import numpy as np

from gaiavfx.integrations import minimax_h3
from gaiavfx.modules.direction.head import head_track
from gaiavfx.modules.direction.replacement import (
    CHECKS,
    Arm,
    Box,
    CheckResult,
    CropPlan,
    Letterbox,
    ReplacementFacts,
    calibrated_check,
    from_record,
    parse_replacement_policy,
    presence,
    to_record,
)
from gaiavfx.shared.errors import ApplicationError

# ADR 0011 / A2 object classification: A1's policy parser has closed keys.
OBJECT_OVERLAP_FRACTION = 0.20
OBJECT_DILATE_PX = 3  # default; the frozen policy's existing dilate overrides it


def _pipe(media, args, data=None):
    with media.process(
        [
            str(media.ffmpeg),
            "-v",
            "error",
            "-nostdin",
            "-protocol_whitelist",
            "file,pipe",
            "-threads",
            "2",
            *args,
        ],
        stdin=subprocess.PIPE if data is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as child:
        try:
            out, error = child.communicate(data, timeout=180)
        except subprocess.TimeoutExpired:
            child.kill()
            child.communicate()
            raise ApplicationError("Replacement media processing timed out.") from None
        if child.returncode:
            raise ApplicationError(error.decode("utf-8", errors="replace")[-1200:])
        return out


def _decode_spec(media, path, start, end, alpha=False):
    info = media.probe(path)
    probe = json.loads(
        media.run(
            media.ffprobe,
            [
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=pix_fmt,color_space,color_range,start_time:format=start_time",
                "-of",
                "json",
                str(path),
            ],
        )
    )
    stream = probe["streams"][0]
    # Pin YUV conversion, honouring tagged matrices/ranges; RGB replay bypasses it.
    matrix = {"bt709": "bt709", "bt470bg": "bt601", "smpte170m": "bt601", "bt2020nc": "bt2020"}.get(
        stream.get("color_space"), "bt709"
    )
    colour = (
        ""
        if stream.get("pix_fmt", "").startswith(("rgb", "bgr", "gbr"))
        else f",scale=in_color_matrix={matrix}:in_range={'full' if stream.get('color_range') == 'pc' else 'limited'}:out_range=full"
    )
    fps = Fraction(info["frame_rate"])
    origin = Fraction(stream.get("start_time", "0")) - Fraction(
        probe.get("format", {}).get("start_time", "0")
    )
    # Preserve original timestamps and seek at least one second early. Half-frame
    # windows tolerate container timestamp quantisation without losing CFR indices.
    seek = max(0, int(start / fps) - 1)
    lower = float(origin + (Fraction(start) - Fraction(1, 2)) / fps)
    trim = f"select='gte(t,{lower:.12f})"
    if end is not None:
        upper = float(origin + (Fraction(end) - Fraction(1, 2)) / fps)
        trim += f"*lt(t,{upper:.12f})"
    trim += "'"
    return info, [
        "-copyts",
        "-start_at_zero",
        *(["-ss", str(seek)] if seek else []),
        "-i",
        str(path),
        "-vf",
        trim + colour,
        *(["-frames:v", str(end - start)] if end is not None else []),
        "-an",
        "-fps_mode",
        "passthrough",
        "-threads:v",
        "1",
        "-pix_fmt",
        "rgba" if alpha else "rgb24",
        "-f",
        "rawvideo",
        "pipe:1",
    ]


def decode_rgb(media, path, start=0, end=None):
    info, args = _decode_spec(media, path, start, end)
    data = _pipe(media, args)
    frames = np.frombuffer(data, np.uint8).reshape(-1, info["height"], info["width"], 3)
    if not len(frames) or (end is not None and len(frames) != end - start):
        raise ApplicationError("The shot could not be decoded in full.", 422)
    return frames


def iter_rgb(media, path, start=0, end=None, *, alpha=False):
    """Decode one native frame at a time, with bounded buffers and the same timing."""
    info, args = _decode_spec(media, path, start, end, alpha)
    channels = 4 if alpha else 3
    size = info["width"] * info["height"] * channels
    with (
        tempfile.TemporaryFile() as error,
        media.process(
            [
                str(media.ffmpeg),
                "-v",
                "error",
                "-nostdin",
                "-threads",
                "1",
                "-filter_threads",
                "1",
                "-thread_queue_size",
                "1",
                *args,
            ],
            stdout=subprocess.PIPE,
            stderr=error,
        ) as child,
    ):
        count = 0
        try:
            while data := child.stdout.read(size):
                if len(data) != size:
                    raise ApplicationError("Incomplete decoded frame.")
                count += 1
                yield np.frombuffer(data, np.uint8).reshape(info["height"], info["width"], channels)
            if child.wait(timeout=180):
                error.seek(0)
                raise ApplicationError(error.read().decode("utf-8", errors="replace")[-1200:])
            if not count or (end is not None and count != end - start):
                raise ApplicationError("The shot could not be decoded in full.", 422)
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()


@contextmanager
def video_writer(media, args):
    """Pipe raw frames without retaining them or filling an unread stderr pipe."""
    with (
        tempfile.TemporaryFile() as error,
        media.process(
            [
                str(media.ffmpeg),
                "-v",
                "error",
                "-nostdin",
                "-threads",
                "1",
                "-filter_threads",
                "1",
                "-thread_queue_size",
                "1",
                *args,
            ],
            stdin=subprocess.PIPE,
            stdout=subprocess.DEVNULL,
            stderr=error,
        ) as child,
    ):
        try:
            yield child.stdin
            child.stdin.close()
            if child.wait(timeout=180):
                error.seek(0)
                raise ApplicationError(error.read().decode("utf-8", errors="replace")[-1200:])
        except OSError:
            error.seek(0)
            detail = error.read().decode("utf-8", errors="replace")[-1200:]
            if not detail:
                raise
            raise ApplicationError(detail) from None
        finally:
            if child.poll() is None:
                child.kill()
                child.wait()
            # Popen.__exit__ closes stdin too; suppress a second Windows pipe
            # flush error so it cannot hide the encoder's actual failure.
            with suppress(OSError):
                child.stdin.close()


def audio_trim(media, source, start, count, fps):
    """Trim on the picture's origin, in FFmpeg's container-relative timestamps."""
    info = media.probe(source)
    origin = (
        (Fraction(info.get("video_start_time", "0")) - Fraction(info.get("format_start_time", "0")))
        if info["kind"] == "video"
        else Fraction(0)
    )
    return f"atrim=start={float(origin + start / fps):.12f}:end={float(origin + (start + count) / fps):.12f},asetpts=PTS-STARTPTS"


def lossless_video(
    media,
    frames,
    target,
    fps,
    audio_source=None,
    shot_start=0,
    shot_frames=None,
    identity=None,
    *,
    source_fps=None,
    size=None,
    frame_count=None,
    alpha=False,
    attachment=None,
    audio_pad_duration=None,
):
    """FFV1 RGB plus source PCM; replay every staged video before publishing."""
    target.parent.mkdir(parents=True, exist_ok=True)
    stage = target.with_name(target.stem + ".writing.mkv")
    height, width = frames.shape[1:3] if size is None else (size[1], size[0])
    frame_count = len(frames) if frame_count is None else frame_count
    args = [
        "-y",
        "-fflags",
        "+bitexact",
        "-f",
        "rawvideo",
        "-pix_fmt",
        "rgba" if alpha else "rgb24",
        "-s",
        f"{width}x{height}",
        "-r",
        str(fps),
        "-i",
        "pipe:0",
    ]
    if audio_source is not None:
        args += ["-i", str(audio_source)]
    args += [
        "-map",
        "0:v:0",
        "-c:v",
        "ffv1",
        "-level",
        "3",
        "-pix_fmt",
        "bgra" if alpha else "bgr0",
        "-flags:v",
        "+bitexact",
        "-threads:v",
        "1" if alpha else "2",
    ]
    if audio_source is not None:
        audio_streams = json.loads(
            media.run(
                media.ffprobe,
                [
                    "-v",
                    "error",
                    "-select_streams",
                    "a:0",
                    "-show_entries",
                    "stream=sample_fmt",
                    "-of",
                    "json",
                    str(audio_source),
                ],
            )
        )["streams"]
        fmt = audio_streams[0]["sample_fmt"].removesuffix("p") if audio_streams else "flt"
        codec = {
            "u8": "pcm_u8",
            "s16": "pcm_s16le",
            "s32": "pcm_s32le",
            "s64": "pcm_s64le",
            "flt": "pcm_f32le",
            "dbl": "pcm_f64le",
        }.get(fmt)
        if codec is None:
            raise ApplicationError("Unsupported source PCM sample format.", 422)
        audio_fps = Fraction(source_fps or fps)
        audio_filter = audio_trim(
            media, audio_source, shot_start, shot_frames or frame_count, audio_fps
        )
        if audio_fps != fps:
            # atempo supports factors >= 0.5; chain for slower constant sources.
            factor = float(Fraction(fps) / audio_fps)
            while factor < 0.5:
                audio_filter += ",atempo=0.5"
                factor *= 2
            while factor > 2:
                audio_filter += ",atempo=2"
                factor /= 2
            audio_filter += f",atempo={factor:.12f}"
        if audio_pad_duration is not None:
            audio_filter += f",apad=whole_dur={float(audio_pad_duration):.12f}"
        args += [
            "-map",
            "1:a:0?",
            "-af",
            audio_filter,
            "-c:a",
            codec,
            "-flags:a",
            "+bitexact",
        ]
    args += ["-map_metadata", "-1", "-fflags", "+bitexact"]
    if identity is not None:
        args += ["-metadata", "comment=GAIAVFX derivative " + identity]
    if attachment is not None:
        args += [
            "-attach",
            str(attachment),
            "-metadata:s:t:0",
            "mimetype=application/json",
            "-metadata:s:t:0",
            "filename=patch.json",
        ]
    args += [str(stage)]
    expected, actual = hashlib.sha256(), hashlib.sha256()
    count = 0
    try:
        with video_writer(media, args) as pipe:
            for frame in frames:
                data = np.ascontiguousarray(frame).tobytes()
                expected.update(data)
                pipe.write(data)
                count += 1
    finally:
        if hasattr(frames, "close"):
            frames.close()
    for frame in iter_rgb(media, stage, alpha=alpha):
        actual.update(frame.tobytes())
    if count != frame_count or expected.digest() != actual.digest():
        raise ApplicationError("Lossless video replay changed RGB pixels.")
    stage.replace(target)
    return target


def dilate(mask, pixels):
    pixels = math.ceil(pixels)
    result = mask.copy()
    for axis in (-2, -1):
        source = result
        result = source.copy()
        for offset in range(1, pixels + 1):
            left, right = [slice(None)] * source.ndim, [slice(None)] * source.ndim
            left[axis], right[axis] = slice(offset, None), slice(None, -offset)
            result[tuple(left)] |= source[tuple(right)]
            result[tuple(right)] |= source[tuple(left)]
    return result


def mask_box(mask):
    ys, xs = np.nonzero(mask.any(axis=0) if mask.ndim == 3 else mask)
    return (
        Box(float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))
        if len(xs)
        else None
    )


def detect_letterbox(frames):
    # Only contiguous, persistently near-black outer bars; never remove a dark interior.
    dark = frames.max(axis=(0, 3)) <= 3
    rows, cols = dark.all(axis=1), dark.all(axis=0)
    height, width = dark.shape
    if rows.all():
        return None
    y0, y1 = np.flatnonzero(~rows)[[0, -1]]
    x0, x1 = np.flatnonzero(~cols)[[0, -1]]
    if (x0, y0, x1 + 1, y1 + 1) == (0, 0, width, height):
        return None
    return Letterbox((int(x1 - x0 + 1), int(y1 - y0 + 1)), (int(x0), int(y0)), (width, height))


def cut_check(frames):
    # Adaptive discontinuity check: 3x neighbourhood and minimum 15/255 content change,
    # the research detector's documented thresholds. RGB residual, not an identity claim.
    small = frames[:, ::4, ::4].astype(np.float32)
    values = np.abs(np.diff(small, axis=0)).mean(axis=(1, 2, 3))
    cuts = []
    for i, value in enumerate(values):
        neighbours = [values[j] for j in range(max(0, i - 2), min(len(values), i + 3)) if j != i]
        if value >= 15 and value > 3 * max(1, float(np.mean(neighbours)) if neighbours else 1):
            cuts.append(i + 1)
    if cuts:
        raise ApplicationError(f"The requested shot contains a cut at relative frames {cuts}.", 422)


def _axis_weights(coordinates, size):
    indices = np.floor(coordinates).astype(int)[:, None] + np.arange(-3, 5)
    distance = coordinates[:, None] - indices
    weights = np.sinc(distance) * np.sinc(distance / 4)
    weights[np.abs(distance) >= 4] = 0
    weights /= weights.sum(axis=1, keepdims=True)
    if size == 1:
        indices[:] = 0
    else:
        indices %= 2 * (size - 1)
        indices = np.minimum(indices, 2 * (size - 1) - indices)
    return indices, weights.astype(np.float32)


def resample(frame, xs, ys):
    """Separable Lanczos-4, reflect-101 border, research pixel-centre affine."""
    xi, xw = _axis_weights(np.asarray(xs), frame.shape[1])
    yi, yw = _axis_weights(np.asarray(ys), frame.shape[0])
    scalar = frame.ndim == 2
    src = frame[..., None] if scalar else frame
    horizontal = sum(
        src[:, xi[:, k], :].astype(np.float32) * xw[None, :, k, None] for k in range(8)
    )
    result = sum(horizontal[yi[:, k], :, :] * yw[:, k, None, None] for k in range(8))
    result = np.clip(np.rint(result), 0, 255).astype(np.uint8)
    return result[..., 0] if scalar else result


def crop(frame, plan):
    return resample(
        frame,
        (np.arange(plan.canvas[0]) + 0.5) / plan.scale - 0.5 + plan.box.x0,
        (np.arange(plan.canvas[1]) + 0.5) / plan.scale - 0.5 + plan.box.y0,
    )


def uncrop(frame, plan, native):
    return resample(
        frame,
        plan.scale * (np.arange(native[0]) + 0.5 - plan.box.x0) - 0.5,
        plan.scale * (np.arange(native[1]) + 0.5 - plan.box.y0) - 0.5,
    )


def alpha_map(plan, native):
    x, y = np.arange(native[0]) + 0.5, np.arange(native[1]) + 0.5
    ax = ((x > plan.box.x0) & (x < plan.box.x1)).astype(float)
    ay = ((y > plan.box.y0) & (y < plan.box.y1)).astype(float)
    for edge, distance, alpha in (
        ("left", x - plan.box.x0, ax),
        ("right", plan.box.x1 - x, ax),
        ("top", y - plan.box.y0, ay),
        ("bottom", plan.box.y1 - y, ay),
    ):
        if edge in plan.interior_edges and plan.feather:
            alpha *= 0.5 - 0.5 * np.cos(np.pi * np.clip(distance / plan.feather, 0, 1))
    return np.outer(ay, ax)


def head_mask(mask, square):
    """Intersect the source performer mask with the stabilised native head square."""
    height, width = mask.shape
    x, y = np.arange(width) + 0.5, np.arange(height) + 0.5
    return (
        mask
        & ((x >= square[0]) & (x < square[2]))[None, :]
        & ((y >= square[1]) & (y < square[3]))[:, None]
    )


def head_alpha(mask, square, plan, policy):
    """SAM head region, native dilation, and a finite integer box-blur feather.

    Exact zero outside the blur support and exact one inside protect parent pixels.
    """
    region = head_mask(mask, square)
    height, width = mask.shape
    region = dilate(region, policy.mask_dilate_px)
    radius = policy.mask_feather_px
    if radius:
        size = 2 * radius + 1
        counts = np.pad(region.astype(np.int64), radius)
        integral = np.pad(counts, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        counts = (
            integral[size:, size:]
            - integral[:-size, size:]
            - integral[size:, :-size]
            + integral[:-size, :-size]
        )
        alpha = counts / (size * size)
    else:
        alpha = region.astype(float)
    return alpha * alpha_map(plan, (width, height))


def paste(original, generated, alpha):
    out = np.rint(original * (1 - alpha[..., None]) + generated * alpha[..., None]).astype(np.uint8)
    out[alpha == 0] = original[alpha == 0]
    out[alpha == 1] = generated[alpha == 1]
    return out


def invert_performer(frame, performer, protected):
    out = frame.copy()
    invert = performer & ~protected
    out[invert] = 255 - out[invert]
    return out


def prepare_crop(frame, plan, performer, protected, treatment):
    """Invert a hard mask on the resampled canvas, as in research suite_v2."""
    prepared = crop(frame, plan)
    if treatment == "inverted":
        selected = crop((performer & ~protected).astype(np.uint8) * 255, plan) > 127
        prepared[selected] = 255 - prepared[selected]
    return prepared


def _mask_data(library, artifact_id):
    with library.database.transaction() as session:
        data = library.read_blob(session, artifact_id)
    return np.load(io.BytesIO(data), allow_pickle=False)["masks"].astype(bool)


def analysis_extract(context):
    spec = context.snapshot["step_spec"]
    source = context.paths[spec["source_asset"]]
    if context.library.media.probe(source, frame_range=(spec["start"], spec["end"]))[
        "variable_frame_rate"
    ]:
        from gaiavfx.application.replace_character import ReplacementPlanningError
        from gaiavfx.modules.direction.replacement import Gap

        raise ReplacementPlanningError(
            (Gap("unsupported_fps", "Variable frame rate is unsupported."),)
        )
    frames = decode_rgb(context.library.media, source, spec["start"], spec["end"])
    target = context.staging / "shot.mkv"
    lossless_video(
        context.library.media, frames, target, Fraction(spec["fps"]), identity=context.staging.name
    )
    return [("shot", target, "video")]


def appearance_histogram(frames, masks, states):
    """Normalised 12 x 6 hue/saturation bins over present masked pixels; no identity decision."""
    from PIL import Image

    hist = np.zeros((12, 6), np.float64)
    for frame, mask, state in zip(frames, masks, states, strict=True):
        if state != "present" or not mask.any():
            continue
        hsv = np.asarray(Image.fromarray(frame).convert("HSV"))[mask]
        hsv = hsv[hsv[:, 2] > 30]  # Dark pixels carry little clothing colour evidence.
        h = hsv[:, 0].astype(int) * 12 // 256
        s = hsv[:, 1].astype(int) * 6 // 256
        np.add.at(hist, (h, s), 1)
    total = hist.sum()
    return (hist / total).tolist() if total else None


def people_report(context):
    spec = context.snapshot["step_spec"]
    manifest = json.loads(context.paths["analysis"].read_text("utf-8"))
    frames = decode_rgb(
        context.library.media, context.paths[spec["source_asset"]], spec["start"], spec["end"]
    )
    cut_check(frames)
    tracks, masks = [], {}
    for entry in manifest["tracks"]:
        masks[entry["id"]] = _mask_data(context.library, entry["mask_artifact"])
    for entry in manifest["tracks"]:
        mask = masks[entry["id"]]
        if mask.shape != frames.shape[:3]:
            raise ApplicationError("Analysis masks do not cover the source shot.")
        box = mask_box(mask)
        if box is None:
            continue
        masks[entry["id"]] = mask
        areas = mask.sum(axis=(1, 2))
        median = float(np.median(areas[areas > 0]))
        others = np.zeros_like(mask)
        if entry["concept"] == "person":
            for other in manifest["tracks"]:
                if other["concept"] == "person" and other["id"] != entry["id"]:
                    others |= masks[other["id"]]
        overlaps = (mask & dilate(others, spec.get("dilate", OBJECT_DILATE_PX))).sum(axis=(1, 2))
        states = []
        for index, area in enumerate(areas):
            previous = mask[index - 1] if index else None
            union = (mask[index] | previous).sum() if previous is not None else 0
            iou = (
                float((mask[index] & previous).sum() / union)
                if union and areas[index - 1]
                else None
            )
            states.append(
                presence(int(area), median, iou, float(overlaps[index] / area) if area else 0)
            )
        tracks.append(
            {
                **entry,
                "box": to_record(box),
                "presence": states,
                "appearance": appearance_histogram(frames, mask, states),
            }
        )
    identities = {}
    if "faces" in context.paths:
        with np.load(context.paths["faces"], allow_pickle=False) as faces:
            cast = json.loads(context.paths["cast"].read_text("utf-8"))
            identities = window_identities(faces, cast, masks, spec["start"])
    for track in tracks:
        identity = identities.get(
            track["id"], {"cluster": None, "film_tracks": {}, "votes": 0, "margin": None}
        )
        track.update(identity=identity, identity_margin=identity["margin"])
    people = [t for t in tracks if t["concept"] == "person"]
    mouth_scan = None
    if "window_faces" in context.paths:
        with np.load(context.paths["window_faces"], allow_pickle=False) as faces:
            mouth_scan = "measured" if "landmark_106" in faces.files else "unavailable"
            mouths = window_mouths(faces, {t["id"]: masks[t["id"]] for t in people}, len(frames))
        for person in people:
            if person["id"] in mouths:
                person["mouth"] = mouths[person["id"]]
    objects = [t for t in tracks if t["concept"] != "person"]
    contacts = {}
    for person in people:
        valid = np.asarray(person["presence"]) == "present"
        expanded = dilate(masks[person["id"]], spec.get("dilate", OBJECT_DILATE_PX))
        contacts[person["id"]] = [
            obj["id"]
            for obj in objects
            if valid.any()
            and ((expanded & masks[obj["id"]]).any(axis=(1, 2)) & valid).sum() / valid.sum()
            >= OBJECT_OVERLAP_FRACTION
        ]
    candidates = {}
    for person in people:
        others = [p for p in people if p["id"] != person["id"]]
        avoided_ids = {p["id"] for p in others} | {o for p in others for o in contacts[p["id"]]}
        avoided_ids -= set(contacts[person["id"]])
        candidates[person["id"]] = {
            "required_context": [t["box"] for t in objects if t["id"] in contacts[person["id"]]],
            "avoided_regions": [t["box"] for t in tracks if t["id"] in avoided_ids],
            "protected_tracks": [t["id"] for t in tracks if t["id"] != person["id"]],
        }
    letterbox = detect_letterbox(frames)
    document = {
        "source_asset": spec["source_asset"],
        "shot_start": spec["start"],
        "shot_end": spec["end"],
        "native": [frames.shape[2], frames.shape[1]],
        "fps": spec["fps"],
        "letterbox": to_record(letterbox) if letterbox else None,
        "concepts": spec["concepts"],
        "tracks": tracks,
        "empty_tracks": [t["id"] for t in manifest["tracks"] if not masks[t["id"]].any()],
        "padded_frames": {t["id"]: t.get("padded_frames", 0) for t in manifest["tracks"]},
        "contacts": contacts,
        "candidates": candidates,
        **({"mouth_scan": mouth_scan} if mouth_scan else {}),
    }
    context.staging.mkdir(parents=True, exist_ok=True)
    target = context.staging / "people.json"
    target.write_text(json.dumps(document, allow_nan=False), "utf-8")
    return [("people", target, "analysis_report")]


def window_mouths(faces, masks, frames):
    """Per person track and window frame: the source inner-lip aperture over face height and
    the face height in pixels, or None where no face is chosen.

    The face is chosen and measured as the face check does (comfy_nodes gaiavfx
    metrics ``choose_face`` and ``mouth_aperture``): the largest fractional box overlap with
    the track's mask, at least 20%, and the 106-point indices 62/60. The face-size floor is
    the mouth policy's, applied when facts are derived. A scan without landmarks (a ComfyUI
    started before the node change) gives no series, so no facts are derived.
    """
    if "landmark_106" not in faces.files:
        return {}
    index = {}
    for row, frame in enumerate(faces["frame"].tolist()):
        index.setdefault(frame, []).append(row)
    boxes, points = faces["box"].astype(float), faces["landmark_106"].astype(float)
    result = {track: {"aperture": [], "face_height": [], "box": []} for track in masks}
    for frame in range(frames):
        chosen = {}
        for track, mask in masks.items():
            height, width = mask.shape[1:]
            best, score = None, 0.0
            for row in index.get(frame, ()):
                box = boxes[row]
                if not np.isfinite(box).all() or np.any(box[2:] <= box[:2]):
                    continue
                x0, y0, x1, y1 = np.rint(box).astype(int)
                x0, y0, x1, y1 = max(0, x0), max(0, y0), min(width, x1), min(height, y1)
                if x1 <= x0 or y1 <= y0:
                    continue
                overlap = float(np.mean(mask[frame, y0:y1, x0:x1] > 0.5))
                if overlap > score:
                    best, score = row, overlap
            if best is not None and score >= 0.2 and np.isfinite(points[best]).all():
                chosen[track] = (best, score)
        # A face serves only the track it overlaps most: a neighbour's mouth never stands in
        # for a performer whose own face was not found.
        for track in masks:
            row, score = chosen.get(track, (None, 0.0))
            if row is None or any(
                other != track and r == row and s > score for other, (r, s) in chosen.items()
            ):
                result[track]["aperture"].append(None)
                result[track]["face_height"].append(None)
                result[track]["box"].append(None)
                continue
            face_height = float(boxes[row][3] - boxes[row][1])
            gap = float(np.linalg.norm(points[row][62] - points[row][60]))
            result[track]["aperture"].append(round(gap / face_height, 6))
            result[track]["face_height"].append(round(face_height, 3))
            tied = any(
                other != track and r == row and s == score for other, (r, s) in chosen.items()
            )
            result[track]["box"].append(None if tied else boxes[row].tolist())
    return result


def window_identities(faces, report, masks, start):
    """Vote by face-box centre inside the frame's SAM mask, in float32.

    Load each NPZ array once and filter rows to the window before mask work.
    Film cluster centroids are normalised means of their member face embeddings.
    With at least three faces in a mask, margin is the normalised track mean's
    cosine to its winning cluster minus its best other centroid (zero if none).
    Fewer than three faces gives a null margin, while preserving raw vote counts.
    """
    tracks = {t["id"]: t for t in report["tracks"]}
    membership, centroids = {}, {}
    film_membership = {row: track["id"] for track in report["tracks"] for row in track["faces"]}
    embeddings = np.asarray(faces["embedding"], dtype=np.float32)
    frames, boxes = faces["frame"], faces["box"]
    end = start + max((len(mask) for mask in masks.values()), default=0)
    window_rows = np.flatnonzero((frames >= start) & (frames < end))
    window_frames, window_boxes = frames[window_rows] - start, boxes[window_rows]

    def unit(vector):
        norm = np.linalg.norm(vector)
        return vector / norm if norm else vector

    for cluster in report["clusters"]:
        rows = [row for track in cluster["track_ids"] for row in tracks[track]["faces"]]
        if rows:
            centroids[cluster["id"]] = unit(embeddings[rows].mean(axis=0))
            membership.update(dict.fromkeys(rows, cluster["id"]))
    result = {}
    for name, mask in masks.items():
        rows = []
        for row, frame, box in zip(window_rows, window_frames, window_boxes, strict=True):
            frame = int(frame)
            x, y = (float(box[0] + box[2]) / 2, float(box[1] + box[3]) / 2)
            if (
                0 <= frame < len(mask)
                and 0 <= y < mask.shape[1]
                and 0 <= x < mask.shape[2]
                and mask[frame, int(y), int(x)]
            ):
                rows.append(row)
        votes, film_votes = {}, {}
        for row in rows:
            if row in film_membership:
                film_track = film_membership[row]
                film_votes[film_track] = film_votes.get(film_track, 0) + 1
            if row in membership:
                cluster = membership[row]
                votes[cluster] = votes.get(cluster, 0) + 1
        identity = {"cluster": None, "film_tracks": {}, "votes": 0, "margin": None}
        identity["film_tracks"] = dict(sorted(film_votes.items()))
        if votes:
            cluster = min(votes, key=lambda c: (-votes[c], c))
            mean = unit(embeddings[rows].mean(axis=0))
            similarity = float(mean @ centroids[cluster])
            other = max(
                (float(mean @ v) for c, v in centroids.items() if c != cluster), default=0.0
            )
            identity = {
                "cluster": cluster,
                "film_tracks": dict(sorted(film_votes.items())),
                "votes": votes[cluster],
                "margin": float(np.clip(similarity - other, -2, 2)) if len(rows) >= 3 else None,
            }
        result[name] = identity
    return result


def inversion_score(frames, delivered, performer, stride=7):
    """Pearson r of the delivered luma against the source luma inside the performer mask.

    A photographic-negative copy of the inverted guide reads close to -1; a replacement keeps
    the scene's light and reads positive. None when the mask holds too few pixels to say.
    """
    weights = np.array([0.299, 0.587, 0.114], np.float32)
    source, result = [], []
    for frame, out, mask in zip(frames, delivered, performer, strict=True):
        if mask.any():
            source.append((frame[mask][::stride].astype(np.float32)) @ weights)
            result.append((out[mask][::stride].astype(np.float32)) @ weights)
    if not source:
        return None
    a, b = np.concatenate(source), np.concatenate(result)
    if a.size < 1000 or np.std(a) < 1e-6 or np.std(b) < 1e-6:
        return None
    return float(np.clip(np.corrcoef(a, b)[0, 1], -1, 1))


def _frozen(context):
    spec = context.snapshot["step_spec"]
    return spec, from_record(ReplacementFacts, spec["facts"]), from_record(CropPlan, spec["crop"])


def _selected_masks(context, spec, frames, *, bridge_masks=None):
    if spec.get("bridge"):
        masks = bridge_masks if bridge_masks is not None else _bridge_masks(context, spec)
        if spec.get("head"):
            index = spec["head_bridge_performer"]
            return masks[0][index], np.logical_or.reduce(
                [masks[1], *(m for i, m in enumerate(masks[0]) if i != index)]
            )
        return np.logical_or.reduce(masks[0]), masks[1]
    selected = set(spec["selected_tracks"])
    performer = np.zeros(frames.shape[:3], bool)
    protected = performer.copy()
    for track in spec["tracks"]:
        if track["id"] in spec.get("ignored_tracks", ()):
            continue
        mask = _mask_data(context.library, track["mask_artifact"])
        if mask.shape != performer.shape:
            raise ApplicationError("Frozen mask dimensions changed.")
        if track["id"] in selected:
            performer |= mask
        else:
            protected |= mask
    return performer, protected


def _bridge_masks(context, spec):
    """Earliest covering window owns guide/evidence masks, in performer order."""
    bridge = spec["bridge"]
    windows = bridge["windows"]
    count = len(windows[1]["selected_tracks"])
    selected, protected = [[] for _ in range(count)], []
    loaded = [
        [
            (t["id"], _mask_data(context.library, t["mask_artifact"]))
            for t in w["tracks"]
            if t["id"] not in w.get("ignored_tracks", ())
        ]
        for w in windows
    ]
    for frame in range(bridge["start"], bridge["end"]):
        index = next(i for i, w in enumerate(windows) if w["start"] <= frame < w["end"])
        window = windows[index]
        masks = {key: mask[frame - window["start"]] for key, mask in loaded[index]}
        for i, key in enumerate(window["selected_tracks"]):
            selected[i].append(masks[key])
        others = [m for key, m in masks.items() if key not in window["selected_tracks"]]
        protected.append(np.logical_or.reduce(others) if others else np.zeros_like(selected[0][-1]))
    return [np.stack(m) for m in selected], np.stack(protected)


def _bridge_pixels(context, spec, facts, plan, frames):
    """Rebuild raw composites one frame at a time; delivery tone never enters a handoff."""
    from contextlib import ExitStack

    windows = spec["bridge"]["windows"]
    sequence = []
    with ExitStack() as stack:
        decoders = []
        for w in windows:
            crop_plan = from_record(CropPlan, w["crop"])
            alpha = alpha_map(crop_plan, facts.native)
            if facts.letterbox:
                x, y = facts.letterbox.offset
                width, height = facts.letterbox.fit
                bars = np.ones(alpha.shape, bool)
                bars[y : y + height, x : x + width] = False
                alpha[bars] = 0
            start = max(facts.shot_start, w["start"]) - w["start"]
            end = min(facts.shot_end, w["end"]) - w["start"]
            decoder = iter_rgb(context.library.media, context.paths[w["render_asset"]], start, end)
            stack.callback(decoder.close)
            decoders.append((decoder, crop_plan, alpha))
        for index, film in enumerate(frames):
            frame = facts.shot_start + index
            composites = []
            for w, (decoder, crop_plan, alpha) in zip(windows, decoders, strict=True):
                if w["start"] <= frame < w["end"]:
                    composites.append(
                        paste(film, uncrop(next(decoder), crop_plan, facts.native), alpha)
                    )
            if len(composites) == 2:
                t = (frame - windows[1]["start"]) / max(
                    1, windows[0]["end"] - windows[1]["start"] - 1
                )
                output = np.rint(
                    composites[0].astype(np.float32) * (1 - t)
                    + composites[1].astype(np.float32) * t
                ).astype(np.uint8)
            else:
                output = composites[0]
            sequence.append(crop(output, plan))
        for decoder, _, _ in decoders:
            if next(decoder, None) is not None:
                raise ApplicationError("Bridge raw render exceeds the requested range.")
    while len(sequence) < spec["window_frames"]:
        sequence.append(sequence[-1])
    if len(sequence) != spec["window_frames"]:
        raise ApplicationError("Bridge pixel sequence differs from the frozen window.")
    return np.stack(sequence)


def _mask_canvas(prepared, frame, plan, arm, facts):
    if arm.crop is None:
        valid = np.ones(frame.shape[:2], np.uint8) * 255
        if arm.framing == "active" and facts.letterbox:
            valid[:] = 0
            x, y = facts.letterbox.offset
            w, h = facts.letterbox.fit
            valid[y : y + h, x : x + w] = 255
        xs = (np.arange(plan.canvas[0]) + 0.5) / plan.scale + plan.box.x0
        ys = (np.arange(plan.canvas[1]) + 0.5) / plan.scale + plan.box.y0
        inside = (
            (ys[:, None] >= 0)
            & (ys[:, None] < frame.shape[0])
            & (xs[None, :] >= 0)
            & (xs[None, :] < frame.shape[1])
        )
        prepared[~inside | (crop(valid, plan) < 128)] = 0
    return prepared


def replacement_prepare(context):
    spec, facts, plan = _frozen(context)
    source = context.paths[facts.source_asset]
    head = spec.get("head")
    frames = (
        decode_rgb(
            context.library.media, context.paths[head["parent_asset"]], 0, facts.delivered_frames
        )
        if head
        else decode_rgb(context.library.media, source, facts.shot_start, facts.shot_end)
    )
    cut_check(frames)
    if not head and detect_letterbox(frames) != facts.letterbox:
        raise ApplicationError("Letterbox detection differs from the frozen analysis.")
    performer, protected = _selected_masks(context, spec, frames)
    arm = from_record(Arm, spec["arm"])
    guide = []
    for i, frame in enumerate(frames):
        prepared = prepare_crop(frame, plan, performer[i], protected[i], arm.treatment)
        prepared = _mask_canvas(prepared, frame, plan, arm, facts)
        guide.append(prepared)
    while len(guide) < spec["window_frames"]:
        guide.append(guide[-1])
    target = context.staging / "guide.mkv"
    guide_fps = minimax_h3.capabilities().frame_grid.fps
    lossless_video(
        context.library.media,
        np.stack(guide),
        target,
        guide_fps,
        source,
        facts.shot_start,
        facts.delivered_frames,
        **({"source_fps": facts.fps} if facts.fps != guide_fps else {}),
        **({"audio_pad_duration": spec["window_frames"] / guide_fps} if arm.audio_lock else {}),
    )
    outputs = [("guide", target, "video")]
    if spec.get("bridge") and not head:
        target = context.staging / "pixels.mkv"
        lossless_video(
            context.library.media,
            _bridge_pixels(context, spec, facts, plan, frames),
            target,
            guide_fps,
        )
        outputs.append(("pixels", target, "video"))
    if chain := spec.get("chain"):
        frames = decode_rgb(
            context.library.media,
            context.paths[chain["asset"]],
            chain["start"] - chain["predecessor_start"],
            chain["end"] - chain["predecessor_start"],
        )
        context_frames = np.stack(
            [_mask_canvas(crop(frame, plan), frame, plan, arm, facts) for frame in frames]
        )
        target = context.staging / "context.mkv"
        lossless_video(context.library.media, context_frames, target, guide_fps)
        outputs.append(("context", target, "video"))
    return outputs


def _bilinear(image, x, y):
    x, y = np.clip(x, 0, image.shape[1] - 1), np.clip(y, 0, image.shape[0] - 1)
    ix, iy = x.astype(int), y.astype(int)
    jx, jy = np.minimum(ix + 1, image.shape[1] - 1), np.minimum(iy + 1, image.shape[0] - 1)
    fx, fy = x - ix, y - iy
    return (image[iy, ix] * (1 - fx) + image[iy, jx] * fx) * (1 - fy) + (
        image[jy, ix] * (1 - fx) + image[jy, jx] * fx
    ) * fy


def _global_geometry(ga, gb, excluded):
    """Masked normalized correlation over the whole canvas and scale [0.75, 1.25]."""
    factor = max(1, math.ceil(max(ga.shape) / 128))
    h, w = (v // factor for v in ga.shape)

    def small(value):
        return value[: h * factor, : w * factor].reshape(h, factor, w, factor).mean(axis=(1, 3))

    a, b, valid = small(ga), small(gb), small(~excluded) == 1
    size = tuple(2 ** math.ceil(math.log2(2 * v)) for v in (h, w))
    # Six masked correlations give overlap, means, variances and covariance at every shift.
    fb, fb2, fm = (np.fft.rfft2(v, s=size) for v in (b * valid, b * b * valid, valid))
    yy, xx = np.mgrid[:h, :w]
    best = (-1.0, 1.0, 0.0, 0.0)

    def search(scales):
        nonlocal best
        for scale in scales:
            x, y = (xx + 0.5) / scale - 0.5, (yy + 0.5) / scale - 0.5
            mask = (
                (x >= 0) & (y >= 0) & (x < w - 1) & (y < h - 1) & (_bilinear(valid, x, y) > 0.999)
            )
            image = _bilinear(a, x, y) * mask
            fa, fa2, fmask = (np.fft.rfft2(v, s=size).conj() for v in (image, image * image, mask))
            count, sa, sb, sa2, sb2, sab = (
                np.fft.irfft2(v, s=size)
                for v in (fm * fmask, fm * fa, fb * fmask, fm * fa2, fb2 * fmask, fb * fa)
            )
            count = np.maximum(count, 1)
            denominator = np.sqrt(
                np.maximum(sa2 - sa * sa / count, 0) * np.maximum(sb2 - sb * sb / count, 0)
            )
            scores = np.divide(
                sab - sa * sb / count, denominator, out=np.zeros_like(sab), where=denominator > 1
            )
            scores[count < max(64, min(mask.sum(), valid.sum()) * 0.25)] = -1
            dy, dx = np.unravel_index(np.argmax(scores), scores.shape)
            score = float(scores[dy, dx])
            if score > best[0]:
                dx = dx if dx < size[1] // 2 else dx - size[1]
                dy = dy if dy < size[0] // 2 else dy - size[0]
                best = (
                    score,
                    float(scale),
                    float(dx * factor + (scale - 1) * 0.5),
                    float(dy * factor + (scale - 1) * 0.5),
                )

    search(np.linspace(0.75, 1.25, 51))
    search(np.arange(max(0.75, best[1] - 0.009), min(1.25, best[1] + 0.009) + 0.0001, 0.001))
    return best[1:]


def background_textured(frame, excluded):
    grey = frame.mean(axis=2)
    mask = ~dilate(excluded, 24)
    # Local differences distinguish texture from flat regions separated by bars/edges.
    usable = mask[1:, 1:] & mask[:-1, 1:] & mask[1:, :-1]
    texture = np.abs(grey[1:, 1:] - grey[:-1, 1:]) + np.abs(grey[1:, 1:] - grey[1:, :-1])
    return bool(np.count_nonzero(usable & (texture > 10)) >= 64)


def background_geometry(a, b, excluded):
    """Global pyramid correlation, locally refined background matches, robust similarity."""
    if not background_textured(a, excluded):
        return None
    ga, gb = a.mean(axis=2), b.mean(axis=2)
    height, width = ga.shape
    excluded = dilate(excluded, 24)
    scale, dx, dy = _global_geometry(ga, gb, excluded)
    step, radius, search = 24, 4, 6
    offsets_y, offsets_x = np.mgrid[-radius : radius + 1, -radius : radius + 1]
    shifts_y, shifts_x = np.mgrid[-search : search + 1, -search : search + 1]
    pairs = []
    for y in range(radius, height - radius, step):
        for x in range(radius, width - radius, step):
            if excluded[y - radius : y + radius + 1, x - radius : x + radius + 1].any():
                continue
            patch = ga[y - radius : y + radius + 1, x - radius : x + radius + 1]
            if patch.std() < 5:
                continue
            u, v = round(scale * x + dx), round(scale * y + dy)
            margin = search + radius * scale + 1
            if not (margin <= u < width - margin and margin <= v < height - margin):
                continue
            windows = _bilinear(
                gb,
                u + shifts_x[..., None, None] + offsets_x * scale,
                v + shifts_y[..., None, None] + offsets_y * scale,
            )
            residual = (windows - windows.mean(axis=(-2, -1), keepdims=True)) - (
                patch - patch.mean()
            )
            scores = (residual**2).mean(axis=(-2, -1))
            sy, sx = np.unravel_index(np.argmin(scores), scores.shape)
            if scores[sy, sx] < patch.var() * 0.8:
                pairs.append((x, y, u + int(sx) - search, v + int(sy) - search))
    if len(pairs) < 10:
        return None
    pairs = np.asarray(pairs, dtype=float)
    x, y, u, v = pairs.T
    matrix = np.zeros((2 * len(x), 4))
    matrix[::2] = np.column_stack((x, -y, np.ones(len(x)), np.zeros(len(x))))
    matrix[1::2] = np.column_stack((y, x, np.zeros(len(x)), np.ones(len(x))))
    target = np.column_stack((u, v)).ravel()
    rng, best = np.random.default_rng(0), np.zeros(len(x), bool)
    for _ in range(80):
        chosen = rng.choice(len(x), 2, replace=False)
        rows = np.column_stack((2 * chosen, 2 * chosen + 1)).ravel()
        fit = np.linalg.lstsq(matrix[rows], target[rows], rcond=None)[0]
        inliers = np.linalg.norm((matrix @ fit - target).reshape(-1, 2), axis=1) < 3
        if inliers.sum() > best.sum():
            best = inliers
    if best.sum() < 10:
        return None
    rows = np.repeat(best, 2)
    fit = np.linalg.lstsq(matrix[rows], target[rows], rcond=None)[0]
    return {
        "scale": float(np.hypot(fit[0], fit[1])),
        "dx": float(fit[2]),
        "dy": float(fit[3]),
        "inliers": int(best.sum()),
    }


def measurement_pixels(frames, *, mask=False):
    """Bound diagnostic evidence to 896x512, without upscaling or softening masks."""
    from PIL import Image

    height, width = frames.shape[1:3]
    scale = min(1.0, 896 / width, 512 / height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    if size == (width, height):
        return frames
    method = Image.Resampling.NEAREST if mask else Image.Resampling.LANCZOS
    return np.stack([np.asarray(Image.fromarray(frame).resize(size, method)) for frame in frames])


def replacement_composite(context):
    spec, facts, plan = _frozen(context)
    source = context.paths[facts.source_asset]
    head = spec.get("head")
    frames = (
        decode_rgb(
            context.library.media, context.paths[head["parent_asset"]], 0, facts.delivered_frames
        )
        if head
        else decode_rgb(context.library.media, source, facts.shot_start, facts.shot_end)
    )
    raw = decode_rgb(context.library.media, context.paths["render"])
    if raw.shape[1:3] != (plan.canvas[1], plan.canvas[0]) or len(raw) != spec["window_frames"]:
        raise ApplicationError("The replacement render differs from the frozen canvas/window.")
    bridge_masks = _bridge_masks(context, spec) if spec.get("bridge") else None
    performer, protected = _selected_masks(context, spec, frames, bridge_masks=bridge_masks)
    tone_report = None
    tone = parse_replacement_policy(spec["policy"]).tone_anchor if "policy" in spec else None
    if tone is not None:
        from gaiavfx.application.tone_anchor import LUMA, background_mask, tone_anchor

        reference = np.stack([crop(frame, plan) for frame in frames])
        background = background_mask(plan, facts, performer, protected, tone)
        fractions = []
        tone_report = {}
        raw, gains, _ = tone_anchor(
            raw,
            reference,
            background,
            tone,
            agreeing_fractions=fractions,
            predecessor=spec.get("handoff", {}).get("tone"),
            report=tone_report,
        )
        luma = gains @ LUMA
        tone_report = {
            **tone_report,
            "applied": bool(np.any(gains != 1)),
            "gain_luma": [float(luma.min()), float(luma.max())],
            "neutral_frames": int(np.all(gains[: len(frames)] == 1, axis=1).sum()),
            "agreeing_fraction_median": float(np.median(fractions)),
        }
        del reference, background
    alpha = alpha_map(plan, facts.native)
    bars = np.zeros(frames.shape[1:3], bool)
    if facts.letterbox:
        bars[:] = True
        x, y = facts.letterbox.offset
        w, h = facts.letterbox.fit
        bars[y : y + h, x : x + w] = False
    alpha[bars] = 0
    head_policy, head_squares = None, None
    if head:
        head_policy = parse_replacement_policy(spec["policy"]).head
        track = next(t for t in spec["tracks"] if t["id"] == spec["selected_tracks"][0])
        head_squares = head_track(track["mouth"]["box"], track["presence"], head_policy)
    result, measures, bar_changes = [], [], 0
    unmeasured = []
    geometry_cache = {}
    for index, frame in enumerate(frames):
        generated = uncrop(raw[index], plan, facts.native)
        if bars.any() and from_record(Arm, spec["arm"]).framing == "full":
            bar_changes += int(np.count_nonzero(generated[bars] != frame[bars]))
        if head:
            alpha = head_alpha(performer[index], head_squares[index], plan, head_policy)
            alpha[bars] = 0
        result.append(paste(frame, generated, alpha))
        original_crop = crop(frame, plan)
        exclusion = crop((performer[index] | protected[index]).astype(np.uint8) * 255, plan) > 127
        digest = hashlib.sha256(
            original_crop.tobytes() + raw[index].tobytes() + exclusion.tobytes()
        ).digest()
        if digest not in geometry_cache:
            geometry_cache[digest] = (
                background_geometry(original_crop, raw[index], exclusion),
                background_textured(original_crop, exclusion),
            )
        measure, textured = geometry_cache[digest]
        measures.append(measure)
        unmeasured.append(measure is None and textured)
    valid = [m for m in measures if m is not None]
    scale_delta = abs(float(np.median([m["scale"] for m in valid])) - 1) if valid else None
    shift = float(np.median([math.hypot(m["dx"], m["dy"]) for m in valid])) if valid else None
    # The raw series remains in the artifact; verdict thresholds belong to policy only.
    geometry = {"scale_delta": scale_delta, "shift": shift}
    if any(unmeasured):
        geometry = {"scale_delta": None, "shift": None}
    geometry_reason = (
        f"scale deviation={geometry['scale_delta']}; median shift={geometry['shift']} crop pixels"
        if geometry["shift"] is not None
        else "geometry unmeasured"
    )
    geometry_check = CheckResult(
        "background_geometry", "unsupported", "none", geometry["shift"], geometry_reason
    )
    if "policy" in spec:
        geometry_check = calibrated_check(
            parse_replacement_policy(spec["policy"]),
            "background_geometry",
            geometry["shift"],
            metrics=geometry,
            reason=geometry_reason,
        )
    # Texture-free shots supply no geometric evidence and cannot justify a retry.
    if not valid and not any(unmeasured):
        geometry_check = CheckResult(
            geometry_check.check,
            geometry_check.verdict,
            "none",
            geometry_check.measured,
            geometry_check.reason,
        )
    source_pixels = None
    if head:
        # Retain only canvas crops while streaming film, never a second native stack.
        with closing(
            iter_rgb(context.library.media, source, facts.shot_start, facts.shot_end)
        ) as film:
            source_pixels = np.stack([crop(frame, plan) for frame in film])
        head_masks = [
            crop(head_mask(mask, square).astype(np.uint8) * 255, plan) > 127
            for mask, square in zip(performer, head_squares, strict=True)
        ]
        inversion = inversion_score(source_pixels, raw[: facts.delivered_frames], head_masks)
    else:
        inversion = inversion_score(frames, result, performer)
    inversion_reason = (
        "inversion unmeasured"
        if inversion is None
        else f"performer luma correlation with the source={inversion:.3f}"
    )
    if head:
        inversion_reason += "; measured on the head canvas; uncalibrated for head passes"
    inversion_check = CheckResult(
        "inverted_copy", "unsupported", "none", inversion, inversion_reason
    )
    if "policy" in spec and not head:
        inversion_check = calibrated_check(
            parse_replacement_policy(spec["policy"]),
            "inverted_copy",
            inversion,
            reason=inversion_reason,
        )
    observations = {"letterbox_difference": float(bar_changes)}
    checks = {
        name: CheckResult(name, "unsupported", "none", observations.get(name))
        for name in sorted(CHECKS)
    }
    checks[geometry_check.check] = geometry_check
    checks[inversion_check.check] = inversion_check
    context.staging.mkdir(parents=True, exist_ok=True)
    report = context.staging / "checks.json"
    report.write_text(
        json.dumps(
            {
                "checks": [to_record(c) for c in checks.values()],
                "background_geometry": measures,
                "geometry_method": "global masked pyramid correlation / local patches / RANSAC similarity",
                "reasons": (["geometry unmeasured"] if any(unmeasured) else [])
                + ([tone_report["reason"]] if tone_report else []),
                "geometry_summary": geometry,
                "letterbox_difference": {"changed_channels": bar_changes, "restored_channels": 0},
                "round": spec["arm"]["round"],
                "arm": spec["arm"],
                **({"tone_anchor": tone_report} if tone_report is not None else {}),
                **(
                    {
                        "head_mask": {
                            "mask_dilate_px": head_policy.mask_dilate_px,
                            "mask_feather_px": head_policy.mask_feather_px,
                        }
                    }
                    if head
                    else {}
                ),
            },
            allow_nan=False,
        ),
        "utf-8",
    )
    target = context.staging / "composite.mkv"
    result = np.stack(result)
    lossless_video(
        context.library.media,
        result,
        target,
        facts.fps,
        source,
        facts.shot_start,
        facts.delivered_frames,
        identity=context.staging.name,
    )
    outputs = [("composite", target, "video"), ("checks", report, "analysis_report")]
    if spec.get("face_measurement"):
        candidate = context.staging / "candidate-evidence.mkv"
        lossless_video(
            context.library.media,
            raw[: facts.delivered_frames] if head else measurement_pixels(result),
            candidate,
            facts.fps,
        )
        outputs.append(("candidate_pixels", candidate, "visual_evidence"))
        shot = context.staging / "source-shot.mkv"
        if head:
            lossless_video(context.library.media, source_pixels, shot, facts.fps)
        else:
            lossless_video(context.library.media, measurement_pixels(frames), shot, facts.fps)
        outputs.append(("source_pixels", shot, "visual_evidence"))
        tracks = {t["id"]: t for t in spec["tracks"]}
        performer_masks = bridge_masks[0] if bridge_masks is not None else None
        if performer_masks is not None and head:
            performer_masks = [performer_masks[spec["head_bridge_performer"]]]
        for n, performer in enumerate(facts.performers):
            masks = (
                performer_masks[n]
                if performer_masks is not None
                else _mask_data(context.library, tracks[performer.track_id]["mask_artifact"])
            )
            target_mask = context.staging / f"performer-{n}.mkv"
            masks = masks.astype(np.uint8) * 255
            masks = (
                np.stack([(crop(m, plan) > 127).astype(np.uint8) * 255 for m in masks])
                if head
                else measurement_pixels(masks, mask=True)
            )
            pixels = np.repeat(masks[..., None], 3, axis=3)
            lossless_video(context.library.media, pixels, target_mask, facts.fps)
            outputs.append((f"performer_mask_{n}", target_mask, "mask_track"))
    return outputs
