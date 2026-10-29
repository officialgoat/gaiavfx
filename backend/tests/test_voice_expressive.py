"""ADR 0016 P5: the expressive recordings library is listed, never read, and its digest follows its recordings."""

import os

import pytest

from gaiavfx.application import engine_jobs
from gaiavfx.integrations.voice import expressive


def write(path, size=16):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"\0" * size)
    return path


@pytest.fixture
def library(tmp_path):
    root = tmp_path / "expressive"
    for relative in (
        "screams/a.wav",
        "screams/alex/b.flac",
        "sobs/c.mp3",
        "music/d.wav",
        "e.wav",
        "screams/alex/deep/f.wav",
    ):
        write(root / relative)
    # Ignored: not audio, hidden names (an AppleDouble copy, a hidden folder).
    write(root / "screams" / "notes.txt")
    write(root / "screams" / "._a.wav")
    write(root / ".hidden" / "g.wav")
    return root


def test_the_scan_lists_categories_speakers_and_skips_with_reasons(library):
    scan = expressive.scan(str(library))
    assert scan["state"] == "ready" and scan["folder"] == str(library)
    assert [(c["id"], c["relative"], c["category"], c["speaker"]) for c in scan["clips"]] == [
        ("lib0001", "screams/a.wav", "scream", None),
        ("lib0002", "screams/alex/b.flac", "scream", "alex"),
        ("lib0003", "sobs/c.mp3", "sob", None),
    ]
    assert all(c["bytes"] == 16 and c["mtime_ns"] > 0 for c in scan["clips"])
    assert {row["relative"]: row["reason"] for row in scan["skipped"]} == {
        "music/d.wav": "unknown_category",
        "e.wav": "root_file",
        "screams/alex/deep/f.wav": "too_deep",
    }
    assert scan["categories"] == {"scream": 2, "sob": 1}
    assert len(scan["digest"]) == 64 and int(scan["digest"], 16) >= 0


def test_the_digest_holds_for_an_unchanged_folder_and_follows_a_rewrite(library):
    first = expressive.scan(str(library))
    assert expressive.scan(str(library)) == first, "two scans of an unchanged folder agree"
    write(library / "sobs" / "c.mp3", size=32)
    second = expressive.scan(str(library))
    assert second["digest"] != first["digest"]
    assert [c["bytes"] for c in second["clips"] if c["relative"] == "sobs/c.mp3"] == [32]
    stat = (library / "sobs" / "c.mp3").stat()
    os.utime(library / "sobs" / "c.mp3", ns=(stat.st_atime_ns, stat.st_mtime_ns + 10**9))
    assert expressive.scan(str(library))["digest"] != second["digest"], "a newer recording counts"


def test_an_unset_or_missing_folder_has_no_clips_and_no_digest(tmp_path):
    for value in ("", "   "):
        scan = expressive.scan(value)
        assert (scan["state"], scan["clips"], scan["digest"]) == ("not_set", [], "")
    for value in (str(tmp_path / "nowhere"), "relative/folder"):
        scan = expressive.scan(value)
        assert (scan["state"], scan["clips"], scan["digest"]) == ("missing", [], "")
    empty = tmp_path / "empty"
    empty.mkdir()
    scan = expressive.scan(str(empty))
    assert scan["state"] == "ready" and scan["clips"] == [] and len(scan["digest"]) == 64


def test_settings_see_counts_never_the_clip_list(library, tmp_path):
    assert engine_jobs.voice_library(str(library)) == {
        "folder": str(library),
        "state": "ready",
        "clips": 3,
        "categories": {"scream": 2, "sob": 1},
        "skipped": 3,
    }
    assert engine_jobs.voice_library("") == {
        "folder": "",
        "state": "not_set",
        "clips": 0,
        "categories": {},
        "skipped": 0,
    }
    assert engine_jobs.voice_library(str(tmp_path / "gone"))["state"] == "missing"
