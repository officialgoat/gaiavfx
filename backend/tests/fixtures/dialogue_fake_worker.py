"""A fake analysis engine for the dialogue analysis tests: the real protocol, no model.

`separate` copies its input as two PCM WAV stems (the dialogue at full level, the remainder at half), `transcribe`
writes a three-segment transcript with words, `speakers` one 192-dimension unit vector and a median pitch per
transcript segment (in its order), `tag` a whispered stretch keyed by the input key. `--crash <task>` makes that
task fail; `--slow <task>` makes it wait for the cancel file.
"""

import json
import math
import struct
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "engine_workers"))
import protocol

SEGMENTS = [
    (0.20, 0.80, [("Hello", 0.20, 0.45), ("there.", 0.50, 0.80)]),
    (1.00, 1.50, [("Go", 1.00, 1.20), ("now.", 1.25, 1.50)]),
    (1.70, 1.90, [("Oh.", 1.70, 1.90)]),
]


def read_wav(path):
    """(rate, channels, frames as float rows) of a PCM16 or float32 RIFF/RF64 WAV."""
    data = Path(path).read_bytes()
    if data[:4] not in (b"RIFF", b"RF64") or data[8:12] != b"WAVE":
        raise RuntimeError("not a WAV file")
    position, fmt, payload = 12, None, None
    while position + 8 <= len(data):
        name, size = (
            data[position : position + 4],
            struct.unpack("<I", data[position + 4 : position + 8])[0],
        )
        body = (
            data[position + 8 : position + 8 + size] if size != 0xFFFFFFFF else data[position + 8 :]
        )
        if name == b"fmt ":
            fmt = struct.unpack("<HHIIHH", body[:16])
        elif name == b"data":
            payload = body
            break
        position += 8 + size + (size & 1)
    _tag, channels, rate, _, _, bits = fmt
    if bits == 32:
        values = struct.unpack(f"<{len(payload) // 4}f", payload[: len(payload) // 4 * 4])
    else:
        values = [
            v / 32768
            for v in struct.unpack(f"<{len(payload) // 2}h", payload[: len(payload) // 2 * 2])
        ]
    return rate, channels, values


def write_wav(path, rate, channels, values, gain):
    frames = struct.pack(
        f"<{len(values)}h", *(max(-32768, min(32767, round(v * gain * 32767))) for v in values)
    )
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(channels)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(frames)


def behaviour(task):
    if task in FLAGS["crash"]:
        raise RuntimeError(
            f"boom: the {task} model file is missing at C:/engines/analysis/model.bin"
        )
    while task in FLAGS["slow"]:
        WORKER["job"].check()
        time.sleep(0.05)


def separate(job):
    behaviour("separate")
    rate, channels, values = read_wav(job.inputs["audio"])
    for key, gain in (("dialogue", 1.0), ("remainder", 0.5)):
        target = job.target(f"{key}.wav")
        with job.writing(target) as temporary:
            write_wav(temporary, rate, channels, values, gain)
        job.output(key, target, "audio_stem", sample_rate=rate, channels=channels)


def transcribe(job):
    behaviour("transcribe")
    document = {
        "language": "en",
        "segments": [
            {
                "start": start,
                "end": end,
                "text": " ".join(w for w, _, _ in words),
                "words": [
                    {"start": a, "end": b, "word": w, "probability": 0.9} for w, a, b in words
                ],
            }
            for start, end, words in SEGMENTS
        ],
    }
    target = job.target("transcript.json")
    with job.writing(target) as temporary:
        temporary.write_text(json.dumps(document), encoding="utf-8")
    job.output("transcript", target, "analysis_report", segments=len(SEGMENTS))


def speakers(job):
    behaviour("speakers")
    document = json.loads(job.inputs["segments"].read_text(encoding="utf-8"))
    items = []
    for index, row in enumerate(document["segments"]):
        a, b = float(row["start"]), float(row["end"])
        key = {"key": f"{a:.3f}-{b:.3f}", "start": a, "end": b}
        if b - a < 0.25:
            items.append({**key, "ecapa": None, "median_hz": None, "voiced_frames": 0})
            continue
        vector = [math.cos(index + d) for d in range(192)]
        norm = math.sqrt(sum(v * v for v in vector))
        items.append(
            {
                **key,
                "ecapa": [v / norm for v in vector],
                "median_hz": 110.0 + index,
                "voiced_frames": 40,
            }
        )
    target = job.target("speakers.json")
    with job.writing(target) as temporary:
        temporary.write_text(
            json.dumps({"embedding": "ecapa", "sample_rate": 16000, "items": items}),
            encoding="utf-8",
        )
    job.output("speakers", target, "speaker_embeddings", items=len(items))


def tag(job):
    behaviour("tag")
    target = job.target("spans.json")
    with job.writing(target) as temporary:
        temporary.write_text(
            json.dumps({key: [[1.0, 1.5]] for key in job.inputs}), encoding="utf-8"
        )
    job.output("spans", target, "analysis_report", clips=len(job.inputs))


TASKS = {"separate": separate, "transcribe": transcribe, "speakers": speakers, "tag": tag}
FLAGS = {"crash": set(), "slow": set()}
WORKER = {}


class _Job(protocol.Job):
    def __init__(self, path):
        super().__init__(path)
        WORKER["job"] = self


if __name__ == "__main__":
    arguments = sys.argv[1:]
    for flag in ("crash", "slow"):
        while f"--{flag}" in arguments:
            at = arguments.index(f"--{flag}")
            FLAGS[flag].add(arguments[at + 1])
            del arguments[at : at + 2]
    protocol.Job = _Job
    sys.exit(protocol.run(TASKS, argv=arguments))
