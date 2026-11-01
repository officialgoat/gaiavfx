"""Model-free pixel operations of a replacement, in NumPy.

How pixels move between the film's native frame and a model's canvas: Lanczos resampling with the
research pixel-centre affine (crop, uncrop), dilated masks, feathered alpha and composites, the guide's
inverted performer and masked canvas, bounded evidence frames, and the checks that read pixels alone
(letterbox bars, cuts, mask bounds). No media, model or library access: replacement_steps decodes and
encodes around them.
"""

import math

import numpy as np

from gaiavfx.modules.recast.replacement import Box, Letterbox
from gaiavfx.shared.errors import ApplicationError


def dilate(mask, pixels):
    pixels = math.ceil(pixels)
    result = mask.copy()
    for axis in (-2, -1):
        source = result
        result = source.copy()
        for offset in range(1, pixels + 1):
            left, right = [slice(None)] * source.ndim, [slice(None)] * source.ndim
            left[axis], right[axis] = slice(offset, None), slice(None, -offset)
            result[tuple(left)] |= source[tuple(right)]
            result[tuple(right)] |= source[tuple(left)]
    return result


def mask_box(mask):
    ys, xs = np.nonzero(mask.any(axis=0) if mask.ndim == 3 else mask)
    return (
        Box(float(xs.min()), float(ys.min()), float(xs.max() + 1), float(ys.max() + 1))
        if len(xs)
        else None
    )


def detect_letterbox(frames):
    # Only contiguous, persistently near-black outer bars; never remove a dark interior.
    dark = frames.max(axis=(0, 3)) <= 3
    rows, cols = dark.all(axis=1), dark.all(axis=0)
    height, width = dark.shape
    if rows.all():
        return None
    y0, y1 = np.flatnonzero(~rows)[[0, -1]]
    x0, x1 = np.flatnonzero(~cols)[[0, -1]]
    if (x0, y0, x1 + 1, y1 + 1) == (0, 0, width, height):
        return None
    return Letterbox((int(x1 - x0 + 1), int(y1 - y0 + 1)), (int(x0), int(y0)), (width, height))


def cut_check(frames):
    # Adaptive discontinuity check: 3x neighbourhood and minimum 15/255 content change,
    # the research detector's documented thresholds. RGB residual, not an identity claim.
    small = frames[:, ::4, ::4].astype(np.float32)
    values = np.abs(np.diff(small, axis=0)).mean(axis=(1, 2, 3))
    cuts = []
    for i, value in enumerate(values):
        neighbours = [values[j] for j in range(max(0, i - 2), min(len(values), i + 3)) if j != i]
        if value >= 15 and value > 3 * max(1, float(np.mean(neighbours)) if neighbours else 1):
            cuts.append(i + 1)
    if cuts:
        raise ApplicationError(f"The requested shot contains a cut at relative frames {cuts}.", 422)


def _axis_weights(coordinates, size):
    indices = np.floor(coordinates).astype(int)[:, None] + np.arange(-3, 5)
    distance = coordinates[:, None] - indices
    weights = np.sinc(distance) * np.sinc(distance / 4)
    weights[np.abs(distance) >= 4] = 0
    weights /= weights.sum(axis=1, keepdims=True)
    if size == 1:
        indices[:] = 0
    else:
        indices %= 2 * (size - 1)
        indices = np.minimum(indices, 2 * (size - 1) - indices)
    return indices, weights.astype(np.float32)


def resample(frame, xs, ys):
    """Separable Lanczos-4, reflect-101 border, research pixel-centre affine."""
    xi, xw = _axis_weights(np.asarray(xs), frame.shape[1])
    yi, yw = _axis_weights(np.asarray(ys), frame.shape[0])
    scalar = frame.ndim == 2
    src = frame[..., None] if scalar else frame
    # The eight taps are float32 products added in order, k = 0..7, into reused buffers: the same arithmetic, and so
    # the same bytes, as summing one temporary array per tap.
    gathered = np.empty((src.shape[0], len(xi), src.shape[2]), np.uint8)
    tap = np.empty(gathered.shape, np.float32)
    horizontal = np.zeros(gathered.shape, np.float32)
    for k in range(8):
        np.take(src, xi[:, k], axis=1, out=gathered)
        np.multiply(gathered, xw[None, :, k, None], out=tap)
        horizontal += tap
    rows = np.empty((len(yi), *horizontal.shape[1:]), np.float32)
    result = np.zeros(rows.shape, np.float32)
    for k in range(8):
        np.take(horizontal, yi[:, k], axis=0, out=rows)
        rows *= yw[:, k, None, None]
        result += rows
    result = np.clip(np.rint(result, out=result), 0, 255).astype(np.uint8)
    return result[..., 0] if scalar else result


def crop(frame, plan):
    return resample(
        frame,
        (np.arange(plan.canvas[0]) + 0.5) / plan.scale - 0.5 + plan.box.x0,
        (np.arange(plan.canvas[1]) + 0.5) / plan.scale - 0.5 + plan.box.y0,
    )


def uncrop(frame, plan, native):
    return resample(
        frame,
        plan.scale * (np.arange(native[0]) + 0.5 - plan.box.x0) - 0.5,
        plan.scale * (np.arange(native[1]) + 0.5 - plan.box.y0) - 0.5,
    )


def alpha_map(plan, native):
    x, y = np.arange(native[0]) + 0.5, np.arange(native[1]) + 0.5
    ax = ((x > plan.box.x0) & (x < plan.box.x1)).astype(float)
    ay = ((y > plan.box.y0) & (y < plan.box.y1)).astype(float)
    for edge, distance, alpha in (
        ("left", x - plan.box.x0, ax),
        ("right", plan.box.x1 - x, ax),
        ("top", y - plan.box.y0, ay),
        ("bottom", plan.box.y1 - y, ay),
    ):
        if edge in plan.interior_edges and plan.feather:
            alpha *= 0.5 - 0.5 * np.cos(np.pi * np.clip(distance / plan.feather, 0, 1))
    return np.outer(ay, ax)


def head_mask(mask, square):
    """Intersect the source performer mask with the stabilised native head square."""
    height, width = mask.shape
    x, y = np.arange(width) + 0.5, np.arange(height) + 0.5
    return (
        mask
        & ((x >= square[0]) & (x < square[2]))[None, :]
        & ((y >= square[1]) & (y < square[3]))[:, None]
    )


def head_alpha(mask, square, plan, policy):
    """SAM head region, native dilation, and a finite integer box-blur feather.

    Exact zero outside the blur support and exact one inside protect parent pixels.
    """
    region = head_mask(mask, square)
    height, width = mask.shape
    region = dilate(region, policy.mask_dilate_px)
    radius = policy.mask_feather_px
    if radius:
        size = 2 * radius + 1
        counts = np.pad(region.astype(np.int64), radius)
        integral = np.pad(counts, ((1, 0), (1, 0))).cumsum(0).cumsum(1)
        counts = (
            integral[size:, size:]
            - integral[:-size, size:]
            - integral[size:, :-size]
            + integral[:-size, :-size]
        )
        alpha = counts / (size * size)
    else:
        alpha = region.astype(float)
    return alpha * alpha_map(plan, (width, height))


def paste(original, generated, alpha):
    out = np.rint(original * (1 - alpha[..., None]) + generated * alpha[..., None]).astype(np.uint8)
    out[alpha == 0] = original[alpha == 0]
    out[alpha == 1] = generated[alpha == 1]
    return out


def invert_performer(frame, performer, protected):
    out = frame.copy()
    invert = performer & ~protected
    out[invert] = 255 - out[invert]
    return out


def prepare_crop(frame, plan, performer, protected, treatment):
    """Invert a hard mask on the resampled canvas, as in research suite_v2."""
    prepared = crop(frame, plan)
    if treatment == "inverted":
        selected = crop((performer & ~protected).astype(np.uint8) * 255, plan) > 127
        prepared[selected] = 255 - prepared[selected]
    return prepared


def mask_canvas(prepared, frame, plan, arm, facts):
    if arm.crop is None:
        valid = np.ones(frame.shape[:2], np.uint8) * 255
        if arm.framing == "active" and facts.letterbox:
            valid[:] = 0
            x, y = facts.letterbox.offset
            w, h = facts.letterbox.fit
            valid[y : y + h, x : x + w] = 255
        xs = (np.arange(plan.canvas[0]) + 0.5) / plan.scale + plan.box.x0
        ys = (np.arange(plan.canvas[1]) + 0.5) / plan.scale + plan.box.y0
        inside = (
            (ys[:, None] >= 0)
            & (ys[:, None] < frame.shape[0])
            & (xs[None, :] >= 0)
            & (xs[None, :] < frame.shape[1])
        )
        prepared[~inside | (crop(valid, plan) < 128)] = 0
    return prepared


def measurement_pixels(frames, *, mask=False):
    """Bound diagnostic evidence to 896x512, without upscaling or softening masks."""
    from PIL import Image

    height, width = frames.shape[1:3]
    scale = min(1.0, 896 / width, 512 / height)
    size = (max(1, round(width * scale)), max(1, round(height * scale)))
    if size == (width, height):
        return frames
    method = Image.Resampling.NEAREST if mask else Image.Resampling.LANCZOS
    return np.stack([np.asarray(Image.fromarray(frame).resize(size, method)) for frame in frames])
