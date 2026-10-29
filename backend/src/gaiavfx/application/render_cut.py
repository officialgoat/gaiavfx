"""Lower a ready Direction segment to immutable H3 render jobs."""

from gaiavfx.application.authored import segment_to_dict
from gaiavfx.application.direction_plans import (
    cut_references as _references,
)
from gaiavfx.application.direction_plans import (
    plan_for_cut,
    segment_render_inputs,
)
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.operations import PRODUCERS, create_operation, knowledge_snapshot
from gaiavfx.application.prepare_references import (
    current_sheet,
)
from gaiavfx.infrastructure.database import new_id
from gaiavfx.integrations import minimax_h3
from gaiavfx.integrations.minimax_h3.graph import (
    build_graph,
    context_descriptor,
    render_presets,
)
from gaiavfx.integrations.minimax_h3.prompt import compile_prompt, gaps
from gaiavfx.modules.direction.segment import PictureRole
from gaiavfx.modules.direction.techniques import Block
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


def compiler_gaps(session, cut_id):
    """Report typed compiler capability gaps without compiling or exposing prompt text."""
    cut = direction.read_cut(session, cut_id)
    try:
        segment, pictures = direction.assemble_segment(session, cut["segment_id"])
    except ApplicationError as exc:
        if exc.status != 422:
            raise
        return [{"code": "structure", "message": str(exc)}]
    plan = plan_for_cut(session, cut_id)
    choice = plan.recommended
    place_id = direction.read_angle(session, cut["to_angle_id"])["place_id"]
    sheet = (
        current_sheet(session, place_id) if choice and Block.PLACE_VIEWS in choice.blocks else None
    )
    pictures, keyframes = _references(
        session,
        cut,
        segment,
        choice,
        pictures,
        sheet,
        strict=False,
        bindings=plan.bindings if choice and Block.PINNED_FRAME in choice.blocks else (),
    )
    return [
        {"code": gap.code, "message": gap.message} for gap in gaps(segment, pictures, keyframes)
    ]


def start_render(
    session,
    *,
    segment_id=None,
    cut_id=None,
    seeds,
    sheet_asset=None,
    context_artifact=None,
    budget_limit=None,
    integration="minimax_h3",
    request_key=None,
):
    if (segment_id is None) == (cut_id is None):
        raise ApplicationError("Exactly one of segment_id or cut_id is required.", 422)
    if cut_id is not None:
        segment_id = direction.read_cut(session, cut_id)["segment_id"]
    segment_row = direction.read_segment(session, segment_id)
    movie_id = segment_row["movie_id"]
    plan, segment, pictures, keyframes, artifact, context_source = segment_render_inputs(
        session, segment_id, integration, sheet_asset=sheet_asset, context_artifact=context_artifact
    )
    if plan.refusals:
        refusal = plan.refusals[0]
        raise ApplicationError(f"{refusal.code}: {refusal.message}", refusal.status)
    sheet_asset = plan.sheet_asset
    context_artifact = artifact["id"] if artifact else None
    if not seeds or len(seeds) != len(set(seeds)):
        raise ApplicationError("Render seeds must be distinct.", 422)
    grid = minimax_h3.capabilities().frame_grid
    handoff = plan.continuation if segment.continues else None
    try:
        compiled = compile_prompt(
            segment,
            pictures,
            tuple(keyframes),
            grid,
            handoff.context_frames if handoff else 0,
        )
    except ValueError as exc:
        raise ApplicationError(str(exc), 422) from exc
    # Run evidence: which handoff this take used, and whether it followed the rule.
    continuation = (
        {
            "method": handoff.method,
            "chosen": handoff.explicit_method is not None,
            "recommended": handoff.recommended,
            "context_frames": handoff.context_frames,
            "audio_context_frames": handoff.audio_context_frames,
            "audio_follows_picture": handoff.audio_follows_picture,
        }
        if handoff
        else None
    )
    jobs = []
    delivery = render_presets().delivery
    for seed in seeds:
        token = new_id()
        prefix = f"gaiavfx/{token}"
        files = {}
        inputs = {}
        for index, asset_id in enumerate(compiled.slots):
            remote = f"{prefix}/picture_{index}.png"
            files[asset_id] = remote
            asset = MediaLibrary.asset_info(session, asset_id)
            inputs[asset_id] = {"sha256": asset["sha256"], "remote_name": remote}
        for index, (asset_id, _) in enumerate(compiled.keyframes):
            remote = f"{prefix}/keyframe_{index}.png"
            files[asset_id] = remote
            asset = MediaLibrary.asset_info(session, asset_id)
            inputs[asset_id] = {"sha256": asset["sha256"], "remote_name": remote}
        if artifact:
            inputs[artifact["id"]] = {
                "source": "artifact",
                "sha256": artifact["sha256"],
                "remote_name": f"{prefix}/context.safetensors",
                "directory": "output",
            }
        jobs.append(
            JobSpec(
                "video",
                "comfy",
                "minimax_h3",
                seed=seed,
                inputs=inputs,
                graph=build_graph(
                    compiled,
                    files=files,
                    seed=seed,
                    **delivery,
                    fps=grid.fps,
                    context_latent=f"{prefix}/context.safetensors" if segment.continues else None,
                    output_prefix=prefix,
                    capture_context=True,
                    handoff=handoff,
                ),
                snapshot={
                    "knowledge": knowledge_snapshot(minimax_h3),
                    "output_prefix": prefix,
                    "capture_context": context_descriptor(prefix),
                    "technique": plan.technique_id,
                    "context_frames": compiled.context_frames,
                    "continuation": continuation,
                    "expect": {
                        "kind": "video",
                        "frames": compiled.window_frames - compiled.context_frames,
                        "fps": grid.fps,
                        **delivery,
                        "audio": True,
                    },
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
        "context_source": context_source,
        "continuation": continuation,
        "segment_revision": segment_row["revision"],
        "phase_revisions": {phase["id"]: phase["revision"] for phase in segment_row["phases"]},
        "subject_revisions": {
            subject.id: direction.read_subject(session, subject.id)["revision"]
            for subject in segment.subjects
        },
        "segment": segment_to_dict(
            segment, tuple(p for p in pictures if p.role is PictureRole.IDENTITY)
        ),
    }
    return create_operation(
        session,
        operations,
        PRODUCERS,
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
