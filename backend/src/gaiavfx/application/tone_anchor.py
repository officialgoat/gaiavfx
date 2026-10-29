"""Policy-frozen, source-anchored tone correction on delivery pixels only."""

import numpy as np

LUMA = np.array([0.299, 0.587, 0.114], np.float32)


def _trimmed(values, trim):
    lo, hi = np.quantile(values, [trim, 1 - trim])
    kept = values[(values >= lo) & (values <= hi)]
    return kept if kept.size else values


def agreeing(raw, film, background, *, width=0.08, bins=0.02):
    """Background pixels whose render/film luma log-ratio sits in the dominant mode.

    The source mask cannot know where the *target* performer reaches (longer hair, a
    bulkier body), so those pixels hide inside the "background". The true background
    reproduces the film up to the drift, which makes a tight peak in the log-ratio
    histogram; changed content scatters. Clipped film pixels carry no exposure evidence.
    """
    r = raw[background].astype(np.float32) @ LUMA
    f = film[background].astype(np.float32) @ LUMA
    usable = (f > 12) & (f < 243)
    ratio = np.log((r + 8) / (f + 8))
    edges = np.arange(-1.5, 1.5 + bins, bins)
    counts, _ = np.histogram(ratio[usable], edges)
    counts = np.convolve(counts, np.ones(5) / 5, mode="same")
    peak = edges[int(np.argmax(counts))] + bins / 2
    keep = usable & (np.abs(ratio - peak) < width)
    selected = np.zeros_like(background)
    selected[background] = keep
    return selected


def fit_frame(
    raw, film, background, policy, *, trim=0.02, mode="gain", robust=True, agreeing_fractions=None
):
    """Per-channel (gain, offset) mapping ``raw`` to ``film`` on ``background`` pixels.

    ``mode`` "gain" matches trimmed means with a gain only (exposure and white balance);
    "affine" also matches the trimmed spread. ``robust`` keeps only the pixels that agree
    (``agreeing``). Returns None when too few pixels.
    """
    if robust:
        background = agreeing(raw, film, background)
        if agreeing_fractions is not None:
            agreeing_fractions.append(float(background.mean()))
        if int(background.sum()) < max(
            policy.min_pixels, policy.min_agreeing_fraction * background.size
        ):
            return None
    elif int(background.sum()) < policy.min_pixels:
        return None
    r = raw[background].astype(np.float32)
    f = film[background].astype(np.float32)
    gain, offset = np.ones(3, np.float32), np.zeros(3, np.float32)
    for c in range(3):
        rc, fc = _trimmed(r[:, c], trim), _trimmed(f[:, c], trim)
        if mode == "affine":
            g = float(np.std(fc)) / max(float(np.std(rc)), 1e-3)
            gain[c] = g
            offset[c] = float(np.mean(fc)) - g * float(np.mean(rc))
        else:
            gain[c] = float(np.mean(fc)) / max(float(np.mean(rc)), 1e-3)
    return gain, offset


def smooth_curve(values, window):
    """Centred moving average along axis 0, edges padded with the nearest value."""
    values = np.asarray(values, np.float32)
    if window <= 1 or len(values) < 2:
        return values
    half = window // 2
    padded = np.concatenate(
        [np.repeat(values[:1], half, 0), values, np.repeat(values[-1:], half, 0)]
    )
    kernel = np.ones(2 * half + 1, np.float32) / (2 * half + 1)
    return np.stack(
        [np.convolve(padded[:, c], kernel, mode="valid") for c in range(values.shape[1])],
        axis=1,
    )


def fill_missing(curve, neutral):
    """Replace None rows by the nearest fitted row (or ``neutral`` if none was fitted)."""
    known = [i for i, v in enumerate(curve) if v is not None]
    if not known:
        return np.stack([np.asarray(neutral, np.float32)] * len(curve))
    out = []
    for i, v in enumerate(curve):
        if v is None:
            v = curve[min(known, key=lambda k: abs(k - i))]
        out.append(np.asarray(v, np.float32))
    return np.stack(out)


def tone_curves(
    raw,
    film,
    background,
    policy,
    *,
    mode="gain",
    agreeing_fractions=None,
    predecessor=None,
    report=None,
):
    """Smoothed per-frame (gain, offset) curves for ``len(film)`` frames.

    ``raw`` may be longer than ``film`` (a padded render window): the extra frames reuse
    the last fitted value.
    """
    smooth, clamp = policy.smooth_frames, (policy.gain_min, policy.gain_max)
    fractions = []
    fits = [
        fit_frame(r, f, b, policy, mode=mode, agreeing_fractions=fractions)
        for r, f, b in zip(raw, film, background)
    ]
    if agreeing_fractions is not None:
        agreeing_fractions.extend(fractions)
    gains = fill_missing([None if x is None else x[0] for x in fits], np.ones(3))
    offsets = fill_missing([None if x is None else x[1] for x in fits], np.zeros(3))
    # Smooth in log space so a gain and its inverse weigh alike.
    gains = np.exp(smooth_curve(np.log(np.clip(gains, *clamp)), smooth))
    offsets = smooth_curve(np.clip(offsets, -40, 40), smooth)
    threshold = policy.min_window_agreeing_fraction
    if threshold is None:
        threshold = policy.min_agreeing_fraction
    fitted = any(fit is not None for fit in fits) and np.median(fractions) >= threshold
    previous_decision = (predecessor or {}).get("decision")
    decision = "fitted" if fitted else "neutral"
    if previous_decision == "neutral":
        decision = "neutral"
    elif previous_decision in {"fitted", "inherited"} and not fitted:
        decision = "inherited"
        gains[:] = predecessor["final_gain"]
        offsets[:] = 0
    if decision == "neutral":
        gains[:], offsets[:] = 1.0, 0.0
    if len(raw) > len(gains):
        extra = len(raw) - len(gains)
        gains = np.concatenate([gains, np.repeat(gains[-1:], extra, 0)])
        offsets = np.concatenate([offsets, np.repeat(offsets[-1:], extra, 0)])
    if report is not None:
        report.update(
            decision=decision,
            unfitted_frames=sum(fit is None for fit in fits),
            final_gain=gains[-1].tolist(),
            reason=f"Tone anchor: {decision}; median agreeing fraction "
            f"{float(np.median(fractions)):.4f}, window threshold {threshold:g}.",
        )
    return gains, offsets


def apply_curves(raw, gains, offsets):
    """Apply per-frame curves to uint8 frames, rounding once."""
    out = raw.astype(np.float32) * gains[:, None, None, :] + offsets[:, None, None, :]
    np.rint(out, out=out)
    return np.clip(out, 0, 255).astype(np.uint8)


def tone_anchor(raw, film, background, policy, **kwargs):
    """Corrected frames and the curves used (see ``tone_curves``)."""
    gains, offsets = tone_curves(raw, film, background, policy, **kwargs)
    return apply_curves(raw, gains, offsets), gains, offsets


def background_mask(plan, facts, performer, protected, policy):
    """Canvas background inside the active film, excluding dilated performer/protection."""
    from gaiavfx.application.replacement_steps import crop, dilate

    x0, y0, x1, y1 = 0, 0, *facts.native
    if facts.letterbox:
        x0, y0 = facts.letterbox.offset
        w, h = facts.letterbox.fit
        x1, y1 = x0 + w, y0 + h
    xs = (np.arange(plan.canvas[0]) + 0.5) / plan.scale + plan.box.x0
    ys = (np.arange(plan.canvas[1]) + 0.5) / plan.scale + plan.box.y0
    margin = 2 / plan.scale
    inside = (
        (ys[:, None] >= y0 + margin)
        & (ys[:, None] < y1 - margin)
        & (xs[None, :] >= x0 + margin)
        & (xs[None, :] < x1 - margin)
    )
    pixels = policy.dilate_fraction * float(np.hypot(*plan.canvas))
    return np.stack(
        [
            inside & ~dilate(crop((p | q).astype(np.uint8) * 255, plan) > 127, pixels)
            for p, q in zip(performer, protected, strict=True)
        ]
    )
