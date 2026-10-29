"""Engine jobs (ADR 0015): build them, and run one claimed job through its worker.

An engine job is a `JobSpec` with `executor="engine"` and `step="<engine>.<task>"`. Its snapshot freezes the task's
GPU need and VRAM estimate, the registry fingerprint and the worker's hash, so a run is traceable to exactly what was
registered when it was created. GPU tasks share the ComfyUI lease and ask ComfyUI to free its models first; CPU
tasks run without the lease and never resolve the ComfyUI endpoint.
"""

from __future__ import annotations

import copy
import hashlib
import json
import logging
import os
import time
from contextlib import nullcontext
from dataclasses import dataclass
from pathlib import Path

from gaiavfx.application import engine_registry as registry
from gaiavfx.infrastructure import engines
from gaiavfx.infrastructure.coordination import gpu_lease
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.integrations.voice import expressive
from gaiavfx.integrations.voice import spec as voice_spec
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.library.media_application import MANAGED_ARTIFACT_KINDS
from gaiavfx.shared.errors import ApplicationError

PROGRESS_SECONDS = 0.5
VRAM_WAIT_SECONDS = 5.0
# A worker runs in its engine's own environment: nothing of the app's Python may leak in, and nothing may reach the
# network for model files the setup has already installed.
STRIPPED_ENVIRONMENT = ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "CONDA_PREFIX")
WORKER_ENVIRONMENT = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "HF_DATASETS_OFFLINE": "1",
    "PYTHONNOUSERSITE": "1",
    "PYTHONUTF8": "1",
    # Bytecode caches would land inside the engine home (the Bandit repository, site-packages); homes stay read-only.
    "PYTHONDONTWRITEBYTECODE": "1",
}


@dataclass(frozen=True)
class EngineCommand:
    """How to start one engine's worker: the program and its arguments, before `--job`, plus the engine's home
    and the native programs its worker drives (both absolute; the job document hands them to the worker)."""

    command: tuple[str, ...]
    env: dict | None = None
    home: str | None = None
    programs: dict[str, str] | None = None


def _digest(path: Path) -> str | None:
    if not path.is_file():
        return None
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def engine_job(
    step: str,
    *,
    params: dict,
    inputs: dict | None = None,
    kind: str = "audio",
    integration: str | None = None,
    depends_on: int | None = None,
    seed: int | None = None,
    registry_text: str | None = None,
    artifact_kind: str | None = None,
    stage: dict | None = None,
) -> JobSpec:
    """A job for a registered task; an unregistered step is refused before anything is stored.
    `registry_text` replaces the packaged registry (tests).

    `artifact_kind` (a managed artifact kind) makes the runner keep the outputs whose kind equals the job's kind as
    artifacts of that kind instead of candidates: the app, not the worker, decides that a voice build's takes are
    hidden material. `stage` (plain data, such as `{"name": "tts", "unit": "fish_audio"}`) lets an advancer find the
    job's place in a staged operation. Both are absent unless given. `integration` defaults to the integration
    whose registry file names the engine."""
    try:
        engine, task = registry.task(registry.load(registry_text), step)
    except ValueError as exc:
        raise ApplicationError(str(exc), 422) from exc
    if artifact_kind is not None and artifact_kind not in MANAGED_ARTIFACT_KINDS:
        raise ApplicationError(f"{artifact_kind!r} is not a managed artifact kind.", 422)
    integration = integration or registry.origin(engine.name, registry_text)
    worker = engines.workers_dir() / engine.worker
    snapshot = {
        "engine": {
            "step": step,
            "gpu": task.gpu,
            "vram_gb": task.vram_gb,
            "registry": registry.fingerprint(engine.name, registry_text),
            "worker_sha256": _digest(worker),
            # The recipe the params came from (ADR 0016): voice jobs carry the voice spec's fingerprint.
            "spec": voice_spec.fingerprint() if integration == "voice" else None,
        },
        "task_spec": params,
    }
    if artifact_kind is not None:
        snapshot["engine"]["artifact_kind"] = artifact_kind
    if stage is not None:
        snapshot["stage"] = copy.deepcopy(stage)
    return JobSpec(
        kind=kind,
        executor="engine",
        integration=integration,
        inputs=inputs or {},
        snapshot=snapshot,
        step=step,
        seed=seed,
        depends_on=depends_on,
    )


def worker_environment(root: Path, engine: str, base: dict | None = None) -> dict:
    """The environment of a worker: the app's, without its Python paths, offline, with a stable temp folder per
    engine under the engines root (audio.cpp unpacks its model resources there once)."""
    env = {
        key: value
        for key, value in (os.environ if base is None else base).items()
        if key.upper() not in STRIPPED_ENVIRONMENT
    }
    temporary = root / "tmp" / engine
    temporary.mkdir(parents=True, exist_ok=True)
    # Numba (librosa's pitch and DTW) caches next to the installed package unless told otherwise.
    return {
        **env,
        **WORKER_ENVIRONMENT,
        "TMP": str(temporary),
        "TEMP": str(temporary),
        "NUMBA_CACHE_DIR": str(temporary / "numba"),
    }


def resolve(
    step: str, registry_text: str | None = None, configured: str | None = None
) -> EngineCommand:
    """The installed command for a registered task, from the registry and the engines root (`configured` is the
    library's setting). `registry_text` replaces the packaged registry (tests)."""
    try:
        engine, _ = registry.task(registry.load(registry_text), step)
    except ValueError as exc:
        raise ApplicationError(str(exc), 409) from exc
    root = engines.engines_root(configured)
    if root is None:
        raise ApplicationError(
            "No engines root is configured; set the local engines folder in Settings.", 409
        )
    home = root / engine.home
    interpreter = home / engine.environment
    if not interpreter.is_file():
        raise ApplicationError(
            f"Engine {engine.name} is not installed ({interpreter} is missing).", 409
        )
    worker = engines.workers_dir() / engine.worker
    if not worker.is_file():
        raise ApplicationError(f"Engine worker {worker} is missing.", 409)
    # The app's own media tools are every engine's ffmpeg and ffprobe unless it registers its own (a stage's fill
    # rounds write and read videos).
    media = MediaTools()
    shared = {
        name: str(path)
        for name, path in (("ffmpeg", media.ffmpeg), ("ffprobe", media.ffprobe))
        if path.is_file()
    }
    return EngineCommand(
        (str(interpreter), str(worker)),
        env=worker_environment(root, engine.name),
        home=str(home),
        programs=shared | {name: str(home / path) for name, path in engine.programs.items()},
    )


def _read(path: Path) -> dict | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def readiness(configured: str = "", registry_text: str | None = None) -> dict:
    """What each registered engine has installed, from its home and the setup script's manifest and probe.
    Readiness is reported, never enforced here: a missing engine fails its job with the reason."""
    root = engines.engines_root(configured)
    source = (
        "environment"
        if os.environ.get("GAIAVFX_ENGINES_ROOT")
        else ("setting" if configured else "none")
    )
    rows = []
    for engine in registry.load(registry_text).values():
        home = root / engine.home if root else None
        manifest = _read(home / "manifest.json") if home else None
        probe = _read(home / "probe.json") if home else None
        files = (manifest or {}).get("files") or []
        rows.append(
            {
                "name": engine.name,
                "home": str(home) if home else None,
                "installed": bool(home and (home / engine.environment).is_file()),
                "worker": (engines.workers_dir() / engine.worker).is_file(),
                "tasks": sorted(engine.tasks),
                "gpu_tasks": sorted(name for name, task in engine.tasks.items() if task.gpu),
                "licences": sorted({model["licence"] for model in engine.models}),
                "files": {
                    state: sum(f.get("state") == state for f in files)
                    for state in ("ok", "missing", "mismatch")
                }
                if manifest
                else None,
                "verified_at": (manifest or {}).get("verified_at"),
                "probe": (probe or {}).get("state"),
                "python": (probe or {}).get("python"),
                "cuda_device": ((probe or {}).get("cuda") or {}).get("device"),
                "drift": sorted((probe or {}).get("drift") or {}),
                "probed_at": (probe or {}).get("probed_at"),
            }
        )
    return {"root": str(root) if root else None, "root_source": source, "engines": rows}


def voice_library(configured: str = "") -> dict:
    """The expressive recordings folder as Settings shows it: its state, clip and skipped counts and the clips per
    category (`configured` is the library's setting). Listing only: no audio is read."""
    scan = expressive.scan(configured)
    return {
        "folder": scan["folder"],
        "state": scan["state"],
        "clips": len(scan["clips"]),
        "categories": scan["categories"],
        "skipped": len(scan["skipped"]),
    }


def _free_comfy(runner, endpoint: str) -> None:
    """Ask ComfyUI to unload its models before an engine takes the card; an offline ComfyUI is fine."""
    try:
        runner.comfy.request(
            endpoint, "POST", "/free", json={"unload_models": True, "free_memory": True}
        )
    except Exception:  # noqa: BLE001 - ComfyUI may be stopped; the lease still serialises the card
        logging.getLogger(__name__).info("ComfyUI did not answer /free at %s", endpoint)


def run_engine(runner, job) -> None:
    """Run (or relaunch) a claimed or running engine job, then collect its outputs."""
    engine = job["snapshot"]["engine"]
    if job["state"] == "claimed":
        if runner._cancelled(job):
            runner._transition(job, "cancelled")
            return
        job = runner._transition(job, "running")
    sources = runner._sources(job)
    command = (
        runner.engines.get(job["step"])
        if runner.engines is not None
        else resolve(job["step"], configured=runner.engines_root())
    )
    if command is None:
        raise ApplicationError(f"No command for engine task {job['step']}.")
    work = runner.db.root / "staging" / "operations" / job["id"]
    scratch = runner.db.root / "staging" / "scratch" / job["operation_id"]
    scratch.mkdir(parents=True, exist_ok=True)
    document = {
        "schema": 1,
        "job_id": job["id"],
        "step": job["step"],
        "task": job["step"].split(".", 1)[1],
        "params": job["snapshot"].get("task_spec") or {},
        "inputs": {key: str(path) for key, (path, _, _) in sources.items()},
        "seed": job["seed"],
        "engine": {"home": command.home, "programs": command.programs or {}},
        "scratch": str(scratch),
    }
    checked = {"at": 0.0, "value": False}

    def cancelled() -> bool:
        now = time.monotonic()
        if not checked["value"] and now - checked["at"] >= PROGRESS_SECONDS:
            checked.update(at=now, value=runner._cancelled(job))
        return checked["value"]

    written = {"at": 0.0}

    def progress(entry: dict) -> None:
        now = time.monotonic()
        if now - written["at"] < PROGRESS_SECONDS:
            return
        written["at"] = now
        with runner.db.transaction() as session:
            runner.service.local_progress(session, job["id"], min(float(entry["progress"]), 0.999))

    lease = nullcontext()
    if engine["gpu"]:
        endpoint = runner.endpoint() if callable(runner.endpoint) else runner.endpoint

        def lease_cancelled() -> bool:
            runner.check_stopping()
            return runner._cancelled(job)

        lease = gpu_lease(endpoint, lease_cancelled)
    with lease:
        if engine["gpu"]:
            _free_comfy(runner, endpoint)
            _wait_for_vram(runner, job, float(engine.get("vram_gb") or 0))
        try:
            outputs = engines.run(
                command.command,
                work,
                document,
                log=runner.db.root / "logs" / "engines" / f"{job['id']}.log",
                cancelled=cancelled,
                stopping=runner.stop.is_set,
                progress=progress,
                env=command.env,
            )
        except engines.EngineStopped:
            runner.check_stopping()  # raises the runner's own stop signal; the job stays running
            raise
    descriptors = [
        {"output_key": o["key"], "path": o["path"], "kind": o["kind"], "info": o["info"]}
        for o in outputs
    ]
    job = runner._transition(job, "collecting", outputs=descriptors)
    runner._collect(job)


def _wait_for_vram(runner, job, needed: float) -> None:
    """Wait while another process holds the memory this task needs; unreadable memory is not a wait."""
    while needed > 0:
        free = engines.free_vram_gb()
        if free is None or free >= needed:
            return
        runner.check_stopping()
        if runner._cancelled(job):
            raise ApplicationError("The engine task was cancelled while waiting for GPU memory.")
        logging.getLogger(__name__).info(
            "Engine task %s waits for %.1f GB of GPU memory (%.1f free)", job["step"], needed, free
        )
        runner.stop.wait(VRAM_WAIT_SECONDS)
