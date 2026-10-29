"""Standalone runtime entry point; defer to the complete setup CLI when merged."""

import argparse
import subprocess
import sys

import httpx


def main(argv=None):
    try:
        from gaiavfx.setup.cli import main as complete_main
    except ModuleNotFoundError as exc:
        if exc.name != "gaiavfx.setup.cli":
            raise
    else:
        return complete_main(argv)
    from gaiavfx.setup import assistant, engines, media

    parser = argparse.ArgumentParser(description="Install GAIAVFX local runtimes")
    subparsers = parser.add_subparsers(dest="command", required=True)
    for module in (media, engines, assistant):
        module.register(subparsers)
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except (OSError, ValueError, RuntimeError, httpx.HTTPError, subprocess.SubprocessError) as exc:
        print(f"Setup failed: {exc}", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("Setup cancelled. Partial downloads can be resumed.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())
