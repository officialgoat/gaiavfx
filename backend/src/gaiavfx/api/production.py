"""HTTP boundary for preparation, execution, review and take selection."""

from __future__ import annotations

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Response
from pydantic import BaseModel, ConfigDict, Field, model_serializer, model_validator

from gaiavfx.api.direction import PlaceDTO, ReceiptDTO
from gaiavfx.application import set_seed, set_views
from gaiavfx.application.authored import load_survey, validate_insertion
from gaiavfx.application.candidate_review import (
    prescreen_candidate,
    review_taxonomy,
    save_ranking,
    select_take,
)
from gaiavfx.application.prepare_references import (
    approve_pin,
    approve_plate,
    approve_view,
    assemble_sheet,
    open_insertion_review,
    open_plate_review,
    open_views_review,
    reject_reference_review,
    sheet_tiles_for_place,
    start_empty_plate,
    start_insertion,
    start_removal,
    start_survey,
)
from gaiavfx.application.render_cut import open_takes_review, start_render
from gaiavfx.application.replace_character import (
    approve_replacement,
    open_replacement_review,
    people_for_asset,
    recorded_replacement_plan,
    replacement_plan,
    start_people_analysis,
    start_replacement,
)
from gaiavfx.application.replacement_review import blind, reveal
from gaiavfx.application.selected_takes import selection_state
from gaiavfx.modules.generation.operations.service import OperationService


class InputDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ReplacementAnalysisPointDTO(InputDTO):
    x: float
    y: float


class ReplacementAnalysisPointsDTO(InputDTO):
    positive: list[ReplacementAnalysisPointDTO]
    negative: list[ReplacementAnalysisPointDTO] = Field(default_factory=list)


class ReplacementPeopleAnalysisInput(InputDTO):
    source_asset_id: str
    start: int = Field(ge=0, strict=True)
    end: int = Field(gt=0, strict=True)
    concepts: tuple[str, ...] = Field(default=("person",), min_length=1, max_length=8)
    points: dict[str, ReplacementAnalysisPointsDTO] | None = None
    request_key: str | None = Field(default=None, min_length=1, max_length=80)


class ReplacementBoxDTO(InputDTO):
    x0: float
    y0: float
    x1: float
    y1: float


class ReplacementPerformerInput(InputDTO):
    track_id: str
    target_asset: str
    role: str = Field(default_factory=lambda: "performer")
    position: str | None = None
    target_subject_id: str | None = None
    action: str | None = None
    selected_by: Literal["user", "identity"] = "user"


class ReplacementOccluderInput(InputDTO):
    name: str
    owner: Literal["scene", "performer"]
    covers: str


class ReplacementPlanInput(InputDTO):
    renders: int | None = Field(default=None, ge=1, le=4, strict=True)
    source_asset: str
    analysis_operation: str
    performers: list[ReplacementPerformerInput] = Field(min_length=1, max_length=2)
    seed: int = Field(default=0, ge=0, le=2**63 - 4, strict=True)
    required_context: list[ReplacementBoxDTO] = Field(default_factory=list, max_length=16)
    avoided_regions: list[ReplacementBoxDTO] = Field(default_factory=list, max_length=16)
    quiet: bool = False
    scene_items: list[str] = Field(default_factory=list)
    occluders: list[ReplacementOccluderInput] = Field(default_factory=list)
    silent_intervals: list[
        tuple[Annotated[int, Field(strict=True, ge=0)], Annotated[int, Field(strict=True, ge=0)]]
    ] = Field(default_factory=list, max_length=8)
    performer_hands_visible: bool | None = None
    source_target_similarity: float | None = None


class ReplacementStartInput(ReplacementPlanInput):
    request_key: str | None = Field(default=None, min_length=1, max_length=80)
    budget_limit: int | None = Field(default=None, ge=0, strict=True)


class ReplacementCropDTO(BaseModel):
    canvas: tuple[int, int]
    box: ReplacementBoxDTO
    scale: float
    interior_edges: list[Literal["left", "right", "top", "bottom"]]
    feather: float
    clearance: float | None
    matrix: tuple[tuple[float, float, float], tuple[float, float, float]]


class ReplacementArmDTO(BaseModel):
    treatment: Literal["rgb", "inverted"]
    grouping: Literal["single", "joint"]
    framing: Literal["crop", "pair", "active", "full", "head"]
    crop: ReplacementCropDTO | None
    seed_count: int
    round: int
    seed_offset: int


class ReplacementGapDTO(BaseModel):
    code: str
    message: str


class ReplacementLetterboxDTO(BaseModel):
    fit: tuple[int, int]
    offset: tuple[int, int]
    canvas: tuple[int, int]


class ReplacementTrackDTO(BaseModel):
    id: str
    box: ReplacementBoxDTO
    presence: list[Literal["present", "absent", "unresolved"]]
    identity_margin: float | None
    selected_by: Literal["user", "identity"]


class ReplacementPerformerDTO(BaseModel):
    track_id: str
    target_asset: str
    target_subject_id: str | None
    target_text: str | None
    role: str
    position: str
    action: str | None


class ReplacementBystanderDTO(BaseModel):
    track_id: str
    role: str
    position: str
    items: list[str]


class ReplacementFactsDTO(BaseModel):
    source_asset: str
    shot_start: int
    shot_end: int
    native: tuple[int, int]
    fps: str = Field(pattern=r"^[0-9]+(/[0-9]+)?$")
    letterbox: ReplacementLetterboxDTO | None
    performers: list[ReplacementPerformerDTO]
    bystanders: list[ReplacementBystanderDTO]
    occluders: list[ReplacementOccluderInput]
    scene_items: list[str]
    quiet: bool
    silent_intervals: list[
        tuple[Annotated[int, Field(strict=True, ge=0)], Annotated[int, Field(strict=True, ge=0)]]
    ]
    tracks: list[ReplacementTrackDTO]
    performer_hands_visible: bool | None
    source_target_similarity: float | None
    required_context: list[ReplacementBoxDTO]
    avoided_regions: list[ReplacementBoxDTO]


class ReplacementPlanDTO(BaseModel):
    arm: ReplacementArmDTO | None
    gaps: list[ReplacementGapDTO]
    reasons: list[str]
    stop: bool
    technique_ids: list[str]
    facts: ReplacementFactsDTO
    crop_preview: ReplacementCropDTO | None


class ReplacementPeopleAnalysisDTO(BaseModel):
    operation_id: str
    state: str
    report: dict | None


class ReplacementApprovalDTO(BaseModel):
    asset_id: str
    provenance: dict


class SurveyInput(InputDTO):
    request_key: str | None = Field(default=None, min_length=1, max_length=80)
    place_id: str
    plan: dict
    seed: int


class SetViewsInput(InputDTO):
    """Render a place's angles from the set it is built as (ADR 0019 §3)."""

    request_key: str | None = Field(default=None, min_length=1, max_length=80)
    place_id: str
    angle_ids: list[str] = Field(min_length=1, max_length=12)


class SetSeedInput(InputDTO):
    """Build a set from one shot of a segment's current take (ADR 0019 §4), filling in what the take never saw unless
    asked not to (ADR 0020)."""

    segment_id: str = Field(min_length=1, max_length=36)
    shot: int = Field(ge=0, le=500)
    name: str = Field(min_length=1, max_length=120)
    use_for_place: bool = False
    fill_unseen: bool = True


class InsertionInput(InputDTO):
    request_key: str | None = Field(default=None, min_length=1, max_length=80)
    cut_id: str
    evidence_id: str
    subject_asset: str
    wording: dict
    seeds: list[int]


class CandidateInput(InputDTO):
    candidate_id: str


class PinApprovalInput(CandidateInput):
    replace: bool = Field(default=False, strict=True)


class RemovalInput(InputDTO):
    request_key: str | None = Field(default=None, min_length=1, max_length=80)
    place_id: str
    angle_id: str
    source_asset: str
    frame: int | None = Field(default=None, ge=0, strict=True)
    wording: dict
    seeds: list[int]


class EmptyPlateInput(InputDTO):
    request_key: str | None = Field(default=None, min_length=1, max_length=80)
    place_id: str
    angle_id: str
    source_asset: str
    empty: Literal[True]


class SheetInput(InputDTO):
    request_key: str | None = Field(default=None, min_length=1, max_length=80)
    place_id: str
    tiles: list[str | tuple[int, int]]
    origin: str


class ProductionSheetTilesDTO(BaseModel):
    """Default inputs for a new assembly; existing layouts have their own identity."""

    place_id: str
    tiles: list[str | tuple[int, int]]


class RenderInput(InputDTO):
    request_key: str | None = Field(default=None, min_length=1, max_length=80)
    segment_id: str | None = Field(default=None, min_length=1)
    cut_id: str | None = Field(default=None, min_length=1)
    seeds: list[int]
    sheet_asset: str | None = None
    context_artifact: str | None = None

    @model_validator(mode="after")
    def one_target(self):
        if (self.segment_id is None) == (self.cut_id is None):
            raise ValueError("Exactly one of segment_id or cut_id is required.")
        return self


class RankingInput(InputDTO):
    expected_revision: int
    tiers: list[list[str]]
    usable_tiers: int
    notes: dict[str, str] = Field(default_factory=dict)
    tags: dict[str, list[str]] = Field(default_factory=dict)
    general: str = ""


class SelectInput(CandidateInput):
    expected_revision: int


class ProductionReferenceRejectionInput(InputDTO):
    expected_revision: int = Field(strict=True, ge=1)
    note: str = Field(min_length=1, max_length=2000)


class ProductionReferenceRejectionDTO(BaseModel):
    rejected_by: Literal["user"]
    rejected_at: str
    note: str
    candidate_ids: list[str]


class JobDTO(BaseModel):
    ordinal: int
    kind: str
    executor: str
    integration: str
    step: str | None
    state: str
    seed: int | None
    error: str | None


class ReviewTagDTO(BaseModel):
    id: str
    label: str
    applies_to: list[str]


class ReviewTaxonomyDTO(BaseModel):
    schema_version: int = Field(alias="schema")
    tags: list[ReviewTagDTO]


class PrescreenCheckDTO(BaseModel):
    check: str
    verdict: Literal["pass", "fail", "unsupported"]
    evidence: dict


class PrescreenDTO(BaseModel):
    version: int
    measured_at: str
    checks: list[PrescreenCheckDTO]


class CandidateDTO(BaseModel):
    prescreen: PrescreenDTO | None
    id: str
    job_id: str
    operation_id: str
    asset_id: str
    artifact_id: str | None
    output_key: str
    kind: str
    created_at: str


class OperationSummaryDTO(BaseModel):
    """Enough to route a review link without loading candidate provenance into the UI."""

    id: str
    kind: str


class OperationDTO(BaseModel):
    id: str
    kind: str
    state: str
    awaits_review: bool
    request: dict
    budget_reserved: int
    budget_released_at: str | None
    cancel_requested: bool
    review: list | None
    error: str | None
    created_at: str
    updated_at: str
    jobs: list[JobDTO]
    candidates: list[CandidateDTO]


class ReplacementCheckDTO(BaseModel):
    calibrated: bool
    check: str
    verdict: Literal["pass", "fail", "unsupported"]
    measured: float | None
    advice: str
    reason: str

    @model_serializer(mode="wrap")
    def serialize_check(self, handler):
        result = handler(self)
        result["measured"] = self.measured
        return result


class ReplacementReviewArmDTO(BaseModel):
    treatment: str
    framing: str


class ReviewMemberDTO(BaseModel):
    replacement_checks: list[ReplacementCheckDTO] | None = None
    round: int | None = None
    arm: ReplacementReviewArmDTO | None = None
    prescreen: PrescreenDTO | None = None
    letter: str
    candidate_id: str
    asset_id: str
    kind: str
    job_id: str | None = None
    operation_id: str | None = None
    integration: str | None = None
    seed: int | None = None
    graph: dict | None = None
    request: dict | None = None

    @model_serializer(mode="wrap")
    def serialize_member(self, handler):
        # Blind routes omit unrevealed generation details, but always expose measurement state.
        result = handler(self)
        if self.prescreen is None:
            result["prescreen"] = None
        return result


class RankingDTO(BaseModel):
    review_set_id: str
    tiers: list[list[str]]
    usable_tiers: int
    notes: dict[str, str]
    tags: dict[str, list[str]]
    general: str
    revision: int
    updated_at: str


class ReviewDTO(BaseModel):
    id: str
    purpose: str
    context: dict
    revision: int
    members: list[ReviewMemberDTO]
    unranked: list[str]
    ranking: RankingDTO
    rejection: ProductionReferenceRejectionDTO | None = None


class SelectionDTO(BaseModel):
    stale: bool = False
    stale_reasons: list[str] = Field(default_factory=list)
    id: str
    movie_id: str
    segment_id: str
    candidate_id: str
    asset_id: str
    authority: str
    basis: str | None
    selected_at: str
    superseded_at: str | None
    revision: int


class SelectionsDTO(BaseModel):
    current: SelectionDTO | None
    history: list[SelectionDTO]
    revision: int


class ProductionEvidenceDTO(BaseModel):
    id: str
    angle_id: str
    kind: str
    asset_id: str
    approved: bool
    approved_at: str | None
    approved_by: str | None
    inputs: dict | None
    created_at: str


class ProductionKeyframeDTO(BaseModel):
    receipt: ReceiptDTO | None = None
    subject_asset_id: str | None
    id: str
    cut_id: str
    asset_id: str
    evidence_id: str
    approved: bool
    approved_at: str | None
    approved_by: str | None
    inputs: dict | None
    created_at: str


def production_router(workspace, authenticate):
    router = APIRouter(prefix="/api/production", dependencies=[Depends(authenticate)])
    operations = OperationService()

    replay_response = {200: {"model": OperationDTO, "description": "Existing operation"}}

    def creation_response(operation, response):
        response.status_code = 201 if operation.pop("_created") else 200
        return operation

    @router.post(
        "/people-analyses", response_model=OperationDTO, status_code=201, responses=replay_response
    )
    def people_analysis(body: ReplacementPeopleAnalysisInput, response: Response):
        with workspace.db.transaction() as session:
            operation = start_people_analysis(session, **body.model_dump())
        return creation_response(operation, response)

    @router.get("/people-analyses", response_model=list[ReplacementPeopleAnalysisDTO])
    def people_analysis_list(asset_id: str):
        with workspace.db.transaction() as session:
            return people_for_asset(session, asset_id, library=workspace.library)

    @router.post("/replacement-plans", response_model=ReplacementPlanDTO)
    def replacement_preview(body: ReplacementPlanInput):
        with workspace.db.transaction() as session:
            return replacement_plan(
                session,
                body.model_dump(exclude={"renders"}),
                library=workspace.library,
                renders=body.renders,
            )

    @router.post(
        "/replacements", response_model=OperationDTO, status_code=201, responses=replay_response
    )
    def replacement_start(body: ReplacementStartInput, response: Response):
        with workspace.db.transaction() as session:
            operation = start_replacement(
                session,
                body.model_dump(exclude={"request_key", "budget_limit", "renders"}),
                body.budget_limit,
                body.request_key,
                library=workspace.library,
                renders=body.renders,
            )
        return creation_response(operation, response)

    @router.post(
        "/replacements/{operation_id}/review",
        response_model=ReviewDTO,
        response_model_exclude_none=True,
    )
    def replacement_review(operation_id: str):
        with workspace.db.transaction() as session:
            review = open_replacement_review(session, operation_id)
            return blind(session, review["id"], library=workspace.library)

    @router.get("/replacements/{operation_id}/plan", response_model=ReplacementPlanDTO)
    def recorded_plan(operation_id: str):
        with workspace.db.transaction() as session:
            return recorded_replacement_plan(session, operation_id)

    @router.post("/replacements/approve", response_model=ReplacementApprovalDTO)
    def replacement_approval(body: CandidateInput):
        with workspace.db.transaction() as session:
            return approve_replacement(session, body.candidate_id)

    @router.post("/plates", response_model=OperationDTO, status_code=201, responses=replay_response)
    def empty_plate(body: EmptyPlateInput, response: Response):
        with workspace.db.transaction() as session:
            operation = start_empty_plate(session, **body.model_dump(exclude={"empty"}))
        return creation_response(operation, response)

    @router.post(
        "/removals", response_model=OperationDTO, status_code=201, responses=replay_response
    )
    def removal(body: RemovalInput, response: Response):
        with workspace.db.transaction() as session:
            operation = start_removal(session, **body.model_dump())
        return creation_response(operation, response)

    @router.post(
        "/removals/{operation_id}/review",
        response_model=ReviewDTO,
        response_model_exclude_none=True,
    )
    def plate_review(operation_id: str):
        with workspace.db.transaction() as session:
            return open_plate_review(session, operation_id)

    @router.post("/plates/approve", response_model=PlaceDTO)
    def plate_approval(body: CandidateInput):
        with workspace.db.transaction() as session:
            return approve_plate(session, candidate_id=body.candidate_id)

    @router.post(
        "/surveys", response_model=OperationDTO, status_code=201, responses=replay_response
    )
    def survey(body: SurveyInput, response: Response):
        plan = load_survey(body.plan)
        with workspace.db.transaction() as session:
            operation = start_survey(
                session,
                place_id=body.place_id,
                plan=plan,
                seed=body.seed,
                request_key=body.request_key,
            )
        return creation_response(operation, response)

    @router.post(
        "/surveys/{operation_id}/review", response_model=ReviewDTO, response_model_exclude_none=True
    )
    def survey_review(operation_id: str):
        with workspace.db.transaction() as session:
            return open_views_review(session, operation_id)

    @router.post(
        "/set-views", response_model=OperationDTO, status_code=201, responses=replay_response
    )
    def set_view_renders(body: SetViewsInput, response: Response):
        with workspace.db.transaction() as session:
            operation = set_views.start_set_views(
                session,
                workspace.library,
                place_id=body.place_id,
                angle_ids=body.angle_ids,
                request_key=body.request_key,
            )
        return creation_response(operation, response)

    @router.post(
        "/set-seeds", response_model=OperationDTO, status_code=201, responses=replay_response
    )
    def set_seed_start(body: SetSeedInput, response: Response):
        with workspace.db.transaction() as session:
            operation = set_seed.start_seed(
                session,
                workspace.library,
                segment_id=body.segment_id,
                shot=body.shot,
                name=body.name,
                use_for_place=body.use_for_place,
                fill_unseen=body.fill_unseen,
            )
        return creation_response(operation, response)

    @router.post(
        "/set-seeds/{operation_id}/retry",
        response_model=OperationDTO,
        status_code=201,
        responses=replay_response,
    )
    def set_seed_retry(operation_id: str, response: Response):
        with workspace.db.transaction() as session:
            operation = set_seed.retry_seed(session, workspace.library, operation_id)
        return creation_response(operation, response)

    @router.post(
        "/set-views/{operation_id}/review",
        response_model=ReviewDTO,
        response_model_exclude_none=True,
    )
    def set_views_review(operation_id: str):
        with workspace.db.transaction() as session:
            return open_views_review(session, operation_id)

    @router.post("/views/approve", response_model=ProductionEvidenceDTO)
    def view_approval(body: CandidateInput):
        with workspace.db.transaction() as session:
            return approve_view(session, candidate_id=body.candidate_id)

    @router.post(
        "/insertions", response_model=OperationDTO, status_code=201, responses=replay_response
    )
    def insertion(body: InsertionInput, response: Response):
        wording = validate_insertion(body.wording)
        with workspace.db.transaction() as session:
            operation = start_insertion(
                session,
                cut_id=body.cut_id,
                evidence_id=body.evidence_id,
                subject_asset=body.subject_asset,
                wording=wording,
                seeds=body.seeds,
                request_key=body.request_key,
            )
        return creation_response(operation, response)

    @router.post(
        "/insertions/{operation_id}/review",
        response_model=ReviewDTO,
        response_model_exclude_none=True,
    )
    def insertion_review(operation_id: str):
        with workspace.db.transaction() as session:
            return open_insertion_review(session, operation_id)

    @router.post("/pins/approve", response_model=ProductionKeyframeDTO)
    def pin_approval(body: PinApprovalInput):
        with workspace.db.transaction() as session:
            return approve_pin(session, **body.model_dump())

    @router.get("/places/{place_id}/sheet-tiles", response_model=ProductionSheetTilesDTO)
    def sheet_tiles(place_id: str):
        with workspace.db.transaction() as session:
            return {"place_id": place_id, "tiles": sheet_tiles_for_place(session, place_id)}

    @router.post("/sheets", response_model=OperationDTO, status_code=201, responses=replay_response)
    def sheet(body: SheetInput, response: Response):
        with workspace.db.transaction() as session:
            operation = assemble_sheet(
                session,
                place_id=body.place_id,
                tiles=body.tiles,
                origin=body.origin,
                request_key=body.request_key,
            )
        return creation_response(operation, response)

    @router.post(
        "/renders", response_model=OperationDTO, status_code=201, responses=replay_response
    )
    def render(body: RenderInput, response: Response):
        with workspace.db.transaction() as session:
            operation = start_render(
                session,
                segment_id=body.segment_id,
                cut_id=body.cut_id,
                seeds=body.seeds,
                sheet_asset=body.sheet_asset,
                context_artifact=body.context_artifact,
                request_key=body.request_key,
            )
        return creation_response(operation, response)

    @router.post(
        "/renders/{operation_id}/review", response_model=ReviewDTO, response_model_exclude_none=True
    )
    def render_review(operation_id: str):
        with workspace.db.transaction() as session:
            return open_takes_review(session, operation_id)

    @router.get("/operations", response_model=list[OperationDTO])
    def recent_operations(
        kind: str | None = None,
        state: str | None = None,
        limit: int = Query(50, ge=1, le=100),
    ):
        with workspace.db.transaction() as session:
            return operations.recent(session, kind=kind, state=state, limit=limit)

    @router.get("/operations/{operation_id}", response_model=OperationDTO)
    def operation(operation_id: str):
        with workspace.db.transaction() as session:
            return operations.operation(session, operation_id)

    @router.get("/operations/{operation_id}/summary", response_model=OperationSummaryDTO)
    def operation_summary(operation_id: str):
        with workspace.db.transaction() as session:
            operation = operations.operation(session, operation_id)
            return {key: operation[key] for key in ("id", "kind")}

    @router.post("/operations/{operation_id}/cancel", response_model=OperationDTO)
    def cancel_operation(operation_id: str):
        with workspace.db.transaction() as session:
            return operations.request_cancel(session, operation_id)

    @router.get("/review-tags", response_model=ReviewTaxonomyDTO)
    def taxonomy():
        return review_taxonomy()

    @router.post("/candidates/{candidate_id}/prescreen", response_model=PrescreenDTO)
    def measure(candidate_id: str):
        return prescreen_candidate(workspace.db.transaction, workspace.library, candidate_id)

    @router.get("/reviews/{review_id}", response_model=ReviewDTO, response_model_exclude_none=True)
    def blind_review(review_id: str):
        with workspace.db.transaction() as session:
            return blind(session, review_id, library=workspace.library)

    @router.get("/reviews/{review_id}/reveal", response_model=ReviewDTO)
    def reveal_review(review_id: str):
        with workspace.db.transaction() as session:
            return reveal(session, review_id, library=workspace.library)

    @router.post(
        "/reviews/{review_id}/reject", response_model=ReviewDTO, response_model_exclude_none=True
    )
    def reject_references(review_id: str, body: ProductionReferenceRejectionInput):
        with workspace.db.transaction() as session:
            return reject_reference_review(session, review_id, **body.model_dump())

    @router.put("/reviews/{review_id}/ranking", response_model=RankingDTO)
    def ranking(review_id: str, body: RankingInput):
        with workspace.db.transaction() as session:
            return save_ranking(session, review_id, **body.model_dump())

    @router.post("/reviews/{review_id}/select", response_model=SelectionDTO)
    def select(review_id: str, body: SelectInput):
        with workspace.db.transaction() as session:
            return select_take(
                session,
                review_set_id=review_id,
                candidate_id=body.candidate_id,
                authority="user",
                expected_revision=body.expected_revision,
            )

    @router.get("/selections", response_model=SelectionsDTO)
    def read_selection_state(movie_id: str, segment_id: str):
        with workspace.db.transaction() as session:
            return selection_state(session, movie_id, segment_id)

    return router
