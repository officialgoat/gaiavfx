"""A panorama operation's deterministic local steps (`operations.STEPS`), around its ComfyUI jobs: the picture cut
free of letterbox bars, placed on the green canvas at its field of view, the seam band prepared (and, for a picture,
the picture's own pixels put back) and the seam closed. Each step's parameters are its `snapshot["step_spec"]`; the
rules are `modules/panoramas`'."""

import json
from pathlib import Path

import numpy as np
from PIL import Image

from gaiavfx.application.operation_steps import LocalContext
from gaiavfx.modules.panoramas import plate, seam


def _read(path: Path) -> np.ndarray:
    with Image.open(path) as image:
        return np.asarray(image.convert("RGB"))


def _image(context: LocalContext, name: str, array: np.ndarray) -> Path:
    context.staging.mkdir(parents=True, exist_ok=True)
    target = context.staging / name
    Image.fromarray(array).save(target)
    return target


def _report(context: LocalContext, name: str, document: dict) -> Path:
    context.staging.mkdir(parents=True, exist_ok=True)
    target = context.staging / name
    target.write_text(json.dumps(document, allow_nan=False), encoding="utf-8")
    return target


def _band(spec: dict) -> seam.SeamBand:
    settings = spec["seam"]
    return seam.SeamBand(
        float(settings["fraction"]),
        float(settings["feather"]),
        float(settings["ring"]),
        float(settings["green_max"]),
    )


def panorama_plate(context: LocalContext) -> list[tuple[str, Path, str]]:
    """The user's picture without letterbox bars: what the panorama keeps."""
    spec = context.snapshot["step_spec"]
    cropped, report = plate.crop_letterbox(
        _read(context.paths[spec["picture"]]), int(spec["threshold"])
    )
    return [
        ("plate", _image(context, "plate.png", cropped), "image"),
        ("letterbox", _report(context, "letterbox.json", report), "analysis_report"),
    ]


def field_of_view(override, geometry: Path) -> tuple[float, str, float | None]:
    """The picture's horizontal field of view: the one a person set, else MoGe's measurement when believable, else
    the research plates' default; with where it came from and what MoGe measured."""
    measured = None
    with np.load(geometry, allow_pickle=False) as data:
        if "intrinsics" in data.files:
            measured = plate.hfov_from_intrinsics(data["intrinsics"])
    if override is not None:
        return float(override), "set", measured
    if measured is not None:
        return measured, "measured", measured
    return plate.DEFAULT_HFOV, "default", None


def panorama_canvas(context: LocalContext) -> list[tuple[str, Path, str]]:
    """The picture at heading 0 on the green panorama canvas, at its field of view (`field_of_view`)."""
    spec = context.snapshot["step_spec"]
    picture = _read(context.paths[spec["plate"]])
    chosen, source, measured = field_of_view(spec.get("hfov"), context.paths["geometry"])
    canvas = plate.canvas(picture, chosen, int(spec["width"]), int(spec["height"]))
    report = {"hfov": chosen, "source": source, "measured": measured}
    report |= plate.footprint(chosen, picture.shape[1], picture.shape[0])
    return [
        ("canvas", _image(context, "canvas.png", canvas), "image"),
        ("field", _report(context, "field.json", report), "analysis_report"),
    ]


def panorama_band(context: LocalContext) -> list[tuple[str, Path, str]]:
    """The seam band's input. A drawn panorama is used as it is; a grown one first gets the picture's own pixels back
    inside its footprint, and says how far the editor drifted on it."""
    spec = context.snapshot["step_spec"]
    outputs = []
    if spec["mode"] == "picture":
        picture = _read(context.paths[spec["plate"]])
        hfov, _, _ = field_of_view(
            spec.get("hfov"), context.paths["geometry"]
        )  # the canvas's own rule
        raw, check = plate.restore(
            _read(context.paths["fill"]),
            picture,
            hfov,
            int(spec["width"]),
            int(spec["height"]),
            int(spec["erode"]),
            int(spec["feather"]),
        )
        outputs += [
            ("raw", _image(context, "raw.png", raw), "image"),
            ("plate_check", _report(context, "plate-check.json", check), "analysis_report"),
        ]
    else:
        raw = _read(context.paths["raw"])
    band = seam.band_input(raw, _band(spec))
    return [*outputs, ("band", _image(context, "band.png", band), "image")]


def panorama_seam(context: LocalContext) -> list[tuple[str, Path, str]]:
    """The panorama with its seam closed (a candidate) and the check that proves nothing else moved."""
    spec = context.snapshot["step_spec"]
    closed, report = seam.close(
        _read(context.paths[spec["raw"]]), _read(context.paths[spec["fill"]]), _band(spec)
    )
    return [
        ("panorama", _image(context, "panorama.png", closed), "image"),
        ("seam", _report(context, "seam.json", report), "analysis_report"),
    ]
