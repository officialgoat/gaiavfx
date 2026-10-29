"""Temporary resumable SHA-256 downloader (deduplicate with Brief B at merge)."""

import hashlib
import re
from pathlib import Path

import httpx


def verify(path: Path, sha256: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("A resolved SHA-256 is required before downloading.")
    with path.open("rb") as stream:
        actual = hashlib.file_digest(stream, "sha256").hexdigest()
    if actual != sha256:
        raise ValueError(f"SHA-256 mismatch for {path.name}: expected {sha256}, got {actual}")


def download(url: str, destination: Path, sha256: str) -> Path:
    try:
        from gaiavfx.setup.download import download as shared_download
    except ModuleNotFoundError as exc:
        if exc.name != "gaiavfx.setup.download":
            raise
    else:
        return shared_download(url, destination, sha256=sha256, verify=True)
    if not url.startswith("https://") or not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise ValueError("Runtime entry is unresolved: HTTPS URL and SHA-256 are required.")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.is_file():
        verify(destination, sha256)
        return destination
    partial = destination.with_name(destination.name + ".partial")
    offset = partial.stat().st_size if partial.exists() else 0
    headers = {"Accept-Encoding": "identity"}
    if offset:
        headers["Range"] = f"bytes={offset}-"
    print(f"Downloading {destination.name}" + (f" (resuming at {offset} bytes)" if offset else ""))
    with httpx.stream("GET", url, headers=headers, follow_redirects=True, timeout=120) as response:
        if response.status_code == 416 and offset:
            try:
                verify(partial, sha256)
            except ValueError:
                partial.unlink(missing_ok=True)
                raise
            partial.replace(destination)
            return destination
        response.raise_for_status()
        if response.status_code == 206:
            if not response.headers.get("Content-Range", "").startswith(f"bytes {offset}-"):
                raise ValueError("Download server returned the wrong byte range.")
        else:
            offset = 0
        with partial.open("ab" if offset else "wb") as stream:
            for chunk in response.iter_bytes(1024 * 1024):
                stream.write(chunk)
    try:
        verify(partial, sha256)
    except ValueError:
        partial.unlink(missing_ok=True)
        raise
    partial.replace(destination)
    return destination
