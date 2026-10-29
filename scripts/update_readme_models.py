"""Regenerate README dependency tables from the setup manifest (no network)."""

from pathlib import Path

import tomllib

ROOT = Path(__file__).resolve().parents[1]


def main():
    manifest = tomllib.loads(
        (ROOT / "backend/src/gaiavfx/setup/manifest/comfy.toml").read_text("utf-8")
    )
    models = [
        "| File | Folder | Size | Features | Licence | Source |",
        "|---|---|---:|---|---|---|",
    ]
    for model in manifest["models"]:
        source = model["url"].split("/resolve/")[0]
        size = model["bytes"]
        display = (
            f"{size / 1024**3:.2f} GiB"
            if size >= 1024**3
            else f"{size / 1024**2:.1f} MiB"
        )
        models.append(
            f"| `{model['file']}` | `{model['folder']}` | {display} | "
            f"{', '.join(model['features'])} | {model['license']} | [repo]({source}) |"
        )
    packs = [
        "| Node pack | Pinned revision | Features | Licence |",
        "|---|---|---|---|",
    ]
    for pack in manifest["node_packs"]:
        source = pack.get("url") or pack["source"].removeprefix("local:")
        revision = pack.get("commit", pack.get("version", ""))
        packs.append(
            f"| [{pack['name']}]({source}) | `{revision}` | "
            f"{', '.join(pack['features'])} | {pack.get('license', 'GPL-3.0-or-later')} |"
        )
    readme = ROOT / "README.md"
    text = readme.read_text("utf-8-sig")
    for name, rows in (("models", models), ("packs", packs)):
        start, end = f"<!-- setup-{name}:start -->", f"<!-- setup-{name}:end -->"
        before, rest = text.split(start, 1)
        _, after = rest.split(end, 1)
        text = before + start + "\n" + "\n".join(rows) + "\n" + end + after
    readme.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
