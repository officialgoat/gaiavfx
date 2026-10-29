"""Prepare reviewed place views, insertion pins and deterministic sheets.

Angle views come from a producer: a survey (H3 frames of the place) or a set (renders of the set the place is built
as, ADR 0019 §3). Both are reviewed and approved the same way, through one review contract per producer.
"""

from dataclasses import asdict, dataclass

from gaiavfx.application import set_views
from gaiavfx.application.authored import validate_removal
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.operations import PRODUCERS, create_operation, knowledge_snapshot
from gaiavfx.infrastructure.database import new_id
from gaiavfx.integrations import minimax_h3, qwen_image_edit
from gaiavfx.integrations.minimax_h3.graph import render_presets as h3_presets
from gaiavfx.integrations.minimax_h3.survey import build_survey_graph, compile_survey
from gaiavfx.integrations.qwen_image_edit.graph import build_edit_graph
from gaiavfx.integrations.qwen_image_edit.graph import render_presets as qwen_presets
from gaiavfx.integrations.qwen_image_edit.prompt import compile_insertion, compile_removal
from gaiavfx.modules.direction.insertion import Insertion, Removal
from gaiavfx.modules.direction.survey import SurveyPlan
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

direction = DIRECTION
operations = OperationService()
reviews = ReviewService()


@dataclass(frozen=True)
class ViewProducer:
    """How one kind of preparation operation makes reviewable pictures of a place's angles: the step whose
    candidates are angle views (each candidate's output key is its angle) and the evidence kind approval records."""

    operation: str
    step: str
    evidence: str
    name: str  # how messages name it
    plate: bool  # its views start from the place's plate
    missing: str  # the step that makes them, in words
    uses: str  # what a shot that asks for them takes, in words
    sheet_origin: str  # the origin a sheet of its views records


VIEW_PRODUCERS = {
    "survey": ViewProducer(
        "survey",
        "extract_frames",
        "survey_view",
        "survey",
        plate=True,
        missing="Prepare survey views",
        uses="Pictures of the place",
        sheet_origin="survey",
    ),
    set_views.OPERATION: ViewProducer(
        set_views.OPERATION,
        set_views.STEP,
        "set_view",
        "stage render",
        plate=False,
        missing="Render views from the stage",
        uses="Pictures of the stage",
        sheet_origin="set",
    ),
}


def view_producer(place: dict) -> ViewProducer:
    """The one producer of a place's reviewed angle views (ADR 0019 §3, §7): a place built as a set renders them from
    the set; any other place is surveyed from its plate. Plans say which views a shot needs; this says who makes
    them, for the plans' words, the API's places, the UI's Prepare steps and the agent."""
    return VIEW_PRODUCERS[set_views.OPERATION if place.get("set_id") else "survey"]


def inactive_view_assets(session, place_id):
    """Views of the place whose candidates were declined or superseded: kept as history, not asked about."""
    return reviews.inactive_reference_assets(
        session, purpose="angle_views", context={"place_id": place_id}
    )


def inactive_reference_assets(session, *, place_id, cut_id):
    return (
        inactive_view_assets(session, place_id),
        reviews.inactive_reference_assets(session, purpose="insertion", context={"cut_id": cut_id}),
    )


def reject_reference_review(session, review_id, *, expected_revision, note):
    return reviews.reject_references(
        session, review_id, expected_revision=expected_revision, note=note, rejected_by="user"
    )


def survey_plan_for_place(session, place_id, *, first_angle_id):
    """Build reusable survey prose and its reviewed first frame from Direction records."""
    place = direction.read_place(session, place_id)
    angles = direction.angles(session, place_id)
    first = next((angle for angle in angles if angle["id"] == first_angle_id), None)
    if first is None:
        raise ApplicationError("The first angle must belong to this place.", 422)
    for field in ("description", "sound", "overhead"):
        if not place[field].strip():
            raise ApplicationError(f"Place {place['name']} has no {field}", 422)
    if not first["description"].strip():
        raise ApplicationError(f"Angle {first['name']} has no description", 422)
    if not place["plate_asset_id"]:
        raise ApplicationError(f"Place {place['name']} has no plate", 422)
    if place["plate_angle_id"] != first_angle_id:
        plate_angle = direction.read_angle(session, place["plate_angle_id"])
        raise ApplicationError(
            f"The plate shows angle {plate_angle['name']}, not {first['name']}", 422
        )
    views = tuple(
        (angle["id"], angle["description"])
        for angle in angles
        if angle["id"] != first_angle_id and angle["description"].strip()
    )
    try:
        return SurveyPlan(
            what=place["description"],
            sound=place["sound"],
            overhead=place["overhead"],
            first_view=first["description"],
            views=views,
            first_angle_id=first_angle_id,
            first_frame_asset=place["plate_asset_id"],
        )
    except ValueError as exc:
        raise ApplicationError(str(exc), 422) from exc


def sheet_tiles_for_place(session, place_id):
    """Every approved view in angle creation / approval order, padded by the integration."""
    evidence = [
        row
        for angle in direction.angles(session, place_id)
        for row in sorted(
            direction.evidence(session, angle["id"]),
            key=lambda item: (item["approved_at"] or "", item["id"]),
        )
        if row["approved"]
    ]
    if not evidence:
        return []
    first = _image(session, evidence[0]["asset_id"])
    columns = minimax_h3.knowledge_pack().views_sheet.columns
    size = (first["info"]["width"], first["info"]["height"])
    return [row["id"] for row in evidence] + [size] * (-len(evidence) % columns)


def latest_sheet(session, place_id):
    """Latest completed assembly only: never silently fall back to an older sheet."""
    for operation in operations.recent(session, kind="assemble", scope={"place_id": [place_id]}):
        if operation["state"] in {"succeeded", "partial"} and operation["candidates"]:
            return operation
    return None


def sheet_staleness(session, place_id, tiles):
    """Stale when the sheet's views differ from the place's approved views, or one went stale.

    The arrangement (order, blanks) is the assembly's own choice, e.g. the research sheets'
    order and grey blank; ADR 0010 lets it inherit approval for exactly that arrangement.
    """
    approved = {tile for tile in sheet_tiles_for_place(session, place_id) if isinstance(tile, str)}
    used = {tile for tile in tiles if isinstance(tile, str)}
    reasons = []
    if used != approved:
        reasons.append("an approved view was added or changed since the sheet was assembled")
    for tile in tiles:
        if isinstance(tile, str):
            binding = direction.reference_binding(session, "view", tile)
            if binding.stale:
                reasons.extend(f"a sheet view is stale: {reason}" for reason in binding.reasons)
    return tuple(dict.fromkeys(reasons))


def current_sheet(session, place_id):
    operation = latest_sheet(session, place_id)
    if operation and not sheet_staleness(session, place_id, operation["request"]["tiles"]):
        return operation["candidates"][0]["asset_id"]
    return None


def _image(session, asset_id):
    row = MediaLibrary.asset_info(session, asset_id)
    if row["kind"] != "image":
        raise ApplicationError("An image asset is required.", 422)
    return row


def _existing_review(session, operation_id):
    try:
        return reviews.blind(session, operation_id)
    except ApplicationError as exc:
        if exc.status != 404:
            raise
        return None


def _candidate_operation(session, candidate_id, kind, *, output_key=None):
    candidate = operations.candidate(session, candidate_id)
    operation = operations.operation(session, candidate["operation_id"])
    job = next(item for item in operation["jobs"] if item["id"] == candidate["job_id"])
    if operation["kind"] != kind or (
        output_key is not None and candidate["output_key"] != output_key
    ):
        raise ApplicationError("Candidate does not belong to this preparation step.", 422)
    return candidate, operation, job


def start_empty_plate(session, *, place_id, angle_id, source_asset, request_key=None):
    """Fit an asserted-empty image; retain the plate producer's review/lifecycle contract.

    The existing removal operation family owns plate reviews. Its request records
    empty=true and no removal wording/seeds; its only job is the deterministic fit.
    No assertion of emptiness is an approval of the fitted result.
    """
    if angle_id not in {row["id"] for row in direction.angles(session, place_id)}:
        raise ApplicationError("The plate angle must belong to this place.", 422)
    source = _image(session, source_asset)
    delivery = h3_presets().delivery
    job = JobSpec(
        "image",
        "local",
        "minimax_h3",
        step="fit_frame",
        inputs={source_asset: {"sha256": source["sha256"]}},
        snapshot={
            "step_spec": {"source": source_asset, **delivery},
            "expect": {"kind": "image", **delivery},
        },
    )
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind="removal",
        request_key=request_key,
        request={
            "place_id": place_id,
            "angle_id": angle_id,
            "source_asset": source_asset,
            "empty": True,
            "frame": None,
            "wording": {},
            "seeds": [],
        },
        jobs=[job],
        budget_limit=None,
    )


def start_removal(
    session,
    *,
    place_id,
    angle_id,
    source_asset,
    frame=None,
    wording,
    seeds,
    budget_limit=None,
    request_key=None,
):
    if angle_id not in {row["id"] for row in direction.angles(session, place_id)}:
        raise ApplicationError("The plate angle must belong to this place.", 422)
    wording = validate_removal(wording)
    if not seeds or any(type(seed) is not int for seed in seeds) or len(seeds) != len(set(seeds)):
        raise ApplicationError("Removal seeds must be non-empty, distinct integers.", 422)
    source = MediaLibrary.asset_info(session, source_asset)
    if source["kind"] == "image":
        if frame is not None:
            raise ApplicationError("An image removal source must not specify a frame.", 422)
    elif source["kind"] == "video":
        if type(frame) is not int or frame < 0:
            raise ApplicationError("A video removal source needs a nonnegative integer frame.", 422)
        count = source["info"].get("video_frames")
        if count is not None and frame >= count:
            raise ApplicationError(
                f"Removal frame must be below the source frame count ({count}).", 422
            )
    else:
        raise ApplicationError("Removal needs an image or video source.", 422)
    removal = Removal(view_asset=source_asset, **wording)
    prompt = compile_removal(removal)
    size = {key: qwen_presets().image[key] for key in ("width", "height")}
    jobs = []
    if source["kind"] == "video":
        jobs.append(
            JobSpec(
                "image",
                "local",
                "minimax_h3",
                inputs={source_asset: {"sha256": source["sha256"]}},
                step="extract_frames",
                snapshot={
                    "step_spec": {"source": source_asset, "frames": [["plate_source", frame]]}
                },
            )
        )
    for seed in seeds:
        prefix = f"gaiavfx/{new_id()}"
        inputs = (
            {"source_image": {"source": "job_output", "remote_name": f"{prefix}/view.png"}}
            if source["kind"] == "video"
            else {source_asset: {"sha256": source["sha256"], "remote_name": f"{prefix}/view.png"}}
        )
        edit_index = len(jobs)
        jobs.append(
            JobSpec(
                "image",
                "comfy",
                "qwen_image_edit",
                depends_on=0 if source["kind"] == "video" else None,
                seed=seed,
                inputs=inputs,
                graph=build_edit_graph(
                    prompt,
                    image_files=[f"{prefix}/view.png"],
                    seed=seed,
                    **size,
                    output_prefix=f"{prefix}/asset",
                ),
                snapshot={
                    "knowledge": knowledge_snapshot(qwen_image_edit),
                    "expect": {"kind": "image", **size},
                    "request": asdict(removal),
                },
            )
        )
        jobs.append(
            JobSpec(
                "image",
                "local",
                "minimax_h3",
                depends_on=edit_index,
                step="fit_frame",
                inputs={"source_image": {"source": "job_output"}},
                snapshot={
                    "step_spec": {"source": "source_image", **h3_presets().delivery},
                    "expect": {"kind": "image", **h3_presets().delivery},
                },
            )
        )
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind="removal",
        request_key=request_key,
        request={
            "place_id": place_id,
            "angle_id": angle_id,
            "source_asset": source_asset,
            "frame": frame,
            "wording": wording,
            "seeds": seeds,
        },
        jobs=jobs,
        budget_limit=budget_limit,
    )


def open_plate_review(session, operation_id):
    operation = operations.operation(session, operation_id)
    if operation["kind"] != "removal" or operation["state"] not in {"succeeded", "partial"}:
        raise ApplicationError(
            "Plate preparation must finish before its plate can be reviewed.", 409
        )
    existing = _existing_review(session, operation_id)
    if existing:
        return existing
    fitted = {job["id"] for job in operation["jobs"] if job["step"] == "fit_frame"}
    return reviews.create(
        session,
        purpose="plate",
        candidate_ids=[
            item["id"]
            for item in operation["candidates"]
            if item["job_id"] in fitted and item["output_key"] == "fitted"
        ],
        context={
            "place_id": operation["request"]["place_id"],
            "angle_id": operation["request"]["angle_id"],
            "operation_id": operation_id,
        },
        set_id=operation_id,
    )


def approve_plate(session, *, candidate_id):
    candidate, operation, job = _candidate_operation(
        session, candidate_id, "removal", output_key="fitted"
    )
    if job["step"] != "fit_frame":
        raise ApplicationError("Only fitted plate frames can be approved.", 422)
    reviews.reference_approved(session, operation["id"], candidate_id)
    place = direction.read_place(session, operation["request"]["place_id"])
    if place["plate_asset_id"] is not None and (
        place["plate_asset_id"] != candidate["asset_id"]
        or place["plate_angle_id"] != operation["request"]["angle_id"]
    ):
        direction.approve_plate_replacement(
            session,
            place["id"],
            place["revision"],
            asset_id=candidate["asset_id"],
            angle_id=operation["request"]["angle_id"],
            approved_by="user",
        )
        return direction.read_place(session, place["id"])
    return direction.set_plate(
        session,
        operation["request"]["place_id"],
        asset_id=candidate["asset_id"],
        angle_id=operation["request"]["angle_id"],
    )


def start_survey(session, *, place_id, plan: SurveyPlan, seed, budget_limit=None, request_key=None):
    angles = {row["id"] for row in direction.angles(session, place_id)}
    if not {plan.first_angle_id, *(angle for angle, _ in plan.views)} <= angles:
        raise ApplicationError("Survey views must name angles of this place.", 422)
    first = _image(session, plan.first_frame_asset)
    grid = minimax_h3.capabilities().frame_grid
    compiled = compile_survey(plan, grid)
    delivery = h3_presets().delivery
    token = new_id()
    prefix = f"gaiavfx/{token}"
    jobs = [
        JobSpec(
            "image",
            "local",
            "minimax_h3",
            inputs={first["id"]: {"sha256": first["sha256"]}},
            step="fit_frame",
            snapshot={
                "step_spec": {"source": first["id"], **delivery},
                "expect": {"kind": "image", **delivery},
            },
        ),
        JobSpec(
            "video",
            "comfy",
            "minimax_h3",
            depends_on=0,
            seed=seed,
            inputs={"first_frame": {"source": "job_output", "remote_name": f"{prefix}/first.png"}},
            graph=build_survey_graph(
                compiled,
                first_frame_file=f"{prefix}/first.png",
                seed=seed,
                **delivery,
                fps=grid.fps,
                output_prefix=prefix,
            ),
            snapshot={
                "knowledge": knowledge_snapshot(minimax_h3),
                "output_prefix": prefix,
                "plan": asdict(plan),
                "expect": {
                    "kind": "video",
                    "frames": compiled.window_frames,
                    "fps": grid.fps,
                    **delivery,
                    "audio": True,
                },
            },
        ),
        JobSpec(
            "image",
            "local",
            "minimax_h3",
            depends_on=1,
            step="extract_frames",
            inputs={"source_video": {"source": "job_output"}},
            snapshot={
                "step_spec": {"source": "source_video", "frames": compiled.frames},
                "expect": {"kind": "image", **delivery},
            },
        ),
    ]
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind="survey",
        request_key=request_key,
        request={"place_id": place_id, "seed": seed, "plan": asdict(plan)},
        jobs=jobs,
        budget_limit=budget_limit,
    )


def _producer(operation) -> ViewProducer:
    producer = VIEW_PRODUCERS.get(operation["kind"])
    if producer is None:
        raise ApplicationError("Only surveys and stage renders make angle views.", 422)
    return producer


def open_views_review(session, operation_id):
    """The contact sheet of an operation's angle views; opening it records each as evidence awaiting review."""
    operation = operations.operation(session, operation_id)
    producer = _producer(operation)
    if operation["state"] not in {"succeeded", "partial"}:
        raise ApplicationError(
            f"The {producer.name} must finish before its views can be reviewed.", 409
        )
    existing = _existing_review(session, operation_id)
    if existing:
        return existing
    place_id = operation["request"]["place_id"]
    angles = {row["id"] for row in direction.angles(session, place_id)}
    steps = {job["id"] for job in operation["jobs"] if job["step"] == producer.step}
    members = [
        item
        for item in operation["candidates"]
        if item["job_id"] in steps and item["output_key"] in angles
    ]
    for candidate in members:
        known = direction.evidence(session, candidate["output_key"])
        if not any(row["asset_id"] == candidate["asset_id"] for row in known):
            direction.record_evidence(
                session, candidate["output_key"], producer.evidence, candidate["asset_id"]
            )
    return reviews.create(
        session,
        purpose="angle_views",
        candidate_ids=[item["id"] for item in members],
        context={"place_id": place_id, "operation_id": operation_id},
        set_id=operation_id,
    )


def approve_view(session, *, candidate_id):
    candidate = operations.candidate(session, candidate_id)
    operation = operations.operation(session, candidate["operation_id"])
    producer = _producer(operation)
    job = next(item for item in operation["jobs"] if item["id"] == candidate["job_id"])
    angle_id = candidate["output_key"]
    if job["step"] != producer.step or angle_id not in {
        row["id"] for row in direction.angles(session, operation["request"]["place_id"])
    }:
        raise ApplicationError("Only angle views can be approved.", 422)
    match = next(
        (
            row
            for row in direction.evidence(session, angle_id)
            if row["asset_id"] == candidate["asset_id"]
        ),
        None,
    )
    if match is None:
        raise ApplicationError(f"Open the {producer.name} review before approving a view.", 422)
    if producer.evidence == "set_view":
        # The picture must still show what the set holds for the angle: approval records the inputs of now.
        made = set_views.rendered(session, candidate["asset_id"])
        now = set_views.set_view_inputs(session, match)
        if (
            made["set_id"],
            made["view_id"],
            made["viewmat"],
            made["fov"],
            made["hide_region_ids"],
        ) != (
            now["set_id"],
            now["view_id"],
            now["viewmat"],
            now["fov"],
            now["hide_region_ids"],
        ):
            raise ApplicationError(
                "The stage's camera or what it hides changed since this render; render the view again.",
                409,
            )
    reviews.reference_approved(session, operation["id"], candidate_id)
    return direction.approve_evidence(session, match["id"])


def start_insertion(
    session,
    *,
    cut_id,
    evidence_id,
    subject_asset,
    wording,
    seeds,
    budget_limit=None,
    request_key=None,
):
    cut = direction.read_cut(session, cut_id)
    evidence = direction.read_evidence(session, evidence_id)
    if not evidence["approved"] or evidence["angle_id"] != cut["to_angle_id"]:
        raise ApplicationError("Insertion needs approved evidence of the target angle.", 422)
    view = _image(session, evidence["asset_id"])
    subject = _image(session, subject_asset)
    if not seeds or len(seeds) != len(set(seeds)):
        raise ApplicationError("Insertion seeds must be distinct.", 422)
    wording = direction.validate_cut_insertion(session, cut_id, wording)
    try:
        request = Insertion(view_asset=view["id"], subject_asset=subject["id"], **wording)
    except (TypeError, ValueError) as exc:
        raise ApplicationError(str(exc), 422) from exc
    prompt = compile_insertion(request)
    size = {key: qwen_presets().image[key] for key in ("width", "height")}
    jobs = []
    for seed in seeds:
        token = new_id()
        prefix = f"gaiavfx/{token}"
        edit_index = len(jobs)
        jobs.append(
            JobSpec(
                "image",
                "comfy",
                "qwen_image_edit",
                seed=seed,
                inputs={
                    view["id"]: {"sha256": view["sha256"], "remote_name": f"{prefix}/view.png"},
                    subject["id"]: {
                        "sha256": subject["sha256"],
                        "remote_name": f"{prefix}/subject.png",
                    },
                },
                graph=build_edit_graph(
                    prompt,
                    image_files=[f"{prefix}/view.png", f"{prefix}/subject.png"],
                    seed=seed,
                    **size,
                    output_prefix=f"{prefix}/asset",
                ),
                snapshot={
                    "knowledge": knowledge_snapshot(qwen_image_edit),
                    "expect": {"kind": "image", **size},
                    "request": asdict(request),
                },
            )
        )
        jobs.append(
            JobSpec(
                "image",
                "local",
                "minimax_h3",
                depends_on=edit_index,
                step="fit_frame",
                inputs={"source_image": {"source": "job_output"}},
                snapshot={
                    "step_spec": {"source": "source_image", **h3_presets().delivery},
                    "expect": {"kind": "image", **h3_presets().delivery},
                },
            )
        )
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind="insert",
        request_key=request_key,
        request={
            "cut_id": cut_id,
            "evidence_id": evidence_id,
            "angle_id": evidence["angle_id"],
            "subject_asset": subject_asset,
            "seeds": seeds,
            "wording": wording,
        },
        jobs=jobs,
        budget_limit=budget_limit,
    )


def open_insertion_review(session, operation_id):
    operation = operations.operation(session, operation_id)
    if operation["kind"] != "insert" or operation["state"] not in {"succeeded", "partial"}:
        raise ApplicationError("Insertion must finish before review.", 409)
    existing = _existing_review(session, operation_id)
    if existing:
        return existing
    fitted = {job["id"] for job in operation["jobs"] if job["step"] == "fit_frame"}
    ids = [
        item["id"]
        for item in operation["candidates"]
        if item["job_id"] in fitted and item["output_key"] == "fitted"
    ]
    return reviews.create(
        session,
        purpose="insertion",
        candidate_ids=ids,
        context={
            "cut_id": operation["request"]["cut_id"],
            "evidence_id": operation["request"]["evidence_id"],
            "operation_id": operation_id,
        },
        set_id=operation_id,
    )


def approve_pin(session, *, candidate_id, replace=False):
    candidate, operation, job = _candidate_operation(
        session, candidate_id, "insert", output_key="fitted"
    )
    if job["step"] != "fit_frame":
        raise ApplicationError("Only fitted insertion frames can be approved.", 422)
    reviews.reference_approved(session, operation["id"], candidate_id)
    cut_id = operation["request"]["cut_id"]
    approved = [item for item in direction.keyframes(session, cut_id) if item["approved"]]
    if approved:
        if approved[0]["asset_id"] == candidate["asset_id"]:
            return direction.approve_keyframe(session, approved[0]["id"])
        if not replace:
            raise ApplicationError(
                "This cut already has a different approved keyframe. Use replace: true to replace it.",
                409,
            )
    row = direction.record_keyframe(
        session,
        cut_id,
        candidate["asset_id"],
        operation["request"]["evidence_id"],
        subject_asset_id=operation["request"]["subject_asset"],
    )
    if approved:
        return direction.approve_keyframe_replacement(session, row["id"], approved_by="user")
    return direction.approve_keyframe(session, row["id"])


def assemble_sheet(session, *, place_id, tiles, origin, request_key=None):
    """A deterministic views-only sheet of approved views. Its origin says where the photographs came from; a
    sheet of set views is always `set` (ADR 0019 §3)."""
    if origin not in {"survey", "walk_around", "set"} or not tiles:
        raise ApplicationError("Sheet needs an origin and tiles.", 422)
    kinds = {
        direction.read_evidence(session, tile)["kind"] for tile in tiles if isinstance(tile, str)
    }
    if kinds == {"set_view"}:
        origin = "set"
    elif origin == "set":
        raise ApplicationError("Only a sheet of stage views comes from the stage.", 422)
    angles = {item["id"] for item in direction.angles(session, place_id)}
    asset_tiles = []
    inputs = {}
    request_tiles = []
    blanks = []
    for tile in tiles:
        if isinstance(tile, str):
            evidence = direction.read_evidence(session, tile)
            if not evidence["approved"] or evidence["angle_id"] not in angles:
                raise ApplicationError("Sheet tile must be approved evidence of this place.", 422)
            asset = _image(session, evidence["asset_id"])
            inputs[asset["id"]] = {"sha256": asset["sha256"]}
            asset_tiles.append(asset["id"])
            request_tiles.append(tile)
        elif (
            isinstance(tile, tuple)
            and len(tile) == 2
            and all(type(n) is int and n > 0 for n in tile)
        ):
            blanks.append(tile)
            asset_tiles.append(None)
            request_tiles.append({"blank": list(tile)})
        else:
            raise ApplicationError("Invalid sheet tile.", 422)
    if len(set(blanks)) > 1:
        raise ApplicationError("Blank tiles must share one size.", 422)
    preset = minimax_h3.knowledge_pack().views_sheet
    step_spec = {
        "tiles": asset_tiles,
        "columns": preset.columns,
        "gap": preset.gap,
        "gap_rgb": preset.gap_rgb,
    }
    if blanks:
        step_spec["blank_tile_size"] = blanks[0]
    job = JobSpec(
        "image",
        "local",
        "minimax_h3",
        inputs=inputs,
        step="views_sheet",
        snapshot={"step_spec": step_spec},
    )
    return create_operation(
        session,
        operations,
        PRODUCERS,
        kind="assemble",
        request_key=request_key,
        request={
            "place_id": place_id,
            "tiles": request_tiles,
            "origin": origin,
            "panels": len(tiles) - len(blanks),
            "blank_panels": len(blanks),
        },
        jobs=[job],
        budget_limit=None,
    )
