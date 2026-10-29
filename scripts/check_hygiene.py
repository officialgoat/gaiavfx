"""Reject private residue and generated artifacts in files eligible for git."""

from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

MAX_BYTES = 2 * 1024 * 1024
WEIGHTS = {".safetensors", ".ckpt", ".gguf", ".onnx", ".pt", ".pth", ".bin"}
MEDIA = {
    ".jpg", ".jpeg", ".png", ".gif", ".webp", ".ico", ".avif", ".bmp", ".tif", ".tiff", ".svg",
    ".mp4", ".mov", ".mkv", ".webm", ".avi", ".wav", ".mp3", ".flac", ".ogg", ".m4a",
    ".npz", ".npy", ".ply", ".splat",
}
PATTERNS = {
    "workstation": re.compile(
        r"aslgr|(?<![A-Za-z\\])(?:D:[\\/]|G:/Comfy|G:\\|F:/|C:/Users/)|/d/Repositories", re.I
    ),
    "old-brand": re.compile(r"FlooPowder|floopowder|FLOO_|\bFloo\b", re.I),
    "local-path": re.compile(r"\.local[/\\]"),
    "secret": re.compile(
        r"\bsk-[A-Za-z0-9_-]{20,}|\bhf_[A-Za-z0-9]{20,}|"
        r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY-----"
    ),
}
# Each exception grants one rule to one file, with a reviewable reason.
ALLOWLIST = {
    ("frontend/public/brand/favicon.svg", "artifact"): "Approved brand favicon copied for Vite.",
    ("frontend/public/brand/favicon.ico", "artifact"): "Approved brand icon for browser and desktop.",
    ("frontend/public/brand/symbol.svg", "artifact"): "Approved currentColor symbol used in both themes.",
    ("frontend/public/brand/app-icon.png", "artifact"): "Approved desktop window icon.",
    ("scripts/check_hygiene.py", "workstation"): "Literal signatures for the scanner itself.",
    ("scripts/check_hygiene.py", "old-brand"): "Literal signatures for the scanner itself.",
    (".gitignore", "local-path"): "Explicitly exclude this legacy cache directory.",
    ("backend/tests/fixtures/harness/Walker.jpg", "artifact"): "Small portable agent fixture.",
    ("backend/tests/fixtures/harness/take.mp4", "artifact"): "Synthetic pan used by CPU agent tests.",
}
CODE_SUFFIXES = {".py", ".ts", ".tsx", ".js", ".mjs", ".cjs", ".mts", ".ps1", ".toml", ".json"}


def tracked_candidates(root: Path) -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-co", "--exclude-standard", "-z"],
        cwd=root, check=True, capture_output=True,
    )
    return [Path(name.decode("utf-8")) for name in sorted(set(result.stdout.split(b"\0"))) if name]


def scan(root: Path) -> list[str]:
    failures = []
    for relative in tracked_candidates(root):
        name = relative.as_posix()
        path = root / relative
        if not path.exists():
            continue  # An unstaged deletion will not be part of the next commit.

        def fail(rule: str, detail: str):
            if (name, rule) not in ALLOWLIST:
                failures.append(f"{name}: {rule}: {detail}")

        if path.is_symlink():
            fail("symlink", "source artifacts must be regular files")
            continue
        if path.stat().st_size > MAX_BYTES:
            fail("size", "file exceeds 2 MiB")
        if path.suffix.lower() in WEIGHTS | MEDIA and not name.startswith(("assets/brand/", "tests/fixtures/", "docs/images/")):
            fail("artifact", "weights and media belong in approved fixture or brand folders")
        try:
            text = path.read_text(encoding="utf-8-sig")
        except UnicodeError:
            continue
        for rule, pattern in PATTERNS.items():
            if rule == "local-path" and path.suffix.lower() not in CODE_SUFFIXES:
                continue
            for number, line in enumerate(text.splitlines(), 1):
                if pattern.search(line):
                    # Do not echo matched values: a rejected file may contain a real secret.
                    fail(rule, f"line {number}")
    return failures


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    args = parser.parse_args(argv)
    failures = scan(args.root.resolve())
    if failures:
        print("\n".join(failures))
        print(f"Hygiene failed: {len(failures)} finding(s).")
        return 1
    print("Hygiene passed: all git-eligible files checked.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
