"""Provision the isolated stages engine; voice engines remain manual."""

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path

from gaiavfx.setup._runtime import (
    manifest,
    python_path,
    read_setup,
    repo_root,
    require_x64,
    workers_dir,
    write_json,
)

try:
    from gaiavfx.setup.manifest import Check, update_setup_file
except ImportError:
    from gaiavfx.setup._contract import Check, update_setup_file


def install_plan(root: Path, system: str | None = None) -> list[list[str]]:
    spec = manifest()["worldbuilder"]
    home = root / "worldbuilder"
    python = str(python_path(home, system))
    pip = ["uv", "pip", "install", "--python", python]
    return [
        ["uv", "venv", "--python", spec["python"], str(home)],
        [*pip, "--index-url", spec["torch_index"], "torch==" + spec["torch"]],
        [
            *pip,
            "--no-deps",
            "--only-binary",
            ":all:",
            "--index-url",
            spec["gsplat_index"],
            "gsplat==" + spec["gsplat"],
        ],
        [*pip, *spec["packages"]],
        [python, "-I", str(workers_dir() / "probe.py"), "--cuda"],
    ]


def install(root: Path) -> int:
    require_x64()
    root = root.expanduser().resolve()
    home = root / "worldbuilder"
    # Existing homes may be adopted junctions; never replace an environment implicitly.
    if home.exists() and not python_path(home).is_file():
        raise ValueError(
            f"{home} exists without its interpreter; choose another --root or repair it."
        )
    root.mkdir(parents=True, exist_ok=True)
    commands = install_plan(root)
    for index, command in enumerate(commands[:-1]):
        if index == 0 and python_path(home).is_file():
            continue
        subprocess.run(command, check=True)
    spec = manifest()["worldbuilder"]
    pins = dict(item.split("==", 1) for item in spec["packages"])
    pins.update(torch=spec["torch"], gsplat=spec["gsplat"])
    now = datetime.now(UTC).isoformat()
    from gaiavfx.application import engine_registry

    write_json(
        home / "manifest.json",
        {
            "schema": 1,
            "engine": "worldbuilder",
            "registered": True,
            "registry": engine_registry.fingerprint("worldbuilder"),
            "files": [],
            "packages": pins,
            "written_at": now,
        },
    )
    update_setup_file(engines_root=str(root))
    try:
        result = subprocess.run(
            commands[-1], check=True, capture_output=True, text=True, timeout=600
        )
        probe = json.loads(result.stdout)
        drift = {
            name: {"pinned": version, "installed": probe["packages"].get(name.lower())}
            for name, version in pins.items()
            if probe["packages"].get(name.lower()) != version
        }
        if probe.get("python") != spec["python"]:
            drift["python"] = {"pinned": spec["python"], "installed": probe.get("python")}
        probe.update(state="ok", drift=drift, probed_at=now)
    except (OSError, ValueError, KeyError, subprocess.SubprocessError) as exc:
        probe = {"state": "failed", "error": str(exc), "probed_at": now}
    write_json(home / "probe.json", probe)
    print(f"Worldbuilder installed in {home}; probe: {probe['state']}")
    if probe.get("drift") or probe["state"] != "ok" or not probe.get("cuda", {}).get("available"):
        print(
            "Stages need a successful probe with CUDA visible. Review probe.json and the NVIDIA driver."
        )
        return 1
    return 0


def check() -> list[Check]:
    fix = "python -m gaiavfx.setup engines install worldbuilder"
    rows = [Check("voice", "voice engines", "optional", "experimental, manual setup", "")]
    try:
        root = Path(
            os.environ.get("GAIAVFX_ENGINES_ROOT")
            or read_setup().get("engines_root")
            or repo_root() / ".runtime/engines"
        )
        home = root / "worldbuilder"
        python = python_path(home)
        rows.append(
            Check(
                "stages", "worldbuilder", "ok" if python.is_file() else "missing", str(python), fix
            )
        )
        probe_path = home / "probe.json"
        probe = json.loads(probe_path.read_text("utf-8")) if probe_path.is_file() else {}
        ok = probe.get("state") == "ok" and not probe.get("drift")
        rows.append(
            Check(
                "stages",
                "probe",
                "ok" if ok else "missing" if not probe else "mismatch",
                str(probe_path),
                fix,
            )
        )
        cuda = probe.get("cuda") or {}
        rows.append(
            Check(
                "stages",
                "CUDA",
                "ok" if cuda.get("available") else "unknown" if not cuda else "missing",
                "Saved probe: "
                + str(cuda.get("device") or cuda.get("error") or "CUDA not recorded as available"),
                fix,
            )
        )
    except Exception as exc:  # noqa: BLE001 - doctor checks must never raise
        rows.append(Check("stages", "worldbuilder", "unknown", str(exc), fix))
    return rows


def register(subparsers):
    parser = subparsers.add_parser("engines", help="Install the stages engine (voice is manual)")
    actions = parser.add_subparsers(dest="action", required=True)
    command = actions.add_parser("install")
    command.add_argument("engine", choices=["worldbuilder"])
    command.add_argument("--root", type=Path, default=repo_root() / ".runtime/engines")
    command.set_defaults(func=lambda args: install(args.root))
