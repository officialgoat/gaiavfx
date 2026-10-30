"""One segment window, compiled once and lowered once per seed (ADR 0019).

`compile_window` binds the compiled prompt to the join's handoff: the context length comes from the handoff, so the
two cannot disagree. `lower` turns the window into a seed's graph, the files it uploads and what the job records:
the delivered frames it expects and which handoff the take used.
"""

from dataclasses import dataclass

from gaiavfx.integrations.minimax_h3.continuation import Handoff
from gaiavfx.integrations.minimax_h3.graph import build_graph, context_descriptor, render_presets
from gaiavfx.integrations.minimax_h3.prompt import CompiledH3, compile_prompt
from gaiavfx.modules.direction.segment import Keyframe, Picture, Segment
from gaiavfx.modules.direction.timebase import FrameGrid


@dataclass(frozen=True)
class Window:
    compiled: CompiledH3
    handoff: Handoff | None  # set exactly when the segment continues
    fps: int

    @property
    def continuation(self) -> dict | None:
        """Run evidence: which handoff this take used, and whether it followed the rule."""
        if self.handoff is None:
            return None
        return {
            "method": self.handoff.method,
            "chosen": self.handoff.explicit_method is not None,
            "recommended": self.handoff.recommended,
            "context_frames": self.handoff.context_frames,
            "audio_context_frames": self.handoff.audio_context_frames,
            "audio_follows_picture": self.handoff.audio_follows_picture,
        }


@dataclass(frozen=True)
class Lowered:
    graph: dict
    files: dict[str, str]  # asset id -> the file ComfyUI loads it by, pictures then keyframes
    context_file: str | None  # where a continuation's context latent is uploaded
    snapshot: dict  # the job's record: context, continuation, capture and expectation


def compile_window(
    segment: Segment,
    pictures: tuple[Picture, ...],
    keyframes: tuple[Keyframe, ...],
    grid: FrameGrid,
    handoff: Handoff | None,
) -> Window:
    """Raises a `CapabilityGap` or `ValueError` the caller reports; a handoff that has refusals never lowers."""
    if (handoff is not None) != segment.continues:
        raise ValueError("a continuing segment, and only one, hands over motion")
    if handoff and handoff.refusals:
        raise ValueError(handoff.refusals[0][1])
    context = handoff.context_frames if handoff else 0
    return Window(compile_prompt(segment, pictures, keyframes, grid, context), handoff, grid.fps)


def lower(window: Window, *, prefix: str, seed: int) -> Lowered:
    compiled = window.compiled
    files = {asset: f"{prefix}/picture_{i}.png" for i, asset in enumerate(compiled.slots)}
    files |= {
        asset: f"{prefix}/keyframe_{i}.png" for i, (asset, _) in enumerate(compiled.keyframes)
    }
    context_file = f"{prefix}/context.safetensors" if window.handoff else None
    delivery = render_presets().delivery
    graph = build_graph(
        compiled,
        files=files,
        seed=seed,
        **delivery,
        fps=window.fps,
        context_latent=context_file,
        output_prefix=prefix,
        capture_context=True,
        handoff=window.handoff,
    )
    snapshot = {
        "capture_context": context_descriptor(prefix),
        "context_frames": compiled.context_frames,
        "continuation": window.continuation,
        "expect": {
            "kind": "video",
            "frames": compiled.window_frames - compiled.context_frames,
            "fps": window.fps,
            **delivery,
            "audio": True,
        },
    }
    return Lowered(graph, files, context_file, snapshot)
