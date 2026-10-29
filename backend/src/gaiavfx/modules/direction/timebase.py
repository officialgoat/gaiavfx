"""Timebases that must never be mixed: editorial seconds, delivered frames, model-window frames and audio samples.

A model's `FrameGrid` converts between the first three. Its hard constraints (fps, the window-length rule and any
hard window maximum) are separate from its editable defaults and display recommendations. `SampleGrid` converts
between a film's frames and its audio samples.
"""

from dataclasses import dataclass
from fractions import Fraction


@dataclass(frozen=True)
class FrameGrid:
    fps: int
    length_base: int  # valid window lengths are length_base + k * length_step
    length_step: int
    context_default: int  # editable default: motion-context frames before the delivered clip
    max_window: int | None = None  # a hard limit, when the integration has one
    recommended_max_window: int | None = None  # display guidance, never a hard limit

    def __post_init__(self):
        if self.fps <= 0 or self.length_step <= 0 or self.length_base < 1:
            raise ValueError("a frame grid needs a positive fps, step and base")
        if self.max_window is not None and self.max_window < self.length_base:
            raise ValueError("the largest window is shorter than the smallest")
        if (
            self.recommended_max_window is not None
            and self.recommended_max_window < self.length_base
        ):
            raise ValueError("the recommended window is shorter than the smallest")
        if self.context_default < 0 or (
            self.max_window is not None and self.context_default >= self.max_window
        ):
            raise ValueError("the context default must fit inside a window")

    def frames(self, seconds: Fraction | int) -> int:
        """Editorial seconds to whole delivered frames, rounding up so no requested time is lost."""
        exact = Fraction(seconds) * self.fps
        return -(-exact.numerator // exact.denominator)

    def seconds(self, frames: int) -> Fraction:
        return Fraction(frames, self.fps)

    def window_frame(self, delivered: int, context: int) -> int:
        """A delivered frame's index inside the model window that begins with `context` frames of context."""
        if delivered < 0 or context < 0:
            raise ValueError("frames and context are never negative")
        return context + delivered

    def valid_window(self, frames: int) -> bool:
        return (
            self.length_base <= frames
            and (self.max_window is None or frames <= self.max_window)
            and (frames - self.length_base) % self.length_step == 0
        )

    def window_for(self, delivered: int, context: int) -> int:
        """The smallest valid window holding `context` frames of context plus `delivered` frames."""
        need = max(self.length_base, context + delivered)
        steps = -(-(need - self.length_base) // self.length_step)
        window = self.length_base + steps * self.length_step
        if self.max_window is not None and window > self.max_window:
            raise ValueError(
                f"{context} + {delivered} frames exceed the {self.max_window}-frame window"
            )
        return window


@dataclass(frozen=True)
class SampleGrid:
    """Audio samples against a film's frame rate (ADR 0016: line edges are integer samples at 48 kHz).

    Sample 0 is the instant of frame 0. Every conversion is exact `Fraction` arithmetic, so 29.97 fps (1601.6
    samples per frame) never multiplies a float: `first_sample(f)` is the first sample at or after frame f begins,
    `frame_of(s)` the frame a sample lies in, and `frame_of(first_sample(f)) == f`.
    """

    rate: int
    fps: Fraction

    def __post_init__(self):
        if type(self.rate) is not int or self.rate <= 0:
            raise ValueError("a sample grid needs a positive integer sample rate")
        fps = Fraction(self.fps)
        if fps <= 0:
            raise ValueError("a sample grid needs a positive frame rate")
        object.__setattr__(self, "fps", fps)

    @property
    def samples_per_frame(self) -> Fraction:
        return Fraction(self.rate) / self.fps

    def first_sample(self, frame: int) -> int:
        exact = frame * self.samples_per_frame
        return -(-exact.numerator // exact.denominator)

    def frame_of(self, sample: int) -> int:
        exact = Fraction(sample) * self.fps / self.rate
        return exact.numerator // exact.denominator

    def sample_of_seconds(self, seconds: Fraction | float) -> int:
        """Round half up on the exact value (a float is read as the binary value it holds)."""
        exact = Fraction(seconds) * self.rate + Fraction(1, 2)
        return exact.numerator // exact.denominator

    def seconds_of_sample(self, sample: int) -> Fraction:
        return Fraction(sample, self.rate)

    def total_samples(self, frame_count: int) -> int:
        """The samples of `frame_count` frames: where frame `frame_count` would begin."""
        return self.first_sample(frame_count)
