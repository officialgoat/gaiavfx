"""Process-local model lifetime, including a bounded, parked GPU lease."""

import errno
import json
import logging
from contextlib import ExitStack
from dataclasses import dataclass, field
from datetime import UTC, datetime
from threading import Event, Lock
from time import monotonic
from typing import Protocol

from gaiavfx.infrastructure.assistant import grammar_schema, resource_identity
from gaiavfx.infrastructure.coordination import gpu_lease
from gaiavfx.shared.assistant import connection_for

RETRYABLE_REPLY_PREFIXES = (
    "The assistant returned invalid JSON",
    "The assistant exhausted its output allowance",
)


@dataclass
class ModelReply:
    raw: dict
    input_tokens: int = 0
    output_tokens: int = 0
    metrics: dict = field(default_factory=dict)


class ModelClient(Protocol):
    def begin(self, settings, cancelled): ...
    def call(self, settings, messages, schema, cancelled, log_path) -> ModelReply: ...
    def end(self): ...


class Cancellation:
    """Gateway runtimes use Event.is_set; coordination uses a predicate."""

    def __init__(self, predicate):
        self.predicate = predicate

    def is_set(self):
        return self.predicate()

    def wait(self, timeout):
        """The kept local runtime waits while its process starts; keep stop responsive."""
        deadline = monotonic() + timeout
        pause = Event()
        while not self.is_set():
            remaining = deadline - monotonic()
            if remaining <= 0:
                return False
            pause.wait(min(0.1, remaining))
        return True


class AssistantModel:
    def __init__(self, gateway, comfy, *, clock=monotonic):
        self.gateway, self.comfy = gateway, comfy
        self.lifetime = None
        self.local = False
        self.clock = clock
        self.parked_at = None
        self.identity = None
        self.loaded = False
        self.call_metrics = {}
        self.session_id = None
        self._lock = Lock()
        self._status = {
            "provider": "",
            "connection_id": "",
            "model": "",
            "state": "unloaded",
            "since": datetime.now(UTC).isoformat(),
            "session_id": None,
        }

    @property
    def active(self):
        return self.lifetime is not None

    def _state(self, state, **fields):
        with self._lock:
            self._status.update(fields, session_id=self.session_id)
            if self._status["state"] != state:
                self._status.update(state=state, since=datetime.now(UTC).isoformat())

    def status(self):
        with self._lock:
            return dict(self._status)

    def begin(self, settings, cancelled):
        connection = connection_for(settings, "agent")
        local = connection["provider"] == "managed_local"
        try:
            identity = json.dumps(
                [
                    resource_identity(settings) if local else connection,
                    settings.get("assistant_context"),
                    settings.get("comfy_url") if local else None,
                ],
                sort_keys=True,
            )
        except Exception:
            self.release()
            raise
        self.parked_at = None
        if self.active and self.identity == identity:
            self._state("ready" if self.loaded else "loading")
            return
        session_id = self.session_id
        self.release()
        self.session_id = session_id
        self.lifetime = ExitStack()
        self.local, self.identity = local, identity
        self._state(
            "waiting_gpu" if local else "ready",
            provider=connection["provider"],
            connection_id=connection.get("id", ""),
            model=connection.get("model", ""),
        )
        if self.local:
            endpoint = settings["comfy_url"]
            self.lifetime.enter_context(
                gpu_lease(endpoint, cancelled, waiting=lambda: self._state("waiting_gpu"))
            )
            try:
                self.comfy.request(
                    endpoint, "POST", "/free", json={"unload_models": True, "free_memory": True}
                )
            except Exception:  # noqa: BLE001 - ComfyUI may be stopped; the lease still serialises the card
                # As an engine job's free (engine_jobs._free_comfy): a dead endpoint must not stop the model.
                logging.getLogger(__name__).info("ComfyUI did not answer /free at %s", endpoint)
            self._state("loading")
        else:
            self.loaded = True

    def call(self, settings, messages, schema, cancelled, log_path):
        self._state("thinking" if self.loaded else "loading")
        self.call_metrics = {"server_restarts": 0}
        for attempt in range(2):
            try:
                result = self.gateway.run(
                    {**settings, "assistant_phase": "agent"},
                    None,
                    Cancellation(cancelled),
                    log_path,
                    response_schema=grammar_schema(schema),
                    keep_alive=True,
                    messages=messages,
                )
                break
            except Exception as exc:
                if attempt or not self.local or cancelled() or not connection_dropped(exc):
                    raise
                # Keep the GPU lease while replacing only the managed server. No
                # tool has run yet, so retry the exact same model request once.
                self.gateway.stop()
                self.call_metrics = {"server_restarts": 1, "restart_reason": "connection_dropped"}
                self.loaded = False
                self._state("loading")
        self.loaded = True
        self._state("ready")
        usage = result.get("metrics", {}).get("usage", {})
        return ModelReply(
            raw={
                key: result[key]
                for key in (
                    schema.get("properties", ("tool", "thought", "input"))
                    if "tool" not in schema.get("properties", {}) and "oneOf" not in schema
                    else ("tool", "thought", "input")
                )
                if key in result
            },
            input_tokens=int(usage.get("prompt_tokens") or 0),
            output_tokens=int(usage.get("completion_tokens") or 0),
            metrics=result.get("metrics", {}) | self.call_metrics,
        )

    def park(self):
        if not self.loaded:
            self.release()
            return
        self.parked_at = self.clock()
        self._state("ready")

    def maintain(self, *, gpu_wanted=False, keep_warm_seconds=180, stopping=False):
        if stopping or (
            self.parked_at is not None
            and ((self.local and gpu_wanted) or self.clock() - self.parked_at >= keep_warm_seconds)
        ):
            self.release()

    def release(self):
        try:
            if self.local:
                self.gateway.stop()
        finally:
            if self.lifetime:
                self.lifetime.close()
            self.lifetime, self.local = None, False
            self.identity, self.parked_at, self.session_id = None, None, None
            self.loaded = False
            self._state("unloaded")

    end = release


def connection_dropped(error):
    """Match reset/refused errors through httpx/httpcore wrappers, not HTTP statuses."""
    seen = set()
    while error is not None and id(error) not in seen:
        seen.add(id(error))
        if isinstance(error, (ConnectionResetError, ConnectionRefusedError)):
            return True
        if isinstance(error, OSError) and (
            error.errno in {errno.ECONNRESET, errno.ECONNREFUSED}
            or getattr(error, "winerror", None) in {10054, 10061}
        ):
            return True
        # Some transport wrappers retain only Windows' message, not the OSError.
        if any(
            text in str(error).lower()
            for text in (
                "[winerror 10054]",
                "[winerror 10061]",
                "connection reset",
                "connection refused",
            )
        ):
            return True
        error = error.__cause__ or error.__context__
    return False
