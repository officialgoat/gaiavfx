from __future__ import annotations

import secrets
import threading
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from fastapi.staticfiles import StaticFiles
from filelock import FileLock, Timeout
from pydantic import BaseModel, Field
from starlette.middleware.trustedhost import TrustedHostMiddleware

from gaiavfx.application.harness.driver import AgentRunner
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.model import AssistantModel
from gaiavfx.application.operations import OperationRunner
from gaiavfx.application.workspace import Workspace
from gaiavfx.infrastructure.assistant_gateway import AssistantGateway
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.modules.library.application import LibraryError, LibraryService
from gaiavfx.shared.errors import ApplicationError

from .agent import agent_router
from .direction import direction_router
from .export import export_router
from .library import library_router
from .production import production_router
from .recast import recast_router
from .sets import sets_router
from .settings import settings_router


class LibraryDTO(BaseModel):
    id: str
    name: str
    created_at: str


class ArchivedLibraryDTO(BaseModel):
    path: str
    revision: str
    archived_at: str


class LibraryState(BaseModel):
    library: LibraryDTO | None
    archived: ArchivedLibraryDTO | None


class CreateLibrary(BaseModel):
    name: str = Field(min_length=1, max_length=120)


class HealthDTO(BaseModel):
    status: str
    version: str


class SessionDTO(BaseModel):
    token: str


def create_app(
    root: Path,
    migrations: Path,
    token: str,
    *,
    browser_origins: tuple[str, ...] = (),
    frontend: Path | None = None,
    acquire_lock: bool = True,
    enable_runner: bool = True,
) -> FastAPI:
    library = LibraryService(root, migrations)
    workspace = Workspace(root)
    gateway = AssistantGateway(workspace.credentials)
    operations = OperationRunner(
        workspace.db,
        workspace.comfy,
        workspace.library,
        lambda: workspace.settings()["comfy_url"],
        engines_root=lambda: workspace.settings()["engines_root"],
    )
    operations_thread: threading.Thread | None = None
    agent = AgentRunner(
        workspace.db,
        Harness(
            workspace.db,
            AgentStore(),
            AssistantModel(gateway, workspace.comfy),
            workspace.settings,
            library=workspace.library,
        ),
        gpu_wanted=operations.jobs_waiting_for_gpu,
    )
    agent_thread: threading.Thread | None = None

    def start_agent():
        nonlocal agent_thread
        if agent_thread is None or not agent_thread.is_alive():
            agent.stop.clear()
            agent_thread = threading.Thread(
                target=agent.run_forever, name="gaiavfx-agent", daemon=True
            )
            app.state.agent_thread = agent_thread
            agent_thread.start()

    def start_operations():
        nonlocal operations_thread
        if operations_thread is None or not operations_thread.is_alive():
            operations.stop.clear()
            operations_thread = threading.Thread(
                target=operations.run_forever, name="gaiavfx-operations", daemon=True
            )
            app.state.operations_thread = operations_thread
            operations_thread.start()

    @asynccontextmanager
    async def lifespan(_app):
        lock = None
        if acquire_lock:
            root.mkdir(parents=True, exist_ok=True)
            lock = FileLock(str(root / ".service.lock"))
            try:
                lock.acquire(timeout=0)
            except Timeout as exc:
                raise RuntimeError(
                    "This library is already open in another GAIAVFX process."
                ) from exc
        try:
            app.state.archived = library.archive_foreign()
            workspace.db.migrate(migrations)
            current_library = library.get()
            workspace.telemetry.library_id = current_library.id if current_library else None
            workspace.telemetry.start()
            if enable_runner and current_library is not None:
                start_operations()
                start_agent()
            yield
        finally:
            agent.stop.set()
            operations.stop.set()
            if agent_thread is not None:
                agent_thread.join(timeout=5)
            if operations_thread is not None:
                operations_thread.join(timeout=5)
            gateway.stop()
            workspace.telemetry.close()
            workspace.credentials.close()
            workspace.db.close()
            if lock:
                lock.release()

    app = FastAPI(
        title="GAIAVFX local API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])
    app.state.workspace = workspace
    app.state.archived = None
    app.state.operations = operations
    app.state.operations_thread = None
    app.state.agent = agent
    app.state.agent_thread = None
    from .telemetry import TraceMiddleware

    app.add_middleware(TraceMiddleware, workspace=workspace, token=token)

    @app.exception_handler(ApplicationError)
    async def application_error(_request, error):
        return JSONResponse({"detail": str(error)}, status_code=error.status)

    @app.middleware("http")
    async def local_origin(request: Request, call_next):
        origin = request.headers.get("origin")
        same_origin = str(request.base_url).rstrip("/")
        if origin and origin not in (*browser_origins, same_origin):
            return JSONResponse({"detail": "Origin not allowed."}, status_code=403)
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # A route that serves content-addressed bytes (a set's splat) marks them immutable; keep that.
        if "immutable" not in response.headers.get("Cache-Control", ""):
            response.headers["Cache-Control"] = (
                "no-store" if request.url.path.startswith("/api/") else "no-cache"
            )
        return response

    bearer = HTTPBearer(auto_error=False)
    authentication = Depends(bearer)

    def authenticate(credentials: HTTPAuthorizationCredentials | None = authentication):
        if credentials is None or not secrets.compare_digest(credentials.credentials, token):
            raise HTTPException(
                status_code=401, detail="The local session expired. Reopen GAIAVFX."
            )

    @app.post("/api/v1/session", response_model=SessionDTO)
    def session(request: Request):
        # Explicit browser-development bootstrap. Packaged Electron receives its session over IPC.
        if (
            request.headers.get("origin") not in browser_origins
            or request.headers.get("x-gaiavfx-bootstrap") != "1"
        ):
            raise HTTPException(
                status_code=403, detail="Browser bootstrap is not enabled for this origin."
            )
        return SessionDTO(token=token)

    @app.get("/api/v1/health", response_model=HealthDTO, dependencies=[Depends(authenticate)])
    def health():
        return HealthDTO(status="ready", version="0.1.0")

    @app.get("/api/v1/library", response_model=LibraryState, dependencies=[Depends(authenticate)])
    def get_library():
        try:
            result = library.get()
            return LibraryState(
                library=LibraryDTO(**asdict(result)) if result else None,
                archived=ArchivedLibraryDTO(**asdict(app.state.archived))
                if app.state.archived
                else None,
            )
        except LibraryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    @app.post(
        "/api/v1/library",
        response_model=LibraryDTO,
        status_code=201,
        dependencies=[Depends(authenticate)],
    )
    def create_library(body: CreateLibrary):
        try:
            result = library.create(body.name)
            workspace.telemetry.library_id = result.id
            if enable_runner:
                start_operations()
                start_agent()
            return LibraryDTO(**asdict(result))
        except LibraryError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    routes, media_routes = library_router(workspace, authenticate, token)
    app.include_router(routes)
    app.include_router(media_routes)
    app.include_router(settings_router(workspace, gateway, authenticate))
    app.include_router(direction_router(workspace, authenticate))
    app.include_router(production_router(workspace, authenticate))
    app.include_router(export_router(workspace, authenticate))
    recast_routes, recast_media = recast_router(workspace, authenticate, token)
    app.include_router(recast_routes)
    app.include_router(recast_media)
    set_routes, set_media = sets_router(workspace, authenticate, token)
    app.include_router(set_routes)
    app.include_router(set_media)
    app.include_router(agent_router(agent.harness, authenticate))
    if frontend and frontend.is_dir():
        app.mount("/", StaticFiles(directory=frontend, html=True), name="frontend")
    return app
