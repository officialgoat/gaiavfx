"""The maintained H3 LoRA loader applies exactly what core applies and names what it drops.

Run with COMFYUI_PATH pointing to the pinned experiment core, using Comfy's Python:
    python tests/test_lora_loader.py
"""

from __future__ import annotations

import importlib.util
import json
import logging
import os
import sys
import types
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMFY = os.environ.get("COMFYUI_PATH")
if not COMFY or not (Path(COMFY) / "comfy" / "lora.py").is_file():
    raise unittest.SkipTest("Set COMFYUI_PATH to the pinned H3 ComfyUI runtime.")
sys.dont_write_bytecode = True
sys.path.insert(0, COMFY)
for overlay in os.environ.get("COMFYUI_PYTHON_OVERLAY", "").split(os.pathsep):
    if overlay:
        sys.path.insert(0, overlay)
ARGV, sys.argv = sys.argv[:], [sys.argv[0], "--cpu"]
import comfy.cli_args

comfy.cli_args.args.cpu = True
import comfy.lora
import comfy.model_patcher
import torch

spec = importlib.util.spec_from_file_location(
    "gaiavfx_lora_loader", ROOT / "comfy_nodes/gaiavfx/lora_loader.py"
)
lora_loader = importlib.util.module_from_spec(spec)
spec.loader.exec_module(lora_loader)

CPU = torch.device("cpu")
QKV = "diffusion_model.blocks.0.attn.qkv_proj"
ADALN = "diffusion_model.blocks.0.adaln_proj.linear"
FINAL = "diffusion_model.final_layer.adaln_proj.linear"


class Block(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.attn = torch.nn.Module()
        self.attn.qkv_proj = torch.nn.Linear(4, 6, bias=False)
        self.adaln_proj = torch.nn.Module()
        # A pruned base: eight table inputs instead of the full hidden size.
        self.adaln_proj.linear = torch.nn.Linear(8, 12)


class Pruned(torch.nn.Module):
    def __init__(self):
        super().__init__()
        self.model_config = types.SimpleNamespace(unet_config={})
        self.diffusion_model = torch.nn.Module()
        self.diffusion_model.blocks = torch.nn.ModuleList([Block()])
        self.diffusion_model.final_layer = torch.nn.Module()
        self.diffusion_model.final_layer.adaln_proj = torch.nn.Module()
        self.diffusion_model.final_layer.adaln_proj.linear = torch.nn.Linear(8, 5)


def patcher():
    torch.manual_seed(7)
    return comfy.model_patcher.ModelPatcher(Pruned(), CPU, CPU)


def full_model_lora():
    """A LoRA trained on the full model: AdaLN deltas take 16 inputs, not 8."""
    torch.manual_seed(11)
    return {
        f"{QKV}.lora_A.weight": torch.randn(2, 4),
        f"{QKV}.lora_B.weight": torch.randn(6, 2),
        f"{ADALN}.lora_A.weight": torch.randn(3, 16),
        f"{ADALN}.lora_B.weight": torch.randn(12, 3),
        f"{FINAL}.lora_A.weight": torch.randn(3, 16),
        f"{FINAL}.lora_B.weight": torch.randn(5, 3),
    }


def patched_weight(model, key):
    weight = model.model.state_dict()[key].clone().float()
    return comfy.lora.calculate_weight(model.patches.get(key, []), weight, key)


class LoaderTests(unittest.TestCase):
    def test_applies_fitting_deltas_and_names_the_rest(self):
        base = patcher()
        lora = full_model_lora()
        patched, text = lora_loader.apply(base, lora, 0.8, name="demo.safetensors")
        report = json.loads(text)
        self.assertEqual((report["patches"], report["applied"], report["skipped"]), (3, 1, 2))
        self.assertEqual(
            report["skipped_groups"],
            {
                "diffusion_model.blocks.N.adaln_proj.linear.weight": 1,
                "diffusion_model.final_layer.adaln_proj.linear.weight": 1,
            },
        )
        self.assertIn("AdaLN", report["reason"])
        self.assertEqual(set(patched.patches), {f"{QKV}.weight"})
        expected = base.model.state_dict()[f"{QKV}.weight"] + 0.8 * (
            lora[f"{QKV}.lora_B.weight"] @ lora[f"{QKV}.lora_A.weight"]
        )
        torch.testing.assert_close(patched_weight(patched, f"{QKV}.weight"), expected)
        self.assertEqual(base.patches, {}, "the input model must stay unpatched")

    def test_matches_what_core_does_with_the_same_file(self):
        """Core patches every key, fails on the AdaLN ones and leaves them unchanged."""
        base = patcher()
        lora = full_model_lora()
        key_map = comfy.lora.model_lora_keys_unet(base.model, {})
        core = base.clone()
        core.add_patches(comfy.lora.load_lora(lora, key_map), 0.8)
        ours, _ = lora_loader.apply(base, lora, 0.8)
        logging.disable(logging.ERROR)
        try:
            for key in base.model.state_dict():
                torch.testing.assert_close(patched_weight(core, key), patched_weight(ours, key))
        finally:
            logging.disable(logging.NOTSET)

    def test_refuses_an_unexpected_skip_count(self):
        with self.assertRaisesRegex(ValueError, "2 deltas on this base; 51 were measured"):
            lora_loader.apply(patcher(), full_model_lora(), 1.0, expected_skipped=51)
        _, text = lora_loader.apply(patcher(), full_model_lora(), 1.0, expected_skipped=2)
        self.assertEqual(json.loads(text)["skipped"], 2)

    def test_a_lora_without_adaln_skips_nothing(self):
        lora = {k: v for k, v in full_model_lora().items() if "adaln" not in k}
        _, text = lora_loader.apply(patcher(), lora, 1.0, expected_skipped=0)
        report = json.loads(text)
        self.assertEqual((report["skipped"], report["reason"]), (0, ""))

    def test_zero_strength_applies_nothing(self):
        patched, text = lora_loader.apply(patcher(), full_model_lora(), 0.0)
        self.assertEqual((patched.patches, json.loads(text)["applied"]), ({}, 0))


if __name__ == "__main__":
    sys.argv = ARGV
    unittest.main()
