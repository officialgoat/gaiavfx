"""The Replacement plan: what one window of a shot renders in its first round, and in each round after.

The application reads a window (its people report as stored, the kind of each performer's target picture, whether the
source has audio) and this module decides: the facts the request freezes, the arm, the reasons a person reads, and the
treatment and FaceSwap pins a shot's later windows keep. Plain Python, without IO or a model: the integration's
replacement support and frame grid are passed in.
"""

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, replace
from fractions import Fraction
from statistics import median

from gaiavfx.modules.direction.capabilities import ReplacementSupport
from gaiavfx.modules.direction.timebase import FrameGrid
from gaiavfx.modules.recast import person_crop
from gaiavfx.modules.recast.mouth import MouthSeries, quiet_facts
from gaiavfx.modules.recast.replacement import (
    Arm,
    Box,
    Bystander,
    CropPlan,
    Letterbox,
    Occluder,
    Performer,
    ReplacementFacts,
    ReplacementPlan,
    ReplacementPolicy,
    Track,
    from_record,
    minor_bystander_reason,
    parse_replacement_policy,
    plan_bridge,
    plan_head_pass,
    plan_replacement,
    union_crop,
)
from gaiavfx.modules.recast.selectors import position, selector_positions


class Refused(Exception):
    """The request cannot be planned as asked; its text is the person's (the application answers 422). Not a
    ValueError: callers that turn a ValueError into a fallback, as the shared crop's do, must not swallow it."""


# --- the request --------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class PerformerChoice:
    track_id: str
    target_asset: str
    role: str = "performer"
    position: str | None = None
    target_subject_id: str | None = None
    action: str | None = None
    selected_by: str = "user"
    # A caller's margin over user-curated characters (Recast) replaces the window's
    # machine-cluster margin; None keeps the window's.
    identity_margin: float | None = None


@dataclass(frozen=True)
class HeadPassChoice:
    parent_asset: str


@dataclass(frozen=True)
class HandoffChoice:
    predecessor_operation: str
    candidate_id: str


@dataclass(frozen=True)
class BridgeChoice:
    join: int
    predecessor: tuple[str, str]
    successor: tuple[str, str]


@dataclass(frozen=True)
class ReplacementRequest:
    source_asset: str
    analysis_operation: str
    performers: tuple[PerformerChoice, ...]
    seed: int = 0
    required_context: tuple[Box, ...] = ()
    avoided_regions: tuple[Box, ...] = ()
    quiet: bool = False
    scene_items: tuple[str, ...] = ()
    occluders: tuple[Occluder, ...] = ()
    silent_intervals: tuple[tuple[int, int], ...] = ()
    performer_hands_visible: bool | None = None
    source_target_similarity: float | None = None
    treatment_override: str | None = None
    faceswap_override: bool | None = None
    head: HeadPassChoice | None = None
    speaking: bool | None = None
    handoff: HandoffChoice | None = None
    crop_override: CropPlan | None = None
    save_latent: bool = False
    bridge: BridgeChoice | None = None
    close_up: bool | None = None


def parse_request(value) -> ReplacementRequest:
    """A ReplacementRequest from the API's or a producer's plain request, validated."""
    if isinstance(value, ReplacementRequest):
        request = value
    else:
        try:
            data = dict(value)
            for name, cls in (
                ("performers", PerformerChoice),
                ("required_context", Box),
                ("avoided_regions", Box),
                ("occluders", Occluder),
            ):
                data[name] = tuple(cls(**v) for v in data.get(name, ()))
            data["scene_items"] = tuple(data.get("scene_items", ()))
            data["silent_intervals"] = tuple(tuple(v) for v in data.get("silent_intervals", ()))
            if data.get("head") is not None:
                data["head"] = HeadPassChoice(**data["head"])
            if data.get("handoff") is not None:
                data["handoff"] = HandoffChoice(**data["handoff"])
            if data.get("crop_override") is not None:
                data["crop_override"] = from_record(CropPlan, data["crop_override"])
            if data.get("bridge") is not None:
                data["bridge"] = BridgeChoice(
                    **{
                        **data["bridge"],
                        "predecessor": tuple(data["bridge"]["predecessor"]),
                        "successor": tuple(data["bridge"]["successor"]),
                    }
                )
            request = ReplacementRequest(**data)
        except (TypeError, ValueError) as exc:
            raise Refused(f"Invalid replacement request: {exc}") from exc
    if request.handoff is not None and (
        not isinstance(request.handoff, HandoffChoice)
        or any(
            not isinstance(v, str) or not v
            for v in (request.handoff.predecessor_operation, request.handoff.candidate_id)
        )
        or request.head is not None
    ):
        raise Refused("A latent handoff needs a predecessor and candidate, without a head pass.")
    if request.bridge is not None and (
        not isinstance(request.bridge, BridgeChoice)
        or request.handoff is not None
        or request.save_latent
        or (request.crop_override is None and request.head is None)
        or type(request.bridge.join) is not int
        or request.bridge.join < 1
        or any(
            len(pair) != 2 or any(not isinstance(v, str) or not v for v in pair)
            for pair in (request.bridge.predecessor, request.bridge.successor)
        )
    ):
        raise Refused("A bridge needs a crop and two frozen neighbour candidates.")
    if request.crop_override is not None and not isinstance(request.crop_override, CropPlan):
        raise Refused("Invalid shared crop.")
    if type(request.save_latent) is not bool:
        raise Refused("save_latent must be boolean.")
    if (request.save_latent or request.handoff) and (
        request.crop_override is None or request.head is not None
    ):
        raise Refused("Latent windows need a pinned crop and cannot be head passes.")
    if request.head is not None and (
        not isinstance(request.head, HeadPassChoice)
        or not isinstance(request.head.parent_asset, str)
        or not request.head.parent_asset
        or len(request.performers) != 1
        or request.treatment_override not in {None, "rgb"}
    ):
        raise Refused("A head pass needs one performer and an RGB parent asset.")
    if request.treatment_override not in {None, "rgb", "inverted"}:
        raise Refused("Invalid treatment override.")
    if request.faceswap_override is not None and type(request.faceswap_override) is not bool:
        raise Refused("FaceSwap override must be boolean.")
    if request.close_up is True and len(request.performers) != 1:
        raise Refused("Close-up applies to a single performer")
    if request.close_up is not None and type(request.close_up) is not bool:
        raise Refused("Close-up must be boolean.")
    if request.speaking is not None and type(request.speaking) is not bool:
        raise Refused("Speaking must be boolean.")
    if type(request.seed) is not int or not 0 <= request.seed <= 2**63 - 4:
        raise Refused("Replacement seed must leave room for four signed 64-bit library seeds.")
    for name, limit in (
        ("performers", 4),
        ("required_context", 16),
        ("avoided_regions", 16),
        ("silent_intervals", 8),
    ):
        # Read-only planning can diagnose all four SAM person slots with a typed gap.
        # The HTTP boundary and executable plans admit at most two performers.
        if len(getattr(request, name)) > limit:
            raise Refused(f"{name} is limited to {limit} entries.")
    return request


def effective_policy(policy_text, renders=None, *, draft=False) -> ReplacementPolicy:
    """The policy a request plans with: its draft rounds when drafting, cut to `renders` when given."""
    policy = parse_replacement_policy(policy_text)
    if draft:
        if policy.draft is None:
            renders = 1
        else:
            policy = replace(
                policy,
                renders=sum(policy.draft.renders_per_round),
                renders_per_round=policy.draft.renders_per_round,
                max_rounds=len(policy.draft.renders_per_round),
                escalation=tuple(
                    row for row in policy.escalation if row.on in policy.draft.retry_on
                ),
            )
    if renders is None:
        return policy
    if type(renders) is not int or not 1 <= renders <= policy.renders:
        raise Refused(f"renders must be an integer from 1 to {policy.renders}.")
    remaining, rounds = renders, []
    for count in policy.renders_per_round:
        if not remaining:
            break
        rounds.append(min(count, remaining))
        remaining -= rounds[-1]
    return replace(policy, renders=renders, renders_per_round=tuple(rounds), max_rounds=len(rounds))


def bridge_facts(predecessor_request, successor_request, layout) -> ReplacementFacts:
    """Merge source facts by film frame, splitting wording at the successor start."""
    left = from_record(ReplacementFacts, predecessor_request["facts"])
    right = from_record(ReplacementFacts, successor_request["facts"])
    if (left.source_asset, left.native, left.fps) != (right.source_asset, right.native, right.fps):
        raise ValueError("Bridge neighbours must share the source, canvas and frame rate")
    if [p.target_asset for p in left.performers] != [p.target_asset for p in right.performers]:
        raise ValueError("Bridge performers cannot be reconciled")
    start, end, split = layout["start"], layout["end"], right.shot_start
    if not left.shot_start <= start < split < end <= right.shot_end or left.shot_end < split:
        raise ValueError("Bridge is not covered by its neighbours")
    lt = {t.id: t for t in left.tracks}
    paired = {
        b.track_id: a.track_id for a, b in zip(left.performers, right.performers, strict=True)
    }
    tracks = []
    for t in right.tracks:
        if t.id not in paired:
            # Only bound performers have identity across analyses. Keep successor
            # bystander facts; protection pixels use the earliest covering masks.
            tracks.append(
                replace(t, presence=tuple(t.presence[max(0, f - split)] for f in range(start, end)))
            )
            continue
        previous = lt[paired[t.id]]
        presence = tuple(
            previous.presence[f - left.shot_start] if f < split else t.presence[f - split]
            for f in range(start, end)
        )
        if "unresolved" in presence or all(v == "absent" for v in presence):
            raise ValueError("Bridge performer presence cannot be reconciled")
        tracks.append(
            replace(t, box=Box(*person_crop.union((previous.box, t.box))), presence=presence)
        )
    quiet = left.quiet and right.quiet
    intervals = []
    if not quiet:
        for facts, lo, hi in ((left, start, split), (right, split, end)):
            rows = ((0, facts.delivered_frames),) if facts.quiet else facts.silent_intervals
            for a, b in rows:
                a, b = max(lo, facts.shot_start + a), min(hi, facts.shot_start + b)
                if a < b:
                    a, b = a - start, b - start
                    if intervals and intervals[-1][1] == a:
                        intervals[-1] = (intervals[-1][0], b)
                    else:
                        intervals.append((a, b))
    return replace(
        right,
        shot_start=start,
        shot_end=end,
        tracks=tuple(tracks),
        quiet=quiet,
        speaking=left.speaking or right.speaking,
        silent_intervals=tuple(intervals),
        required_context=tuple(dict.fromkeys((*left.required_context, *right.required_context))),
        avoided_regions=tuple(dict.fromkeys((*left.avoided_regions, *right.avoided_regions))),
        handoff="bridge",
    )


# --- what the application read ------------------------------------------------------------------------------------


@dataclass(frozen=True)
class WindowReading:
    """What the application read for a fresh, latent or head window: the window's people report as stored, the
    library kind of each performer's target picture, and whether the source has audio. Never changed here."""

    report: Mapping
    target_kinds: Mapping[str, str]
    has_audio: bool


@dataclass(frozen=True)
class BridgeReading:
    """What the application read for a bridge: `bridge_facts` of its two frozen neighbours, each neighbour's frozen
    window (arm, tracks, selected_tracks, start) and the successor's frozen request."""

    facts: ReplacementFacts
    windows: tuple[Mapping, Mapping]
    successor: Mapping


@dataclass(frozen=True)
class RoundSettings:
    """What a request fixes for every round of its plan."""

    policy_text: str
    renders: int | None = None
    draft: bool = False
    treatment_override: str | None = None
    faceswap_override: bool | None = None
    crop_override: CropPlan | None = None
    bridge_arm: Arm | None = None

    @classmethod
    def frozen(cls, request: Mapping) -> "RoundSettings":
        """The settings a started replacement froze in its request."""
        return cls(
            request["policy"],
            request.get("renders"),
            request.get("draft", False),
            request.get("treatment_override") or None,
            request.get("faceswap_override"),
            from_record(CropPlan, request["crop_override"])
            if request.get("crop_override")
            else None,
            from_record(Arm, request["bridge"]["windows"][1]["arm"])
            if request.get("bridge")
            else None,
        )


@dataclass(frozen=True)
class ShotPins:
    """What every later window of a shot keeps from its first window's plan: the treatment, and FaceSwap with the
    close-up when the policy or the request pins them (None leaves them to each window)."""

    treatment_override: str
    faceswap_override: bool | None = None
    close_up: bool | None = None

    @classmethod
    def frozen(cls, first_request: Mapping) -> "ShotPins":
        """The pins a shot's first window froze in its started request."""
        faceswap = first_request.get("faceswap_override")
        return cls(
            first_request["first_plan"]["arm"]["treatment"],
            faceswap,
            first_request["facts"].get("close_up", False) if faceswap is not None else None,
        )


@dataclass(frozen=True)
class WindowPlan:
    """A window's first round: the facts its request freezes, its plan (the reading's notes among the reasons), the
    report tracks the request freezes beside them, and the pins the shot's later windows keep (None without an arm).
    """

    facts: ReplacementFacts
    plan: ReplacementPlan
    tracks: Sequence[Mapping]
    pins: ShotPins | None = None


# --- the two questions ----------------------------------------------------------------------------------------------


def plan_window(
    request: ReplacementRequest,
    reading: WindowReading | BridgeReading,
    policy_text: str,
    support: ReplacementSupport,
    grid: FrameGrid,
    *,
    renders: int | None = None,
    draft: bool = False,
) -> WindowPlan:
    """What this window renders in its first round, and why."""
    bridge_arm = None
    if isinstance(reading, BridgeReading):
        if request.analysis_operation != reading.successor["analysis_operation"]:
            raise Refused("Bridge analysis must be its successor's.")
        facts, notes = reading.facts, ()
        report = {"tracks": reading.successor["tracks"], "contacts": {}, "concepts": ["person"]}
        bridge_arm = from_record(Arm, reading.windows[1]["arm"])
        if request.head:
            facts, report = _head_bridge(request, facts, report, reading.windows)
    else:
        facts, notes = _window_facts(
            request, reading, effective_policy(policy_text, renders, draft=draft)
        )
        report = reading.report
    settings = RoundSettings(
        policy_text,
        renders,
        draft,
        request.treatment_override,
        request.faceswap_override,
        request.crop_override,
        bridge_arm,
    )
    face_boxes = ()
    if request.head is not None:
        track = next(t for t in report["tracks"] if t["id"] == request.performers[0].track_id)
        face_boxes = (track.get("mouth") or {}).get("box", [None] * facts.delivered_frames)
        if report.get("mouth_scan") == "unavailable":
            face_boxes = [None] * facts.delivered_frames
    plan = _with_notes(
        _round(facts, settings, support, grid, head=request.head, face_boxes=face_boxes),
        notes,
        facts,
        report,
    )
    pins = None
    if plan.arm is not None:
        # FaceSwap is pinned once the policy names a first arm for it, or the request already pinned it.
        pinned = (
            parse_replacement_policy(policy_text).first_arm_faceswap_configured
            or request.faceswap_override is not None
        )
        pins = ShotPins(
            plan.arm.treatment,
            plan.arm.faceswap if pinned else None,
            facts.close_up if pinned else None,
        )
    return WindowPlan(facts, plan, report["tracks"], pins)


def plan_round(
    facts: ReplacementFacts,
    settings: RoundSettings,
    support: ReplacementSupport,
    grid: FrameGrid,
    prior=(),
) -> ReplacementPlan:
    """What the next round of a started window renders, after the rounds in `prior`."""
    return _round(facts, settings, support, grid, prior)


def shot_crop(windows, policy_text, support: ReplacementSupport) -> CropPlan:
    """The one crop every window of a latent shot shares, from each window's (request, reading); a ValueError when
    none fits."""
    policy = effective_policy(policy_text)
    facts = [_window_facts(request, reading, policy)[0] for request, reading in windows]
    return union_crop(facts, policy, support)


# --- inside ------------------------------------------------------------------------------------------------------------


def _window_facts(request, reading, policy):
    """What the window holds for this request: the chosen performers' tracks, crops, close-ups and mouth facts, and
    the notes they leave for the plan's reasons."""
    report = reading.report
    if report["source_asset"] != request.source_asset:
        raise Refused("Analysis belongs to a different source asset.")
    people = {t["id"]: t for t in report["tracks"] if t["concept"] == "person"}
    choices = {p.track_id: p for p in request.performers}
    if not choices or len(choices) != len(request.performers) or set(choices) - set(people):
        raise Refused("Select distinct people from this shot's analysis.")
    positions = selector_positions(
        [people[p.track_id] for p in request.performers], report["native"]
    )
    performers = []
    for choice, derived_position in zip(request.performers, positions, strict=True):
        if reading.target_kinds[choice.target_asset] != "image":
            raise Refused("Replacement targets must be Library images.")
        # Direction currently has no last-editor provenance for subject descriptions.
        performers.append(
            Performer(
                choice.track_id,
                choice.target_asset,
                choice.target_subject_id,
                None,
                choice.role,
                choice.position or derived_position,
                choice.action,
            )
        )
    tracks = tuple(
        Track(
            t["id"],
            from_record(Box, t["box"]),
            tuple(t["presence"]),
            t.get("identity_margin")
            if t["id"] not in choices or choices[t["id"]].identity_margin is None
            else choices[t["id"]].identity_margin,
            choices[t["id"]].selected_by if t["id"] in choices else "user",
        )
        for t in people.values()
    )
    minor = {
        t.id: reason
        for t in tracks
        if t.id not in choices and (reason := minor_bystander_reason(t, report["native"], policy))
    }
    tracks = tuple(t for t in tracks if t.id not in minor)
    bystander_ids = set(people) - set(choices) - set(minor)
    bystanders = tuple(
        Bystander(t["id"], "person", f"at the {position(t, report['native'])[0]}", ())
        for t in people.values()
        if t["id"] in bystander_ids
    )
    required = list(request.required_context)
    avoided = list(request.avoided_regions)
    # Compute against the selected set, so a joint partner is never an avoided bystander.
    contact_ids = {o for p in choices for o in report["contacts"].get(p, ())}
    avoided_ids = bystander_ids | {o for p in bystander_ids for o in report["contacts"].get(p, ())}
    avoided_ids -= contact_ids
    for track in report["tracks"]:
        if track["id"] in contact_ids:
            required.append(from_record(Box, track["box"]))
        if track["id"] in avoided_ids:
            avoided.append(from_record(Box, track["box"]))
    quiet, silent_intervals, speaking, mouth_reasons, lips_unmeasured = _mouth_facts(
        request,
        people,
        tracks,
        policy,
        report.get("mouth_scan"),
        has_audio=reading.has_audio,
    )
    close_up, close_up_reasons = _close_up_facts(request, people, report["native"], policy)
    facts = ReplacementFacts(
        request.source_asset,
        report["shot_start"],
        report["shot_end"],
        tuple(report["native"]),
        Fraction(report["fps"]),
        from_record(Letterbox, report["letterbox"]) if report["letterbox"] else None,
        tuple(performers),
        bystanders,
        request.occluders,
        request.scene_items,
        quiet,
        silent_intervals,
        tracks,
        request.performer_hands_visible,
        request.source_target_similarity,
        tuple(dict.fromkeys(required)),
        tuple(dict.fromkeys(avoided)),
        speaking=speaking,
        handoff="latent" if request.handoff else "none",
        close_up=close_up,
        lips_unmeasured=lips_unmeasured,
    )
    return facts, (*minor.values(), *mouth_reasons, *close_up_reasons)


def _mouth_facts(request, people, tracks, policy, scan=None, *, has_audio=False):
    """Explicit request facts win; otherwise derive them from the source performers' lips.

    Returns (quiet, silent_intervals, speaking, reasons, lips_unmeasured). The request freezes
    these facts, so every later round of the operation compiles the same wording.
    """
    grouping = "joint" if len(request.performers) == 2 else "single"
    measure_outside_intervals = (
        has_audio
        and request.speaking is None
        and grouping in policy.audio_lock_unmeasured
        and policy.mouth is not None
    )
    if request.quiet or (request.silent_intervals and not measure_outside_intervals):
        return request.quiet, request.silent_intervals, False, (), False
    speaking = bool(request.speaking and has_audio and not request.quiet)
    requested_reasons = (
        ("Speaking (requested)." if request.speaking else "Not speaking (requested).",)
        if request.speaking is not None
        else ()
    )
    if policy.mouth is None:
        return False, (), speaking, requested_reasons, False
    present = sum(
        any(states != "absent" for states in frame)
        for frame in zip(
            *(t.presence for t in tracks if t.id in {p.track_id for p in request.performers})
        )
    )

    def unmeasured(measured, quiet=False):
        return bool(
            has_audio
            and not quiet
            and request.speaking is None
            and measured < policy.min_face_frames_fraction * present
        )

    series = [people[p.track_id].get("mouth") for p in request.performers]
    if scan == "unavailable" or any(s is None for s in series):
        if request.silent_intervals:
            return False, request.silent_intervals, False, (), False
        reason = (
            "The window face scan returned no lip landmarks (restart ComfyUI after updating "
            "the people nodes); the mouth follows the source."
            if scan == "unavailable"
            else "No source mouth measurements in this analysis; the mouth follows the source."
        )
        return False, (), speaking, (reason, *requested_reasons), False
    presence = {t.id: t.presence for t in tracks}
    derived = quiet_facts(
        tuple(MouthSeries(tuple(s["aperture"]), tuple(s["face_height"])) for s in series),
        tuple(presence[p.track_id] for p in request.performers),
        policy.mouth,
    )
    if request.silent_intervals:
        return False, request.silent_intervals, False, (), unmeasured(derived.measured_frames)
    quiet = derived.quiet
    intervals = derived.silent_intervals
    speaking = bool(
        has_audio
        and not quiet
        and (
            request.speaking
            if request.speaking is not None
            else derived.open_frames >= policy.speaking_min_open_frames
        )
    )
    reasons = (derived.reason, *requested_reasons)
    if speaking and request.speaking is None:
        reasons += (f"Speaking: {derived.open_frames} open source-lip frames.",)
    return quiet, intervals, speaking, reasons, unmeasured(derived.measured_frames, quiet)


def _close_up_facts(request, people, native, policy):
    if len(request.performers) != 1:
        if request.close_up is True:
            raise Refused("Close-up applies to a single performer")
        return False, ()
    if request.close_up is not None:
        return request.close_up, (
            "Close-up (requested)." if request.close_up else "Not a close-up (requested).",
        )
    if policy.faceswap is None:
        return False, ()
    person = people[request.performers[0].track_id]
    mouth = person.get("mouth") or {}
    presence = person.get("presence", ())
    present = sum(state == "present" for state in presence)
    heights = [
        h
        for h, state in zip(mouth.get("face_height", ()), presence)
        if state == "present" and type(h) in {int, float} and math.isfinite(h) and h > 0
    ]
    if not heights or not present or len(heights) / present < policy.min_face_frames_fraction:
        return False, ("Close-up unmeasured",)
    fraction = median(heights) / native[1]
    close_up = fraction >= policy.faceswap.close_up_min_face_fraction
    reasons = (f"Close-up: median face {fraction:.0%} of the frame height.",) if close_up else ()
    return close_up, reasons


def _head_bridge(request, facts, report, windows):
    """A head pass on a bridge: its one performer, with the mouth boxes of the neighbour each frame belongs to; the
    other performers become bystanders."""
    chosen = next(
        (p for p in facts.performers if p.track_id == request.performers[0].track_id),
        None,
    )
    if chosen is None or chosen.target_asset != request.performers[0].target_asset:
        raise Refused("Head bridge performer must match its body target.")
    index = facts.performers.index(chosen)
    tracks = [
        next(t for t in w["tracks"] if t["id"] == w["selected_tracks"][index]) for w in windows
    ]
    boxes = []
    for frame in range(facts.shot_start, facts.shot_end):
        side = int(frame >= windows[1]["start"])
        series = (tracks[side].get("mouth") or {}).get("box")
        boxes.append(series[frame - windows[side]["start"]] if series else None)
    merged_track = next(t for t in facts.tracks if t.id == chosen.track_id)
    report = {
        **report,
        "tracks": [
            {
                **t,
                "presence": list(merged_track.presence),
                "mouth": {**(t.get("mouth") or {}), "box": boxes},
            }
            if t["id"] == chosen.track_id
            else t
            for t in report["tracks"]
        ],
    }
    facts = replace(
        facts,
        handoff="none",
        performers=(chosen,),
        bystanders=(
            *facts.bystanders,
            *(
                Bystander(p.track_id, "person", p.position, ())
                for p in facts.performers
                if p != chosen
            ),
        ),
    )
    return facts, report


def _round(facts, settings, support, grid, prior=(), *, head=None, face_boxes=()):
    renders, treatment_override, faceswap_override = (
        settings.renders,
        settings.treatment_override,
        settings.faceswap_override,
    )
    if prior and (head is not None or any(a.arm.framing == "head" for a in prior)):
        raise ValueError("head pass runs one round")
    policy = effective_policy(settings.policy_text, renders, draft=settings.draft)
    pinned_fallback = (
        faceswap_override is False
        and treatment_override == "inverted"
        and facts.close_up
        and policy.first_arm_faceswap == "close_up"
        and policy.treatment == "rgb"
        and not support.faceswap
    )
    if treatment_override is not None:
        policy = replace(
            policy,
            treatment=treatment_override,
            inverted_when_similarity_at_least=None,
            # Old requests pin only treatment; their FaceSwap escalation stays intact.
            escalation=tuple(row for row in policy.escalation if row.to != "treatment"),
        )
    if faceswap_override is not None:
        policy = replace(
            policy,
            first_arm_faceswap="close_up" if faceswap_override else "never",
            escalation=tuple(row for row in policy.escalation if row.to != "faceswap"),
        )
    if facts.handoff == "bridge":
        if renders != 1 or settings.bridge_arm is None or settings.crop_override is None:
            raise Refused("A bridge requires one render, its successor arm and a crop.")
        plan = plan_bridge(
            facts, settings.bridge_arm, settings.crop_override, support, policy, grid, prior
        )
    elif head is not None:
        if renders != 1:
            raise Refused("A head pass requires renders = 1.")
        plan = plan_head_pass(facts, face_boxes, support, policy, grid, prior)
    else:
        plan = plan_replacement(
            facts, support, policy, grid, prior, shared_crop=settings.crop_override
        )
    if (
        treatment_override is not None
        and faceswap_override is not None
        and policy.first_arm_faceswap_configured
        and plan.arm is not None
        and plan.arm.round == 0
    ):
        plan = replace(
            plan, reasons=(*plan.reasons, f"Treatment pinned per shot: {treatment_override}.")
        )
    if faceswap_override is not None:
        plan = replace(
            plan, reasons=(*plan.reasons, f"FaceSwap pinned per shot: {faceswap_override}.")
        )
    if pinned_fallback:
        plan = replace(
            plan,
            reasons=(
                *plan.reasons,
                "Close-up without FaceSwap support: inverted guide fallback (pinned per shot).",
            ),
        )
    if settings.draft and policy.draft is not None:
        plan = replace(
            plan,
            reasons=(
                *plan.reasons,
                "Draft: one likeness retry; geometry advice and mouth failures do not retry.",
            ),
        )
    return plan


def _with_notes(plan, notes, facts, report):
    """The reading's notes after the round's reasons: speech under an audio lock, objects both a performer and a
    bystander touch, and a report without object concepts."""
    if plan.arm is not None and plan.arm.audio_lock:
        notes = tuple(
            note[:-1] + "; the render is locked to the window's speech."
            if note.startswith("Speaking:") or note == "Speaking (requested)."
            else note
            for note in notes
        )
    plan = replace(plan, reasons=(*plan.reasons, *notes))
    selected = {p.track_id for p in facts.performers}
    bystanders = {p.track_id for p in facts.bystanders}
    required = {o for p in selected for o in report["contacts"].get(p, ())}
    shared = required & {
        o for p, objects in report["contacts"].items() if p in bystanders for o in objects
    }
    if shared:
        plan = replace(
            plan,
            reasons=(
                *plan.reasons,
                "Objects touching both performer and bystander are required context only: "
                + ", ".join(sorted(shared)),
            ),
        )
    if not any(c != "person" for c in report["concepts"]):
        plan = replace(
            plan,
            reasons=(
                *plan.reasons,
                "No object concepts were analysed: context comes from explicit boxes and bystanders; inversion protects other people only.",
            ),
        )
    return plan
