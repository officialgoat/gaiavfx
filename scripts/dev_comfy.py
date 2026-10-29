"""Start the explicitly configured installed ComfyUI runtime for manual development."""

import argparse
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--config", type=Path, default=ROOT / ".cache/development-machine.json"
    )
    parser.add_argument("--worker", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if not args.config.is_file():
        parser.error(
            "Missing machine configuration: provide --config with a JSON comfy object containing directory and python."
        )
    config = json.loads(args.config.read_text(encoding="utf-8-sig"))["comfy"]
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
                "--config",
                str(args.config.resolve()),
                "--worker",
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
        *config.get("arguments", []),
    ]
    runpy.run_path(str(runtime / "main.py"), run_name="__main__")


if __name__ == "__main__":
    main()
