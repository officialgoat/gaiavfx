"""Start the explicitly configured installed ComfyUI runtime for manual development."""

import argparse
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def configured_runtime(config_path=None):
    if config_path is not None:
        return json.loads(config_path.read_text(encoding="utf-8-sig"))["comfy"]
    setup_path = Path(
        os.environ.get("GAIAVFX_SETUP_FILE") or ROOT / ".runtime/setup.json"
    )
    if not setup_path.is_file():
        raise ValueError(
            "No ComfyUI configuration. Run the installer with --comfy-dir first."
        )
    settings = json.loads(setup_path.read_text(encoding="utf-8-sig"))
    if not settings.get("comfy_dir"):
        raise ValueError(
            "No ComfyUI folder configured. Run the installer with --comfy-dir first."
        )
    runtime = Path(settings["comfy_dir"]).expanduser().resolve()
    if not (runtime / "main.py").is_file() and (runtime / "ComfyUI/main.py").is_file():
        runtime = runtime / "ComfyUI"
    suffix = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    candidates = [
        runtime.parent / "python_embeded/python.exe",
        runtime / ".venv" / suffix,
        runtime / "venv" / suffix,
    ]
    python = settings.get("comfy_python") or next(
        (str(p) for p in candidates if p.is_file()), ""
    )
    if not python:
        raise ValueError(
            "No ComfyUI interpreter found; configure comfy_python in setup.json."
        )
    return {
        "directory": str(runtime),
        "python": python,
        "port": settings.get("comfy_port", 8188),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path)
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument(
        "arguments", nargs=argparse.REMAINDER, help="Extra ComfyUI arguments after --"
    )
    args = parser.parse_args()
    extra = args.arguments[1:] if args.arguments[:1] == ["--"] else args.arguments
    try:
        config = configured_runtime(args.config)
    except (OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))
    runtime = Path(config["directory"]).resolve()
    python = Path(config["python"]).resolve()
    if not python.is_file() or not (runtime / "main.py").is_file():
        parser.error(
            "Configured ComfyUI Python or main.py is missing; no models are downloaded."
        )
    if not args.worker:
        child = subprocess.Popen(
            [
                str(python),
                str(Path(__file__).resolve()),
                *(["--config", str(args.config.resolve())] if args.config else []),
                "--worker",
                "--",
                *extra,
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=sys.stdout,
            stderr=sys.stderr,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        stopped = False
        try:
            # A Windows infinite process wait delays Python's Ctrl+C handler.
            # Return to Python regularly so the IDE Stop action reaches finally.
            while child.poll() is None:
                try:
                    child.wait(timeout=0.5)
                except subprocess.TimeoutExpired:
                    pass
        except KeyboardInterrupt:
            stopped = True
        finally:
            if child.poll() is None:
                child.terminate()
                try:
                    child.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    child.kill()
                    child.wait(timeout=5)
        raise SystemExit(0 if stopped else child.returncode or 0)
    # The single GAIAVFX ComfyUI keeps its user/input/output/temp here (default: a repo-local folder).
    output = Path(
        config.get("data_directory") or ROOT / ".cache/manual-development/comfy"
    ).resolve()
    for name in ("user", "input", "output", "temp"):
        (output / name).mkdir(parents=True, exist_ok=True)
    sys.path[:0] = [*config.get("python_paths", []), str(runtime)]
    os.chdir(runtime)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ.update(
        {key: str(value) for key, value in config.get("environment", {}).items()}
    )
    sys.argv = [
        str(runtime / "main.py"),
        "--base-directory",
        str(runtime),
        "--user-directory",
        str(output / "user"),
        "--database-url",
        "sqlite:///" + (output / "user/comfyui.db").as_posix(),
        "--input-directory",
        str(output / "input"),
        "--output-directory",
        str(output / "output"),
        "--temp-directory",
        str(output / "temp"),
        "--listen",
        "127.0.0.1",
        "--port",
        str(config.get("port", 8195)),
        "--disable-auto-launch",
        "--disable-api-nodes",
        *config.get("arguments", []),
        *extra,
    ]
    runpy.run_path(str(runtime / "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
