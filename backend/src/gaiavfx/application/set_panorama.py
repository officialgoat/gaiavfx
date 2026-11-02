"""A stage built from the panorama a person chose: a seed (`set_seed`'s stages and words, source "panorama") whose
geometry is MoGe-3 on the whole panorama with SAM 3's sky and glass finds, laid out by the stage engine as one
keyframe, K0, the stage's ground truth (`worldbuilder_splat/panorama.py`). The splat is then fitted from the
panorama's crops and, when asked, its unseen space filled by H3 probes that start from them (R5's expandable
splat). Only a chosen panorama is built from; v1 is proposed for the person to accept."""

from __future__ import annotations

import json
from hashlib import sha256

from gaiavfx.application import operation_start, panoramas, set_fill
from gaiavfx.application.direction_reading import DIRECTION as direction
from gaiavfx.application.engine_jobs import engine_job
from gaiavfx.application.set_seed import GEOMETRY_OUTPUT, MODE, unique_name
from gaiavfx.application.sets import clip_words
from gaiavfx.integrations import worldbuilder
from gaiavfx.integrations.worldbuilder import rules
from gaiavfx.integrations.worldbuilder.panorama import panorama_geometry_graph, settings
from gaiavfx.modules.generation.operations.attempts import attempts
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

SOURCE = "panorama"
ATLAS = "worldbuilder.atlas_from_panorama"
BUILD_VERSION = 1
SHORT_REFUSAL = "Refused: too little measured surface"
REFUSAL = "Too little of this panorama is measured surface to build a stage from: it may be mostly open sky or water."
operations = OperationService()


def _frozen(fill_unseen: bool) -> dict:
    return {
        "panorama": settings(),
        "splat": rules.rebuild(),
        "fill": set_fill.frozen() if fill_unseen else None,
    }


def _key(candidate_id: str, fingerprint: str, attempt: int) -> str:
    material = [MODE, SOURCE, candidate_id, BUILD_VERSION, fingerprint, attempt]
    return sha256(json.dumps(material).encode()).hexdigest()[:12]


def _chosen(session, candidate_id: str) -> tuple[dict, dict]:
    candidate = operations.candidate(session, candidate_id)
    operation = operations.operation(session, candidate["operation_id"])
    if operation["kind"] != panoramas.NAME or candidate["output_key"] != "panorama":
        raise ApplicationError("Only a panorama is built into a stage here.", 422)
    if panoramas.chosen(session, operation) != candidate_id:
        raise ApplicationError(
            "Choose this panorama first: a stage is built from the chosen one.", 409
        )
    return candidate, operation


def estimate(fill_unseen: bool) -> str:
    """What building means for the user's machine: MoGe-3 on the panorama, the fit, and filling's renders."""
    steps = rules.rebuild()["steps"]
    fit = f"MoGe-3 measures the panorama, then a {steps}-step splat is fitted from its crops"
    after = "It runs in Jobs while you keep working, and you can cancel it there."
    if not fill_unseen:
        return f"About 4 minutes of GPU work: {fit}. {after}"
    rounds = set_fill.frozen()["rounds"]
    return (
        f"Up to {rounds['renders']} renders, about {set_fill.FILL_TIME} of GPU work: {fit}, then up to "
        f"{rounds['rounds']} rounds of probes fill in what the panorama never saw. {after}"
    )


def stage_preview(session, library, candidate_id: str, *, fill_unseen: bool = True) -> dict:
    """What building would do, in words, before it does."""
    _, operation = _chosen(session, candidate_id)
    request = operation["request"]
    name = clip_words(request["place"] or "Panorama stage", 120)
    return {
        "name": unique_name(session, name),
        "what": (
            "MoGe-3 measures the panorama, the stage engine lays it out and fits a splat"
            + (", then H3 fills the space it never saw." if fill_unseen else ".")
        ),
        "estimate": estimate(fill_unseen),
    }


def start_panorama_stage(
    session,
    library,
    *,
    candidate_id: str,
    name: str | None = None,
    fill_unseen: bool = True,
    use_for_place: bool = False,
) -> dict:
    """Build a stage from the chosen panorama (or return the build already under way)."""
    candidate, operation = _chosen(session, candidate_id)
    request = operation["request"]
    asset = library.get(session, candidate["asset_id"])
    name = clip_words(name or request["place"] or "Panorama stage", 120)
    if not name:
        raise ApplicationError("Name the stage.", 422)
    place = None
    if use_for_place:
        if not request["place_id"]:
            raise ApplicationError("This panorama is not of a place.", 422)
        place = direction.read_place(session, request["place_id"])
        if place.get("set_id"):
            raise ApplicationError("This place is already built as a stage.", 409)
    frozen = _frozen(fill_unseen)
    fingerprint = sha256(json.dumps(frozen, sort_keys=True).encode()).hexdigest()[:16]
    chain = attempts(operations, session, lambda n: _key(candidate_id, fingerprint, n))
    existing, attempt = chain.in_flight()
    if existing is not None:
        return existing | {"_created": False}
    key = _key(candidate_id, fingerprint, attempt)
    prefix = f"gaiavfx/sets/panorama-{key}"
    remote = f"{prefix}/panorama.png"
    masks, gate = frozen["panorama"]["masks"], frozen["panorama"]["gate"]
    fill = frozen["fill"]
    return operation_start.create_operation(
        session,
        kind="set_build",
        request={
            "mode": MODE,
            "source": SOURCE,
            "panorama_operation_id": operation["id"],
            "candidate_id": candidate_id,
            "asset_id": asset["id"],
            "asset_sha256": asset["sha256"],
            "panorama": frozen["panorama"],
            "splat": frozen["splat"],
            "fill": fill,
            "settings": fingerprint,
            "name": unique_name(session, name),
            "place_id": place["id"] if place else None,
            "place_text": request["place"],
            "attempt": attempt,
        },
        request_key=key,
        budget_limit=None,
        # The probes' renders, reserved when the build starts (a later stage cannot add to its reservation).
        budget=fill["rounds"]["renders"] if fill else 0,
        awaits_review=True,
        jobs=[
            JobSpec(
                "analysis",
                "comfy",
                worldbuilder.NAME,
                inputs={asset["id"]: {"sha256": asset["sha256"], "remote_name": remote}},
                graph=panorama_geometry_graph(remote, settings=frozen["panorama"], prefix=prefix),
                snapshot={
                    "knowledge": operation_start.knowledge_snapshot(worldbuilder),
                    "stage": {"name": "geometry"},
                },
            ),
            engine_job(
                ATLAS,
                params={
                    "name": "seed",
                    "place": name,
                    "panorama": asset["id"],
                    "rules": {**masks, **gate},
                },
                inputs={
                    "geometry": {"source": "job_artifact", "output_key": GEOMETRY_OUTPUT},
                    asset["id"]: {"sha256": asset["sha256"]},
                },
                kind="analysis",
                depends_on=0,
                stage={"name": "atlas"},
            ),
        ],
    )


def retry_panorama_stage(session, library, previous: dict) -> dict:
    """A panorama build that stopped, tried again: the same chosen panorama, name, place and fill."""
    request = previous["request"]
    return start_panorama_stage(
        session,
        library,
        candidate_id=request["candidate_id"],
        name=request["name"],
        fill_unseen=bool(request.get("fill")),
        use_for_place=bool(request.get("place_id")),
    )
