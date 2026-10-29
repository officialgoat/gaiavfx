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
    assert (
        installer.main(
            [
                "--non-interactive",
                "--comfy-dir",
                str(tmp_path),
                "--skip-models",
                "--skip-assistant",
                "--skip-stages-engine",
            ]
        )
        == 0
    )
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
        installer.main(
            [
                "--non-interactive",
                "--skip-models",
                "--skip-assistant",
                "--skip-stages-engine",
                "--features",
                "core",
            ]
        )
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
    assert (
        installer.main(
            ["--non-interactive", "--skip-models", "--skip-assistant", "--skip-stages-engine"]
        )
        == 1
    )


def test_comfy_worker_disables_api_nodes_and_forwards_extra_arguments(tmp_path, monkeypatch):
    launcher = script("dev_comfy")
    runtime = tmp_path / "ComfyUI"
    runtime.mkdir()
    (runtime / "main.py").touch()
    python = tmp_path / "python"
    python.touch()
    monkeypatch.setattr(
        launcher,
        "configured_runtime",
        lambda _: {
            "directory": str(runtime),
            "python": str(python),
            "data_directory": str(tmp_path / "data"),
            "arguments": ["--lowvram"],
        },
    )
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("HF_HUB_OFFLINE", "0")
    monkeypatch.setattr(launcher.sys, "path", list(launcher.sys.path))
    monkeypatch.setattr(
        launcher.sys, "argv", ["dev_comfy.py", "--worker", "--", "--reserve-vram", "4"]
    )
    calls = []
    monkeypatch.setattr(
        launcher.runpy, "run_path", lambda *a, **kw: calls.append(list(launcher.sys.argv))
    )
    launcher.main()
    assert len(calls) == 1
    assert "--disable-api-nodes" in calls[0]
    assert calls[0][-3:] == ["--lowvram", "--reserve-vram", "4"]


def test_start_comfy_forwards_arguments_without_starting_service(tmp_path, monkeypatch):
    monkeypatch.syspath_prepend(str(ROOT / "scripts"))
    launcher = script("start_comfy")
    (tmp_path / "main.py").touch()
    (tmp_path / "python").touch()
    monkeypatch.setattr(
        launcher,
        "configured_runtime",
        lambda: {
            "directory": str(tmp_path),
            "python": str(tmp_path / "python"),
        },
    )
    monkeypatch.setattr(
        launcher.sys, "argv", ["start_comfy.py", "--lowvram", "--reserve-vram", "4"]
    )
    calls = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda args, **kwargs: calls.append(args))
    launcher.main()
    assert calls[0][-5:] == ["--worker", "--", "--lowvram", "--reserve-vram", "4"]


@pytest.mark.parametrize("interactive", [True, False])
def test_installer_addons_default_on(tmp_path, monkeypatch, setup_file, interactive):
    installer = script("install")
    calls, prompts = [], []
    monkeypatch.setattr(installer.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(installer, "default_comfy_directory", lambda: tmp_path / "ComfyUI")
    monkeypatch.setattr(installer, "has_command", lambda *a: True)
    monkeypatch.setattr(installer, "run_setup", lambda *a: calls.append(a))
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))

    def answer(prompt):
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", answer)
    assert installer.main([] if interactive else ["--non-interactive"]) == 0
    assert ("assistant", "install") in calls
    assert ("engines", "install", "worldbuilder") in calls
    assert (
        ("comfy", "install", tmp_path / "ComfyUI") in calls
        if interactive
        else not any(c[0] == "comfy" for c in calls)
    )
    if interactive:
        assert any("GB total)? [Y/n]" in p for p in prompts)
        assert all("[Y/n]" in p for p in prompts[1:])
        assert any(c[0] == "models" for c in calls)


@pytest.mark.parametrize("interactive", [True, False])
def test_skipped_comfy_never_prompts_for_models(
    tmp_path, monkeypatch, setup_file, capsys, interactive
):
    installer = script("install")
    setup_file.write_text(json.dumps({"comfy_dir": str(tmp_path / "saved")}))
    monkeypatch.setattr(installer.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(installer, "has_command", lambda *a: True)
    calls, prompts = [], []
    monkeypatch.setattr(installer, "run_setup", lambda *a: calls.append(a))
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))

    def answer(prompt):
        prompts.append(prompt)
        return "3"

    monkeypatch.setattr("builtins.input", answer)
    args = ["--skip-assistant", "--skip-stages-engine"]
    if not interactive:
        args.append("--non-interactive")
    assert installer.main(args) == 0
    assert not calls
    assert not any("model" in p.lower() for p in prompts)
    output = capsys.readouterr().out
    assert "models were skipped because no ComfyUI folder was chosen" in output
    assert ("-InstallComfy" if os.name == "nt" else "--install-comfy") in output
    assert ("-NonInteractive" if os.name == "nt" else "--non-interactive") in output
    assert "Ignoring saved ComfyUI" in output
    assert "scripts/install.py" not in output
    assert str(installer.default_comfy_directory()) in output


def test_legacy_flags_are_noops_and_skip_wins(monkeypatch, setup_file, capsys):
    installer = script("install")
    calls = []
    monkeypatch.setattr(installer, "has_command", lambda *a: True)
    monkeypatch.setattr(installer, "run_setup", lambda *a: calls.append(a))
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))
    assert (
        installer.main(
            [
                "--non-interactive",
                "--with-assistant",
                "--with-stages-engine",
                "--skip-assistant",
                "--skip-stages-engine",
            ]
        )
        == 0
    )
    assert not calls
    output = capsys.readouterr().out
    assert output.count("Deprecated:") == 2
    assert json.loads(setup_file.read_text())["skip_assistant"] is True


def test_installer_interrupt_message(monkeypatch, capsys):
    installer = script("install")

    def interrupt(_):
        print("download progress", end="")
        raise KeyboardInterrupt

    monkeypatch.setattr(installer, "main", interrupt)
    assert installer.entrypoint([]) == 130
    assert (
        capsys.readouterr().err
        == "\nCancelled. Run the same command again to resume partial downloads.\n"
    )


def test_interactive_existing_folder_and_explicit_no(tmp_path, monkeypatch, setup_file):
    installer = script("install")
    calls, prompts = [], []
    answers = iter(["2", str(tmp_path / "existing"), "n", "no", "n"])
    monkeypatch.setattr(installer.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(installer, "has_command", lambda *a: True)
    monkeypatch.setattr(installer, "run_setup", lambda *a: calls.append(a))
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))

    def answer(prompt):
        prompts.append(prompt)
        return next(answers)

    monkeypatch.setattr("builtins.input", answer)
    assert installer.main([]) == 0
    assert [call[0] for call in calls] == ["nodes", "configure"]
    settings = json.loads(setup_file.read_text())
    assert settings["comfy_dir"] == str(tmp_path / "existing")
    assert settings["skip_assistant"] and settings["skip_stages_engine"]


@pytest.fixture
def mocked_installer(monkeypatch, setup_file):
    installer = script("install")
    calls, prompts = [], []
    monkeypatch.setattr(installer.sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr(installer, "has_command", lambda *a: True)

    def run_setup(*args):
        calls.append(args)
        if args[:2] == ("comfy", "install"):
            installer.update_setup_file(comfy_dir=str(args[2]))

    monkeypatch.setattr(installer, "run_setup", run_setup)
    monkeypatch.setattr(installer.subprocess, "run", lambda *a, **kw: SimpleNamespace(returncode=0))

    def answer(prompt):
        prompts.append(prompt)
        return ""

    monkeypatch.setattr("builtins.input", answer)
    return installer, calls, prompts


@pytest.mark.parametrize("interactive", [True, False])
@pytest.mark.parametrize("layout", ["", "ComfyUI", "ComfyUI_windows_portable/ComfyUI"])
def test_saved_comfy_is_reused(tmp_path, setup_file, mocked_installer, capsys, interactive, layout):
    installer, calls, prompts = mocked_installer
    saved = tmp_path / "saved folder"
    actual = saved / layout
    actual.mkdir(parents=True)
    (actual / "main.py").touch()
    setup_file.write_text(json.dumps({"comfy_dir": str(saved)}))
    assert installer.main([] if interactive else ["--non-interactive"]) == 0
    assert ("nodes", "install", "--comfy-dir", actual) in calls
    assert any(call[0] == "models" for call in calls)
    assert not any(call[0] == "comfy" for call in calls)
    if interactive:
        assert (
            prompts[0]
            == f"ComfyUI: (1) use saved {actual}, (2) install fresh into {installer.default_comfy_directory()}, (3) use another existing folder, (4) skip [1]: "
        )
    else:
        assert not prompts
        assert (
            f"Using saved ComfyUI {actual}; pass -ComfyDir/--comfy-dir to change"
            in capsys.readouterr().out
        )


@pytest.mark.parametrize("choice", ["2", "3", "4"])
def test_saved_comfy_other_choices(tmp_path, monkeypatch, setup_file, mocked_installer, choice):
    installer, calls, _ = mocked_installer
    saved = tmp_path / "saved"
    saved.mkdir()
    (saved / "main.py").touch()
    setup_file.write_text(json.dumps({"comfy_dir": str(saved)}))
    fresh, other = tmp_path / "fresh", tmp_path / "other"
    monkeypatch.setattr(installer, "default_comfy_directory", lambda: fresh)
    answers = iter([choice, str(other)])
    monkeypatch.setattr("builtins.input", lambda _: next(answers))
    assert installer.main(["--skip-models", "--skip-assistant", "--skip-stages-engine"]) == 0
    if choice == "2":
        assert ("comfy", "install", fresh) in calls
        assert ("nodes", "install", "--comfy-dir", str(fresh)) in calls
    elif choice == "3":
        assert ("nodes", "install", "--comfy-dir", other) in calls
    else:
        assert not calls


@pytest.mark.parametrize("flag", ["--comfy-dir", "--install-comfy"])
def test_explicit_location_overrides_saved(tmp_path, setup_file, mocked_installer, capsys, flag):
    installer, calls, _ = mocked_installer
    saved = tmp_path / "saved"
    saved.mkdir()
    (saved / "main.py").touch()
    setup_file.write_text(json.dumps({"comfy_dir": str(saved)}))
    chosen = tmp_path / "chosen"
    assert installer.main(["--non-interactive", flag, str(chosen)]) == 0
    assert (
        ("comfy", "install", chosen) in calls
        if flag == "--install-comfy"
        else ("nodes", "install", "--comfy-dir", chosen) in calls
    )
    assert "Using saved ComfyUI" not in capsys.readouterr().out


def test_saved_folder_without_main_is_ignored(tmp_path, setup_file, mocked_installer, capsys):
    installer, calls, _ = mocked_installer
    saved = tmp_path / "empty"
    saved.mkdir()
    setup_file.write_text(json.dumps({"comfy_dir": str(saved)}))
    assert installer.main(["--non-interactive", "--skip-assistant", "--skip-stages-engine"]) == 0
    assert not calls
    output = capsys.readouterr().out
    assert "Ignoring saved ComfyUI" in output
    assert "no ComfyUI folder was chosen" in output


@pytest.mark.parametrize("platform", ["win32", "linux"])
def test_skip_hints_use_platform_launcher(
    tmp_path, monkeypatch, mocked_installer, capsys, platform
):
    installer, _, _ = mocked_installer
    monkeypatch.setattr(installer.sys, "platform", platform)
    monkeypatch.setattr(installer, "default_comfy_directory", lambda: tmp_path / "Comfy UI")
    assert (
        installer.main(
            [
                "--non-interactive",
                "--skip-models",
                "--skip-assistant",
                "--skip-stages-engine",
                "--features",
                "core",
                "stages",
            ]
        )
        == 0
    )
    output = capsys.readouterr().out
    expected = (
        f'.\\install.ps1 -InstallComfy "{tmp_path / "Comfy UI"}" -NonInteractive -Features core,stages'
        if platform == "win32"
        else 'bash install.sh --install-comfy "'
        + str(tmp_path / "Comfy UI").replace("\\", "\\\\")
        + '" --non-interactive --features core stages'
    )
    assert expected in output
    assert "scripts/install.py" not in output
    assistant = next(
        line for line in output.splitlines() if line.startswith("Assistant was skipped.")
    )
    engine = next(
        line for line in output.splitlines() if line.startswith("Stages engine was skipped.")
    )
    assert ("-SkipAssistant" if platform == "win32" else "--skip-assistant") not in assistant
    assert ("-SkipStagesEngine" if platform == "win32" else "--skip-stages-engine") not in engine
    assert ("-SkipModels" if platform == "win32" else "--skip-models") in assistant
    comfy = tmp_path / "chosen folder"
    assert installer.main(["--non-interactive", "--comfy-dir", str(comfy), "--skip-models"]) == 0
    output = capsys.readouterr().out
    hint = next(line for line in output.splitlines() if line.startswith("Models were skipped."))
    assert ("-ComfyDir" if platform == "win32" else "--comfy-dir") in hint
    assert "-SkipModels" not in hint and "--skip-models" not in hint
    assert "-Features" not in hint and "--features" not in hint
