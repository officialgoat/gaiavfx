"""Install the tested ComfyUI portable build or an isolated Git checkout."""

import os
import subprocess
from pathlib import Path
from urllib.parse import urlparse

from .download import download
from .manifest import comfy_directory, command, load_manifest, read_setup_file, update_setup_file
from .nodes import checkout, python_path, run


def start_command(comfy_dir: Path, port: int = 8188) -> str:
    try:
        interpreter = python_path(comfy_dir)
    except ValueError:
        interpreter = (
            comfy_dir / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
    args = [interpreter]
    if interpreter.parent.name == "python_embeded":
        args.append("-s")
    return command(*args, comfy_dir / "main.py", "--port", port)


def install_comfy(args) -> int:
    baseline = load_manifest()["comfyui"]
    destination = args.directory.expanduser().resolve()
    tag = args.version or baseline["tag"]
    existing = comfy_directory(destination)
    portable = (existing.parent / "python_embeded/python.exe").is_file()
    use_git = args.git or ((destination / ".git").exists() and not portable)
    if os.name == "nt" and not use_git:
        if tag != baseline["tag"] or args.cuda:
            raise ValueError(
                "The verified portable build uses the pinned version and CUDA. Use --git with --version or --cuda."
            )
        comfy_dir = existing if portable else destination / "ComfyUI_windows_portable" / "ComfyUI"
        if (comfy_dir / "main.py").is_file():
            if (comfy_dir / ".git").exists():
                if not checkout(baseline["repo"], comfy_dir, tag):
                    return 1
                run(
                    [
                        python_path(comfy_dir),
                        "-m",
                        "pip",
                        "install",
                        "-r",
                        comfy_dir / "requirements.txt",
                    ]
                )
        else:
            if destination.exists() and any(destination.iterdir()):
                raise ValueError(
                    "Portable target is not empty. Choose an empty folder, or --git for an existing clone."
                )
            archive = (
                destination.parent / ".cache" / "gaiavfx" / "ComfyUI_windows_portable_nvidia.7z"
            )
            download(
                baseline["windows_portable_url"],
                archive,
                size=baseline["windows_portable_bytes"],
                sha256=baseline["windows_portable_sha256"],
                verify=True,
            )
            destination.mkdir(parents=True, exist_ok=True)
            try:
                run(["tar.exe", "-xf", archive, "-C", destination])
            except (OSError, subprocess.SubprocessError) as exc:
                raise ValueError(
                    f"Portable extraction failed. Extract {archive} into {destination} with 7-Zip, or use comfy install DIR --git."
                ) from exc
        python_path(comfy_dir)
    else:
        comfy_dir = destination
        if not checkout(baseline["repo"], comfy_dir, tag):
            return 1
        environment = comfy_dir / ".venv"
        if not environment.exists():
            run(["uv", "venv", "--python", baseline["python"], "--seed", environment])
        interpreter = python_path(comfy_dir)
        cuda = args.cuda or baseline["torch_index"]
        index = cuda if cuda.startswith("https://") else f"https://download.pytorch.org/whl/{cuda}"
        packages = [f"{name}=={baseline[name]}" for name in ("torch", "torchvision", "torchaudio")]
        run([interpreter, "-m", "pip", "install", *packages, "--index-url", index])
        run([interpreter, "-m", "pip", "install", "-r", comfy_dir / "requirements.txt"])
    comfy_url = os.environ.get("GAIAVFX_COMFY_URL") or read_setup_file().get(
        "comfy_url", "http://127.0.0.1:8188"
    )
    update_setup_file(comfy_dir=str(comfy_dir), comfy_url=comfy_url)
    port = urlparse(comfy_url).port or 8188
    print(f"ComfyUI installed at {comfy_dir}. Start it with:\n{start_command(comfy_dir, port)}")
    return 0


def register(subparsers):
    parser = subparsers.add_parser("comfy", help="Install ComfyUI without starting it")
    actions = parser.add_subparsers(dest="comfy_command", required=True)
    install = actions.add_parser("install")
    install.add_argument("directory", type=Path, metavar="DIR")
    install.add_argument("--version", metavar="TAG")
    install.add_argument(
        "--git", action="store_true", help="Use Git and uv instead of the Windows portable build"
    )
    install.add_argument(
        "--cuda", help="PyTorch wheel index, e.g. cu130, cu128 or cpu (Git installs)"
    )
    install.set_defaults(func=install_comfy)
