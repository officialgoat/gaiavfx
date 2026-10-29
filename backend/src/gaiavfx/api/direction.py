"""Direction records, commands and cut planning."""

from typing import Literal

from fastapi import APIRouter, Depends, Query
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict, Field, computed_field

from gaiavfx.application import set_scenes, set_seed, sets
from gaiavfx.application.authored import segment_to_dict
from gaiavfx.application.continuation import set_continuation
from gaiavfx.application.direction_plans import (
    DEFAULT_INTEGRATION,
    plan_for_cut,
    plan_for_segment,
    plan_to_dict,
)
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.prepare_references import view_producer
from gaiavfx.modules.direction.coverage import COVERAGE_WORDS, Coverage
from gaiavfx.modules.direction.place_text import validate_place_text
from gaiavfx.modules.direction.planning import Action
from gaiavfx.modules.direction.service import SegmentSplitRefusal
from gaiavfx.modules.direction.techniques import Status
from gaiavfx.shared.errors import ApplicationError


class DirectionInput(BaseModel):
    model_config = ConfigDict(extra="forbid")


class NameInput(DirectionInput):
    name: str = Field(min_length=1, max_length=120)


class AngleInput(NameInput):
    placement: dict | None = None


class DirectionAngleTextPatch(DirectionInput):
    movie_id: str = Field(min_length=1, max_length=120)
    expected_revision: int = Field(ge=1, strict=True)
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(max_length=8000)


class DirectionPlaceTextPatch(DirectionInput):
    movie_id: str = Field(min_length=1, max_length=120)
    expected_revision: int = Field(ge=1, strict=True)
    description: str = Field(max_length=8000)
    sound: str = Field(max_length=8000)
    overhead: str = Field(max_length=8000)


class PlaceDTO(BaseModel):
    id: str
    name: str
    description: str
    sound: str
    overhead: str
    revision: int
    plate_asset_id: str | None
    plate_angle_id: str | None
    set_id: str | None = None
    created_at: str

    @computed_field
    @property
    def view_producer(self) -> Literal["survey", "set_views"]:
        """Who makes this place's reviewed angle views (application/prepare_references.view_producer)."""
        return view_producer({"set_id": self.set_id}).operation


class PlaceSetInput(DirectionInput):
    """The set this place is built as (ADR 0019 §1), or null to unlink it."""

    expected_revision: int = Field(ge=1, strict=True)
    set_id: str | None = Field(default=None, min_length=1, max_length=36)


class AngleDTO(BaseModel):
    id: str
    place_id: str
    name: str
    description: str
    revision: int
    placement: dict | None
    created_at: str


class EvidenceDTO(BaseModel):
    id: str
    angle_id: str
    kind: str
    asset_id: str
    approved: bool
    approved_at: str | None
    approved_by: str | None
    inputs: dict | None
    created_at: str


class KeyframeDTO(BaseModel):
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


class CutFields(DirectionInput):
    segment_id: str
    frame: int
    from_angle_id: str
    to_angle_id: str
    relation: str | None = None
    crosses_barrier: bool | None = None
    relation_source: str = "unknown"
    receives_predecessor: bool = False
    side_facts: bool = False
    fidelity: bool = False
    override_technique: str | None = None


class CutCreate(CutFields):
    expected_revision: int = Field(strict=True, ge=1)


class CutPatch(DirectionInput):
    expected_revision: int = Field(strict=True, ge=1)
    frame: int | None = None
    from_angle_id: str | None = None
    to_angle_id: str | None = None
    relation: str | None = None
    crosses_barrier: bool | None = None
    relation_source: str | None = None
    receives_predecessor: bool | None = None
    side_facts: bool | None = None
    fidelity: bool | None = None
    override_technique: str | None = None
    insertion: dict | None = None


class CutDTO(CutFields):
    insertion: dict | None = None
    movie_id: str
    id: str
    revision: int
    updated_at: str


class ReceiptDTO(BaseModel):
    id: str
    movie_id: str
    command: str
    target_kind: Literal["movie", "subject", "segment", "phase", "cut", "angle", "place"]
    target_id: str
    before: dict
    after: dict
    undoes: str | None
    undone_at: str | None
    created_at: str


class DirectionRevisionInput(DirectionInput):
    expected_revision: int = Field(strict=True, ge=1)


class MovieInput(DirectionInput):
    title: str


class MoviePatch(DirectionRevisionInput):
    title: str


class SubjectCreate(DirectionRevisionInput):
    id: str | None = None
    name: str
    description: str
    keeps: list[str]
    identity_asset_id: str | None


class SubjectPatch(DirectionRevisionInput):
    name: str | None = None
    description: str | None = None
    keeps: list[str] | None = None
    identity_asset_id: str | None = None


class ContentDTO(DirectionInput):
    """Typed transport shape; Direction's content validator owns authored rules.

    Routes dump only explicitly set fields, preserving schema-1 documents and
    the difference between an unauthored cast and an authored empty cast.
    """

    model_config = ConfigDict(extra="forbid", strict=True)


class PlaceTextDTO(ContentDTO):
    noun: str
    description: str
    landmarks: str


class EventDTO(ContentDTO):
    at_frame: int
    description: str


class SpeechDTO(ContentDTO):
    speaker_id: str
    at_frame: int
    text: str
    language: str
    delivery: str = Field(default_factory=str)
    off_screen: bool = Field(default_factory=bool)


class StateDTO(ContentDTO):
    at_frame: int
    subject_id: str
    attribute: str
    value: str


class SegmentContentDTO(ContentDTO):
    subjects: list[str]
    place: PlaceTextDTO
    style: str = Field(default_factory=str)
    events: list[EventDTO] = Field(default_factory=list)
    speech: list[SpeechDTO] = Field(default_factory=list)
    states: list[StateDTO] = Field(default_factory=list)


class PhaseTextDTO(ContentDTO):
    description: str
    screen_direction: str
    sound: str


class ShotDTO(PhaseTextDTO):
    cast: list[str] = Field(default_factory=list)


class LaterPhaseDTO(PhaseTextDTO):
    start_frame: int


class PhaseInput(DirectionInput):
    start_frame: int = Field(strict=True)
    content: ShotDTO


class PhaseCreate(PhaseInput, DirectionRevisionInput):
    pass


class PhasePatch(DirectionRevisionInput):
    start_frame: int | None = Field(default=None, strict=True)
    content: ShotDTO | None = None


class SegmentCreate(DirectionRevisionInput):
    content: SegmentContentDTO
    delivered_frames: int = Field(strict=True)
    continues: bool = Field(strict=True)
    phases: list[PhaseInput] = Field(default_factory=list)
    at: int | None = Field(default=None, strict=True)


class SegmentPatch(DirectionRevisionInput):
    content: SegmentContentDTO | None = None
    delivered_frames: int | None = Field(default=None, strict=True)
    continues: bool | None = Field(default=None, strict=True)
    # The first shot (ADR 0019 §1); null clears a zero-cut segment's angle or the technique override.
    opening_angle_id: str | None = Field(default=None, min_length=1, max_length=36)
    opening_technique: str | None = Field(default=None, min_length=1, max_length=80)


class SegmentContinuationPatch(DirectionRevisionInput):
    """A continuing segment's handoff (docs/CONTINUATION.md). Only the fields sent change; null follows the rule."""

    method: Literal["paired", "masked"] | None = None
    context_frames: int | None = Field(default=None, strict=True)


class SegmentMove(DirectionRevisionInput):
    ordinal: int = Field(strict=True)


class DirectionSegmentSplitInput(DirectionRevisionInput):
    frame: int = Field(strict=True)


class DirectionSplitPointDTO(BaseModel):
    frame: int
    code: (
        Literal["cut_has_keyframes", "cut_has_suitability_history", "missing_initial_phase"] | None
    )
    reason: str | None


class DirectionSplitRefusalDTO(BaseModel):
    code: Literal[
        "not_a_boundary",
        "cut_has_keyframes",
        "cut_has_suitability_history",
        "missing_initial_phase",
    ]
    detail: str


class RecordDTO(BaseModel):
    id: str
    revision: int
    created_at: str
    updated_at: str


class DirectionMovieDTO(RecordDTO):
    title: str


class SubjectDTO(RecordDTO):
    movie_id: str
    name: str
    description: str
    keeps: list[str]
    identity_asset_id: str | None


class PhaseDTO(RecordDTO):
    segment_id: str
    start_frame: int
    content: ShotDTO
    content_schema: int


class SegmentDTO(RecordDTO):
    movie_id: str
    ordinal: int
    delivered_frames: int
    continues: bool
    content: SegmentContentDTO
    content_schema: int
    opening_angle_id: str | None = None
    opening_technique: str | None = None
    # A continuation's chosen handoff; null follows the rule (the segment plan says what it resolves to).
    continuation_method: str | None = None
    context_frames: int | None = None
    phases: list[PhaseDTO]
    cuts: list[CutDTO]


class MovieDetailDTO(DirectionMovieDTO):
    subjects: list[SubjectDTO]
    segments: list[SegmentDTO]


class MovieCommandDTO(DirectionMovieDTO):
    receipt: ReceiptDTO


class SubjectCommandDTO(SubjectDTO):
    receipt: ReceiptDTO


class SegmentCommandDTO(SegmentDTO):
    receipt: ReceiptDTO


class DirectionSegmentSplitCommandDTO(BaseModel):
    segments: list[SegmentDTO]
    receipt: ReceiptDTO


class PhaseCommandDTO(PhaseDTO):
    receipt: ReceiptDTO


class CutCommandDTO(CutDTO):
    receipt: ReceiptDTO


class RemovedDTO(BaseModel):
    id: str
    removed: bool
    receipt: ReceiptDTO


class UndoDTO(ReceiptDTO):
    record: dict


class DirectionAngleTextCommandDTO(AngleDTO):
    receipt: ReceiptDTO


class DirectionPlaceTextCommandDTO(PlaceDTO):
    receipt: ReceiptDTO


class AuthoredSubjectDTO(BaseModel):
    id: str
    name: str
    description: str
    keeps: list[str]
    identity_asset: str | None


class AssembledSegmentDTO(BaseModel):
    subjects: list[AuthoredSubjectDTO]
    place: PlaceTextDTO
    shots: list[ShotDTO]
    cut_frames: list[int]
    delivered_frames: int
    continues: bool
    style: str = Field(default_factory=str)
    phases: list[LaterPhaseDTO] = Field(default_factory=list)
    events: list[EventDTO] = Field(default_factory=list)
    speech: list[SpeechDTO] = Field(default_factory=list)
    states: list[StateDTO] = Field(default_factory=list)


class AssessmentDTO(BaseModel):
    coverage: Coverage
    reasons: list[str]
    evidence_ids: list[str]

    @computed_field
    @property
    def coverage_words(self) -> str:
        return COVERAGE_WORDS[self.coverage]


class NextActionDTO(BaseModel):
    action: Action
    target: str
    reason: str


class ChoiceDTO(BaseModel):
    technique_id: str
    title: str
    status: Status
    blocks: list[Literal["direction_text", "side_facts_text", "place_views", "pinned_frame"]]
    ready: bool
    actions: list[NextActionDTO]
    keyframe_offset_frames: int | None
    evidence: list[str]
    limitations: list[str]


class GapDTO(BaseModel):
    code: str
    message: str
    actions: list[NextActionDTO]


class CutBindingDTO(BaseModel):
    kind: Literal["view", "pin", "sheet"]
    id: str
    asset_id: str
    approved_at: str | None
    stale: bool
    reasons: list[str]
    suitability: Literal["suits", "unsuitable", "unjudged"]
    suitability_note: str


class CutSuitabilityInput(DirectionInput):
    reference_kind: Literal["view", "pin", "sheet"]
    reference_id: str
    verdict: Literal["suits", "unsuitable"]
    note: str = Field(default="", max_length=500)


class CutSuitabilityDTO(BaseModel):
    id: str
    cut_id: str
    reference_kind: Literal["view", "pin", "sheet"]
    reference_id: str
    verdict: Literal["suits", "unsuitable"]
    note: str
    judged_at: str
    judged_by: str
    superseded_at: str | None
    stale: bool


class CutPlanDTO(BaseModel):
    bindings: list[CutBindingDTO]
    cut_id: str
    assessment: AssessmentDTO
    recommended: ChoiceDTO | None
    alternatives: list[ChoiceDTO]
    unavailable: list[tuple[str, str]]
    gaps: list[GapDTO]
    override: bool


class DirectionSegmentRefusalDTO(BaseModel):
    code: str
    message: str
    cut_ids: list[str]
    status: int


class OpeningPlanDTO(BaseModel):
    """The plan of a segment's first shot (ADR 0019 §1)."""

    bindings: list[CutBindingDTO]
    segment_id: str
    angle_id: str
    assessment: AssessmentDTO
    recommended: ChoiceDTO | None
    alternatives: list[ChoiceDTO]
    unavailable: list[tuple[str, str]]
    gaps: list[GapDTO]
    override: bool


class PlaceStatusDTO(BaseModel):
    """What a segment's first shot takes from its place, in words (ADR 0019 §5)."""

    place_id: str
    place_name: str
    set_id: str | None
    set_name: str | None
    set_version: int | None
    uses_views: bool
    uses: str
    rendered_version: int | None
    update_available: bool
    blocker: str | None


class UseSetInput(DirectionInput):
    """Use a set for a scene (ADR 0019 §5): a segment without an opening angle names one, or a place and a new
    angle's name."""

    expected_revision: int = Field(ge=1, strict=True)
    set_id: str = Field(min_length=1, max_length=36)
    angle_id: str | None = Field(default=None, min_length=1, max_length=36)
    place_id: str | None = Field(default=None, min_length=1, max_length=36)
    angle_name: str | None = Field(default=None, min_length=1, max_length=120)


class UseSetSceneDTO(BaseModel):
    segment_id: str
    movie: str
    label: str


class UseSetPreviewDTO(BaseModel):
    """What Use set would change, said before it does."""

    set_name: str
    accepted: bool
    place_name: str
    angle_name: str
    new_angle: bool
    relinks_from: str | None
    other_scenes: list[UseSetSceneDTO]
    first_cut_changes: bool
    camera: str | None


class SeedStateDTO(BaseModel):
    """A set being built from a take (ADR 0019 §4), in words."""

    state: Literal["building", "refused", "failed", "done"]
    name: str  # the set it builds
    label: str  # a few words, for the References row
    message: str  # a sentence, for the dialog
    operation_id: str
    set_id: str | None
    shot: int  # the shot it builds from
    waiting: bool  # done, and its v1 still waits for the human
    can_retry: bool  # it stopped, and its frozen request can run again


class SeedShotDTO(BaseModel):
    index: int
    label: str
    start: int
    end: int
    times: str  # the shot as clock time, 0:00–0:04


class SeedPlaceDTO(BaseModel):
    id: str
    name: str
    set_id: str | None


class BuildSetPreviewDTO(BaseModel):
    """What Build a set from this take… would do, said before it does."""

    shots: list[SeedShotDTO]
    shot: int
    frames: int
    fill_unseen: bool  # the build fills in what the take never saw (ADR 0020)
    renders: int  # the probe renders it reserves: 0 without filling
    estimate: str  # what the build costs, in words
    what: str  # what it makes, in words, with filling on or off
    name: str
    candidate_id: str | None
    place: SeedPlaceDTO | None
    can_use_for_place: bool
    refusal: str | None
    seed: SeedStateDTO | None


class ContinuationPlanDTO(BaseModel):
    """How a continuing segment hands over its predecessor's motion (docs/CONTINUATION.md)."""

    method: str  # what the segment renders with
    explicit_method: str | None  # the choice made; null follows the rule
    recommended: Literal["paired", "masked"]
    reason: str  # why the rule recommends it, in words
    masked_problem: str | None  # why masked handoff does not qualify here
    context_frames: int
    explicit_context: int | None
    default_context: int
    context_choices: list[int]  # the lengths this join can carry now
    audio_context_frames: int
    audio_follows_picture: bool  # masked ties the audio history to the picture context


class DirectionSegmentPlanDTO(BaseModel):
    segment_id: str
    cuts: list[CutPlanDTO]
    technique_id: str
    title: str
    ready: bool
    refusals: list[DirectionSegmentRefusalDTO]
    sheet_asset: str | None
    opening: OpeningPlanDTO | None = None
    place: PlaceStatusDTO | None = None
    seed: SeedStateDTO | None = None
    build_refusal: str | None = None  # why Build a set from this take… cannot run now
    continuation: ContinuationPlanDTO | None = None  # set for a continuing segment


def direction_router(workspace, authenticate):
    router = APIRouter(prefix="/api/direction", dependencies=[Depends(authenticate)])
    direction = DIRECTION

    def empty_place_text(session, movie_id, *texts):
        cast = [row["name"] for row in direction.subjects(session, movie_id)]
        try:
            for value in texts:
                validate_place_text(value, cast)
        except ValueError as exc:
            raise ApplicationError(str(exc), 422) from exc

    def cut_dto(session, cut):
        return cut | {"movie_id": direction.read_segment(session, cut["segment_id"])["movie_id"]}

    def segment_dto(session, segment):
        return segment | {"cuts": [cut_dto(session, cut) for cut in segment["cuts"]]}

    @router.post("/movies", response_model=MovieCommandDTO, status_code=201)
    def create_movie(body: MovieInput):
        with workspace.db.transaction() as session:
            return direction.create_movie(session, body.title)

    @router.get("/movies", response_model=list[DirectionMovieDTO])
    def movies():
        with workspace.db.transaction() as session:
            return direction.movies(session)

    @router.get(
        "/movies/{movie_id}", response_model=MovieDetailDTO, response_model_exclude_unset=True
    )
    def movie(movie_id: str):
        with workspace.db.transaction() as session:
            row = direction.read_movie(session, movie_id)
            return row | {"segments": [segment_dto(session, seg) for seg in row["segments"]]}

    @router.patch("/movies/{movie_id}", response_model=MovieCommandDTO)
    def update_movie(movie_id: str, body: MoviePatch):
        with workspace.db.transaction() as session:
            return direction.update_movie(session, movie_id, **body.model_dump())

    @router.post("/movies/{movie_id}/subjects", response_model=SubjectCommandDTO, status_code=201)
    def add_subject(movie_id: str, body: SubjectCreate):
        with workspace.db.transaction() as session:
            return direction.add_subject(
                session, movie_id, subject_id=body.id, **body.model_dump(exclude={"id"})
            )

    @router.get("/movies/{movie_id}/subjects", response_model=list[SubjectDTO])
    def subjects(movie_id: str):
        with workspace.db.transaction() as session:
            return direction.subjects(session, movie_id)

    @router.patch("/subjects/{subject_id}", response_model=SubjectCommandDTO)
    def update_subject(subject_id: str, body: SubjectPatch):
        with workspace.db.transaction() as session:
            return direction.update_subject(
                session, subject_id, **body.model_dump(exclude_unset=True)
            )

    @router.post("/subjects/{subject_id}/remove", response_model=RemovedDTO)
    def remove_subject(subject_id: str, body: DirectionRevisionInput):
        with workspace.db.transaction() as session:
            return direction.remove_subject(session, subject_id, body.expected_revision)

    @router.post(
        "/movies/{movie_id}/segments",
        response_model=SegmentCommandDTO,
        status_code=201,
        response_model_exclude_unset=True,
    )
    def add_segment(movie_id: str, body: SegmentCreate):
        with workspace.db.transaction() as session:
            return segment_dto(
                session,
                direction.add_segment(
                    session,
                    movie_id,
                    **body.model_dump(exclude={"phases"}, exclude_unset=True),
                    phases=[
                        (phase.start_frame, phase.content.model_dump(exclude_unset=True))
                        for phase in body.phases
                    ],
                ),
            )

    @router.get(
        "/segments/{segment_id}", response_model=SegmentDTO, response_model_exclude_unset=True
    )
    def segment(segment_id: str):
        with workspace.db.transaction() as session:
            return segment_dto(session, direction.read_segment(session, segment_id))

    @router.get(
        "/segments/{segment_id}/assembled",
        response_model=AssembledSegmentDTO,
        response_model_exclude_unset=True,
    )
    def assembled_segment(segment_id: str):
        with workspace.db.transaction() as session:
            return segment_to_dict(*direction.assemble_segment(session, segment_id))

    @router.patch(
        "/segments/{segment_id}",
        response_model=SegmentCommandDTO,
        response_model_exclude_unset=True,
    )
    def update_segment(segment_id: str, body: SegmentPatch):
        with workspace.db.transaction() as session:
            return segment_dto(
                session,
                direction.update_segment(
                    session, segment_id, **body.model_dump(exclude_unset=True)
                ),
            )

    @router.patch(
        "/segments/{segment_id}/continuation",
        response_model=SegmentCommandDTO,
        response_model_exclude_unset=True,
    )
    def set_segment_continuation(segment_id: str, body: SegmentContinuationPatch):
        with workspace.db.transaction() as session:
            return segment_dto(
                session,
                set_continuation(
                    session,
                    segment_id,
                    body.expected_revision,
                    **body.model_dump(exclude_unset=True, exclude={"expected_revision"}),
                ),
            )

    @router.get("/segments/{segment_id}/use-set", response_model=UseSetPreviewDTO)
    def use_set_preview(
        segment_id: str,
        set_id: str = Query(min_length=1, max_length=36),
        angle_id: str | None = Query(default=None, max_length=36),
        place_id: str | None = Query(default=None, max_length=36),
        angle_name: str | None = Query(default=None, max_length=120),
    ):
        with workspace.db.transaction() as session:
            return set_scenes.use_set_preview(
                session,
                segment_id,
                set_id=set_id,
                angle_id=angle_id or None,
                place_id=place_id or None,
                angle_name=angle_name or None,
            )

    @router.get("/segments/{segment_id}/build-set", response_model=BuildSetPreviewDTO)
    def build_set_preview(
        segment_id: str,
        shot: int = Query(default=0, ge=0, le=500),
        fill_unseen: bool = Query(default=True),
    ):
        with workspace.db.transaction() as session:
            return set_seed.seed_preview(
                session,
                workspace.library,
                segment_id=segment_id,
                shot=shot,
                fill_unseen=fill_unseen,
            )

    @router.post("/segments/{segment_id}/use-set", response_model=SegmentDTO)
    def use_set(segment_id: str, body: UseSetInput):
        """Build the first shot's place as the set and ask the first shot for its views (ADR 0019 §5)."""
        with workspace.db.transaction() as session:
            return segment_dto(
                session, set_scenes.use_set(session, segment_id, **body.model_dump())
            )

    @router.get("/segments/{segment_id}/split-points", response_model=list[DirectionSplitPointDTO])
    def segment_split_points(segment_id: str):
        with workspace.db.transaction() as session:
            return direction.split_points(session, segment_id)

    @router.post(
        "/segments/{segment_id}/split",
        response_model=DirectionSegmentSplitCommandDTO,
        response_model_exclude_unset=True,
        responses={422: {"model": DirectionSplitRefusalDTO}},
    )
    def split_segment(segment_id: str, body: DirectionSegmentSplitInput):
        try:
            with workspace.db.transaction() as session:
                result = direction.split_segment(session, segment_id, **body.model_dump())
                return result | {"segments": [segment_dto(session, s) for s in result["segments"]]}
        except SegmentSplitRefusal as error:
            return JSONResponse(
                {"code": error.code, "detail": str(error)}, status_code=error.status
            )

    @router.post("/segments/{segment_id}/remove", response_model=RemovedDTO)
    def remove_segment(segment_id: str, body: DirectionRevisionInput):
        with workspace.db.transaction() as session:
            return direction.remove_segment(session, segment_id, body.expected_revision)

    @router.post(
        "/segments/{segment_id}/move",
        response_model=SegmentCommandDTO,
        response_model_exclude_unset=True,
    )
    def move_segment(segment_id: str, body: SegmentMove):
        with workspace.db.transaction() as session:
            return segment_dto(
                session, direction.move_segment(session, segment_id, **body.model_dump())
            )

    @router.post(
        "/segments/{segment_id}/phases",
        response_model=PhaseCommandDTO,
        status_code=201,
        response_model_exclude_unset=True,
    )
    def add_phase(segment_id: str, body: PhaseCreate):
        with workspace.db.transaction() as session:
            return direction.add_phase(session, segment_id, **body.model_dump(exclude_unset=True))

    @router.patch(
        "/phases/{phase_id}", response_model=PhaseCommandDTO, response_model_exclude_unset=True
    )
    def update_phase(phase_id: str, body: PhasePatch):
        with workspace.db.transaction() as session:
            return direction.update_phase(session, phase_id, **body.model_dump(exclude_unset=True))

    @router.post("/phases/{phase_id}/remove", response_model=RemovedDTO)
    def remove_phase(phase_id: str, body: DirectionRevisionInput):
        with workspace.db.transaction() as session:
            return direction.remove_phase(session, phase_id, body.expected_revision)

    @router.get("/movies/{movie_id}/receipts", response_model=list[ReceiptDTO])
    def receipts(movie_id: str, limit: int = Query(50, ge=1)):
        with workspace.db.transaction() as session:
            return direction.receipts(session, movie_id, limit)

    @router.post("/receipts/{receipt_id}/undo", response_model=UndoDTO)
    def undo(receipt_id: str):
        with workspace.db.transaction() as session:
            return direction.undo(session, receipt_id)

    @router.post("/places", response_model=PlaceDTO, status_code=201)
    def create_place(body: NameInput):
        with workspace.db.transaction() as session:
            return direction.create_place(session, body.name)

    @router.get("/places", response_model=list[PlaceDTO])
    def list_places():
        with workspace.db.transaction() as session:
            return direction.places(session)

    @router.get("/places/{place_id}", response_model=PlaceDTO)
    def get_place(place_id: str):
        with workspace.db.transaction() as session:
            return direction.read_place(session, place_id)

    @router.put("/places/{place_id}/set", response_model=PlaceDTO)
    def link_place_set(place_id: str, body: PlaceSetInput):
        """Build this place as a set, or unlink it (ADR 0019 §1)."""
        with workspace.db.transaction() as session:
            return sets.link_place(session, place_id, **body.model_dump())

    @router.post("/places/{place_id}/angles", response_model=AngleDTO, status_code=201)
    def add_angle(place_id: str, body: AngleInput):
        with workspace.db.transaction() as session:
            return direction.add_angle(session, place_id, body.name, body.placement)

    @router.patch("/places/{place_id}", response_model=DirectionPlaceTextCommandDTO)
    def edit_place_text(place_id: str, body: DirectionPlaceTextPatch):
        with workspace.db.transaction() as session:
            empty_place_text(session, body.movie_id, body.description, body.sound, body.overhead)
            return direction.update_place(session, place_id, **body.model_dump())

    @router.get("/places/{place_id}/angles", response_model=list[AngleDTO])
    def list_angles(place_id: str):
        with workspace.db.transaction() as session:
            return direction.angles(session, place_id)

    @router.patch("/angles/{angle_id}", response_model=DirectionAngleTextCommandDTO)
    def edit_angle_text(angle_id: str, body: DirectionAngleTextPatch):
        with workspace.db.transaction() as session:
            empty_place_text(session, body.movie_id, body.name, body.description)
            return direction.update_angle(session, angle_id, **body.model_dump())

    @router.get("/angles/{angle_id}/evidence", response_model=list[EvidenceDTO])
    def angle_evidence(angle_id: str):
        with workspace.db.transaction() as session:
            return direction.evidence(session, angle_id)

    @router.post("/evidence/{evidence_id}/approve", response_model=EvidenceDTO)
    def approve_evidence(evidence_id: str):
        with workspace.db.transaction() as session:
            evidence = direction.read_evidence(session, evidence_id)
            if evidence["kind"] not in {"imported", "set_view"}:
                raise ApplicationError(
                    "Only imported evidence and stage views use this approval route.", 422
                )
            return direction.approve_evidence(session, evidence_id)

    @router.post("/cuts", response_model=CutCommandDTO, status_code=201)
    def create_cut(body: CutCreate):
        with workspace.db.transaction() as session:
            return cut_dto(session, direction.create_cut(session, **body.model_dump()))

    @router.get("/cuts/{cut_id}", response_model=CutDTO)
    def get_cut(cut_id: str):
        with workspace.db.transaction() as session:
            return cut_dto(session, direction.read_cut(session, cut_id))

    @router.patch("/cuts/{cut_id}", response_model=CutCommandDTO)
    def update_cut(cut_id: str, body: CutPatch):
        changes = body.model_dump(exclude_unset=True, exclude={"expected_revision"})
        with workspace.db.transaction() as session:
            return cut_dto(
                session, direction.update_cut(session, cut_id, body.expected_revision, **changes)
            )

    @router.get("/cuts/{cut_id}/keyframes", response_model=list[KeyframeDTO])
    def cut_keyframes(cut_id: str):
        with workspace.db.transaction() as session:
            return direction.keyframes(session, cut_id)

    @router.post("/cuts/{cut_id}/remove", response_model=RemovedDTO)
    def remove_cut(cut_id: str, body: DirectionRevisionInput):
        with workspace.db.transaction() as session:
            return direction.remove_cut(session, cut_id, body.expected_revision)

    @router.post("/cuts/{cut_id}/suitability", response_model=CutSuitabilityDTO, status_code=201)
    def judge_cut_suitability(cut_id: str, body: CutSuitabilityInput):
        with workspace.db.transaction() as session:
            return direction.judge_suitability(session, cut_id, **body.model_dump())

    @router.get("/cuts/{cut_id}/suitability", response_model=list[CutSuitabilityDTO])
    def cut_suitability(cut_id: str):
        with workspace.db.transaction() as session:
            return direction.suitability(session, cut_id)

    @router.get("/cuts/{cut_id}/plan", response_model=CutPlanDTO)
    def get_cut_plan(cut_id: str, integration: str = DEFAULT_INTEGRATION):
        with workspace.db.transaction() as session:
            return CutPlanDTO.model_validate(
                plan_to_dict(plan_for_cut(session, cut_id, integration))
            )

    @router.get("/segments/{segment_id}/plan", response_model=DirectionSegmentPlanDTO)
    def get_segment_plan(
        segment_id: str,
        integration: str = DEFAULT_INTEGRATION,
        sheet_asset: str | None = None,
        context_artifact: str | None = None,
    ):
        with workspace.db.transaction() as session:
            return plan_to_dict(
                plan_for_segment(
                    session,
                    segment_id,
                    integration,
                    sheet_asset=sheet_asset,
                    context_artifact=context_artifact,
                )
            )

    return router
