"""Local steps of the voice build and its audition (ADR 0016 §4, P5).

The build's: the recordings joined into one 48 kHz mono stem (`voice_samples`), the seed cut from it at its
transcript's segments (`voice_seed`), the expressive library frozen as clips (`voice_library`), the filter's candidate
manifests (`voice_candidates`) and the `voice_model` manifest (`voice_package`). The audition's: the lines cut from
the dialogue stem (`voice_audition_cut`) and the reel that plays each original before its conversion
(`voice_reel`).

Every step is resumable: a step that finds its finished output (its manifest is written last, after the files it
names) returns it again. Manifests list clips by id, never by path; audio is decoded through `MediaTools` and written
as mono PCM16 WAV with `wave`.
"""

import json
import re
import wave
from pathlib import Path

import numpy as np

from gaiavfx.application import voice_build
from gaiavfx.application.dialogue_steps import envelope_db
from gaiavfx.shared.errors import ApplicationError


def _decode(context, source: Path, raw: Path, rate: int) -> np.ndarray:
    """The first audio stream as mono float32 samples at `rate`."""
    context.library.media.decode_audio(source, raw, sample_rate=rate, mono=True)
    try:
        return np.fromfile(raw, dtype="<f4")
    finally:
        raw.unlink(missing_ok=True)


def _pcm16(values) -> np.ndarray:
    return np.clip(np.round(np.asarray(values, dtype=np.float64) * 32767.0), -32768, 32767).astype(
        "<i2"
    )


def _write_wav(path: Path, pcm: np.ndarray, rate: int) -> None:
    staged = path.with_name(path.stem + ".writing.wav")
    with wave.open(str(staged), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(np.ascontiguousarray(pcm, dtype="<i2").tobytes())
    staged.replace(path)


def _read_wav(path: Path) -> tuple[np.ndarray, int]:
    with wave.open(str(path), "rb") as stream:
        if stream.getsampwidth() != 2 or stream.getnchannels() != 1:
            raise ApplicationError("The voice build expects mono 16-bit recordings.", 409)
        return (
            np.frombuffer(stream.readframes(stream.getnframes()), dtype="<i2"),
            stream.getframerate(),
        )


def _write_json(path: Path, document) -> None:
    staged = path.with_name(path.stem + ".writing.json")
    staged.write_text(json.dumps(document, indent=1, allow_nan=False), encoding="utf-8")
    staged.replace(path)


def _read_json(path: Path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _rms_db(pcm: np.ndarray) -> float:
    values = pcm.astype(np.float64) / 32768.0
    rms = float(np.sqrt(np.mean(values * values))) if values.size else 0.0
    return round(20 * float(np.log10(max(rms, 1e-9))), 2)


# --- the build -----------------------------------------------------------------------------------------------------


def _samples_outputs(output: Path, marker: Path) -> list:
    manifest = _read_json(marker)
    info = {
        "seconds": manifest["seconds"],
        "channels": 1,
        "sample_rate": manifest["sample_rate"],
        "samples": manifest["samples"],
    }
    return [
        ("samples", output, "audio_stem", info),
        ("manifest", marker, "analysis_report", {"assets": len(manifest["assets"])}),
    ]


def voice_samples(context):
    """The recordings in order as one mono PCM16 stem at 48 kHz, joined with `gap_seconds` of silence, and a manifest
    of each recording's place in it. Refuses a total outside the sample bounds (beyond a few frames of decoding)."""
    spec = context.snapshot["step_spec"]
    rate = int(spec["sample_rate"])
    context.staging.mkdir(parents=True, exist_ok=True)
    output, marker = context.staging / "samples.wav", context.staging / "samples.json"
    if output.is_file() and marker.is_file():
        return _samples_outputs(output, marker)
    gap = np.zeros(round(float(spec["gap_seconds"]) * rate), dtype="<i2")
    parts, assets, position = [], [], 0
    for n, asset_id in enumerate(spec["assets"]):
        values = _decode(
            context, context.paths[asset_id], context.staging / f"sample{n:02d}.f32", rate
        )
        if not values.size:
            raise ApplicationError("A voice recording holds no audio.", 422)
        if parts:
            parts.append(gap)
            position += gap.size
        pcm = _pcm16(values)
        parts.append(pcm)
        assets.append(
            {
                "id": asset_id,
                "sha256": context.inputs[asset_id]["sha256"],
                "seconds": round(pcm.size / rate, 3),
                "start_sample": position,
                "samples": int(pcm.size),
            }
        )
        position += pcm.size
    seconds = sum(asset["samples"] for asset in assets) / rate
    low, high = spec["bounds"]
    tolerance = float(spec.get("tolerance_seconds", 0.0))
    if not low - tolerance <= seconds <= high + tolerance:
        raise ApplicationError(
            voice_build.seconds_sentence(seconds, {"min": low, "max": high}), 422
        )
    _write_wav(output, np.concatenate(parts), rate)
    _write_json(
        marker,
        {
            "sample_rate": rate,
            "samples": position,
            "seconds": round(seconds, 3),
            "gap_samples": int(gap.size),
            "assets": assets,
        },
    )
    return _samples_outputs(output, marker)


def _seed_outputs(staging: Path, path: Path) -> list:
    seed = _read_json(path)
    outputs = [
        (
            "seed",
            path,
            "analysis_report",
            {
                "seconds": seed["seconds"],
                "clips": len(seed["clips"]),
                "target_median_hz": seed["target_median_hz"],
            },
        )
    ]
    for clip in seed["clips"]:
        outputs.append(
            (
                f"clip:{clip['id']}",
                staging / f"{clip['id']}.wav",
                "voice_material",
                {"text": clip["text"], "seconds": clip["seconds"]},
            )
        )
    return outputs


def voice_seed(context):
    """The seed: clips cut from the joined recordings at their transcript's segments (the research's rule, inside each
    recording), with the target's speaking median from the speaker measurements. The manifest lists the clips by id
    with their text; each clip is a `voice_material` artifact."""
    spec = context.snapshot["step_spec"]
    rate = int(spec["sample_rate"])
    context.staging.mkdir(parents=True, exist_ok=True)
    path = context.staging / "seed.json"
    if path.is_file():
        return _seed_outputs(context.staging, path)
    manifest = _read_json(context.paths["manifest"])
    transcript = _read_json(context.paths["transcript"])
    speakers = _read_json(context.paths["speakers"])
    target = voice_build.target_median(speakers.get("items") or [])
    if target is None:
        raise ApplicationError("The recordings hold no voiced speech to measure.", 422)
    pcm, found_rate = _read_wav(context.paths["samples"])
    if found_rate != rate:
        raise ApplicationError("The joined recordings have an unexpected sample rate.", 409)
    bounds = [
        (asset["start_sample"] / rate, (asset["start_sample"] + asset["samples"]) / rate)
        for asset in manifest["assets"]
    ]
    segments = [
        (float(s["start"]), float(s["end"]), str(s.get("text") or ""))
        for s in transcript.get("segments") or []
    ]
    cut = spec["cut"]
    cuts = voice_build.seed_cuts(
        segments,
        min_s=float(cut["min"]),
        max_s=float(cut["max"]),
        pad_before=float(cut["pad_before"]),
        pad_after=float(cut["pad_after"]),
        total_s=float(spec["total_seconds"]),
        bounds=bounds,
    )
    if not cuts:
        raise ApplicationError(
            "The recordings hold no stretch of speech long enough for the seed.", 422
        )
    clips = []
    for n, (start, end, text) in enumerate(cuts):
        a, b = round(start * rate), min(round(end * rate), pcm.size)
        clip_id = f"seed{n:02d}"
        _write_wav(context.staging / f"{clip_id}.wav", pcm[a:b], rate)
        clips.append({"id": clip_id, "text": text, "seconds": round((b - a) / rate, 3)})
    _write_json(
        path,
        {
            "seconds": round(sum(clip["seconds"] for clip in clips), 3),
            "target_median_hz": target,
            "clips": clips,
        },
    )
    return _seed_outputs(context.staging, path)


def _suffix(n: int) -> str:
    """a, b, ... z, aa, ab, ...: the piece letters of a cut library clip."""
    letters = ""
    n += 1
    while n:
        n, rest = divmod(n - 1, 26)
        letters = chr(ord("a") + rest) + letters
    return letters


def _library_outputs(staging: Path, path: Path) -> list:
    document = _read_json(path)
    outputs = [("library", path, "analysis_report", {"clips": len(document["clips"])})]
    for clip in document["clips"]:
        outputs.append(
            (
                f"clip:{clip['id']}",
                staging / f"{clip['id']}.wav",
                "voice_material",
                {
                    "category": clip["category"],
                    "speaker": clip["speaker"],
                    "seconds": clip["seconds"],
                },
            )
        )
    return outputs


def _library_clip(context, spec, clip, folder: Path) -> dict:
    """One scanned recording as clips: {"pieces": [...]} or {"skipped": reason}. A recording that vanished or changed
    since the scan fails the step (the build's library would no longer be the one its request records)."""
    rate, hop = int(spec["sample_rate"]), int(spec["hop"])
    source = folder.joinpath(*clip["relative"].split("/"))
    try:
        stat = source.stat()
    except OSError as exc:
        raise ApplicationError(
            f"An expressive recording is missing: {clip['relative']}.", 409
        ) from exc
    if stat.st_size != clip["bytes"] or stat.st_mtime_ns != clip["mtime_ns"]:
        raise ApplicationError(
            f"An expressive recording changed during the build: {clip['relative']}.", 409
        )
    raw = context.staging / f"{clip['id']}.f32"
    try:
        context.library.media.decode_audio(source, raw, sample_rate=rate, mono=True)
    except ApplicationError:
        raw.unlink(missing_ok=True)
        return {"skipped": "unreadable"}
    try:
        values = np.fromfile(raw, dtype="<f4")
        seconds = values.size / rate
        if seconds < float(spec["min_seconds"]):
            return {"skipped": "too_short"}
        spans = [(0, values.size)]
        if seconds > float(spec["max_seconds"]):
            spans = voice_build.piece_spans(
                envelope_db(raw, hop),
                values.size,
                rate=rate,
                hop=hop,
                piece_seconds=float(spec["piece_seconds"]),
                window_seconds=float(spec["window_seconds"]),
            )
    finally:
        raw.unlink(missing_ok=True)
    pieces = []
    for n, (a, b) in enumerate(spans):
        piece_id = clip["id"] if len(spans) == 1 else f"{clip['id']}{_suffix(n)}"
        _write_wav(context.staging / f"{piece_id}.wav", _pcm16(values[a:b]), rate)
        pieces.append(
            {
                "id": piece_id,
                "category": clip["category"],
                "speaker": clip["speaker"],
                "seconds": round((b - a) / rate, 3),
                "source_relative": clip["relative"],
            }
        )
    return {"pieces": pieces}


def voice_library(context):
    """The expressive library as scanned when the build was asked for: each recording decoded to a mono PCM16 clip at
    48 kHz, a recording longer than the filter's limit cut into pieces at quiet frames; the manifest lists the clips
    with their category and speaker. Without a folder (unset or missing) the manifest is empty."""
    spec = context.snapshot["step_spec"]
    context.staging.mkdir(parents=True, exist_ok=True)
    path = context.staging / "library.json"
    if path.is_file():
        return _library_outputs(context.staging, path)
    clips, skipped = [], []
    if spec.get("state") == "ready" and spec.get("clips"):
        folder = Path(spec["folder"])
        for clip in spec["clips"]:
            sidecar = context.staging / f"{clip['id']}.json"
            if sidecar.is_file():
                record = _read_json(sidecar)
            else:
                record = _library_clip(context, spec, clip, folder)
                _write_json(sidecar, record)
            if record.get("skipped"):
                skipped.append({"relative": clip["relative"], "reason": record["skipped"]})
            else:
                clips.extend(record["pieces"])
    _write_json(path, {"clips": clips, "skipped": skipped})
    return _library_outputs(context.staging, path)


def voice_candidates(context):
    """The filter's candidate manifests: the rows (ids, categories, kinds, texts; never paths) in chunks of
    `per_job`, as `candidates:<n>`."""
    spec = context.snapshot["step_spec"]
    context.staging.mkdir(parents=True, exist_ok=True)
    outputs = []
    for n, piece in enumerate(voice_build.chunks(spec["rows"], int(spec["per_job"]))):
        path = context.staging / f"candidates_{n}.json"
        if not path.is_file():
            _write_json(path, piece)
        outputs.append((f"candidates:{n}", path, "analysis_report", {"rows": len(piece)}))
    return outputs


def _filter_summary(scored: list[dict]) -> dict:
    kept = [row for row in scored if row.get("kept")]
    by_category = {}
    for row in scored:
        entry = by_category.setdefault(row.get("category"), {"kept": 0, "of": 0, "minutes": 0.0})
        entry["of"] += 1
        if row.get("kept"):
            entry["kept"] += 1
            entry["minutes"] += float(row.get("seconds") or 0) / 60
    return {
        "kept": len(kept),
        "of": len(scored),
        "minutes": round(sum(float(row.get("seconds") or 0) for row in kept) / 60, 2),
        "by_category": {
            category: entry | {"minutes": round(entry["minutes"], 2)}
            for category, entry in sorted(by_category.items(), key=lambda item: str(item[0]))
        },
    }


def voice_package(context):
    """The `voice_model` manifest: the weights and index artifacts with their hashes, the target median, the seed,
    dataset and filter summaries, the request (with its library digest), the recipe and registry fingerprints."""
    spec = context.snapshot["step_spec"]
    context.staging.mkdir(parents=True, exist_ok=True)
    path = context.staging / "voice_model.json"
    if not path.is_file():
        seed = _read_json(context.paths["seed"])
        dataset = _read_json(context.paths["dataset"])
        items = _read_json(context.paths["speakers"]).get("items") or []
        scored = [
            row
            for key in sorted(context.paths, key=lambda k: (len(k), k))
            if key.startswith("scored:")
            for row in _read_json(context.paths[key])
        ]
        manifest = {
            "schema": 1,
            "version": spec["version"],
            "operation_id": spec["operation_id"],
            "samples": spec["samples"],
            "weights": spec["weights"],
            "index": spec["index"],
            "epochs": spec.get("epochs"),
            "target_median_hz": seed["target_median_hz"],
            "seed": {
                "seconds": seed["seconds"],
                "clips": len(seed["clips"]),
                "segments": len(items),
                "voiced_segments": sum(item.get("median_hz") is not None for item in items),
            },
            "dataset": {key: dataset.get(key) for key in voice_build.DATASET_KEYS},
            "filter": _filter_summary(scored),
            "library": spec["request"].get("library") or {},
            "recipe_fingerprint": spec["recipe_fingerprint"],
            "engine_registry": spec["engine_registry"],
        }
        _write_json(path, manifest)
    manifest = _read_json(path)
    return [
        (
            "model",
            path,
            "voice_model",
            {
                "epochs": manifest.get("epochs"),
                "dataset_seconds": manifest["dataset"].get("dataset_seconds"),
                "kept": manifest["filter"]["kept"],
                "target_median_hz": manifest["target_median_hz"],
            },
        )
    ]


# --- the audition --------------------------------------------------------------------------------------------------


def _clip_name(key: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", key).strip("._") or "line"


def _stem(context, rate: int) -> Path:
    """The dialogue stem as a raw mono float32 file at `rate`; lines are read from it by offset (a film's stem can be
    long)."""
    raw = context.staging / "stem.f32"
    if not raw.is_file():
        context.library.media.decode_audio(context.paths["stem"], raw, sample_rate=rate, mono=True)
    return raw


def _cut(raw: Path, line: dict) -> np.ndarray:
    """A line's samples [start, end) of the stem, zero-padded past its end: exactly end - start samples."""
    start, end = int(line["start_sample"]), int(line["end_sample"])
    piece = np.zeros(end - start, dtype=np.float32)
    if start * 4 < raw.stat().st_size:
        available = np.fromfile(raw, dtype="<f4", count=end - start, offset=start * 4)
        piece[: available.size] = available
    return piece


def voice_audition_cut(context):
    """The audition's lines cut from the dialogue stem at their integer samples (mono PCM16 at the stem's rate), and
    their whispered spans relative to each clip, keyed by line."""
    spec = context.snapshot["step_spec"]
    rate = int(spec["sample_rate"])
    context.staging.mkdir(parents=True, exist_ok=True)
    spans_path = context.staging / "spans.json"
    if not spans_path.is_file():
        raw = _stem(context, rate)
        for line in spec["lines"]:
            target = context.staging / f"{_clip_name(line['key'])}.wav"
            if not target.is_file():
                _write_wav(target, _pcm16(_cut(raw, line)), rate)
        raw.unlink(missing_ok=True)
        _write_json(spans_path, {line["key"]: line["whisper_spans"] for line in spec["lines"]})
    outputs = [("spans", spans_path, "analysis_report", {"lines": len(spec["lines"])})]
    for line in spec["lines"]:
        target = context.staging / f"{_clip_name(line['key'])}.wav"
        pcm, _ = _read_wav(target)
        outputs.append(
            (
                f"clip:{line['key']}",
                target,
                "voice_material",
                {"seconds": round(pcm.size / rate, 6), "rms_db": _rms_db(pcm)},
            )
        )
    return outputs


def voice_reel(context):
    """The audition reel: per line in pick order the original (cut from the stem as `voice_audition_cut` cuts it),
    `between` seconds of silence, the converted line gained to the original's RMS (a reel-only gain), `after` seconds
    of silence; mono PCM16 at the stem's rate. The reel is the operation's one candidate."""
    spec = context.snapshot["step_spec"]
    rate = int(spec["sample_rate"])
    context.staging.mkdir(parents=True, exist_ok=True)
    reel_path, report_path = context.staging / "audition.wav", context.staging / "reel.json"
    if not (reel_path.is_file() and report_path.is_file()):
        between = np.zeros(round(float(spec["gaps"]["between"]) * rate), dtype=np.float32)
        after = np.zeros(round(float(spec["gaps"]["after"]) * rate), dtype=np.float32)
        raw = _stem(context, rate)
        parts, rows = [], []
        for n, line in enumerate(spec["lines"]):
            original = _cut(raw, line)
            converted = _decode(
                context,
                context.paths[f"converted:{line['key']}"],
                context.staging / f"converted{n:03d}.f32",
                rate,
            )
            gain_db = _match_gain(original, converted)
            parts += [original, between, converted * 10 ** (gain_db / 20), after]
            rows.append(
                {
                    "key": line["key"],
                    "gain_db": gain_db,
                    "seconds": round(original.size / rate, 6),
                    "converted_seconds": round(converted.size / rate, 6),
                }
            )
        raw.unlink(missing_ok=True)
        _write_wav(reel_path, _pcm16(np.concatenate(parts)), rate)
        _write_json(report_path, {"sample_rate": rate, "lines": rows})
    report = _read_json(report_path)
    return [
        ("reel", reel_path, "audio", {"lines": len(report["lines"])}),
        ("reel_report", report_path, "analysis_report", {"lines": len(report["lines"])}),
    ]


def _match_gain(original: np.ndarray, converted: np.ndarray) -> float:
    """dB that bring the converted line to the original's RMS; 0 when either is silent."""

    def rms(values):
        values = np.asarray(values, dtype=np.float64)
        return float(np.sqrt(np.mean(values * values))) if values.size else 0.0

    a, b = rms(original), rms(converted)
    if a <= 1e-9 or b <= 1e-9:
        return 0.0
    return round(20 * float(np.log10(a / b)), 3)
