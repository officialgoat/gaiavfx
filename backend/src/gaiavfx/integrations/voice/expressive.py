"""The expressive recordings library (ADR 0016 §4, P5): real screams, sobs, laughs ... of other speakers that the voice
build converts into each target voice. It is a folder chosen in Settings, one per library: a subfolder per category
(singular or plural, any case: `screams/`, `Sob/`), optionally a subfolder per speaker below it (`screams/alex/`).

`scan` lists it without reading audio. Audio files directly in the folder, below a speaker folder or in a folder that
names no recipe category are skipped with a reason; files whose extension is not one of the recipe's
`library_extensions` and hidden names (a leading dot: system files, AppleDouble `._` copies) are ignored. Clip ids
(`lib0001` ...) follow the sorted relative paths, and the digest covers each clip's relative path, size and
modification time, so it changes when the folder's recordings change. The folder path never leaves the scan's caller:
a voice build records the digest and counts, and its library step records the folder it scanned."""

import hashlib
import os
from pathlib import Path

from gaiavfx.integrations.voice import spec


def _entries(folder: str) -> list[os.DirEntry]:
    """The folder's visible entries by name."""
    with os.scandir(folder) as found:
        return sorted((e for e in found if not e.name.startswith(".")), key=lambda e: e.name)


def _audio(entry: os.DirEntry, extensions: frozenset) -> bool:
    return entry.is_file() and Path(entry.name).suffix.lower().lstrip(".") in extensions


def _skip_all(folder: str, relative: str, reason: str, extensions: frozenset, skipped: list):
    """Every audio file at any depth below `folder` is skipped with `reason`; links to folders are not followed."""
    try:
        entries = _entries(folder)
    except OSError:
        skipped.append({"relative": relative, "reason": "unreadable"})
        return
    for entry in entries:
        path = f"{relative}/{entry.name}"
        if entry.is_dir(follow_symlinks=False):
            _skip_all(entry.path, path, reason, extensions, skipped)
        elif _audio(entry, extensions):
            skipped.append({"relative": path, "reason": reason})


def _listed(folder: str, relative: str, skipped: list) -> list[os.DirEntry] | None:
    try:
        return _entries(folder)
    except OSError:
        skipped.append({"relative": relative, "reason": "unreadable"})
        return None


def scan(folder: str) -> dict:
    """{"folder", "state": "not_set" | "missing" | "ready", "clips": [{"id", "relative", "category", "speaker",
    "bytes", "mtime_ns"}], "skipped": [{"relative", "reason"}], "categories": {category: count}, "digest"}. Skip
    reasons: `root_file`, `unknown_category`, `too_deep`, `unreadable`. The digest is "" while the folder is unset or
    missing; a relative folder counts as missing."""
    folder = str(folder or "").strip()
    result = {
        "folder": folder,
        "state": "not_set",
        "clips": [],
        "skipped": [],
        "categories": {},
        "digest": "",
    }
    if not folder:
        return result
    try:
        if not Path(folder).is_absolute() or not Path(folder).is_dir():
            return {**result, "state": "missing"}
        top = _entries(folder)
    except OSError:
        return {**result, "state": "missing"}
    document = spec.load()
    extensions = frozenset(spec.build(document)["library_extensions"])
    found, skipped = [], []
    for entry in top:
        if not entry.is_dir():
            if _audio(entry, extensions):
                skipped.append({"relative": entry.name, "reason": "root_file"})
            continue
        category = spec.library_category(entry.name, document)
        if category is None:
            _skip_all(entry.path, entry.name, "unknown_category", extensions, skipped)
            continue
        for item in _listed(entry.path, entry.name, skipped) or []:
            relative = f"{entry.name}/{item.name}"
            if not item.is_dir():
                if _audio(item, extensions):
                    found.append((relative, category, None, item))
                continue
            for clip in _listed(item.path, relative, skipped) or []:
                path = f"{relative}/{clip.name}"
                if clip.is_dir(follow_symlinks=False):
                    _skip_all(clip.path, path, "too_deep", extensions, skipped)
                elif _audio(clip, extensions):
                    found.append((path, category, item.name, clip))
    found.sort(key=lambda row: row[0])
    clips, categories, digest = [], {}, hashlib.sha256()
    for relative, category, speaker, entry in found:
        try:
            stat = entry.stat()
        except OSError:
            skipped.append({"relative": relative, "reason": "unreadable"})
            continue
        clips.append(
            {
                "id": f"lib{len(clips) + 1:04d}",
                "relative": relative,
                "category": category,
                "speaker": speaker,
                "bytes": stat.st_size,
                "mtime_ns": stat.st_mtime_ns,
            }
        )
        categories[category] = categories.get(category, 0) + 1
        # NUL never occurs in a path, so the three fields of every row stay unambiguous.
        digest.update(f"{relative}\0{stat.st_size}\0{stat.st_mtime_ns}\0".encode())
    return {
        **result,
        "state": "ready",
        "clips": clips,
        "skipped": sorted(skipped, key=lambda row: row["relative"]),
        "categories": dict(sorted(categories.items())),
        "digest": digest.hexdigest(),
    }
