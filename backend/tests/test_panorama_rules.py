"""Panoramas' model-free rules: the ERP geometry the stage engine shares, closing the seam and growing a picture."""

import importlib.util
import math
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

from gaiavfx.modules.panoramas import erp, plate, seam
from gaiavfx.modules.panoramas.ask import DescribedPlace, Landmark, PicturedPlace

_spec = importlib.util.spec_from_file_location(
    "engine_erp", Path(__file__).parents[2] / "engine_workers/worldbuilder_splat/erp.py"
)
engine_erp = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(engine_erp)


def scene(width=512, height=256, seed=3):
    """A textured panorama whose joined edges do not meet, as a generator leaves them."""
    rng = np.random.default_rng(seed)
    x = np.linspace(0, 1, width)[None, :, None]
    y = np.linspace(0, 1, height)[:, None, None]
    base = 40 + 160 * x * np.array([1.0, 0.6, 0.3]) + 50 * y
    noise = rng.integers(0, 12, (height, width, 3))
    return np.clip(base + noise, 0, 255).astype(np.uint8)


def test_the_app_and_the_stage_engine_share_one_geometry():
    picture = scene(160, 90, seed=1)
    panorama = scene(256, 128, seed=2)
    assert np.array_equal(
        erp.project_to_erp(picture, 70, 256, 128), engine_erp.project_to_erp(picture, 70, 256, 128)
    )
    for yaw in (0.0, 135.0, 270.0):
        assert np.array_equal(
            erp.crop_view(panorama, yaw, 75, 96, 54),
            engine_erp.crop_view(panorama, yaw, 75, 96, 54),
        )
    assert np.array_equal(erp.band_mask(256, 128), engine_erp.band_mask(256, 128))
    assert np.array_equal(
        erp.plate_mask(256, 128, 70, 160, 90), engine_erp.plate_mask(256, 128, 70, 160, 90)
    )


def test_the_band_input_puts_the_seam_in_the_middle_painted_green():
    raw = scene()
    band = seam.band_input(raw)
    middle = band[:, raw.shape[1] // 2]
    assert (middle == (0, 255, 0)).all()
    assert np.array_equal(band[:, 0], raw[:, raw.shape[1] // 2])
    assert 0.04 < erp.green_fraction(band) < 0.05


def test_closing_the_seam_changes_only_the_band_and_reports_it():
    raw = scene()
    # The editor answers at its own size, with continuous scenery where the band was and a slight tone drift.
    rolled = erp.roll_half(raw).astype(np.int16)
    smooth = np.linspace(rolled[:, 230], rolled[:, 282], 512, axis=1)
    rolled[:, 230:282] = smooth[:, 230:282].astype(np.int16)
    fill = np.clip(rolled + 6, 0, 255).astype(np.uint8)
    fill = np.asarray(Image.fromarray(fill).resize((384, 192)))
    merged, report = seam.close(raw, fill)
    outside = erp.roll_half(erp.band_mask(512, 256)) == 0
    assert np.array_equal(merged[outside], raw[outside])
    assert report["outside_identical"] and report["clean"]
    assert report["band_px"] == 24 and report["feather_px"] == 6
    assert report["edge_step_after"] < report["edge_step_before"]


def test_a_fill_that_leaves_green_is_not_clean():
    raw = scene()
    _, report = seam.close(raw, seam.band_input(raw))
    assert report["green_in_band"] > 0.5 and not report["clean"]


def test_a_merge_that_would_touch_the_outside_is_refused(monkeypatch):
    raw = scene()
    monkeypatch.setattr(seam.erp, "composite_band", lambda base, repaint, *a: repaint)
    with pytest.raises(seam.SeamChanged):
        seam.close(raw, raw[::-1])


def test_a_picture_is_measured_cropped_and_kept_exactly_in_its_footprint():
    picture = np.zeros((90, 160, 3), np.uint8)
    picture[10:80] = scene(160, 70, seed=4)  # letterbox bars above and below
    cropped, report = plate.crop_letterbox(picture)
    assert cropped.shape == (70, 160, 3) and report["kept_rows"] == [10, 79]
    focal = 0.5 / math.tan(math.radians(35))  # MoGe's normalised intrinsics for a 70-degree lens
    assert plate.hfov_from_intrinsics([[focal, 0, 0.5], [0, focal, 0.5], [0, 0, 1]]) == 70.0
    assert plate.hfov_from_intrinsics([[0.05, 0, 0.5], [0, 0.05, 0.5], [0, 0, 1]]) is None
    canvas = plate.canvas(cropped, 70, 512, 256)
    assert 0.9 < erp.green_fraction(canvas) < 0.99
    filled = np.where(canvas == (0, 255, 0), 90, canvas).astype(np.uint8)
    filled = np.clip(filled.astype(np.int16) + 15, 0, 255).astype(np.uint8)  # the editor drifted
    restored, check = plate.restore(filled, cropped, 70, 512, 256, erode=2, feather=3)
    core = erp.plate_mask(512, 256, 70, 160, 70, erode=6)
    projected = erp.project_to_erp(cropped, 70, 512, 256)
    assert np.array_equal(restored[core], projected[core])
    assert check["plate_psnr"] < 30 and check["green"] == 0.0
    assert check["heading"] == 0.0 and check["hfov"] == 70.0 and 0 < check["vfov"] < 70


def test_requests_speak_about_the_place():
    with pytest.raises(ValueError, match="heading"):
        Landmark("Door", 360)
    with pytest.raises(ValueError, match="one heading"):
        DescribedPlace("A hall.", (Landmark("Door", 0), Landmark("Stair", 0)))
    with pytest.raises(ValueError, match="at most"):
        DescribedPlace("A hall.", tuple(Landmark(f"Thing {n}", n * 10) for n in range(9)))
    with pytest.raises(ValueError, match="field of view"):
        PicturedPlace("asset-1", hfov=150)
    assert PicturedPlace("asset-1").hfov is None
