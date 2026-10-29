"""Prerequisite selection and discovery without modifying installed system tools."""

import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlparse

import pytest

from gaiavfx.setup import _runtime, engines, nodes

ROOT = Path(__file__).resolve().parents[2]
MANIFEST = json.loads((ROOT / "scripts/prerequisites.json").read_text())
POWERSHELL = shutil.which("powershell.exe")
BASH = shutil.which("bash")
requires_powershell = pytest.mark.skipif(not POWERSHELL, reason="Windows PowerShell unavailable")


def ps(*args):
    return subprocess.run(
        [POWERSHELL, "-NoProfile", "-ExecutionPolicy", "Bypass", *args],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
        timeout=30,
    )


def plan(winget, *args):
    result = ps(
        "-File",
        "scripts/prerequisites.ps1",
        "-Plan",
        "-SimulateMissing",
        "git,uv,node,npm",
        "-SimulateWinget",
        winget,
        "-NonInteractive",
        *args,
    )
    assert result.stderr == ""
    return result.returncode, json.loads(result.stdout)


@requires_powershell
@pytest.mark.parametrize("winget", ["present", "registers"])
def test_winget_plans(winget):
    code, result = plan(winget, "-InstallPrerequisites")
    assert code == result["exit_code"] == 0
    steps = result["steps"]
    if winget == "registers":
        assert steps.pop(0) == {
            "method": "register",
            "command": [
                "Add-AppxPackage",
                "-RegisterByFamilyName",
                "-MainPackage",
                "Microsoft.DesktopAppInstaller_8wekyb3d8bbwe",
            ],
        }
    assert len(steps) == 3  # npm belongs to the Node package, never a second installation.
    for step, package in zip(steps, ["Git.Git", "astral-sh.uv", "OpenJS.NodeJS.LTS"], strict=True):
        assert step["method"] == "winget"
        assert step["command"] == [
            "winget",
            "install",
            "--id",
            package,
            "-e",
            "--source",
            "winget",
            "--accept-package-agreements",
            "--accept-source-agreements",
        ]
        assert step["fallback"]["url"] == MANIFEST["windows"][step["tool"]]["url"]


@requires_powershell
@pytest.mark.parametrize("force", [False, True])
def test_portable_plan_matches_pins(force):
    code, result = plan("absent", "-ForcePortable" if force else "-InstallPrerequisites")
    assert code == 0
    steps = result["steps"]
    if not force:
        assert steps.pop(0)["method"] == "register"
    assert len(steps) == 3
    for step in steps:
        assert step["method"] == "portable"
        spec = MANIFEST["windows"][step["tool"]]
        for key in ("url", "bytes", "sha256", "version"):
            assert step[key] == spec[key]


@requires_powershell
def test_noninteractive_requires_consent():
    code, result = plan("present")
    assert code == result["exit_code"] == 1
    assert result["steps"] == []
    assert result["consent"] == "required"
    assert "https://aka.ms/getwinget" in "\n".join(result["hints"])


def test_manifest_official_pins():
    assert MANIFEST["schema"] == 1
    assert set(MANIFEST["windows"]) == {"git", "uv", "node"}
    assert set(MANIFEST["linux"]) == {"uv", "node"}
    for system in ("windows", "linux"):
        for tool, spec in MANIFEST[system].items():
            parsed = urlparse(spec["url"])
            assert parsed.scheme == "https"
            assert parsed.hostname == ("nodejs.org" if tool == "node" else "github.com")
            if tool != "node":
                assert parsed.path.startswith(
                    "/git-for-windows/git/releases/download/"
                    if tool == "git"
                    else "/astral-sh/uv/releases/download/"
                )
            assert re.fullmatch(r"[a-f0-9]{64}", spec["sha256"])
            assert spec["bytes"] > 0
            assert spec["version"]
            if tool == "node":
                assert spec["version"].startswith("v24.")
                assert spec["checksum_url"].endswith("/SHASUMS256.txt")


@pytest.mark.parametrize("name", ["git", "uv", "node", "npm"])
def test_resolve_path_first_then_portable(tmp_path, monkeypatch, name):
    monkeypatch.setattr(_runtime, "repo_root", lambda: tmp_path)
    relative = {
        "git": "git/cmd/git.exe" if os.name == "nt" else "git/bin/git",
        "uv": "uv/uv.exe" if os.name == "nt" else "uv/uv",
        "node": "node/node.exe" if os.name == "nt" else "node/bin/node",
        "npm": "node/npm.cmd" if os.name == "nt" else "node/bin/npm",
    }[name]
    portable = tmp_path / ".runtime/tools" / relative
    portable.parent.mkdir(parents=True)
    portable.touch(mode=0o755)
    monkeypatch.setattr(_runtime.shutil, "which", lambda _: "from-path")
    assert _runtime.resolve_tool(name) == "from-path"
    monkeypatch.setattr(_runtime.shutil, "which", lambda _: None)
    assert _runtime.resolve_tool(name) == str(portable)
    portable.unlink()
    with pytest.raises(FileNotFoundError, match=f"Required tool '{name}'.*InstallPrerequisites"):
        _runtime.resolve_tool(name)


def test_nodes_and_comfy_runner_resolves_git_and_uv(monkeypatch):
    calls = []
    monkeypatch.setattr(nodes, "resolve_tool", lambda tool: "portable/" + tool)
    monkeypatch.setattr(nodes.subprocess, "run", lambda command, **kwargs: calls.append(command))
    nodes.run(["git", "--version"])
    nodes.run(["uv", "--version"])
    nodes.run(["python", "--version"])
    assert calls == [
        ["portable/git", "--version"],
        ["portable/uv", "--version"],
        ["python", "--version"],
    ]
    from gaiavfx.setup import comfy_install

    assert comfy_install.run is nodes.run


def test_engine_execution_resolves_uv(tmp_path, monkeypatch):
    _runtime.host_os()  # Prime platform discovery before mocking subprocess globally.
    monkeypatch.setattr(engines, "resolve_tool", lambda tool: "portable/" + tool)
    calls = []

    def stop_after_provision(command, **kwargs):
        calls.append(command)
        if len(calls) == 4:
            raise RuntimeError("stop before probe")

    monkeypatch.setattr(engines.subprocess, "run", stop_after_provision)
    with pytest.raises(RuntimeError, match="stop before probe"):
        engines.install(tmp_path / "engine")
    assert all(command[0] == "portable/uv" for command in calls)


@requires_powershell
def test_powershell_parse():
    result = ps(
        "-Command",
        "$failed = $false; foreach ($file in @('install.ps1', 'start.ps1', 'scripts/prerequisites.ps1')) { "
        "$tokens = $null; $errors = $null; "
        "[System.Management.Automation.Language.Parser]::ParseFile((Join-Path $PWD $file), "
        "[ref]$tokens, [ref]$errors) | Out-Null; "
        "if ($errors) { $errors | Out-String | Write-Output; $failed = $true } }; "
        "if ($failed) { exit 1 }",
    )
    assert result.returncode == 0, result.stdout + result.stderr


@pytest.mark.skipif(not BASH, reason="Bash unavailable")
def test_bash_parse_and_linux_manifest_reader():
    for script in ("install.sh", "start.sh", "scripts/prerequisites.sh"):
        subprocess.run([BASH, "-n", script], cwd=ROOT, check=True, capture_output=True)
    result = subprocess.run(
        [
            BASH,
            "-c",
            (
                "source scripts/prerequisites.sh; "
                "for tool in uv node; do for field in url bytes sha256 strip_prefix; do "
                'prerequisite_field "$tool" "$field"; done; done'
            ),
        ],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=True,
    )
    assert result.stdout.splitlines() == [
        str(MANIFEST["linux"][tool][field])
        for tool in ("uv", "node")
        for field in ("url", "bytes", "sha256", "strip_prefix")
    ]


@requires_powershell
def test_checksum_failure_deletes_download_without_extracting(tmp_path):
    # Mock only the transport; exercise real size/hash validation and failure cleanup.
    destination = str(tmp_path).replace("'", "''")
    result = ps(
        "-Command",
        ". ./scripts/prerequisites.ps1; "
        "function Invoke-WebRequest { param([switch]$UseBasicParsing, $Uri, $OutFile, $ErrorAction) "
        "[IO.File]::WriteAllText($OutFile, 'corrupt') }; "
        "function Expand-Archive { throw 'EXTRACTION MUST NOT RUN' }; "
        f"try {{ Install-PortablePrerequisite -Tool uv -ToolsRoot '{destination}'; exit 9 }} "
        "catch { Write-Output $_.Exception.Message; exit 1 }",
    )
    assert result.returncode == 1
    assert "SHA-256 or size mismatch" in result.stdout
    assert not list(tmp_path.rglob("*.zip"))
    assert not (tmp_path / "uv").exists()


@requires_powershell
def test_portable_detection_prefers_valid_node_over_old_path(tmp_path):
    # Tiny cmd stubs exercise discovery without invoking installed runtimes.
    tools = tmp_path / "tools"
    for folder, executable, version in (
        ("git/cmd", "git.cmd", "git version fixture"),
        ("uv", "uv.cmd", "uv fixture"),
        ("node", "node.cmd", "24"),
        ("node", "npm.cmd", "11"),
    ):
        path = tools / folder / executable
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(f"@echo {version}\n")
    quoted = str(tools).replace("'", "''")
    old_node = tmp_path / "old-node"
    old_node.mkdir()
    (old_node / "node.cmd").write_text("@echo 22\n")
    quoted_old = str(old_node).replace("'", "''")
    result = ps(
        "-Command",
        f". ./scripts/prerequisites.ps1; $env:Path = '{quoted_old}'; "
        "if ('node' -notin @(Get-MissingPrerequisites)) { exit 5 }; "
        f"Add-PortableToolsPath -ToolsRoot '{quoted}'; "
        "@(Get-MissingPrerequisites) | ConvertTo-Json -Compress",
    )
    assert result.returncode == 0, result.stderr
    assert not result.stdout.strip()


@requires_powershell
def test_real_noninteractive_gate_never_installs(tmp_path):
    quoted = str(tmp_path).replace("'", "''")
    result = ps(
        "-Command",
        ". ./scripts/prerequisites.ps1; "
        "function Get-MissingPrerequisites { 'git'; 'node' }; "
        "function Install-PortablePrerequisite { throw 'MUST NOT DOWNLOAD' }; "
        "function Update-PrerequisitePath { throw 'MUST NOT REGISTER' }; "
        f"if (Ensure-Prerequisites -NonInteractive -ToolsRoot '{quoted}') {{ exit 9 }}; exit 1",
    )
    assert result.returncode == 1
    assert "-InstallPrerequisites" in result.stdout
    assert "MUST NOT" not in result.stdout + result.stderr
    assert list(tmp_path.iterdir()) == []


@pytest.mark.skipif(not BASH, reason="Bash unavailable")
@pytest.mark.parametrize("missing_git", [False, True])
def test_linux_missing_tools_require_consent_or_manual_git(missing_git):
    result = subprocess.run(
        [
            BASH,
            "-c",
            "set -eu; source scripts/prerequisites.sh; "
            "add_portable_tools_path() { :; }; node() { echo 22; }; "
            "install_portable_prerequisite() { echo MUST_NOT_DOWNLOAD; exit 9; }; "
            "command() { if [[ $1 == -v && $2 == git ]]; then return "
            + ("1" if missing_git else "0")
            + "; fi; return 1; }; ensure_prerequisites "
            + ("1" if missing_git else "0")
            + " 1",
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "MUST_NOT_DOWNLOAD" not in result.stdout
    assert "Node.js >=24" in result.stdout
    assert "--install-prerequisites" in result.stdout
    if missing_git:
        assert "Missing prerequisite: git" in result.stdout


@pytest.mark.skipif(not BASH, reason="Bash unavailable")
def test_linux_corrupt_archive_never_extracts(tmp_path):
    result = subprocess.run(
        [
            BASH,
            "-c",
            (
                'set -eu; source scripts/prerequisites.sh; prerequisite_root=$(cd "$1" && pwd); '
                "uname() { if [[ $1 == -s ]]; then echo Linux; else echo x86_64; fi; }; "
                "prerequisite_field() { case $2 in url) echo https://example.test/uv.tar.gz ;; "
                "bytes) echo 7 ;; sha256) printf '%064d\\n' 0 ;; strip_prefix) echo uv ;; esac; }; "
                'curl() { while [[ $1 != --output ]]; do shift; done; printf corrupt > "$2"; }; '
                "tar() { echo MUST_NOT_EXTRACT; exit 9; }; install_portable_prerequisite uv"
            ),
            "test",
            tmp_path.as_posix(),
        ],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "SHA-256 or size mismatch" in result.stderr
    assert "MUST_NOT_EXTRACT" not in result.stdout
    assert not list(tmp_path.rglob("*.tar.gz"))
