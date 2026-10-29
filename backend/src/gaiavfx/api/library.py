"""Library media, collections and catalog associations."""

import hashlib
import hmac
import time
from pathlib import Path
from typing import Annotated, Literal
from uuid import uuid4

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


class AssetDTO(BaseModel):
    id: str
    name: str
    kind: Literal["image", "video", "audio"]
    sha256: str
    mime: str
    info: dict
    provenance: list[dict]
    review: str
    created_at: str


class AssetPage(BaseModel):
    items: list[AssetDTO]
    total: int
    offset: int
    limit: int


class NamedInput(StrictModel):
    name: str = Field(min_length=1, max_length=120)


class EntityInput(NamedInput):
    kind: Literal["character", "outfit", "environment"]
    description: str = Field(default="", max_length=8000)


class AssociationInput(StrictModel):
    asset_id: str
    role: str = "reference"


class ReviewInput(StrictModel):
    review: Literal["unreviewed", "preferred", "rejected"]


class ImportDTO(BaseModel):
    asset: AssetDTO
    disposition: Literal["created", "reused"]


def library_router(workspace, authenticate, token: str):
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])
    w = workspace

    @router.post("/imports", response_model=ImportDTO, status_code=201)
    async def import_media(
        file: Annotated[UploadFile, File()],
        collection_id: str | None = Form(None),
        entity_id: str | None = Form(None),
        role: str = Form("reference"),
    ):
        target = None
        try:
            if not w.db.path.exists():
                raise ApplicationError("Create a library before importing media.")
            name = Path((file.filename or "media").replace("\\", "/")).name
            target = w.db.root / "staging" / (str(uuid4()) + Path(name).suffix[:12])
            target.parent.mkdir(parents=True, exist_ok=True)
            size = 0
            with target.open("wb") as stream:
                while chunk := await file.read(1024 * 1024):
                    stream.write(chunk)
                    size += len(chunk)
            if not size:
                raise ApplicationError("The selected file is empty.", 422)
            return await run_in_threadpool(
                w.import_media, target, name, collection_id, entity_id, role
            )
        finally:
            if target is not None:
                target.unlink(missing_ok=True)
            await file.close()

    @router.get("/assets", response_model=AssetPage)
    def assets(
        q: str = "",
        kind: str = "",
        collection: str = "",
        offset: int = Query(0, ge=0),
        limit: int = Query(60, ge=1, le=100),
        imported_only: bool = False,
        exclude: str = Query("", max_length=500),
        keep: str = Query("", max_length=500),
    ):
        rules = tuple(rule for rule in exclude.split(",") if rule)
        kept = tuple(step for step in keep.split(",") if step)
        with w.db.transaction() as session:
            return w.library.browse(
                session, q, kind, collection, offset, limit, imported_only, rules, kept
            )

    def signature(asset_id, rendition, expires):
        return hmac.new(
            token.encode(), f"{asset_id}:{rendition}:{expires}".encode(), hashlib.sha256
        ).hexdigest()

    @router.get("/assets/{asset_id}/urls")
    def media_urls(asset_id: str):
        with w.db.transaction() as session:
            asset = w.library.get(session, asset_id)
        expires = int(time.time()) + 6 * 3600
        return {
            rendition: f"/api/v1/media/{asset_id}/{rendition}?expires={expires}&signature={signature(asset_id, rendition, expires)}"
            for rendition in ("original", "preview", "thumbnail")
            if rendition != "thumbnail" or asset["kind"] != "audio"
        }

    @router.get("/assets/{asset_id}", response_model=AssetDTO)
    def asset(asset_id: str):
        with w.db.transaction() as session:
            return w.library.get(session, asset_id)

    @router.patch("/assets/{asset_id}/review", response_model=AssetDTO)
    def review_asset(asset_id: str, body: ReviewInput):
        with w.db.transaction() as session:
            w.library.review(session, asset_id, body.review)
            return w.library.get(session, asset_id)

    @router.get("/collections")
    def collections():
        with w.db.transaction() as session:
            return w.library.collections(session)

    @router.post("/collections", status_code=201)
    def create_collection(body: NamedInput):
        with w.db.transaction() as session:
            return w.library.create_collection(session, body.name)

    @router.post("/collections/{collection_id}/assets")
    def assign_collection(collection_id: str, body: AssociationInput):
        with w.db.transaction() as session:
            w.library.assign_collection(session, collection_id, body.asset_id)
        return {"ok": True}

    @router.get("/entities")
    def entities(kind: str = ""):
        with w.db.transaction() as session:
            return w.catalog.list(session, kind)

    @router.post("/entities", status_code=201)
    def create_entity(body: EntityInput):
        with w.db.transaction() as session:
            return w.catalog.save(session, body.kind, body.name, body.description)

    @router.put("/entities/{entity_id}")
    def update_entity(entity_id: str, body: EntityInput):
        with w.db.transaction() as session:
            return w.catalog.save(session, body.kind, body.name, body.description, entity_id)

    @router.post("/entities/{entity_id}/assets")
    def assign_entity(entity_id: str, body: AssociationInput):
        with w.db.transaction() as session:
            w.library.get(session, body.asset_id)
            w.catalog.assign(session, entity_id, body.asset_id, body.role)
            session.flush()
            return w.catalog.get(session, entity_id)

    media_router = APIRouter()

    @media_router.get("/api/v1/media/{asset_id}/{rendition}")
    def media_file(asset_id: str, rendition: str, expires: int, signature: str):
        expected = hmac.new(
            token.encode(), f"{asset_id}:{rendition}:{expires}".encode(), hashlib.sha256
        ).hexdigest()
        if expires < time.time() or not hmac.compare_digest(signature, expected):
            raise ApplicationError("This media link expired. Reopen the asset.", 401)
        with w.db.transaction() as session:
            path = w.library.path(session, asset_id, rendition)
        return FileResponse(path)

    return router, media_router
