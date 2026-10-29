"""The maintained Power Shift schedule matches the pinned Silveroxide output bit for bit.

Run with COMFYUI_PATH pointing to the pinned experiment core, using Comfy's Python:
    python tests/test_power_shift.py
The recorded schedules are checked without external source code.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
COMFY = os.environ.get("COMFYUI_PATH")
if not COMFY or not (Path(COMFY) / "comfy" / "model_sampling.py").is_file():
    raise unittest.SkipTest("Set COMFYUI_PATH to the pinned H3 ComfyUI runtime.")
sys.dont_write_bytecode = True
sys.path.insert(0, COMFY)
for overlay in os.environ.get("COMFYUI_PYTHON_OVERLAY", "").split(os.pathsep):
    if overlay:
        sys.path.insert(0, overlay)
ARGV, sys.argv = sys.argv[:], [sys.argv[0], "--cpu"]
import comfy.cli_args

comfy.cli_args.args.cpu = True
import torch
from comfy.model_sampling import ModelSamplingAV

spec = importlib.util.spec_from_file_location(
    "gaiavfx_power_shift", ROOT / "comfy_nodes/gaiavfx/power_shift.py"
)
power_shift = importlib.util.module_from_spec(spec)
spec.loader.exec_module(power_shift)

# Recorded from the pinned upstream scheduler on this core (power-schedule-parity.json).
RECORDED = {
    (20, 2.0, 1.0): [
        1.0,
        0.9995814561843872,
        0.998302161693573,
        0.9961792826652527,
        0.9929994344711304,
        0.9886587858200073,
        0.9829837679862976,
        0.9757128357887268,
        0.9664613604545593,
        0.9544772505760193,
        0.9392465353012085,
        0.9193015694618225,
        0.892922043800354,
        0.8575096726417542,
        0.8090932369232178,
        0.7403598427772522,
        0.6419753432273865,
        0.5037674903869629,
        0.3155650198459625,
        0.10810810327529907,
        0.0,
    ],
    (4, 2.0, 1.0): [
        1.0,
        0.9886587858200073,
        0.9392465353012085,
        0.7403598427772522,
        0.0,
    ],
    (4, 1.25, 0.9): [
        1.0,
        0.9722676277160645,
        0.9125101566314697,
        0.7511679530143738,
        0.0,
    ],
}


def h3_sampling():
    sampling = ModelSamplingAV()
    sampling.set_parameters(shift=12.0, audio_shift=3.0)
    return sampling


class Model:
    def __init__(self, sampling):
        self.sampling = sampling

    def get_model_object(self, name):
        assert name == "model_sampling"
        return self.sampling




class PowerShiftTests(unittest.TestCase):
    def test_recorded_quality_and_turbo_schedules_are_bit_exact(self):
        node = power_shift.GaiaH3PowerShift()
        for (steps, power, midpoint), expected in RECORDED.items():
            (sigmas,) = node.schedule(Model(h3_sampling()), steps, power, midpoint)
            self.assertEqual(sigmas.dtype, torch.float32)
            self.assertTrue(
                torch.equal(sigmas, torch.tensor(expected, dtype=torch.float32))
            )

    def test_schedule_is_strictly_decreasing_and_ends_at_zero(self):
        table = h3_sampling().sigmas
        for steps in (1, 2, 4, 8, 20, 50, 200, 1000):
            sigmas = power_shift.power_shift_sigmas(table, steps, 2.0, 1.0)
            self.assertEqual(float(sigmas[0]), float(table[-1]))
            self.assertEqual(float(sigmas[-1]), 0.0)
            self.assertTrue(bool((sigmas[1:] < sigmas[:-1]).all()))
            self.assertLessEqual(len(sigmas), steps + 1)



if __name__ == "__main__":
    unittest.main(argv=ARGV, verbosity=2)
