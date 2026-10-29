"""`plan_cut` and `plan_opening`: one plan per shot that the UI, the agent and the compiler all read (ADR 0009,
ADR 0019).

A segment's shots are its opening and the shots after its cuts. Each plan names the coverage and its reasons, the
recommended technique, the alternatives, what is still missing as typed next actions, and the techniques this
model cannot lower. It never renders, never approves and never selects; it only says what the shot needs.
"""

from dataclasses import dataclass
from enum import StrEnum
from typing import Literal

from gaiavfx.modules.direction.capabilities import Capabilities, KnowledgePack
from gaiavfx.modules.direction.coverage import (
    Assessment,
    Coverage,
    CutContext,
    OpeningContext,
    assess_cut,
    assess_opening,
)
from gaiavfx.modules.direction.techniques import (
    Block,
    Policy,
    Prerequisite,
    Shot,
    Status,
    Technique,
)


class Action(StrEnum):
    DECLARE_RELATION = "declare_relation"  # say how the new angle relates to the previous one
    VIEW_ANGLE = (
        "view_angle"  # make a reviewed view of the new angle (the place's producer decides how)
    )
    REVIEW_VIEW = "review_view"  # review the survey views waiting for approval
    VIEW_PLACE = "view_place"  # make reviewed views of the place for a sheet
    INSERT_SUBJECT = "insert_subject"  # insert the subject into the new angle's approved view
    REVIEW_KEYFRAME = "review_keyframe"  # review the inserted frame
    STATE_SIDES = "state_sides"  # record which side of the new view each landmark is on


@dataclass(frozen=True)
class NextAction:
    action: Action
    target: str  # a cut id, a segment id (its opening) or an angle id
    reason: str


@dataclass(frozen=True)
class Available:
    """What the library already holds for this shot, as the application found it."""

    approved_view_angles: frozenset[str] = frozenset()
    pending_view_angles: frozenset[str] = frozenset()
    approved_keyframe_cuts: frozenset[str] = frozenset()
    pending_keyframe_cuts: frozenset[str] = frozenset()
    place_views: int = 0  # reviewed views of the place that suit this shot


@dataclass(frozen=True)
class Choice:
    technique_id: str
    title: str
    status: Status
    blocks: tuple[Block, ...]
    ready: bool
    actions: tuple[NextAction, ...]
    keyframe_offset_frames: int | None
    evidence: tuple[str, ...]
    limitations: tuple[str, ...]


@dataclass(frozen=True)
class Gap:
    code: str
    message: str
    actions: tuple[NextAction, ...] = ()


@dataclass(frozen=True)
class Binding:
    kind: Literal["view", "pin", "sheet"]
    id: str
    asset_id: str
    approved_at: str | None
    stale: bool = False
    reasons: tuple[str, ...] = ()
    suitability: Literal["suits", "unsuitable", "unjudged"] = "unjudged"
    suitability_note: str = ""


def binding_problem(binding: Binding) -> str | None:
    label = {
        "view": "approved view of the new angle",
        "pin": "cut's pinned frame",
        "sheet": "assembled sheet",
    }[binding.kind]
    if binding.stale:
        return f"the {label} is stale: {'; '.join(binding.reasons)}"
    if binding.suitability == "unsuitable":
        return f"the user judged this {binding.kind} unsuitable for this cut: {binding.suitability_note}"
    return None


@dataclass(frozen=True)
class CutPlan:
    cut_id: str
    assessment: Assessment
    recommended: Choice | None
    alternatives: tuple[Choice, ...]
    unavailable: tuple[tuple[str, str], ...]
    gaps: tuple[Gap, ...]
    override: bool = False
    bindings: tuple[Binding, ...] = ()


@dataclass(frozen=True)
class OpeningPlan:
    """The plan of a segment's first shot, from its opening angle."""

    segment_id: str
    angle_id: str
    assessment: Assessment
    recommended: Choice | None
    alternatives: tuple[Choice, ...]
    unavailable: tuple[tuple[str, str], ...]
    gaps: tuple[Gap, ...]
    override: bool = False
    bindings: tuple[Binding, ...] = ()


@dataclass(frozen=True)
class _Target:
    """The shot a technique is checked for: its id (a cut, or the segment for its opening) and its angle."""

    id: str
    angle: str
    side_facts: bool
    angle_words: str = "the new angle"  # how the reasons name the shot's angle
    shot_words: str = "this cut"


def _missing(technique, target, available, bindings):
    def problem(kind, fallback):
        return (
            "; ".join(p for b in bindings if b.kind == kind and (p := binding_problem(b)))
            or fallback
        )

    actions = []
    angle, shot = target.angle, target.id
    for need in technique.requires:
        if need is Prerequisite.APPROVED_VIEW and angle not in available.approved_view_angles:
            if angle in available.pending_view_angles and not problem("view", ""):
                actions.append(
                    NextAction(
                        Action.REVIEW_VIEW, angle, f"views of {target.angle_words} await review"
                    )
                )
            else:
                actions.append(
                    NextAction(
                        Action.VIEW_ANGLE,
                        angle,
                        problem("view", f"no reviewed view of {target.angle_words} exists"),
                    )
                )
        elif (
            need is Prerequisite.APPROVED_KEYFRAME and shot not in available.approved_keyframe_cuts
        ):
            if shot in available.pending_keyframe_cuts and not problem("pin", ""):
                actions.append(
                    NextAction(Action.REVIEW_KEYFRAME, shot, "the inserted frame awaits review")
                )
            else:
                actions.append(
                    NextAction(
                        Action.INSERT_SUBJECT,
                        shot,
                        problem("pin", "the subject is not yet in the new angle's view"),
                    )
                )
        elif need is Prerequisite.PLACE_VIEWS and available.place_views < 1:
            actions.append(
                NextAction(
                    Action.VIEW_PLACE,
                    angle,
                    problem(
                        "sheet",
                        problem("view", f"no reviewed view of the place suits {target.shot_words}"),
                    ),
                )
            )
        elif need is Prerequisite.SIDE_FACTS and not target.side_facts:
            actions.append(
                NextAction(
                    Action.STATE_SIDES, shot, "the landmarks' sides in the new view are not known"
                )
            )
    if Block.PLACE_VIEWS in technique.blocks:
        for binding in bindings:
            if (
                binding.kind in {"sheet", "view"}
                and (reason := binding_problem(binding))
                and not any(a.action == Action.VIEW_PLACE for a in actions)
            ):
                actions.append(NextAction(Action.VIEW_PLACE, angle, reason))
    return tuple(actions)


def _choice(technique, status, target, available, pack, bindings):
    actions = _missing(technique, target, available, bindings)
    offset = pack.pin_offset_frames if Block.PINNED_FRAME in technique.blocks else None
    return Choice(
        technique_id=technique.id,
        title=technique.title,
        status=status,
        blocks=technique.blocks,
        ready=not actions,
        actions=actions,
        keyframe_offset_frames=offset,
        evidence=technique.evidence + pack.evidence.get(technique.id, ()),
        limitations=technique.limitations,
    )


def _plan(
    shot,
    target,
    assessment,
    relation,
    policy,
    capabilities,
    pack,
    available,
    *,
    fidelity,
    override,
    bindings,
    gaps,
):
    """The choices of one shot: the matching rows of its kind, the override if any, and the default."""
    matching, unavailable = [], []

    def status_of(technique: Technique) -> Status:
        return pack.status.get(technique.id, technique.status)

    for technique in policy.techniques:
        status = status_of(technique)
        if technique.shot is not shot or status is Status.RETIRED:
            continue
        if not technique.applies(assessment.coverage, relation, target.side_facts, fidelity):
            continue
        reason = next(
            (why for block in technique.blocks if (why := capabilities.lacks(block))), None
        )
        if reason:
            unavailable.append((technique.id, reason))
            continue
        matching.append(_choice(technique, status, target, available, pack, bindings))

    if override is not None:
        technique = policy.technique(override)
        if technique.shot is not shot:
            raise ValueError(f"{override} plans a {technique.shot.value}, not a {shot.value}")
        why = next((w for block in technique.blocks if (w := capabilities.lacks(block))), None)
        if why:
            raise ValueError(f"{override} cannot be lowered by {capabilities.integration}: {why}")
        chosen = _choice(technique, status_of(technique), target, available, pack, bindings)
        others = tuple(c for c in matching if c.technique_id != override)
        return chosen, others, tuple(unavailable), tuple(gaps), True

    recommended = next((c for c in matching if c.status is Status.DEFAULT), None)
    alternatives = tuple(c for c in matching if c is not recommended)
    if recommended is None and assessment.coverage is not Coverage.UNKNOWN:
        if unavailable:
            gaps.append(
                Gap(
                    "technique_unavailable",
                    f"The techniques for this shot cannot be lowered by {capabilities.integration}.",
                )
            )
        else:
            gaps.append(Gap("no_default_technique", "No default technique covers this shot."))
    return recommended, alternatives, tuple(unavailable), tuple(gaps), False


def _check_pack(pack: KnowledgePack, capabilities: Capabilities):
    if pack.integration != capabilities.integration:
        raise ValueError("the knowledge pack belongs to a different integration")


def plan_cut(
    context: CutContext,
    policy: Policy,
    capabilities: Capabilities,
    pack: KnowledgePack,
    available: Available | None = None,
    *,
    fidelity: bool = False,
    override: str | None = None,
    bindings: tuple[Binding, ...] = (),
) -> CutPlan:
    """Plan one cut. `override` names a technique the user (or the agent, when allowed) chose instead."""
    _check_pack(pack, capabilities)
    available = available or Available()
    assessment = assess_cut(context)
    if context.from_angle == context.to_angle:
        reason = "A cut needs a different target angle; add that camera angle, then cut to it."
        return CutPlan(
            context.cut_id,
            Assessment(Coverage.UNKNOWN, (reason,)),
            None,
            (),
            (),
            (Gap("same_angle_cut", reason),),
            bindings=bindings,
        )
    gaps = []
    if assessment.coverage is Coverage.UNKNOWN:
        gaps.append(
            Gap(
                "coverage_unknown",
                "How the new angle relates to the previous one is not known.",
                (NextAction(Action.DECLARE_RELATION, context.cut_id, "state the angle relation"),),
            )
        )
    recommended, alternatives, unavailable, gaps, overridden = _plan(
        Shot.CUT,
        _Target(context.cut_id, context.to_angle, context.side_facts),
        assessment,
        context.relation.value,
        policy,
        capabilities,
        pack,
        available,
        fidelity=fidelity,
        override=override,
        bindings=bindings,
        gaps=gaps,
    )
    return CutPlan(
        context.cut_id,
        assessment,
        recommended,
        alternatives,
        unavailable,
        gaps,
        override=overridden,
        bindings=bindings,
    )


def plan_opening(
    context: OpeningContext,
    policy: Policy,
    capabilities: Capabilities,
    pack: KnowledgePack,
    available: Available | None = None,
    *,
    override: str | None = None,
    bindings: tuple[Binding, ...] = (),
) -> OpeningPlan:
    """Plan a segment's first shot. An `override` is the user's explicit request (using a set, ADR 0019 §1): its
    prerequisites are required, never dropped for the default."""
    _check_pack(pack, capabilities)
    available = available or Available()
    assessment = assess_opening(context)
    recommended, alternatives, unavailable, gaps, overridden = _plan(
        Shot.OPENING,
        _Target(context.segment_id, context.angle, False, "the opening angle", "this shot"),
        assessment,
        None,
        policy,
        capabilities,
        pack,
        available,
        fidelity=False,
        override=override,
        bindings=bindings,
        gaps=[],
    )
    return OpeningPlan(
        context.segment_id,
        context.angle,
        assessment,
        recommended,
        alternatives,
        unavailable,
        gaps,
        override=overridden,
        bindings=bindings,
    )
