"""Authenticated configuration and metadata-only HTTP tracing."""

import json
import re
import secrets
from collections import deque
from itertools import pairwise
from typing import Literal

from fastapi import APIRouter
from pydantic import BaseModel, Field, SecretStr

from gaiavfx.infrastructure.telemetry.delivery import Delivery
from gaiavfx.shared.errors import ApplicationError


class TelemetrySettings(BaseModel):
    enabled: bool = False
    base_url: str = "https://cloud.langfuse.com"
    environment: str = Field(default="development", pattern=r"^[a-z0-9_-]{1,80}$")
    storage_limit_gib: float = Field(default=10, ge=0.01, le=1024)
    credential_storage: str = Field(default="session", pattern="^(session|portable)$")


class SaveTelemetry(TelemetrySettings):
    public_key: SecretStr | None = None
    secret_key: SecretStr | None = None


class TelemetrySettingsDTO(TelemetrySettings):
    public_key_hint: str | None = None
    has_secret: bool = False
    project_id: str | None = None


class ObservabilityReference(BaseModel):
    session_id: str
    trace_id: str
    trace_url: str | None = None
    session_url: str | None = None
    delivery_state: str


class TelemetryStatus(BaseModel):
    enabled: bool
    connection: str
    observations: dict[str, int]
    media: dict[str, int]
    bytes: int
    capture_error: str | None
    last_delivery: float | None
    errors: list[dict]


class ConnectionResult(BaseModel):
    ok: bool
    project_id: str
    project_name: str


class TraceLink(BaseModel):
    url: str


def telemetry_router(w):
    router = APIRouter(prefix="/settings/telemetry")

    @router.get("", response_model=TelemetrySettingsDTO)
    def settings():
        return w.telemetry.settings()

    @router.put("", response_model=TelemetrySettingsDTO)
    def save(body: SaveTelemetry):
        value = body.model_dump(exclude={"secret_key", "public_key"})
        if body.public_key:
            value["public_key"] = body.public_key.get_secret_value().strip()
        return w.telemetry.configure(
            value, body.secret_key.get_secret_value() if body.secret_key else None
        )

    @router.get("/status", response_model=TelemetryStatus)
    def status():
        return w.telemetry.status()

    @router.post("/test", response_model=ConnectionResult)
    def test():
        return Delivery(w.telemetry).test()

    @router.post("/retry", response_model=TelemetryStatus)
    def retry():
        w.telemetry.store.retry(w.telemetry.config.get("destination_id"))
        if not w.telemetry.store.status()["capture_error"]:
            w.telemetry.capture_error = None
        return w.telemetry.status()

    @router.get("/links/{trace_id}", response_model=TraceLink)
    def link(trace_id: str, kind: Literal["trace", "session"] = "trace"):
        if not re.fullmatch(r"[a-f0-9]{32}", trace_id):
            raise ApplicationError("Invalid trace identifier.", 422)
        with w.telemetry.store.db() as db:
            row = db.execute(
                "SELECT session_id, destination FROM observations WHERE trace_id=? LIMIT 1",
                (trace_id,),
            ).fetchone()
        if row is None:
            raise ApplicationError("This trace has not been recorded yet.", 404)
        ref = w.telemetry.reference(
            {
                "session_id": row["session_id"],
                "destination_id": row["destination"],
                "trace_id": trace_id,
            }
        )
        if not ref or not ref[kind + "_url"]:
            raise ApplicationError(
                "Test the saved Langfuse connection to enable project links.", 422
            )
        return {"url": ref[kind + "_url"]}

    return router


ID = re.compile(r"^[a-f0-9-]{32,36}$")
CORRELATION = {
    "movies": "movie_id",
    "segments": "segment_id",
    "cuts": "cut_id",
    "operations": "operation_id",
    "reviews": "review_id",
    "sessions": "session_id",
}
PREFIXES = ("/api/direction", "/api/production", "/api/agent", "/api/export")


def _ids(value):
    # Only top-level identities are correlation. Never descend into candidate,
    # reveal, request, model-content or provenance maps.
    return (
        {
            key: value[key]
            for key in CORRELATION.values()
            if isinstance(value.get(key), str) and ID.fullmatch(value[key])
        }
        if isinstance(value, dict)
        else {}
    )


class TraceMiddleware:
    def __init__(self, app, workspace, token):
        self.app, self.w, self.token = app, workspace, token

    async def __call__(self, scope, receive, send):
        path = scope.get("path", "")
        headers = dict(scope.get("headers", []))
        rebuilt = any(path == prefix or path.startswith(prefix + "/") for prefix in PREFIXES)
        eligible = (
            scope["type"] == "http"
            and self.w.telemetry.enabled
            and (
                rebuilt
                or (
                    scope.get("method") in {"POST", "PUT", "PATCH", "DELETE"}
                    and path.startswith("/api/v1/")
                    and not path.startswith("/api/v1/session")
                    and (
                        not path.startswith("/api/v1/settings")
                        or (
                            path.startswith("/api/v1/settings/assistant/connections/")
                            and path.endswith("/test")
                        )
                    )
                )
            )
            and secrets.compare_digest(
                headers.get(b"authorization", b""), ("Bearer " + self.token).encode()
            )
        )
        if not eligible:
            await self.app(scope, receive, send)
            return
        parts = path.split("/")
        identities = {}
        for previous, part in pairwise(parts):
            if previous in CORRELATION and ID.fullmatch(part):
                identities[CORRELATION[previous]] = part
        buffered = deque()
        body = bytearray()
        # Buffer only small JSON requests for top-level correlation, replaying
        # every ASGI message unchanged. No body is ever passed to the recorder.
        if b"application/json" in headers.get(b"content-type", b""):
            while True:
                message = await receive()
                buffered.append(message)
                if message["type"] == "http.request":
                    body.extend(message.get("body", b"")[: 65537 - len(body)])
                if len(body) > 65536:
                    break
                if message["type"] != "http.request" or not message.get("more_body", False):
                    break
            if len(body) <= 65536:
                try:
                    identities = {**_ids(json.loads(body)), **identities}
                except (ValueError, RecursionError):
                    pass
        inherited = None
        for value in identities.values():
            inherited = self.w.telemetry.association(value) or inherited
        context = self.w.telemetry.new_context(inherited=inherited)
        context["metadata"] = identities
        status, response_body = 500, bytearray()

        async def traced_receive():
            return buffered.popleft() if buffered else await receive()

        async def traced_send(message):
            nonlocal status
            if message["type"] == "http.response.start":
                status = message["status"]
                message["headers"] = [
                    *message.get("headers", []),
                    (b"x-gaiavfx-trace-id", context["trace_id"].encode()),
                ]
            elif message["type"] == "http.response.body" and len(response_body) <= 65536:
                response_body.extend(message.get("body", b"")[: 65537 - len(response_body)])
            await send(message)

        # Names and routes are static route templates, never arbitrary URL text.
        failure = None
        with self.w.telemetry.observe(
            "api.request", context=context, root=True, input={"method": scope["method"]}
        ) as span:
            try:
                await self.app(scope, traced_receive, traced_send)
            except BaseException as exc:  # noqa: BLE001 - re-raised outside the content recorder
                # The recorder normally captures exception text and stack locals.
                # An API failure may contain model content; record only status.
                failure = exc
                status = 500
            finally:
                route = getattr(scope.get("route"), "path", None)
                if route is None:
                    route = (
                        next((p for p in PREFIXES if path.startswith(p + "/")), "/api/v1")
                        + "/{unmatched}"
                    )
                result_ids = {}
                if status < 400 and len(response_body) <= 65536:
                    try:
                        result = json.loads(response_body)
                        # Correlate only the entity represented by this route,
                        # not any ids in a review's revealed member projection.
                        if isinstance(result, dict):
                            result_ids = _ids(result)
                            entity = next(
                                (CORRELATION[p] for p in reversed(parts) if p in CORRELATION), None
                            )
                            if path.startswith("/api/export/"):
                                entity = "operation_id" if scope["method"] == "POST" else None
                            if (
                                entity
                                and isinstance(result.get("id"), str)
                                and ID.fullmatch(result["id"])
                            ):
                                result_ids[entity] = result["id"]
                    except (ValueError, RecursionError):
                        pass
                span.update(
                    input={"method": scope["method"], "route": route, **identities},
                    output={"status": status, **result_ids},
                    level="ERROR" if status >= 400 else "DEFAULT",
                )
                if status < 400 and span.context:
                    for value in {**identities, **result_ids}.values():
                        self.w.telemetry.associate(value, span.context)
        if failure is not None:
            raise failure
