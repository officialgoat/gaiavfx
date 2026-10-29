"""Run a small inference through the application's actual ComfyUI integrations.

Standalone before setup CLI integration: python -m gaiavfx.setup.smoke --help.
No database, model downloads, engine startup or GPU work happens on this client.
"""

from __future__ import annotations

import argparse
import json
import math
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from uuid import uuid4

import httpx
import numpy as np
from PIL import Image, ImageDraw

from gaiavfx.infrastructure.comfy import ComfyGateway
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.integrations import minimax_h3
from gaiavfx.integrations.minimax_h3.graph import build_graph, render_presets
from gaiavfx.integrations.minimax_h3.prompt import compile_prompt
from gaiavfx.integrations.qwen_image_edit.graph import build_edit_graph
from gaiavfx.integrations.qwen_image_edit.prompt import compile_removal
from gaiavfx.integrations.worldbuilder.graph import settings as take_settings
from gaiavfx.integrations.worldbuilder.graph import take_geometry_graph
from gaiavfx.modules.direction.insertion import Removal
from gaiavfx.modules.direction.segment import (
    Picture,
    PictureRole,
    PlaceText,
    Segment,
    Shot,
    Subject,
)
from gaiavfx.shared.errors import ApplicationError

try:
    from gaiavfx.setup.manifest import Check
except ImportError:

    @dataclass
    class Check:
        feature: str
        item: str
        status: str
        detail: str
        fix: str


FEATURES = ("core", "image-edit", "stages")
DOCTOR = "Run python -m gaiavfx.setup doctor."
CONNECT_FIX = (
    "Start your configured ComfyUI, check --comfy-url, then run python -m gaiavfx.setup doctor."
)


def _graph(feature, remote, prefix, width, height, frames):
    if feature == "core":
        grid = minimax_h3.capabilities().frame_grid
        segment = Segment(
            subjects=(Subject("shape", "Blue square", "A blue square", ("blue colour",)),),
            place=PlaceText("background", "A plain grey background.", "grey background"),
            shots=(Shot("The square stays still. Locked camera.", "", "Quiet room tone."),),
            cut_frames=(),
            delivered_frames=frames,
            continues=False,
        )
        compiled = compile_prompt(
            segment, (Picture(PictureRole.IDENTITY, "reference", "shape"),), (), grid, 0
        )
        return build_graph(
            compiled,
            files={"reference": remote},
            seed=1,
            width=width,
            height=height,
            fps=grid.fps,
            context_latent=None,
            output_prefix=prefix,
            capture_context=False,
        )
    if feature == "image-edit":
        return build_edit_graph(
            compile_removal(Removal("reference", "blue square", "it", "the grey background")),
            image_files=[remote],
            seed=1,
            width=width,
            height=height,
            output_prefix=prefix + "/edited",
        )
    if feature == "stages":
        return take_geometry_graph(remote, start=0, end=frames, take=take_settings(), prefix=prefix)
    raise ValueError(f"Unknown smoke feature: {feature}")


def _bounds(schema, node, name):
    inputs = schema.get(node["class_type"], {}).get("input", {})
    spec = {**inputs.get("required", {}), **inputs.get("optional", {})}.get(name, [])
    return spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}


def _minimum(schema, node, name, alignment):
    """Use the server's minimum and step, respecting the latent's spatial grid.

    Missing schemas are left to ComfyGateway.validate_graph for a named error.
    Older schemas without bounds fall back to a small 64-pixel canvas.
    """
    bounds = _bounds(schema, node, name)
    step = math.lcm(alignment, max(1, int(bounds.get("step", alignment))))
    return math.ceil(max(1, bounds.get("min", 64)) / step) * step


def build_smoke_graph(feature, remote, prefix, schema):
    """Return a real integration graph and its effective smoke settings."""
    grid = minimax_h3.capabilities().frame_grid
    frames = grid.length_base if feature == "core" else 3 if feature == "stages" else 1
    width = height = 256 if feature == "stages" else 64
    draft = _graph(feature, remote, prefix, width, height, frames)
    if feature in {"core", "image-edit"}:
        node = draft["conditioning" if feature == "core" else "latent"]
        alignment = 32 if feature == "core" else 16
        width, height = (_minimum(schema, node, name, alignment) for name in ("width", "height"))
        if feature == "core":
            frames = grid.window_for(int(_bounds(schema, node, "length").get("min", frames)), 0)
    graph = _graph(feature, remote, prefix, width, height, frames)
    settings = {"width": width, "height": height, "frames": frames, "seed": 1}
    if feature == "core":
        settings.update(
            fps=grid.fps, steps=render_presets().sampling["steps"], capture_context=False
        )
    elif feature == "image-edit":
        settings.update(
            steps=graph["sample"]["inputs"]["steps"],
            reference_megapixels=graph["scaled_1"]["inputs"]["megapixels"],
        )
    else:
        settings.update(fps=24, geometry_resolution=take_settings()["geometry"]["resolution"])
    return graph, settings


def _synthetic(path, width, height, offset=0):
    image = Image.new("RGB", (width, height))
    draw = ImageDraw.Draw(image)
    for y in range(height):
        shade = 100 + 80 * y // height
        draw.line((0, y, width, y), fill=(shade, shade, shade))
    draw.rectangle(
        (width // 4 + offset, height // 4, width * 3 // 4 + offset, height * 3 // 4),
        fill=(35, 95, 210),
    )
    image.save(path)


def _prepare_input(feature, directory, settings, media):
    directory.mkdir(parents=True, exist_ok=True)
    width, height = settings["width"], settings["height"]
    if feature != "stages":
        target = directory / "reference.png"
        _synthetic(target, width, height)
        return target
    for index in range(settings["frames"]):
        _synthetic(directory / f"frame-{index:03}.png", width, height, offset=index * 4)
    target = directory / "take.mp4"
    media.run(
        media.ffmpeg,
        [
            "-v",
            "error",
            "-y",
            "-framerate",
            str(settings["fps"]),
            "-i",
            str(directory / "frame-%03d.png"),
            "-frames:v",
            str(settings["frames"]),
            "-c:v",
            "libx264",
            "-pix_fmt",
            "yuv420p",
            str(target),
        ],
        timeout=30,
    )
    return target


@contextmanager
def _stage(report, name, emit):
    entry = {"name": name, "status": "running", "seconds": None, "detail": ""}
    report["stages"].append(entry)
    start = time.monotonic()
    emit(f"{name}: starting")
    try:
        yield entry
    except Exception as exc:
        entry.update(status="fail", detail=str(exc))
        raise
    else:
        entry["status"] = "ok"
    finally:
        entry["seconds"] = round(time.monotonic() - start, 3)
        emit(f"{name}: {entry['status']} ({entry['seconds']:.3f}s) {entry['detail']}".rstrip())


def _history_error(entry):
    status = entry.get("status", {})
    for kind, detail in status.get("messages", []):
        if kind in {"execution_error", "execution_interrupted"}:
            return f"ComfyUI {kind}: {json.dumps(detail, ensure_ascii=False)}"
    if status.get("status_str") == "error":
        return f"ComfyUI execution failed: {status.get('messages', [])}"
    return None


def _poll(gateway, url, prompt_id, timeout, report, emit):
    start = time.monotonic()
    deadline = start + timeout * 60
    running_at = None
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError(
                f"ComfyUI exceeded {timeout:g} minutes waiting for prompt {prompt_id}."
            )
        history = gateway.request(url, "GET", f"/history/{prompt_id}", timeout=min(5, remaining))
        entry = history.get(prompt_id)
        if entry:
            error = _history_error(entry)
            if error:
                raise ApplicationError(error)
            if entry.get("status", {}).get("completed"):
                elapsed = time.monotonic() - start
                stamps = {
                    kind: data["timestamp"] / 1000
                    for kind, data in entry.get("status", {}).get("messages", [])
                    if isinstance(data, dict) and isinstance(data.get("timestamp"), (int, float))
                }
                # Server timestamps measure execution even when a quick job finishes between polls.
                duration = None
                if "execution_start" in stamps and "execution_success" in stamps:
                    duration = max(0, stamps["execution_success"] - stamps["execution_start"])
                    queue_wait = max(0, elapsed - duration)
                    source = "history timestamps"
                else:
                    queue_wait = None if running_at is None else running_at - start
                    duration = None if running_at is None else time.monotonic() - running_at
                    source = "queue polling estimate" if running_at else "unavailable"
                report["timings"] = {
                    "queue_wait_seconds": None if queue_wait is None else round(queue_wait, 3),
                    "run_seconds": None if duration is None else round(duration, 3),
                    "source": source,
                }
                emit(
                    f"queue wait: {queue_wait if queue_wait is not None else 'unknown'}s; "
                    f"run time: {duration if duration is not None else 'unknown'}s ({source})"
                )
                return entry
        queue = gateway.request(
            url, "GET", "/queue", timeout=min(5, max(0.01, deadline - time.monotonic()))
        )
        if running_at is None and any(
            str(item[1]) == prompt_id for item in queue.get("queue_running", [])
        ):
            running_at = time.monotonic()
            emit("queue wait: finished; inference running")
        time.sleep(min(0.5, max(0, deadline - time.monotonic())))


def _descriptors(entry, feature):
    # Restrict collection to the integration's delivery node; previews cannot produce a false PASS.
    output = entry.get("outputs", {}).get("save_video" if feature == "core" else "save", {})
    fields = (
        ("videos", "gifs", "images")
        if feature == "core"
        else ("geometry",)
        if feature == "stages"
        else ("images",)
    )
    seen = set()
    for field in fields:
        for item in output.get(field, []):
            key = (item.get("subfolder", ""), item.get("filename"))
            if item.get("filename") and item.get("type", "output") == "output" and key not in seen:
                seen.add(key)
                yield item


def _inspect(path, feature, settings, media):
    result = {"path": str(path), "bytes": path.stat().st_size, "frames": None}
    if not result["bytes"]:
        raise ApplicationError(f"Empty output: {path.name}")
    if feature == "stages":
        with np.load(path, allow_pickle=False) as data:
            required = {"image", "depth", "confidence", "extrinsics", "intrinsics", "people"}
            if not required <= set(data.files):
                raise ApplicationError(
                    f"Geometry output lacks {sorted(required - set(data.files))}"
                )
            result["frames"] = int(data["image"].shape[0])
    elif feature == "image-edit" or media.ffprobe.is_file():
        info = media.probe(path)
        expected_kind = "video" if feature == "core" else "image"
        if info["kind"] != expected_kind:
            raise ApplicationError(f"Expected {expected_kind}, received {info['kind']}.")
        result.update(width=info["width"], height=info["height"])
        result["frames"] = 1 if info["kind"] == "image" else info.get("video_frames")
        if info["kind"] == "video" and result["frames"] is None:
            counted = json.loads(
                media.run(
                    media.ffprobe,
                    [
                        "-v",
                        "error",
                        "-select_streams",
                        "v:0",
                        "-count_frames",
                        "-show_entries",
                        "stream=nb_read_frames",
                        "-of",
                        "json",
                        str(path),
                    ],
                )
            )
            count = counted.get("streams", [{}])[0].get("nb_read_frames")
            result["frames"] = int(count) if str(count).isdigit() else None
        if (info["width"], info["height"]) != (settings["width"], settings["height"]):
            raise ApplicationError("Output dimensions do not match the submitted graph.")
    else:
        result["probe_note"] = (
            "ffprobe unavailable; frame count was not verified. Run npm run setup:media."
        )
    if result["frames"] is not None and result["frames"] != settings["frames"]:
        raise ApplicationError(
            f"Expected {settings['frames']} frames, received {result['frames']}."
        )
    return result


def run_smoke(
    *, comfy_url="http://127.0.0.1:8188", feature="core", out=None, timeout=20, emit=None
):
    """Return the JSON-compatible result; all HTTP goes through the app gateway."""
    emit = emit or (lambda message: None)
    run_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S-%fZ") + "-" + uuid4().hex[:8]
    directory = Path(out) if out is not None else Path(".runtime/smoke") / run_id
    report = {
        "feature": feature,
        "comfy_url": comfy_url,
        "out": str(directory),
        "result": "FAIL",
        "prompt_id": None,
        "settings": {},
        "stages": [],
        "outputs": [],
        "checks": [],
        "timings": {"queue_wait_seconds": None, "run_seconds": None, "source": "unavailable"},
        "error": None,
        "fix": "",
        "cleanup": [],
    }
    gateway, media = ComfyGateway(), MediaTools()
    connected = completed = False
    try:
        if feature not in FEATURES or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Choose a supported feature and a positive finite timeout in minutes.")
        with _stage(report, "connect", emit):
            schema = gateway.request(comfy_url, "GET", "/object_info", timeout=2)
            connected = True
        prefix = f"gaiavfx/smoke/{run_id}"
        remote = prefix + ("/take.mp4" if feature == "stages" else "/reference.png")
        graph, settings = build_smoke_graph(feature, remote, prefix, schema)
        report["settings"] = settings
        directory.mkdir(parents=True, exist_ok=True)
        with _stage(report, "validate", emit):
            graph = gateway.validate_graph(comfy_url, graph)
        (directory / "graph.json").write_text(json.dumps(graph, indent=2), encoding="utf-8")
        with _stage(report, "upload", emit):
            source = _prepare_input(feature, directory / "inputs", settings, media)
            gateway.upload(comfy_url, source, remote)
        with _stage(report, "submit", emit):
            submitted = gateway.request(
                comfy_url, "POST", "/prompt", json={"prompt": graph, "client_id": run_id}
            )
            report["prompt_id"] = submitted.get("prompt_id")
            if submitted.get("node_errors") or not report["prompt_id"]:
                raise ApplicationError(f"ComfyUI rejected the graph: {json.dumps(submitted)}")
        with _stage(report, "queue and run", emit):
            entry = _poll(gateway, comfy_url, report["prompt_id"], timeout, report, emit)
            completed = True
        with _stage(report, "outputs", emit):
            for index, descriptor in enumerate(_descriptors(entry, feature)):
                # Never use a remote directory or filename as a local path component.
                suffix = Path(str(descriptor["filename"])).suffix.lower()
                if suffix not in {".png", ".jpg", ".webp", ".mp4", ".webm", ".npz"}:
                    raise ApplicationError(f"Unexpected output extension: {suffix!r}")
                target = directory / f"output-{index + 1}{suffix}"
                gateway.download(comfy_url, descriptor, target)
                output = _inspect(target, feature, settings, media)
                report["outputs"].append(output | {"remote": descriptor})
                count = output["frames"] if output["frames"] is not None else "unknown"
                emit(f"output: {target} ({output['bytes']} bytes, {count} frames)")
                if output.get("probe_note"):
                    emit(output["probe_note"])
            if not report["outputs"]:
                raise ApplicationError("ComfyUI completed without a delivered output.")
        report["result"] = "PASS"
    except (ApplicationError, httpx.HTTPError, OSError, ValueError, KeyError) as exc:
        report["error"] = str(exc)
        report["fix"] = CONNECT_FIX if not connected else DOCTOR
        if isinstance(exc, httpx.TransportError) and not connected:
            report["error"] = f"ComfyUI is unreachable at {comfy_url}: {exc}"
        if isinstance(exc, httpx.HTTPStatusError):
            report["error"] += " " + exc.response.text[:2000]
    except KeyboardInterrupt:
        report["error"] = "Smoke interrupted."
    finally:
        # Cancellation is scoped to our prompt, like the app's job runner. Never interrupt other work.
        if report["prompt_id"] and not completed:
            for route, body in (
                ("/queue", {"delete": [report["prompt_id"]]}),
                ("/interrupt", {"prompt_id": report["prompt_id"]}),
            ):
                try:
                    gateway.request(comfy_url, "POST", route, json=body, timeout=2)
                except (httpx.HTTPError, ApplicationError) as exc:
                    report["cleanup"].append(f"{route}: {exc}")
        try:
            gateway.request(
                comfy_url,
                "POST",
                "/free",
                json={"unload_models": True, "free_memory": True},
                timeout=2,
            )
            report["cleanup"].append("/free: ok")
        except (httpx.HTTPError, ApplicationError) as exc:
            report["cleanup"].append(f"/free: {exc}")
            if report["result"] == "PASS":
                report.update(
                    result="FAIL", error=f"Could not release ComfyUI models: {exc}", fix=DOCTOR
                )
    report["checks"].append(
        asdict(
            Check(
                feature,
                "inference",
                "ok" if report["result"] == "PASS" else "missing",
                report["error"] or "Delivered output verified.",
                report["fix"],
            )
        )
    )
    if directory.is_dir():
        try:
            (directory / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        except OSError as exc:
            report.update(result="FAIL", error=f"Could not save smoke report: {exc}")
            report["checks"][0].update(status="missing", detail=report["error"])
    return report


def _positive_minutes(value):
    try:
        number = float(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("timeout must be positive finite minutes") from exc
    if not math.isfinite(number) or number <= 0:
        raise argparse.ArgumentTypeError("timeout must be positive finite minutes")
    return number


def _arguments(parser):
    parser.add_argument("--comfy-url", default="http://127.0.0.1:8188")
    parser.add_argument("--feature", choices=FEATURES, default="core")
    parser.add_argument(
        "--out", type=Path, help="output directory (default: .runtime/smoke/<timestamp>/)"
    )
    parser.add_argument(
        "--timeout",
        type=_positive_minutes,
        default=20,
        metavar="MIN",
        help="queue and inference timeout in minutes (default: 20)",
    )
    parser.add_argument(
        "--json", action="store_true", help="print one JSON result instead of progress"
    )


def _run(args):
    report = run_smoke(
        comfy_url=args.comfy_url,
        feature=args.feature,
        out=args.out,
        timeout=args.timeout,
        emit=None if args.json else print,
    )
    if args.json:
        print(json.dumps(report, ensure_ascii=False))
    else:
        if report["error"]:
            print(report["error"])
        if report["fix"]:
            print(report["fix"])
        print(f"{report['result']}: {args.feature} smoke")
    return 0 if report["result"] == "PASS" else 1


def register(subparsers):
    parser = subparsers.add_parser("smoke", help="run inference with the app's real graphs")
    _arguments(parser)
    parser.set_defaults(func=_run)
    return parser


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    _arguments(parser)
    return _run(parser.parse_args(argv))


if __name__ == "__main__":
    raise SystemExit(main())
