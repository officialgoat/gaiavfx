"""Offline installer contracts: never download model weights or invoke an engine."""

import hashlib
import io
import json
import os
import tarfile
import zipfile
from pathlib import Path

import httpx
import pytest

from gaiavfx.integrations.voice import engines as registry
from gaiavfx.setup import assistant, engines, media
from gaiavfx.setup import download as downloads
from gaiavfx.setup._runtime import manifest, python_path
from gaiavfx.setup.engines import update_setup_file


@pytest.fixture(autouse=True)
def isolated_setup(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIAVFX_SETUP_FILE", str(tmp_path / "setup.json"))
    monkeypatch.delenv("GAIAVFX_ENGINES_ROOT", raising=False)


@pytest.mark.parametrize("system,extension", [("Windows", ".zip"), ("Linux", ".tar.xz")])
def test_media_archive_selection(system, extension):
    spec = media.archive_spec(system)
    assert spec["url"].endswith(extension)
    assert len(spec["sha256"]) == 64


@pytest.mark.parametrize("system", ["Windows", "Linux"])
def test_extract_exactly_two_tools(tmp_path, system):
    archive = tmp_path / "archive"
    suffix = ".exe" if system == "Windows" else ""
    names = ["ffmpeg" + suffix, "ffprobe" + suffix]
    entries = {"release/bin/" + name: name.encode() for name in names}
    entries.update({"release/bin/ffplay" + suffix: b"unused", "../../escape": b"unsafe"})
    if system == "Windows":
        with zipfile.ZipFile(archive, "w") as bundle:
            for name, data in entries.items():
                bundle.writestr(name, data)
    else:
        with tarfile.open(archive, "w:xz") as bundle:
            for name, data in entries.items():
                entry = tarfile.TarInfo(name)
                entry.size = len(data)
                bundle.addfile(entry, io.BytesIO(data))
    target = tmp_path / "installed"
    media.extract_tools(archive, target, system)
    assert sorted(p.name for p in target.iterdir()) == sorted(names)
    assert all((target / name).read_bytes() == name.encode() for name in names)
    assert not (tmp_path / "escape").exists()


def test_sha_mismatch_never_publishes(tmp_path):
    target = tmp_path / "binary"
    transport = httpx.MockTransport(lambda request: httpx.Response(200, content=b"wrong"))
    with (
        httpx.Client(transport=transport) as client,
        pytest.raises(ValueError, match="SHA-256 mismatch"),
    ):
        downloads.download("https://example.test/binary", target, sha256="a" * 64, client=client)
    assert not target.exists()
    assert not target.with_name("binary.part").exists()


@pytest.mark.parametrize("resume_supported", [True, False])
def test_resumable_download(tmp_path, resume_supported):
    target = tmp_path / "binary"
    target.with_name("binary.part").write_bytes(b"abc")
    content = b"abcdef"

    def response(request):
        assert request.headers["Range"] == "bytes=3-"
        return (
            httpx.Response(206, headers={"Content-Range": "bytes 3-5/6"}, content=b"def")
            if resume_supported
            else httpx.Response(200, content=content)
        )

    with httpx.Client(transport=httpx.MockTransport(response)) as client:
        downloads.download(
            "https://example.test/binary",
            target,
            sha256=hashlib.sha256(content).hexdigest(),
            client=client,
        )
    assert target.read_bytes() == content


def test_media_path_fallback(tmp_path, monkeypatch):
    from gaiavfx.infrastructure import media as runtime

    # Relocate the module's repo fallback so existing developer media cannot mask PATH.
    monkeypatch.setattr(
        runtime, "__file__", str(tmp_path / "repo/backend/src/gaiavfx/infrastructure/media.py")
    )
    monkeypatch.setenv("GAIAVFX_MEDIA_DIR", str(tmp_path / "missing"))
    suffix = ".exe" if os.name == "nt" else ""
    for name in ("ffmpeg", "ffprobe"):
        (tmp_path / (name + suffix)).touch()
    monkeypatch.setattr(runtime.shutil, "which", lambda name: str(tmp_path / (name + suffix)))
    tools = runtime.MediaTools()
    assert tools.ffmpeg == tmp_path / ("ffmpeg" + suffix)
    assert tools.ffprobe == tmp_path / ("ffprobe" + suffix)
    preferred = tmp_path / "preferred"
    preferred.mkdir()
    (preferred / ("ffmpeg" + suffix)).touch()
    monkeypatch.setenv("GAIAVFX_MEDIA_DIR", str(preferred))
    assert runtime.MediaTools().ffmpeg.parent == preferred


@pytest.mark.parametrize(
    "system,suffix,platform",
    [("Windows", "Scripts/python.exe", "win32"), ("Linux", "bin/python", "linux")],
)
def test_engine_plan_and_registry(tmp_path, system, suffix, platform):
    plan = engines.install_plan(tmp_path, system)
    python = tmp_path / "worldbuilder" / suffix
    assert python_path(tmp_path / "worldbuilder", system) == python
    assert plan[0] == ["uv", "venv", "--python", "3.10.19", str(tmp_path / "worldbuilder")]
    assert "torch==2.4.1+cu124" in plan[1]
    assert "https://download.pytorch.org/whl/cu124" in plan[1]
    assert "gsplat==1.5.3+pt24cu124" in plan[2]
    assert "--only-binary" in plan[2]
    assert "plyfile==1.1.3" in plan[3]
    assert plan[-1][0] == str(python)
    for engine in registry.load(platform=platform).values():
        assert engine.environment.endswith(suffix)
    from gaiavfx.integrations.worldbuilder.engines import registry_text

    assert registry.load(registry_text(), platform=platform)["worldbuilder"].environment == suffix


def test_engine_check_and_backend_readiness(tmp_path):
    from gaiavfx.api.settings import EnginesDTO
    from gaiavfx.application.engine_jobs import readiness

    root = tmp_path / "engines"
    home = root / "worldbuilder"
    python = python_path(home)
    python.parent.mkdir(parents=True)
    python.touch()
    (home / "manifest.json").write_text(json.dumps({"files": [], "schema": 1}))
    (home / "probe.json").write_text(
        json.dumps(
            {
                "state": "ok",
                "python": "3.10.19",
                "cuda": {"available": True, "device": "Fixture GPU"},
            }
        )
    )
    update_setup_file(engines_root=str(root))
    rows = engines.check()
    assert all(row.status == "ok" for row in rows if row.feature == "stages")
    assert rows[0].status == "optional"
    assert rows[0].detail == "experimental, manual setup"
    ready = next(item for item in readiness()["engines"] if item["name"] == "worldbuilder")
    assert ready["installed"] and ready["probe"] == "ok"
    assert ready["cuda_device"] == "Fixture GPU"
    EnginesDTO.model_validate(readiness())
    (home / "probe.json").write_text("broken")
    assert any(row.status == "unknown" for row in engines.check())


def test_engine_install_records_probe_without_executing(tmp_path, monkeypatch):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command)
        if "--cuda" in command:
            spec = manifest()["worldbuilder"]
            packages = {
                name.lower(): version for name, version in (p.split("==") for p in spec["packages"])
            }
            packages.update(torch=spec["torch"], gsplat=spec["gsplat"])
            return type(
                "Result",
                (),
                {
                    "stdout": json.dumps(
                        {
                            "packages": packages,
                            "python": spec["python"],
                            "cuda": {"available": True},
                        }
                    )
                },
            )()

    monkeypatch.setattr(engines.subprocess, "run", fake_run)
    assert engines.install(tmp_path / "engines") == 0
    assert len(calls) == 5
    probe = json.loads((tmp_path / "engines/worldbuilder/probe.json").read_text())
    assert probe["state"] == "ok" and not probe["drift"]
    assert json.loads((tmp_path / "setup.json").read_text())["engines_root"] == str(
        (tmp_path / "engines").resolve()
    )


@pytest.mark.parametrize("system,count", [("Windows", 4), ("Linux", 3)])
def test_assistant_plan(tmp_path, system, count):
    plan = assistant.install_plan(tmp_path, system)
    assert len(plan) == count
    assert all(item["url"].startswith("https://") and len(item["sha256"]) == 64 for item in plan)
    assert len(assistant.install_plan(tmp_path, system, model_only=True)) == 2
    assert all(
        item["kind"] == "server"
        for item in assistant.install_plan(tmp_path, system, server_only=True)
    )
    with pytest.raises(ValueError):
        assistant.install_plan(tmp_path, system, server_only=True, model_only=True)


def test_assistant_install_keys_and_check(tmp_path, monkeypatch):
    def fake_download(url, destination, *, sha256, verify):
        assert len(sha256) == 64 and verify
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.touch()
        return destination

    def fake_extract(archive, target):
        target.mkdir(parents=True, exist_ok=True)
        (target / ("llama-server.exe" if os.name == "nt" else "llama-server")).touch()

    monkeypatch.setattr(assistant, "download", fake_download)
    monkeypatch.setattr(assistant, "extract_server", fake_extract)
    update_setup_file(comfy_dir="existing")
    assert assistant.install(tmp_path / "assistant") == 0
    settings = json.loads((tmp_path / "setup.json").read_text())
    assert settings["comfy_dir"] == "existing"
    assert all(
        Path(settings[key]).is_file()
        for key in ("assistant_executable", "assistant_model", "assistant_projector")
    )
    assert all(row.status == "ok" for row in assistant.check())
    Path(settings["assistant_model"]).unlink()
    assert (
        next(row for row in assistant.check() if row.item == "assistant_model").status == "missing"
    )
    (tmp_path / "setup.json").write_text("[]")
    assert assistant.check()[0].status == "unknown"


def test_server_archive_refuses_traversal(tmp_path):
    archive = tmp_path / "bad.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../../escaped", b"bad")
    with pytest.raises(ValueError, match="Unsafe"):
        assistant.extract_server(archive, tmp_path / "server")
    assert not (tmp_path / "escaped").exists()


def test_linux_server_materializes_internal_library_links(tmp_path):
    archive = tmp_path / "server.tar.gz"
    with tarfile.open(archive, "w:gz") as bundle:
        entry = tarfile.TarInfo("build/bin/libllama.so.1")
        entry.size, entry.mode = 3, 0o755
        bundle.addfile(entry, io.BytesIO(b"lib"))
        link = tarfile.TarInfo("build/bin/libllama.so")
        link.type, link.linkname = tarfile.SYMTYPE, "libllama.so.1"
        bundle.addfile(link)
    assistant.extract_server(archive, tmp_path / "server")
    library = tmp_path / "server/build/bin/libllama.so"
    assert library.read_bytes() == b"lib" and not library.is_symlink()
