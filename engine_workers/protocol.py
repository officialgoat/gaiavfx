"""The engine worker protocol (ADR 0015). Standard library only; a worker never imports GAIAVFX.

A worker is started as `<interpreter> <worker.py> --job <work>/job.json` with its work directory as the current
directory, or as `<worker.py> --probe`. The job document is

    {"schema": 1, "job_id": str, "step": "<engine>.<task>", "task": str, "params": {...},
     "inputs": {key: absolute path}, "seed": int | null,
     "engine": {"home": absolute path | null, "programs": {name: absolute path}},
     "scratch": absolute path | null}

`scratch` is a folder shared by every job of one operation (a training run's checkpoints, a fine-tuned model): it
survives relaunches and the job's own work directory, which is removed after collection. Its contract:

- a task keeps its state in its own subfolder (`scratch/<task>/...`), never at the top;
- every file there is written by atomic rename (`Job.writing`), so a stopped job leaves either the old or the new
  checkpoint, never half of one;
- state that depends on inputs or settings carries their identity (a manifest of input hashes and params) and a job
  that finds a different identity starts its state again instead of resuming it;
- outputs are still published inside the job's own work directory: a relaunch rebuilds them from scratch state and
  writes `result.json` again;
- retention belongs to the operation that owns the scratch (decided with the voice build, ADR 0016 P5).

While it runs, a worker appends progress lines to `progress.jsonl` ({"progress": 0..1, "message": str, ...}) and
checks for a `cancel` file. It writes its outputs inside the work directory, each by atomic rename, and finishes by
writing `result.json`: {"schema": 1, "outputs": [{"key", "path" (relative to the work directory), "kind", "info"}]}.
Exit code 0 means success, 3 cancelled, anything else failure (the log holds the reason). A relaunch reuses the work
directory: a worker skips outputs it already finished.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import traceback
from contextlib import contextmanager
from pathlib import Path

SCHEMA = 1
CANCELLED_EXIT = 3


class Cancelled(Exception):
    """Raised by a worker when it has seen the cancel file."""


class Job:
    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.work = self.path.parent
        self.document = json.loads(self.path.read_text(encoding="utf-8"))
        if self.document.get("schema") != SCHEMA:
            raise ValueError("Unsupported job document schema.")
        self.task = self.document["task"]
        self.params = self.document.get("params") or {}
        self.inputs = {
            key: Path(value)
            for key, value in (self.document.get("inputs") or {}).items()
        }
        self.seed = self.document.get("seed")
        scratch = self.document.get("scratch")
        self.scratch = Path(scratch) if scratch else None
        engine = self.document.get("engine") or {}
        self.home = Path(engine["home"]) if engine.get("home") else None
        self.programs = {
            name: Path(path) for name, path in (engine.get("programs") or {}).items()
        }
        self.outputs: list[dict] = []

    def program(self, name: str) -> Path:
        """A native program the registry lists for this engine (audio.cpp's CLI, for example)."""
        if name not in self.programs:
            raise KeyError(f"The engine registers no program {name!r}.")
        return self.programs[name]

    def progress(self, value: float, message: str = "", **info) -> None:
        line = json.dumps(
            {"progress": max(0.0, min(float(value), 1.0)), "message": message, **info}
        )
        with (self.work / "progress.jsonl").open("a", encoding="utf-8") as stream:
            stream.write(line + "\n")
            stream.flush()

    def cancelled(self) -> bool:
        return (self.work / "cancel").exists()

    def check(self) -> None:
        if self.cancelled():
            raise Cancelled

    def target(self, name: str) -> Path:
        """A path for an output inside the work directory's `outputs/` folder."""
        folder = self.work / "outputs"
        folder.mkdir(exist_ok=True)
        path = (folder / name).resolve()
        if not path.is_relative_to(folder.resolve()):
            raise ValueError("Output names stay inside the work directory.")
        return path

    @contextmanager
    def writing(self, path: Path):
        """Yield a temporary path; on success it replaces `path` atomically."""
        temporary = path.with_name(path.name + ".partial")
        temporary.unlink(missing_ok=True)
        yield temporary
        os.replace(temporary, path)

    def output(self, key: str, path: Path, kind: str, **info) -> None:
        relative = Path(path).resolve().relative_to(self.work)
        self.outputs.append(
            {"key": key, "path": relative.as_posix(), "kind": kind, "info": info}
        )

    def finish(self) -> None:
        document = {"schema": SCHEMA, "outputs": self.outputs}
        temporary = self.work / "result.json.partial"
        temporary.write_text(json.dumps(document), encoding="utf-8")
        os.replace(temporary, self.work / "result.json")


def run(tasks: dict, probe=None, argv=None) -> int:
    """Dispatch `--job` to `tasks[job.task](job)` or answer `--probe`; returns the exit code."""
    parser = argparse.ArgumentParser()
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--job")
    group.add_argument("--probe", action="store_true")
    args = parser.parse_args(argv)
    if args.probe:
        print(
            json.dumps(
                {"schema": SCHEMA, "tasks": sorted(tasks), **(probe() if probe else {})}
            )
        )
        return 0
    job = Job(Path(args.job))
    handler = tasks.get(job.task)
    if handler is None:
        print(f"Unknown task {job.task!r}.", file=sys.stderr)
        return 2
    try:
        handler(job)
    except Cancelled:
        print("Cancelled.", file=sys.stderr)
        return CANCELLED_EXIT
    except Exception:  # noqa: BLE001 - the traceback goes to the job log
        traceback.print_exc()
        return 1
    job.finish()
    return 0
