"""How an H3 continuation hands a selected take's motion to the next segment, and which handoff the research
recommends (docs/CONTINUATION.md).

Both methods start from the selected take's original paired AV latent (its `h3_context` artifact); neither has a
pixel fallback.

- `paired`, Original paired latents (the default handoff): MiniMax's motion context. The picture and audio
  windows are independent; the audio window is `render.toml`'s `audio_context_frames`.
- `masked`, Masked handoff (GaiaH3ContinuityMasked, Continuity 00704a4): the source video latent's tail is
  protected under a mask and its decoded audio is re-encoded as an end-aligned reference, so the audio window is
  always the picture window.

The rule is a hint the user sees, never a silent switch: masked handoff for a same-shot continuation that
qualifies, paired latents otherwise. Masked qualifies only with the original paired artifact at the selected
source ending (the only source a GAIAVFX continuation reads), equal picture and audio windows (true by
construction here) and no timed or anchor guides.
"""

from dataclasses import dataclass

from gaiavfx.modules.direction.timebase import FrameGrid

PAIRED, MASKED = "paired", "masked"
METHODS = (PAIRED, MASKED)
NAMES = {PAIRED: "Paired latents", MASKED: "Masked handoff"}

# Measured by FlooPowder's continuity and cross-angle campaigns (21–24 Sep 2026), all at 22 context frames.
SAME_SHOT = (
    "This segment continues the same shot. Masked handoff kept the smallest joins and the most faithful "
    "context in the continuity campaign."
)
ACROSS_CUTS = (
    "This segment cuts to a new angle. Across cuts, paired latents crossed the line on 9 of 11 renders and "
    "masked handoff on 6 of 11: the masked prefix carried the old walking direction past the cut. Masked "
    "kept the sound 3 to 10 dB louder, so it remains a choice when sound matters more than geography."
)


@dataclass(frozen=True)
class ContinuationFacts:
    continues: bool
    cuts: int  # cuts inside the segment's window
    guides: int  # timed or anchor guides (pinned keyframes) the window lowers or plans to
    source_frames: int | None = None  # the source take's model window, once a take is selected


@dataclass(frozen=True)
class Handoff:
    method: str  # the method this segment renders with
    explicit_method: str | None  # the user's or agent's choice; None follows the rule
    recommended: str
    reason: str  # why the rule recommends `recommended`
    masked_problem: str | None  # why masked handoff does not qualify here, if it does not
    context_frames: int  # picture context, in model-window frames
    explicit_context: int | None
    default_context: int
    context_choices: tuple[int, ...]
    audio_context_frames: int
    audio_follows_picture: bool
    refusals: tuple[tuple[str, str], ...] = ()  # (code, message) that block rendering


def source_frames(info: dict | None) -> int | None:
    """The model window of a saved paired latent: 5n+2 latent steps hold 17n+5 frames."""
    shape = (info or {}).get("video_shape")
    if not isinstance(shape, list) or len(shape) != 5 or type(shape[2]) is not int:
        return None
    steps = shape[2]
    if steps < 2 or (steps - 2) % 5:
        return None
    return 5 + (steps - 2) // 5 * 17


def masked_problem(facts: ContinuationFacts) -> str | None:
    if not facts.continues:
        return "Only a continuing segment hands over motion."
    if facts.guides:
        pins = "a pinned keyframe" if facts.guides == 1 else f"{facts.guides} pinned keyframes"
        return (
            f"Masked handoff does not combine with timed or anchor guides, and this segment lowers {pins}. "
            "Keep paired latents, or remove the pin."
        )
    return None


def recommend(facts: ContinuationFacts) -> tuple[str, str]:
    if facts.cuts:
        return PAIRED, ACROSS_CUTS
    if problem := masked_problem(facts):
        return PAIRED, f"{problem} Paired latents remain the recommendation."
    return MASKED, SAME_SHOT


def context_problem(grid: FrameGrid, context: int, source: int | None) -> str | None:
    choices = ", ".join(map(str, grid.context_lengths()))
    if not grid.valid_context(context):
        return f"Choose a motion context of {choices} frames."
    if source is not None and context > source:
        return (
            f"The selected take's window holds {source} frames; choose a motion context of at most "
            f"{source} frames."
        )
    return None


def resolve(
    facts: ContinuationFacts,
    *,
    method: str | None,
    context_frames: int | None,
    grid: FrameGrid,
    audio_context_frames: int | None = None,
) -> Handoff:
    """The handoff a continuing segment renders with. Stored choices are re-checked: a later pin can disqualify
    masked handoff, and a shorter source take can rule out a long context."""
    if audio_context_frames is None:
        from gaiavfx.integrations.minimax_h3.graph import render_presets

        audio_context_frames = render_presets().conditioning["audio_context_frames"]
    recommended, reason = recommend(facts)
    chosen = method or recommended
    context = grid.context_default if context_frames is None else context_frames
    problem = masked_problem(facts)
    refusals = []
    if method is not None and method not in METHODS:
        refusals.append(("continuation_method", f"Unknown continuation method {method!r}."))
    elif chosen == MASKED and problem:
        refusals.append(("continuation_method", problem))
    if issue := context_problem(grid, context, facts.source_frames):
        refusals.append(("continuation_context", issue))
    follows = chosen == MASKED
    return Handoff(
        chosen,
        method,
        recommended,
        reason,
        problem,
        context,
        context_frames,
        grid.context_default,
        grid.context_lengths(facts.source_frames),
        context if follows else audio_context_frames,
        follows,
        tuple(refusals),
    )


def choice_problem(
    facts: ContinuationFacts,
    grid: FrameGrid,
    *,
    method: str | None,
    context_frames: int | None,
    changing: frozenset[str],
) -> str | None:
    """Why an explicit choice is refused now. Only the fields being chosen are judged: a stored masked choice
    a later pin disqualified is reported by the plan, not by an unrelated context edit."""
    if not facts.continues:
        return "Only a continuing segment hands over motion; continue from the predecessor first."
    if "method" in changing and method is not None:
        if method not in METHODS:
            return f"Choose {' or '.join(NAMES[m] for m in METHODS)}."
        if method == MASKED and (problem := masked_problem(facts)):
            return problem
    if "context_frames" in changing and context_frames is not None:
        return context_problem(grid, context_frames, facts.source_frames)
    return None
