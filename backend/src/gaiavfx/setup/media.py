"""Install pinned FFmpeg tools without requiring PowerShell."""

import os
import shutil
import tarfile
import tempfile
import zipfile
from pathlib import Path

from gaiavfx.setup._runtime import host_os, manifest, repo_root, require_x64, write_json
from gaiavfx.setup.download import download
from gaiavfx.setup.manifest import Check


def archive_spec(system: str | None = None) -> dict:
    return manifest()["media"][host_os(system)]


def extract_tools(archive: Path, target: Path, system: str | None = None) -> None:
    suffix = ".exe" if host_os(system) == "windows" else ""
    wanted = {"ffmpeg" + suffix, "ffprobe" + suffix}
    target.mkdir(parents=True, exist_ok=True)
    # Stage both tools before replacing either; paths in the archive are never extracted.
    with tempfile.TemporaryDirectory(dir=target) as staging:
        found = set()

        def copy(name, stream):
            leaf = Path(name).name
            if leaf not in wanted:
                return
            if leaf in found:
                raise ValueError(f"Archive contains more than one {leaf}")
            with (Path(staging) / leaf).open("wb") as output:
                shutil.copyfileobj(stream, output)
            found.add(leaf)

        if zipfile.is_zipfile(archive):
            with zipfile.ZipFile(archive) as bundle:
                for entry in bundle.infolist():
                    if not entry.is_dir() and Path(entry.filename).name in wanted:
                        with bundle.open(entry) as stream:
                            copy(entry.filename, stream)
        else:
            with tarfile.open(archive) as bundle:
                for entry in bundle:
                    if entry.isfile() and Path(entry.name).name in wanted:
                        with bundle.extractfile(entry) as stream:
                            copy(entry.name, stream)
        if found != wanted:
            raise ValueError(f"Archive is missing {', '.join(sorted(wanted - found))}")
        for name in sorted(wanted):
            path = Path(staging) / name
            path.chmod(0o755)
            path.replace(target / name)


def install() -> int:
    require_x64()
    spec = archive_spec()
    target = Path(os.environ.get("GAIAVFX_MEDIA_DIR") or repo_root() / ".runtime/media")
    cache = repo_root() / ".cache/downloads" / spec["url"].rsplit("/", 1)[-1]
    archive = download(spec["url"], cache, sha256=spec["sha256"], verify=True)
    extract_tools(archive, target)
    # Keep the redistribution notice alongside the two executables for portable builds.
    if zipfile.is_zipfile(archive):
        with zipfile.ZipFile(archive) as bundle:
            notices = [name for name in bundle.namelist() if Path(name).name == "LICENSE"]
            if len(notices) == 1:
                (target / "LICENSE").write_bytes(bundle.read(notices[0]))
    else:
        with tarfile.open(archive) as bundle:
            notices = [
                entry
                for entry in bundle
                if entry.isfile() and Path(entry.name).name in ("LICENSE", "LICENSE.txt")
            ]
            if len(notices) == 1:
                with bundle.extractfile(notices[0]) as stream:
                    (target / "LICENSE").write_bytes(stream.read())
    write_json(target / "provenance.json", {"schema": 1, **spec, "platform": host_os()})
    print(f"FFmpeg and ffprobe installed in {target}")
    return 0


def check() -> list[Check]:
    try:
        from gaiavfx.infrastructure.media import MediaTools

        tools = MediaTools()
        return [
            Check(
                "media",
                name,
                "ok" if path.is_file() else "missing",
                str(path),
                "python -m gaiavfx.setup media install",
            )
            for name, path in (("ffmpeg", tools.ffmpeg), ("ffprobe", tools.ffprobe))
        ]
    except Exception as exc:  # noqa: BLE001 - doctor checks must never raise
        return [
            Check("media", "tools", "unknown", str(exc), "python -m gaiavfx.setup media install")
        ]


def register(subparsers):
    parser = subparsers.add_parser("media", help="Install pinned FFmpeg and ffprobe")
    actions = parser.add_subparsers(dest="action", required=True)
    actions.add_parser("install", help="Download and verify media tools").set_defaults(
        func=lambda args: install()
    )
