"""Human movie export and its readiness projection."""

from fastapi import APIRouter, Depends, Response
from pydantic import BaseModel, ConfigDict, Field

from gaiavfx.api.production import OperationDTO
from gaiavfx.application.export_movie import movie_export_state, start_export


class MovieExportInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    request_key: str = Field(min_length=1, max_length=80)
    allow_stale: bool = False


class MovieExportStatusDTO(BaseModel):
    id: str
    state: str
    progress: float
    asset_id: str | None
    cancel_requested: bool
    error: str | None


class MovieExportSegmentDTO(BaseModel):
    segment_id: str
    ordinal: int
    frames: int
    selection_id: str | None
    asset_id: str | None
    name: str | None
    stale_reasons: list[str]


class MovieExportDTO(BaseModel):
    movie_id: str
    movie_revision: int
    title: str
    segments: list[MovieExportSegmentDTO]
    latest: MovieExportStatusDTO | None


def export_router(workspace, authenticate):
    router = APIRouter(prefix="/api/export", dependencies=[Depends(authenticate)])

    @router.get("/movies/{movie_id}", response_model=MovieExportDTO)
    def readiness(movie_id: str):
        with workspace.db.transaction() as session:
            return movie_export_state(session, workspace.library, movie_id)

    @router.post("/movies/{movie_id}", response_model=OperationDTO, status_code=201)
    def export(movie_id: str, body: MovieExportInput, response: Response):
        with workspace.db.transaction() as session:
            result = start_export(session, workspace.library, movie_id, **body.model_dump())
            response.status_code = 201 if result.pop("_created") else 200
            return result

    return router
