"""HTTP boundary for Panoramas: a place's panorama attempts, starting one from words or a picture, two more, and the
person's choice. Choosing is a human-only route the agent harness never calls (`review_approvals.choose_panorama`)."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from gaiavfx.api.signed_links import SignedLinks
from gaiavfx.application import panoramas, review_approvals, set_panorama
from gaiavfx.modules.panoramas.ask import MAX_LANDMARKS, MAX_PLACE, MAX_WHAT, PicturedPlace


class LandmarkInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    what: str = Field(min_length=1, max_length=MAX_WHAT)
    heading: int = Field(ge=0, le=359)


class PanoramaInput(BaseModel):
    """Words about the place (with landmarks by heading and its light) or a picture to grow, never both."""

    model_config = ConfigDict(extra="forbid")
    mode: Literal["description", "picture"]
    place_id: str | None = None
    place: str = Field(default="", max_length=MAX_PLACE)
    landmarks: list[LandmarkInput] = Field(default_factory=list, max_length=MAX_LANDMARKS)
    light: str = Field(default="", max_length=2 * MAX_WHAT)
    picture_id: str | None = None
    hfov: float | None = Field(default=None, ge=30, le=120)


class PanoramaPreviewDTO(BaseModel):
    what: str
    prompt: str | None
    estimate: str
    renders: int
    licence: str


class PanoramaMediaDTO(BaseModel):
    thumbnail: str
    original: str


class SeamCheckDTO(BaseModel):
    outside_identical: bool
    green_in_band: float
    clean: bool
    band_px: int
    feather_px: int
    edge_step_before: float
    edge_step_after: float


class PlateCheckDTO(BaseModel):
    plate_psnr: float
    green: float
    plate_pixels: int
    heading: float
    hfov: float
    vfov: float


class PanoramaCandidateDTO(BaseModel):
    letter: Literal["A", "B"]
    candidate_id: str
    asset_id: str
    image: PanoramaMediaDTO
    seam: SeamCheckDTO | None
    plate: PlateCheckDTO | None
    chosen: bool


class PanoramaBuildDTO(BaseModel):
    """The stage built from the chosen panorama, in `set_seed.seed_state`'s words."""

    state: Literal["building", "refused", "failed", "done"]
    name: str
    label: str
    message: str
    operation_id: str
    set_id: str | None
    waiting: bool
    can_retry: bool


class PanoramaStageInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, min_length=1, max_length=120)
    fill_unseen: bool = True
    use_for_place: bool = False


class PanoramaStagePreviewDTO(BaseModel):
    name: str
    what: str
    estimate: str


class PanoramaDTO(BaseModel):
    id: str
    mode: Literal["description", "picture"]
    place_id: str | None
    place: str
    prompt: str | None
    attempt: int
    state: Literal["building", "ready", "chosen", "stopped"]
    stage: str | None
    message: str
    candidates: list[PanoramaCandidateDTO]
    chosen: str | None
    build: PanoramaBuildDTO | None
    created_at: str


def _request(body: PanoramaInput) -> dict:
    if body.mode == "description":
        return {
            "described_place": panoramas.described(
                body.place, [item.model_dump() for item in body.landmarks], body.light
            )
        }
    return {"pictured": PicturedPlace(body.picture_id or "", body.hfov, body.place)}


def panoramas_router(workspace, authenticate, token):
    router = APIRouter(prefix="/api/panoramas", dependencies=[Depends(authenticate)])
    links = SignedLinks(token)

    def signed(state: dict) -> dict:
        for member in state["candidates"]:
            member["image"] = {
                rendition: links.media(member["asset_id"], rendition)
                for rendition in ("thumbnail", "original")
            }
        return state

    def created(state: dict, response: Response, fresh: bool) -> dict:
        response.status_code = 201 if fresh else 200
        return state

    @router.get("", response_model=list[PanoramaDTO])
    def list_panoramas(place_id: str | None = None):
        with workspace.db.transaction() as session:
            found = panoramas.panoramas_for(session, workspace.library, place_id)
        return [signed(state) for state in found]

    @router.get("/{operation_id}", response_model=PanoramaDTO)
    def read_panorama(operation_id: str):
        with workspace.db.transaction() as session:
            return signed(panoramas.read_panorama(session, workspace.library, operation_id))

    @router.post("/preview", response_model=PanoramaPreviewDTO)
    def preview(body: PanoramaInput):
        with workspace.db.transaction() as session:
            return panoramas.preview(session, workspace.library, **_request(body))

    @router.post("", response_model=PanoramaDTO, status_code=201)
    def start(body: PanoramaInput, response: Response):
        with workspace.db.transaction() as session:
            operation = panoramas.start_panorama(
                session, workspace.library, place_id=body.place_id, **_request(body)
            )
            state = panoramas.panorama_state(session, workspace.library, operation)
        return created(signed(state), response, operation["_created"])

    @router.post("/{operation_id}/more", response_model=PanoramaDTO, status_code=201)
    def more(operation_id: str, response: Response):
        with workspace.db.transaction() as session:
            operation = panoramas.make_more(session, workspace.library, operation_id)
            state = panoramas.panorama_state(session, workspace.library, operation)
        return created(signed(state), response, operation["_created"])

    @router.post("/candidates/{candidate_id}/choose", response_model=PanoramaDTO)
    def choose(candidate_id: str):
        with workspace.db.transaction() as session:
            return signed(
                review_approvals.choose_panorama(session, workspace.library, candidate_id)
            )

    @router.get("/candidates/{candidate_id}/stage-preview", response_model=PanoramaStagePreviewDTO)
    def stage_preview(candidate_id: str, fill_unseen: bool = True):
        with workspace.db.transaction() as session:
            return set_panorama.stage_preview(
                session, workspace.library, candidate_id, fill_unseen=fill_unseen
            )

    @router.post("/candidates/{candidate_id}/stage", response_model=PanoramaDTO, status_code=201)
    def build_stage(candidate_id: str, body: PanoramaStageInput, response: Response):
        with workspace.db.transaction() as session:
            build = set_panorama.start_panorama_stage(
                session,
                workspace.library,
                candidate_id=candidate_id,
                name=body.name,
                fill_unseen=body.fill_unseen,
                use_for_place=body.use_for_place,
            )
            state = panoramas.read_panorama(
                session, workspace.library, build["request"]["panorama_operation_id"]
            )
        return created(signed(state), response, build["_created"])

    return router
