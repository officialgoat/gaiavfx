"""Launcher configuration and orchestration, without starting services or installing dependencies."""

import importlib.util
import json
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parents[2]


def script(name):
    spec = importlib.util.spec_from_file_location(name, ROOT / "scripts" / f"{name}.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def setup_file(tmp_path, monkeypatch):
    path = tmp_path / "setup.json"
    monkeypatch.setenv("GAIAVFX_SETUP_FILE", str(path))
    return path


@pytest.mark.parametrize("portable", [True, False])
def test_comfy_launcher_discovers_configured_interpreter(tmp_path, setup_file, portable):
    launcher = script("dev_comfy")
    directory = tmp_path / "ComfyUI"
    directory.mkdir()
    (directory / "main.py").touch()
    suffix = "Scripts/python.exe" if os.name == "nt" else "bin/python"
    python = tmp_path / "python_embeded/python.exe" if portable else directory / ".venv" / suffix
    python.parent.mkdir(parents=True)
    python.touch()
    setup_file.write_text(json.dumps({"comfy_dir": str(directory)}))
    config = launcher.configured_runtime()
    assert Path(config["directory"]) == directory
    assert Path(config["python"]) == python
    assert config["port"] == 8188


def test_comfy_launcher_keeps_explicit_machine_configuration(tmp_path):
    path = tmp_path / "machine.json"
    config = {"directory": "configured", "python": "chosen", "arguments": ["--cpu"]}
    path.write_text(json.dumps({"comfy": config}))
    assert script("dev_comfy").configured_runtime(path) == config


def test_installer_uses_comfy_cli_contract_and_unattended_model_flag(
    tmp_path, monkeypatch, setup_file
):
    installer = script("install")
    calls = []
    monkeypatch.setattr(installer, "has_command", lambda *args: True)
    monkeypatch.setattr(installer, "run_setup", lambda *args: calls.append(tuple(map(str, args))))
    monkeypatch.setattr(
        installer.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=0)
    )
    comfy = tmp_path / "ComfyUI"
    assert (
        installer.main(
            [
                "--non-interactive",
                "--install-comfy",
                str(comfy),
                "--with-assistant",
                "--with-stages-engine",
            ]
        )
        == 0
    )
    assert calls == [
        ("comfy", "install", str(comfy)),
        ("nodes", "install", "--comfy-dir", str(comfy)),
        (
            "models",
            "download",
            "--comfy-dir",
            str(comfy),
            "--features",
            "core",
            "image-edit",
            "stages",
            "recast",
            "--yes",
        ),
        ("configure", "--comfy-url", "http://127.0.0.1:8188"),
        ("engines", "install", "worldbuilder"),
        ("assistant", "install"),
    ]
    assert json.loads(setup_file.read_text())["comfy_dir"] == str(comfy.resolve())


def test_installer_tolerates_absent_comfy_commands(tmp_path, monkeypatch, setup_file, capsys):
    installer = script("install")
    monkeypatch.setattr(installer, "has_command", lambda *args: False)
    monkeypatch.setattr(
        installer, "run_setup", lambda *args: pytest.fail("No unavailable command may be run")
    )
    assert installer.main(["--non-interactive", "--comfy-dir", str(tmp_path), "--skip-models"]) == 0
    assert "Setup finished" in capsys.readouterr().out


def test_installer_requires_explicit_unattended_mode(monkeypatch):
    installer = script("install")
    monkeypatch.setattr(installer.sys.stdin, "isatty", lambda: False)
    with pytest.raises(SystemExit) as error:
        installer.main([])
    assert error.value.code == 2


@pytest.mark.parametrize("doctor_exit,installer_exit", [(1, 0), (2, 2)])
def test_installer_reports_stopped_services_without_masking_cli_errors(
    monkeypatch, setup_file, capsys, doctor_exit, installer_exit
):
    installer = script("install")
    commands = []
    monkeypatch.setattr(installer, "has_command", lambda *args: True)

    def run(command, **kwargs):
        commands.append(command)
        return SimpleNamespace(
            returncode=doctor_exit,
            stdout=json.dumps(
                [{"feature": "core", "item": "ComfyUI", "status": "missing", "detail": "stopped"}]
            )
            if doctor_exit == 1
            else "",
            stderr="",
        )

    monkeypatch.setattr(installer.subprocess, "run", run)
    assert (
        installer.main(["--non-interactive", "--skip-models", "--features", "core"])
        == installer_exit
    )
    assert commands[0][-3:] == ["--features", "core", "runtime"]
    if doctor_exit == 1:
        assert "remaining setup or stopped services" in capsys.readouterr().out


def test_installer_does_not_hide_a_doctor_exception(monkeypatch, setup_file):
    installer = script("install")
    monkeypatch.setattr(installer, "has_command", lambda *args: True)
    monkeypatch.setattr(
        installer.subprocess,
        "run",
        lambda *args, **kwargs: SimpleNamespace(
            returncode=1, stdout="", stderr="Setup failed: invalid manifest"
        ),
    )
    assert installer.main(["--non-interactive", "--skip-models"]) == 1
