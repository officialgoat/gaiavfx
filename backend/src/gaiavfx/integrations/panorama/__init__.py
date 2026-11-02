"""The Panoramas integration: Krea 2 Turbo with the 360 text-to-image LoRA draws a panorama from words, Fibo 2.0 Edit
Turbo grows a picture into one and closes every panorama's seam, and MoGe-3 measures a picture's field of view. The
recipes are FlooPowder's Worldbuilder research winners (R1-R2, October 2026), frozen in `knowledge/`."""

from functools import cache
from importlib import resources

NAME = "panorama"
# Candidates are images; a picture's measured field of view is collected as an analysis.
OUTPUT_KINDS = frozenset({"image", "analysis"})


@cache
def knowledge() -> dict[str, str]:
    """Return the integration's authored knowledge files for run snapshots."""
    root = resources.files(__name__)
    return {
        name: root.joinpath(name).read_text("utf-8")
        for name in ("knowledge/wording.toml", "knowledge/render.toml")
    }
