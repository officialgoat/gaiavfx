"""ADR 0015: the engine process runner (fake worker under this interpreter, no model, no GPU)."""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from gaiavfx.infrastructure import engines

WORKER = Path(__file__).parent / "fixtures" / "engine_fake_worker.py"
COMMAND = (sys.executable, str(WORKER))


def document(task, **params):
    return {
        "schema": 1,
        "job_id": "job",
        "step": f"fake.{task}",
        "task": task,
        "params": params,
        "inputs": {},
        "seed": None,
    }


def launch(tmp_path, task, *, cancelled=lambda: False, stopping=lambda: False, grace=5.0, **params):
    seen = []
    outputs = engines.run(
        COMMAND,
        tmp_path / "work",
        document(task, **params),
        log=tmp_path / "logs" / "job.log",
        cancelled=cancelled,
        stopping=stopping,
        progress=seen.append,
        grace=grace,
        poll=0.05,
    )
    return outputs, seen


def alive(pid: int) -> bool:
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        return True
    import ctypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.restype = ctypes.c_void_p
    handle = kernel32.OpenProcess(0x00100000 | 0x1000, False, pid)  # SYNCHRONIZE | QUERY_LIMITED
    if not handle:
        return False
    try:
        return kernel32.WaitForSingleObject(ctypes.c_void_p(handle), 0) == 0x102  # WAIT_TIMEOUT
    finally:
        kernel32.CloseHandle(ctypes.c_void_p(handle))


def gone(pid: int, seconds: float = 10.0) -> bool:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        if not alive(pid):
            return True
        time.sleep(0.1)
    return False


def test_progress_and_outputs_follow_the_protocol(tmp_path):
    outputs, seen = launch(tmp_path, "echo")
    assert [o["key"] for o in outputs] == ["line"]
    assert outputs[0]["path"] == "outputs/line.wav"  # relative: the work directory is portable
    assert outputs[0]["kind"] == "audio" and outputs[0]["info"] == {"seconds": 0.25}
    assert (tmp_path / "work" / outputs[0]["path"]).is_file()
    assert [round(entry["progress"], 2) for entry in seen] == [0.5, 1.0]
    assert "launch" in (tmp_path / "logs" / "job.log").read_text(encoding="utf-8")


def test_cancel_stops_the_worker_and_its_children(tmp_path):
    child = tmp_path / "work" / "child.pid"
    with pytest.raises(engines.EngineCancelled):
        launch(tmp_path, "slow", spawn=True, cancelled=child.exists)
    worker = int((tmp_path / "work" / "worker.pid").read_text())
    grandchild = int(child.read_text())
    assert gone(worker)
    assert gone(grandchild), "the worker's own child process outlived the cancelled task"


def test_a_worker_ignoring_cancel_is_killed_after_the_grace_period(tmp_path):
    pid_file = tmp_path / "work" / "worker.pid"
    started = time.monotonic()
    with pytest.raises(engines.EngineCancelled):
        launch(tmp_path, "slow", ignore_cancel=True, cancelled=pid_file.exists, grace=0.5)
    assert time.monotonic() - started < 30
    assert gone(int(pid_file.read_text()))


def test_a_crash_reports_the_log_tail(tmp_path):
    with pytest.raises(engines.EngineFailed) as failure:
        launch(tmp_path, "crash")
    assert "boom: the model file is missing" in str(failure.value)
    assert "exit 1" in str(failure.value)


def test_unknown_task_fails(tmp_path):
    with pytest.raises(engines.EngineFailed, match="exit 2"):
        launch(tmp_path, "nothing")


def test_stopping_kills_at_once_and_a_relaunch_skips_finished_outputs(tmp_path):
    first = tmp_path / "work" / "outputs" / "part0.wav"
    with pytest.raises(engines.EngineStopped):
        launch(tmp_path, "chunks", n=3, hang_at=1, stopping=first.exists)
    assert first.is_file() and not (tmp_path / "work" / "outputs" / "part1.wav").exists()
    outputs, _ = launch(tmp_path, "chunks", n=3, hang_at=1)
    assert [o["key"] for o in outputs] == ["part0", "part1", "part2"]
    assert "skipped 1" in (tmp_path / "logs" / "job.log").read_text(encoding="utf-8")


def test_an_output_outside_the_work_directory_is_refused(tmp_path):
    with pytest.raises(engines.EngineFailed, match="not a file in the work directory"):
        launch(tmp_path, "escape")


def test_progress_lines_written_in_pieces_are_read_whole(tmp_path):
    path = tmp_path / "progress.jsonl"
    seen = []
    path.write_text('{"progress": 0.25}\n{"progr', encoding="utf-8")
    offset = engines._read_progress(path, 0, seen.append)
    assert seen == [{"progress": 0.25}]
    with path.open("a", encoding="utf-8") as stream:
        stream.write('ess": 0.5}\n')
    engines._read_progress(path, offset, seen.append)
    assert seen == [{"progress": 0.25}, {"progress": 0.5}]
