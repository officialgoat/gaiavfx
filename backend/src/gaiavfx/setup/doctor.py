"""Read-only ComfyUI and local-runtime readiness, shared with the API."""

from __future__ import annotations

import importlib
import json
import os
import re
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, replace
from pathlib import Path
from urllib.parse import quote, urlparse

import httpx

from .comfy_install import start_command
from .download import file_sha256
from .manifest import (
    FEATURES,
    Check,
    comfy_directory,
    load_manifest,
    read_setup_file,
    selected_features,
    setup_command,
)
from .models import model_candidates


class HttpGateway:
    """The same request interface as ComfyGateway; CLI needs only stdlib + httpx."""

    def request(self, url, method, route, **kwargs):
        with httpx.Client(follow_redirects=False) as client:
            response = client.request(method, url.rstrip("/") + route, **kwargs)
            response.raise_for_status()
            return response.json()


def runtime_checks() -> list[Check]:
    checks = []
    for name in ("engines", "assistant", "media"):
        fix = (
            setup_command(name, "install", "worldbuilder")
            if name == "engines"
            else setup_command(name, "install")
        )
        try:
            module = importlib.import_module(f"gaiavfx.setup.{name}")
            for check in module.check():
                # Preserve feature dependencies (e.g. Stages needs Worldbuilder), and
                # also list all runtime items together for setup and installation.
                if check.feature in FEATURES:
                    checks.append(check)
                checks.append(replace(check, feature="runtime", item=f"{name}: {check.item}"))
        except ModuleNotFoundError as exc:
            if exc.name != f"gaiavfx.setup.{name}":
                checks.append(
                    Check(
                        "runtime",
                        name,
                        "unknown",
                        f"Runtime checker dependency is missing: {exc.name}",
                        fix,
                    )
                )
            else:
                checks.append(Check("runtime", name, "optional", "not available in this build"))
        except Exception as exc:  # noqa: BLE001 -- one unavailable optional runtime must not break the doctor
            checks.append(
                Check(
                    "runtime",
                    name,
                    "unknown",
                    f"Could not check {name}: {exc}",
                    fix,
                )
            )
    return checks


def verdicts(checks: list[Check]) -> dict[str, str]:
    groups: dict[str, list[str]] = {}
    for check in checks:
        groups.setdefault(check.feature, []).append(check.status)
    return {
        feature: next(
            (status for status in ("missing", "mismatch", "unknown") if status in statuses), "ok"
        )
        for feature, statuses in groups.items()
    }


def _version(value: str) -> tuple[int, int, int] | None:
    match = re.match(r"^v?(\d+)\.(\d+)\.(\d+)(?:\D|$)", value)
    return tuple(map(int, match.groups())) if match else None


def check_setup(
    comfy_url: str = "http://127.0.0.1:8188",
    *,
    comfy_dir: Path | None = None,
    features: str | list[str] | None = None,
    verify: bool = False,
    extra_paths: Path | None = None,
    gateway=None,
    manifest: dict | None = None,
    include_runtime: bool = True,
) -> list[Check]:
    manifest = manifest or load_manifest()
    requested = selected_features(features)
    selected = [feature for feature in requested if feature != "runtime"]
    gateway = gateway or HttpGateway()
    checks = []
    if comfy_dir:
        comfy_dir = comfy_directory(comfy_dir)
    defaults = read_setup_file()
    parsed_url = urlparse(comfy_url)
    # Saved local paths inform fix commands only, never remote presence checks.
    saved_url = defaults.get("comfy_url", "http://127.0.0.1:8188")
    saved_dir = (
        defaults.get("comfy_dir")
        if parsed_url.hostname in {"localhost", "127.0.0.1", "::1"}
        and isinstance(saved_url, str)
        and comfy_url.rstrip("/") == saved_url.rstrip("/")
        else None
    )
    known_dir = comfy_dir or (
        comfy_directory(Path(saved_dir)) if isinstance(saved_dir, str) and saved_dir else None
    )
    target = known_dir or Path("ComfyUI")
    install = setup_command("comfy", "install", target)
    nodes_fix = setup_command("nodes", "install", "--comfy-dir", target)
    baseline = manifest["comfyui"]

    def request(route):
        try:
            return gateway.request(comfy_url, "GET", route, timeout=httpx.Timeout(3, connect=2)), ""
        except Exception as exc:  # noqa: BLE001 -- transport adapters return diagnostics, including ApplicationError
            return None, str(exc)

    if selected:
        stats, _error = request("/system_stats")
        if not isinstance(stats, dict):
            fix = (
                start_command(known_dir, parsed_url.port or 8188)
                if known_dir
                else setup_command("comfy", "install", target)
            )
            detail = f"ComfyUI not reachable at {comfy_url}. Start ComfyUI or check its address in Settings."
            checks.extend(Check(f, "ComfyUI", "missing", detail, fix) for f in selected)
        else:
            system = stats.get("system")
            version = str(system.get("comfyui_version") or "") if isinstance(system, dict) else ""
            actual = _version(version)
            minimum = _version(baseline["min_version"])
            tested = _version(baseline["tested_version"])
            status = "unknown" if actual is None else "mismatch" if actual < minimum else "ok"
            detail = f"ComfyUI {version or 'version unknown'}; minimum {baseline['min_version']}, tested {baseline['tested_version']}."
            checks.extend(
                Check(f, "ComfyUI", status, detail, install if status != "ok" else "")
                for f in selected
            )
            if actual and minimum <= actual < tested:
                checks.extend(
                    Check(
                        f,
                        "ComfyUI compatibility",
                        "optional",
                        "Untested ComfyUI version; use the tested version "
                        + baseline["tested_version"]
                        + ".",
                        install,
                    )
                    for f in selected
                )
            models = [m for m in manifest["models"] if set(m["features"]) & set(selected)]
            routes = [
                "/object_info",
                "/models",
                "/gaiavfx/health",
                *sorted(
                    {
                        "/models/" + quote(m["folder"], safe="")
                        for m in models
                        if not m["folder"].startswith("insightface/")
                    }
                ),
            ]
            with ThreadPoolExecutor(max_workers=8) as pool:
                fetched = dict(zip(routes, pool.map(request, routes)))
            objects, objects_error = fetched["/object_info"]
            health, _health_error = fetched["/gaiavfx/health"]
            folders, folders_error = fetched["/models"]
            for feature in selected:
                definition = manifest["features"][feature]
                for kind in [*definition["core_classes"], *definition["node_classes"]]:
                    fix = nodes_fix if kind in definition["node_classes"] else install
                    state = (
                        "unknown"
                        if not isinstance(objects, dict)
                        else "ok"
                        if kind in objects
                        else "missing"
                    )
                    checks.append(
                        Check(
                            feature,
                            "node:" + kind,
                            state,
                            objects_error
                            or (
                                "Registered"
                                if state == "ok"
                                else f"Class {kind} is not registered. Install it and restart ComfyUI."
                            ),
                            fix if state != "ok" else "",
                        )
                    )
                needs_health = any(c.startswith("Gaia") for c in definition["node_classes"])
                if needs_health:
                    valid_health = (
                        isinstance(health, dict)
                        and health.get("pack") == "gaiavfx"
                        and isinstance(health.get("nodes"), list)
                    )
                    checks.append(
                        Check(
                            feature,
                            "GAIAVFX node pack health",
                            "ok" if valid_health else "missing",
                            f"Pack {health.get('version', 'unknown')} responding"
                            if valid_health
                            else "GAIAVFX health route unavailable. Install the pack and restart ComfyUI.",
                            "" if valid_health else nodes_fix,
                        )
                    )
                    if valid_health:
                        expected = next(
                            p for p in manifest["node_packs"] if p["name"] == "gaiavfx"
                        ).get("version")
                        if expected and health.get("version") != expected:
                            checks.append(
                                Check(
                                    feature,
                                    "GAIAVFX node pack version",
                                    "mismatch",
                                    f"Found {health.get('version')}; expected {expected}.",
                                    nodes_fix,
                                )
                            )
                        absent = [
                            c
                            for c in definition["node_classes"]
                            if c.startswith("Gaia") and c not in health["nodes"]
                        ]
                        if absent:
                            checks.append(
                                Check(
                                    feature,
                                    "GAIAVFX registered nodes",
                                    "missing",
                                    ", ".join(absent),
                                    nodes_fix,
                                )
                            )
                if feature == "recast":
                    for dependency in ("insightface", "onnxruntime"):
                        info = health.get(dependency, {}) if isinstance(health, dict) else {}
                        ready = isinstance(info, dict) and info.get("importable") is True
                        checks.append(
                            Check(
                                feature,
                                dependency,
                                "ok" if ready else "missing",
                                "Importable in ComfyUI Python"
                                if ready
                                else f"{dependency} is unavailable in ComfyUI Python. Reinstall the optional face requirements and restart ComfyUI.",
                                "" if ready else nodes_fix,
                            )
                        )
                for pack in manifest["node_packs"]:
                    if feature in pack["features"] and pack.get("note"):
                        checks.append(
                            Check(feature, pack["name"] + " licence", "optional", pack["note"])
                        )
                for model in models:
                    if model["file"] not in definition["models"]:
                        continue
                    fix = setup_command(
                        "models", "download", "--comfy-dir", target, "--features", feature
                    )
                    state, info = "unknown", "Model listing unavailable"
                    if model["folder"].startswith("insightface/"):
                        buffalo = health.get("buffalo_l") if isinstance(health, dict) else None
                        present = buffalo.get("present") if isinstance(buffalo, dict) else None
                        state = (
                            "ok"
                            if present is True
                            else "missing"
                            if present is False
                            else "unknown"
                        )
                        info = (
                            "buffalo_l files present (GAIAVFX health)"
                            if present
                            else "buffalo_l is unavailable at ComfyUI/models/insightface/models/buffalo_l"
                        )
                    else:
                        listing, listing_error = fetched[
                            "/models/" + quote(model["folder"], safe="")
                        ]
                        if isinstance(listing, list):
                            present = any(
                                str(file).replace("\\", "/") == model["file"] for file in listing
                            )
                            state = "ok" if present else "missing"
                            info = f"{model['folder']}/{model['file']}: " + (
                                "present via ComfyUI" if present else "missing"
                            )
                        elif isinstance(folders, list) and model["folder"] not in folders:
                            state, info = (
                                "missing",
                                "ComfyUI model folder is not registered: " + model["folder"],
                            )
                        else:
                            info = listing_error or folders_error or info
                    if comfy_dir:
                        candidates = model_candidates(comfy_dir, model, extra_paths)
                        existing = [p for p in candidates if p.is_file()]
                        correct = [
                            p
                            for p in existing
                            if not model["bytes"] or p.stat().st_size == model["bytes"]
                        ]
                        if verify:
                            correct = [
                                p
                                for p in correct
                                if model["sha256"] and file_sha256(p) == model["sha256"]
                            ]
                        if not correct:
                            state = "mismatch" if existing else "missing"
                            info = f"{candidates[0]}: " + (
                                "size or SHA-256 mismatch" if existing else "missing locally"
                            )
                        elif state == "ok" or model["folder"].startswith("insightface/"):
                            state, info = (
                                "ok",
                                f"{correct[0]}: size checked"
                                + (", SHA-256 verified" if verify else ""),
                            )
                        if state == "mismatch":
                            fix += " --verify"
                    if not model["url"] and state != "ok":
                        info += "; manual download: " + model["note"]
                        fix = ""
                    if model["optional"] and state != "ok":
                        state = "optional"
                    info += ". Licence: " + model["license"]
                    checks.append(
                        Check(
                            feature,
                            "model:" + model["file"],
                            state,
                            info,
                            fix if state != "ok" else "",
                        )
                    )
    if include_runtime:
        checks.extend(
            check
            for check in runtime_checks()
            if check.feature == "runtime" or check.feature in selected
        )
    return checks


def doctor(args) -> int:
    if (args.verify or args.extra_model_paths) and not args.comfy_dir:
        raise ValueError("--verify and --extra-model-paths require --comfy-dir.")
    defaults = read_setup_file()
    url = (
        args.comfy_url
        or os.environ.get("GAIAVFX_COMFY_URL")
        or defaults.get("comfy_url", "http://127.0.0.1:8188")
    )
    selected = (
        selected_features(args.features)
        if args.features
        else [*load_manifest()["features"], "runtime"]
    )
    checks = check_setup(
        url,
        comfy_dir=args.comfy_dir,
        features=selected,
        verify=args.verify,
        extra_paths=args.extra_model_paths,
    )
    if args.json:
        print(json.dumps([asdict(check) for check in checks], indent=2))
    else:
        for feature, verdict in verdicts(checks).items():
            print(f"\n{feature}\n{'STATUS':9} {'ITEM':45} DETAIL")
            for check in checks:
                if check.feature == feature:
                    print(f"{check.status:9} {check.item:45} {check.detail}")
                    if check.fix:
                        print("  Fix: " + check.fix)
            print(
                f"{feature}: {'ready' if verdict == 'ok' else 'setup incomplete (' + verdict + ')'}"
            )
    states = verdicts(checks)
    return int(any(states.get(feature, "unknown") != "ok" for feature in selected))


def register(subparsers):
    parser = subparsers.add_parser("doctor", help="Check ComfyUI nodes, models and local runtimes")
    parser.add_argument("--comfy-url")
    parser.add_argument("--comfy-dir", type=Path, help="Also check local file sizes")
    parser.add_argument("--features", nargs="+")
    parser.add_argument("--verify", action="store_true", help="Hash local model files")
    parser.add_argument("--extra-model-paths", type=Path)
    parser.add_argument("--json", action="store_true")
    parser.set_defaults(func=doctor)
