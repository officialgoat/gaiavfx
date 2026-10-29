"""CPU continuity contracts using real ComfyUI tensors, layout and helpers; no weights or inference.

Run with ComfyUI's Python and COMFYUI_PATH set to its ComfyUI folder:
    python tests/test_community_continuity.py
Audio encode/decode is a deterministic fake VAE; core audio helpers are real.
Set GAIAVFX_CONTINUITY_ORIGINAL to the research adapter's continuity.py (sha256-pinned below)
for the exact parity checks against the reviewed original.
"""

import ast
import copy
import hashlib
import importlib.util
import json
import math
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMFY = os.environ.get("COMFYUI_PATH")
if not COMFY or not (Path(COMFY) / "comfy" / "model_base.py").is_file():
    raise unittest.SkipTest("Set COMFYUI_PATH to the H3 ComfyUI runtime.")
sys.dont_write_bytecode = True
sys.path.insert(0, COMFY)
ARGV, sys.argv = sys.argv, [sys.argv[0], "--cpu"]
import comfy.cli_args

comfy.cli_args.args.cpu = True
sys.argv = ARGV
import torch
from comfy.ldm.minimax.model import PackedLayout
from comfy.model_patcher import ModelPatcher
from comfy.nested_tensor import NestedTensor
from comfy.patcher_extension import WrappersMP

MODULE = ROOT / "comfy_nodes" / "gaiavfx" / "continuity.py"
spec = importlib.util.spec_from_file_location("continuity_contract_adapter", MODULE)
adapter = importlib.util.module_from_spec(spec)
spec.loader.exec_module(adapter)

# The masked adapter the continuity campaign measured (research repository commit 12a1918d).
ORIGINAL_SHA256 = "9ee1eb1f541627370706cd0e3397397f8622427351bc0a0c18ddef66b981876c"


def masked_node(module):
    """The original names its node and receipt for its own app; parity compares behaviour, not names."""
    return next(v for k, v in vars(module).items() if k.endswith("H3ContinuityMasked"))


def receipt(latent):
    return next(v for k, v in latent.items() if k.endswith("_continuity"))


def original_adapter():
    path = os.environ.get("GAIAVFX_CONTINUITY_ORIGINAL")
    if not path or not Path(path).is_file():
        raise unittest.SkipTest("Set GAIAVFX_CONTINUITY_ORIGINAL for the parity checks.")
    if hashlib.sha256(Path(path).read_bytes()).hexdigest() != ORIGINAL_SHA256:
        raise AssertionError("The original adapter differs from the reviewed pinned file.")
    loaded = importlib.util.spec_from_file_location("continuity_original_adapter", path)
    module = importlib.util.module_from_spec(loaded)
    loaded.loader.exec_module(module)
    return module


class AudioVAE:
    """Deterministic stereo VAE stub; tests core audio normalization and tail selection."""
    audio_sample_rate = 32000
    crop_input = False

    def __init__(self):
        self.decoded_latent = None
        self.encoded_waveform = None
        self.decode_calls = 0

    def decode(self, latent):
        self.decode_calls += 1
        self.decoded_latent = latent
        ticks = latent.mean(dim=(1, 2))
        samples = ticks.repeat_interleave(800, dim=-1)
        return torch.stack((samples, samples * 0.5), dim=-1)

    def encode(self, waveform):
        self.encoded_waveform = waveform.clone()
        ticks = math.ceil(waveform.shape[1] / 800)
        return waveform.mean().expand(1, 32, 2, ticks).clone()


def latent(video_steps=22, height=2, width=4, plain=False, filled=False):
    frames = 5 + ((video_steps - 2) // 5) * 17
    video = torch.zeros(1, 24, video_steps, height, width)
    audio = torch.zeros(1, 32, 2, round(frames * 40 / 24))
    if filled:
        video = torch.arange(video.numel(), dtype=torch.float32).reshape_as(video) / 10000
        audio = torch.linspace(-0.1, 0.1, audio.numel()).reshape_as(audio)
    return {"samples": [video, audio] if plain else NestedTensor((video, audio))}


class MaskedTests(unittest.TestCase):
    def setUp(self):
        self.target = latent()
        self.source = latent(plain=True, filled=True)
        self.vae = AudioVAE()
        self.model = ModelPatcher(torch.nn.Identity(), torch.device("cpu"), torch.device("cpu"))
        self.tokens = torch.randn(1, 8, 4)
        self.picture = {"kind": "image", "latent_h": 2, "latent_w": 4,
                        "latent": torch.randn(1, 24, 1, 2, 4)}
        self.tags = torch.arange(8)
        self.positive = [[self.tokens, {
            "minimax_refs": [self.picture], "minimax_token_tags": self.tags, "authored": "unchanged",
        }]]

    def arguments(self, **overrides):
        arguments = {"model": self.model, "positive": self.positive, "latent": self.target,
                     "context_latent": self.source, "audio_vae": self.vae,
                     "context_length": 22, "fps": 24}
        arguments.update(overrides)
        return arguments

    def prepare(self, **overrides):
        return adapter.GaiaH3ContinuityMasked().prepare(**self.arguments(**overrides))

    def unsupported(self):
        return [
            {"context_latent": latent(height=4, plain=True)},
            {"context_length": 21}, {"context_length": True}, {"fps": 25},
            {"context_length": 22, "latent": latent(video_steps=7)},
            {"context_length": 56, "context_latent": latent(video_steps=12, plain=True)},
            {"context_latent": {"samples": [self.source["samples"][0]]}},
            {"positive": [[self.tokens, {"minimax_keyframes": [{"resolved_frame_index": 0}]}]]},
            {"positive": [[self.tokens, {"control": object()}]]},
            {"latent": {**self.target, "noise_mask": torch.ones(1)}},
        ]

    def test_normalizes_saved_and_nested_without_mutating_originals(self):
        video, audio = adapter.normalize_av(self.source)
        self.assertIs(video, self.source["samples"][0])
        nested = {**self.source, "samples": NestedTensor(self.source["samples"])}
        nv, na = adapter.normalize_av(nested)
        self.assertIs(nv, video)
        self.assertIs(na, audio)
        self.assertIsInstance(self.source["samples"], list)

    def test_masked_prefix_retains_exact_tail_and_audio_unmodified(self):
        _, _, result, trim = self.prepare()
        video, audio = result["samples"].unbind()
        vm, am = result["noise_mask"].unbind()
        self.assertTrue(torch.equal(video[:, :, :7], self.source["samples"][0][:, :, -7:]))
        self.assertEqual(int(torch.count_nonzero(video[:, :, 7:])), 0)
        self.assertEqual(int(torch.count_nonzero(vm[:, :, :7])), 0)
        self.assertTrue(torch.all(vm[:, :, 7:] == 1))
        self.assertTrue(torch.all(am == 1))
        self.assertIs(audio, self.target["samples"].unbind()[1])
        self.assertEqual(int(torch.count_nonzero(self.target["samples"].unbind()[0])), 0)
        self.assertNotIn("noise_mask", self.target)
        self.assertEqual(trim, 22)
        self.assertEqual(result["gaiavfx_continuity"]["delivered_frames"], 51)

    def test_every_context_length_protects_its_latent_steps(self):
        for frames, steps in zip(adapter.CONTEXT_FRAMES, (2, 7, 12, 17)):
            with self.subTest(frames=frames):
                _, _, result, trim = self.prepare(context_length=frames, audio_vae=AudioVAE())
                video, _ = result["samples"].unbind()
                vm, _ = result["noise_mask"].unbind()
                self.assertTrue(torch.equal(video[:, :, :steps],
                                            self.source["samples"][0][:, :, -steps:]))
                self.assertEqual(int(torch.count_nonzero(vm[:, :, :steps])), 0)
                self.assertTrue(torch.all(vm[:, :, steps:] == 1))
                self.assertEqual(trim, frames)
                self.assertEqual(result["gaiavfx_continuity"]["delivered_frames"], 73 - frames)

    def test_preserves_prompt_tags_reference_identity_and_metadata(self):
        _, positive, _, _ = self.prepare()
        self.assertIs(positive[0][0], self.tokens)
        self.assertIs(positive[0][1]["minimax_token_tags"], self.tags)
        self.assertIs(positive[0][1]["minimax_refs"][0], self.picture)
        self.assertEqual(positive[0][1]["authored"], "unchanged")
        self.assertEqual(len(self.positive[0][1]["minimax_refs"]), 1)
        self.assertEqual(len(positive[0][1]["minimax_refs"]), 2)

    def test_audio_is_decoded_then_reencoded_and_end_aligned(self):
        _, positive, result, _ = self.prepare()
        seam = positive[0][1]["minimax_refs"][-1]
        self.assertIs(self.vae.decoded_latent, self.source["samples"][1])
        self.assertEqual(self.vae.decode_calls, 1)
        self.assertEqual(self.vae.encoded_waveform.shape, (1, round(22 / 24 * 32000), 2))
        ticks = self.source["samples"][1].mean(dim=(1, 2)).repeat_interleave(800, dim=-1)
        expected = torch.stack((ticks, ticks * 0.5), dim=-1)[:, -29333:]
        self.assertTrue(torch.equal(self.vae.encoded_waveform, expected))
        self.assertEqual(seam[adapter.AUDIO_END_KEY], 22)
        self.assertEqual(seam["ref_audio_t"], 37)
        self.assertEqual(result["gaiavfx_continuity"]["audio_handoff"], "decoded_reencoded_reference")

    def test_audio_history_follows_the_picture_context(self):
        _, positive, _, _ = self.prepare(context_length=39)
        self.assertEqual(self.vae.encoded_waveform.shape, (1, 52000, 2))
        self.assertEqual(positive[0][1]["minimax_refs"][-1][adapter.AUDIO_END_KEY], 39)

    def test_wrapper_is_idempotent_and_preserves_source_model(self):
        patched, _, _, _ = self.prepare()
        again = adapter._patch_audio(patched)
        kind = WrappersMP.DIFFUSION_MODEL
        self.assertEqual(len(self.model.get_wrappers(kind, adapter.WRAPPER_KEY)), 0)
        self.assertEqual(len(patched.get_wrappers(kind, adapter.WRAPPER_KEY)), 1)
        self.assertEqual(len(again.get_wrappers(kind, adapter.WRAPPER_KEY)), 1)

    def test_wrapper_refuses_missing_or_mismatched_layout_before_forward(self):
        _, conditioned, result, _ = self.prepare()
        pair = result["samples"].unbind()
        payload = {"refs": conditioned[0][1]["minimax_refs"]}
        calls = []

        def forward(*args, **kwargs):
            calls.append(True)

        with self.assertRaisesRegex(RuntimeError, "layout"):
            adapter._audio_wrapper(forward, pair, torch.tensor([1.0]), self.tokens,
                                   minimax_payload=payload)
        payload["layout"] = PackedLayout(9, 22, 2, 4, 122, refs=payload["refs"])
        with self.assertRaisesRegex(RuntimeError, "layout"):
            adapter._audio_wrapper(forward, pair, torch.tensor([1.0]), self.tokens,
                                   minimax_payload=payload)
        self.assertFalse(calls)
        payload["layout"] = PackedLayout(8, 22, 2, 4, 122, refs=payload["refs"])
        adapter._audio_wrapper(forward, pair, torch.tensor([1.0]), self.tokens,
                               minimax_payload=payload)
        self.assertEqual(calls, [True])

    def test_unsupported_inputs_fail_before_audio_work(self):
        for arguments in self.unsupported():
            with self.subTest(arguments=tuple(arguments)), self.assertRaises(ValueError):
                self.prepare(**arguments)
        self.assertEqual(self.vae.decode_calls, 0)

    def test_nonfinite_wrong_clock_and_nonempty_target_rejected(self):
        broken = latent(plain=True)
        broken["samples"][0][0, 0, 0, 0, 0] = float("nan")
        with self.assertRaisesRegex(ValueError, "finite"):
            self.prepare(context_latent=broken)
        broken = latent(plain=True)
        broken["samples"][1] = broken["samples"][1][..., :-1]
        with self.assertRaisesRegex(ValueError, "clock"):
            self.prepare(context_latent=broken)
        with self.assertRaisesRegex(ValueError, "empty"):
            self.prepare(latent=latent(filled=True))
        self.assertEqual(self.vae.decode_calls, 0)


class ParityTests(unittest.TestCase):
    """The port equals the research adapter's masked node on identical inputs."""

    def setUp(self):
        self.original = original_adapter()
        self.case = MaskedTests()
        self.case.setUp()

    def both(self, **overrides):
        port_vae, original_vae = AudioVAE(), AudioVAE()
        port = adapter.GaiaH3ContinuityMasked().prepare(
            **self.case.arguments(audio_vae=port_vae, **copy.deepcopy(overrides)))
        original = masked_node(self.original)().prepare(
            **self.case.arguments(audio_vae=original_vae, **copy.deepcopy(overrides)))
        return port, original, port_vae, original_vae

    def test_identical_latents_masks_seams_and_receipts(self):
        for frames in adapter.CONTEXT_FRAMES:
            with self.subTest(frames=frames):
                port, original, port_vae, original_vae = self.both(context_length=frames)
                self.assertEqual(port[3], original[3])
                for key in ("samples", "noise_mask"):
                    for left, right in zip(port[2][key].unbind(), original[2][key].unbind()):
                        self.assertTrue(torch.equal(left, right), key)
                self.assertEqual(port[2]["gaiavfx_continuity"], receipt(original[2]))
                self.assertTrue(torch.equal(port_vae.encoded_waveform, original_vae.encoded_waveform))
                ours, theirs = port[1][0][1]["minimax_refs"], original[1][0][1]["minimax_refs"]
                self.assertEqual(len(ours), len(theirs))
                self.assertIs(ours[0], theirs[0])
                self.assertEqual(set(ours[-1]), set(theirs[-1]))
                self.assertTrue(torch.equal(ours[-1]["audio_latent"], theirs[-1]["audio_latent"]))
                for name in ("kind", "ref_audio_t", adapter.AUDIO_END_KEY):
                    self.assertEqual(ours[-1][name], theirs[-1][name])
                kind = WrappersMP.DIFFUSION_MODEL
                self.assertEqual(len(port[0].get_wrappers(kind, adapter.WRAPPER_KEY)), 1)
                self.assertEqual(len(original[0].get_wrappers(kind, self.original.WRAPPER_KEY)), 1)

    def test_identical_audio_positions_with_existing_refs(self):
        port, original, _, _ = self.both()
        old_sound = {"kind": "audio", "ref_audio_t": 3, "audio_latent": torch.zeros(1, 32, 2, 3)}
        positions = []
        for module, result in ((adapter, port), (self.original, original)):
            refs = [self.case.picture, old_sound, result[1][0][1]["minimax_refs"][-1]]
            layout = PackedLayout(8, 22, 2, 4, 122, refs=refs)
            before = layout.position_ids.clone()
            module.reposition_audio(layout, {"refs": refs})
            self.assertFalse(torch.equal(layout.position_ids, before))
            positions.append(layout.position_ids)
        self.assertTrue(torch.equal(*positions))

    def test_identical_refusals(self):
        for arguments in self.case.unsupported():
            with self.subTest(arguments=tuple(arguments)):
                messages = []
                for module in (adapter, self.original):
                    with self.assertRaises(ValueError) as caught:
                        masked_node(module)().prepare(**self.case.arguments(**arguments))
                    messages.append(str(caught.exception))
                self.assertEqual(*messages)


class TrimTests(unittest.TestCase):
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
