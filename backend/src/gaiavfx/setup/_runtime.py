"""Small shared helpers for the explicit runtime installers."""

import json
import os
import platform
import sys
import tomllib
from importlib import resources
from pathlib import Path


def repo_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[4]


def setup_file() -> Path:
    return Path(os.environ.get("GAIAVFX_SETUP_FILE") or repo_root() / ".runtime/setup.json")


def read_setup() -> dict:
    path = setup_file()
    if not path.exists():
        return {}
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path}")  # noqa: TRY004 - malformed configuration
    return value


def write_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".partial")
    temporary.write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")
    temporary.replace(path)


def manifest() -> dict:
    return tomllib.loads(
        resources.files("gaiavfx.setup").joinpath("manifest/runtime.toml").read_text("utf-8")
    )


def host_os(system: str | None = None) -> str:
    value = (system or platform.system()).lower()
    if value in ("windows", "win32", "nt"):
        return "windows"
    if value == "linux":
        return "linux"
    raise ValueError(f"No pinned runtime for {value}; configure an existing runtime in Settings.")


def require_x64() -> None:
    if platform.machine().lower() not in ("amd64", "x86_64"):
        raise ValueError("Pinned runtime downloads require x86-64 Windows or Linux.")


def python_path(home: Path, system: str | None = None) -> Path:
    return home / ("Scripts/python.exe" if host_os(system) == "windows" else "bin/python")


def workers_dir() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys._MEIPASS) / "engine_workers"
    return repo_root() / "engine_workers"
