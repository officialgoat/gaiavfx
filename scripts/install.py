"""Finish one-command setup after dependencies, media and the renderer are installed."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from gaiavfx.setup._runtime import read_setup

try:
    from gaiavfx.setup.manifest import update_setup_file
except ImportError:
    from gaiavfx.setup._contract import update_setup_file


def run_setup(*arguments):
    subprocess.run(
        [sys.executable, "-m", "gaiavfx.setup", *map(str, arguments)], check=True
    )


def has_command(*arguments):
    result = subprocess.run(
        [sys.executable, "-m", "gaiavfx.setup", *arguments, "--help"],
        capture_output=True,
        text=True,
    )
    if result.returncode:
        print(
            f"Skipping {' '.join(arguments)}: this checkout does not include that setup command yet."
        )
        return False
    return True


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--comfy-dir", type=Path)
    location.add_argument("--install-comfy", type=Path)
    parser.add_argument(
        "--features",
        nargs="+",
        default=["core", "image-edit", "stages", "recast"],
        choices=["core", "image-edit", "stages", "recast"],
    )
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument("--with-assistant", action="store_true")
    parser.add_argument("--with-stages-engine", action="store_true")
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args(argv)
    if not args.non_interactive and not sys.stdin.isatty():
        parser.error(
            "No interactive terminal. Pass --non-interactive and the desired install flags."
        )
    if not args.non_interactive:
        if not args.comfy_dir and not args.install_comfy:
            existing = input(
                "Existing ComfyUI folder (Enter to choose a fresh install or skip): "
            ).strip()
            if existing:
                args.comfy_dir = Path(existing)
            else:
                fresh = input("New ComfyUI install folder (Enter to skip): ").strip()
                if fresh:
                    args.install_comfy = Path(fresh)
        print("Feature groups: " + ", ".join(args.features))
        if not args.skip_models:
            args.skip_models = (
                input("Download the selected model groups (many GB)? [y/N] ")
                .strip()
                .lower()
                != "y"
            )
        if not args.with_assistant:
            args.with_assistant = (
                input("Install the optional local assistant (~18 GB)? [y/N] ")
                .strip()
                .lower()
                == "y"
            )
        if not args.with_stages_engine:
            args.with_stages_engine = (
                input("Install the stages engine (torch/gsplat, NVIDIA)? [y/N] ")
                .strip()
                .lower()
                == "y"
            )
    comfy = args.comfy_dir or args.install_comfy or read_setup().get("comfy_dir")
    if args.install_comfy and has_command("comfy", "install"):
        run_setup("comfy", "install", args.install_comfy)
        # Portable installations may place ComfyUI one level below the requested directory.
        comfy = read_setup().get("comfy_dir") or args.install_comfy
    if comfy:
        update_setup_file(comfy_dir=str(Path(comfy).expanduser().resolve()))
        if has_command("nodes", "install"):
            run_setup("nodes", "install", "--comfy-dir", comfy)
        if not args.skip_models and has_command("models", "download"):
            run_setup(
                "models",
                "download",
                "--comfy-dir",
                comfy,
                "--features",
                *args.features,
                "--yes",
            )
        if has_command("configure"):
            run_setup(
                "configure",
                "--comfy-url",
                read_setup().get("comfy_url") or "http://127.0.0.1:8188",
            )
    else:
        print(
            "ComfyUI setup skipped. Rerun with --comfy-dir or --install-comfy to enable generation."
        )
    if args.with_stages_engine:
        run_setup("engines", "install", "worldbuilder")
    if args.with_assistant:
        run_setup("assistant", "install")
    if has_command("doctor"):
        doctor_arguments = ["--features", *args.features, "runtime"]
        if comfy:
            doctor_arguments.extend(["--comfy-dir", str(comfy)])
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gaiavfx.setup",
                "doctor",
                "--json",
                *doctor_arguments,
            ],
            check=False,
            capture_output=True,
            text=True,
        )
        code = result.returncode
        output = getattr(result, "stdout", "")
        try:
            report = json.loads(output)
        except ValueError:
            report = None
        valid_report = (
            isinstance(report, list)
            and bool(report)
            and all(
                isinstance(row, dict)
                and all(key in row for key in ("status", "feature", "item", "detail"))
                for row in report
            )
        )
        if valid_report:
            for row in report:
                print(
                    f"{row['status']:8} {row['feature']}/{row['item']}: {row['detail']}"
                )
                if row.get("fix"):
                    print("  Fix: " + row["fix"])
        elif output:
            print(output, end="")
        if getattr(result, "stderr", ""):
            print(result.stderr, end="", file=sys.stderr)
        if code == 1 and valid_report:
            print(
                "Doctor found remaining setup or stopped services. Review its summary and rerun npm run doctor after starting the configured services."
            )
            # Installation does not start ComfyUI. Its expected offline verdict is diagnostic;
            # failed downloads, provisioning commands, builds and doctor errors still fail.
            code = 0
    else:
        # Brief C can be reviewed and exercised before the ComfyUI CLI is merged.
        from gaiavfx.setup import assistant, engines, media

        for module in (media, engines, assistant):
            for row in module.check():
                print(f"{row.status:8} {row.feature}/{row.item}: {row.detail}")
        code = 0
    print(
        "Setup finished. Start with bash start.sh or powershell -NoProfile -ExecutionPolicy Bypass -File .\\start.ps1. Review missing items above."
    )
    return code


if __name__ == "__main__":
    try:
        sys.exit(main())
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        sys.exit(1)
