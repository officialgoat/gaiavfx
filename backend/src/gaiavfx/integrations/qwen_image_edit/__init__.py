"""Maintained Qwen-Image-Edit-2511 insertion and removal integration."""

from functools import cache
from importlib import resources

NAME = "qwen_image_edit"
OUTPUT_KINDS = frozenset({"image"})


@cache
def knowledge() -> dict[str, str]:
    """Return the integration's authored knowledge files for run snapshots."""
    root = resources.files(__name__)
    return {
        name: root.joinpath(name).read_text("utf-8")
        for name in ("knowledge/wording.toml", "knowledge/render.toml")
    }
