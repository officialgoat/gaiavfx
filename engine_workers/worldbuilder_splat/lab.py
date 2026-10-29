# Vendored from the maintained engine implementation on branch worldbuilder (27 October 2026) for the fill rounds (ADR 0020 §4);
# only the imports changed. The research module stays the reference; keep their numerics equal.
"""The research lab's helpers a fill round needs: filling a memory's holes, the H3 latent patches a render cannot be
trusted in, the anchor views and one still's geometric acceptance (`_consolidate_one`, R4 F3 and R5)."""

from __future__ import annotations

import json
from pathlib import Path


def _load_rgb(path: Path):
    import numpy as np
    from PIL import Image

    return np.asarray(Image.open(path).convert("RGB"))


def fill_holes(image, known, levels: int = 7):
    """Fill pixels where `known` is False from their surroundings by normalised convolution over an image pyramid
    (each hole takes the finest level that has support there), so no black or grey patch remains."""
    import numpy as np

    return np.clip(fill_values(image, known, levels), 0, 255).astype(np.uint8)


def fill_values(values, known, levels: int = 7):
    """fill_holes for float data with any channel count (H x W x C, or H x W), unclipped."""
    import numpy as np
    from PIL import Image

    values = np.asarray(values, dtype=np.float32)
    flat = values.ndim == 2
    if flat:
        values = values[..., None]
    h, w = known.shape
    weight = known.astype(np.float32)
    colour = values * weight[..., None]
    out = values.copy()
    todo = ~known
    for level in range(1, levels + 1):
        if not todo.any():
            break
        size = (max(1, w >> level), max(1, h >> level))

        def down_up(a, size=size):
            small = Image.fromarray(a).resize(size, Image.BOX)
            return np.asarray(small.resize((w, h), Image.BILINEAR), dtype=np.float32)

        wsum = down_up(weight)
        csum = np.stack(
            [
                down_up(np.ascontiguousarray(colour[..., c]))
                for c in range(values.shape[-1])
            ],
            -1,
        )
        ready = todo & (wsum > 1e-3)
        out[ready] = csum[ready] / wsum[ready][:, None]
        todo &= ~ready
    out[todo] = values[known].mean(0) if known.any() else 0
    return out[..., 0] if flat else out


def patch_holes(
    alpha,
    block: int = 32,
    hole_below: float = 0.5,
    hole_share: float = 0.02,
    mean_below: float = 0.75,
):
    """The H3 latent patches (`block` px, aligned to the frame's top-left) a splat render cannot be trusted in:
    more than `hole_share` of the patch under `hole_below` alpha (a real hole), or its mean alpha under
    `mean_below` (thin coverage). Scattered low-alpha speckle marks no patch, where a pixel threshold grown by a
    patch width covered 73% of a frame (H2 p090, 24 Oct). Returns a boolean mask at pixel resolution."""
    import numpy as np

    h, w = alpha.shape
    rows, cols = -(-h // block), -(-w // block)
    padded = np.ones((rows * block, cols * block), np.float32)
    padded[:h, :w] = alpha
    blocks = padded.reshape(rows, block, cols, block)
    marked = ((blocks < hole_below).mean((1, 3)) > hole_share) | (
        blocks.mean((1, 3)) < mean_below
    )
    return np.repeat(np.repeat(marked, block, 0), block, 1)[:h, :w]


def latent_frame_or(masks):
    """OR per-frame masks over the frames that share one H3 latent frame (the causal video VAE keeps frame 0
    alone, then packs 8 frames per latent frame), so a regenerated patch stays regenerated for its whole group: a
    mask that blinks inside a group left the Fun ControlNet's grey source showing (R2x m3b, 24 Oct)."""
    import numpy as np

    masks = np.asarray(masks, bool)
    out = masks.copy()
    for start in range(1, len(masks), 8):
        out[start : start + 8] = masks[start : start + 8].any(0)
    return out


def _anchor_views(keyframes, width=512, height=288):
    """Every geometry keyframe's own view, small: the eight panorama-centre crops of an equirectangular keyframe,
    a perspective keyframe's own camera (with its trusted pixels). Yields (viewmat, K, image, mask or None)."""
    import numpy as np
    from . import memory as mem
    from PIL import Image
    from .erp import crop_view

    for key in keyframes:
        if key.kind == "erp":
            for yaw in range(0, 360, 45):
                view, k = mem.probe_camera(key.centre, yaw, 75.0, width, height)
                yield view, k, crop_view(key.image, yaw, 75.0, width, height), None
            continue
        h, w = key.depth.shape
        k = key.k.copy()
        k[0] *= width / w
        k[1] *= height / h
        image = np.asarray(
            Image.fromarray(key.image).resize((width, height), Image.BILINEAR)
        )
        trust = (
            np.asarray(
                Image.fromarray(key.trust.astype(np.uint8) * 255).resize(
                    (width, height), Image.NEAREST
                )
            )
            > 127
        )
        yield key.viewmat, k, image, trust


def _consolidate_one(
    still,
    base: Path,
    moge: Path,
    keyframes,
    key: str,
    glass=None,
    tolerance: float = 0.05,
    contradiction: float = 0.1,
    anchor_psnr: float = 22.0,
    voxel=None,
    trusted_anchor: bool = False,
):
    """One destination still against the geometry keyframes (see `step_consolidate`). `base` holds the still's
    view.json, protected.png and region.png; `glass` (a mask) gets fitted planes. With `trusted_anchor` (R5), the
    anchor check compares only the pixels the still would supply as protected: a glass pane is never trusted, and
    its reflection naturally differs from what the panorama showed there (H1's window failed at 21.8 dB on 25 Oct).
    Returns the candidate keyframe (untrusted when rejected), the report and whether it was accepted."""
    import numpy as np
    from . import explore as ex
    from . import geometry as geo
    from . import memory as mem
    from PIL import Image

    view = json.loads((base / "view.json").read_text(encoding="utf-8"))
    viewmat, k = np.asarray(view["viewmat"]), np.asarray(view["K"])
    w, h = view["width"], view["height"]
    still = np.asarray(still)
    if still.shape[:2] != (h, w):
        still = np.asarray(Image.fromarray(still).resize((w, h), Image.LANCZOS))
    protected = _load_rgb(base / "protected.png")[..., 0] > 127
    region = _load_rgb(base / "region.png")[..., 0] > 127
    memory = mem.reproject(keyframes, viewmat, k, w, h)
    with np.load(moge) as data:
        depth_moge = np.squeeze(data["depth"]).astype(np.float32)
        valid = np.squeeze(data["mask"]) > 0.5
    if depth_moge.shape != (h, w):
        depth_moge = np.asarray(
            Image.fromarray(depth_moge).resize((w, h), Image.BILINEAR)
        )
        valid = np.asarray(Image.fromarray(valid).resize((w, h), Image.NEAREST))
    valid &= np.isfinite(depth_moge) & (depth_moge > 0)
    surface = np.isfinite(memory.depth)
    pick = ~region & surface & valid  # the pixels the merge kept exactly
    report = {"key": key, "aligned_pixels": int(pick.sum())}
    if pick.sum() < 500:
        raise RuntimeError(f"only {int(pick.sum())} kept pixels to align {key}")
    scale = float(np.median(memory.depth[pick] / depth_moge[pick]))
    aligned = scale * depth_moge
    residual = float(
        np.median(np.abs(aligned[pick] - memory.depth[pick]) / memory.depth[pick])
    )
    depth = np.where(protected & surface, memory.depth, aligned)

    space = ex.observed_space(keyframes, voxel=voxel)
    core = ex.free_core(space)
    invented = region & ~protected & valid
    if glass is not None:
        invented &= ~np.asarray(glass, dtype=bool)
    rays = geo.camera_rays(k, w, h)[invented] @ viewmat[:3, :3]
    centre = -viewmat[:3, :3].T @ viewmat[:3, 3]
    points = centre + rays * depth[invented][:, None]
    idx = space.index(points)
    inside = space.inside(idx)
    hits = np.zeros(len(points), bool)
    hits[inside] = core[tuple(idx[inside].T)]
    share = float(hits.mean()) if len(points) else 0.0

    candidate = mem.view_keyframe(key, still, viewmat, k, depth, valid, glass=glass)

    errors, shares = [], []
    for v, kk, truth, mask in _anchor_views(keyframes):
        seen = mem.reproject(
            [*keyframes, candidate], v, kk, truth.shape[1], truth.shape[0]
        )
        new = (seen.protected if trusted_anchor else seen.known) & (
            seen.source == len(keyframes)
        )
        if mask is not None:
            new &= mask
        errors.append(((seen.rgb[new] - truth[new].astype(float)) / 255) ** 2)
        shares.append(float(new.mean()))
    errors = np.concatenate(errors)
    supplied = float(np.mean(shares))
    agreement = (
        float(-10 * np.log10(max(float(errors.mean()), 1e-12))) if errors.size else None
    )
    accepted = (
        residual <= tolerance
        and share <= contradiction
        and (supplied < 0.005 or agreement >= anchor_psnr)
    )
    report.update(
        scale=round(scale, 4),
        residual=round(residual, 4),
        invented=round(float(invented.mean()), 4),
        contradiction=round(share, 4),
        anchor_supplied=round(supplied, 4),
        anchor_psnr=None if agreement is None else round(agreement, 2),
        glass=round(float(np.mean(glass)), 4) if glass is not None else 0.0,
        accepted=bool(accepted),
    )
    if not accepted:  # appearance only: never loaded as geometry, never protected
        candidate = mem.Keyframe(
            key,
            "pinhole",
            still,
            depth,
            candidate.valid,
            np.zeros_like(candidate.trust),
            viewmat,
            k,
            solid=np.zeros_like(candidate.trust),
        )
    return candidate, report, bool(accepted)

