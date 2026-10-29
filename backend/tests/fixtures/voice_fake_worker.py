"""A fake voice engine for the voice build tests (ADR 0016 P5): the real protocol, no model.

One worker for every engine step of the build and the audition, dispatched on the job's `step`. Where the real
workers plan from manifests (seed clips, candidate rows, kept rows, Seed-VC pitch shifts, takes), the fake calls the
real workers' standard-library planning functions, so a manifest the real worker could not resolve fails here too.

- `analysis.transcribe`: a two-second segment every 2.5 s of the input, four words each.
- `analysis.speakers`: a unit vector and `median_hz` 190 per transcript segment; for `clip:<id>` inputs the clip's
  pitch from its zero crossings (the tests' clips are tones), keyed by clip id.
- `analysis.filter`: keeps every row whose id does not contain `VOICE_FAKE_DROP` (default "bad").
- `tts.generate`: take 0 of every planned line (every `VOICE_FAKE_EVERY`-th line when set), 0.5 s, at 24 kHz for
  `higgs_audio_tts` and `omnivoice`, 48 kHz otherwise; skips takes already present (a relaunch).
- `tts.superres`: clips below `below_hz` are repeated up to 48 kHz.
- `seedvc.finetune`: `scratch/finetune/ft_model.pth`; `reused` when it was there already.
- `seedvc.convert`, `rvc.convert`: each clip at 0.9 of its level.
- `rvc.prepare`: `scratch/prepare/manifest.json` from the real dataset plan.
- `rvc.train`: appends each chunk to `scratch/train/state.json`; a `scratch/train/crash` file naming an epoch fails
  that chunk once (the file is removed).
- `rvc.index`: a small index file.

`VOICE_FAKE_CRASH` (comma separated steps) fails those steps on every launch. `VOICE_FAKE_HANG` (`<step>` or
`tts.generate:<generator>`) hangs the first launch after its first output, with a `hanging` file in the work
directory, so a test can stop the runner there.
"""

import array
import itertools
import json
import math
import os
import sys
import time
import wave
import zlib
from pathlib import Path

WORKERS = Path(__file__).resolve().parents[3] / "engine_workers"
sys.path.insert(0, str(WORKERS))
import analysis  # the real workers' standard-library planning
import protocol
import rvc
import seedvc
import tts

LOW_RATE = {"higgs_audio_tts", "omnivoice"}


def write_tone(path, seconds, rate, hz, gain=0.3):
    count = round(seconds * rate)
    step = 2 * math.pi * hz / rate
    samples = array.array("h", (int(gain * 32767 * math.sin(step * n)) for n in range(count)))
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())


def read_wav(path):
    with wave.open(str(path), "rb") as stream:
        rate, channels = stream.getframerate(), stream.getnchannels()
        samples = array.array("h", stream.readframes(stream.getnframes()))
    if channels > 1:
        samples = array.array("h", samples[::channels])
    return rate, samples


def write_samples(path, rate, samples):
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(samples.tobytes())


def scaled(samples, gain):
    return array.array("h", (int(value * gain) for value in samples))


def pitch(path):
    """Hz of a tone from its zero crossings."""
    rate, samples = read_wav(path)
    crossings = sum(1 for a, b in itertools.pairwise(samples) if (a < 0) != (b < 0))
    return round(crossings / 2 / (len(samples) / rate), 1) if samples else None


def behaviour(job):
    crash = {item.strip() for item in os.environ.get("VOICE_FAKE_CRASH", "").split(",")}
    if job.document["step"] in crash:
        raise RuntimeError(f"boom: the model file is missing at C:/engines/{job.task}/model.bin")


def hang_once(job, *units):
    wanted = os.environ.get("VOICE_FAKE_HANG", "")
    names = {job.document["step"], *(f"{job.document['step']}:{unit}" for unit in units)}
    marker = job.work / "hang-done"
    if wanted in names and not marker.exists():
        marker.write_text("1")
        (job.work / "hanging").write_text("1")
        for _ in range(2400):
            job.check()  # a cancel ends the hang at once; a shutdown kills the worker
            time.sleep(0.05)


def output_json(job, key, name, document, kind="analysis_report", **info):
    target = job.target(name)
    with job.writing(target) as temporary:
        temporary.write_text(json.dumps(document), encoding="utf-8")
    job.output(key, target, kind, **info)


def unit_vector(seed):
    vector = [math.cos(seed + d) for d in range(8)]
    norm = math.sqrt(sum(v * v for v in vector))
    return [round(v / norm, 6) for v in vector]


def transcribe(job):
    behaviour(job)
    rate, samples = read_wav(job.inputs["audio"])
    seconds = len(samples) / rate
    segments, start, n = [], 0.25, 0
    while start + 2.0 <= seconds:
        words = [
            {"start": round(start + 0.5 * w, 3), "end": round(start + 0.5 * w + 0.4, 3)}
            | {"word": f"word{n}-{w}", "probability": 0.9}
            for w in range(4)
        ]
        segments.append(
            {
                "start": round(start, 3),
                "end": round(start + 2.0, 3),
                "text": " ".join(w["word"] for w in words),
                "words": words,
            }
        )
        start, n = start + 2.5, n + 1
    output_json(
        job,
        "transcript",
        "transcript.json",
        {"language": "en", "segments": segments},
        language="en",
        segments=len(segments),
    )


def speakers(job):
    behaviour(job)
    items = []
    if "segments" in job.inputs:
        document = json.loads(job.inputs["segments"].read_text(encoding="utf-8"))
        for index, (a, b) in enumerate(analysis.segments_of(document)):
            items.append(
                {"key": f"{a:.3f}-{b:.3f}", "start": a, "end": b, "ecapa": unit_vector(index)}
                | {"median_hz": 190.0, "voiced_frames": 40}
            )
    else:
        for index, (key, path) in enumerate(analysis.clips(job).items()):
            items.append(
                {"key": key, "ecapa": unit_vector(index), "median_hz": pitch(path)}
                | {"voiced_frames": 40}
            )
    output_json(
        job,
        "speakers",
        "speakers.json",
        {"embedding": "ecapa", "sample_rate": 16000, "items": items, "profile": unit_vector(0)},
        kind="speaker_embeddings",
        items=len(items),
    )


def filter_clips(job):
    behaviour(job)
    seed = analysis.seed_paths(
        json.loads(job.inputs["seed"].read_text(encoding="utf-8")), job.inputs
    )
    if not seed or not all(path.is_file() for path in seed):
        raise RuntimeError("analysis.filter needs the seed's clips.")
    rows = json.loads(job.inputs["candidates"].read_text(encoding="utf-8"))
    drop = os.environ.get("VOICE_FAKE_DROP", "bad")
    scored = []
    for row in rows:
        path, source = analysis.row_files(row, job.inputs)
        rate, samples = read_wav(path)
        if source is not None:
            read_wav(source)
        kept = drop not in str(row["id"])
        scored.append(
            {**row, "seconds": round(len(samples) / rate, 2), "ecapa_to_seed": 0.5}
            | {"failed": [] if kept else ["identity"], "kept": kept}
        )
    kept = [row for row in scored if row["kept"]]
    output_json(
        job,
        "kept",
        "kept.json",
        kept,
        kept=len(kept),
        of=len(scored),
        minutes=round(sum(r["seconds"] for r in kept) / 60, 2),
    )
    output_json(job, "scored", "scored.json", scored, clips=len(scored))


def generate(job):
    behaviour(job)
    params = job.params
    name = params["generator"]
    clips = tts.seed_clips(
        json.loads(job.inputs["seed"].read_text(encoding="utf-8")),
        job.inputs["seed"].parent,
        job.inputs,
    )
    if not all(Path(clip["path"]).is_file() for clip in clips):
        raise RuntimeError("A seed clip has no file.")
    planned, skipped = tts.plan_takes(params, clips, job.seed)
    every = int(os.environ.get("VOICE_FAKE_EVERY", "1"))
    planned = [t for t in planned if t["take"] == 0 and t["index"] % every == 0]
    rate = 24000 if name in LOW_RATE else 48000
    made = []
    for take in planned:
        target = job.target(f"{take['line']}_{take['take']}.wav")
        if target.exists():
            print(f"present {take['id']}", flush=True)
        else:
            with job.writing(target) as temporary:
                write_tone(temporary, 0.5, rate, 150 + zlib.crc32(take["id"].encode()) % 400)
        job.output(
            f"take:{take['line']}_{take['take']}",
            target,
            "audio",
            generator=name,
            line=take["line"],
            take=take["take"],
            category=take["category"],
            line_kind=take["line_kind"],
            text=take["text"],
            sample_rate=rate,
            seconds=0.5,
            reference=take["reference_id"],
        )
        made.append(take["id"])
        hang_once(job, name)
    output_json(
        job,
        "report",
        "report.json",
        {"generator": name, "planned": len(planned), "made": made, "skipped": skipped},
        made=len(made),
        failed=0,
        skipped=len(skipped),
    )


def superres(job):
    behaviour(job)
    lifted = []
    for key, path in sorted(job.inputs.items()):
        if not key.startswith("clip:"):
            continue
        rate, samples = read_wav(path)
        if rate >= int(job.params["below_hz"]):
            continue
        factor = int(job.params["sample_rate"]) // rate
        target = job.target(tts.safe_name(key.split(":", 1)[1]) + ".wav")
        with job.writing(target) as temporary:
            write_samples(
                temporary,
                rate * factor,
                array.array("h", (value for value in samples for _ in range(factor))),
            )
        job.output(key, target, "audio", sample_rate=rate * factor, source_rate=rate)
        lifted.append(key)
    output_json(job, "report", "report.json", {"lifted": lifted}, lifted=len(lifted))


def finetune(job):
    behaviour(job)
    clips = seedvc.clips(job)
    folder = job.scratch / "finetune"
    folder.mkdir(parents=True, exist_ok=True)
    model = folder / "ft_model.pth"
    reused = model.exists()
    if not reused:
        with job.writing(model) as temporary:
            temporary.write_bytes(b"fine-tuned " + json.dumps(sorted(clips)).encode())
    hang_once(job)
    target = job.target("ft_model.pth")
    target.write_bytes(model.read_bytes())
    job.output("model", target, "seedvc_model", clips=len(clips), reused=reused)


def seedvc_convert(job):
    behaviour(job)
    sources = seedvc.clips(job)
    shifts = seedvc.clip_shifts(job.params, sources)
    seedvc.references(job.inputs)
    if not seedvc.fine_tuned(job)[0].is_file():
        raise RuntimeError("No fine-tuned model.")
    for key, path in sources.items():
        rate, samples = read_wav(path)
        target = job.target(seedvc.safe_name(key.split(":", 1)[-1]) + ".wav")
        with job.writing(target) as temporary:
            write_samples(temporary, rate, scaled(samples, 0.9))
        job.output(key, target, "audio", pitch_shift=shifts[key], sample_rate=rate)


def prepare(job):
    behaviour(job)
    seed = rvc.seed_clips(json.loads(job.inputs["seed"].read_text(encoding="utf-8")), job.inputs)
    rows = rvc.kept_rows(
        [
            row
            for key, path in sorted(job.inputs.items())
            if key == "kept" or key.startswith("kept:")
            for row in json.loads(path.read_text(encoding="utf-8"))
        ],
        job.inputs,
    )
    plan = rvc.dataset_plan(seed, rows, job.params["dataset"])
    report = {
        **rvc.dataset_summary(plan),
        "dataset_seconds": 60.0,
        "slices": len(plan),
        "sample_rate": 48000,
        "files": [
            {key: item.get(key) for key in ("name", "category", "seconds", "id", "generator")}
            | {"levelled": item["lufs"] is not None}
            for item in plan
        ],
    }
    folder = job.scratch / "prepare"
    folder.mkdir(parents=True, exist_ok=True)
    with job.writing(folder / "manifest.json") as temporary:
        temporary.write_text(json.dumps({"identity": "fake", "report": report}), encoding="utf-8")
    output_json(job, "dataset", "dataset.json", report, clips=report["clips"])


def prepared(job):
    if not (job.scratch / "prepare" / "manifest.json").is_file():
        raise RuntimeError("rvc: nothing is prepared in the scratch.")


def train(job):
    behaviour(job)
    prepared(job)
    folder = job.scratch / "train"
    folder.mkdir(parents=True, exist_ok=True)
    state_path = folder / "state.json"
    state = (
        json.loads(state_path.read_text(encoding="utf-8"))
        if state_path.exists()
        else {"epoch": 0, "chunks": []}
    )
    crash = folder / "crash"
    for end in rvc.chunk_ends(
        state["epoch"], int(job.params["epochs"]), int(job.params["chunk_epochs"])
    ):
        if crash.exists() and crash.read_text(encoding="utf-8").strip() == str(end):
            crash.unlink()
            raise RuntimeError(f"Applio's training to epoch {end} failed (exit 1); see the log.")
        state = {"epoch": end, "chunks": [*state["chunks"], end]}
        with job.writing(state_path) as temporary:
            temporary.write_text(json.dumps(state), encoding="utf-8")
    target = job.target("voice.pth")
    target.write_bytes(b"w" * int(job.params["epochs"]))
    job.output("model", target, "voice_weights", epochs=state["epoch"], dataset_seconds=60.0)


def index(job):
    behaviour(job)
    prepared(job)
    target = job.target("voice.index")
    target.write_bytes(b"index")
    job.output("index", target, "voice_index", algorithm=job.params["algorithm"], bytes=5)


def rvc_convert(job):
    behaviour(job)
    if not job.inputs["model"].is_file() or not job.inputs["index"].is_file():
        raise RuntimeError("rvc.convert needs the model and the index.")
    clips = rvc.clips(job)
    spans = rvc.spans_for(
        json.loads(job.inputs["spans"].read_text(encoding="utf-8"))
        if "spans" in job.inputs
        else None,
        list(clips),
    )
    for key, path in clips.items():
        rate, samples = read_wav(path)
        output_key, name = rvc.output_of(key)
        target = job.target(name)
        with job.writing(target) as temporary:
            write_samples(temporary, rate, scaled(samples, 0.9))
        job.output(output_key, target, "audio", source=key, spans=spans.get(key, []))


STEPS = {
    "analysis.transcribe": transcribe,
    "analysis.speakers": speakers,
    "analysis.filter": filter_clips,
    "tts.generate": generate,
    "tts.superres": superres,
    "seedvc.finetune": finetune,
    "seedvc.convert": seedvc_convert,
    "rvc.prepare": prepare,
    "rvc.train": train,
    "rvc.index": index,
    "rvc.convert": rvc_convert,
}


def dispatch(job):
    STEPS[job.document["step"]](job)


TASKS = {step.split(".", 1)[1]: dispatch for step in STEPS}

if __name__ == "__main__":
    sys.exit(protocol.run(TASKS))
