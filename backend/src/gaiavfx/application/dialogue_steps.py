"""Local steps of the dialogue analysis (ADR 0016 §2): the mix on the picture's clock, and the dialogue envelope.

Both are resumable: a step that finds its finished output (written last, after validation) returns it again.
"""

import json
from fractions import Fraction

import numpy as np

from gaiavfx.application.dialogue_analysis import HOP, gap_for
from gaiavfx.shared.errors import ApplicationError

FRAMES_PER_READ = 4096


def _fraction(value) -> Fraction:
    try:
        return Fraction(str(value))
    except (TypeError, ValueError, ZeroDivisionError):
        return Fraction(0)


def dialogue_audio(context):
    """The film's first audio stream as float PCM at 48 kHz whose sample 0 is the instant of video frame 0."""
    spec = context.snapshot["step_spec"]
    rate = int(spec["sample_rate"])
    context.staging.mkdir(parents=True, exist_ok=True)
    output = context.staging / "mix.wav"
    marker = context.staging / "mix.json"
    if output.is_file() and marker.is_file():
        info = json.loads(marker.read_text(encoding="utf-8"))
        return [("mix", output, "audio_stem", info)]
    source = context.paths[spec["source_asset"]]
    probe = context.library.media.probe(source)
    gap = gap_for(probe)
    if gap:
        # The import's probe may predate the channel count: refuse here again, with the same sentence.
        raise ApplicationError(gap["message"], 422)
    offset = _fraction(probe.get("audio_start_time", "0")) - _fraction(
        probe.get("video_start_time", "0")
    )
    extracted = context.library.media.extract_audio(
        source,
        output,
        sample_rate=rate,
        offset=offset,
        channels=int(probe.get("audio_channels") or 0),
    )
    info = {
        "sample_rate": rate,
        "channels": extracted["channels"],
        "samples": extracted["samples"],
        "offset_samples": extracted["offset_samples"],
        "role": "mix",
    }
    staged = marker.with_suffix(".writing.json")
    staged.write_text(json.dumps(info), encoding="utf-8")
    staged.replace(marker)
    return [("mix", output, "audio_stem", info)]


def envelope_db(path, hop=HOP):
    """10 ms RMS levels in dB of a raw mono float32 file, read in chunks; a trailing partial frame is dropped."""
    levels = []
    carry = np.empty(0, dtype=np.float32)
    with path.open("rb") as stream:
        while True:
            chunk = np.fromfile(stream, dtype="<f4", count=hop * FRAMES_PER_READ)
            if not chunk.size:
                break
            values = np.concatenate([carry, chunk]) if carry.size else chunk
            whole = values.size // hop * hop
            frames = values[:whole].astype(np.float64).reshape(-1, hop)
            levels.append(10 * np.log10(np.mean(frames * frames, axis=1) + 1e-12))
            carry = values[whole:]
    return np.concatenate(levels) if levels else np.empty(0)


def dialogue_envelope(context):
    """The dialogue stem's envelope (`db` per 480-sample hop at 48 kHz) as an `analysis_report`."""
    spec = context.snapshot["step_spec"]
    rate, hop = int(spec["sample_rate"]), int(spec["hop"])
    context.staging.mkdir(parents=True, exist_ok=True)
    output = context.staging / "envelope.npz"
    if output.is_file():
        return [("envelope", output, "analysis_report", {"hop": hop, "rate": rate})]
    raw = context.staging / "dialogue.f32"
    context.library.media.decode_audio(context.paths["dialogue"], raw, sample_rate=rate, mono=True)
    try:
        db = envelope_db(raw, hop)
    finally:
        raw.unlink(missing_ok=True)
    staged = context.staging / "envelope.writing.npz"
    with staged.open("wb") as stream:
        np.savez(
            stream,
            db=db.astype(np.float32),
            hop=np.int32(hop),
            rate=np.int32(rate),
            peak_db=np.float32(db.max() if db.size else -120.0),
        )
    staged.replace(output)
    return [
        ("envelope", output, "analysis_report", {"hop": hop, "rate": rate, "frames": int(db.size)})
    ]
