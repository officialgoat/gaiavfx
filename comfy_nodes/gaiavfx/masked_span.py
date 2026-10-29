"""Freeze video spans of a joint H3 AV latent, retaining its audio and masks."""

from __future__ import annotations

import logging

_LOG = logging.getLogger(__name__)
FRAME_PER_TOKEN = (1, 4, 4, 4, 4)


def _pair(latent, label):
    samples = latent["samples"] if isinstance(latent, dict) else latent
    pair = samples.unbind() if getattr(samples, "is_nested", False) else samples
    if isinstance(pair, (list, tuple)) and len(pair) == 2:
        return pair[0], pair[1]
    if hasattr(pair, "ndim") and pair.ndim == 5:
        return pair, None
    raise ValueError(f"{label} must be an H3 AV latent or a 5-D video latent.")


def span_mask(steps, head, tail, mode, source_steps=None):
    """Pure: which target steps are frozen, and from which source step each comes.

    Returns a list of (target_step, source_step) pairs.
    """
    if head < 0 or tail < 0 or head + tail >= steps:
        raise ValueError("Frozen spans must leave at least one generated step.")
    if mode == "tail_to_head":
        if tail:
            raise ValueError("tail_to_head freezes a head only.")
        if source_steps is None or source_steps < head:
            raise ValueError("The source latent is shorter than the frozen head.")
        start = source_steps - head
        if start % 5:
            raise ValueError("The source tail must start at a VAE cycle boundary.")
        return [(k, start + k) for k in range(head)]
    if mode == "aligned":
        if source_steps != steps:
            raise ValueError("An aligned source must have the target's step count.")
        return [(k, k) for k in range(head)] + [
            (k, k) for k in range(steps - tail, steps)
        ]
    raise ValueError(f"Unknown mode {mode}")


def validate_canvas(target_shape, source_shape):
    """Pure shape refusal, also used before copying a single tensor step."""
    if len(target_shape) != 5 or len(source_shape) not in {4, 5}:
        raise ValueError("Video latents must have five dimensions (unbatched source: four).")
    if len(source_shape) == 4:
        source_shape = (1, *source_shape)
    if source_shape[1] != target_shape[1] or source_shape[-2:] != target_shape[-2:]:
        raise ValueError("Source and target differ in channels or canvas; a latent cannot be resized.")


class GaiaH3MaskedSpan:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "latent": ("LATENT",),
                "source": ("LATENT",),
                "mode": (["tail_to_head", "aligned"],),
                "head_steps": ("INT", {"default": 7, "min": 0, "max": 64}),
                "tail_steps": ("INT", {"default": 0, "min": 0, "max": 64}),
            }
        }

    RETURN_TYPES = ("LATENT", "STRING")
    RETURN_NAMES = ("latent", "receipt")
    FUNCTION = "apply"
    CATEGORY = "GAIAVFX/H3"

    def apply(self, latent, source, mode, head_steps, tail_steps):
        import json

        video, audio = _pair(latent, "target latent")
        if audio is None:
            raise ValueError("The target must be a joint H3 AV latent.")
        src, _ = _pair(source, "source latent")
        if src.ndim == 4:
            src = src.unsqueeze(0)
        validate_canvas(tuple(video.shape), tuple(src.shape))
        pairs = span_mask(
            int(video.shape[2]),
            int(head_steps),
            int(tail_steps),
            mode,
            int(src.shape[2]),
        )
        import torch
        from comfy.nested_tensor import NestedTensor

        incoming = latent.get("noise_mask")
        if incoming is not None:
            video_mask, audio_mask = incoming.unbind()
            video_mask, audio_mask = video_mask.clone(), audio_mask.clone()
            if video_mask.shape != video.shape:
                video_mask = video_mask.expand_as(video).clone()
        else:
            video_mask, audio_mask = torch.ones_like(video), torch.ones_like(audio)
        copied = video.clone()
        src = src.to(copied)
        for target, origin in pairs:
            copied[:, :, target] = src[:, :, origin]
            video_mask[:, :, target] = 0
        receipt = {
            "mode": mode,
            "target_steps": int(video.shape[2]),
            "source_steps": int(src.shape[2]),
            "frozen_steps": [t for t, _ in pairs],
            "audio_mask_kept": incoming is not None,
        }
        _LOG.info("GaiaH3MaskedSpan %s", json.dumps(receipt))
        return (
            {
                **latent,
                "samples": NestedTensor((copied, audio)),
                "noise_mask": NestedTensor((video_mask, audio_mask)),
            },
            json.dumps(receipt),
        )


NODE_CLASS_MAPPINGS = {"GaiaH3MaskedSpan": GaiaH3MaskedSpan}
NODE_DISPLAY_NAME_MAPPINGS = {
    "GaiaH3MaskedSpan": "GAIAVFX H3 masked latent spans"
}
