"""A fake engine worker for tests: the real protocol (`engine_workers/protocol.py`), no model."""

import json
import math
import os
import struct
import subprocess
import sys
import time
import wave
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[3] / "engine_workers"))
import protocol


def tone(path, seconds=0.25, rate=48000):
    samples = int(seconds * rate)
    frames = b"".join(
        struct.pack("<h", int(8000 * math.sin(2 * math.pi * 440 * n / rate)))
        for n in range(samples)
    )
    with wave.open(str(path), "wb") as stream:
        stream.setnchannels(1)
        stream.setsampwidth(2)
        stream.setframerate(rate)
        stream.writeframes(frames)


def echo(job):
    """One 0.25 s tone as `line`, of kind `audio` unless `params["kind"]` names another."""
    job.progress(0.5, "halfway")
    target = job.target("line.wav")
    with job.writing(target) as temporary:
        tone(temporary)
    job.output("line", target, job.params.get("kind", "audio"), seconds=0.25)
    job.progress(1.0, "done")


def report(job):
    target = job.target("report.json")
    with job.writing(target) as temporary:
        temporary.write_text(json.dumps({"schema": 1, "segments": []}), encoding="utf-8")
    job.output("report", target, "analysis_report", segments=0)


def slow(job):
    (job.work / "worker.pid").write_text(str(os.getpid()))
    if job.params.get("spawn"):
        child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"])
        (job.work / "child.pid").write_text(str(child.pid))
    for step in range(2400):
        job.progress(step / 2400, "working")
        if not job.params.get("ignore_cancel"):
            job.check()
        time.sleep(0.05)


def crash(job):
    print("loading the model", file=sys.stderr)
    raise RuntimeError("boom: the model file is missing")


def chunks(job):
    """Writes `n` parts; the first launch hangs before part `hang_at` (to be stopped)."""
    skipped = 0
    for index in range(job.params["n"]):
        target = job.target(f"part{index}.wav")
        if target.exists():
            skipped += 1
            continue
        marker = job.work / "relaunched"
        if job.params.get("hang_at") == index and not marker.exists():
            marker.write_text("1")
            time.sleep(120)
        with job.writing(target) as temporary:
            tone(temporary)
        job.progress((index + 1) / job.params["n"], f"part {index}")
    print(f"skipped {skipped}")
    for index in range(job.params["n"]):
        job.output(f"part{index}", job.target(f"part{index}.wav"), "audio")


def escape(job):
    outside = job.work.parent / "escape.wav"
    tone(outside)
    job.outputs.append({"key": "x", "path": "../escape.wav", "kind": "audio", "info": {}})


def engine_info(job):
    """Succeeds only when the job document carried the engine's home and its registered program."""
    assert job.home == Path(job.params["home"]), job.home
    assert job.program("cli") == Path(job.params["cli"])
    echo(job)


def scratch_note(job):
    """Appends its name to the operation's shared scratch and expects the earlier jobs' names there."""
    notes = job.scratch / "notes.txt"
    seen = notes.read_text(encoding="utf-8").split() if notes.exists() else []
    assert seen == job.params["expect"], seen
    with notes.open("a", encoding="utf-8") as stream:
        stream.write(job.params["name"] + "\n")
    echo(job)


def env_check(job):
    """Succeeds only in a worker environment: no app Python paths, offline, the engine's temp folder."""
    assert "PYTHONPATH" not in os.environ, os.environ.get("PYTHONPATH")
    assert os.environ["HF_HUB_OFFLINE"] == "1" and os.environ["TRANSFORMERS_OFFLINE"] == "1"
    assert os.environ["TMP"] == job.params["tmp"] == os.environ["TEMP"]
    echo(job)


TASKS = {
    "echo": echo,
    "gpu_echo": echo,
    "report": report,
    "slow": slow,
    "crash": crash,
    "chunks": chunks,
    "escape": escape,
    "engine_info": engine_info,
    "scratch_note": scratch_note,
    "env_check": env_check,
}

if __name__ == "__main__":
    sys.exit(protocol.run(TASKS))
