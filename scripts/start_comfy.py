"""Launch configured ComfyUI in its own console/session, inheriting output."""

import os
import subprocess
import sys
from pathlib import Path

from dev_comfy import configured_runtime


def main():
    config = configured_runtime()
    runtime, python = Path(config["directory"]), Path(config["python"])
    if not python.is_file() or not (runtime / "main.py").is_file():
        raise ValueError("Configured ComfyUI is incomplete. Run the installer first.")
    subprocess.Popen(
        [str(python), str(Path(__file__).with_name("dev_comfy.py")), "--worker"],
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0,
        start_new_session=os.name != "nt",
        cwd=runtime,
    )
    print("ComfyUI launched. Its output stays in its own console/session.")


if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError) as exc:
        print(str(exc), file=sys.stderr)
        sys.exit(1)
