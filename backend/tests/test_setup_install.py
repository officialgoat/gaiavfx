import argparse
import hashlib
import os
import subprocess
from pathlib import Path

import pytest

from gaiavfx.setup import cli, comfy_install, models, nodes
from gaiavfx.setup.manifest import load_manifest, read_setup_file


def git(*args):
    return subprocess.run(
        ["git", *map(str, args)], check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def local_repo(tmp_path):
    source = tmp_path / "source"
    git("init", source)
    git("-C", source, "config", "user.email", "fixture@example.invalid")
    git("-C", source, "config", "user.name", "Setup fixture")
    (source / "node").mkdir()
    (source / "node/__init__.py").write_text("NODE_CLASS_MAPPINGS = {}\n")
    git("-C", source, "add", ".")
    git("-C", source, "commit", "-m", "pinned fixture")
    pinned = git("-C", source, "rev-parse", "HEAD")
    (source / "later.txt").write_text("later revision")
    git("-C", source, "add", ".")
    git("-C", source, "commit", "-m", "newer fixture")
    bare = tmp_path / "upstream.git"
    git("clone", "--bare", source, bare)
    return bare, pinned


def test_nodes_install_pinned_local_pack_and_subdirectory(tmp_path, local_repo, monkeypatch):
    bare, pinned = local_repo
    monkeypatch.setenv("GAIAVFX_SETUP_FILE", str(tmp_path / "setup.json"))
    comfy = tmp_path / "ComfyUI"
    interpreter = comfy / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    interpreter.parent.mkdir(parents=True)
    interpreter.touch()
    manifest = {
        "node_packs": [
            {"name": "gaiavfx", "source": "local:comfy_nodes/gaiavfx", "requirements": False},
            {"name": "fixture", "url": str(bare), "commit": pinned, "requirements": False},
            {
                "name": "subdir",
                "url": str(bare),
                "commit": pinned,
                "subdir": "node",
                "requirements": False,
            },
        ]
    }
    assert nodes.install_nodes(comfy, manifest=manifest) == 0
    assert git("-C", comfy / "custom_nodes/fixture", "rev-parse", "HEAD") == pinned
    ours = comfy / "custom_nodes/gaiavfx"
    assert ours.is_symlink() or ours.is_junction()
    assert (comfy / "custom_nodes/subdir/__init__.py").is_file()
    assert read_setup_file()["comfy_dir"] == str(comfy)
    assert nodes.install_nodes(comfy, manifest=manifest) == 0
    change = comfy / "custom_nodes/fixture/local.txt"
    change.write_text("user changes")
    assert nodes.install_nodes(comfy, manifest=manifest) == 1
    assert change.read_text() == "user changes"
    assert git("-C", comfy / "custom_nodes/fixture", "rev-parse", "HEAD") == pinned


def test_copy_and_link_failure_fallback(tmp_path, monkeypatch):
    source = tmp_path / "source"
    source.mkdir()
    (source / "node.py").write_text("example")
    assert nodes.link_pack(source, tmp_path / "copy", copy=True)
    assert not (tmp_path / "copy").is_symlink()
    if os.name == "nt":
        import _winapi

        monkeypatch.setattr(
            _winapi, "CreateJunction", lambda *_: (_ for _ in ()).throw(OSError("denied"))
        )
    else:
        monkeypatch.setattr(
            Path, "symlink_to", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("denied"))
        )
    assert nodes.link_pack(source, tmp_path / "fallback")
    assert (tmp_path / "fallback/node.py").read_text() == "example"
    (tmp_path / "copy/node.py").write_text("local changes")
    assert not nodes.link_pack(source, tmp_path / "copy", copy=True)


def test_node_requirements_suppress_script_location_warning(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIAVFX_SETUP_FILE", str(tmp_path / "setup.json"))
    pack = tmp_path / "custom_nodes/gaiavfx"
    pack.mkdir(parents=True)
    (pack / "requirements.txt").write_text("fixture==1\n")
    monkeypatch.setattr(nodes, "python_path", lambda *args: tmp_path / "python")
    monkeypatch.setattr(nodes, "link_pack", lambda *args, **kwargs: True)
    commands = []
    monkeypatch.setattr(nodes, "run", lambda args: commands.append(args))
    assert (
        nodes.install_nodes(
            tmp_path,
            manifest={
                "node_packs": [
                    {
                        "name": "gaiavfx",
                        "source": "local:comfy_nodes/gaiavfx",
                        "requirements": True,
                    }
                ]
            },
        )
        == 0
    )
    assert commands == [
        [
            tmp_path / "python",
            "-m",
            "pip",
            "install",
            "--no-warn-script-location",
            "-r",
            pack / "requirements.txt",
        ]
    ]


def test_interpreter_detection_order(tmp_path):
    comfy = tmp_path / "ComfyUI"
    candidates = [
        comfy / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
        tmp_path / "python_embeded/python.exe",
        comfy / "venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python"),
    ]
    for path in candidates:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.touch()
    for path in candidates:
        assert nodes.python_path(comfy) == path.resolve()
        path.unlink()
    with pytest.raises(ValueError, match="--python"):
        nodes.python_path(comfy)


def test_git_comfy_install_uses_pins_without_starting(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GAIAVFX_SETUP_FILE", str(tmp_path / "setup.json"))
    monkeypatch.setenv("GAIAVFX_COMFY_URL", "http://127.0.0.1:8999")
    calls = []
    monkeypatch.setattr(comfy_install, "checkout", lambda *args: calls.append(args) or True)
    monkeypatch.setattr(comfy_install, "run", lambda args: calls.append(args))
    monkeypatch.setattr(comfy_install, "python_path", lambda path: path / ".venv/python")
    args = argparse.Namespace(directory=tmp_path / "ComfyUI", version=None, git=True, cuda=None)
    assert comfy_install.install_comfy(args) == 0
    assert calls[0][-1] == "v0.42.1"
    assert "3.13" in calls[1]
    assert "torch==2.14.0" in calls[2]
    assert "https://download.pytorch.org/whl/cu130" in calls[2]
    assert not any("main.py" in str(call) for call in calls)
    assert read_setup_file()["comfy_dir"] == str(args.directory)
    assert read_setup_file()["comfy_url"] == "http://127.0.0.1:8999"
    output = capsys.readouterr().out
    assert "--port 8999" in output
    assert "--disable-api-nodes" in output


def test_model_download_uses_shared_path_and_dry_run_is_read_only(tmp_path, monkeypatch, capsys):
    comfy = tmp_path / "ComfyUI"
    comfy.mkdir()
    (comfy / "extra_model_paths.yaml").write_text("shared:\n  base_path: ../models\n  vae: vae\n")
    payload = b"small fixture"
    manifest = load_manifest()
    manifest["models"] = [
        {
            "file": "tiny.bin",
            "folder": "vae",
            "bytes": len(payload),
            "sha256": hashlib.sha256(payload).hexdigest(),
            "url": "https://example.invalid/model",
            "features": ["core"],
            "optional": False,
            "license": "Test",
            "note": "",
        }
    ]
    monkeypatch.setattr(models, "load_manifest", lambda: manifest)
    args = argparse.Namespace(
        comfy_dir=comfy,
        features=["core"],
        all=False,
        dry_run=True,
        verify=False,
        extra_model_paths=None,
        yes=True,
    )
    assert models.install_models(args) == 0
    assert "total 13 bytes" in capsys.readouterr().out
    assert not (tmp_path / "models").exists()
    downloads = []
    monkeypatch.setattr(models, "download", lambda url, path, **kwargs: downloads.append(path))
    args.dry_run = False
    assert models.install_models(args) == 0
    assert downloads == [(tmp_path / "models/vae/tiny.bin").resolve()]


def test_configure_and_missing_runtime_cli(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("GAIAVFX_SETUP_FILE", str(tmp_path / "setup.json"))
    assert (
        cli.main(
            ["configure", "--comfy-url", "http://localhost:8999/", "--engines-root", str(tmp_path)]
        )
        == 0
    )
    assert read_setup_file()["comfy_url"] == "http://localhost:8999"
    assert cli.main(["configure", "--comfy-url", "file:///invalid"]) == 1

    def absent(name):
        raise ModuleNotFoundError(name=name)

    monkeypatch.setattr(cli.importlib, "import_module", absent)
    assert cli.main(["engines", "install"]) == 1
    assert "not available in this build" in capsys.readouterr().out


@pytest.mark.skipif(os.name != "nt", reason="Windows portable profile")
def test_portable_install_detects_embedded_python_and_is_repeatable(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIAVFX_SETUP_FILE", str(tmp_path / "setup.json"))
    destination = tmp_path / "install"
    comfy = destination / "ComfyUI_windows_portable/ComfyUI"
    calls = []
    monkeypatch.setattr(comfy_install, "download", lambda *a, **kw: calls.append((a, kw)))

    def extract(args):
        calls.append(args)
        comfy.mkdir(parents=True)
        (comfy / "main.py").touch()
        interpreter = comfy.parent / "python_embeded/python.exe"
        interpreter.parent.mkdir()
        interpreter.touch()

    monkeypatch.setattr(comfy_install, "run", extract)
    args = argparse.Namespace(directory=destination, version=None, git=False, cuda=None)
    assert comfy_install.install_comfy(args) == 0
    assert calls[0][1]["sha256"] == load_manifest()["comfyui"]["windows_portable_sha256"]
    assert calls[1][0:2] == ["tar.exe", "-xf"]
    assert "python_embeded" in comfy_install.start_command(comfy)
    assert read_setup_file()["comfy_dir"] == str(comfy)
    assert comfy_install.install_comfy(args) == 0
    assert len(calls) == 2
    from gaiavfx.setup.manifest import comfy_directory

    assert comfy_directory(destination) == comfy
    assert comfy_directory(comfy.parent) == comfy
    assert comfy_directory(comfy) == comfy


@pytest.mark.skipif(os.name != "nt", reason="Windows portable profile")
def test_portable_extraction_failure_prints_recovery(tmp_path, monkeypatch):
    monkeypatch.setattr(comfy_install, "download", lambda *_a, **_kw: None)

    def broken(args):
        raise subprocess.CalledProcessError(1, args)

    monkeypatch.setattr(comfy_install, "run", broken)
    args = argparse.Namespace(directory=tmp_path / "install", version=None, git=False, cuda=None)
    with pytest.raises(ValueError, match="7-Zip.*--git"):
        comfy_install.install_comfy(args)
