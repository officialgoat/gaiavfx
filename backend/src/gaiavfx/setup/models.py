"""ComfyUI model locations, including its extra_model_paths YAML subset."""

from __future__ import annotations

import os
import re
from pathlib import Path

from .download import download, matches
from .manifest import comfy_directory, load_manifest, selected_features


def _scalar(value: str) -> str:
    value = value.strip()
    if value.startswith(("'", '"')):
        quoted = re.fullmatch(r"(['\"])(.*?)\1(?:\s+#.*)?", value)
        if not quoted:
            raise ValueError("Unclosed quote in extra_model_paths.yaml")
        return quoted[2].replace("''", "'") if quoted[1] == "'" else quoted[2]
    return value.split(" #", 1)[0].strip()


def extra_model_paths(path: Path) -> dict[str, list[Path]]:
    """Read section -> base_path / folder scalars or | blocks; reject unsupported YAML.

    This deliberately does not interpret YAML tags, anchors, flow maps or objects.
    Relative base paths are resolved against the configuration file, as ComfyUI does.
    """
    sections: list[dict[str, str]] = []
    section = None
    block = None
    for number, raw in enumerate(path.read_text(encoding="utf-8-sig").splitlines(), 1):
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" "))
        text = raw.strip()
        if "\t" in raw[: indent + 1]:
            raise ValueError(f"Use spaces in {path}:{number}")
        if block and indent > block[1]:
            section[block[0]] += "\n" + _scalar(text)
            continue
        block = None
        if ":" not in text:
            raise ValueError(f"Expected a folder mapping in {path}:{number}")
        key, value = text.split(":", 1)
        key, value = _scalar(key), _scalar(value)
        if indent == 0:
            if value:
                raise ValueError(f"Expected a section in {path}:{number}")
            section = {}
            sections.append(section)
        elif section is None:
            raise ValueError(f"Expected a section in {path}:{number}")
        else:
            if value.startswith(("[", "{", "&", "*", "!")):
                raise ValueError(f"Use plain folder paths or a | block in {path}:{number}")
            section[key] = "" if value in {"|", "|-", "|+"} else value
            if value in {"|", "|-", "|+"}:
                block = (key, indent)
    result: dict[str, list[Path]] = {}
    for section in sections:
        base = Path(os.path.expandvars(section.get("base_path", ""))).expanduser()
        if not base.is_absolute():
            base = path.resolve().parent / base
        for folder, paths in section.items():
            if folder in {"base_path", "is_default", "custom_nodes"}:
                continue
            folder = {"unet": "diffusion_models", "clip": "text_encoders"}.get(folder, folder)
            for entry in paths.splitlines():
                if entry.strip():
                    candidate = Path(os.path.expandvars(entry.strip())).expanduser()
                    result.setdefault(folder, []).append((base / candidate).resolve())
    return result


def model_paths(comfy_dir: Path, folder: str, extra_paths: Path | None = None) -> list[Path]:
    local = comfy_dir / "models" / folder
    if folder == "insightface/models/buffalo_l":
        return [local]
    config = extra_paths or comfy_dir / "extra_model_paths.yaml"
    if extra_paths and not config.is_file():
        raise ValueError(f"Extra model paths file does not exist: {config}")
    mapped = extra_model_paths(config).get(folder, []) if config.is_file() else []
    legacy = {"diffusion_models": "unet", "text_encoders": "clip"}.get(folder)
    aliases = [comfy_dir / "models" / legacy] if legacy else []
    return list(dict.fromkeys([*mapped, local, *aliases]))


def model_candidates(comfy_dir: Path, model: dict, extra_paths: Path | None = None) -> list[Path]:
    # Graphs use this exact relative filename. A nested file with the same basename
    # does not satisfy ComfyUI's get_full_path(folder, filename).
    return [
        folder / model["file"] for folder in model_paths(comfy_dir, model["folder"], extra_paths)
    ]


def install_models(args) -> int:
    manifest = load_manifest()
    args.comfy_dir = comfy_directory(args.comfy_dir)
    features = selected_features(args.features) if not args.all else list(manifest["features"])
    models = [
        m
        for m in manifest["models"]
        if set(m["features"]) & set(features) and (args.all or not m["optional"])
    ]
    plan = []
    for model in models:
        candidates = model_candidates(args.comfy_dir, model, args.extra_model_paths)
        existing = next(
            (
                p
                for p in candidates
                if matches(p, model["bytes"], model["sha256"], verify=args.verify)
            ),
            None,
        )
        plan.append((model, existing or candidates[0], existing is not None))
    total = sum(m["bytes"] for m, _, present in plan if not present)
    print(
        f"Models: {len(plan)} files; total {sum(m['bytes'] for m, _, _ in plan):,} bytes; to download {total:,} bytes ({total / 1024**3:.2f} GiB)."
    )
    for model, target, present in plan:
        state = "present" if present else "download" if model["url"] else "manual download"
        print(
            f"{state:15} {model['folder']}/{model['file']} ({model['bytes']:,} bytes) -> {target}"
        )
        print(f"  Licence: {model['license']}")
        if not model["url"]:
            print("  " + model["note"])
    if args.dry_run:
        return 0
    if total and not args.yes:
        try:
            answer = input("Download these files? [y/N] ")
        except EOFError:
            answer = ""
        if answer.strip().lower() not in {"y", "yes"}:
            print("Cancelled. Use --yes for unattended downloads.")
            return 1
    failed = False
    for model, target, present in plan:
        if present:
            continue
        if not model["url"]:
            failed = True
            continue
        download(
            model["url"], target, size=model["bytes"], sha256=model["sha256"], verify=args.verify
        )
    return int(failed)


def register(subparsers):
    parser = subparsers.add_parser("models", help="Download verified ComfyUI models")
    actions = parser.add_subparsers(dest="models_command", required=True)
    install = actions.add_parser("download", help="Download the selected feature models")
    install.add_argument("--comfy-dir", type=Path, required=True)
    install.add_argument("--features", nargs="+", default=["core"])
    install.add_argument("--all", action="store_true")
    install.add_argument("--dry-run", action="store_true")
    install.add_argument(
        "--verify", action="store_true", help="Hash existing files before skipping"
    )
    install.add_argument("--yes", action="store_true")
    install.add_argument("--extra-model-paths", type=Path)
    install.set_defaults(func=install_models)
