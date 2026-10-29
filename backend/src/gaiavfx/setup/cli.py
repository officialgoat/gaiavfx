"""python -m gaiavfx.setup: explicit installation, read-only diagnostics."""

import argparse
import importlib
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlparse

import httpx

from . import comfy_install, doctor, models, nodes
from .manifest import update_setup_file


def configure(args) -> int:
    parsed = urlparse(args.comfy_url)
    if (
        parsed.scheme not in {"http", "https"}
        or not parsed.hostname
        or parsed.username
        or parsed.password
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Enter a ComfyUI HTTP(S) URL without credentials, query or fragment.")
    keys = {"comfy_url": args.comfy_url.rstrip("/")}
    if args.engines_root:
        keys["engines_root"] = str(args.engines_root.expanduser().resolve())
    print("Saved defaults for new libraries: " + str(update_setup_file(**keys)))
    return 0


def unavailable(args) -> int:
    print(f"{args.command}: not available in this build")
    return 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m gaiavfx.setup",
        description="Install local dependencies and check what each GAIAVFX feature needs.",
    )
    commands = parser.add_subparsers(dest="command", required=True)
    for module in (doctor, comfy_install, nodes, models):
        module.register(commands)
    config = commands.add_parser("configure", help="Save connection defaults for new libraries")
    config.add_argument("--comfy-url", required=True)
    config.add_argument("--engines-root", type=Path)
    config.set_defaults(func=configure)
    # Only the runtime subcommand selected by the caller is imported.
    requested = (argv if argv is not None else sys.argv[1:])[:1]
    for name in ("engines", "assistant", "media", "smoke"):
        if requested == [name]:
            try:
                module = importlib.import_module(f"gaiavfx.setup.{name}")
            except ModuleNotFoundError as exc:
                if exc.name != f"gaiavfx.setup.{name}":
                    print(
                        f"{name}: not available in this build (missing {exc.name})", file=sys.stderr
                    )
                    return 1
            else:
                module.register(commands)
                continue
        stub = commands.add_parser(name, help=f"Install the {name} runtime (when included)")
        stub.add_argument("arguments", nargs=argparse.REMAINDER)
        stub.set_defaults(func=unavailable)
    args = parser.parse_args(argv)
    try:
        return int(args.func(args) or 0)
    except (OSError, ValueError, RuntimeError, httpx.HTTPError, subprocess.SubprocessError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print(
            "\nCancelled. Run the same command again to resume partial downloads.", file=sys.stderr
        )
        return 130
