"""Bounded background delivery. Inference is never retried by this worker."""

import base64
import hashlib
import json
import threading
import time
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from urllib.parse import quote

import httpx

from gaiavfx.shared.errors import ApplicationError


def iso(seconds):
    return datetime.fromtimestamp(seconds, UTC).isoformat().replace("+00:00", "Z")


def retry_delay(response, attempts):
    raw = response.headers.get("retry-after", "") if response is not None else ""
    try:
        return max(1, float(raw))
    except ValueError:
        try:
            return max(1, parsedate_to_datetime(raw).timestamp() - time.time())
        except (ValueError, TypeError):
            return min(300, 2 ** min(attempts + 1, 8))


def finished_payload(document, *, name=None, output=None, level=None):
    """Encode recovered or delayed attachment observations without reopening ingested spans."""
    context = document["context"]
    attributes = {
        "langfuse.observation.type": "span",
        "session.id": context["session_id"],
        "langfuse.environment": context.get("environment", "development"),
        "langfuse.release": context.get("release", "0.1.0"),
        "langfuse.trace.name": context.get("trace_name", document.get("name", "workflow")),
        "langfuse.observation.input": json.dumps(document.get("input"), ensure_ascii=False),
        "langfuse.observation.output": json.dumps(
            output if output is not None else document.get("output"), ensure_ascii=False
        ),
        "langfuse.observation.level": level or document.get("level", "DEFAULT"),
    }
    for key, value in {**context.get("metadata", {}), **document.get("metadata", {})}.items():
        if key == "_fp_context":
            continue
        attributes["langfuse.observation.metadata." + key] = (
            value if isinstance(value, str) else json.dumps(value, ensure_ascii=False)
        )
    if document.get("status_message"):
        attributes["langfuse.observation.status_message"] = document["status_message"]
    span = {
        "traceId": context["trace_id"],
        "spanId": document["id"],
        "name": name or document.get("name", "Media attachment"),
        "kind": 1,
        "startTimeUnixNano": str(
            document.get("start_ns", document.get("created_ns", time.time_ns()))
        ),
        "endTimeUnixNano": str(time.time_ns()),
        "attributes": [{"key": k, "value": {"stringValue": str(v)}} for k, v in attributes.items()],
    }
    if document.get("parent_id"):
        span["parentSpanId"] = document["parent_id"]
    return json.dumps(
        {
            "resourceSpans": [
                {
                    "resource": {
                        "attributes": [{"key": "service.name", "value": {"stringValue": "gaiavfx"}}]
                    },
                    "scopeSpans": [{"scope": {"name": "gaiavfx.telemetry"}, "spans": [span]}],
                }
            ]
        },
        ensure_ascii=False,
    ).encode()


class Delivery:
    def __init__(self, telemetry, transport=None):
        self.telemetry, self.store = telemetry, telemetry.store
        self.transport = transport
        self.stop = threading.Event()
        self.thread = None

    def start(self):
        self.store.recover_delivery()
        with self.store.db() as db:
            active = [json.loads(row[0]) for row in db.execute("SELECT document FROM active")]
        for document in active:
            context = document["context"]
            document["metadata"]["interrupted"] = True
            document["status_message"] = "Application stopped before this stage completed."
            self.store.enqueue(
                document["id"],
                context["trace_id"],
                context["session_id"],
                context["destination_id"],
                finished_payload(document, level="ERROR"),
            )
        self.thread = threading.Thread(target=self.run, name="GAIAVFX telemetry", daemon=True)
        self.thread.start()

    def close(self):
        self.stop.set()
        if self.thread:
            self.thread.join(timeout=1)

    def run(self):
        while not self.stop.is_set():
            try:
                if self.telemetry.enabled:
                    self.tick()
            except Exception as error:  # noqa: BLE001 - isolate optional telemetry worker
                self.telemetry.capture_failure(error)
            self.stop.wait(1)

    def credentials(self, destination):
        key = self.telemetry.credentials.get(destination["credential_id"])
        if not key:
            raise ApplicationError("Enter the Langfuse secret key in Settings.", 422)
        return (destination["public_key"], key)

    def client(self):
        return httpx.Client(
            transport=self.transport, timeout=httpx.Timeout(15, connect=5), follow_redirects=False
        )

    def test(self):
        config = self.telemetry.config
        destination = self.store.destination(config.get("destination_id"))
        if not destination:
            raise ApplicationError("Save a Langfuse public and secret key first.", 422)
        try:
            with self.client() as client:
                response = client.get(
                    destination["base_url"] + "/api/public/projects",
                    auth=self.credentials(destination),
                )
            if not response.is_success:
                raise ApplicationError(
                    f"Langfuse connection test returned HTTP {response.status_code}.", 422
                )
            projects = response.json()["data"]
            if len(projects) != 1 or not isinstance(projects[0].get("id"), str):
                raise ValueError("Expected one key-scoped project")
        except (httpx.HTTPError, ValueError, KeyError) as error:
            raise ApplicationError(
                "Langfuse connection could not be verified. Check the region and keys.", 422
            ) from error
        config["project_id"] = destination["project_id"] = projects[0]["id"]
        self.store.write_config(config, destination)
        return {
            "ok": True,
            "project_id": config["project_id"],
            "project_name": projects[0].get("name", "Langfuse project"),
        }

    def tick(self):
        if not self.telemetry.enabled:
            return
        id = self.telemetry.config["destination_id"]
        destination = self.store.destination(id)
        try:
            auth = self.credentials(destination)
        except ApplicationError:
            return
        with self.client() as client:
            row = self.store.next("observations", id)
            if row:
                self.observation(client, destination, auth, row)
            if not self.stop.is_set():
                media = self.store.next("media", id)
                if media:
                    self.media(client, destination, auth, media)

    def postpone(self, table, row, state, message, response=None):
        self.store.update(
            table,
            row["id"],
            state=state,
            attempts=row["attempts"] + 1,
            next_try=time.time() + retry_delay(response, row["attempts"]),
            error=self.telemetry.sanitizer.text(message),
        )
        if table == "media" and state == "failed":
            document = json.loads(row["document"])
            document["id"] = hashlib.sha256((row["id"] + "-failure").encode()).hexdigest()[:16]
            context = document["context"]
            self.store.enqueue(
                document["id"],
                context["trace_id"],
                context["session_id"],
                context["destination_id"],
                finished_payload(
                    document,
                    name="media.upload_failed",
                    output={
                        "attachment_id": row["id"],
                        "error": self.telemetry.sanitizer.text(message),
                    },
                    level="ERROR",
                ),
            )

    def observation(self, client, destination, auth, row):
        if row["state"] == "uncertain":
            self.reconcile(client, destination, auth, row)
            return
        self.store.update("observations", row["id"], state="sending")
        try:
            response = client.post(
                destination["base_url"] + "/api/public/otel/v1/traces",
                auth=auth,
                headers={"Content-Type": "application/json", "x-langfuse-ingestion-version": "4"},
                content=row["payload"],
            )
        except (httpx.ConnectError, httpx.ConnectTimeout, httpx.PoolTimeout):
            self.postpone(
                "observations", row, "pending", "Langfuse is unreachable; delivery will retry."
            )
            return
        except httpx.HTTPError:
            self.postpone(
                "observations",
                row,
                "uncertain",
                "Receipt is uncertain; checking before another send.",
            )
            return
        if response.is_success:
            # OTLP may acknowledge a request with partial rejection. Never lose the failed payload.
            try:
                partial = response.json().get("partialSuccess", {}) if response.content else {}
            except ValueError:
                partial = {}
            if int(partial.get("rejectedSpans", 0)):
                self.postpone("observations", row, "failed", "Langfuse rejected this observation.")
            else:
                self.store.update(
                    "observations",
                    row["id"],
                    state="delivered",
                    payload=None,
                    error=None,
                    delivered=time.time(),
                )
        elif response.status_code == 429:
            self.postpone(
                "observations", row, "pending", "Langfuse rate limited delivery.", response
            )
        elif response.status_code in (401, 403):
            self.postpone(
                "observations",
                row,
                "blocked",
                "Langfuse credentials were rejected. Update them and retry.",
            )
        elif response.status_code >= 500:
            self.postpone(
                "observations",
                row,
                "uncertain",
                "Langfuse returned a server error; receipt needs reconciliation.",
                response,
            )
        else:
            self.postpone(
                "observations",
                row,
                "failed",
                f"Langfuse rejected the observation: HTTP {response.status_code}.",
            )

    def reconcile(self, client, destination, auth, row):
        try:
            response = client.get(
                destination["base_url"] + "/api/public/v2/observations",
                auth=auth,
                params={
                    "traceId": row["trace_id"],
                    "fromStartTime": iso(row["created"] - 7 * 86400),
                    "toStartTime": iso(time.time() + 60),
                    "limit": 100,
                    "filter": json.dumps(
                        [{"type": "string", "column": "id", "operator": "=", "value": row["id"]}]
                    ),
                },
            )
            response.raise_for_status()
            found = any(item.get("id") == row["id"] for item in response.json().get("data", []))
        except (httpx.HTTPError, ValueError, TypeError):
            found = False
        if found:
            self.store.update(
                "observations",
                row["id"],
                state="delivered",
                payload=None,
                error=None,
                delivered=time.time(),
            )
        else:
            self.store.update(
                "observations",
                row["id"],
                state="uncertain",
                next_try=time.time() + 300,
                error="Receipt remains uncertain. No duplicate observation was sent; use Retry delivery to check again.",
            )

    def media(self, client, destination, auth, row):
        document = json.loads(row["document"])
        path = (self.telemetry.root / document["path"]).resolve()
        if not path.is_relative_to(self.telemetry.root) or not path.is_file():
            self.postpone("media", row, "failed", "The retained media file is missing.")
            return
        digest = document["sha256"]
        with path.open("rb") as stream:
            if hashlib.file_digest(stream, "sha256").hexdigest() != digest:
                self.postpone("media", row, "failed", "The media bytes changed; upload refused.")
                return
        self.store.update("media", row["id"], state="sending")
        try:
            with self.store.db() as db:
                known = db.execute(
                    "SELECT remote_id FROM uploaded_media WHERE destination=? AND sha256=?",
                    (destination["id"], digest),
                ).fetchone()
            if known:
                media_id = known[0]
            else:
                checksum = base64.b64encode(bytes.fromhex(digest)).decode()
                response = client.post(
                    destination["base_url"] + "/api/public/media",
                    auth=auth,
                    json={
                        "traceId": document["context"]["trace_id"],
                        "observationId": row["id"],
                        "contentType": document["mime"],
                        "contentLength": document["bytes"],
                        "sha256Hash": checksum,
                        "field": document["field"],
                    },
                )
                if not response.is_success:
                    state = (
                        "blocked"
                        if response.status_code in (401, 403)
                        else "pending"
                        if response.status_code == 429 or response.status_code >= 500
                        else "failed"
                    )
                    self.postpone(
                        "media",
                        row,
                        state,
                        f"Langfuse media registration returned HTTP {response.status_code}.",
                        response,
                    )
                    return
                info = response.json()
                media_id = info["mediaId"]
                if info.get("uploadUrl"):
                    upload_started = time.monotonic()
                    with path.open("rb") as stream:
                        uploaded = client.put(
                            info["uploadUrl"],
                            content=stream,
                            headers={
                                "Content-Type": document["mime"],
                                "Content-Length": str(document["bytes"]),
                                "x-amz-checksum-sha256": checksum,
                            },
                        )
                    if not uploaded.is_success:
                        self.postpone(
                            "media",
                            row,
                            "pending"
                            if uploaded.status_code >= 500 or uploaded.status_code in (403, 429)
                            else "failed",
                            f"Media upload returned HTTP {uploaded.status_code}.",
                            uploaded,
                        )
                        return
                    acknowledged = client.patch(
                        destination["base_url"] + "/api/public/media/" + quote(media_id, safe=""),
                        auth=auth,
                        json={
                            "uploadedAt": iso(time.time()),
                            "uploadHttpStatus": uploaded.status_code,
                            "uploadTimeMs": round((time.monotonic() - upload_started) * 1000),
                        },
                    )
                    acknowledged.raise_for_status()
                with self.store.db() as db:
                    db.execute(
                        "INSERT OR REPLACE INTO uploaded_media VALUES (?, ?, ?)",
                        (destination["id"], digest, media_id),
                    )
            token = f"@@@langfuseMedia:type={document['mime']}|id={media_id}|source=bytes@@@"
            result = {
                "media": token,
                "sha256": digest,
                "mime": document["mime"],
                "bytes": document["bytes"],
                "field": document["field"],
                "source_observation_id": document["parent_id"],
                **document["metadata"],
            }
            kind = document["mime"].split("/", 1)[0]
            document["name"] = (
                f"{document['field'].capitalize()} {kind} · {document['metadata'].get('role', 'model media')}"
            )
            if document["field"] == "input":
                document["input"] = result
                result = {"status": "Uploaded", "sha256": digest}
            context = document["context"]
            self.store.enqueue(
                row["id"],
                context["trace_id"],
                context["session_id"],
                context["destination_id"],
                finished_payload(document, output=result),
            )
            self.store.update("media", row["id"], state="delivered", remote_id=media_id, error=None)
            self.cleanup_attachment(path)
        except (httpx.HTTPError, KeyError, ValueError, OSError) as error:
            self.postpone(
                "media", row, "pending", "Media upload did not complete: " + type(error).__name__
            )

    def cleanup_attachment(self, path):
        if not path.is_relative_to(self.store.root / "attachments"):
            return
        relative = path.relative_to(self.telemetry.root).as_posix()
        with self.store.lock, self.store.db() as db:
            pending = any(
                json.loads(r[0])["path"] == relative
                for r in db.execute("SELECT document FROM media WHERE state != 'delivered'")
            )
            if not pending:
                path.unlink(missing_ok=True)
