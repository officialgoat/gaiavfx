"""Resumable, verified downloads without loading model files into memory."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import sys
import time
from contextlib import nullcontext
from pathlib import Path
from urllib.parse import urlparse

import httpx


def file_sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def matches(path: Path, size: int, sha256: str = "", *, verify: bool = False) -> bool:
    return (
        path.is_file()
        and (not size or path.stat().st_size == size)
        and (not (verify or not size) or bool(sha256) and file_sha256(path) == sha256)
    )


def download(
    url: str,
    target: Path,
    *,
    sha256: str,
    size: int = 0,
    verify: bool = False,
    client: httpx.Client | None = None,
    progress=print,
) -> Path:
    target = Path(target)
    if not url or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError(f"Manual download required for {target.name}: no verified URL/SHA-256")
    if matches(target, size, sha256, verify=verify):
        progress(f"Already present: {target}")
        return target
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    offset = part.stat().st_size if part.exists() else 0
    if size and offset > size:
        part.unlink()
        offset = 0
    digest = hashlib.sha256()
    if offset:
        with part.open("rb") as stream:
            for chunk in iter(lambda: stream.read(1024 * 1024), b""):
                digest.update(chunk)
    if size and offset == size:
        if digest.hexdigest() != sha256:
            part.unlink()
            raise ValueError(
                f"SHA-256 mismatch for {target.name}; discarded {part.name}. Retry the download."
            )
        part.replace(target)
        return target
    headers = {"Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    if urlparse(url).hostname == "huggingface.co" and (token := os.environ.get("HF_TOKEN")):
        headers["Authorization"] = "Bearer " + token
    started = time.monotonic()
    reported = started
    reported_bucket = offset * 10 // size if size else 0
    terminal = progress is print and sys.stdout.isatty()
    line_width = 0
    received = 0
    try:
        with (
            (
                nullcontext(client) if client else httpx.Client(timeout=60, follow_redirects=True)
            ) as transport,
            transport.stream("GET", url, headers=headers, follow_redirects=True) as response,
        ):
            if response.status_code == 416 and offset:
                if (not size or offset == size) and digest.hexdigest() == sha256:
                    part.replace(target)
                    progress(f"Verified {target.name}: {offset:,} bytes")
                    return target
                part.unlink(missing_ok=True)
                raise ValueError(f"Size or SHA-256 mismatch for {target.name}; retry the download.")
            response.raise_for_status()
            if response.status_code == 206:
                content_range = re.fullmatch(
                    r"bytes (\d+)-(\d+)/(\d+|\*)", response.headers.get("content-range", "")
                )
                if not content_range or int(content_range[1]) != offset:
                    raise ValueError(
                        f"Invalid resume response for {target.name}; kept partial download"
                    )
                if size and content_range[3] != "*" and int(content_range[3]) != size:
                    part.unlink(missing_ok=True)
                    raise ValueError(f"Size mismatch for {target.name}; discarded partial download")
            elif response.status_code == 200:
                offset = 0
                reported_bucket = 0
                digest = hashlib.sha256()
            else:
                raise ValueError(
                    f"Unexpected download response {response.status_code} for {target.name}"
                )
            with part.open("ab" if offset else "wb") as stream:
                for chunk in response.iter_bytes(1024 * 1024):
                    stream.write(chunk)
                    digest.update(chunk)
                    received += len(chunk)
                    now = time.monotonic()
                    bucket = (offset + received) * 10 // size if size else 0
                    due = (
                        now - reported >= 0.25
                        if terminal
                        else bucket > reported_bucket or now - reported >= 30
                    )
                    if due:
                        message = f"{target.name}: {offset + received:,}/{size or '?'} bytes, {received / max(now - started, 0.001):,.0f} bytes/s"
                        if terminal:
                            message = message[: max(1, shutil.get_terminal_size().columns - 1)]
                            line_width = max(line_width, len(message))
                            print(message.ljust(line_width), end="\r", flush=True)
                        else:
                            progress(message)
                        reported = now
                        reported_bucket = bucket
    finally:
        if terminal and line_width:
            print(flush=True)
    if (size and part.stat().st_size != size) or digest.hexdigest() != sha256:
        part.unlink(missing_ok=True)
        raise ValueError(
            f"Size or SHA-256 mismatch for {target.name}; discarded {part.name}. Retry the download."
        )
    part.replace(target)
    progress(
        f"Verified {target.name}: {target.stat().st_size:,} bytes, {received / max(time.monotonic() - started, 0.001):,.0f} bytes/s"
    )
    return target


download_file = download
