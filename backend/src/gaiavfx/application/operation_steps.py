"""Deterministic local steps for Generation operations."""

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageOps

from gaiavfx.infrastructure.views_sheet import render_views_sheet
from gaiavfx.integrations.minimax_h3.sheet import portrait_sheet_layout, views_sheet_layout
from gaiavfx.modules.library.media_application import MediaLibrary


@dataclass(frozen=True)
class LocalContext:
    library: MediaLibrary
    staging: Path
    inputs: dict
    paths: dict[str, Path]
    snapshot: dict


def views_sheet(context: LocalContext) -> list[tuple[str, Path, str]]:
    """Make one sheet from the ordered tile ids and the layout the caller resolved from the integration's pack.

    Nothing is defaulted here: a missing parameter fails the job rather than silently changing the geometry.
    """
    spec = context.snapshot["step_spec"]
    tiles = [
        context.paths[asset_id] if asset_id is not None else None for asset_id in spec["tiles"]
    ]
    sizes = []
    for tile in tiles:
        if tile is None:
            sizes.append(tuple(spec["blank_tile_size"]))
        else:
            with Image.open(tile) as image:
                sizes.append(image.size)
    layout = views_sheet_layout(sizes, spec["columns"], spec["gap"])
    context.staging.mkdir(parents=True, exist_ok=True)
    path = context.staging / "views-sheet.png"
    render_views_sheet(tiles, layout, tuple(spec["gap_rgb"])).save(path)
    return [("views_sheet:images:0", path, "image")]


def extract_frames(context: LocalContext) -> list[tuple[str, Path, str]]:
    spec = context.snapshot["step_spec"]
    source = context.paths.get(spec["source"])
    if source is None:
        raise ValueError("Frame source is missing.")
    context.staging.mkdir(parents=True, exist_ok=True)
    outputs = []
    seen = set()
    for key, ordinal in spec["frames"]:
        if not isinstance(key, str) or not key or key in seen:
            raise ValueError("Frame output keys must be unique non-empty strings.")
        if type(ordinal) is not int or ordinal < 0:
            raise ValueError("Frame ordinal must be a nonnegative integer.")
        seen.add(key)
        path = context.staging / f"{key}.png"
        if not path.resolve().is_relative_to(context.staging.resolve()):
            raise ValueError("Invalid frame output key.")
        context.library.media.frame(source, path, ordinal)
        outputs.append((key, path, "image"))
    return outputs


def fit_frame(context: LocalContext) -> list[tuple[str, Path, str]]:
    spec = context.snapshot["step_spec"]
    source = context.paths.get(spec["source"])
    if source is None:
        raise ValueError("Frame source is missing.")
    width, height = spec["width"], spec["height"]
    if type(width) is not int or type(height) is not int or width <= 0 or height <= 0:
        raise ValueError("Fit size must be positive integers.")
    context.staging.mkdir(parents=True, exist_ok=True)
    target = context.staging / "fitted.png"
    with Image.open(source) as original:
        image = original.convert("RGB")
    scale = max(width / image.width, height / image.height)
    resized = image.resize(
        (round(image.width * scale), round(image.height * scale)), Image.Resampling.LANCZOS
    )
    left = (resized.width - width) // 2
    top = (resized.height - height) // 2
    resized.crop((left, top, left + width, top + height)).save(target)
    return [("fitted", target, "image")]


def _portrait_image(path):
    with Image.open(path) as original:
        image = ImageOps.exif_transpose(original).convert("RGBA")
    matte = Image.new("RGB", image.size, (196, 196, 196))
    matte.paste(image, mask=image.getchannel("A"))
    return matte


def portrait_crop(context: LocalContext) -> list[tuple[str, Path, str]]:
    spec = context.snapshot["step_spec"]
    image = _portrait_image(context.paths[spec["target"]])
    x1, y1, x2, y2 = spec["box"]
    if x2 <= x1 or y2 <= y1:
        raise ValueError("Face crop requires a positive face box.")
    cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
    side = max(1, round(max(x2 - x1, y2 - y1) * 2.5))
    left, top = round(cx - side / 2), round(cy - side / 2)
    # Keep equal scale and the face centred even when the square exceeds the picture.
    square = Image.new("RGB", (side, side), (196, 196, 196))
    square.paste(image, (-left, -top))
    context.staging.mkdir(parents=True, exist_ok=True)
    path = context.staging / "face.png"
    square.resize((512, 512), Image.Resampling.LANCZOS).save(path)
    normalized = context.staging / "target.png"
    image.save(normalized)
    return [("crop", path, "visual_evidence"), ("normalized", normalized, "visual_evidence")]


def portrait_sheet(context: LocalContext) -> list[tuple[str, Path, str]]:
    spec = context.snapshot["step_spec"]
    images = []
    for key in ("target", "front", "three_quarter", "profile"):
        images.append(_portrait_image(context.paths[spec[key]]))
    boxes = portrait_sheet_layout(images[0].size, spec["width"], spec["height"], spec["gap"])
    sheet = Image.new("RGB", (spec["width"], spec["height"]), tuple(spec["gap_rgb"]))
    for image, (x, y, w, h) in zip(images, boxes, strict=True):
        sheet.paste(image.resize((w, h), Image.Resampling.LANCZOS), (x, y))
    context.staging.mkdir(parents=True, exist_ok=True)
    path = context.staging / "face-sheet.png"
    sheet.save(path)
    return [("sheet", path, "image")]
