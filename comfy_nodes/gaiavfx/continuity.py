"""Continuity's H3 masked seam, and the trim to synchronized delivered video and audio.

Adapted from roadmaus/ComfyUI-Continuity, commit
00704a413a29fa556a28ce9c8d753422e7b197b4: creator/families/h3/encode.py
(_context_run, _masked_prefix, _seam_audio), creator/families/h3/payload.py
(audio position repair) and timeline.py (trim).
Copyright (c) 2026 roadmaus. MIT; see licenses/continuity-LICENSE.

The source video latent is protected in the target, while source audio is
decoded and encoded again as an end-aligned reference. This is not paired
audio masking. The application's conditioning owns all prompt/reference content.
Only single-sampler, unmodified, same-canvas H3 AV continuations are supported.
No external package code, compiler, settings, routes or UI is imported.
"""

import json
import logging
from typing import Any

SOURCE_COMMIT = "00704a413a29fa556a28ce9c8d753422e7b197b4"
ADAPTER_VERSION = "continuity-masked-v1"
AUDIO_END_KEY = "minimax_creator_audio_end_frame"
WRAPPER_KEY = "gaiavfx_continuity_audio_end"
_LAYOUT_DONE = "_gaiavfx_continuity_audio_positioned"
CONTEXT_FRAMES = (5, 22, 39, 56)
_LOG = logging.getLogger(__name__)


def _frame_count(steps: int) -> int:
    if steps < 2 or (steps - 2) % 5:
        raise ValueError("Continuity requires an original H3 17n+5-frame sampler latent.")
    return 5 + ((steps - 2) // 5) * 17


def _whole(value: Any, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or int(value) != value:
        raise ValueError(f"{label} must be a whole number.")
    return int(value)


def _context_steps(context_length: int) -> int:
    context_length = _whole(context_length, "context_length")
    if context_length not in CONTEXT_FRAMES:
        raise ValueError(f"Continuity context_length must be one of {CONTEXT_FRAMES}.")
    return 2 + ((context_length - 5) // 17) * 5


def normalize_av(latent: dict, label: str = "latent"):
    """Accept Comfy's nested AV pair and Motion Context's saved plain-list pair."""
    import torch

    if not isinstance(latent, dict) or "samples" not in latent:
        raise ValueError(f"{label} must contain an H3 AV samples pair.")
    samples = latent["samples"]
    pair = samples.unbind() if getattr(samples, "is_nested", False) else samples
    if not isinstance(pair, (list, tuple)) or len(pair) != 2:
        raise ValueError(f"{label} must have both original video and audio streams.")
    video, audio = pair
    if not all(isinstance(stream, torch.Tensor) for stream in pair):
        raise ValueError(f"{label} must contain tensors.")
    if video.ndim != 5 or video.shape[:2] != (1, 24):
        raise ValueError(f"{label} video must be [1,24,T,H,W].")
    if audio.ndim != 4 or audio.shape[:3] != (1, 32, 2):
        raise ValueError(f"{label} audio must be [1,32,2,T].")
    if min(video.shape[-2:]) < 2 or any(size % 2 for size in video.shape[-2:]):
        raise ValueError(f"{label} canvas must be divisible by 32 pixels.")
    frames = _frame_count(int(video.shape[2]))
    if audio.shape[-1] != round(frames * 40 / 24):
        raise ValueError(f"{label} audio length does not match the H3 40 Hz AV clock.")
    if not all(stream.is_floating_point() for stream in pair):
        raise ValueError(f"{label} streams must be floating point.")
    if not all(bool(torch.isfinite(stream).all()) for stream in pair):
        raise ValueError(f"{label} streams must contain finite values.")
    return video, audio


def _validate_conditioning(positive):
    if not isinstance(positive, (list, tuple)) or not positive:
        raise ValueError("Continuity needs existing H3 positive conditioning.")
    for entry in positive:
        if not isinstance(entry, (list, tuple)) or len(entry) != 2 or not isinstance(entry[1], dict):
            raise ValueError("Malformed H3 conditioning entry.")
        data = entry[1]
        if data.get("minimax_keyframes") or data.get("control") is not None:
            raise ValueError("Continuity masked v1 does not combine with guides or ControlNet.")
        for ref in data.get("minimax_refs") or []:
            if not isinstance(ref, dict) or AUDIO_END_KEY in ref:
                raise ValueError("Continuity cannot stack another continuation audio seam.")
    return positive


def masked_prefix(latent: dict, context_latent: dict, context_length: int):
    """Copy a phase-aligned original-video tail into a fresh target under a binary mask."""
    import torch
    from comfy.nested_tensor import NestedTensor

    steps = _context_steps(context_length)
    video, audio = normalize_av(latent, "target latent")
    source_video, _ = normalize_av(context_latent, "context latent")
    if source_video.shape[-2:] != video.shape[-2:]:
        raise ValueError("Continuity requires the same source and target canvas; no pixel fallback.")
    if source_video.shape[2] < steps:
        raise ValueError("Source latent is shorter than the requested continuation context.")
    if video.shape[2] <= steps:
        raise ValueError("The protected context cannot occupy the entire target clip.")
    if latent.get("noise_mask") is not None:
        raise ValueError("Continuity masked v1 does not combine with an existing noise mask.")
    if bool(torch.count_nonzero(video)) or bool(torch.count_nonzero(audio)):
        raise ValueError("Continuity requires a fresh empty target AV latent.")
    copied = video.clone()
    copied[:, :, :steps] = source_video[:, :, -steps:].to(video)
    video_mask = torch.ones_like(video)
    video_mask[:, :, :steps] = 0
    return {
        **latent,
        "samples": NestedTensor((copied, audio)),
        "noise_mask": NestedTensor((video_mask, torch.ones_like(audio))),
    }


def _audio_tail_reference(audio_vae, source_pair, context_length: int):
    import torch
    from comfy.nested_tensor import NestedTensor
    from comfy_extras.nodes_audio import vae_decode_audio
    from comfy_extras.nodes_minimax_h3 import _encode_ref_audio

    # GaiaH3AudioVAE supplies the same crop_input=False VAE used for normal H3
    # audio. The decode includes core's output attenuation, exactly as
    # Continuity's reel/spill path does before it reads the tail.
    if getattr(audio_vae, "audio_sample_rate", None) != 32000:
        raise ValueError("Continuity requires the H3 audio VAE at its 32 kHz input rate.")
    decoded = vae_decode_audio(audio_vae, {"samples": NestedTensor(source_pair)})
    waveform = decoded["waveform"]
    rate = _whole(decoded["sample_rate"], "audio sample rate")
    if rate <= 0 or waveform.ndim != 3 or waveform.shape[:2] != (1, 2) or not waveform.shape[-1]:
        raise ValueError("H3 source audio decode must return nonempty stereo audio.")
    if not bool(torch.isfinite(waveform).all()):
        raise ValueError("H3 source audio decode returned nonfinite samples.")
    requested = round(context_length / 24 * rate)
    count = min(int(waveform.shape[-1]), requested)
    encoded, ticks = _encode_ref_audio(
        audio_vae, {"waveform": waveform[..., -count:], "sample_rate": rate}
    )
    if encoded.ndim != 4 or encoded.shape[:3] != (1, 32, 2) or ticks <= 0:
        raise ValueError("H3 seam audio encode returned an unsupported shape.")
    if not bool(torch.isfinite(encoded).all()):
        raise ValueError("H3 seam audio encode returned nonfinite values.")
    return {
        "kind": "audio",
        "ref_audio_t": int(ticks),
        "audio_latent": encoded,
        AUDIO_END_KEY: int(context_length),
    }, {"audio_tail_samples": count, "audio_sample_rate": rate, "audio_reference_ticks": int(ticks)}


def reposition_audio(layout, payload):
    """Adaptation of upstream payload._reposition, restricted to audio seams."""
    import torch
    from comfy.ldm.minimax.model import FRAME_RESCALE

    if getattr(layout, _LAYOUT_DONE, False):
        return
    if payload.get("keyframes"):
        raise RuntimeError("Continuity masked v1 cannot reposition a payload containing guides.")
    segments = layout.segments
    if not segments or segments[-1][2] != "video" or segments[-1][1] <= segments[-1][0]:
        raise RuntimeError("H3 layout changed: target video must be the final nonempty segment.")
    origin = float(layout.position_ids[segments[-1][0], 0])
    audio_refs = [
        ref for ref in payload.get("refs") or []
        if ref["kind"] in ("audio", "video_audio") and ref["ref_audio_t"] > 0
    ]
    audio_segments = [(first, last) for first, last, kind in segments if kind == "ref_audio"]
    if len(audio_refs) != len(audio_segments):
        raise RuntimeError("H3 layout changed: audio references do not match packed audio rows.")
    for ref, (first, last) in zip(audio_refs, audio_segments):
        if AUDIO_END_KEY not in ref:
            continue
        ticks = int(ref["ref_audio_t"])
        if last - first != ticks * 2:
            raise RuntimeError("H3 layout changed: a stereo audio reference must have 2*T rows.")
        start = origin + FRAME_RESCALE * float(ref[AUDIO_END_KEY]) - ticks
        times = start + torch.arange(ticks, dtype=layout.position_ids.dtype,
                                      device=layout.position_ids.device)
        layout.position_ids[first:last, 0] = times.repeat(2)
    setattr(layout, _LAYOUT_DONE, True)


def _audio_wrapper(executor, *args, **kwargs):
    payload = kwargs.get("minimax_payload")
    if payload and any(AUDIO_END_KEY in ref for ref in payload.get("refs") or []):
        layout = payload.get("layout")
        if len(args) < 3 or len(args[0]) != 2:
            raise RuntimeError("H3 diffusion calling convention changed for the continuity seam.")
        video, audio = args[0]
        expected = (
            args[2].shape[1], video.shape[2],
            (video.shape[3] + 1) // 2 * 2, (video.shape[4] + 1) // 2 * 2, audio.shape[-1],
        )
        if layout is None or layout.signature != expected:
            raise RuntimeError("H3 seam layout is missing or mismatched; refusing stock audio positions.")
        reposition_audio(layout, payload)
    return executor(*args, **kwargs)


def _patch_audio(model):
    from comfy.patcher_extension import WrappersMP

    patched = model.clone()
    # Unlike add_wrapper_with_key alone (which appends), this is idempotent.
    patched.remove_wrappers_with_key(WrappersMP.DIFFUSION_MODEL, WRAPPER_KEY)
    patched.add_wrapper_with_key(WrappersMP.DIFFUSION_MODEL, WRAPPER_KEY, _audio_wrapper)
    return patched


class GaiaH3ContinuityMasked:
    @classmethod
    def INPUT_TYPES(cls):
        return {"required": {
            "model": ("MODEL",), "positive": ("CONDITIONING",), "latent": ("LATENT",),
            "context_latent": ("LATENT",), "audio_vae": ("VAE",),
            "context_length": ("INT", {"default": 22, "min": 5, "max": 56, "step": 17}),
            "fps": ("FLOAT", {"default": 24.0, "min": 24.0, "max": 24.0}),
        }}

    RETURN_TYPES = ("MODEL", "CONDITIONING", "LATENT", "INT")
    RETURN_NAMES = ("model", "positive", "latent", "trim_frames")
    FUNCTION = "prepare"
    CATEGORY = "GAIAVFX/H3/Community"
    DESCRIPTION = (
        "Continuity 00704a4: original video-latent masked prefix, decoded/reencoded "
        "end-aligned audio reference. Same canvas, 24 fps, no guides or other masks."
    )

    def prepare(self, model, positive, latent, context_latent, audio_vae,
                context_length=22, fps=24.0):
        if fps != 24.0:
            raise ValueError("Continuity H3 continuation requires 24 fps.")
        _context_steps(context_length)
        _validate_conditioning(positive)
        masked = masked_prefix(latent, context_latent, context_length)
        pair = normalize_av(context_latent, "context latent")
        seam, receipt = _audio_tail_reference(audio_vae, pair, int(context_length))
        conditioned = [
            [tokens, {**metadata, "minimax_refs": [*(metadata.get("minimax_refs") or []), seam]}]
            for tokens, metadata in positive
        ]
        frames = _frame_count(masked["samples"].unbind()[0].shape[2])
        masked["gaiavfx_continuity"] = {
            "adapter": ADAPTER_VERSION, "source_commit": SOURCE_COMMIT,
            "video_handoff": "original_latent_masked", "audio_handoff": "decoded_reencoded_reference",
            "raw_frames": frames, "trim_frames": int(context_length),
            "delivered_frames": frames - int(context_length), "fps": 24, **receipt,
        }
        patched = _patch_audio(model)
        _LOG.info("GAIAVFX H3 Continuity %s", json.dumps(masked["gaiavfx_continuity"], sort_keys=True))
        return patched, conditioned, masked, int(context_length)


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

