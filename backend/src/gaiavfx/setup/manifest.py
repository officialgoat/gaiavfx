"""The dependency manifest and defaults shared by the CLI and application."""

from __future__ import annotations

import json
import os
import re
import sys
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

Status = Literal["ok", "missing", "mismatch", "unknown", "optional"]
FEATURES = ("core", "image-edit", "recast", "stages")
FOLDERS = {
    "diffusion_models",
    "text_encoders",
    "vae",
    "loras",
    "checkpoints",
    "model_patches",
    "geometry_estimation",
    "insightface/models/buffalo_l",
}


@dataclass(frozen=True)
class Check:
    feature: str
    item: str
    status: Status
    detail: str
    fix: str = ""


def repo_root() -> Path:
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parents[4]


def setup_file() -> Path:
    override = os.environ.get("GAIAVFX_SETUP_FILE")
    return Path(override).expanduser() if override else repo_root() / ".runtime" / "setup.json"


def default_comfy_directory() -> Path:
    return repo_root().resolve().parent / "ComfyUI"


def comfy_directory(path: Path) -> Path:
    """Accept either the ComfyUI folder or the container created by portable install."""
    path = path.expanduser().resolve()
    for candidate in (path, path / "ComfyUI", path / "ComfyUI_windows_portable" / "ComfyUI"):
        if (candidate / "main.py").is_file():
            return candidate
    return path


def read_setup_file() -> dict:
    try:
        data = json.loads(setup_file().read_text(encoding="utf-8-sig"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def update_setup_file(**keys) -> Path:
    """Merge learned paths; atomic replacement keeps interrupted writes readable."""
    path = setup_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    data = {
        **read_setup_file(),
        **{k: str(v) if isinstance(v, Path) else v for k, v in keys.items()},
    }
    with tempfile.NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=path.parent, delete=False
    ) as f:
        temporary = Path(f.name)
        json.dump(data, f, indent=2)
        f.write("\n")
    try:
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return path


def connection_defaults() -> dict[str, str]:
    data = read_setup_file()
    defaults = {
        k: v
        for k, v in data.items()
        if k
        in {
            "comfy_url",
            "assistant_executable",
            "assistant_model",
            "assistant_projector",
            "engines_root",
        }
        and isinstance(v, str)
    }
    if value := os.environ.get("GAIAVFX_COMFY_URL"):
        defaults["comfy_url"] = value.rstrip("/")
    return defaults


def selected_features(value: str | list[str] | None) -> list[str]:
    if value is None:
        return list(FEATURES)
    values = [value] if isinstance(value, str) else value
    result = list(
        dict.fromkeys(part.strip() for v in values for part in v.split(",") if part.strip())
    )
    unknown = set(result) - {*FEATURES, "runtime"}
    if unknown or not result:
        raise ValueError("Choose features from: " + ", ".join((*FEATURES, "runtime")))
    return result


def load_manifest(path: Path | None = None) -> dict:
    with (path or Path(__file__).parent / "manifest" / "comfy.toml").open("rb") as stream:
        data = tomllib.load(stream)
    validate_manifest(data)
    return data


def validate_manifest(data: dict) -> None:
    models = data["models"]
    packs = data["node_packs"]
    features = data["features"]
    files = [m["file"] for m in models]
    if len(files) != len(set(files)):
        raise ValueError("Model filenames must be unique")
    if len({p["name"] for p in packs}) != len(packs):
        raise ValueError("Node pack names must be unique")
    if set(features) != set(FEATURES):
        raise ValueError("Manifest features must match the supported features")
    classes = {c for p in packs for c in p["classes"]}
    for model in models:
        if model["folder"] not in FOLDERS:
            raise ValueError("Unknown model folder: " + model["folder"])
        if (
            Path(model["file"]).name != model["file"]
            or "\\" in model["file"]
            or model["file"] in {".", ".."}
        ):
            raise ValueError("Model file must be a filename")
        if not isinstance(model["bytes"], int) or model["bytes"] < 0:
            raise ValueError("Model bytes must be nonnegative")
        if model["sha256"] and not re.fullmatch(r"[a-f0-9]{64}", model["sha256"]):
            raise ValueError("Invalid SHA-256: " + model["file"])
        if not model["url"] and not model["note"]:
            raise ValueError("Manual downloads need a note")
        if model["url"] and not model["sha256"]:
            raise ValueError("Automatic downloads need a verified SHA-256")
    for entry in [*models, *packs]:
        if set(entry["features"]) - set(features):
            raise ValueError("Unknown entry feature")
    for name, feature in features.items():
        if set(feature["models"]) - set(files):
            raise ValueError("Feature references an unknown model: " + name)
        if set(feature["node_classes"]) - classes:
            raise ValueError("Feature references an unknown node pack class: " + name)
        for model in models:
            if (name in model["features"]) != (model["file"] in feature["models"]):
                raise ValueError("Model and feature membership disagree: " + model["file"])


def command(*args: object) -> str:
    """A pasteable command for the user's native shell (PowerShell on Windows)."""
    import shlex

    def quote(value: object) -> str:
        text = str(value)
        if os.name == "nt":
            return "'" + text.replace("'", "''") + "'" if re.search(r"[^\w./:=,-]", text) else text
        return shlex.quote(text)

    result = " ".join(quote(arg) for arg in args)
    return "& " + result if os.name == "nt" and result.startswith("'") else result


def setup_command(*args: object) -> str:
    return command("python", "-m", "gaiavfx.setup", *args)
