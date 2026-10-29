"""Compose ordinary ADR 0011 operations into a windowed shot."""

import hashlib
from dataclasses import replace
from typing import Literal

from gaiavfx.application.replace_character import (
    BridgeChoice,
    HandoffChoice,
    HeadPassChoice,
    PerformerChoice,
    ReplacementPlanningError,
    ReplacementRequest,
    _facts,
    _initial,
    _report,
    _request,
    effective_policy,
    operations,
    start_people_analysis,
    start_replacement,
)
from gaiavfx.application.shot_assembly import (
    chain_take,
    shot_versions,
    start_shot_assembly,
    window_takes,
)
from gaiavfx.integrations import minimax_h3
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.direction.replacement import (
    CropPlan,
    from_record,
    parse_replacement_policy,
    to_record,
    union_crop,
)
from gaiavfx.modules.direction.windows import shot_handoff, shot_windows
from gaiavfx.shared.errors import ApplicationError

__all__ = [
    "ReplacementPlanningError",
    "chain_take",
    "continue_shot_replacement",
    "shared_crop_for_windows",
    "shot_versions",
    "shot_windows",
    "start_shot_assembly",
    "start_shot_bridge",
    "start_shot_head_pass",
    "start_shot_replacement",
    "start_window_analyses",
    "window_takes",
]


def _key(request_key, index, namespace):
    if not isinstance(request_key, str) or not request_key or len(request_key) > 80:
        raise ApplicationError("Request key must contain 1 to 80 characters.", 422)
    return hashlib.sha256(f"{namespace}:{request_key}:{index}".encode()).hexdigest()


def start_window_analyses(
    session,
    library,
    *,
    source_asset,
    shot_start,
    shot_end,
    faces_operation,
    concepts=("person",),
    request_key,
):
    """Create window analyses with sha-frozen faces.npz and cast.json inputs.

    Each track carries identity {cluster, film_tracks, votes, margin}; film_tracks
    counts raw FilmReport track votes for B's overrides. No film report is copied
    into requests or snapshots. See window_identities for the margin method.
    """
    from gaiavfx.application.film_analysis import film_analysis_report

    report = film_analysis_report(session, library, faces_operation)
    if report is None or report["source_asset"] != source_asset:
        raise ApplicationError(
            "Film analysis must finish for this source before window analysis.", 409
        )
    operation = operations.operation(session, faces_operation)
    # The merged film-wide faces come from the cast step. Chunked scans also emit per-chunk
    # .npz reports, so never select by file type.
    faces = {
        output["artifact_id"]
        for job in operation["jobs"]
        if job.get("step") == "film_cast"
        for output in job["outputs"]
        if output.get("artifact_id") and output.get("output_key") == "faces"
    }
    if len(faces) != 1:
        raise ApplicationError("Film analysis has no unique faces artifact.", 409)
    casts = {
        output["artifact_id"]
        for job in operation["jobs"]
        for output in job["outputs"]
        if output.get("artifact_id") and output.get("output_key") == "cast"
    }
    if len(casts) != 1:
        raise ApplicationError("Film analysis has no unique cast artifact.", 409)

    def binding(artifact_id):
        artifact = library.artifact(session, artifact_id)
        return {
            "source": "artifact",
            "directory": "input",
            "artifact_id": artifact["id"],
            "sha256": artifact["sha256"],
        }

    with session.begin_nested():
        return [
            start_people_analysis(
                session,
                source_asset,
                start,
                end,
                _key(request_key, index, "window-analysis"),
                concepts=concepts,
                faces=binding(next(iter(faces))),
                cast=binding(next(iter(casts))),
            )
            for index, (start, end, _) in enumerate(shot_windows(shot_start, shot_end))
        ]


def shared_crop_for_windows(session, library, *, requests):
    """Plan through Direction on every window's bound, authoritative facts."""
    policy = effective_policy(replacement_policy_text())
    facts = [_facts(session, _request(r), library, policy)[0] for r in requests]
    return to_record(union_crop(facts, policy, minimax_h3.capabilities().replacement))


def start_shot_replacement(
    session,
    library,
    *,
    source_asset,
    window_analyses: list[str],
    performers: list[dict],
    mode: Literal["draft", "final"],
    seed: int,
    request_key: str,
    budget_limit: int | None = None,
    handoff=None,
    faceswap_allowed: bool = True,
):
    """Validate the shot and start its fresh windows or first latent window.

    Performers are {tracks: {analysis_id: track_id}, target_asset: str,
    selected_by?: "identity" | "user", margins?: {analysis_id: float | None}}.
    Identity is the default; user-confirmed tracks need no identity margin, and
    margins (over the caller's characters) replace the windows' cluster margins. Draft uses
    its frozen policy's one-take rounds and likeness retry; final uses the full loop.
    Treatment and FaceSwap stay fixed per shot. A planned shot crop also pins framing.
    A blocked plan raises ReplacementPlanningError (ApplicationError, status 409)
    with ``gaps: tuple[Gap, ...]``; no window operations survive that failure.
    """
    if mode not in {"draft", "final"} or not 1 <= len(performers) <= 2:
        raise ApplicationError("Choose draft or final and one or two performers.", 422)
    if not window_analyses or len(set(window_analyses)) != len(window_analyses):
        raise ApplicationError("Shot needs distinct window analyses.", 422)
    reports = [
        _report(session, operations.operation(session, key), library) for key in window_analyses
    ]
    expected = shot_windows(reports[0]["shot_start"], reports[-1]["shot_end"])
    if [(r["shot_start"], r["shot_end"]) for r in reports] != [(a, b) for a, b, _ in expected]:
        raise ApplicationError("Analyses must cover the shot in window order.", 422)
    if any(r["source_asset"] != source_asset for r in reports):
        raise ApplicationError("Window analysis belongs to a different source.", 422)
    if any(set(p["tracks"]) != set(window_analyses) for p in performers):
        raise ApplicationError("Every performer needs a track in every window.", 422)
    if any(p.get("selected_by", "identity") not in {"identity", "user"} for p in performers):
        raise ApplicationError("selected_by must be identity or user.", 422)

    def request_for(analysis):
        return ReplacementRequest(
            source_asset,
            analysis,
            tuple(
                PerformerChoice(
                    p["tracks"][analysis],
                    p["target_asset"],
                    selected_by=p.get("selections", {}).get(
                        analysis, p.get("selected_by", "identity")
                    ),
                    identity_margin=p.get("margins", {}).get(analysis),
                )
                for p in performers
            ),
            seed=seed,
        )

    requests = [request_for(a) for a in window_analyses]
    if handoff is None:
        handoff = {"kind": shot_handoff(len(requests)), "joins": []}
        if handoff["kind"] == "latent":
            try:
                handoff["crop"] = shared_crop_for_windows(session, library, requests=requests)
            except ValueError:
                handoff = {"kind": "crossfade", "reason": "no_shared_crop", "joins": []}
    kind = handoff["kind"]
    crop = (
        from_record(CropPlan, handoff["crop"])
        if kind in {"latent", "bridge"} and handoff.get("crop") is not None
        else None
    )
    with session.begin_nested():
        first = replace(requests[0], crop_override=crop, save_latent=kind == "latent")
        if not faceswap_allowed:
            # A target without a human face (a robot) never takes FaceSwap, in any window.
            first = replace(first, faceswap_override=False)
        facts, _, text, plan = _initial(session, first, library, draft=mode == "draft")
        if plan.arm is None:
            raise ReplacementPlanningError(plan.gaps)
        result = []
        for index, request in enumerate([first] if kind == "latent" else requests):
            request = replace(
                request,
                crop_override=crop,
                treatment_override=plan.arm.treatment,
                **(
                    {"faceswap_override": plan.arm.faceswap, "close_up": facts.close_up}
                    if parse_replacement_policy(text).first_arm_faceswap_configured
                    or not faceswap_allowed
                    else {}
                ),
            )
            result.append(
                start_replacement(
                    session,
                    request,
                    budget_limit,
                    _key(request_key, index, "window-replacement"),
                    library=library,
                    draft=mode == "draft",
                )
            )
        return result


def start_shot_bridge(
    session,
    library,
    *,
    source_asset,
    predecessor,
    successor,
    join,
    crop,
    seed,
    request_key,
    budget_limit=None,
):
    """One ordinary replacement, frozen on the chosen neighbour candidates."""
    previous, following = predecessor["window"], successor["window"]
    successor_request = operations.operation(session, following["operation_id"])["request"]
    request = ReplacementRequest(
        source_asset,
        successor_request["analysis_operation"],
        tuple(
            PerformerChoice(p["track_id"], p["target_asset"], selected_by="user")
            for p in successor_request["facts"]["performers"]
        ),
        seed=seed,
        crop_override=from_record(CropPlan, crop),
        bridge=BridgeChoice(
            join,
            (previous["operation_id"], previous["candidate_id"]),
            (following["operation_id"], following["candidate_id"]),
        ),
        faceswap_override=successor_request.get("faceswap_override"),
        close_up=successor_request["facts"].get("close_up", False),
    )
    return start_replacement(
        session,
        request,
        budget_limit,
        request_key,
        library=library,
        renders=1,
        continuation_snapshot=successor_request,
    )


class ChainBroken(ApplicationError):
    def __init__(self):
        super().__init__("Predecessor has no succeeded composite to chain.", 409)


def continue_shot_replacement(
    session,
    library,
    *,
    source_asset,
    window_analyses,
    window_operations,
    performers,
    mode,
    seed,
    request_key,
    budget_limit=None,
    predecessor_take=None,
    handoff=None,
):
    """Start the next window from a deterministic delivered predecessor take."""
    index = len(window_operations)
    if not 1 <= index < len(window_analyses):
        raise ApplicationError("Continuation needs an incomplete, started shot.", 422)
    predecessor = operations.operation(session, window_operations[-1])
    take = (
        predecessor_take
        if predecessor_take is not None
        else chain_take(session, library, predecessor)
    )
    if take is None:
        raise ChainBroken()
    first = operations.operation(session, window_operations[0])
    if first["request"].get("chain") or not first["request"].get("save_latent"):
        raise ApplicationError("Pixel chaining is superseded; retake the shot.", 409)
    if not predecessor["request"].get("save_latent"):
        raise ApplicationError("Predecessor must save a latent for continuation.", 409)
    crop = from_record(CropPlan, first["request"]["crop_override"])
    analysis = window_analyses[index]
    request = ReplacementRequest(
        source_asset,
        analysis,
        tuple(
            PerformerChoice(
                p["tracks"][analysis],
                p["target_asset"],
                selected_by=p.get("selections", {}).get(analysis, p.get("selected_by", "identity")),
                identity_margin=p.get("margins", {}).get(analysis),
            )
            for p in performers
        ),
        seed=seed,
        treatment_override=first["request"]["first_plan"]["arm"]["treatment"],
        faceswap_override=first["request"].get("faceswap_override"),
        close_up=(
            first["request"]["facts"].get("close_up", False)
            if first["request"].get("faceswap_override") is not None
            else None
        ),
        handoff=HandoffChoice(predecessor["id"], take["window"]["candidate_id"]),
        crop_override=crop,
        save_latent=index < len(window_analyses) - 1,
    )
    return start_replacement(
        session,
        request,
        budget_limit,
        _key(request_key, index, "window-replacement"),
        library=library,
        draft=mode == "draft",
        continuation_snapshot=first["request"],
    )


def start_shot_head_pass(
    session,
    library,
    *,
    source_asset,
    window_analyses,
    performer,
    parent_version,
    seed,
    request_key,
    budget_limit=None,
):
    """One head operation per parent window and bridge, atomically in delivery order."""
    windows = parent_version["windows"]
    if (
        not window_analyses
        or len(windows) != len(window_analyses)
        or len(set(window_analyses)) != len(window_analyses)
    ):
        raise ApplicationError("Head pass needs one parent take per window.", 422)
    if set(performer["tracks"]) != set(window_analyses):
        raise ApplicationError("The performer needs a track in every window.", 422)
    reports = [_report(session, operations.operation(session, a), library) for a in window_analyses]
    expected = shot_windows(reports[0]["shot_start"], reports[-1]["shot_end"])
    if [(r["shot_start"], r["shot_end"]) for r in reports] != [(a, b) for a, b, _ in expected]:
        raise ApplicationError("Analyses must cover the shot in window order.", 422)
    result = []
    with session.begin_nested():
        for index, (analysis, window, report) in enumerate(
            zip(window_analyses, windows, reports, strict=True)
        ):
            parent_op = operations.operation(session, window["operation_id"])
            candidate = operations.candidate(session, window["candidate_id"])
            facts = parent_op["request"]["facts"]
            if (
                candidate["operation_id"] != parent_op["id"]
                or parent_op["request"]["analysis_operation"] != analysis
                or (facts["source_asset"], facts["shot_start"], facts["shot_end"])
                != (source_asset, report["shot_start"], report["shot_end"])
            ):
                raise ApplicationError("Parent takes must match the source window analyses.", 422)
            request = ReplacementRequest(
                source_asset,
                analysis,
                (
                    PerformerChoice(
                        performer["tracks"][analysis],
                        performer["targets"][analysis]
                        if "targets" in performer
                        else performer["target_asset"],
                        selected_by=performer.get("selected_by", "identity"),
                        identity_margin=performer.get("margins", {}).get(analysis),
                    ),
                ),
                seed=seed,
                head=HeadPassChoice(candidate["asset_id"]),
            )
            result.append(
                start_replacement(
                    session,
                    request,
                    budget_limit,
                    _key(request_key, index, "window-head"),
                    library=library,
                    renders=1,
                )
            )
        for bridge in parent_version.get("bridges", []):
            parent_op = operations.operation(session, bridge["operation_id"])
            candidate = operations.candidate(session, bridge["candidate_id"])
            frozen = parent_op["request"].get("bridge")
            if not frozen or candidate["operation_id"] != parent_op["id"]:
                raise ApplicationError(
                    "Head bridge parent must be the delivered bridge candidate.", 422
                )
            analysis = parent_op["request"]["analysis_operation"]
            previous, following = frozen["windows"]
            request = ReplacementRequest(
                source_asset,
                analysis,
                (
                    PerformerChoice(
                        performer["tracks"][analysis],
                        performer["targets"][analysis]
                        if "targets" in performer
                        else performer["target_asset"],
                        selected_by="user",
                    ),
                ),
                seed=seed,
                head=HeadPassChoice(candidate["asset_id"]),
                bridge=BridgeChoice(
                    frozen["join"],
                    (previous["operation"], previous["candidate_id"]),
                    (following["operation"], following["candidate_id"]),
                ),
            )
            result.append(
                start_replacement(
                    session,
                    request,
                    budget_limit,
                    _key(request_key, frozen["join"], "bridge-head"),
                    library=library,
                    renders=1,
                )
            )
    return result
