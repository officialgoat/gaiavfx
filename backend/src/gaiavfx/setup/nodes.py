"""Install pinned node packs without replacing local work."""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

from .manifest import comfy_directory, load_manifest, repo_root, update_setup_file


def run(args: list[str | Path], **kwargs):
    return subprocess.run([str(a) for a in args], check=True, **kwargs)


def python_path(comfy_dir: Path, explicit: Path | None = None) -> Path:
    candidates = (
        [explicit]
        if explicit
        else [
            comfy_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
            comfy_dir.parent / "python_embeded" / "python.exe",
            comfy_dir / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
        ]
    )
    for path in candidates:
        if path.is_file():
            return path.resolve()
    raise ValueError(
        "ComfyUI Python was not found. Run comfy install first, or pass --python PATH."
    )


def checkout(url: str, target: Path, revision: str) -> bool:
    if target.exists():
        if not (target / ".git").exists():
            print(
                f"Warning: {target} is not a Git clone; left unchanged. Move it aside to install the pinned pack."
            )
            return False
        dirty = run(
            ["git", "-C", target, "status", "--porcelain"], capture_output=True, text=True
        ).stdout
        if dirty.strip():
            print(
                f"Warning: {target} has local changes; left unchanged. Commit or stash them before retrying."
            )
            return False
        head = run(
            ["git", "-C", target, "rev-parse", "HEAD"], capture_output=True, text=True
        ).stdout.strip()
        remote = run(
            ["git", "-C", target, "remote", "get-url", "origin"], capture_output=True, text=True
        ).stdout.strip()

        def canonical(value):
            return (
                value.rstrip("/")
                .removesuffix(".git")
                .replace("github.com/comfyanonymous/ComfyUI", "github.com/Comfy-Org/ComfyUI")
            )

        if canonical(remote) != canonical(url):
            print(f"Warning: {target} has a different origin; left unchanged.")
            return False
        # Do not silently orphan local commits, even with a clean working tree.
        if head != revision:
            run(["git", "-C", target, "fetch", "origin"])
            local = run(
                ["git", "-C", target, "rev-list", "HEAD", "--not", "--remotes=origin"],
                capture_output=True,
                text=True,
            ).stdout
            if local.strip():
                print(
                    f"Warning: {target} has local commits; left unchanged. Preserve them on a branch before installing into a separate directory."
                )
                return False
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
        run(["git", "clone", "--", url, target])
    run(["git", "-C", target, "fetch", "origin", revision])
    run(["git", "-C", target, "checkout", "--detach", revision])
    return True


def link_pack(source: Path, target: Path, *, copy: bool = False) -> bool:
    source = source.resolve(strict=True)
    if target.exists() or target.is_symlink() or target.is_junction():
        if target.resolve() == source:
            return True
        # Copies are repeatable when unmodified. Never overwrite an unrelated/local pack.
        if target.is_dir() and all(
            (target / p.relative_to(source)).is_file()
            and (target / p.relative_to(source)).read_bytes() == p.read_bytes()
            for p in source.rglob("*")
            if p.is_file() and "__pycache__" not in p.parts
        ):
            return True
        print(
            f"Warning: {target} already exists and differs; left unchanged. Move it aside before reinstalling."
        )
        return False
    target.parent.mkdir(parents=True, exist_ok=True)
    if not copy:
        try:
            if os.name == "nt":
                import _winapi

                _winapi.CreateJunction(str(source), str(target))
            else:
                target.symlink_to(source, target_is_directory=True)
            return True
        except (OSError, AttributeError):
            print(f"Link unavailable; copying {source.name} instead.")
    shutil.copytree(source, target, ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".git"))
    return True


def install_nodes(
    comfy_dir: Path, *, copy: bool = False, python: Path | None = None, manifest: dict | None = None
) -> int:
    comfy_dir = comfy_directory(comfy_dir)
    if not comfy_dir.is_dir():
        raise ValueError("ComfyUI directory does not exist. Run comfy install first.")
    interpreter = python_path(comfy_dir, python)
    manifest = manifest or load_manifest()
    failed = False
    for pack in manifest["node_packs"]:
        target = comfy_dir / "custom_nodes" / pack["name"]
        local = pack.get("source", "").startswith("local:")
        if local:
            installed = link_pack(
                repo_root() / pack["source"].removeprefix("local:"), target, copy=copy
            )
        elif pack.get("subdir"):
            cache = comfy_dir / ".cache" / "gaiavfx" / "node-packs" / pack["name"]
            installed = checkout(pack["url"], cache, pack["commit"])
            if installed:
                installed = link_pack(cache / pack["subdir"], target, copy=copy)
        else:
            installed = checkout(pack["url"], target, pack["commit"])
        if not installed:
            failed = True
            continue
        print(f"Installed {pack['name']}" + (f": {pack['note']}" if pack.get("note") else ""))
        requirements = target / "requirements.txt"
        if pack["requirements"] and requirements.is_file():
            try:
                run([interpreter, "-m", "pip", "install", "-r", requirements])
            except subprocess.CalledProcessError:
                if not local:
                    raise
                print(
                    "Optional face requirements could not be installed. "
                    + pack.get(
                        "requirements_note",
                        "Install insightface and onnxruntime in ComfyUI's Python, then restart ComfyUI.",
                    )
                )
    update_setup_file(comfy_dir=str(comfy_dir))
    print("Restart ComfyUI, then run: python -m gaiavfx.setup doctor")
    return int(failed)


def register(subparsers):
    parser = subparsers.add_parser("nodes", help="Install pinned custom node packs")
    actions = parser.add_subparsers(dest="nodes_command", required=True)
    install = actions.add_parser("install")
    install.add_argument("--comfy-dir", type=Path, required=True)
    install.add_argument(
        "--copy", action="store_true", help="Copy local and subdirectory packs instead of linking"
    )
    install.add_argument("--python", type=Path, help="Override ComfyUI's Python interpreter")
    install.set_defaults(
        func=lambda args: install_nodes(args.comfy_dir, copy=args.copy, python=args.python)
    )
