"""Shot-local character replacement use cases; no editorial selection authority."""

import json
import math
import re
from copy import deepcopy
from dataclasses import dataclass, replace
from fractions import Fraction
from statistics import median

from gaiavfx.application.replacement_checks import take_checks
from gaiavfx.application.replacement_selectors import position, selector_positions
from gaiavfx.infrastructure.database import new_id
from gaiavfx.integrations import minimax_h3, person_analysis
from gaiavfx.integrations.minimax_h3.graph import context_descriptor, parse_render
from gaiavfx.integrations.minimax_h3.replacement import (
    build_replacement_graph,
    compile_replacement,
    parse_replacement_wording,
)
from gaiavfx.integrations.person_analysis.face_check import build_face_check_graph
from gaiavfx.integrations.person_analysis.graph import build_analysis_graph
from gaiavfx.modules.direction import person_crop
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.direction.mouth import MouthSeries, quiet_facts
from gaiavfx.modules.direction.replacement import (
    Arm,
    Attempt,
    Box,
    Bystander,
    CropPlan,
    Gap,
    Letterbox,
    Occluder,
    Performer,
    ReplacementFacts,
    Track,
    from_record,
    minor_bystander_reason,
    parse_replacement_policy,
    plan_bridge,
    plan_head_pass,
    plan_replacement,
    to_record,
)
from gaiavfx.modules.direction.windows import bridge_layout
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL, JobSpec
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

operations = OperationService()
reviews = ReviewService()


class ReplacementPlanningError(ApplicationError):
    """A blocked replacement plan, with its typed Gap values in ``gaps``."""

    def __init__(self, gaps):
        self.gaps = tuple(gaps)
        super().__init__(
            "Replacement cannot start: " + "; ".join(f"{g.code}: {g.message}" for g in gaps), 409
        )


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


def _chosen_window(session, operation_id, candidate_id, source_asset):
    predecessor = operations.operation(session, operation_id)
    if (
        predecessor["kind"] != "character_replacement"
        or predecessor["state"] not in OPERATION_TERMINAL
    ):
        raise ApplicationError("Handoff neighbour must be a settled character replacement.", 409)
    previous = from_record(ReplacementFacts, predecessor["request"]["facts"])
    if previous.source_asset != source_asset:
        raise ApplicationError("Handoff neighbour belongs to a different source.", 422)
    candidate = next((c for c in predecessor["candidates"] if c["id"] == candidate_id), None)
    job = next(
        (
            j
            for j in predecessor["jobs"]
            if candidate
            and j["id"] == candidate["job_id"]
            and j["step"] == "replacement_composite"
            and j["state"] == "succeeded"
        ),
        None,
    )
    if job is None:
        raise ApplicationError("Handoff candidate must be a succeeded neighbour composite.", 422)
    asset = MediaLibrary.asset_info(session, candidate["asset_id"])
    info = asset["info"]
    if (
        asset["kind"] != "video"
        or (info["width"], info["height"]) != previous.native
        or Fraction(info["frame_rate"]) != previous.fps
        or round(info["duration"] * previous.fps) != previous.delivered_frames
    ):
        raise ApplicationError("Handoff composite must match the native neighbour window.", 422)
    render = next(
        (
            j
            for j in predecessor["jobs"]
            if j["ordinal"] == job["depends_on"] and j["state"] == "succeeded"
        ),
        None,
    )
    if render is None:
        raise ApplicationError("Handoff candidate has no succeeded raw render.", 422)
    return predecessor, job, render


def _freeze_handoff(session, choice, facts, crop, library):
    predecessor, composite, render = _chosen_window(
        session, choice.predecessor_operation, choice.candidate_id, facts.source_asset
    )
    previous = from_record(ReplacementFacts, predecessor["request"]["facts"])
    if (
        previous.native != facts.native
        or previous.fps != facts.fps
        or previous.shot_end - facts.shot_start != 22
        or not previous.shot_start < facts.shot_start < previous.shot_end
        or crop is None
        or composite["snapshot"]["step_spec"]["crop"] != to_record(crop)
    ):
        raise ApplicationError("Latent handoff needs a shared crop and a 22-frame overlap.", 422)
    outputs = [o for o in render["outputs"] if o.get("context_artifact_id")]
    if len(outputs) != 1:
        raise ApplicationError("Predecessor render has no unique saved latent.", 409)
    output = outputs[0]
    artifact = MediaLibrary.artifact(session, output["context_artifact_id"])
    if artifact["sha256"] != output["context_sha256"]:
        raise ApplicationError("Saved latent differs from its frozen SHA-256.", 422)
    checks = next((o for o in composite["outputs"] if o.get("output_key") == "checks"), None)
    tone = (
        json.loads(library.read_blob(session, checks["artifact_id"])).get("tone_anchor", {})
        if checks
        else {}
    )
    return {
        **(
            {"tone": {"decision": tone["decision"], "final_gain": tone["final_gain"]}}
            if "decision" in tone
            else {}
        ),
        "kind": "latent",
        "predecessor_operation": predecessor["id"],
        "candidate_id": choice.candidate_id,
        "take": composite["seed"] - predecessor["request"]["seed"],
        "latent_artifact": artifact["id"],
        "sha256": artifact["sha256"],
        "crop": to_record(crop),
    }


def bridge_facts(predecessor_request, successor_request, layout):
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
        context_frames=0,
        handoff="bridge",
    )


def _bridge_data(session, choice, source_asset):
    neighbours = [
        _chosen_window(session, *pair, source_asset)
        for pair in (choice.predecessor, choice.successor)
    ]
    previous, successor = [n[0]["request"] for n in neighbours]
    layout = bridge_layout(
        [(r["facts"]["shot_start"], r["facts"]["shot_end"]) for r in (previous, successor)],
        successor["facts"]["shot_end"],
    )[0]
    layout.update(join=choice.join, generated=list(layout["generated"]))
    try:
        facts = bridge_facts(previous, successor, layout)
    except ValueError as exc:
        raise ReplacementPlanningError((Gap("bridge_facts_unresolved", str(exc)),)) from exc
    windows = []
    for (operation, composite, render), (_, candidate_id) in zip(
        neighbours, (choice.predecessor, choice.successor), strict=True
    ):
        request = operation["request"]
        outputs = [o for o in render["outputs"] if o.get("asset_id")]
        if len(outputs) != 1:
            raise ApplicationError("Bridge neighbour needs one frozen raw render.", 422)
        asset = MediaLibrary.asset_info(session, outputs[0]["asset_id"])
        spec = composite["snapshot"]["step_spec"]
        if (
            asset["sha256"] != outputs[0]["sha256"]
            or asset["kind"] != "video"
            or [asset["info"]["width"], asset["info"]["height"]] != spec["crop"]["canvas"]
            or Fraction(asset["info"]["frame_rate"]) != minimax_h3.capabilities().frame_grid.fps
            or round(asset["info"]["duration"] * Fraction(asset["info"]["frame_rate"]))
            != spec["window_frames"]
        ):
            raise ApplicationError("Bridge raw render differs from its frozen window.", 422)
        windows.append(
            {
                "operation": operation["id"],
                "analysis_operation": request["analysis_operation"],
                "candidate_id": candidate_id,
                "take": composite["seed"] - request["seed"],
                "render_asset": asset["id"],
                "render_sha256": asset["sha256"],
                "crop": spec["crop"],
                "arm": spec["arm"],
                "start": request["facts"]["shot_start"],
                "end": request["facts"]["shot_end"],
                "tracks": request["tracks"],
                "selected_tracks": spec["selected_tracks"],
                "ignored_tracks": spec.get("ignored_tracks", []),
            }
        )
    return facts, {**layout, "windows": windows}, successor


def _request(value):
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
            raise ApplicationError(f"Invalid replacement request: {exc}", 422) from exc
    if request.handoff is not None and (
        not isinstance(request.handoff, HandoffChoice)
        or any(
            not isinstance(v, str) or not v
            for v in (request.handoff.predecessor_operation, request.handoff.candidate_id)
        )
        or request.head is not None
    ):
        raise ApplicationError(
            "A latent handoff needs a predecessor and candidate, without a head pass.", 422
        )
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
        raise ApplicationError("A bridge needs a crop and two frozen neighbour candidates.", 422)
    if request.crop_override is not None and not isinstance(request.crop_override, CropPlan):
        raise ApplicationError("Invalid shared crop.", 422)
    if type(request.save_latent) is not bool:
        raise ApplicationError("save_latent must be boolean.", 422)
    if (request.save_latent or request.handoff) and (
        request.crop_override is None or request.head is not None
    ):
        raise ApplicationError("Latent windows need a pinned crop and cannot be head passes.", 422)
    if request.head is not None and (
        not isinstance(request.head, HeadPassChoice)
        or not isinstance(request.head.parent_asset, str)
        or not request.head.parent_asset
        or len(request.performers) != 1
        or request.treatment_override not in {None, "rgb"}
    ):
        raise ApplicationError("A head pass needs one performer and an RGB parent asset.", 422)
    if request.treatment_override not in {None, "rgb", "inverted"}:
        raise ApplicationError("Invalid treatment override.", 422)
    if request.faceswap_override is not None and type(request.faceswap_override) is not bool:
        raise ApplicationError("FaceSwap override must be boolean.", 422)
    if request.close_up is True and len(request.performers) != 1:
        raise ApplicationError("Close-up applies to a single performer", 422)
    if request.close_up is not None and type(request.close_up) is not bool:
        raise ApplicationError("Close-up must be boolean.", 422)
    if request.speaking is not None and type(request.speaking) is not bool:
        raise ApplicationError("Speaking must be boolean.", 422)
    if type(request.seed) is not int or not 0 <= request.seed <= 2**63 - 4:
        raise ApplicationError(
            "Replacement seed must leave room for four signed 64-bit library seeds.", 422
        )
    for name, limit in (
        ("performers", 4),
        ("required_context", 16),
        ("avoided_regions", 16),
        ("silent_intervals", 8),
    ):
        # Read-only planning can diagnose all four SAM person slots with a typed gap.
        # The HTTP boundary and executable plans admit at most two performers.
        if len(getattr(request, name)) > limit:
            raise ApplicationError(f"{name} is limited to {limit} entries.", 422)
    return request


def validate_concepts(concepts):
    if not isinstance(concepts, tuple) or not 1 <= len(concepts) <= 8:
        raise ApplicationError("Analysis needs one to eight noun-phrase concepts.", 422)
    result = []
    prose = {
        "is",
        "are",
        "was",
        "were",
        "please",
        "detect",
        "find",
        "replace",
        "then",
        "and",
        "or",
        "that",
        "which",
    }
    for concept in concepts:
        if (
            not isinstance(concept, str)
            or not 1 <= len(concept) <= 64
            or not re.fullmatch(r"[\w'-]+(?: [\w'-]+){0,3}", concept, flags=re.UNICODE)
            or set(concept.lower().split()) & prose
        ):
            raise ApplicationError(
                "Concepts must be short noun phrases of at most four words.", 422
            )
        result.append(concept.lower())
    if len(set(result)) != len(result):
        raise ApplicationError("Analysis concepts must be distinct.", 422)
    return tuple(result)


def start_people_analysis(
    session,
    source_asset_id,
    start,
    end,
    request_key=None,
    *,
    concepts=("person",),
    points=None,
    faces=None,
    cast=None,
):
    from gaiavfx.application.operations import PRODUCERS, create_operation, knowledge_snapshot

    concepts = validate_concepts(concepts)
    asset = MediaLibrary.asset_info(session, source_asset_id)
    info = asset["info"]
    if (
        asset["kind"] != "video"
        or type(start) is not int
        or type(end) is not int
        or not 0 <= start < end
    ):
        raise ApplicationError("Analysis needs a video and a positive frame range.", 422)
    fps = Fraction(info["frame_rate"])
    if end - start > 73 or end > round(info["duration"] * fps):
        raise ApplicationError("Analysis needs one shot of at most 73 frames.", 422)
    if points is not None:
        if not isinstance(points, dict) or set(points) - set(concepts):
            raise ApplicationError("Point corrections must name an analysed concept.", 422)
        for value in points.values():
            if (
                not isinstance(value, dict)
                or set(value) != {"positive", "negative"}
                or not value["positive"]
            ):
                raise ApplicationError(
                    "Point corrections need positive and negative coordinate lists.", 422
                )
            for group in value.values():
                if not isinstance(group, list) or len(group) > 32:
                    raise ApplicationError(
                        "Point corrections are limited to 32 points per label.", 422
                    )
                for point in group:
                    if (
                        not isinstance(point, dict)
                        or set(point) != {"x", "y"}
                        or any(
                            type(point[k]) not in {int, float} or not 0 <= point[k] < info[d]
                            for k, d in (("x", "width"), ("y", "height"))
                        )
                    ):
                        raise ApplicationError(
                            "Correction points must be inside the source frame.", 422
                        )
    remote = f"gaiavfx/{new_id()}/shot.mkv"
    source = {source_asset_id: {"sha256": asset["sha256"]}}
    policy = parse_replacement_policy(replacement_policy_text())
    if policy.fps_status(fps) == "unsupported":
        raise ReplacementPlanningError(
            (Gap("unsupported_fps", "Source frame rate is unsupported by replacement policy."),)
        )
    spec = {
        "source_asset": source_asset_id,
        "start": start,
        "end": end,
        "fps": str(fps),
        "concepts": list(concepts),
        "dilate": policy.dilate,
    }
    if cast is not None:
        spec["cast"] = cast
    if faces is not None:
        spec["faces"] = faces
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind="person_analysis",
        request={**spec, "points": points},
        budget_limit=None,
        budget=0,
        request_key=request_key,
        jobs=[
            JobSpec(
                "video",
                "local",
                person_analysis.NAME,
                step="analysis_extract",
                inputs=source,
                snapshot={"step_spec": spec, "intermediate": True},
            ),
            JobSpec(
                "analysis",
                "comfy",
                person_analysis.NAME,
                depends_on=0,
                inputs={"shot": {"source": "job_output", "remote_name": remote}},
                graph=build_analysis_graph(remote, concepts, points, frames=end - start),
                snapshot={
                    "knowledge": knowledge_snapshot(person_analysis),
                    "concepts": list(concepts),
                    "frames": end - start,
                    "native": [info["width"], info["height"]],
                },
            ),
            JobSpec(
                "analysis",
                "local",
                person_analysis.NAME,
                step="people_report",
                depends_on=1,
                inputs={
                    **source,
                    "analysis": {"source": "job_artifact", "output_key": "analysis"},
                    "window_faces": {
                        "source": "job_artifact",
                        "output_key": "window_faces:reports:0",
                    },
                    **({"faces": faces} if faces else {}),
                    **({"cast": cast} if cast else {}),
                },
                snapshot={"step_spec": spec},
            ),
        ],
    )


def _report(session, operation, library):
    if operation["kind"] != "person_analysis" or operation["state"] != "succeeded":
        raise ApplicationError("People analysis must finish before planning replacement.", 409)
    outputs = [
        d
        for j in operation["jobs"]
        if j["step"] == "people_report"
        for d in j["outputs"]
        if d.get("output_key") == "people" and d.get("artifact_id")
    ]
    if len(outputs) != 1:
        raise ApplicationError("People analysis has no complete report.", 409)
    return json.loads(library.read_blob(session, outputs[0]["artifact_id"]))


def people_for_asset(session, asset_id, *, library):
    MediaLibrary.asset_info(session, asset_id)
    rows = operations.recent(
        session, kind="person_analysis", scope={"source_asset": [asset_id]}, limit=100
    )
    return [
        {
            "operation_id": op["id"],
            "state": op["state"],
            "report": _report(session, op, library) if op["state"] == "succeeded" else None,
        }
        for op in rows
    ]


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
            raise ApplicationError("Close-up applies to a single performer", 422)
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


def _facts(session, request, library, policy):
    analysis = operations.operation(session, request.analysis_operation)
    report = _report(session, analysis, library)
    if report["source_asset"] != request.source_asset:
        raise ApplicationError("Analysis belongs to a different source asset.", 422)
    people = {t["id"]: t for t in report["tracks"] if t["concept"] == "person"}
    choices = {p.track_id: p for p in request.performers}
    if not choices or len(choices) != len(request.performers) or set(choices) - set(people):
        raise ApplicationError("Select distinct people from this shot's analysis.", 422)
    positions = selector_positions(
        [people[p.track_id] for p in request.performers], report["native"]
    )
    performers = []
    for choice, derived_position in zip(request.performers, positions, strict=True):
        target = MediaLibrary.asset_info(session, choice.target_asset)
        if target["kind"] != "image":
            raise ApplicationError("Replacement targets must be Library images.", 422)
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
        has_audio=MediaLibrary.asset_info(session, request.source_asset)["info"]["has_audio"],
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
    return facts, report, (*minor.values(), *mouth_reasons, *close_up_reasons)


def effective_policy(policy_text, renders=None, *, draft=False):
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
        raise ApplicationError(f"renders must be an integer from 1 to {policy.renders}.", 422)
    remaining, rounds = renders, []
    for count in policy.renders_per_round:
        if not remaining:
            break
        rounds.append(min(count, remaining))
        remaining -= rounds[-1]
    return replace(policy, renders=renders, renders_per_round=tuple(rounds), max_rounds=len(rounds))


def _plan(
    facts,
    policy_text,
    prior=(),
    renders=None,
    treatment_override=None,
    *,
    head=None,
    face_boxes=(),
    faceswap_supported=None,
    crop_override=None,
    bridge_arm=None,
    faceswap_override=None,
    draft=False,
):
    if prior and (head is not None or any(a.arm.framing == "head" for a in prior)):
        raise ValueError("head pass runs one round")
    cap = minimax_h3.capabilities()
    policy = effective_policy(policy_text, renders, draft=draft)
    if faceswap_supported is not None:
        cap = replace(cap, replacement=replace(cap.replacement, faceswap=faceswap_supported))
    pinned_fallback = (
        faceswap_override is False
        and treatment_override == "inverted"
        and facts.close_up
        and policy.first_arm_faceswap == "close_up"
        and policy.treatment == "rgb"
        and not cap.replacement.faceswap
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
        if renders != 1 or bridge_arm is None or crop_override is None:
            raise ApplicationError(
                "A bridge requires one render, its successor arm and a crop.", 422
            )
        plan = plan_bridge(
            facts, bridge_arm, crop_override, cap.replacement, policy, cap.frame_grid, prior
        )
    elif head is not None:
        if renders != 1:
            raise ApplicationError("A head pass requires renders = 1.", 422)
        plan = plan_head_pass(facts, face_boxes, cap.replacement, policy, cap.frame_grid, prior)
    else:
        plan = plan_replacement(
            facts, cap.replacement, policy, cap.frame_grid, prior, shared_crop=crop_override
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
    if draft and policy.draft is not None:
        plan = replace(
            plan,
            reasons=(
                *plan.reasons,
                "Draft: one likeness retry; geometry advice and mouth failures do not retry.",
            ),
        )
    return plan


def _initial(session, request, library, renders=None, *, continuation_snapshot=None, draft=False):
    if continuation_snapshot is not None and request.handoff is None and request.bridge is None:
        raise ApplicationError("Frozen continuation settings require a latent window.", 422)
    try:
        text = (
            continuation_snapshot["policy"]
            if continuation_snapshot is not None
            else replacement_policy_text()
        )
        bridge_arm = None
        if request.bridge:
            facts, bridge, successor = _bridge_data(session, request.bridge, request.source_asset)
            if request.analysis_operation != successor["analysis_operation"]:
                raise ApplicationError("Bridge analysis must be its successor's.", 422)
            report = {"tracks": successor["tracks"], "contacts": {}, "concepts": ["person"]}
            notes = ()
            bridge_arm = from_record(Arm, bridge["windows"][1]["arm"])
            if request.head:
                chosen = next(
                    (p for p in facts.performers if p.track_id == request.performers[0].track_id),
                    None,
                )
                if chosen is None or chosen.target_asset != request.performers[0].target_asset:
                    raise ApplicationError("Head bridge performer must match its body target.", 422)
                index = facts.performers.index(chosen)
                neighbours = bridge["windows"]
                tracks = [
                    next(t for t in w["tracks"] if t["id"] == w["selected_tracks"][index])
                    for w in neighbours
                ]
                boxes = []
                for frame in range(facts.shot_start, facts.shot_end):
                    side = int(frame >= neighbours[1]["start"])
                    series = (tracks[side].get("mouth") or {}).get("box")
                    boxes.append(series[frame - neighbours[side]["start"]] if series else None)
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
        else:
            facts, report, notes = _facts(
                session, request, library, effective_policy(text, renders, draft=draft)
            )
        if getattr(library, "media", None) is not None and hasattr(library, "path"):
            info = library.media.probe(
                library.path(session, facts.source_asset),
                frame_range=(facts.shot_start, facts.shot_end),
            )
            if info["variable_frame_rate"]:
                raise ReplacementPlanningError(
                    (Gap("unsupported_fps", "Variable frame rate is unsupported."),)
                )
        face_boxes = ()
        if request.head is not None:
            track = next(t for t in report["tracks"] if t["id"] == request.performers[0].track_id)
            face_boxes = (track.get("mouth") or {}).get("box", [None] * facts.delivered_frames)
            if report.get("mouth_scan") == "unavailable":
                face_boxes = [None] * facts.delivered_frames
        plan = _plan(
            facts,
            text,
            renders=renders,
            treatment_override=request.treatment_override,
            faceswap_override=request.faceswap_override,
            draft=draft,
            head=request.head,
            face_boxes=face_boxes,
            crop_override=request.crop_override,
            bridge_arm=bridge_arm,
            **(
                {
                    "faceswap_supported": minimax_h3.capabilities(
                        parse_render(continuation_snapshot["knowledge"]["render.toml"])
                    ).replacement.faceswap
                }
                if continuation_snapshot is not None and facts.close_up
                else {}
            ),
        )
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
        return facts, report, text, plan
    except (TypeError, ValueError) as exc:
        raise ApplicationError(str(exc), 422) from exc


def replacement_plan(session, request, *, library, renders: int | None = None, draft=False):
    request = _request(request)
    facts, _, _, plan = _initial(session, request, library, renders, draft=draft)
    result = to_record(plan)
    result["facts"] = {**to_record(facts), "fps": str(facts.fps)}
    result["crop_preview"] = (
        to_record(_crop(facts, plan.arm, compile_replacement(facts, plan.arm).canvas))
        if plan.arm
        else None
    )
    return result


def recorded_replacement_plan(session, operation_id):
    """Preview the immutable plan attached to a started replacement, without candidate details."""
    operation = operations.operation(session, operation_id)
    if operation["kind"] != "character_replacement":
        raise ApplicationError("Operation is not a character replacement.", 422)
    request = operation["request"]
    result = dict(request["first_plan"])
    facts = from_record(ReplacementFacts, request["facts"])
    arm = from_record(Arm, result["arm"]) if result["arm"] else None
    result["facts"] = {**to_record(facts), "fps": str(facts.fps)}
    result["crop_preview"] = (
        to_record(_crop(facts, arm, compile_replacement(facts, arm).canvas)) if arm else None
    )
    return result


def _crop(facts, arm, canvas):
    if arm.crop:
        return arm.crop
    if arm.framing == "active" and facts.letterbox:
        x, y = facts.letterbox.offset
        w, h = facts.letterbox.fit
    else:
        x, y, w, h = 0, 0, *facts.native
    # Fit the complete active frame into the fixed integration canvas. The extra
    # canvas area is black padding; inverse mapping restores every source pixel.
    scale = min(canvas[0] / w, canvas[1] / h)
    cw, ch = canvas[0] / scale, canvas[1] / scale
    x, y = x + (w - cw) / 2, y + (h - ch) / 2
    box = Box(x, y, x + cw, y + ch)
    return CropPlan(
        canvas,
        box,
        scale,
        (),
        0,
        None,
        ((scale, 0.0, scale * (0.5 - x) - 0.5), (0.0, scale, scale * (0.5 - y) - 0.5)),
    )


def round_render(request, arm):
    """Facts, render presets, compiled wording and crop of one round, from the frozen request.

    The single path from a stored replacement request to its H3 lowering inputs; research
    harnesses reuse it instead of compiling the wording themselves.
    """
    facts = from_record(ReplacementFacts, request["facts"])
    presets = parse_render(request["knowledge"]["render.toml"])
    compiled = compile_replacement(
        facts,
        arm,
        parse_replacement_wording(
            request["knowledge"]["head.toml" if arm.framing == "head" else "replacement.toml"]
        ),
        presets=presets,
        save_latent=request.get("save_latent", False),
    )
    if arm.crop is None:
        compiled = replace(compiled, canvas=tuple(presets.replacement["full_canvas"]))
    return facts, presets, compiled, _crop(facts, arm, compiled.canvas)


def _round_jobs(request, arm, start):
    facts, presets, compiled, crop = round_render(request, arm)
    policy = parse_replacement_policy(request["policy"])
    spec = {
        "facts": request["facts"],
        "arm": to_record(arm),
        "crop": to_record(crop),
        "tracks": request["tracks"],
        "selected_tracks": [p.track_id for p in facts.performers],
        "ignored_tracks": [
            t["id"]
            for t in request["tracks"]
            if t["concept"] == "person" and t["id"] not in {p.id for p in facts.tracks}
        ],
        "window_frames": compiled.window_frames,
        "policy": request["policy"],
        "face_measurement": True,
    }
    source = {facts.source_asset: {"sha256": request["sources"][facts.source_asset]["sha256"]}}

    if request.get("bridge"):
        spec["bridge"] = request["bridge"]
        if request.get("head"):
            spec["head_bridge_performer"] = request["bridge"]["windows"][1][
                "selected_tracks"
            ].index(facts.performers[0].track_id)
    if request.get("handoff"):
        spec["handoff"] = request["handoff"]
    if request.get("head"):
        spec["head"] = request["head"]
        source[request["head"]["parent_asset"]] = {"sha256": request["head"]["parent_sha256"]}

    def picture_inputs(prefix):
        return {
            asset_id: {
                "sha256": request["sources"][asset_id]["sha256"],
                "remote_name": f"{prefix}/picture_{index}.png",
            }
            for index, asset_id in enumerate(
                dict.fromkeys(p.target_asset for p in facts.performers)
            )
        }

    snapshot = {"step_spec": spec, "replacement_source": facts.source_asset}
    prepare_source = dict(source)
    prepare_spec = dict(spec)
    if request.get("bridge") and not request.get("head"):
        for window in request["bridge"]["windows"]:
            prepare_source[window["render_asset"]] = {"sha256": window["render_sha256"]}
    if request.get("chain"):
        prepare_spec["chain"] = request["chain"]
        prepare_source[request["chain"]["asset"]] = {"sha256": request["chain"]["sha256"]}
    jobs = [
        JobSpec(
            "video",
            "local",
            minimax_h3.NAME,
            step="replacement_prepare",
            inputs=prepare_source,
            snapshot={**snapshot, "step_spec": prepare_spec, "intermediate": True},
        )
    ]
    for i in range(arm.seed_count):
        seed = request["seed"] + arm.seed_offset + i
        # Fresh per-job folders also isolate identical inputs: overwrite=false can
        # rename any repeated upload, and the gateway must preserve graph bindings.
        prefix = f"gaiavfx/{new_id()}"
        guide = f"{prefix}/guide.mkv"
        context_file = f"{prefix}/context.mkv" if request.get("chain") else None
        bridge_file = f"{prefix}/bridge.mkv" if facts.handoff == "bridge" else None
        latent_file = f"{prefix}/context.safetensors" if request.get("handoff") else None
        pictures = picture_inputs(prefix)
        graph = build_replacement_graph(
            compiled,
            presets=presets,
            guide_file=guide,
            picture_files={key: value["remote_name"] for key, value in pictures.items()},
            seed=seed,
            output_prefix=prefix
            if compiled.save_latent
            else f"gaiavfx/replacement/r{arm.round}-s{seed}",
            context_file=context_file,
            latent_file=latent_file,
            bridge_file=bridge_file,
        )
        jobs.append(
            JobSpec(
                "video",
                "comfy",
                minimax_h3.NAME,
                depends_on=start,
                seed=seed,
                graph=graph,
                inputs={
                    "guide": {"source": "job_output", "output_key": "guide", "remote_name": guide},
                    **pictures,
                    **(
                        {
                            "pixels": {
                                "source": "job_output",
                                "output_key": "pixels",
                                "remote_name": bridge_file,
                            }
                        }
                        if bridge_file
                        else {}
                    ),
                    **(
                        {
                            "latent": {
                                "source": "artifact",
                                "artifact_id": request["handoff"]["latent_artifact"],
                                "sha256": request["handoff"]["sha256"],
                                "remote_name": latent_file,
                                "directory": "output",
                            }
                        }
                        if latent_file
                        else {}
                    ),
                    **(
                        {
                            "context": {
                                "source": "job_output",
                                "output_key": "context",
                                "remote_name": context_file,
                            }
                        }
                        if context_file
                        else {}
                    ),
                },
                snapshot={
                    **snapshot,
                    "knowledge": request["knowledge"],
                    "intermediate": True,
                    **(
                        {"capture_context": context_descriptor(prefix)}
                        if compiled.save_latent
                        else {}
                    ),
                },
            )
        )
        jobs.append(
            JobSpec(
                "video",
                "local",
                minimax_h3.NAME,
                step="replacement_composite",
                seed=seed,
                depends_on=start + len(jobs) - 1,
                inputs={**source, "render": {"source": "job_output"}},
                snapshot={
                    **snapshot,
                    "expect": {
                        "kind": "video",
                        "width": facts.native[0],
                        "height": facts.native[1],
                        "fps": float(facts.fps),
                        "frames": facts.delivered_frames,
                        "audio": request["has_audio"],
                    },
                },
            )
        )
        composite_index = start + len(jobs) - 1
        prefix = f"gaiavfx/{new_id()}"
        pictures = picture_inputs(prefix)
        bindings = [
            {
                "track_id": p.track_id,
                "mask_file": f"{prefix}/mask_{n}.mkv",
                "target_file": pictures[p.target_asset]["remote_name"],
            }
            for n, p in enumerate(facts.performers)
        ]
        jobs.append(
            JobSpec(
                "analysis",
                "comfy",
                person_analysis.NAME,
                depends_on=composite_index,
                graph=build_face_check_graph(
                    f"{prefix}/candidate.mkv",
                    f"{prefix}/source.mkv",
                    bindings,
                    min_face_height_fraction=policy.min_face_height_fraction,
                    min_face_frames_fraction=policy.min_face_frames_fraction,
                ),
                inputs={
                    "candidate": {
                        "source": "job_artifact",
                        "output_key": "candidate_pixels",
                        "remote_name": f"{prefix}/candidate.mkv",
                    },
                    "source": {
                        "source": "job_artifact",
                        "output_key": "source_pixels",
                        "remote_name": f"{prefix}/source.mkv",
                    },
                    **{
                        f"mask_{n}": {
                            "source": "job_artifact",
                            "output_key": f"performer_mask_{n}",
                            "remote_name": b["mask_file"],
                        }
                        for n, b in enumerate(bindings)
                    },
                    **pictures,
                },
                snapshot={
                    **snapshot,
                    "knowledge": {"replacement-policy.toml": request["policy"]},
                    "face_check": {
                        f"performer_{n}": p.track_id for n, p in enumerate(facts.performers)
                    },
                },
            )
        )
    return jobs


def start_replacement(
    session,
    request,
    budget_limit=None,
    request_key=None,
    *,
    library,
    renders: int | None = None,
    continuation_snapshot=None,
    draft=False,
):
    from gaiavfx.application.operations import PRODUCERS, create_operation, knowledge_snapshot

    request = _request(request)
    facts, report, policy_text, plan = _initial(
        session,
        request,
        library,
        renders,
        draft=draft,
        **(
            {"continuation_snapshot": continuation_snapshot}
            if continuation_snapshot is not None
            else {}
        ),
    )
    if plan.arm is None:
        raise ReplacementPlanningError(plan.gaps)
    policy = effective_policy(policy_text, renders, draft=draft)
    sources = {}
    for asset_id in (facts.source_asset, *(p.target_asset for p in facts.performers)):
        asset = MediaLibrary.asset_info(session, asset_id)
        sources[asset_id] = {"sha256": asset["sha256"]}
    head = None
    if request.head is not None:
        parent = MediaLibrary.asset_info(session, request.head.parent_asset)
        info = parent["info"]
        if (
            parent["kind"] != "video"
            or (info["width"], info["height"]) != facts.native
            or Fraction(info["frame_rate"]) != facts.fps
            or round(info["duration"] * facts.fps) != facts.delivered_frames
        ):
            raise ApplicationError(
                "Head parent must match the native window dimensions, rate and length.", 422
            )
        head = {"parent_asset": request.head.parent_asset, "parent_sha256": parent["sha256"]}
        sources[request.head.parent_asset] = {"sha256": parent["sha256"]}
    handoff = (
        _freeze_handoff(session, request.handoff, facts, request.crop_override, library)
        if request.handoff
        else None
    )
    bridge = (
        _bridge_data(session, request.bridge, request.source_asset)[1] if request.bridge else None
    )
    frozen = {
        **({"bridge": bridge} if bridge else {}),
        **({"handoff": handoff} if handoff else {}),
        **({"save_latent": True} if request.save_latent else {}),
        **({"crop_override": to_record(request.crop_override)} if request.crop_override else {}),
        **({"head": head} if head else {}),
        "source_asset": facts.source_asset,
        "analysis_operation": request.analysis_operation,
        "facts": to_record(facts),
        "tracks": report["tracks"],
        "policy": policy_text,
        "renders": renders,
        "treatment_override": request.treatment_override,
        "faceswap_override": request.faceswap_override,
        **({"draft": True} if draft else {}),
        "policy_limits": {"max_rounds": min(3, policy.max_rounds)},
        "first_plan": to_record(plan),
        "seed": request.seed,
        "first_seeds": [request.seed + i for i in range(plan.arm.seed_count)],
        "sources": sources,
        "knowledge": deepcopy(continuation_snapshot["knowledge"])
        if continuation_snapshot is not None
        else knowledge_snapshot(minimax_h3),
        "has_audio": MediaLibrary.asset_info(session, facts.source_asset)["info"]["has_audio"],
    }
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind="character_replacement",
        request=frozen,
        jobs=_round_jobs(frozen, plan.arm, 0),
        budget=policy.renders,
        budget_limit=budget_limit,
        request_key=request_key,
        awaits_review=True,
    )


def advance_replacement(session, operation, *, library):
    operation = operations.operation(session, operation["id"])
    if operation["state"] != "awaiting_review" or operation["cancel_requested"]:
        return operation
    request = operation["request"]
    policy = effective_policy(
        request["policy"], request.get("renders"), draft=request.get("draft", False)
    )
    if any(
        j.get("snapshot", {}).get("face_check")
        and j["state"] not in {"succeeded", "failed", "cancelled", "needs_attention"}
        for j in operation["jobs"]
    ):
        return operation
    decisions = [d for d in (operation["review"] or []) if d.get("decided_by") == "advancer"]
    round_index = len(decisions) + 1
    # A resume and its decision share the caller's transaction. Recovery observes both.
    if any(
        j["snapshot"].get("step_spec", {}).get("arm", {}).get("round") == round_index
        for j in operation["jobs"]
    ):
        return operation
    attempts = []
    reasons = []
    empty_round = False
    for index in range(round_index):
        jobs = [
            j
            for j in operation["jobs"]
            if j["step"] == "replacement_composite"
            and j["snapshot"]["step_spec"]["arm"]["round"] == index
        ]
        successful = [j for j in jobs if j["state"] == "succeeded"]
        if not successful:
            reasons.append(f"Round {index + 1} has no successful composites; closing the loop.")
            empty_round = True
            break
        if len(successful) != len(jobs):
            reasons.append(
                f"Round {index + 1}: using {len(successful)} successful composites of {len(jobs)} takes."
            )
        arm = from_record(Arm, jobs[0]["snapshot"]["step_spec"]["arm"])
        takes = []
        for job in successful:
            artifact = next(
                d["artifact_id"] for d in job["outputs"] if d.get("output_key") == "checks"
            )
            report = json.loads(library.read_blob(session, artifact))
            takes.append(take_checks(session, library, operation, job, report, policy))
        attempts.append(Attempt(arm, tuple(takes)))
    used = sum(
        j["executor"] == "comfy" and j["kind"] in {"image", "video"} for j in operation["jobs"]
    )
    remaining = operation["budget_reserved"] - used
    limit = min(3, policy.max_rounds, request["policy_limits"]["max_rounds"])
    plan = None
    arm = None
    if round_index >= limit:
        reasons.append("Maximum rounds reached; closing the loop.")
    elif remaining <= 0:
        reasons.append("Render reservation exhausted; closing the loop.")
    elif not empty_round:
        facts = from_record(ReplacementFacts, request["facts"])
        # Only close-ups need the optional frozen capability; older requests and
        # non-close-up planning do not depend on render knowledge.
        capability = {}
        if facts.close_up:
            render_text = request.get("knowledge", {}).get("render.toml")
            capability["faceswap_supported"] = bool(
                render_text
                and minimax_h3.capabilities(parse_render(render_text)).replacement.faceswap
            )
        plan = _plan(
            facts,
            request["policy"],
            tuple(attempts),
            renders=request.get("renders"),
            faceswap_override=request.get("faceswap_override"),
            draft=request.get("draft", False),
            **capability,
            **(
                {"crop_override": from_record(CropPlan, request["crop_override"])}
                if request.get("crop_override")
                else {}
            ),
            **(
                {"bridge_arm": from_record(Arm, request["bridge"]["windows"][1]["arm"])}
                if request.get("bridge")
                else {}
            ),
            **(
                {"treatment_override": request["treatment_override"]}
                if request.get("treatment_override")
                else {}
            ),
        )
        arm = plan.arm
        reasons.extend(plan.reasons)
        if arm and arm.seed_count > remaining:
            arm = replace(arm, seed_count=remaining)
            reasons.append("Next round limited to the remaining render reservation.")
    decision = {
        "decided_by": "advancer",
        "attempts": [to_record(a) for a in attempts],
        "round": round_index,
        "arm": to_record(arm) if arm else None,
        "gaps": [to_record(g) for g in plan.gaps] if plan else [],
        "reasons": reasons,
        "seeds": [request["seed"] + arm.seed_offset + i for i in range(arm.seed_count)]
        if arm
        else [],
    }
    return operations.resume(
        session,
        operation["id"],
        decision,
        jobs=_round_jobs(request, arm, len(operation["jobs"])) if arm else [],
        awaits_review=bool(arm),
    )


def open_replacement_review(session, operation_id):
    operation = operations.operation(session, operation_id)
    if operation["kind"] != "character_replacement" or not operation["candidates"]:
        raise ApplicationError("Replacement has no composited candidates to review.", 409)
    try:
        review = reviews.blind(session, operation_id)
        known = {m["candidate_id"] for m in review["members"]}
        added = [c["id"] for c in operation["candidates"] if c["id"] not in known]
        return (
            reviews.add(session, operation_id, added, expected_revision=review["revision"])
            if added
            else review
        )
    except ApplicationError as exc:
        if exc.status != 404:
            raise
    return reviews.create(
        session,
        purpose="character_replacement",
        candidate_ids=[c["id"] for c in operation["candidates"]],
        context={
            "operation_id": operation_id,
            "source_asset": operation["request"]["source_asset"],
            **{k: operation["request"]["facts"][k] for k in ("shot_start", "shot_end", "fps")},
        },
        set_id=operation_id,
    )


def approve_replacement(session, candidate_id):
    candidate = operations.candidate(session, candidate_id)
    operation = operations.operation(session, candidate["operation_id"])
    job = next((j for j in operation["jobs"] if j["id"] == candidate["job_id"]), None)
    if (
        job is None
        or operation["kind"] != "character_replacement"
        or job["step"] != "replacement_composite"
        or job["state"] != "succeeded"
    ):
        raise ApplicationError("Only a delivered replacement composite can be approved.", 409)
    facts, arm = operation["request"]["facts"], job["snapshot"]["step_spec"]["arm"]
    provenance = {
        "type": "character_replacement",
        "source_asset": facts["source_asset"],
        "shot": {"start": facts["shot_start"], "end": facts["shot_end"]},
        "operation": operation["id"],
        "round": arm["round"],
        "arm": arm,
    }
    return MediaLibrary.approve_derivative(
        session, candidate["asset_id"], facts["source_asset"], provenance
    )
