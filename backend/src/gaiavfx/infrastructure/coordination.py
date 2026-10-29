"""Coordinate GAIAVFX-owned heavy work across local app instances."""

import hashlib
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from filelock import FileLock, Timeout

from gaiavfx.shared.errors import ApplicationError


def gpu_lock_path(endpoint):
    normalized = endpoint.rstrip("/").replace("localhost", "127.0.0.1")
    return Path(
        os.environ.get("GAIAVFX_GPU_LOCK")
        or (
            Path(tempfile.gettempdir())
            / ("gaiavfx-gpu-" + hashlib.sha256(normalized.encode()).hexdigest()[:20] + ".lock")
        )
    )


@contextmanager
def gpu_lease(endpoint, cancelled, waiting=lambda: None):
    lock = FileLock(str(gpu_lock_path(endpoint)))
    while True:
        if cancelled():
            raise ApplicationError("This GPU task was cancelled before starting.")
        try:
            lock.acquire(timeout=0.5)
            break
        except Timeout:
            waiting()
    try:
        yield
    finally:
        lock.release()
