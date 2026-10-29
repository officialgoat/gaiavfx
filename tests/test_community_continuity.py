"""CPU continuation-trim contracts; use ComfyUI's Python with COMFYUI_PATH set."""

import ast
import importlib.util
import json
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMFY = os.environ.get("COMFYUI_PATH")
if not COMFY or not (Path(COMFY) / "comfy").is_dir():
    raise unittest.SkipTest("Set COMFYUI_PATH to the H3 ComfyUI runtime.")
sys.dont_write_bytecode = True
import torch

MODULE = ROOT / "comfy_nodes" / "gaiavfx" / "continuity.py"
spec = importlib.util.spec_from_file_location("continuity_contract_adapter", MODULE)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)


class ContinuityTests(unittest.TestCase):
    def test_trim_preserves_unique_frames_and_exact_audio_duration(self):
        images = torch.arange(73).float().view(73, 1, 1, 1).expand(73, 2, 4, 3).clone()
        audio = {"waveform": torch.arange(97600).float().view(1, 1, -1).expand(1, 2, -1).clone(),
                 "sample_rate": 32000}
        result, sound, receipt = adapter.GaiaH3ContinuityTrim().trim(images, audio, 22, 24)
        self.assertTrue(torch.equal(result, images[22:]))
        self.assertEqual(result.shape[0], 51)
        self.assertTrue(torch.equal(sound["waveform"], audio["waveform"][..., 29333:97333]))
        report = json.loads(receipt)
        self.assertEqual(report["audio_output_samples"], 68000)
        self.assertEqual(report["duration_seconds"], 2.125)
        self.assertEqual(report["audio_tail_discard_samples"], 267)


    def test_trim_pads_short_audio_at_end_only_and_rejects_invalid_frames(self):
        images = torch.zeros(73, 2, 4, 3)
        audio = {"waveform": torch.ones(1, 2, 97300), "sample_rate": 32000}
        _, sound, receipt = adapter.GaiaH3ContinuityTrim().trim(images, audio, 22)
        self.assertTrue(torch.all(sound["waveform"][..., :-33] == 1))
        self.assertTrue(torch.all(sound["waveform"][..., -33:] == 0))
        self.assertEqual(json.loads(receipt)["audio_pad_samples"], 33)
        with self.assertRaisesRegex(ValueError, "duration"):
            adapter.GaiaH3ContinuityTrim().trim(
                images, {**audio, "waveform": audio["waveform"][..., :70000]}, 22)
        with self.assertRaises(ValueError):
            adapter.GaiaH3ContinuityTrim().trim(images[:-1], audio, 22)
        with self.assertRaises(ValueError):
            adapter.GaiaH3ContinuityTrim().trim(images, audio, 73)


    def test_adapter_imports_no_external_app_or_compiler(self):
        tree = ast.parse(MODULE.read_text(encoding="utf-8"))
        imports = [
            node.module or "" for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
        ] + [
            name.name for node in ast.walk(tree) if isinstance(node, ast.Import) for name in node.names
        ]
        self.assertFalse(any(name.startswith(("creator", "gaiavfx.", "server", "aiohttp"))
                             for name in imports))



if __name__ == "__main__":
    unittest.main(verbosity=2)
