"""MiniMax H3 as a direction integration (ADR 0010): its capability manifest and its knowledge pack.

This package is where H3-specific knowledge lives: the pinned frame lands 12 frames after a cut, pictures are
fed at `ref_image_size=match`, the tested sheet geometry and wording. The direction core only sees the
capabilities and the pack.
"""

from functools import cache
from importlib import resources

from gaiavfx.modules.direction.capabilities import (
    Capabilities,
    KeyframeAnchor,
    KnowledgePack,
    ReplacementSupport,
    parse_pack,
)
from gaiavfx.modules.direction.timebase import FrameGrid

NAME = "minimax_h3"
OUTPUT_KINDS = frozenset({"video"})


def capabilities(presets=None) -> Capabilities:
    from gaiavfx.integrations.minimax_h3.graph import render_presets

    return Capabilities(
        integration=NAME,
        picture_slots=9,
        keyframes=frozenset({KeyframeAnchor.START, KeyframeAnchor.END, KeyframeAnchor.CUT_OFFSET}),
        motion_context=True,
        # Legacy ref_audios/ref_videos and AddGuide audio wiring has no tested product wording.
        media_guides=frozenset(),
        replacement=ReplacementSupport(
            guide_video=True,
            audio_lock=True,
            motion_context=True,
            masked_span=True,
            faceswap=(presets or render_presets()).faceswap is not None,
            pictures=2,
            treatments=frozenset({"rgb", "inverted"}),
            groupings=frozenset({"single", "joint"}),
            framings=frozenset({"crop", "pair", "active", "full", "head"}),
            canvas_budget_pixels=896 * 512,
            canvas_step=32,
            tested_frames=frozenset({56, 73}),
            max_performers=2,
            canvas_low=256,
            canvas_high=1280,
            canvas_aspect=(0.5, 2.0),
        ),
        # windows are 5 + 17k frames at 24 fps; a continuation carries 22 frames of motion context by default.
        # The manifest sets no hard maximum; 396 is the recommended 15 s plus 22 context frames, on the grid.
        frame_grid=FrameGrid(
            fps=24,
            length_base=5,
            length_step=17,
            context_default=22,
            max_window=None,
            recommended_max_window=396,
        ),
    )


@cache
def knowledge_pack() -> KnowledgePack:
    return parse_pack(resources.files(__name__).joinpath("knowledge/pack.toml").read_text("utf-8"))
