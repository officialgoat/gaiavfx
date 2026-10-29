"""Install a pinned local assistant server and a public vision-capable Qwen GGUF."""

import posixpath
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath

from gaiavfx.setup._runtime import host_os, manifest, read_setup, repo_root, require_x64
from gaiavfx.setup._runtime_download import download

try:
    from gaiavfx.setup.manifest import Check, update_setup_file
except ImportError:
    from gaiavfx.setup._contract import Check, update_setup_file

ALTERNATIVE = "Any OpenAI-compatible local endpoint (LM Studio, Ollama, vLLM) can be added in Settings instead."


def install_plan(
    directory: Path, system: str | None = None, *, model_only=False, server_only=False
) -> list[dict]:
    if model_only and server_only:
        raise ValueError("Choose either --model-only or --server-only.")
    spec = manifest()["assistant"]
    result = []
    if not model_only:
        for key in [host_os(system), "cudart"] if host_os(system) == "windows" else ["linux"]:
            result.append(
                {
                    "kind": "server",
                    **spec[key],
                    "destination": directory / "downloads" / spec[key]["file"],
                }
            )
    if not server_only:
        for key in ("model", "projector"):
            result.append(
                {"kind": key, **spec[key], "destination": directory / "models" / spec[key]["file"]}
            )
    return result


def extract_server(archive: Path, target: Path) -> None:
    """Retain release layout; materialize internal library links as ordinary files."""
    target.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target) as staging:
        root = Path(staging)

        def destination(name):
            relative = PurePosixPath(name.replace("\\", "/"))
            path = root.joinpath(*relative.parts).resolve()
            if relative.is_absolute() or ":" in name or not path.is_relative_to(root.resolve()):
                raise ValueError(f"Unsafe archive path: {name}")
            path.parent.mkdir(parents=True, exist_ok=True)
            return path

        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as bundle:
                for entry in bundle.infolist():
                    if entry.is_dir():
                        continue
                    if (entry.external_attr >> 16) & 0o170000 == 0o120000:
                        raise ValueError("Server archive contains a symbolic link")
                    with (
                        bundle.open(entry) as source,
                        destination(entry.filename).open("wb") as output,
                    ):
                        shutil.copyfileobj(source, output)
        else:
            with tarfile.open(archive) as bundle:
                members = {posixpath.normpath(entry.name): entry for entry in bundle.getmembers()}

                def regular(entry, seen=None):
                    seen = set() if seen is None else seen
                    if entry.name in seen:
                        raise ValueError("Cyclic link in server archive")
                    seen.add(entry.name)
                    if entry.isfile():
                        return entry
                    if not (entry.issym() or entry.islnk()):
                        raise ValueError(f"Server archive contains a special file: {entry.name}")
                    if PurePosixPath(entry.linkname).is_absolute():
                        raise ValueError("Unsafe absolute link in server archive")
                    linked = posixpath.normpath(
                        posixpath.join(posixpath.dirname(entry.name), entry.linkname)
                        if entry.issym()
                        else entry.linkname
                    )
                    destination(linked)
                    if linked not in members:
                        raise ValueError(f"Missing server archive link target: {linked}")
                    return regular(members[linked], seen)

                for entry in members.values():
                    if entry.isdir():
                        continue
                    path = destination(entry.name)
                    source_entry = regular(entry)
                    with bundle.extractfile(source_entry) as source, path.open("wb") as output:
                        shutil.copyfileobj(source, output)
                    path.chmod(source_entry.mode & 0o777)
        shutil.copytree(root, target, dirs_exist_ok=True)


def install(directory: Path, *, model_only=False, server_only=False) -> int:
    require_x64()
    directory = directory.expanduser().resolve()
    keys = {}
    for item in install_plan(directory, model_only=model_only, server_only=server_only):
        path = download(item["url"], item["destination"], item["sha256"])
        if item["kind"] == "server":
            extract_server(path, directory / "server")
        else:
            keys["assistant_" + item["kind"]] = str(path)
    if not model_only:
        name = "llama-server.exe" if host_os() == "windows" else "llama-server"
        candidates = list((directory / "server").rglob(name))
        if len(candidates) != 1:
            raise ValueError(f"Expected exactly one {name} in the release archive")
        keys["assistant_executable"] = str(candidates[0])
    update_setup_file(**keys)
    if host_os() == "linux" and not model_only:
        print(
            "Linux release is CPU-only. For GPU acceleration, build llama.cpp with CUDA or use another local endpoint."
        )
    print(ALTERNATIVE)
    print(f"Assistant files installed in {directory}. No server was started.")
    return 0


def check() -> list[Check]:
    fix = "python -m gaiavfx.setup assistant install"
    try:
        settings = read_setup()
        return [
            Check(
                "assistant",
                key,
                "ok" if settings.get(key) and Path(settings[key]).is_file() else "missing",
                settings.get(key) or "Not configured. " + ALTERNATIVE,
                fix,
            )
            for key in ("assistant_executable", "assistant_model", "assistant_projector")
        ]
    except Exception as exc:  # noqa: BLE001 - doctor checks must never raise
        return [Check("assistant", "files", "unknown", str(exc), fix)]


def register(subparsers):
    parser = subparsers.add_parser(
        "assistant", help="Install the optional local assistant", description=ALTERNATIVE
    )
    actions = parser.add_subparsers(dest="action", required=True)
    command = actions.add_parser("install", description=ALTERNATIVE)
    command.add_argument("--dir", type=Path, default=repo_root() / ".runtime/assistant")
    choice = command.add_mutually_exclusive_group()
    choice.add_argument("--model-only", action="store_true")
    choice.add_argument("--server-only", action="store_true")
    command.set_defaults(
        func=lambda args: install(
            args.dir, model_only=args.model_only, server_only=args.server_only
        )
    )
