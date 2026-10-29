"""Power Shift sigma schedule for the H3 Quality preset, maintained in GAIAVFX.

The schedule picks sigmas from the model's own discrete sigma table. Position
i/steps is warped by `midpoint_shift`, mapped through (1 - x**power)**power to
a fraction of the table, rounded to the nearest table index, and repeated
indices are dropped. A final 0.0 ends the schedule. It matches the recorded
Silveroxide Power Shift output bit for bit (tests/test_power_shift.py) without
loading that external code at run time.
"""

import numpy
import torch


def power_shift_sigmas(table, steps, power, midpoint_shift):
    top = len(table) - 1
    position = numpy.linspace(0.0, 1.0, steps, endpoint=False) ** midpoint_shift
    index = numpy.minimum(
        numpy.rint((1.0 - position**power) ** power * top), top
    ).astype(int)
    first = numpy.concatenate(([True], index[1:] != index[:-1]))
    chosen = [float(table[i]) for i in index[first]]
    return torch.tensor([*chosen, 0.0], dtype=torch.float32)


class GaiaH3PowerShift:
    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "model": ("MODEL",),
                "steps": ("INT", {"default": 20, "min": 1, "max": 1000}),
                "power": ("FLOAT", {"default": 2.0, "min": 0.01, "max": 10}),
                "midpoint_shift": ("FLOAT", {"default": 1.0, "min": 0.01, "max": 10}),
            }
        }

    RETURN_TYPES = ("SIGMAS",)
    FUNCTION = "schedule"
    CATEGORY = "GAIAVFX/H3"

    def schedule(self, model, steps, power, midpoint_shift):
        table = model.get_model_object("model_sampling").sigmas
        return (power_shift_sigmas(table, steps, power, midpoint_shift),)
