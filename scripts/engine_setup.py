"""Set up the local voice engines outside the app (ADR 0015, P2) by adopting the research environments.

    .venv/Scripts/python.exe scripts/engine_setup.py plan
    .venv/Scripts/python.exe scripts/engine_setup.py adopt [--engine rvc ...] [--hash recorded|full]
    .venv/Scripts/python.exe scripts/engine_setup.py probe [--no-cuda]
    .venv/Scripts/python.exe scripts/engine_setup.py verify
    .venv/Scripts/python.exe scripts/engine_setup.py status
    .venv/Scripts/python.exe scripts/engine_setup.py unlink --engine rvc

An engine home is a directory under the engines root whose children are directory junctions into the research
folders (`.cache/voice-research`). The virtual environments there embed absolute paths, so they are linked, never
copied. Nothing is downloaded or installed: a file the registry needs and the research folders lack is reported with
its source. Recorded hashes come from the research download manifests; `verify` hashes every file again. `unlink`
removes junctions only and never touches what they point to.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

from gaiavfx.integrations.voice import engines as registry

ROOT = Path(__file__).resolve().parents[1]
PROBE = ROOT / "engine_workers" / "probe.py"
# Each engine home's links into the research folders (child name -> folder under --from).
LAYOUT = {
    "analysis": {"env": "envs/eval", "models": "models/eval", "bandit": "repos/bandit"},
    "seedvc": {"env": "envs/seedvc", "models": "models/seedvc", "repo": "repos/seedvc"},
    "rvc": {"env": "envs/rvc", "models": "models/rvc", "repo": "repos/rvc"},
    "tts": {
        "env": "envs/eval",
        "models": "models/audiocpp",
        "bin": "repos/audiocpp/bin",
    },
}
OWN_FILES = ("manifest.json", "probe.json")
OFFLINE = {
    "HF_HUB_OFFLINE": "1",
    "TRANSFORMERS_OFFLINE": "1",
    "PYTHONDONTWRITEBYTECODE": "1",
}


class SetupError(Exception):
    """A refusal: the script changes nothing it cannot account for."""


def now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def same(a: Path, b: Path) -> bool:
    return os.path.normcase(os.path.realpath(a)) == os.path.normcase(
        os.path.realpath(b)
    )


def is_link(path: Path) -> bool:
    return os.path.isjunction(path) or path.is_symlink()


def link(target: Path, path: Path) -> None:
    """A directory junction on Windows (no administrator rights needed), a symlink elsewhere."""
    if os.name == "nt":
        subprocess.run(
            ["cmd", "/c", "mklink", "/J", str(path), str(target)],
            check=True,
            capture_output=True,
            text=True,
        )
    else:
        path.symlink_to(target, target_is_directory=True)


def remove_link(path: Path) -> None:
    if os.path.isjunction(path):
        os.rmdir(path)  # removes the junction itself, never the folder it points to
    elif path.is_symlink():
        path.unlink()
    else:
        raise SetupError(f"{path} is not a link; refusing to remove it.")


def sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def recorded(source: Path) -> dict[str, dict]:
    """The research download manifests, keyed by the normalised real path of each file."""
    found = {}
    for manifest in sorted((source / "downloads").glob("*.json")):
        for entry in json.loads(manifest.read_text(encoding="utf-8")):
            found[os.path.normcase(os.path.realpath(entry["path"]))] = entry
    return found


def research_path(source: Path, engine: str, path: str) -> Path | None:
    """Where a home-relative path lives in the research folders, or None when no link covers it."""
    child, _, rest = path.replace("\\", "/").partition("/")
    target = LAYOUT.get(engine, {}).get(child)
    return source / target / rest if target else None


def entries(names: list[str] | None) -> list[str]:
    chosen = names or sorted(LAYOUT)
    unknown = [name for name in chosen if name not in LAYOUT]
    if unknown:
        raise SetupError(f"No adoption layout for {', '.join(unknown)}.")
    return chosen


def check_files(
    engine, home: Path, source: Path, hashing: str, records: dict
) -> list[dict]:
    files = []
    for model in engine.models if engine else ():
        path = home / model["path"]
        row = {key: model[key] for key in ("path", "source", "licence")}
        if not path.is_file():
            files.append({**row, "state": "missing"})
            continue
        size = path.stat().st_size
        known = records.get(os.path.normcase(os.path.realpath(path)))
        if hashing == "recorded" and known and known.get("bytes") == size:
            digest, how = known["sha256"], "recorded"
        else:
            digest, how = sha256(path), "full"
        state = "ok" if digest == model["sha256"] else "mismatch"
        files.append(
            {**row, "bytes": size, "sha256": digest, "hash": how, "state": state}
        )
    return files


def write_json(path: Path, document: dict) -> None:
    temporary = path.with_name(path.name + ".partial")
    temporary.write_text(
        json.dumps(document, indent=1) + "\n", encoding="utf-8", newline="\n"
    )
    os.replace(temporary, path)


def summary(root: Path) -> dict:
    path = root / "engines.json"
    if path.is_file():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"schema": 1, "engines": {}}


def record(root: Path, name: str, **fields) -> None:
    document = summary(root)
    document["engines"].setdefault(name, {"home": name}).update(fields)
    write_json(root / "engines.json", document)


def plan(
    root: Path, source: Path, names: list[str] | None, text: str | None = None
) -> dict:
    """What adopt would link and which registry files the research folders hold; changes nothing."""
    known = registry.load(text)
    report = {}
    for name in entries(names):
        links = {
            child: {
                "target": str(source / target),
                "exists": (source / target).is_dir(),
            }
            for child, target in LAYOUT[name].items()
        }
        missing = []
        for model in known[name].models if name in known else ():
            found = research_path(source, name, model["path"])
            if found is None or not found.is_file():
                missing.append({"path": model["path"], "source": model["source"]})
        report[name] = {
            "home": str(root / name),
            "registered": name in known,
            "links": links,
            "missing": missing,
        }
    return report


def adopt(
    root: Path,
    source: Path,
    names: list[str] | None,
    hashing: str = "recorded",
    text: str | None = None,
) -> dict:
    known = registry.load(text)
    records = recorded(source)
    report = {}
    for name in entries(names):
        home = root / name
        home.mkdir(parents=True, exist_ok=True)
        linked = {}
        for child, folder in LAYOUT[name].items():
            target, path = source / folder, home / child
            if not target.is_dir():
                raise SetupError(f"{name}: {target} does not exist; nothing to adopt.")
            if path.exists() or is_link(path):
                if not is_link(path):
                    raise SetupError(
                        f"{path} exists and is not a link; refusing to replace it."
                    )
                if not same(path, target):
                    raise SetupError(f"{path} points elsewhere; run unlink first.")
            else:
                link(target, path)
            linked[child] = str(target)
        files = check_files(known.get(name), home, source, hashing, records)
        manifest = {
            "schema": 1,
            "engine": name,
            "registered": name in known,
            "registry": registry.fingerprint(text),
            "adopted_from": linked,
            "files": files,
            "written_at": now(),
        }
        write_json(home / "manifest.json", manifest)
        counts = {
            state: sum(f["state"] == state for f in files)
            for state in ("ok", "missing", "mismatch")
        }
        record(root, name, adopted_at=manifest["written_at"], files=counts)
        report[name] = {"links": linked, "files": counts}
    return report


def probe(
    root: Path,
    names: list[str] | None,
    cuda: bool = True,
    text: str | None = None,
    timeout: float = 600,
) -> dict:
    """Run the probe inside each adopted engine's interpreter, offline and isolated."""
    known = registry.load(text)
    report = {}
    for name in entries(names):
        home = root / name
        if not home.is_dir():
            report[name] = {"state": "not adopted"}
            continue
        environment = (
            known[name].environment
            if name in known
            else "env/Scripts/python.exe"
            if os.name == "nt"
            else "env/bin/python"
        )
        command = [
            str(home / environment),
            "-I",
            str(PROBE),
            *(["--cuda"] if cuda else []),
        ]
        done = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout,
            env={**os.environ, **OFFLINE},
            cwd=home,
        )
        if done.returncode != 0:
            result = {"state": "failed", "stderr": done.stderr[-2000:]}
        else:
            found = json.loads(done.stdout.strip().splitlines()[-1])
            pins = known[name].packages if name in known else {}
            drift = {
                package: {
                    "pinned": version,
                    "installed": found["packages"].get(package.lower()),
                }
                for package, version in pins.items()
                if found["packages"].get(package.lower()) != version
            }
            result = {"state": "ok", "drift": drift, **found}
        result["probed_at"] = now()
        write_json(home / "probe.json", result)
        brief = {
            key: result.get(key)
            for key in ("state", "python", "cuda", "drift", "probed_at")
        }
        record(root, name, probe=brief)
        report[name] = brief
    return report


def verify(
    root: Path, source: Path, names: list[str] | None, text: str | None = None
) -> dict:
    """Hash every registry file of the adopted engines again and update their manifests."""
    known = registry.load(text)
    report = {}
    for name in entries(names):
        home = root / name
        manifest_path = home / "manifest.json"
        if not manifest_path.is_file():
            report[name] = {"state": "not adopted"}
            continue
        files = check_files(known.get(name), home, source, "full", {})
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        manifest.update(files=files, verified_at=now())
        write_json(manifest_path, manifest)
        counts = {
            state: sum(f["state"] == state for f in files)
            for state in ("ok", "missing", "mismatch")
        }
        record(root, name, verified_at=manifest["verified_at"], files=counts)
        report[name] = counts
    return report


def unlink(root: Path, names: list[str]) -> dict:
    """Remove an engine home: its junctions (never their targets) and the script's own files."""
    report = {}
    for name in names:
        home = root / name
        if not home.exists():
            report[name] = "absent"
            continue
        children = list(home.iterdir())
        strangers = [c for c in children if not is_link(c) and c.name not in OWN_FILES]
        if strangers:
            raise SetupError(
                f"{home} holds {', '.join(c.name for c in strangers)}; refusing to remove it."
            )
        for child in children:
            if is_link(child):
                remove_link(child)
            else:
                child.unlink()
        home.rmdir()
        document = summary(root)
        document["engines"].pop(name, None)
        write_json(root / "engines.json", document)
        report[name] = "removed"
    return report


def troubled(report: dict) -> bool:
    """Missing or mismatched files, or a failed probe, in any engine's entry."""
    for entry in report.values():
        if not isinstance(entry, dict):
            continue
        counts = entry.get("files", entry)
        if entry.get("missing") or entry.get("state") == "failed":
            return True
        if isinstance(counts, dict) and (
            counts.get("missing") or counts.get("mismatch")
        ):
            return True
    return False


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument(
        "command", choices=["plan", "adopt", "probe", "verify", "status", "unlink"]
    )
    parser.add_argument("--root", type=Path, default=ROOT / ".runtime" / "engines")
    parser.add_argument(
        "--from", dest="source", type=Path, default=ROOT / ".cache" / "voice-research"
    )
    parser.add_argument("--engine", action="append", dest="engines")
    parser.add_argument("--hash", choices=["recorded", "full"], default="recorded")
    parser.add_argument("--no-cuda", action="store_true")
    args = parser.parse_args(argv)
    root, source = args.root.resolve(), args.source.resolve()
    try:
        if args.command == "plan":
            result = plan(root, source, args.engines)
        elif args.command == "adopt":
            root.mkdir(parents=True, exist_ok=True)
            result = adopt(root, source, args.engines, args.hash)
        elif args.command == "probe":
            result = probe(root, args.engines, cuda=not args.no_cuda)
        elif args.command == "verify":
            result = verify(root, source, args.engines)
        elif args.command == "status":
            result = summary(root)
        else:
            if not args.engines:
                parser.error("unlink needs --engine")
            result = unlink(root, args.engines)
    except SetupError as exc:
        print(f"refused: {exc}", file=sys.stderr)
        return 2
    print(json.dumps(result, indent=1))
    return 1 if args.command != "status" and troubled(result) else 0


if __name__ == "__main__":
    sys.exit(main())
