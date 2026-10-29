"""Run local engine workers as child processes (ADR 0015).

A worker runs one task in its work directory (`engine_workers/protocol.py` defines the files). It lives in a Windows
Job Object that kills its whole process tree when the job handle closes, so no worker or grandchild (Applio starts
its own stage processes) outlives its task, the app, or a cancellation. A task is never reattached: after a restart
it is relaunched in the same work directory and the worker skips what it already finished.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from gaiavfx.shared.errors import ApplicationError

CANCELLED_EXIT = 3


class EngineFailed(ApplicationError):
    """The worker exited with an error, or its result did not follow the protocol."""


class EngineCancelled(ApplicationError):
    """The task was cancelled by its operation."""


class EngineStopped(Exception):
    """The app is stopping; the worker was killed and the task stays relaunchable."""


def engines_root(configured: str | None = None) -> Path | None:
    """Where engine homes are installed: GAIAVFX_ENGINES_ROOT, else the library's setting, else none."""
    value = os.environ.get("GAIAVFX_ENGINES_ROOT") or configured
    return Path(value) if value else None


def workers_dir() -> Path:
    """`engine_workers/` of the checkout (a packaged build sets GAIAVFX_ENGINE_WORKERS)."""
    value = os.environ.get("GAIAVFX_ENGINE_WORKERS")
    return Path(value) if value else Path(__file__).resolve().parents[4] / "engine_workers"


class _ProcessTree:
    """The worker and everything it starts: a kill-on-close Job Object on Windows, a session elsewhere."""

    def __init__(self, process: subprocess.Popen):
        self.process = process
        self.handle = None
        if os.name == "nt":
            self.handle = _job_object()
            if not _kernel32().AssignProcessToJobObject(self.handle, int(process._handle)):
                _kernel32().CloseHandle(self.handle)
                self.handle = None
                process.kill()
                raise EngineFailed("Could not place the engine worker in a job object.")

    def kill(self) -> None:
        if self.handle is not None:
            _kernel32().TerminateJobObject(self.handle, 1)
        elif self.process.poll() is None:
            os.killpg(self.process.pid, signal.SIGKILL)

    def close(self) -> None:
        if self.handle is not None:
            _kernel32().CloseHandle(self.handle)  # kill-on-close ends anything left
            self.handle = None
        elif self.process.poll() is None:
            self.kill()


def run(
    command: Sequence[str],
    work: Path,
    document: dict,
    *,
    log: Path,
    cancelled: Callable[[], bool],
    stopping: Callable[[], bool],
    progress: Callable[[dict], None],
    grace: float = 10.0,
    poll: float = 0.2,
    env: dict | None = None,
) -> list[dict]:
    """Launch the worker on `document` in `work` and wait for it.

    Returns the validated outputs of `result.json` ({key, path relative to `work`, kind, info}). Raises
    EngineCancelled (the cancel file was written and the worker stopped, or was killed after `grace` seconds),
    EngineStopped (`stopping()` turned true: the tree was killed at once) or EngineFailed (non-zero exit, with the
    log's tail, or a malformed result).
    """
    work.mkdir(parents=True, exist_ok=True)
    log.parent.mkdir(parents=True, exist_ok=True)
    job_file = work / "job.json"
    temporary = work / "job.json.partial"
    temporary.write_text(json.dumps(document, allow_nan=False), encoding="utf-8")
    os.replace(temporary, job_file)
    for stale in ("cancel", "result.json"):
        (work / stale).unlink(missing_ok=True)
    progress_file = work / "progress.jsonl"
    offset = progress_file.stat().st_size if progress_file.exists() else 0
    cancel_at = None
    with log.open("ab") as stream:
        stream.write(
            f"\n--- launch {time.strftime('%Y-%m-%d %H:%M:%S')}: {' '.join(command)}\n".encode()
        )
        stream.flush()
        process = subprocess.Popen(
            [*command, "--job", str(job_file)],
            cwd=work,
            stdin=subprocess.DEVNULL,
            stdout=stream,
            stderr=subprocess.STDOUT,
            env=env,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            start_new_session=os.name != "nt",
        )
        tree = _ProcessTree(process)
        try:
            while process.poll() is None:
                offset = _read_progress(progress_file, offset, progress)
                if stopping():
                    tree.kill()
                    process.wait()
                    raise EngineStopped
                if cancel_at is None and cancelled():
                    (work / "cancel").write_text("cancel", encoding="utf-8")
                    cancel_at = time.monotonic()
                if cancel_at is not None and time.monotonic() - cancel_at > grace:
                    tree.kill()
                time.sleep(poll)
            offset = _read_progress(progress_file, offset, progress)
        finally:
            tree.close()
    if cancel_at is not None or process.returncode == CANCELLED_EXIT:
        raise EngineCancelled("The engine task was cancelled.")
    if process.returncode != 0:
        raise EngineFailed(
            f"Engine task {document.get('step', '')} failed (exit {process.returncode}):\n{tail(log)}"
        )
    return _result(work)


def _read_progress(path: Path, offset: int, progress: Callable[[dict], None]) -> int:
    if not path.exists():
        return offset
    with path.open("rb") as stream:
        stream.seek(offset)
        data = stream.read()
    complete = data[: data.rfind(b"\n") + 1]  # a line still being written waits for the next read
    for line in complete.splitlines():
        try:
            entry = json.loads(line)
        except ValueError:
            continue
        if isinstance(entry, dict) and isinstance(entry.get("progress"), (int, float)):
            progress(entry)
    return offset + len(complete)


def _result(work: Path) -> list[dict]:
    path = work / "result.json"
    try:
        document = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise EngineFailed(f"The engine worker wrote no readable result: {exc}") from exc
    outputs = document.get("outputs") if isinstance(document, dict) else None
    if document.get("schema") != 1 or not isinstance(outputs, list):
        raise EngineFailed("The engine result does not follow schema 1.")
    root, keys = work.resolve(), set()
    for item in outputs:
        if not isinstance(item, dict) or not all(
            isinstance(item.get(field), str) and item[field] for field in ("key", "path", "kind")
        ):
            raise EngineFailed("Every engine output needs a key, a path and a kind.")
        target = (root / item["path"]).resolve()
        if not target.is_relative_to(root) or not target.is_file():
            raise EngineFailed(
                f"Engine output {item['key']!r} is not a file in the work directory."
            )
        if item["key"] in keys:
            raise EngineFailed(f"Engine output {item['key']!r} appears twice.")
        keys.add(item["key"])
        item.setdefault("info", {})
    return outputs


def tail(log: Path, lines: int = 20) -> str:
    try:
        text = log.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return "(no log)"
    return "\n".join(text.splitlines()[-lines:])


def free_vram_gb() -> float | None:
    """Free memory of the first GPU from nvidia-smi, or None when it cannot be read."""
    try:
        out = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.free", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
            check=False,
        ).stdout
        return float(out.strip().splitlines()[0]) / 1024
    except (OSError, ValueError, IndexError, subprocess.SubprocessError):
        return None


def _kernel32():
    import ctypes
    from ctypes import wintypes

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.CreateJobObjectW.restype = wintypes.HANDLE
    kernel32.CreateJobObjectW.argtypes = (wintypes.LPVOID, wintypes.LPCWSTR)
    kernel32.SetInformationJobObject.argtypes = (
        wintypes.HANDLE,
        ctypes.c_int,
        wintypes.LPVOID,
        wintypes.DWORD,
    )
    kernel32.AssignProcessToJobObject.argtypes = (wintypes.HANDLE, wintypes.HANDLE)
    kernel32.TerminateJobObject.argtypes = (wintypes.HANDLE, wintypes.UINT)
    kernel32.CloseHandle.argtypes = (wintypes.HANDLE,)
    return kernel32


def _job_object():
    import ctypes
    from ctypes import wintypes

    class IoCounters(ctypes.Structure):
        _fields_ = [
            (name, ctypes.c_ulonglong)
            for name in (
                "read_ops",
                "write_ops",
                "other_ops",
                "read_bytes",
                "write_bytes",
                "other_bytes",
            )
        ]

    class BasicLimits(ctypes.Structure):
        _fields_ = [
            ("per_process_user_time", wintypes.LARGE_INTEGER),
            ("per_job_user_time", wintypes.LARGE_INTEGER),
            ("limit_flags", wintypes.DWORD),
            ("minimum_working_set", ctypes.c_size_t),
            ("maximum_working_set", ctypes.c_size_t),
            ("active_process_limit", wintypes.DWORD),
            ("affinity", ctypes.c_size_t),
            ("priority_class", wintypes.DWORD),
            ("scheduling_class", wintypes.DWORD),
        ]

    class ExtendedLimits(ctypes.Structure):
        _fields_ = [
            ("basic", BasicLimits),
            ("io", IoCounters),
            ("process_memory_limit", ctypes.c_size_t),
            ("job_memory_limit", ctypes.c_size_t),
            ("peak_process_memory", ctypes.c_size_t),
            ("peak_job_memory", ctypes.c_size_t),
        ]

    kernel32 = _kernel32()
    handle = kernel32.CreateJobObjectW(None, None)
    if not handle:
        raise EngineFailed(f"Could not create a job object (error {ctypes.get_last_error()}).")
    limits = ExtendedLimits()
    limits.basic.limit_flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
    if not kernel32.SetInformationJobObject(handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
        kernel32.CloseHandle(handle)
        raise EngineFailed(f"Could not configure the job object (error {ctypes.get_last_error()}).")
    return handle
