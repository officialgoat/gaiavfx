"""HTTP boundary for sets (ADR 0018): the Library › Sets list, the workbench's set state and map, the human commands,
the bundle import, and splats and keyframe stills served by signed links.

Import, accept and discard are human-only routes the agent harness never calls (§5).
"""

from __future__ import annotations

import hashlib
import hmac
import shutil
import tempfile
import time
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, File, Query, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel, ConfigDict, Field

from gaiavfx.application import set_build, set_bundles, sets
from gaiavfx.shared.errors import ApplicationError

IssueKind = Literal[
    "shape",
    "space",
    "duplicate",
    "stairs",
    "obstacle",
    "pocket",
    "floaters",
    "hole",
    "split",
    "doubled",
    "two-way",
    "other",
]
Author = Literal["human", "agent", "judge"]


class SetInputDTO(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SetMediaDTO(BaseModel):
    thumbnail: str
    original: str


class SetVersionSummaryDTO(BaseModel):
    id: str
    ordinal: int
    state: Literal["proposed", "accepted", "discarded"]
    origin: Literal["import", "seed", "expand", "repair", "human"]
    # the version a build started from, and the build (a set_build operation) that wrote it
    parent_id: str | None = None
    operation_id: str | None = None
    note: str
    created_at: str
    decided_at: str | None


class SetSummaryDTO(BaseModel):
    id: str
    name: str
    place_text: str
    revision: int
    updated_at: str
    current_version_id: str | None
    shown_version: SetVersionSummaryDTO
    versions: int
    keyframes: int
    open_issues: int
    poster: SetMediaDTO | None
    has_splat: bool


class SetKeyframeDTO(BaseModel):
    id: str
    label: str
    key: str
    kind: Literal["erp", "pinhole"]
    role: Literal["observed", "generated", "trajectory", "appearance-only"]
    viewmat: list[list[float]]
    intrinsics: list[list[float]] | None
    width: int
    height: int
    heading: float | None
    image: SetMediaDTO
    # the label an excluded keyframe had in the version it was excluded from
    was: str | None = None
    # why filling set this probe still aside, in Python's words, and whether Keep this still can bring it back
    dropped: str | None = None
    can_keep: bool = False


class SetSplatDTO(BaseModel):
    artifact_id: str
    url: str
    size: int | None
    gaussians: int | None
    sh_degree: int | None


class SetVersionDTO(SetVersionSummaryDTO):
    map_meta: dict
    metrics: dict
    has_map: bool
    splat: SetSplatDTO | None
    keyframes: list[SetKeyframeDTO]


class SetIssueDTO(BaseModel):
    id: str
    found_in_id: str
    resolved_in_id: str | None
    kind: IssueKind
    author: Author
    status: Literal["open", "queued", "fixed", "dismissed", "failed"]
    cells: list[str]
    cameras: list[str]
    region: dict | None
    why: str
    note: str
    created_at: str
    updated_at: str


class SetFixDTO(BaseModel):
    id: str
    issue_id: str | None
    action: Literal["reshoot", "extend", "retract", "keep"]
    author: Author
    target: dict
    shows: str
    state: Literal["queued", "running", "done", "failed", "cancelled"]
    operation_id: str | None
    created_at: str


class SetHideRegionDTO(BaseModel):
    id: str
    version_id: str
    shape: Literal["box", "sphere"]
    params: dict
    label: str
    created_at: str


class SetViewAngleDTO(BaseModel):
    """The Direction angle a saved view is the camera of (ADR 0019 §2)."""

    id: str
    name: str
    place_name: str


class SetPlaceAngleDTO(BaseModel):
    id: str
    name: str


class SetPlaceDTO(BaseModel):
    """A Direction place built as the set, with the angles its views can be aimed at (ADR 0019 §2)."""

    id: str
    name: str
    angles: list[SetPlaceAngleDTO]


class SetViewDTO(BaseModel):
    still: SetMediaDTO | None = None
    angle: SetViewAngleDTO | None = None
    id: str
    version_id: str
    name: str
    viewmat: list[list[float]]
    fov: float
    width: int
    height: int
    hide_region_ids: list[str]
    author: Literal["human", "agent"]
    asset_id: str | None
    created_at: str


class SetBuildDTO(BaseModel):
    """The set's newest build (ADR 0018 §4): `stage` prepare (the atlas and map), splat (the fit) or, with
    extensions, one of a fill round's stages (ADR 0020 §4), which `filling` says in words."""

    operation_id: str
    state: str
    base_version_id: str | None
    stage: str | None
    filling: str | None = None
    # the GPU stage waits for the card (another job holds it); no progress yet
    waiting: bool
    cancelling: bool
    progress: float
    version_id: str | None
    error: str | None
    reason: str | None = None  # why a build ended without a version, in Python's words
    fix_ids: list[str]


class SetRunDTO(BaseModel):
    """What Run queued fixes would do on the shown version: the fixes it runs and Python's words for it."""

    fix_ids: list[str]
    words: str


class SetStateDTO(BaseModel):
    id: str
    name: str
    place_text: str
    seed: int | None
    revision: int
    provenance: dict
    current_version_id: str | None
    versions: list[SetVersionSummaryDTO]
    version: SetVersionDTO
    issues: list[SetIssueDTO]
    fixes: list[SetFixDTO]
    hide_regions: list[SetHideRegionDTO]
    views: list[SetViewDTO]
    build: SetBuildDTO | None = None
    run: SetRunDTO


class SetMapDTO(BaseModel):
    """The eye-level map: `plane` int8 (0 unknown, 1 free, 2 occupied) indexed [x, z], row-major, base64;
    `frontier` the same grid bit-packed (numpy packbits order), base64."""

    version_id: str
    shape: list[int]
    plane: str
    frontier: str
    meta: dict


class SetImportInput(SetInputDTO):
    folder: str = Field(min_length=1, max_length=1000)


class SetRegionDTO(SetInputDTO):
    """Where an issue is: a world point or box, a fly-through time, the camera it was seen from, and, where no
    geometry answered (a hole), the spot on that camera's picture (`screen`, 0..1 across and down)."""

    point: list[float] | None = Field(None, min_length=3, max_length=3)
    box: dict | None = None
    time: float | None = None
    viewmat: list[list[float]] | None = None
    fov: float | None = Field(None, ge=10, le=140)
    screen: list[float] | None = Field(None, min_length=2, max_length=2)


class SetIssueInput(SetInputDTO):
    version_id: str
    kind: IssueKind
    why: str = Field(min_length=1, max_length=2000)
    cells: list[str] = Field(default_factory=list, max_length=40)
    cameras: list[str] = Field(default_factory=list, max_length=20)
    region: SetRegionDTO | None = None
    note: str = Field("", max_length=2000)


class SetIssueStatusInput(SetInputDTO):
    status: Literal["open", "dismissed", "fixed"]
    expected_revision: int = Field(ge=0)
    version_id: str | None = None


class SetFixInput(SetInputDTO):
    version_id: str
    action: Literal["reshoot", "extend", "retract", "keep"]
    target: dict
    shows: str = Field("", max_length=600)
    issue_id: str | None = None


class SetHideRegionInput(SetInputDTO):
    version_id: str
    shape: Literal["box", "sphere"]
    params: dict
    label: str = Field("", max_length=80)


class SetRemovedInput(SetInputDTO):
    removed: bool
    version_id: str | None = None


class SetViewInput(SetInputDTO):
    version_id: str
    name: str = Field("", max_length=80)
    viewmat: list[list[float]]
    fov: float = Field(ge=10, le=140)
    width: int = Field(ge=16, le=8192)
    height: int = Field(ge=16, le=8192)


class SetAimInput(SetInputDTO):
    version_id: str
    angle_id: str | None = Field(min_length=1, max_length=36)


class SetAcceptInput(SetInputDTO):
    expected_current_id: str | None
    # the issues this version's fixes addressed that the human saw fixed
    looks_fixed: list[str] = Field(default_factory=list, max_length=100)


class SetBuildInput(SetInputDTO):
    version_id: str = Field(min_length=1, max_length=36)
    # queue the newest build's failed fixes again first (Retry build)
    retry: bool = False


def sets_router(workspace, authenticate, token):
    router = APIRouter(prefix="/api/sets", dependencies=[Depends(authenticate)])
    public = APIRouter()

    def stable_expiry():
        # Stable within the hour, so repeated reads agree and caches hold; valid for at least 6 hours.
        return (int(time.time()) // 3600 + 7) * 3600

    def media(asset_id):
        expires = stable_expiry()

        def url(rendition):
            # The Library media route's own signature (`api/library.py`).
            message = f"{asset_id}:{rendition}:{expires}".encode()
            signature = hmac.new(token.encode(), message, hashlib.sha256).hexdigest()
            return f"/api/v1/media/{asset_id}/{rendition}?expires={expires}&signature={signature}"

        return {"thumbnail": url("thumbnail"), "original": url("original")}

    def splat_signature(set_id, artifact_id, expires):
        message = f"set-splat:{set_id}:{artifact_id}:{expires}".encode()
        return hmac.new(token.encode(), message, hashlib.sha256).hexdigest()

    def signed(state):
        for view in state["views"]:
            view["still"] = media(view["asset_id"]) if view.get("asset_id") else None
        version = state["version"]
        for keyframe in version["keyframes"]:
            keyframe["image"] = media(keyframe.pop("image_asset_id"))
        if version["splat"]:
            artifact_id, expires = version["splat"]["artifact_id"], stable_expiry()
            version["splat"]["url"] = (
                f"/api/sets/{state['id']}/splats/{artifact_id}?expires={expires}"
                f"&signature={splat_signature(state['id'], artifact_id, expires)}"
            )
        return state

    def command(call, set_id, *args, **kwargs):
        with workspace.db.transaction() as session:
            return signed(call(session, workspace.library, set_id, *args, **kwargs))

    @public.get("/api/sets/{set_id}/splats/{artifact_id}", response_class=FileResponse)
    def splat(set_id: str, artifact_id: str, expires: int, sig: str = Query(alias="signature")):
        expected = splat_signature(set_id, artifact_id, expires)
        if expires < time.time() or not hmac.compare_digest(sig, expected):
            raise ApplicationError("This splat link expired. Reopen the stage.", 401)
        with workspace.db.transaction() as session:
            path = sets.splat_path(session, workspace.library, set_id, artifact_id)
        # Content-addressed, so it never changes; the middleware keeps an immutable cache header.
        return FileResponse(
            path,
            media_type="application/octet-stream",
            headers={"Cache-Control": "private, max-age=86400, immutable"},
        )

    @router.get("", response_model=list[SetSummaryDTO])
    def list_sets():
        with workspace.db.transaction() as session:
            rows = sets.list_sets(session, workspace.library)
        for row in rows:
            asset_id = row.pop("poster_asset_id")
            row["poster"] = media(asset_id) if asset_id else None
        return rows

    @router.post("/import", response_model=SetStateDTO, status_code=201)
    def import_set(body: SetImportInput):
        set_id = set_bundles.import_bundle(workspace.db, workspace.library, Path(body.folder))
        return command(sets.set_state, set_id)

    @router.get("/{set_id}", response_model=SetStateDTO)
    def get_set(set_id: str, version_id: str | None = None):
        return command(sets.set_state, set_id, version_id)

    @router.get("/{set_id}/versions/{version_id}/map", response_model=SetMapDTO)
    def get_map(set_id: str, version_id: str):
        with workspace.db.transaction() as session:
            return sets.version_map(session, workspace.library, set_id, version_id)

    @router.post("/{set_id}/issues", response_model=SetStateDTO)
    def mark_issue(set_id: str, body: SetIssueInput):
        data = body.model_dump()
        data["region"] = body.region.model_dump(exclude_none=True) if body.region else None
        return command(sets.mark_issue, set_id, **data)

    @router.patch("/{set_id}/issues/{issue_id}", response_model=SetStateDTO)
    def issue_status(set_id: str, issue_id: str, body: SetIssueStatusInput):
        return command(sets.set_issue_status, set_id, issue_id, **body.model_dump())

    @router.post("/{set_id}/fixes", response_model=SetStateDTO)
    def queue_fix(set_id: str, body: SetFixInput):
        return command(sets.queue_fix, set_id, **body.model_dump())

    @router.post("/{set_id}/fill-more", response_model=SetStateDTO)
    def fill_more(set_id: str, body: SetBuildInput):
        """Queue an extension that fills the stage's unseen space all round (ADR 0020 §4)."""
        return command(sets.fill_more, set_id, version_id=body.version_id)

    @router.delete("/{set_id}/fixes/{fix_id}", response_model=SetStateDTO)
    def cancel_fix(set_id: str, fix_id: str, version_id: str | None = None):
        return command(sets.cancel_fix, set_id, fix_id, version_id=version_id)

    @router.post("/{set_id}/fixes/{fix_id}/retry", response_model=SetStateDTO)
    def retry_fix(set_id: str, fix_id: str, body: SetBuildInput):
        """Human-only: queue a fix again after its build stopped."""
        return command(sets.retry_fix, set_id, fix_id, version_id=body.version_id)

    @router.post("/{set_id}/build", response_model=SetStateDTO)
    def run_fixes(set_id: str, body: SetBuildInput):
        """Human-only (ADR 0018 §5): run the queued fixes on this version; the result is a proposed version."""
        return command(set_build.run_queued_fixes, set_id, **body.model_dump())

    @router.post("/{set_id}/hide-regions", response_model=SetStateDTO)
    def hide_region(set_id: str, body: SetHideRegionInput):
        return command(sets.hide_region, set_id, **body.model_dump())

    @router.patch("/{set_id}/hide-regions/{region_id}", response_model=SetStateDTO)
    def hide_region_removed(set_id: str, region_id: str, body: SetRemovedInput):
        return command(sets.set_hide_region_removed, set_id, region_id, **body.model_dump())

    @router.post("/{set_id}/views", response_model=SetStateDTO)
    def save_view(set_id: str, body: SetViewInput):
        return command(sets.save_view, set_id, **body.model_dump())

    # Named, so the form body's schema has a stable name without the path's double underscores.
    @router.post(
        "/{set_id}/views/{view_id}/still", response_model=SetStateDTO, operation_id="set_view_still"
    )
    def view_still(set_id: str, view_id: str, file: Annotated[UploadFile, File()]):
        """The picture of a saved view as the workbench drew it: a Library image with `set_view` provenance."""
        with tempfile.TemporaryDirectory() as scratch:
            source = Path(scratch) / "view.png"
            with source.open("wb") as out:
                shutil.copyfileobj(file.file, out)
            prepared = sets.prepare_view_still(workspace.library, source)
            with workspace.db.transaction() as session:
                return signed(
                    sets.attach_view_still(session, workspace.library, set_id, view_id, prepared)
                )

    @router.get("/{set_id}/places", response_model=list[SetPlaceDTO])
    def places_built_as(set_id: str):
        with workspace.db.transaction() as session:
            return sets.places_built_as(session, set_id)

    @router.put("/{set_id}/views/{view_id}/angle", response_model=SetStateDTO)
    def aim_view(set_id: str, view_id: str, body: SetAimInput):
        """Make the view the camera of a place's angle, or of none (ADR 0019 §2)."""
        return command(sets.aim_view, set_id, view_id, **body.model_dump())

    @router.patch("/{set_id}/views/{view_id}", response_model=SetStateDTO)
    def view_removed(set_id: str, view_id: str, body: SetRemovedInput):
        return command(sets.set_view_removed, set_id, view_id, **body.model_dump())

    @router.post("/{set_id}/versions/{version_id}/accept", response_model=SetStateDTO)
    def accept(set_id: str, version_id: str, body: SetAcceptInput):
        return command(sets.accept_version, set_id, version_id, **body.model_dump())

    @router.post("/{set_id}/versions/{version_id}/discard", response_model=SetStateDTO)
    def discard(set_id: str, version_id: str):
        return command(sets.discard_version, set_id, version_id)

    return router, public
