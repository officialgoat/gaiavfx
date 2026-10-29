"""HTTP boundary for film recasts: shots, characters, mappings, runs, choices and conforms.

The DTOs are the frozen contract of ADR 0012. Human-only commands (confirming a mapping,
choosing a shot version, conforming) are separate routes the agent harness never calls.
"""

from __future__ import annotations

import hashlib
import hmac
import time
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from gaiavfx.api.production import OperationDTO
from gaiavfx.application import recast, recast_edits, recast_lines, recast_voice
from gaiavfx.application.recast_run import estimate_run
from gaiavfx.application.target_faces import TargetReportRefused
from gaiavfx.shared.errors import ApplicationError


class RecastInputDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RevisionInputDTO(RecastInputDTO):
    expected_revision: int = Field(ge=0)


class FrameRangeDTO(BaseModel):
    """Film frames, start inclusive, end exclusive."""

    model_config = ConfigDict(extra="forbid")
    start: int = Field(ge=0)
    end: int = Field(ge=1)


class FilmDTO(BaseModel):
    asset_id: str
    name: str
    fps_num: int
    fps_den: int
    frame_count: int
    width: int
    height: int
    has_audio: bool
    fps_status: Literal["tested", "developing", "experimental", "unsupported"]
    letterbox: list[int] | None  # [top, bottom] rows of bars at native size, when detected


class AnalysisStatusDTO(BaseModel):
    operation_id: str | None
    state: Literal["not_started", "running", "succeeded", "failed"]
    stage: Literal["shots", "faces", "cast", "done"] | None
    done: int
    total: int
    error: str | None


class FaceDTO(BaseModel):
    """A face crop from the film (Library artifact rendition), for cards and trays."""

    artifact_id: str
    url: str
    frame: int
    track_id: str | None = None  # set for track crops, so a tray face can be moved


class CharacterTrackDTO(BaseModel):
    """One face track of a character, for the drawer that splits or moves tracks."""

    id: str
    shot_id: str
    frames_with_face: int
    face: FaceDTO | None


class ReferenceDTO(BaseModel):
    asset_id: str | None
    state: Literal["making", "waiting", "approved", "rejected", "failed"]


class TargetDTO(BaseModel):
    reference: ReferenceDTO | None = None
    face: Literal["checking", "face", "no_face", "failed"]
    asset_id: str
    label: str
    state: Literal["proposed", "confirmed"]
    proposed_by: Literal["user", "agent"]


class SimilarCharacterDTO(BaseModel):
    character_id: str
    similarity: float


class VoiceSampleDTO(BaseModel):
    """One Library audio recording of the target voice, in the order the user chose it."""

    asset_id: str
    name: str
    seconds: float


class VoiceDTO(BaseModel):
    """A character's voice (ADR 0016 §1, §4): the chosen recordings, the shared build's progress and the audition.
    No operation id, artifact id, path, folder or worker text; the audition reel's asset id is the only other id."""

    samples: list[VoiceSampleDTO]
    seconds: float
    state: Literal["proposed", "confirmed"]
    proposed_by: Literal["agent", "user"]
    model_state: Literal["making", "waiting", "approved", "rejected", "failed"] | None
    stage: (
        Literal["samples", "seed", "material", "convert", "filter", "train", "package", "audition"]
        | None
    )
    done: int
    total: int
    error: str | None  # one actionable sentence when the build or the audition failed
    # Why nothing can start now (D2 text); None when the next step is available or running.
    reason: str | None
    audition_asset_id: str | None  # the reel, once an audition succeeded
    library_changed: bool  # the expressive recordings changed since this voice was built
    # Build voice refuses these recordings: outside the bounds, proposed, or a hidden character (Python decides).
    blocked: bool


class CharacterDTO(BaseModel):
    id: str
    label: str
    hidden: bool
    face: FaceDTO | None
    screen_time_frames: int
    shot_count: int
    presence: list[list[int]]  # merged [start, end) film-frame ranges where a face was seen
    track_count: int
    tracks: list[CharacterTrackDTO]
    target: TargetDTO | None
    similar: list[SimilarCharacterDTO] = Field(default_factory=list)
    lines_proposed: int = 0  # lines proposed for this character (computed or by the agent)
    lines_confirmed: int = 0
    voice: VoiceDTO | None = None  # None until recordings are chosen or proposed


class AppearanceDTO(BaseModel):
    character_id: str | None  # None: an unknown face or a person without a face
    track_ids: list[str]
    frames_with_face: int
    coverage: float  # fraction of the shot's frames with this character's face
    confirmed: bool  # user confirmed, or identity from a face with a clear margin


class AppearanceLinkDTO(BaseModel):
    track_id: str
    analysis_operation_id: str
    character_id: str
    state: Literal["proposed", "confirmed", "rejected"]
    proposed_by: Literal["analysis", "agent", "user"]
    evidence: dict = Field(default_factory=dict)
    created_at: str
    decided_at: str | None


class AppearanceDecisionInput(RevisionInputDTO):
    character_id: str = Field(min_length=1, max_length=36)


class AppearanceProposalInput(AppearanceDecisionInput):
    proposed_by: Literal["analysis", "user"] = "user"
    evidence: dict = Field(default_factory=dict)


class WindowDTO(BaseModel):
    index: int
    start: int
    end: int
    length: int  # generated frames (5 + 17k)
    operation_id: str | None
    state: str | None


class RecastGapDTO(BaseModel):
    code: str
    message: str


class ShotPlanDTO(BaseModel):
    performers: list[str]  # character ids replaced in this shot (at most two)
    grouping: Literal["single", "joint"]
    windows: list[WindowDTO]
    renders_per_window: int
    gaps: list[RecastGapDTO]
    canvas_scale: float | None  # generated pixels / native pixels of the replaced region
    reasons: list[str] = Field(default_factory=list)


class ShotCheckDTO(BaseModel):
    check: str
    verdict: Literal["pass", "fail", "unsupported"]
    calibrated: bool
    measured: float | None
    reason: str  # blind-safe


class ShotVersionDTO(BaseModel):
    """One complete replacement of the shot (all windows assembled); anonymous until reveal."""

    id: str
    letter: str
    asset_id: str  # assembled preview video of the shot
    checks: list[ShotCheckDTO]


class ShotChoiceDTO(BaseModel):
    version: str  # a ShotVersionDTO id, or "original"
    chosen_at: str
    revision: int


ShotStatus = Literal[
    "absent",  # no mapped character appears
    "excluded",  # the user left it out
    "gap",  # a typed gap prevents replacement
    "unplanned",  # mapped, but no plan requested
    "analysing",  # window analyses have not settled
    "planned",
    "queued",
    "rendering",
    "needs_review",
    "approved",
    "kept_original",
    "failed",
]


class ShotLayerDTO(BaseModel):
    character_id: str
    state: Literal["rendering", "settled", "cancelled", "failed"]
    windows_settled: int
    windows_total: int
    renders_reserved: int


class RecastShotDTO(BaseModel):
    appearance_links: list[AppearanceLinkDTO] = Field(default_factory=list)
    layers: list[ShotLayerDTO] = Field(default_factory=list)
    retaking: bool = False  # another take is active; no generation provenance
    id: str
    index: int
    start: int
    end: int
    poster: FaceDTO | None
    appearances: list[AppearanceDTO]
    included: bool
    priority: int | None
    plan: ShotPlanDTO | None
    status: ShotStatus
    versions: list[ShotVersionDTO]
    choice: ShotChoiceDTO | None


class EstimateDTO(BaseModel):
    shots: int
    windows: int
    windows_pending: int = 0
    renders: int
    gpu_seconds_low: int
    gpu_seconds_high: int
    storage_bytes: int
    basis: Literal["seed", "measured"]


class RunDTO(BaseModel):
    reason: str | None = None
    state: Literal["idle", "running", "paused", "finished"]
    mode: Literal["draft", "final"]
    range: FrameRangeDTO | None
    render_budget: int
    renders_used: int
    shots_done: int
    shots_total: int
    current_shot_id: str | None


class ConformDTO(BaseModel):
    operation_id: str
    state: str
    mode: Literal["chosen", "preview_unreviewed"]
    range: FrameRangeDTO | None
    progress: float
    asset_id: str | None
    created_at: str
    codec: Literal["h264", "prores"] = "h264"
    error: str | None = None


class NotSamePairDTO(BaseModel):
    character_a: str
    character_b: str


class RecastEditDTO(BaseModel):
    """One journaled cast edit (ADR 0012 Amendment 7 §1); its row images never leave the backend."""

    id: str
    command: str
    kind: Literal["edit", "undo", "redo"]  # an undo or redo inverted an earlier entry
    summary: str
    created_at: str
    undone_at: str | None


class DialogueStatusDTO(BaseModel):
    """The film's shared dialogue analysis (ADR 0016 §2), as the Voice step reads it."""

    operation_id: str | None
    state: Literal["not_started", "running", "succeeded", "failed"]
    stage: Literal["audio", "separate", "transcribe", "speakers", "done"] | None
    done: int
    total: int
    error: str | None
    attempt: int
    version: int
    sample_rate: int
    reason: str | None  # why the Voice step cannot start now (D2 text); None when it can or has run
    gap: RecastGapDTO | None  # no_audio | channels


class LineCountsDTO(BaseModel):
    segments: int
    proposed: int
    confirmed: int
    rejected: int


class LineWordDTO(BaseModel):
    start_sample: int
    end_sample: int
    word: str


class LineDTO(BaseModel):
    """One transcript segment as a line: its decision, or the rule's computed proposal (48 kHz samples)."""

    segment_key: str
    index: int
    shot_id: str | None
    start_sample: int
    end_sample: int
    start_frame: int
    end_frame: int
    text: str
    words: list[LineWordDTO]
    character_id: str | None
    state: Literal["proposed", "confirmed", "rejected"] | None
    proposed_by: Literal["analysis", "agent", "user"] | None
    edges: Literal["rule", "user"]
    whispered: bool
    median_hz: float | None
    # Scores by character id, in_shot, seed_for, reason.
    evidence: dict = Field(default_factory=dict)
    decided_at: str | None


class LineProfileDTO(BaseModel):
    segments: int
    seconds: float


class LinesDTO(BaseModel):
    dialogue_operation_id: str | None
    rule_version: int
    sample_rate: int
    lines: list[LineDTO]
    profiles: dict[str, LineProfileDTO]


class LineDecisionInput(RevisionInputDTO):
    character_id: str = Field(min_length=1, max_length=36)


class LineEdgesInput(RevisionInputDTO):
    start_sample: int | None = Field(default=None, ge=0)
    end_sample: int | None = Field(default=None, ge=1)


class BulkConfirmInput(RevisionInputDTO):
    character_id: str = Field(min_length=1, max_length=36)
    segment_keys: list[str] | None = Field(default=None, max_length=5000)


class VoiceSamplesInput(RevisionInputDTO):
    """Library audio recordings of the target voice, in order: 15 to 60 seconds in all."""

    asset_ids: list[str] = Field(min_length=1, max_length=12)


class BuildVoiceInput(RevisionInputDTO):
    rebuild: bool = False  # build again after the expressive recordings changed


class RecastDTO(BaseModel):
    id: str
    title: str
    revision: int
    film: FilmDTO
    analysis: AnalysisStatusDTO
    dialogue: DialogueStatusDTO
    lines: LineCountsDTO
    characters: list[CharacterDTO]
    unknown_faces: list[FaceDTO]
    not_same: list[NotSamePairDTO] = []
    edits: list[RecastEditDTO] = Field(default_factory=list)  # the newest 20, newest first
    shots: list[RecastShotDTO]
    run: RunDTO
    estimate: EstimateDTO | None
    conforms: list[ConformDTO]


class RecastSummaryDTO(BaseModel):
    id: str
    title: str
    source_asset_id: str
    revision: int
    analysis_state: str
    shots_total: int
    shots_approved: int
    updated_at: str


class ShotRevealDTO(BaseModel):
    shot_id: str
    versions: list[dict]  # per version: windows with seed, treatment, framing, round, candidate ids


class CreateRecastInput(RecastInputDTO):
    source_asset_id: str
    title: str | None = Field(default=None, max_length=120)
    request_key: str = Field(min_length=1, max_length=80)


class TargetInput(RevisionInputDTO):
    asset_id: str
    label: str = Field(min_length=1, max_length=80)


class CharacterPatchInput(RevisionInputDTO):
    label: str | None = Field(default=None, min_length=1, max_length=80)
    hidden: bool | None = None


class MergeInput(RevisionInputDTO):
    into: str
    sources: list[str] = Field(min_length=1, max_length=32)


class NotSameInput(RevisionInputDTO):
    pairs: list[NotSamePairDTO] = Field(min_length=1, max_length=50)


class MoveTracksInput(RevisionInputDTO):
    track_ids: list[str] = Field(min_length=1, max_length=256)
    to: str  # a character id, "new" or "unknown"


class ShotPatchInput(RevisionInputDTO):
    included: bool | None = None
    priority: int | None = Field(default=None, ge=0, le=100000)


class PlanInput(RevisionInputDTO):
    range: FrameRangeDTO | None = None


class RunInput(RevisionInputDTO):
    mode: Literal["draft", "final"] = "draft"
    range: FrameRangeDTO | None = None
    render_budget: int = Field(ge=1, le=10000)


class ChoiceInput(RevisionInputDTO):
    """A version id, "original" (keep the shot as filmed) or "unchosen" (clear the choice; undo)."""

    version: str = Field(min_length=1, max_length=64)


class HeadPassInput(RevisionInputDTO):
    version: str = Field(min_length=1, max_length=64)
    character_id: str = Field(min_length=1, max_length=36)


class RetakeInput(RevisionInputDTO):
    mode: Literal["draft", "final"] = "draft"


class ConformInput(RecastInputDTO):
    mode: Literal["chosen", "preview_unreviewed"] = "chosen"
    range: FrameRangeDTO | None = None
    codec: Literal["h264", "prores"] = "h264"
    request_key: str = Field(min_length=1, max_length=80)


def recast_router(workspace, authenticate, token):
    """Authenticated recast routes, plus face images served by signed links.

    An <img> cannot send the bearer token, so face URLs in every response carry an expiring
    HMAC signature, as Library media links do.
    """
    router = APIRouter(prefix="/api/recasts", dependencies=[Depends(authenticate)])
    public = APIRouter()

    def signature(recast_id, artifact_id, expires):
        message = f"recast-face:{recast_id}:{artifact_id}:{expires}"
        return hmac.new(token.encode(), message.encode(), hashlib.sha256).hexdigest()

    def sign(value):
        if isinstance(value, list):
            return [sign(v) for v in value]
        if not isinstance(value, dict):
            return value
        result = {k: sign(v) for k, v in value.items()}
        url = result.get("url")
        if isinstance(url, str) and url.startswith("/api/recasts/") and "/faces/" in url:
            recast_id, artifact_id = url.removeprefix("/api/recasts/").split("/faces/", 1)
            # Stable within the hour, so repeated reads agree; valid for at least 6 hours.
            expires = (int(time.time()) // 3600 + 7) * 3600
            result["url"] = (
                f"{url}?expires={expires}&signature={signature(recast_id, artifact_id, expires)}"
            )
        return result

    def read(call, *args, **kwargs):
        with workspace.db.transaction() as session:
            return sign(call(session, workspace.library, *args, **kwargs))

    def expressive_library():
        """The expressive recordings folder from Settings (read before the recast's transaction)."""
        return workspace.settings()["expressive_library"]

    @public.get("/api/recasts/{recast_id}/faces/{artifact_id}", response_class=FileResponse)
    def face(recast_id: str, artifact_id: str, expires: int, sig: str = Query(alias="signature")):
        expected = signature(recast_id, artifact_id, expires)
        if expires < time.time() or not hmac.compare_digest(sig, expected):
            raise ApplicationError("This face link expired. Reopen the recast.", 401)
        with workspace.db.transaction() as session:
            return FileResponse(
                recast.face_path(session, workspace.library, recast_id, artifact_id)
            )

    @router.get("", response_model=list[RecastSummaryDTO])
    def recasts():
        return read(recast.list_recasts)

    @router.post("", response_model=RecastDTO, status_code=201)
    def create(body: CreateRecastInput, response: Response):
        result = read(recast.create_recast, **body.model_dump())
        response.status_code = 201 if result.pop("_created", True) else 200
        return result

    @router.get("/{recast_id}", response_model=RecastDTO)
    def get(recast_id: str):
        # Only this read compares a built voice with the expressive recordings (`library_changed`).
        folder = expressive_library()
        return read(recast.recast_state, recast_id, expressive_library=folder)

    @router.get("/{recast_id}/estimate", response_model=EstimateDTO)
    def estimate(
        recast_id: str,
        mode: Literal["draft", "final"] = "draft",
        start: int | None = Query(None, ge=0),
        end: int | None = Query(None, ge=1),
    ):
        """What a run over a range would cost in a mode, before it starts (the run's own estimate covers the last
        run's range)."""
        with workspace.db.transaction() as session:
            state = recast.recast_state(session, workspace.library, recast_id, private_plans=True)
        if (start is None) != (end is None):
            raise ApplicationError("A range needs both a start and an end.", 422)
        frame_range = None if start is None else {"start": start, "end": end}
        return estimate_run(state, mode=mode, frame_range=frame_range)[1]

    @router.patch("/{recast_id}/characters/{character_id}", response_model=RecastDTO)
    def patch_character(recast_id: str, character_id: str, body: CharacterPatchInput):
        return read(recast.patch_character, recast_id, character_id, **body.model_dump())

    @router.post("/{recast_id}/characters/merge", response_model=RecastDTO)
    def merge(recast_id: str, body: MergeInput):
        return read(recast.merge_characters, recast_id, **body.model_dump())

    @router.post("/{recast_id}/not-same", response_model=RecastDTO)
    def not_same(recast_id: str, body: NotSameInput):
        return read(
            recast.set_not_same,
            recast_id,
            expected_revision=body.expected_revision,
            pairs=[(p.character_a, p.character_b) for p in body.pairs],
        )

    @router.post("/{recast_id}/tracks/move", response_model=RecastDTO)
    def move_tracks(recast_id: str, body: MoveTracksInput):
        return read(recast.move_tracks, recast_id, **body.model_dump())

    @router.post("/{recast_id}/edits/{edit_id}/undo", response_model=RecastDTO)
    def undo_edit(recast_id: str, edit_id: str, body: RevisionInputDTO):
        """Human only: the guarded inverse of one cast edit. Redo is undo of the inverse entry."""
        return read(recast_edits.undo_edit, recast_id, edit_id, **body.model_dump())

    appearance_path = "/{recast_id}/shots/{shot_id}/appearances/{analysis_operation_id}/{track_id}"

    @router.post(appearance_path + "/propose", response_model=RecastDTO)
    def propose_appearance(
        recast_id: str,
        shot_id: str,
        analysis_operation_id: str,
        track_id: str,
        body: AppearanceProposalInput,
    ):
        return read(
            recast.propose_appearance,
            recast_id,
            shot_id,
            analysis_operation_id,
            track_id,
            **body.model_dump(),
        )

    @router.post(appearance_path + "/confirm", response_model=RecastDTO)
    def confirm_appearance(
        recast_id: str,
        shot_id: str,
        analysis_operation_id: str,
        track_id: str,
        body: AppearanceDecisionInput,
    ):
        return read(
            recast.confirm_appearance,
            recast_id,
            shot_id,
            analysis_operation_id,
            track_id,
            **body.model_dump(),
        )

    @router.post(appearance_path + "/reject", response_model=RecastDTO)
    def reject_appearance(
        recast_id: str,
        shot_id: str,
        analysis_operation_id: str,
        track_id: str,
        body: AppearanceDecisionInput,
    ):
        return read(
            recast.reject_appearance,
            recast_id,
            shot_id,
            analysis_operation_id,
            track_id,
            **body.model_dump(),
        )

    @router.delete(appearance_path, response_model=RecastDTO)
    def clear_appearance(
        recast_id: str,
        shot_id: str,
        analysis_operation_id: str,
        track_id: str,
        expected_revision: int = Query(ge=0),
    ):
        return read(
            recast.clear_appearance,
            recast_id,
            shot_id,
            analysis_operation_id,
            track_id,
            expected_revision=expected_revision,
        )

    @router.post("/{recast_id}/shots/{shot_id}/appearances/suggest", response_model=RecastDTO)
    def suggest_appearances(recast_id: str, shot_id: str, body: RevisionInputDTO):
        from gaiavfx.application.recast_appearance import suggest_appearances

        return sign(
            suggest_appearances(
                workspace.db, workspace.library, recast_id, shot_id, **body.model_dump()
            )
        )

    @router.put("/{recast_id}/characters/{character_id}/target", response_model=RecastDTO)
    def set_target(recast_id: str, character_id: str, body: TargetInput):
        """A human mapping: confirmed at once."""
        return read(recast.set_target, recast_id, character_id, **body.model_dump())

    @router.post("/{recast_id}/characters/{character_id}/target/confirm", response_model=RecastDTO)
    def confirm_target(recast_id: str, character_id: str, body: RevisionInputDTO):
        return read(recast.confirm_target, recast_id, character_id, **body.model_dump())

    @router.post(
        "/{recast_id}/characters/{character_id}/reference/approve", response_model=RecastDTO
    )
    def approve_reference(recast_id: str, character_id: str, body: RevisionInputDTO):
        return read(recast.approve_reference, recast_id, character_id, **body.model_dump())

    @router.post(
        "/{recast_id}/characters/{character_id}/reference/reject", response_model=RecastDTO
    )
    def reject_reference(recast_id: str, character_id: str, body: RevisionInputDTO):
        return read(recast.reject_reference, recast_id, character_id, **body.model_dump())

    @router.delete("/{recast_id}/characters/{character_id}/target", response_model=RecastDTO)
    def clear_target(recast_id: str, character_id: str, expected_revision: int):
        return read(recast.clear_target, recast_id, character_id, expected_revision)

    voice_path = "/{recast_id}/characters/{character_id}/voice"

    @router.put(voice_path, response_model=RecastDTO)
    def set_voice(recast_id: str, character_id: str, body: VoiceSamplesInput):
        """Human only: the character's voice recordings, confirmed at once (journaled). Building asks separately."""
        return read(recast_voice.set_voice, recast_id, character_id, **body.model_dump())

    @router.delete(voice_path, response_model=RecastDTO)
    def clear_voice(recast_id: str, character_id: str, expected_revision: int = Query(ge=0)):
        """Human only: remove the recordings, or reject proposed ones (journaled)."""
        return read(
            recast_voice.clear_voice, recast_id, character_id, expected_revision=expected_revision
        )

    @router.post(voice_path + "/confirm", response_model=RecastDTO)
    def confirm_voice(recast_id: str, character_id: str, body: RevisionInputDTO):
        """Human only: confirm proposed recordings (journaled)."""
        return read(recast_voice.confirm_voice, recast_id, character_id, **body.model_dump())

    @router.post(voice_path + "/build", response_model=RecastDTO)
    def build_voice(recast_id: str, character_id: str, body: BuildVoiceInput):
        """Human only: start the voice build (GPU hours; the client asks first). Never automatic."""
        folder = expressive_library()
        return read(
            recast_voice.build_voice,
            recast_id,
            character_id,
            expressive_library=folder,
            **body.model_dump(),
        )

    @router.post(voice_path + "/retry", response_model=RecastDTO)
    def retry_voice(recast_id: str, character_id: str, body: RevisionInputDTO):
        """Human only: resume a stopped build, start the next attempt of a failed one, or audition again."""
        folder = expressive_library()
        return read(
            recast_voice.retry_voice,
            recast_id,
            character_id,
            expressive_library=folder,
            **body.model_dump(),
        )

    @router.post(voice_path + "/approve", response_model=RecastDTO)
    def approve_voice(recast_id: str, character_id: str, body: RevisionInputDTO):
        """Human only: approve the audition; the reel records the generated voice's provenance."""
        return read(recast_voice.approve_voice, recast_id, character_id, **body.model_dump())

    @router.post(voice_path + "/reject", response_model=RecastDTO)
    def reject_voice(recast_id: str, character_id: str, body: RevisionInputDTO):
        """Human only: reject the audition."""
        return read(recast_voice.reject_voice, recast_id, character_id, **body.model_dump())

    @router.patch("/{recast_id}/shots/{shot_id}", response_model=RecastDTO)
    def patch_shot(recast_id: str, shot_id: str, body: ShotPatchInput):
        return read(recast.patch_shot, recast_id, shot_id, **body.model_dump(exclude_unset=True))

    @router.post("/{recast_id}/plan", response_model=RecastDTO)
    def plan(recast_id: str, body: PlanInput):
        return read(recast.plan_recast, recast_id, **body.model_dump())

    @router.post("/{recast_id}/run", response_model=RecastDTO)
    def run(recast_id: str, body: RunInput):
        return read(recast.start_run, recast_id, **body.model_dump())

    @router.post("/{recast_id}/analysis/retry", response_model=RecastDTO)
    def retry_analysis(recast_id: str, body: RevisionInputDTO):
        return read(recast.retry_analysis, recast_id, **body.model_dump())

    @router.post("/{recast_id}/run/pause", response_model=RecastDTO)
    def pause(recast_id: str, body: RevisionInputDTO):
        return read(recast.pause_run, recast_id, **body.model_dump())

    @router.post("/{recast_id}/run/cancel", response_model=RecastDTO)
    def cancel(recast_id: str, body: RevisionInputDTO):
        return read(recast.cancel_run, recast_id, **body.model_dump())

    @router.post("/{recast_id}/shots/{shot_id}/choice", response_model=RecastDTO)
    def choose(recast_id: str, shot_id: str, body: ChoiceInput):
        """Human only: the shot version the conform uses, or "original"."""
        return read(recast.choose_version, recast_id, shot_id, **body.model_dump())

    @router.post("/{recast_id}/shots/{shot_id}/head-pass", response_model=RecastDTO)
    def head_pass(recast_id: str, shot_id: str, body: HeadPassInput):
        pending = None
        with workspace.db.transaction() as session:
            try:
                result = recast.start_head_pass(
                    session, workspace.library, recast_id, shot_id, **body.model_dump()
                )
            except TargetReportRefused as exc:
                # Retain new reports even if another frozen target refuses the pass.
                pending = exc
        if pending is not None:
            raise pending
        return sign(result)

    @router.post("/{recast_id}/shots/{shot_id}/retake", response_model=RecastDTO)
    def retake(recast_id: str, shot_id: str, body: RetakeInput):
        return read(recast.retake_shot, recast_id, shot_id, **body.model_dump())

    @router.get("/{recast_id}/shots/{shot_id}/reveal", response_model=ShotRevealDTO)
    def reveal(recast_id: str, shot_id: str):
        return read(recast.reveal_shot, recast_id, shot_id)

    @router.post("/{recast_id}/dialogue", response_model=RecastDTO)
    def start_dialogue(recast_id: str, body: RevisionInputDTO):
        """Human only: start the film's shared dialogue analysis (asks first; never automatic)."""
        return read(recast_lines.start_dialogue_analysis, recast_id, **body.model_dump())

    @router.post("/{recast_id}/dialogue/retry", response_model=RecastDTO)
    def retry_dialogue(recast_id: str, body: RevisionInputDTO):
        """Human only: a new attempt after the dialogue analysis failed or was cancelled."""
        return read(recast_lines.retry_dialogue_analysis, recast_id, **body.model_dump())

    @router.get("/{recast_id}/lines", response_model=LinesDTO)
    def lines(recast_id: str):
        return read(recast_lines.lines_state, recast_id)

    @router.post("/{recast_id}/lines/confirm", response_model=RecastDTO)
    def confirm_lines(recast_id: str, body: BulkConfirmInput):
        """Human only: confirm a character's proposed lines in one journaled edit."""
        return read(recast_lines.confirm_lines, recast_id, **body.model_dump())

    @router.post("/{recast_id}/lines/{segment_key}/confirm", response_model=RecastDTO)
    def confirm_line(recast_id: str, segment_key: str, body: LineDecisionInput):
        """Human only: this line is the character's."""
        return read(recast_lines.confirm_line, recast_id, segment_key, **body.model_dump())

    @router.post("/{recast_id}/lines/{segment_key}/reject", response_model=RecastDTO)
    def reject_line(recast_id: str, segment_key: str, body: LineDecisionInput):
        """Human only: this line is not the character's."""
        return read(recast_lines.reject_line, recast_id, segment_key, **body.model_dump())

    @router.delete("/{recast_id}/lines/{segment_key}", response_model=RecastDTO)
    def clear_line(recast_id: str, segment_key: str, expected_revision: int = Query(ge=0)):
        """Human only: remove the decision on a line; the computed proposal returns."""
        return read(
            recast_lines.clear_line, recast_id, segment_key, expected_revision=expected_revision
        )

    @router.put("/{recast_id}/lines/{segment_key}/edges", response_model=RecastDTO)
    def edge_line(recast_id: str, segment_key: str, body: LineEdgesInput):
        """Human only: the line's edges in 48 kHz samples (confirms it); both null removes them."""
        return read(recast_lines.edge_line, recast_id, segment_key, **body.model_dump())

    @router.post("/{recast_id}/conform", response_model=OperationDTO, status_code=201)
    def conform(recast_id: str, body: ConformInput, response: Response):
        """Human only: the film with chosen versions (or labelled unreviewed drafts) pasted in."""
        result = read(recast.start_conform, recast_id, **body.model_dump())
        response.status_code = 201 if result.pop("_created", True) else 200
        return result

    return router, public
