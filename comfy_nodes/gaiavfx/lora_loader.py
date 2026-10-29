"""Apply a LoRA to an H3 model, dropping only the deltas that cannot fit their weight.

The pruned H3 bases replace the time embedder with a small table, so every
`adaln_proj.linear` weight takes eight inputs instead of the full model's 2688.
AdaLN deltas trained on the full model therefore cannot be added. Core ComfyUI
tries anyway, logs a reshape error for each layer on every step and skips them.
This loader makes that outcome explicit: it applies exactly the patches core
would apply and reports what it left out. A strict expected count guards against
running the adapter on a base it was not measured on.
"""

import json
import math
import re

import comfy.lora
import comfy.lora_convert

ADAPTER = "gaiavfx-lora-loader-v1"


def target_shape(tensor):
    """The logical shape of a model weight, including quantized weights."""
    params = getattr(tensor, "_params", None)
    shape = getattr(params, "orig_shape", None)
    return tuple(shape) if shape is not None else tuple(tensor.shape)


def delta_shape(patch):
    """The full shape a patch adds, or None when core decides by itself."""
    if isinstance(patch, tuple) and len(patch) == 2 and patch[0] == "diff":
        return tuple(patch[1][0].shape)
    if type(patch).__name__ != "LoRAAdapter":
        return None
    up, down, _alpha, mid, _dora, reshape = patch.weights
    if mid is not None or reshape is not None:
        return None
    return (int(up.shape[0]), int(down.flatten(start_dim=1).shape[1]))


def fits(patch, shape):
    """Core adds a LoRA delta with `reshape(weight.shape)`, so element counts decide."""
    delta = delta_shape(patch)
    if delta is None:
        return True
    if isinstance(patch, tuple):
        return delta == tuple(shape)
    return math.prod(delta) == math.prod(shape)


def group(name):
    return re.sub(r"\.\d+\.", ".N.", name)


def apply(model, lora, strength, expected_skipped=-1, name=""):
    """Return (patched model, JSON report)."""
    key_map = comfy.lora.model_lora_keys_unet(model.model, {})
    loaded = comfy.lora.load_lora(comfy.lora_convert.convert_lora(lora), key_map)
    state = model.model.state_dict()
    kept, skipped = {}, []
    for key, patch in loaded.items():
        weight = key if isinstance(key, str) else key[0]
        target = state.get(weight)
        if target is not None and not fits(patch, target_shape(target)):
            skipped.append(weight)
        else:
            kept[key] = patch
    if expected_skipped >= 0 and len(skipped) != expected_skipped:
        raise ValueError(
            f"{name or 'This LoRA'} leaves out {len(skipped)} deltas on this base; "
            f"{expected_skipped} were measured. Use the base it was qualified on."
        )
    patched = model.clone()
    applied = patched.add_patches(kept, strength) if strength != 0 else []
    groups = {}
    for weight in skipped:
        groups[group(weight)] = groups.get(group(weight), 0) + 1
    report = {
        "adapter": ADAPTER,
        "lora": name,
        "strength": strength,
        "patches": len(loaded),
        "applied": len(applied),
        "skipped": len(skipped),
        "skipped_groups": groups,
        "reason": "Delta cannot reshape to the model weight (pruned bases factorize AdaLN)."
        if skipped
        else "",
    }
    return patched, json.dumps(report, sort_keys=True)


class GaiaH3LoraLoader:
    @classmethod
    def INPUT_TYPES(cls):
        import folder_paths

        return {
            "required": {
                "model": ("MODEL",),
                "lora_name": (folder_paths.get_filename_list("loras"),),
                "strength_model": (
                    "FLOAT",
                    {"default": 1.0, "min": -100.0, "max": 100.0, "step": 0.01},
                ),
                "expected_skipped": ("INT", {"default": -1, "min": -1, "max": 100000}),
            }
        }

    RETURN_TYPES = ("MODEL", "STRING")
    RETURN_NAMES = ("model", "report")
    FUNCTION = "load"
    CATEGORY = "GAIAVFX/H3"

    def __init__(self):
        self.loaded = None

    def load(self, model, lora_name, strength_model, expected_skipped):
        import comfy.utils
        import folder_paths

        path = folder_paths.get_full_path_or_raise("loras", lora_name)
        if self.loaded is None or self.loaded[0] != path:
            self.loaded = (path, comfy.utils.load_torch_file(path, safe_load=True))
        return apply(model, self.loaded[1], strength_model, expected_skipped, lora_name)
