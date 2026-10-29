"""Langfuse v4 observations with a local-only SDK exporter and durable media receipts."""

# Optional diagnostics must never replace a creative result with an SDK/disk error.
# ruff: noqa: BLE001, S110

import base64
import hashlib
import json
import logging
import mimetypes
import os
import secrets
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import quote

from gaiavfx.shared.errors import ApplicationError
from gaiavfx.shared.telemetry import QUIET, _active, _workflow

from .presentation import title
from .sanitize import Sanitizer
from .store import Store

logger = logging.getLogger(__name__)
HOSTS = (
    "https://cloud.langfuse.com",
    "https://us.cloud.langfuse.com",
    "https://jp.cloud.langfuse.com",
    "https://hipaa.cloud.langfuse.com",
)
DEFAULTS = {
    "enabled": False,
    "base_url": HOSTS[0],
    "environment": "development",
    "storage_limit_gib": 10,
    "credential_storage": "session",
}


class Observation:
    def __init__(self, recorder, sdk_span, context, name):
        self.recorder, self.span = recorder, sdk_span
        self.id, self.trace_id = sdk_span.id, sdk_span.trace_id
        self.context = {**context, "trace_id": self.trace_id, "parent_span_id": self.id}
        self.document = {
            "id": self.id,
            "context": self.context,
            "name": name,
            "start_ns": time.time_ns(),
            "input": None,
            "output": None,
            "metadata": {},
        }

    def update(self, **values):
        try:
            clean = {}
            for key, value in values.items():
                if value is None:
                    continue
                field = "output" if key == "output" else "input" if key == "input" else "metadata"
                clean[key] = self.recorder.sanitizer.clean(
                    value, lambda uri, f=field: self.recorder.inline_media(self, uri, f)
                )
                if key in {"input", "output"}:
                    encoded = json.dumps(clean[key], ensure_ascii=False).encode()
                    if len(encoded) > 256 * 1024:
                        attachment = self.recorder.bytes_media(
                            self, encoded, "application/json", field
                        )
                        clean[key] = {
                            "complete_json": attachment,
                            "bytes": len(encoded),
                            "summary": encoded[:4000].decode("utf-8", errors="replace"),
                        }
            self.document.update(clean)
            self.recorder.store.begin(self.document)
            if isinstance(clean.get("completion_start_time"), str):
                from datetime import datetime

                clean["completion_start_time"] = datetime.fromisoformat(
                    clean["completion_start_time"]
                )
            self.span.update(**clean)
        except Exception as error:
            self.recorder.capture_failure(error)

    def media(self, path, **metadata):
        try:
            return self.recorder.file_media(self, Path(path), metadata)
        except Exception as error:
            self.recorder.capture_failure(error)
            return {"capture_error": self.recorder.sanitizer.text(str(error))}


class Telemetry:
    def __init__(self, root, credentials):
        self.root, self.credentials = root.resolve(), credentials
        self.store = Store(self.root)
        # Composition must not open the library before startup locks and archives it.
        self._config = None
        self.sanitizer = Sanitizer(self.root, credentials, self.public_keys)
        self.sdk = None
        self.provider = None
        self.delivery = None
        self.lock = threading.RLock()
        self.capture_error = None
        self.library_id = None
        self._recovered = False

    @property
    def config(self):
        with self.lock:
            if self._config is None:
                try:
                    saved = self.store.read_config()
                except Exception:
                    saved = {}
                    logger.warning("Telemetry settings could not be read; tracing is disabled.")
                self._config = {**DEFAULTS, **saved}
                self.store.limit = int(self._config["storage_limit_gib"] * 1024**3)
            return self._config

    @config.setter
    def config(self, value):
        self._config = value

    def public_keys(self):
        return (self.config.get("public_key", ""),)

    @property
    def enabled(self):
        return bool(self.config.get("enabled") and self.config.get("destination_id"))

    def settings(self):
        key = self.config.get("public_key", "")
        return {
            **{k: self.config.get(k, v) for k, v in DEFAULTS.items()},
            "public_key_hint": (key[:6] + "…" + key[-4:]) if key else None,
            "has_secret": self.config.get("credential_id")
            in self.credentials.status()["credential_ids"],
            "project_id": self.config.get("project_id"),
        }

    def configure(self, value, secret=None):
        config = {**self.config, **value}
        config["base_url"] = config["base_url"].rstrip("/")
        if config["environment"].startswith("langfuse"):
            raise ApplicationError("Environment names cannot start with langfuse.", 422)
        if config["base_url"] not in HOSTS:
            raise ApplicationError("Choose a supported Langfuse Cloud region.", 422)
        if not config.get("public_key"):
            config["public_key"] = self.config.get("public_key", "")
        if config["enabled"] and not config["public_key"]:
            raise ApplicationError("Enter the Langfuse project public key.", 422)
        destination = None
        if config["public_key"]:
            identity = hashlib.sha256(
                (config["base_url"] + "\n" + config["public_key"]).encode()
            ).hexdigest()[:32]
            if identity != self.config.get("destination_id"):
                config.pop("project_id", None)
            config.update(
                destination_id=identity,
                credential_id="langfuse-" + identity + "-" + config["credential_storage"],
            )
            if secret:
                self.credentials.save(config["credential_id"], secret, config["credential_storage"])
            destination = {
                "id": identity,
                "base_url": config["base_url"],
                "public_key": config["public_key"],
                "credential_id": config["credential_id"],
                "project_id": config.get("project_id"),
            }
        self.store.write_config(config, destination)
        self.config = config
        self.store.limit = int(config["storage_limit_gib"] * 1024**3)
        self.start()
        return self.settings()

    def capture_failure(self, error):
        self.capture_error = self.sanitizer.text(str(error))
        try:
            self.store.gap(self.capture_error)
        except Exception:
            pass
        logger.warning("Telemetry capture unavailable: %s", self.capture_error)

    def _client(self):
        with self.lock:
            if self.sdk is not None:
                return self.sdk
            import httpx
            from google.protobuf.json_format import MessageToDict
            from langfuse import Langfuse
            from langfuse.types import MaskOtelSpansResult, OtelSpanPatch
            from opentelemetry.exporter.otlp.proto.common.trace_encoder import encode_spans
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import SpanLimits, TracerProvider
            from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult

            owner = self

            class DurableExporter(SpanExporter):
                def export(self, spans):
                    for span in spans:
                        try:
                            context = json.loads(
                                span.attributes["langfuse.observation.metadata._fp_context"]
                            )
                            payload = MessageToDict(encode_spans([span]))
                            # The SDK is a local formatter: its synthetic scope key is not a project key.
                            for resource in payload.get("resourceSpans", []):
                                for scope in resource.get("scopeSpans", []):
                                    scope.get("scope", {}).pop("attributes", None)
                                    for encoded_span in scope.get("spans", []):
                                        encoded_span["attributes"] = [
                                            a
                                            for a in encoded_span.get("attributes", [])
                                            if a["key"]
                                            != "langfuse.observation.metadata._fp_context"
                                        ]
                                        # trace_context without a parent makes an SDK synthetic parent.
                                        # This is an application-owned root, not a remote child.
                                        if not context.get("parent_span_id"):
                                            encoded_span.pop("parentSpanId", None)
                                        for key in ("traceId", "spanId", "parentSpanId"):
                                            if key in encoded_span:
                                                encoded_span[key] = base64.b64decode(
                                                    encoded_span[key]
                                                ).hex()
                            payload = owner.sanitizer.clean(payload)
                            owner.store.enqueue(
                                f"{span.context.span_id:016x}",
                                f"{span.context.trace_id:032x}",
                                context["session_id"],
                                context["destination_id"],
                                json.dumps(payload, ensure_ascii=False).encode(),
                            )
                        except Exception as error:
                            owner.capture_failure(error)
                            return SpanExportResult.FAILURE
                    return SpanExportResult.SUCCESS

            def mask_otel_spans(*, params):
                return MaskOtelSpansResult(
                    span_patches={
                        id: OtelSpanPatch(
                            set_attributes={
                                k: owner.sanitizer.clean(v) for k, v in span.attributes.items()
                            }
                        )
                        for id, span in params.spans.items()
                    }
                )

            self.provider = TracerProvider(
                resource=Resource({"service.name": "gaiavfx", "service.version": "0.1.0"}),
                span_limits=SpanLimits(max_attributes=512, max_attribute_length=None),
                shutdown_on_exit=False,
            )
            # Real credentials are resolved ONLY by Delivery. The SDK cannot contact any host.
            self.sdk = Langfuse(
                public_key="local-recorder-" + uuid.uuid4().hex,
                secret_key="local-export-only",
                base_url=HOSTS[0],
                tracer_provider=self.provider,
                span_exporter=DurableExporter(),
                httpx_client=httpx.Client(
                    transport=httpx.MockTransport(
                        lambda request: httpx.Response(503, json={"error": "local recorder only"})
                    )
                ),
                environment=self.config["environment"],
                release=os.environ.get("GAIAVFX_RELEASE", "0.1.0"),
                mask_otel_spans=mask_otel_spans,
                sample_rate=1.0,
                should_export_span=lambda span: (
                    "langfuse.observation.metadata._fp_context" in span.attributes
                ),
            )
            return self.sdk

    def new_context(self, workflow_id=None, inherited=None, **metadata):
        inherited = inherited or {}
        return {
            "session_id": inherited.get("session_id") or "fp-" + (workflow_id or uuid.uuid4().hex),
            "trace_id": secrets.token_hex(16),
            "destination_id": inherited.get("destination_id") or self.config.get("destination_id"),
            "previous_trace_id": inherited.get("trace_id"),
            "environment": self.config["environment"],
            "release": os.environ.get("GAIAVFX_RELEASE", "0.1.0"),
            "metadata": {
                "library_id": self.library_id,
                **inherited.get("metadata", {}),
                **metadata,
            },
        }

    @contextmanager
    def workflow(self, id, **metadata):
        """Restore scheduler identity without recording idle polls."""
        if not self.enabled:
            yield
            return
        inherited = self.association(id)
        context = self.new_context(id, inherited=inherited, **metadata)
        if not inherited:
            self.associate(id, context)
        token = _workflow.set((self, context))
        try:
            yield
        finally:
            _workflow.reset(token)

    @contextmanager
    def observe(self, name, *, input=None, metadata=None, as_type="span", context=None, root=False):
        parent = _active.get()
        if not self.enabled:
            yield QUIET
            return
        token, span = None, None
        try:
            from langfuse import propagate_attributes

            context = dict(
                context or (parent.context if parent and not root else self.new_context())
            )
            display_name = title(name)
            context.setdefault("trace_name", display_name)
            metadata = {**context.get("metadata", {}), **(metadata or {}), "operation": name}
            if context.get("previous_trace_id"):
                metadata["previous_trace_id"] = context["previous_trace_id"]
            context["metadata"] = self.sanitizer.clean(metadata)
            trace = {"trace_id": context["trace_id"]}
            if not root and context.get("parent_span_id"):
                trace["parent_span_id"] = context["parent_span_id"]
            with propagate_attributes(
                session_id=context["session_id"],
                trace_name=context.get("trace_name", name),
                environment=self.config["environment"],
                metadata={k: str(v) for k, v in context["metadata"].items() if v is not None},
                tags=["gaiavfx", self.config["environment"]],
            ):
                sdk_span = self._client().start_observation(
                    name=display_name,
                    trace_context=trace,
                    as_type=as_type,
                    metadata={**context["metadata"], "_fp_context": context},
                )
            context.setdefault("trace_name", name)
            span = Observation(self, sdk_span, context, name)
            span.document["parent_id"] = trace.get("parent_span_id")
            span.update(input=input, metadata={**context["metadata"], "_fp_context": context})
            token = _active.set(span)
        except Exception as error:
            self.capture_failure(error)
        try:
            yield span or QUIET
        except BaseException as error:
            if span:
                import traceback

                span.update(
                    level="ERROR",
                    status_message=str(error),
                    metadata={
                        "exception_type": type(error).__name__,
                        "exception": str(error),
                        "stack": "".join(traceback.format_exception(error)),
                    },
                )
            raise
        finally:
            if token is not None:
                _active.reset(token)
            if span:
                try:
                    span.span.end()
                    # Only a local SQLite write. This does not flush over the network.
                    if not self.provider.force_flush(timeout_millis=2000):
                        self.capture_failure(RuntimeError("Local observation flush timed out."))
                except Exception as error:
                    self.capture_failure(error)

    def inline_media(self, span, uri, field):
        header, encoded = uri.split(",", 1)
        mime = header[5:].split(";", 1)[0]
        try:
            return self.bytes_media(span, base64.b64decode(encoded, validate=True), mime, field)
        except Exception as error:
            self.capture_failure(error)
            return {"capture_error": "Inline media could not be retained.", "mime": mime}

    def bytes_media(self, span, data, mime, field):
        digest = hashlib.sha256(data).hexdigest()
        path = self.store.root / "attachments" / digest
        with self.store.lock:
            if not path.exists():
                self.store.require_space(len(data))
                path.parent.mkdir(parents=True, exist_ok=True)
                temporary = path.with_suffix(".tmp")
                temporary.write_bytes(data)
                temporary.replace(path)
        return self.file_media(span, path, {"mime": mime, "field": field, "sha256": digest})

    def file_media(self, span, path, metadata):
        path = path.resolve()
        mime = (
            metadata.get("mime") or mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        )
        if not mime.startswith(("image/", "video/", "audio/")) and mime != "application/json":
            return {
                "name": self.sanitizer.text(path.name),
                "mime": mime,
                "bytes": path.stat().st_size if path.is_file() else None,
                "attachment": "Opaque artifact; identity metadata only",
            }
        with path.open("rb") as stream:
            digest = hashlib.file_digest(stream, "sha256").hexdigest()
        owned = path.is_relative_to(self.root) and path.relative_to(self.root).parts[0] in {
            "originals",
            "generated",
            "exports",
            "telemetry",
        }
        if not owned:
            target = self.store.root / "attachments" / digest
            with self.store.lock:
                if not target.exists():
                    import shutil

                    self.store.require_space(path.stat().st_size)
                    target.parent.mkdir(parents=True, exist_ok=True)
                    temporary = target.with_suffix(".tmp")
                    shutil.copyfile(path, temporary)
                    temporary.replace(target)
            path = target
        context = span.context
        field = metadata.get("field", "output")
        id = hashlib.sha256((context["trace_id"] + span.id + digest + field).encode()).hexdigest()[
            :16
        ]
        doc = {
            "id": id,
            "context": context,
            "parent_id": span.id,
            "sha256": digest,
            "path": path.relative_to(self.root).as_posix(),
            "mime": mime,
            "bytes": path.stat().st_size,
            "field": field,
            "metadata": self.sanitizer.clean(metadata),
            "created_ns": time.time_ns(),
        }
        encoded = json.dumps(doc, ensure_ascii=False)
        with self.store.lock:
            self.store.require_space(len(encoded.encode()))
            with self.store.db() as db:
                db.execute(
                    "INSERT OR IGNORE INTO media (id,destination,document,state) VALUES (?,?,?,'pending')",
                    (id, context["destination_id"], encoded),
                )
        return {"attachment_id": id, "sha256": digest, "mime": mime, "bytes": doc["bytes"]}

    def associate(self, id, context):
        try:
            self.store.associate(id, context)
        except Exception as error:
            self.capture_failure(error)

    def association(self, id):
        try:
            return self.store.association(id)
        except Exception as error:
            self.capture_failure(error)
            return None

    def reference(self, context):
        if not context:
            return None
        try:
            destination = self.store.destination(context["destination_id"])
            base = destination["base_url"] if destination else None
            project = destination.get("project_id") if destination else None
            prefix = f"{base}/project/{quote(project, safe='')}" if project else None
            with self.store.db() as db:
                states = [
                    r[0]
                    for r in db.execute(
                        "SELECT state FROM observations WHERE trace_id=?", (context["trace_id"],)
                    )
                ]
            state = next(
                (
                    s
                    for s in ("uncertain", "blocked", "failed", "pending", "sending")
                    if s in states
                ),
                "delivered" if states else "recording",
            )
            return {
                "session_id": context["session_id"],
                "trace_id": context["trace_id"],
                "trace_url": f"{prefix}/traces/{context['trace_id']}" if prefix else None,
                "session_url": f"{prefix}/sessions/{quote(context['session_id'], safe='')}"
                if prefix
                else None,
                "delivery_state": state,
            }
        except Exception as error:
            self.capture_failure(error)
            return None

    def status(self):
        result = self.store.status()
        result.update(
            enabled=self.enabled, capture_error=self.capture_error or result["capture_error"]
        )
        if not self.enabled:
            result["connection"] = "disabled"
        else:
            try:
                self.credentials.get(self.config.get("credential_id"))
                result["connection"] = "configured"
            except ApplicationError:
                result["connection"] = "credentials_required"
        return result

    def start(self):
        if not self.enabled:
            return
        with self.lock:
            if self.delivery is None:
                from .delivery import Delivery

                try:
                    self.delivery = Delivery(self)
                    self.delivery.start()
                except Exception as error:
                    self.capture_failure(error)

    def close(self):
        try:
            if self.delivery:
                self.delivery.close()
            if self.sdk:
                self.provider.force_flush(timeout_millis=2000)
                self.sdk.shutdown()
        except Exception as error:
            self.capture_failure(error)
