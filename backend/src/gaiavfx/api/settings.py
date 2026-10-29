"""Process connection settings and transport health."""

from typing import Literal

import httpx
from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from gaiavfx.application.engine_jobs import readiness, voice_library
from gaiavfx.shared.assistant import AssistantConnection
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError

from .assistant_connections import assistant_connection_router
from .telemetry import telemetry_router


class ConnectionSettings(StrictModel):
    comfy_url: str = "http://127.0.0.1:8188"
    assistant_executable: str = ""
    assistant_model: str = ""
    assistant_projector: str = ""
    assistant_context: int = Field(default=8192, ge=4096, le=32768)
    assistant_agent_context: int = Field(default=32768, ge=4096, le=131072)
    assistant_connections: list[AssistantConnection] = Field(default_factory=list, max_length=32)
    assistant_agent_connection: str = "local"
    assistant_agent_thinking: Literal["inherit", "off", "low", "medium", "xhigh"] = "inherit"
    agent_autonomy: Literal["off", "prepare", "render"] = "off"
    agent_budget_jobs: int = Field(default=6, ge=0, le=64, strict=True)
    agent_keep_warm_seconds: int = Field(default=180, ge=0, le=1800, strict=True)
    engines_root: str = Field(default="", max_length=1024)
    expressive_library: str = Field(default="", max_length=1024)


class EngineReadinessDTO(BaseModel):
    name: str
    home: str | None
    installed: bool
    worker: bool
    tasks: list[str]
    gpu_tasks: list[str]
    licences: list[str]
    files: dict[str, int] | None
    verified_at: str | None
    probe: str | None
    python: str | None
    cuda_device: str | None
    drift: list[str]
    probed_at: str | None


class EnginesDTO(BaseModel):
    root: str | None
    root_source: Literal["environment", "setting", "none"]
    engines: list[EngineReadinessDTO]


class VoiceLibraryDTO(BaseModel):
    """The expressive recordings folder (ADR 0016 P5): its state and what a voice build would use from it."""

    folder: str
    state: Literal["not_set", "missing", "ready"]
    clips: int
    categories: dict[str, int]
    skipped: int


class ComfyStatusDTO(BaseModel):
    connected: bool
    version: str | None = None
    error: str | None = None


def settings_router(workspace, gateway, authenticate):
    router = APIRouter(prefix="/api/v1", dependencies=[Depends(authenticate)])
    router.include_router(assistant_connection_router(workspace, gateway))
    router.include_router(telemetry_router(workspace))

    @router.get("/settings/connections", response_model=ConnectionSettings)
    def settings():
        return workspace.settings()

    @router.put("/settings/connections", response_model=ConnectionSettings)
    def save_settings(body: ConnectionSettings):
        return workspace.save_settings(body.model_dump())

    @router.get("/settings/engines", response_model=EnginesDTO)
    def engine_readiness():
        return readiness(workspace.settings()["engines_root"])

    @router.get("/settings/voice-library", response_model=VoiceLibraryDTO)
    def expressive_library():
        return voice_library(workspace.settings()["expressive_library"])

    @router.get("/settings/comfy", response_model=ComfyStatusDTO)
    def comfy_status():
        try:
            stats = workspace.comfy.request(
                workspace.settings()["comfy_url"], "GET", "/system_stats"
            )
            return ComfyStatusDTO(
                connected=True, version=stats.get("system", {}).get("comfyui_version")
            )
        except (httpx.HTTPError, ApplicationError, ValueError) as error:
            return ComfyStatusDTO(connected=False, error=str(error))

    return router
