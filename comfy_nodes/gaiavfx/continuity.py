"""Trim an H3 continuation to synchronized delivered video and audio.

Adapted from roadmaus/ComfyUI-Continuity, commit
00704a413a29fa556a28ce9c8d753422e7b197b4 (timeline.py).
Copyright (c) 2026 roadmaus. MIT; see licenses/continuity-LICENSE.
"""

import json
from typing import Any

ADAPTER_VERSION = "continuity-masked-v1"
CONTEXT_FRAMES = (5, 22, 39, 56)


def _whole(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError(f"{label} must be a whole number.")
    return int(value)



class GaiaH3ContinuityTrim:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "images": ("IMAGE",), "audio": ("AUDIO",),
            "trim_frames": ("INT", {"default": 22, "min": 0, "max": 56}),
            "fps": ("FLOAT", {"default": 24.0, "min": 24.0, "max": 24.0}),
        }}

    RETURN_TYPES = ("IMAGE", "AUDIO", "STRING")
    RETURN_NAMES = ("images", "audio", "receipt")
    FUNCTION = "trim"
    CATEGORY = "GAIAVFX/H3/Community"

    def trim(self, images, audio, trim_frames=22, fps=24.0):
        import torch
        from torch.nn import functional

        trim_frames = _whole(trim_frames, "trim_frames")
        if fps != 24.0:
            raise ValueError("Continuity H3 output requires 24 fps.")
        if trim_frames and trim_frames not in CONTEXT_FRAMES:
            raise ValueError("Continuity trim must match a supported protected context.")
        if images.ndim != 4 or images.shape[-1] != 3 or images.shape[0] % 17 != 5:
            raise ValueError("Continuity trim requires the raw 17n+5-frame H3 RGB output.")
        if trim_frames < 0 or images.shape[0] <= trim_frames:
            raise ValueError("Continuity trim would remove the entire clip.")
        waveform = audio["waveform"]
        rate = _whole(audio["sample_rate"], "audio sample rate")
        if (waveform.ndim != 3 or waveform.shape[:2] != (1, 2)
                or rate <= 0 or not waveform.shape[-1]):
            raise ValueError("Continuity trim requires stereo H3 audio with a positive sample rate.")
        if not bool(torch.isfinite(waveform).all()):
            raise ValueError("Continuity audio contains nonfinite samples.")
        delivered = images[trim_frames:]
        start = round(trim_frames / fps * rate)
        wanted = round(int(delivered.shape[0]) / fps * rate)
        remaining = waveform[..., start:]
        have = int(remaining.shape[-1])
        if abs(have - wanted) > (rate + 39) // 40:
            raise ValueError("H3 audio duration differs from video by more than one latent tick.")
        # Continuity's mux holds each pass's audio to its video duration. Fit
        # once here so external video savers cannot invent another timing rule.
        fitted = functional.pad(remaining, (0, max(0, wanted - have)))[..., :wanted]
        receipt = {
            "adapter": ADAPTER_VERSION, "raw_frames": int(images.shape[0]),
            "trim_frames": trim_frames, "delivered_frames": int(delivered.shape[0]),
            "fps": fps, "duration_seconds": int(delivered.shape[0]) / fps,
            "audio_sample_rate": rate, "audio_trim_samples": start,
            "audio_output_samples": wanted, "audio_pad_samples": max(0, wanted - have),
            "audio_tail_discard_samples": max(0, have - wanted),
        }
        return delivered, {**audio, "waveform": fitted}, json.dumps(receipt, sort_keys=True)

