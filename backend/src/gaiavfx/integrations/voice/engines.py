"""The closed registry of local engines (ADR 0015): which engine tasks may run, in which environment, with which
model files. Only registered tasks run; the registry text is fingerprinted into every engine job's snapshot.

Every engine is a Python worker in its own environment. An engine lives in a home directory under the engines root;
its interpreter, the native programs its worker drives (audio.cpp) and its model files are paths inside that home."""

import hashlib
import os
import re
import tomllib
from dataclasses import dataclass
from importlib import resources
from pathlib import PurePosixPath, PureWindowsPath

SCHEMA = 2
SHA256 = re.compile(r"[0-9a-f]{64}")


@dataclass(frozen=True)
class EngineTask:
    gpu: bool
    vram_gb: float


@dataclass(frozen=True)
class Engine:
    name: str
    home: str  # the engine's directory under the engines root
    environment: str  # its Python interpreter, relative to the home
    worker: str  # file under `engine_workers/`
    programs: dict[str, str]  # native programs the worker drives, relative to the home
    packages: dict[str, str]  # pinned package versions the probe checks
    tasks: dict[str, EngineTask]
    models: tuple[dict, ...]  # path (relative to the home), sha256, bytes, source, licence


def registry_text() -> str:
    return (
        resources.files(__package__)
        .joinpath("knowledge", "engines.toml")
        .read_text(encoding="utf-8")
    )


def fingerprint(text: str | None = None) -> str:
    return hashlib.sha256((registry_text() if text is None else text).encode("utf-8")).hexdigest()


def inside(value: object) -> bool:
    """A non-empty relative path that cannot leave the directory it is resolved against."""
    if not isinstance(value, str) or not value:
        return False
    windows = PureWindowsPath(value)
    if windows.drive or windows.root or value.startswith(("/", "\\")):
        return False
    return ".." not in PurePosixPath(value.replace("\\", "/")).parts


def load(text: str | None = None, *, platform: str | None = None) -> dict[str, Engine]:
    """Parse and check the registry; any malformed entry is a ValueError, never a silent default."""
    document = tomllib.loads(registry_text() if text is None else text)
    if document.get("schema") != SCHEMA:
        raise ValueError(f"The engine registry needs schema = {SCHEMA}.")
    engines = {}
    for name, entry in (document.get("engines") or {}).items():
        home, environment = entry.get("home"), entry.get("environment")
        if isinstance(environment, dict):
            environment = environment.get(
                "windows" if (platform or os.name) in ("win32", "nt") else "linux"
            )
        if not inside(home) or not inside(environment):
            raise ValueError(
                f"Engine {name}: home and environment must be relative paths that stay inside."
            )
        worker = entry.get("worker")
        if not inside(worker) or not worker.endswith(".py"):
            raise ValueError(f"Engine {name}: name its worker file under engine_workers/.")
        programs = entry.get("programs") or {}
        programs = {
            name: value.get("windows" if (platform or os.name) in ("win32", "nt") else "linux")
            if isinstance(value, dict)
            else value
            for name, value in programs.items()
        }
        if not all(inside(path) for path in programs.values()):
            raise ValueError(f"Engine {name}: programs are paths inside the engine's home.")
        packages = entry.get("packages") or {}
        if not all(isinstance(version, str) and version for version in packages.values()):
            raise ValueError(f"Engine {name}: package pins are version strings.")
        tasks = {}
        for task, spec in (entry.get("tasks") or {}).items():
            gpu, vram = spec.get("gpu"), spec.get("vram_gb", 0.0)
            if type(gpu) is not bool or not isinstance(vram, (int, float)) or vram < 0:
                raise ValueError(
                    f"Engine {name}, task {task}: gpu must be true or false, vram_gb >= 0."
                )
            tasks[task] = EngineTask(gpu=gpu, vram_gb=float(vram))
        if not tasks:
            raise ValueError(f"Engine {name} declares no tasks.")
        models = tuple(entry.get("models") or ())
        for model in models:
            if not all(
                isinstance(model.get(key), str) and model[key]
                for key in ("path", "sha256", "source", "licence")
            ):
                raise ValueError(
                    f"Engine {name}: every model file needs path, sha256, source and licence."
                )
            if not inside(model["path"]) or not SHA256.fullmatch(model["sha256"]):
                raise ValueError(
                    f"Engine {name}: model {model['path']} needs a path inside the home "
                    "and a full lowercase SHA-256."
                )
        engines[name] = Engine(
            name, home, environment, worker, dict(programs), dict(packages), tasks, models
        )
    return engines


def task(engines: dict[str, Engine], step: str) -> tuple[Engine, EngineTask]:
    """`step` is `<engine>.<task>`; an unregistered one is a ValueError."""
    name, _, task_name = step.partition(".")
    engine = engines.get(name)
    if engine is None or task_name not in engine.tasks:
        raise ValueError(f"No registered engine task {step!r}.")
    return engine, engine.tasks[task_name]
