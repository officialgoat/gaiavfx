"""Worldbuilder resolves portable media tools without depending on a workstation PATH."""

import importlib
import importlib.util
import os
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace


def test_ffmpeg_resolution_order_and_worker_override(tmp_path, monkeypatch):
    workers = Path(__file__).parents[2] / "engine_workers"
    package = ModuleType("media_probe")
    package.__path__ = [str(workers / "worldbuilder_splat")]
    monkeypatch.setitem(sys.modules, package.__name__, package)
    splat = importlib.import_module("media_probe.splat2")
    monkeypatch.setattr(
        splat, "__file__", str(tmp_path / "engine_workers/worldbuilder_splat/splat2.py")
    )
    executable = "ffmpeg.exe" if os.name == "nt" else "ffmpeg"
    bundled = tmp_path / ".runtime" / "media" / executable
    configured = tmp_path / "custom" / executable
    for path in (bundled, configured):
        path.parent.mkdir(parents=True)
        path.touch()
    monkeypatch.setenv("GAIAVFX_MEDIA_DIR", str(configured.parent))
    monkeypatch.setattr(splat.shutil, "which", lambda name: "path-ffmpeg")
    assert splat._ffmpeg() == str(configured)
    configured.unlink()
    assert splat._ffmpeg() == str(bundled)
    bundled.unlink()
    assert splat._ffmpeg() == "path-ffmpeg"

    monkeypatch.syspath_prepend(str(workers))
    monkeypatch.setitem(sys.modules, "worldbuilder_splat", SimpleNamespace(splat2=splat))
    spec = importlib.util.spec_from_file_location(
        "worldbuilder_media_probe", workers / "worldbuilder.py"
    )
    worker = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(worker)
    configured.touch()
    assert worker._ffmpeg(SimpleNamespace(programs={})) == str(configured)
    bundled.touch()
    assert worker._ffmpeg(SimpleNamespace(programs={"ffmpeg": bundled})) == str(bundled)
