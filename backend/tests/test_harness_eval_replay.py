"""Offline replay works with a copied library and releases its Windows file handle."""

import shutil

import harness_support
from harness_support import kernel as kernel  # noqa: PLC0414


def test_replay_of_copied_session_leaves_database_unchanged(kernel, tmp_path, monkeypatch):
    import harness_eval_replay as replay

    harness_support.run(kernel, harness_support.action("say", text="Ready to plan.", done=False))
    copied = tmp_path / ".cache" / "copy" / "library.sqlite3"
    copied.parent.mkdir(parents=True)
    shutil.copyfile(kernel.db.path, copied)
    monkeypatch.setattr(replay, "ROOT", tmp_path)
    before = copied.read_bytes()
    result = replay.replay(copied)
    assert result["requests"] == 3
    assert 0 < result["max_reserved"] <= 32768
    assert result["windowed_requests"] == 0
    assert copied.read_bytes() == before
    copied.unlink()  # Fails on Windows if the read-only SQLite connection leaked.
