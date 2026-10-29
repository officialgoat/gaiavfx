"""The public-repo guard checks real git candidates and never prints secrets."""

import subprocess
from pathlib import Path

import pytest

SCRIPT = Path(__file__).parents[2] / "scripts" / "check_hygiene.py"


@pytest.mark.parametrize(
    "name,content,rule",
    [
        ("source.py", "D" + ":/private/file", "workstation"),
        ("source.py", "Flo" + "oPowder", "old-brand"),
        ("source.py", ".local" + "/cache", "local-path"),
        ("token.txt", "sk" + "-" + "a" * 32, "secret"),
        ("token.txt", "hf" + "_" + "b" * 32, "secret"),
        ("key.pem", "-----BEGIN " + "PRIVATE KEY-----", "secret"),
        ("weights.onnx", "fixture", "artifact"),
        ("preview.png", "fixture", "artifact"),
        ("large.txt", "x" * (2 * 1024 * 1024 + 1), "size"),
    ],
    ids=[
        "workstation",
        "brand",
        "local",
        "api-key",
        "hub-token",
        "private-key",
        "weight",
        "media",
        "size",
    ],
)
def test_rejects_untracked_residue_without_echoing_it(tmp_path, name, content, rule):
    import importlib.util

    spec = importlib.util.spec_from_file_location("hygiene_guard", SCRIPT)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / name).write_text(content)
    findings = guard.scan(tmp_path)
    assert any(f": {rule}:" in finding for finding in findings)
    if rule == "secret":
        assert all(content not in finding for finding in findings)


def test_ignores_local_output_and_accepts_portable_fixture(tmp_path):
    import importlib.util

    spec = importlib.util.spec_from_file_location("hygiene_guard", SCRIPT)
    guard = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(guard)
    subprocess.run(["git", "init", "--quiet", str(tmp_path)], check=True)
    (tmp_path / ".gitignore").write_text("cache/\n")
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "output.mp4").touch()
    fixtures = tmp_path / "tests" / "fixtures"
    fixtures.mkdir(parents=True)
    (fixtures / "image.png").write_bytes(b"\x89PNG\r\n\x1a\n")
    assert guard.scan(tmp_path) == []
