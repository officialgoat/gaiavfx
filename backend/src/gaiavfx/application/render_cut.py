"""Lower a ready Direction segment to immutable H3 render jobs."""

from gaiavfx.application import operation_start, take_basis
from gaiavfx.application.authored import segment_to_dict
from gaiavfx.application.direction_plans import segment_render_inputs
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.infrastructure.database import new_id
from gaiavfx.integrations import minimax_h3
from gaiavfx.integrations.minimax_h3.window import lower
from gaiavfx.modules.direction.segment import PictureRole
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

# Direction as the application reads it, with the sheet and set-view readers (ADR 0019 §3).
direction = DIRECTION
operations = OperationService()


def operations_for_asset(session, asset_id):
    """Use the Generation reader to follow a candidate's owning operation."""
    for candidate in operations.candidates_for_asset(session, asset_id):
        yield operations.operation(session, candidate["operation_id"])


def start_render(
    session,
    *,
    segment_id=None,
    cut_id=None,
    seeds,
    sheet_asset=None,
    context_artifact=None,
    budget_limit=None,
    request_key=None,
):
    if (segment_id is None) == (cut_id is None):
        raise ApplicationError("Exactly one of segment_id or cut_id is required.", 422)
    if cut_id is not None:
        segment_id = direction.read_cut(session, cut_id)["segment_id"]
    segment_row = direction.read_segment(session, segment_id)
    movie_id = segment_row["movie_id"]
    inputs_plan = segment_render_inputs(
        session, segment_id, sheet_asset=sheet_asset, context_artifact=context_artifact
    )
    plan, segment, artifact = inputs_plan.plan, inputs_plan.segment, inputs_plan.artifact
    if plan.refusals:
        refusal = plan.refusals[0]
        raise ApplicationError(f"{refusal.code}: {refusal.message}", refusal.status)
    sheet_asset = plan.sheet_asset
    context_artifact = artifact["id"] if artifact else None
    if not seeds or len(seeds) != len(set(seeds)):
        raise ApplicationError("Render seeds must be distinct.", 422)
    jobs = []
    for seed in seeds:
        prefix = f"gaiavfx/{new_id()}"
        lowered = lower(inputs_plan.window, prefix=prefix, seed=seed)
        inputs = {
            asset_id: {
                "sha256": MediaLibrary.asset_info(session, asset_id)["sha256"],
                "remote_name": remote,
            }
            for asset_id, remote in lowered.files.items()
        }
        if artifact:
            inputs[artifact["id"]] = {
                "source": "artifact",
                "sha256": artifact["sha256"],
                "remote_name": lowered.context_file,
                "directory": "output",
            }
        jobs.append(
            JobSpec(
                "video",
                "comfy",
                "minimax_h3",
                seed=seed,
                inputs=inputs,
                graph=lowered.graph,
                snapshot={
                    "knowledge": operation_start.knowledge_snapshot(minimax_h3),
                    "output_prefix": prefix,
                    "technique": plan.technique_id,
                    **lowered.snapshot,
                },
            )
        )
    request = {
        "cut_ids": [cut.cut_id for cut in plan.cuts],
        "movie_id": movie_id,
        "segment_id": segment_id,
        "technique": plan.technique_id,
        "blocks": list(
            dict.fromkeys(
                block
                for shot in ((plan.opening,) if plan.opening else ()) + plan.cuts
                for block in shot.recommended.blocks
            )
        ),
        "cut_techniques": {cut.cut_id: cut.recommended.technique_id for cut in plan.cuts},
        "opening_technique": plan.opening.recommended.technique_id if plan.opening else None,
        "seeds": seeds,
        **({"sheet_asset": sheet_asset} if sheet_asset is not None else {}),
        "context_artifact": context_artifact,
        "context_source": inputs_plan.source,
        "continuation": inputs_plan.window.continuation,
        **take_basis.record(session, segment_row, inputs_plan.keyframes),
        "segment": segment_to_dict(
            segment, tuple(p for p in inputs_plan.pictures if p.role is PictureRole.IDENTITY)
        ),
    }
    return operation_start.create_operation(
        session,
        kind="render",
        request_key=request_key,
        request=request,
        jobs=jobs,
        budget_limit=budget_limit,
    )


def open_takes_review(session, operation_id):
    operation = operations.operation(session, operation_id)
    if operation["kind"] != "render" or operation["state"] not in {"succeeded", "partial"}:
        raise ApplicationError("Render must finish before review.", 409)
    review = ReviewService()
    try:
        return review.blind(session, operation_id)
    except ApplicationError as exc:
        if exc.status != 404:
            raise
    request = operation["request"]
    return review.create(
        session,
        purpose="segment_takes",
        candidate_ids=[item["id"] for item in operation["candidates"]],
        context={key: request[key] for key in ("movie_id", "segment_id")}
        | {
            "cut_ids": request.get("cut_ids", [request["cut_id"]] if request.get("cut_id") else []),
            "operation_id": operation_id,
        },
        set_id=operation_id,
    )
