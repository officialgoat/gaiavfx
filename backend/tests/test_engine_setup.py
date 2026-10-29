"""ADR 0015 P2: engine setup adopts the research environments by junction, records hashes and never downloads."""

from __future__ import annotations

import hashlib
import importlib.metadata
import importlib.util
import json
import socket
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "engine_setup.py"
spec = importlib.util.spec_from_file_location("engine_setup", SCRIPT)
setup = importlib.util.module_from_spec(spec)
spec.loader.exec_module(setup)

CHECKPOINT = b"bandit weights"
SHA = hashlib.sha256(CHECKPOINT).hexdigest()
PYTEST = importlib.metadata.version("pytest")
REGISTRY = f"""
schema = 2
[engines.analysis]
home = "analysis"
environment = "env/python.exe"
worker = "analysis.py"
packages = {{ pytest = "{PYTEST}", never-installed = "1.0" }}
[engines.analysis.tasks.separate]
gpu = true
vram_gb = 4
[[engines.analysis.models]]
path = "models/bandit/checkpoint-eng.ckpt"
sha256 = "{SHA}"
bytes = {len(CHECKPOINT)}
source = "zenodo.org/records/bandit"
licence = "CC BY-SA 4.0"
[[engines.analysis.models]]
path = "models/whisper/model.bin"
sha256 = "{"b" * 64}"
bytes = 3
source = "huggingface.co/Systran/faster-whisper-large-v3"
licence = "MIT"
"""


@pytest.fixture
def research(tmp_path, monkeypatch):
    """A research tree with the analysis folders, a recorded checkpoint and no network."""

    def no_network(*args, **kwargs):
        raise AssertionError("engine setup must not open network connections")

    monkeypatch.setattr(socket, "socket", no_network)
    source = tmp_path / "voice-research"
    for folder in setup.LAYOUT["analysis"].values():
        (source / folder).mkdir(parents=True, exist_ok=True)
    checkpoint = source / "models" / "eval" / "bandit" / "checkpoint-eng.ckpt"
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_bytes(CHECKPOINT)
    (source / "downloads").mkdir()
    (source / "downloads" / "eval.json").write_text(
        json.dumps(
            [
                {
                    "path": str(checkpoint),
                    "bytes": len(CHECKPOINT),
                    "sha256": SHA,
                    "source": "zenodo.org/records/bandit",
                    "licence": "CC BY-SA 4.0",
                }
            ]
        ),
        encoding="utf-8",
    )
    spawned = []
    real_run = subprocess.run

    def recording_run(command, *args, **kwargs):
        spawned.append(list(command))
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(setup.subprocess, "run", recording_run)
    return source, tmp_path / "engines", spawned


def manifest(root):
    return json.loads((root / "analysis" / "manifest.json").read_text(encoding="utf-8"))


def test_plan_reports_links_and_missing_files_without_changing_anything(research):
    source, root, spawned = research
    report = setup.plan(root, source, ["analysis"], REGISTRY)["analysis"]
    assert report["registered"] and all(link["exists"] for link in report["links"].values())
    assert report["missing"] == [
        {
            "path": "models/whisper/model.bin",
            "source": "huggingface.co/Systran/faster-whisper-large-v3",
        }
    ]
    assert not root.exists() and spawned == []


def test_adopt_links_records_hashes_and_is_idempotent(research):
    source, root, spawned = research
    report = setup.adopt(root, source, ["analysis"], text=REGISTRY)
    home = root / "analysis"
    assert all(setup.is_link(home / child) for child in setup.LAYOUT["analysis"])
    assert (home / "models" / "bandit" / "checkpoint-eng.ckpt").read_bytes() == CHECKPOINT
    assert report["analysis"]["files"] == {"ok": 1, "missing": 1, "mismatch": 0}
    ok, missing = manifest(root)["files"]
    assert (ok["state"], ok["hash"], ok["sha256"]) == ("ok", "recorded", SHA)
    assert missing == {
        "path": "models/whisper/model.bin",
        "source": "huggingface.co/Systran/faster-whisper-large-v3",
        "licence": "MIT",
        "state": "missing",
    }
    links = len(spawned)
    setup.adopt(root, source, ["analysis"], text=REGISTRY)
    assert len(spawned) == links  # nothing relinked
    if sys.platform == "win32":
        assert links == 3 and all(
            command[:4] == ["cmd", "/c", "mklink", "/J"] for command in spawned
        )
    status = setup.summary(root)["engines"]["analysis"]
    assert status["files"] == {"ok": 1, "missing": 1, "mismatch": 0}


def test_an_unrecorded_size_is_hashed_in_full(research):
    source, root, _ = research
    entries = json.loads((source / "downloads" / "eval.json").read_text(encoding="utf-8"))
    entries[0]["bytes"] += 1
    (source / "downloads" / "eval.json").write_text(json.dumps(entries), encoding="utf-8")
    setup.adopt(root, source, ["analysis"], text=REGISTRY)
    assert manifest(root)["files"][0]["hash"] == "full"


def test_adopt_refuses_to_replace_what_it_did_not_make(research, tmp_path):
    source, root, _ = research
    home = root / "analysis"
    home.mkdir(parents=True)
    (home / "env").mkdir()
    with pytest.raises(setup.SetupError, match="not a link"):
        setup.adopt(root, source, ["analysis"], text=REGISTRY)
    (home / "env").rmdir()
    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    setup.link(elsewhere, home / "env")
    with pytest.raises(setup.SetupError, match="points elsewhere"):
        setup.adopt(root, source, ["analysis"], text=REGISTRY)


def test_verify_finds_a_changed_file(research):
    source, root, _ = research
    setup.adopt(root, source, ["analysis"], text=REGISTRY)
    (source / "models" / "eval" / "bandit" / "checkpoint-eng.ckpt").write_bytes(b"other")
    assert setup.verify(root, source, ["analysis"], REGISTRY)["analysis"]["mismatch"] == 1
    assert manifest(root)["files"][0]["hash"] == "full" and manifest(root)["verified_at"]


def test_unlink_removes_the_links_and_keeps_their_targets(research):
    source, root, _ = research
    setup.adopt(root, source, ["analysis"], text=REGISTRY)
    (root / "analysis" / "notes.txt").write_text("mine", encoding="utf-8")
    with pytest.raises(setup.SetupError, match="notes.txt"):
        setup.unlink(root, ["analysis"])
    (root / "analysis" / "notes.txt").unlink()
    assert setup.unlink(root, ["analysis"]) == {"analysis": "removed"}
    assert not (root / "analysis").exists()
    assert (
        source / "models" / "eval" / "bandit" / "checkpoint-eng.ckpt"
    ).read_bytes() == CHECKPOINT
    assert all((source / folder).is_dir() for folder in setup.LAYOUT["analysis"].values())
    assert "analysis" not in setup.summary(root)["engines"]


def test_probe_runs_isolated_and_offline_and_reports_drift(research, monkeypatch):
    source, root, _ = research
    setup.adopt(root, source, ["analysis"], text=REGISTRY)
    seen = {}
    real_run = setup.subprocess.run

    def the_test_interpreter(command, *args, **kwargs):
        if command[0].endswith("python.exe") and str(setup.PROBE) in command:
            seen.update(command=list(command), env=kwargs["env"])
            command = [sys.executable, *command[1:]]  # the fake env holds no interpreter
        return real_run(command, *args, **kwargs)

    monkeypatch.setattr(setup.subprocess, "run", the_test_interpreter)
    report = setup.probe(root, ["analysis"], cuda=False, text=REGISTRY)["analysis"]
    assert report["state"] == "ok" and report["python"]
    assert report["drift"] == {"never-installed": {"pinned": "1.0", "installed": None}}
    assert seen["command"][1] == "-I" and "--cuda" not in seen["command"]
    assert seen["env"]["HF_HUB_OFFLINE"] == "1" and seen["env"]["TRANSFORMERS_OFFLINE"] == "1"
    saved = json.loads((root / "analysis" / "probe.json").read_text(encoding="utf-8"))
    assert saved["packages"]["pytest"] == PYTEST


def test_an_unknown_engine_is_refused(research):
    source, root, _ = research
    with pytest.raises(setup.SetupError, match="No adoption layout"):
        setup.plan(root, source, ["dramabox"], REGISTRY)
