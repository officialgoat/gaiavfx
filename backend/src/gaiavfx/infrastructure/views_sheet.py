"""Render the H3 views-only sheet with the research sheet's pixel operations."""

from pathlib import Path

from PIL import Image


def render_views_sheet(
    tiles: list[Path | None],
    layout: tuple[int, int, list[tuple[int, int, int, int]]],
    gap_rgb: tuple[int, int, int],
) -> Image.Image:
    """Use RGB source pixels, LANCZOS scaling and grey for gaps and blank tiles."""
    width, height, boxes = layout
    canvas = Image.new("RGB", (width, height), gap_rgb)
    for tile, (x, y, w, h) in zip(tiles, boxes, strict=True):
        if tile is not None:
            with Image.open(tile) as opened:
                image = opened.convert("RGB")
            canvas.paste(image.resize((w, h), Image.LANCZOS), (x, y))
    return canvas
