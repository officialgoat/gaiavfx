"""Finish one-command setup after dependencies, media and the renderer are installed."""

import argparse
import json
import subprocess
import sys
from pathlib import Path

from gaiavfx.setup._runtime import read_setup
from gaiavfx.setup.manifest import (
    comfy_directory,
    default_comfy_directory,
    load_manifest,
    update_setup_file,
)

DEFAULT_FEATURES = ["core", "image-edit", "stages", "recast"]


def run_setup(*arguments):
    subprocess.run(
        [sys.executable, "-m", "gaiavfx.setup", *map(str, arguments)], check=True
    )


def has_command(*arguments):
    result = subprocess.run(
        [sys.executable, "-m", "gaiavfx.setup", *arguments, "--help"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode:
        print(
            f"Skipping {' '.join(arguments)}: this checkout does not include that setup command yet."
        )
        return False
    return True


def ask_yes(prompt):
    while True:
        answer = input(prompt + " [Y/n] ").strip().lower()
        if answer in ("", "y", "yes"):
            return True
        if answer in ("n", "no"):
            return False
        print("Enter yes or no.")


def rerun_hint(args, comfy=None, *, resume="comfy"):
    windows = sys.platform == "win32"
    path = str(comfy or default_comfy_directory())
    # Double quotes keep paths pasteable, including shell expansion characters.
    escape = "`" if windows else "\\"
    for char in ("`", "$", '"') if windows else ("\\", "$", "`", '"'):
        path = path.replace(char, escape + char)
    parts = [r".\install.ps1" if windows else "bash install.sh"]
    parts.extend(
        [
            ("-ComfyDir" if comfy else "-InstallComfy")
            if windows
            else ("--comfy-dir" if comfy else "--install-comfy"),
            '"' + path + '"',
            "-NonInteractive" if windows else "--non-interactive",
        ]
    )
    if set(args.features) != set(DEFAULT_FEATURES):
        parts.extend(
            [
                "-Features" if windows else "--features",
                ("," if windows else " ").join(args.features),
            ]
        )
    for key, flag, feature in (
        ("skip_models", "SkipModels", "models"),
        ("skip_assistant", "SkipAssistant", "assistant"),
        ("skip_stages_engine", "SkipStagesEngine", "stages-engine"),
    ):
        if getattr(args, key) and resume != feature:
            parts.append("-" + flag if windows else "--" + key.replace("_", "-"))
    return " ".join(parts)


def saved_comfy_directory():
    saved = read_setup().get("comfy_dir")
    if isinstance(saved, str) and saved:
        directory = comfy_directory(Path(saved))
        if (directory / "main.py").is_file():
            return directory
        print(
            f"Ignoring saved ComfyUI {saved}: folder is missing or no longer contains ComfyUI."
        )
    return None


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    location = parser.add_mutually_exclusive_group()
    location.add_argument("--comfy-dir", type=Path)
    location.add_argument("--install-comfy", type=Path)
    parser.add_argument(
        "--features",
        nargs="+",
        default=DEFAULT_FEATURES,
        choices=["core", "image-edit", "stages", "recast"],
    )
    parser.add_argument("--skip-models", action="store_true")
    parser.add_argument("--skip-assistant", action="store_true")
    parser.add_argument("--skip-stages-engine", action="store_true")
    parser.add_argument(
        "--with-assistant",
        action="store_true",
        help="Deprecated: already enabled by default",
    )
    parser.add_argument(
        "--with-stages-engine",
        action="store_true",
        help="Deprecated: already enabled by default",
    )
    parser.add_argument("--non-interactive", action="store_true")
    args = parser.parse_args(argv)
    if not args.non_interactive and not sys.stdin.isatty():
        parser.error(
            "No interactive terminal. Pass --non-interactive and the desired install flags."
        )
    for legacy in ("with_assistant", "with_stages_engine"):
        if getattr(args, legacy):
            print(
                f"Deprecated: --{legacy.replace('_', '-')} is a no-op; installation is now enabled by default."
            )
    saved = None
    if not args.comfy_dir and not args.install_comfy:
        saved = saved_comfy_directory()
        if saved and args.non_interactive:
            args.comfy_dir = saved
            print(f"Using saved ComfyUI {saved}; pass -ComfyDir/--comfy-dir to change")
    if not args.non_interactive:
        if not args.comfy_dir and not args.install_comfy:
            default = default_comfy_directory()
            prompt = (
                f"ComfyUI: (1) use saved {saved}, (2) install fresh into {default}, (3) use another existing folder, (4) skip [1]: "
                if saved
                else f"ComfyUI: (1) install fresh into {default}, (2) use an existing folder, (3) skip [1]: "
            )
            while True:
                choice = input(prompt).strip() or "1"
                if saved and choice == "1":
                    args.comfy_dir = saved
                    break
                if choice == ("2" if saved else "1"):
                    args.install_comfy = default
                    break
                if choice == ("3" if saved else "2"):
                    existing = input("Existing ComfyUI folder: ").strip().strip('"')
                    if existing:
                        args.comfy_dir = Path(existing).expanduser().resolve()
                        break
                    print("Enter a folder path, or choose skip.")
                elif choice == ("4" if saved else "3"):
                    break
                else:
                    print("Choose 1, 2, 3 or 4." if saved else "Choose 1, 2 or 3.")
        print("Feature groups: " + ", ".join(args.features))
        if (args.comfy_dir or args.install_comfy) and not args.skip_models:
            total = sum(
                model["bytes"]
                for model in load_manifest()["models"]
                if set(model["features"]) & set(args.features) and not model["optional"]
            )
            args.skip_models = not ask_yes(
                f"Download the selected model groups ({total / 1_000_000_000:.1f} GB total)?"
            )
        if not args.skip_assistant:
            args.skip_assistant = not ask_yes("Install the local assistant (~18.4 GB)?")
        if not args.skip_stages_engine:
            args.skip_stages_engine = not ask_yes(
                "Install the stages engine (torch/gsplat, NVIDIA)?"
            )
    comfy = args.comfy_dir or args.install_comfy
    update_setup_file(
        skip_assistant=args.skip_assistant, skip_stages_engine=args.skip_stages_engine
    )
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
        rerun = rerun_hint(args)
        print(
            "ComfyUI and models were skipped because no ComfyUI folder was chosen. Rerun: "
            + rerun
        )
    if comfy and args.skip_models:
        print(
            "Models were skipped. Install later: "
            + rerun_hint(args, comfy, resume="models")
        )
    if args.skip_stages_engine:
        print(
            "Stages engine was skipped. Install later: "
            + rerun_hint(args, comfy, resume="stages-engine")
        )
    if args.skip_assistant:
        print(
            "Assistant was skipped. Install later: "
            + rerun_hint(args, comfy, resume="assistant")
        )
    if not args.skip_stages_engine:
        run_setup("engines", "install", "worldbuilder")
    if not args.skip_assistant:
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
                if row["status"] != "ok" and row.get("fix"):
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


def entrypoint(argv=None):
    try:
        return main(argv)
    except (OSError, ValueError, subprocess.SubprocessError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "\nCancelled. Run the same command again to resume partial downloads.",
            file=sys.stderr,
        )
        return 130


if __name__ == "__main__":
    sys.exit(entrypoint())
