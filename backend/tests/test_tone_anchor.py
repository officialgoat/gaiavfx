"""Delivery gains use the film, ignore changed subjects and abstain without evidence."""

from dataclasses import replace
from pathlib import Path

import numpy as np
import pytest

from gaiavfx.application import tone_anchor as ta
from gaiavfx.modules.direction.application import (
    default_replacement_policy,
    replacement_policy_text,
)
from gaiavfx.modules.direction.replacement import parse_replacement_policy

POLICY = default_replacement_policy().tone_anchor


def scene(frames=12, size=(96, 128), seed=0):
    rng = np.random.default_rng(seed)
    return np.repeat(rng.integers(40, 200, (1, *size, 3), dtype=np.uint8), frames, 0)


def test_gain_drift_removed_and_changed_performer_ignored():
    film = scene()
    raw = (film * np.linspace(1, 0.75, len(film))[:, None, None, None]).astype(np.uint8)
    raw[:, 10:60, 20:70] = 15
    fixed, gains, _ = ta.tone_anchor(
        raw, film, np.ones(film.shape[:3], bool), replace(POLICY, smooth_frames=1)
    )
    outside = np.ones(film.shape[1:3], bool)
    outside[10:60, 20:70] = False
    assert np.abs(fixed[:, outside].astype(float) - film[:, outside]).mean() < 2
    assert gains[0] @ ta.LUMA == pytest.approx(1, abs=0.03)
    assert gains[-1] @ ta.LUMA == pytest.approx(1 / 0.75, abs=0.05)


def test_matching_render_is_neutral():
    film = scene(seed=1)
    fixed, gains, _ = ta.tone_anchor(film, film, np.ones(film.shape[:3], bool), POLICY)
    assert np.allclose(gains, 1, atol=0.02)
    assert np.array_equal(fixed, film)


def test_padded_render_reuses_last_fit():
    film = scene(frames=6)
    raw = np.concatenate([film, film[-1:].repeat(4, 0)])
    _, gains, offsets = ta.tone_anchor(raw, film, np.ones(film.shape[:3], bool), POLICY)
    assert len(gains) == len(raw) == len(offsets)
    assert np.array_equal(gains[-1], gains[5])


def test_small_background_fills_between_fits_but_an_unfitted_window_is_neutral():
    film = scene(frames=3, size=(320, 320))
    background = np.ones(film.shape[:3], bool)
    background[1] = False
    background[1, :8] = True  # 2560 pixels, 2.5%; enough pixels but not enough area.
    _, gains, _ = ta.tone_anchor((film * 0.8).astype(np.uint8), film, background, POLICY)
    assert np.all(gains[1] > 1.1)
    assert np.max(np.abs(np.diff(gains, axis=0))) <= 0.01
    assert np.all(gains[0] > 1.1)
    film = scene(frames=3, size=(20, 20))
    _, gains, _ = ta.tone_anchor(film // 2, film, np.ones(film.shape[:3], bool), POLICY)
    assert np.array_equal(gains, np.ones_like(gains))


def old_policy():
    return parse_replacement_policy(
        (Path(__file__).parent / "fixtures/replacement/policy-before-batch5.toml").read_text()
    ).tone_anchor


@pytest.mark.parametrize("legacy", [False, True])
def test_window_median_decides_once_and_no_frame_resets(legacy):
    policy = old_policy() if legacy else replace(POLICY, min_window_agreeing_fraction=0.03)
    film = scene(frames=20, size=(320, 320))
    background = np.zeros(film.shape[:3], bool)
    for i, mask in enumerate(background):
        mask.flat[: round(mask.size * (0.029 if i % 2 else 0.031))] = True
    raw = (film * np.linspace(0.85, 0.82, len(film))[:, None, None, None]).astype(np.uint8)
    report = {}
    _, gains, _ = ta.tone_anchor(raw, film, background, policy, report=report)
    assert report["decision"] == "fitted"
    assert report["unfitted_frames"] == 10
    assert np.all(gains > 1.1)
    assert np.max(np.abs(np.diff(gains, axis=0))) <= 0.01
    # An old policy decides on the 3% median, a new default on 5%.
    _, gains, _ = ta.tone_anchor(raw, film, background, POLICY, report=report)
    assert report["decision"] == "neutral"
    assert np.array_equal(gains, np.ones_like(gains))
    background[0] = background[1]
    _, gains, _ = ta.tone_anchor(raw, film, background, policy, report=report)
    assert report["decision"] == "neutral"
    assert np.array_equal(gains, np.ones_like(gains))


@pytest.mark.parametrize(
    "previous,strong,decision,gain",
    [
        ("neutral", True, "neutral", 1.0),
        ("neutral", False, "neutral", 1.0),
        ("fitted", True, "fitted", 1.25),
        ("fitted", False, "inherited", 1.08),
        (None, True, "fitted", 1.25),
        (None, False, "neutral", 1.0),
    ],
)
def test_successor_follows_window_one(previous, strong, decision, gain):
    film = np.full((3, 96, 128, 3), 100, np.uint8)
    raw = np.full_like(film, 80)
    background = np.full(film.shape[:3], strong, bool)
    predecessor = (
        {"decision": previous, "final_gain": [1.08] * 3} if previous else {"applied": True}
    )
    report = {}
    _, gains, offsets = ta.tone_anchor(
        raw, film, background, POLICY, predecessor=predecessor, report=report
    )
    assert report["decision"] == decision
    assert gains == pytest.approx(np.full_like(gains, gain))
    assert report["final_gain"] == pytest.approx([gain] * 3)
    assert np.all(offsets == 0)


@pytest.mark.parametrize("smooth,tolerance", [(1, 0), (9, 0.02)])
def test_identical_overlaps_have_no_gain_step(smooth, tolerance):
    film = scene(frames=124)
    raw = (film * np.linspace(0.95, 0.8, len(film))[:, None, None, None]).astype(np.uint8)
    policy = replace(POLICY, smooth_frames=smooth)
    _, a, _ = ta.tone_anchor(raw[:73], film[:73], np.ones(film[:73].shape[:3], bool), policy)
    _, b, _ = ta.tone_anchor(raw[51:], film[51:], np.ones(film[51:].shape[:3], bool), policy)
    assert float(np.abs((a[-22:] - b[:22]) @ ta.LUMA).max()) <= tolerance


def test_policy_is_optional_closed_and_validated():
    text = replacement_policy_text()
    without = (
        text[: text.index("[tone_anchor]")]
        + text[text.index("\n[", text.index("[tone_anchor]") + 1) :]
    )
    assert parse_replacement_policy(without).tone_anchor is None
    for old, new in [
        ("smooth_frames = 9", "smooth_frames = 8"),
        ("gain_min = 0.7", "gain_min = 1.1"),
        ("min_pixels = 2000", "min_pixels = true"),
        ("dilate_fraction = 0.04", "dilate_fraction = nan"),
        ("[tone_anchor]", "[tone_anchor]\nunknown = 1"),
    ]:
        with pytest.raises(ValueError):
            parse_replacement_policy(text.replace(old, new))
