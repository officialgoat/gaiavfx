"""Shot-local character replacement use cases; no editorial selection authority."""

import json
import re
from contextlib import contextmanager
from copy import deepcopy
from dataclasses import replace
from fractions import Fraction

from gaiavfx.application import operation_start
from gaiavfx.application.candidate_review import existing_review
from gaiavfx.application.replacement_checks import take_checks
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
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL, JobSpec
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.modules.recast.application import replacement_policy_text
from gaiavfx.modules.recast.replacement import (
    Arm,
    Attempt,
    Box,
    CropPlan,
    Gap,
    ReplacementFacts,
    from_record,
    parse_replacement_policy,
    to_record,
)
from gaiavfx.modules.recast.replacement_plan import (
    BridgeReading,
    Refused,
    RoundSettings,
    WindowReading,
    bridge_facts,
    effective_policy,
    parse_request,
    plan_round,
    plan_window,
)
from gaiavfx.modules.recast.windows import bridge_layout
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
    return operation_start.create_operation(
        session,
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
                    "knowledge": operation_start.knowledge_snapshot(person_analysis),
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


def analysis_report(session, operation, library):
    """The finished people analysis's report: the tracks, faces and concepts a replacement plans from."""
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
            "report": analysis_report(session, op, library) if op["state"] == "succeeded" else None,
        }
        for op in rows
    ]


@contextmanager
def planning_errors():
    """The Replacement plan's refusals as the application's: the person's words, status 422."""
    try:
        yield
    except Refused as exc:
        raise ApplicationError(str(exc), 422) from exc


def window_reading(session, request, library) -> WindowReading:
    """What the Replacement plan needs read for a window: its people report, the kind of each performer's target
    picture and whether the source has audio."""
    analysis = operations.operation(session, request.analysis_operation)
    return WindowReading(
        analysis_report(session, analysis, library),
        {
            p.target_asset: MediaLibrary.asset_info(session, p.target_asset)["kind"]
            for p in request.performers
        },
        MediaLibrary.asset_info(session, request.source_asset)["info"]["has_audio"],
    )


def plan_request(
    session, request, library, renders=None, *, continuation_snapshot=None, draft=False
):
    """The window plan and the frozen policy text of a replacement request; the single-shot producer and each Recast
    window plan through it. This reads the window; the Replacement plan decides."""
    if continuation_snapshot is not None and request.handoff is None and request.bridge is None:
        raise ApplicationError("Frozen continuation settings require a latent window.", 422)
    try:
        text = (
            continuation_snapshot["policy"]
            if continuation_snapshot is not None
            else replacement_policy_text()
        )
        cap = minimax_h3.capabilities()
        support = cap.replacement
        if continuation_snapshot is not None:
            # A continuation keeps its first window's FaceSwap support (read only for close-ups).
            frozen = parse_render(continuation_snapshot["knowledge"]["render.toml"])
            support = replace(
                support, faceswap=minimax_h3.capabilities(frozen).replacement.faceswap
            )
        with planning_errors():
            if request.bridge:
                facts, bridge, successor = _bridge_data(
                    session, request.bridge, request.source_asset
                )
                reading = BridgeReading(facts, tuple(bridge["windows"]), successor)
            else:
                # A render count the policy refuses is refused before the window is read.
                effective_policy(text, renders, draft=draft)
                reading = window_reading(session, request, library)
            planned = plan_window(
                request, reading, text, support, cap.frame_grid, renders=renders, draft=draft
            )
        facts = planned.facts
        if getattr(library, "media", None) is not None and hasattr(library, "path"):
            info = library.media.probe(
                library.path(session, facts.source_asset),
                frame_range=(facts.shot_start, facts.shot_end),
            )
            if info["variable_frame_rate"]:
                raise ReplacementPlanningError(
                    (Gap("unsupported_fps", "Variable frame rate is unsupported."),)
                )
        return planned, text
    except (TypeError, ValueError) as exc:
        raise ApplicationError(str(exc), 422) from exc


def replacement_plan(session, request, *, library, renders: int | None = None, draft=False):
    with planning_errors():
        request = parse_request(request)
    planned, _ = plan_request(session, request, library, renders, draft=draft)
    facts, plan = planned.facts, planned.plan
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
    with planning_errors():
        request = parse_request(request)
    planned, policy_text = plan_request(
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
    facts, plan = planned.facts, planned.plan
    if plan.arm is None:
        raise ReplacementPlanningError(plan.gaps)
    with planning_errors():
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
        "tracks": planned.tracks,
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
        else operation_start.knowledge_snapshot(minimax_h3),
        "has_audio": MediaLibrary.asset_info(session, facts.source_asset)["info"]["has_audio"],
    }
    return operation_start.create_operation(
        session,
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
    with planning_errors():
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
        cap = minimax_h3.capabilities()
        support = cap.replacement
        # Only close-ups need the optional frozen capability; older requests and
        # non-close-up planning do not depend on render knowledge.
        if facts.close_up:
            render_text = request.get("knowledge", {}).get("render.toml")
            support = replace(
                support,
                faceswap=bool(
                    render_text
                    and minimax_h3.capabilities(parse_render(render_text)).replacement.faceswap
                ),
            )
        with planning_errors():
            plan = plan_round(
                facts, RoundSettings.frozen(request), support, cap.frame_grid, tuple(attempts)
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
    if review := existing_review(session, operation_id):
        known = {m["candidate_id"] for m in review["members"]}
        added = [c["id"] for c in operation["candidates"] if c["id"] not in known]
        return (
            reviews.add(session, operation_id, added, expected_revision=review["revision"])
            if added
            else review
        )
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
