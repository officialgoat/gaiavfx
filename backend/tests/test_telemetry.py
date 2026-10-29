import json
import threading

import httpx
import pytest

from gaiavfx.infrastructure.credentials import CredentialStore
from gaiavfx.infrastructure.telemetry import Telemetry
from gaiavfx.infrastructure.telemetry.delivery import Delivery
from gaiavfx.shared.telemetry import observation


@pytest.fixture
def telemetry(tmp_path, monkeypatch):
    recorder = Telemetry(tmp_path, CredentialStore(tmp_path))
    monkeypatch.setattr(recorder, "start", lambda: None)
    recorder.configure({"enabled": True, "public_key": "pk-lf-test-project"}, "sk-lf-test-secret")
    yield recorder
    recorder.close()


def rows(recorder):
    with recorder.store.db() as db:
        return [dict(r) for r in db.execute("SELECT * FROM observations ORDER BY created")]


def span(row):
    return json.loads(row["payload"])["resourceSpans"][0]["scopeSpans"][0]["spans"][0]


def operation(value):
    attrs = {a["key"]: a["value"] for a in value["attributes"]}
    return attrs["langfuse.observation.metadata.operation"]["stringValue"]


def test_disabled_is_noop_and_does_not_create_queue(tmp_path):
    recorder = Telemetry(tmp_path, CredentialStore(tmp_path))
    with recorder.observe("not-recorded"):
        pass
    assert not recorder.store.path.exists()


def test_nested_spans_persist_exact_structured_data_and_mask_secrets(telemetry):
    context = telemetry.new_context("proposal-1", movie_id="movie-1")
    with telemetry.observe(
        "assistant.refine", context=context, root=True, input={"prompt": "Keep the red bag."}
    ) as root:
        with observation("model.call", as_type="generation") as child:
            child.update(
                input={
                    "messages": [{"role": "user", "content": "Draw the bag"}],
                    "authorization": "Bearer private",
                    "api_key": "any-key",
                },
                output={"raw": "sk-lf-test-secret", "answer": {"color": "red"}},
                model="qwen",
            )
        root.update(output={"applied": False})
    recorded = rows(telemetry)
    assert len(recorded) == 2
    assert {r["session_id"] for r in recorded} == {context["session_id"]}
    encoded = b"".join(r["payload"] for r in recorded)
    assert b"sk-lf-test-secret" not in encoded and b"any-key" not in encoded
    assert b"pk-lf-test-project" not in encoded
    assert b"Draw the bag" in encoded and b"red" in encoded
    assert span(recorded[0])["parentSpanId"] == span(recorded[1])["spanId"]
    assert "parentSpanId" not in span(recorded[1])
    assert b"_fp_context" not in encoded
    assert len(span(recorded[0])["traceId"]) == 32
    with telemetry.store.db() as db:
        assert db.execute("SELECT COUNT(*) FROM active").fetchone()[0] == 0


def test_concurrent_workflow_contexts_do_not_leak(telemetry):
    barrier = threading.Barrier(2)

    def work(id):
        with telemetry.observe(id, root=True, context=telemetry.new_context(id)):
            barrier.wait(timeout=10)
            with observation("child"):
                pass

    threads = [threading.Thread(target=work, args=(str(i),)) for i in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=15)
        assert not t.is_alive()
    recorded = rows(telemetry)
    assert len(recorded) == 4
    assert len({r["trace_id"] for r in recorded}) == 2
    assert len({r["session_id"] for r in recorded}) == 2


def test_sanitization_reads_vault_once_and_preserves_http_urls(telemetry, monkeypatch):
    reads = []

    def values():
        reads.append(True)
        return ("super-secret-value",)

    monkeypatch.setattr(telemetry.credentials, "sensitive_values", values)
    result = telemetry.sanitizer.clean(
        {
            "items": [
                {"name": str(i), "url": "http://127.0.0.1:8195", "secret_key": "super-secret-value"}
                for i in range(1000)
            ]
        }
    )
    assert len(reads) == 1
    assert result["items"][0]["url"] == "http://127.0.0.1:8195"
    assert result["items"][0]["secret_key"] == "[credential omitted]"
    assert (
        telemetry.sanitizer.clean("A basic white shirt with a red bag.")
        == "A basic white shirt with a red bag."
    )
    assert "unknown-token" not in telemetry.sanitizer.clean("Authorization: Bearer unknown-token")


def test_failure_retains_partial_output_without_masking_application_error(telemetry):
    with (
        pytest.raises(ValueError, match="invalid JSON"),
        telemetry.observe("model.call", root=True, as_type="generation") as stage,
    ):
        stage.update(output={"raw": '{"unfinished":'})
        raise ValueError("invalid JSON")
    payload = rows(telemetry)[0]["payload"]
    assert b"unfinished" in payload and b"invalid JSON" in payload and b"ERROR" in payload


def test_outage_and_restart_keep_original_ids(telemetry):
    with telemetry.observe("test", root=True):
        pass
    original = rows(telemetry)[0]

    def offline(request):
        raise httpx.ConnectError("offline")

    Delivery(telemetry, httpx.MockTransport(offline)).tick()
    assert rows(telemetry)[0]["state"] == "pending"
    telemetry.store.retry(telemetry.config["destination_id"])
    sent = []

    def online(request):
        sent.append(request)
        return httpx.Response(200, json={})

    Delivery(telemetry, httpx.MockTransport(online)).tick()
    final = rows(telemetry)[0]
    assert final["id"] == original["id"] and final["state"] == "delivered"
    assert final["payload"] is None
    assert sent[0].headers["x-langfuse-ingestion-version"] == "4"


def test_ambiguous_receipt_is_queried_not_blindly_resent(telemetry):
    with telemetry.observe("test", root=True):
        pass
    requests = []

    def timeout(request):
        requests.append(request.method)
        if request.method == "POST":
            raise httpx.ReadTimeout("may have arrived")
        return httpx.Response(200, json={"data": []})

    delivery = Delivery(telemetry, httpx.MockTransport(timeout))
    delivery.tick()
    telemetry.store.retry(telemetry.config["destination_id"])
    delivery.tick()
    assert requests == ["POST", "GET"]
    assert rows(telemetry)[0]["state"] == "uncertain"


def test_quota_never_breaks_creative_work_or_deletes_pending(telemetry):
    with telemetry.observe("existing", root=True):
        pass
    before = rows(telemetry)
    telemetry.store.limit = 1
    with telemetry.observe("new", root=True):
        result = 42
    assert result == 42
    assert {r["id"] for r in rows(telemetry)} == {r["id"] for r in before}
    assert "full" in telemetry.status()["capture_error"]


def test_acknowledged_database_pages_can_be_reused_at_quota(telemetry):
    store = telemetry.store
    context = telemetry.new_context("quota-reuse")
    payload = b"x" * (1024 * 1024)
    store.enqueue(
        "a" * 16, context["trace_id"], context["session_id"], context["destination_id"], payload
    )
    used = store.size()
    store.update("observations", "a" * 16, state="delivered", payload=None)
    assert store.size() < used / 2
    store.limit = used + 65536
    store.enqueue(
        "b" * 16, context["trace_id"], context["session_id"], context["destination_id"], payload
    )
    assert len(rows(telemetry)) == 2


def test_destination_change_preserves_old_queue(telemetry):
    with telemetry.observe("old", root=True):
        pass
    telemetry.configure({"public_key": "pk-lf-another-project"}, "sk-lf-another-secret")
    requests = []
    Delivery(telemetry, httpx.MockTransport(lambda request: requests.append(request))).tick()
    assert not requests and rows(telemetry)[0]["state"] == "pending"


def test_media_bytes_are_uploaded_once_and_attached_to_each_observation(telemetry, tmp_path):
    source = tmp_path / "sample.png"
    source.write_bytes(b"sample-exact-bytes")
    with telemetry.observe("first", root=True) as first:
        first.media(source)
    with telemetry.observe("second", root=True) as second:
        second.media(source)
    puts = []

    def respond(request):
        if request.method == "PUT":
            assert "authorization" not in request.headers
            puts.append(request.content)
            return httpx.Response(200)
        if request.url.path == "/api/public/media":
            return httpx.Response(
                200,
                json={
                    "mediaId": "media-1",
                    "uploadUrl": "https://uploads.example/file?signature=secret",
                },
            )
        return httpx.Response(200, json={})

    delivery = Delivery(telemetry, httpx.MockTransport(respond))
    for _ in range(5):
        delivery.tick()
    assert puts == [b"sample-exact-bytes"]
    assert telemetry.status()["media"] == {"delivered": 2}
    assert len(rows(telemetry)) == 4
    assert not list((telemetry.store.root / "attachments").glob("*"))
    assert source.read_bytes() == b"sample-exact-bytes"


def test_inline_media_and_large_json_have_complete_retained_attachments(telemetry):
    import base64

    content = "data:image/png;base64," + base64.b64encode(b"pixels").decode()
    with telemetry.observe("input", root=True) as stage:
        stage.update(
            input={"messages": [{"image_url": {"url": content}}]},
            output={"long_answer": "x" * 300000},
        )
    record = rows(telemetry)[0]
    assert b"base64" not in record["payload"]
    with telemetry.store.db() as db:
        docs = [json.loads(row[0]) for row in db.execute("SELECT document FROM media")]
    assert len(docs) == 2
    attachment = next(d for d in docs if d["mime"] == "application/json")
    assert (
        json.loads((telemetry.root / attachment["path"]).read_text())["long_answer"] == "x" * 300000
    )


def test_media_rejection_emits_a_linked_failure_observation(telemetry, tmp_path):
    source = tmp_path / "rejected.png"
    source.write_bytes(b"pixels")
    with telemetry.observe("media test", root=True) as root:
        root.media(source, field="input")

    def respond(request):
        return httpx.Response(413 if request.url.path == "/api/public/media" else 200, json={})

    Delivery(telemetry, httpx.MockTransport(respond)).tick()
    failures = [
        span(r)
        for r in rows(telemetry)
        if r["payload"] and span(r)["name"] == "media.upload_failed"
    ]
    assert len(failures) == 1
    attrs = {a["key"]: a["value"] for a in failures[0]["attributes"]}
    assert attrs["session.id"]["stringValue"] == root.context["session_id"]
    assert attrs["langfuse.environment"]["stringValue"] == "development"
    assert b"413" in next(r["payload"] for r in rows(telemetry) if r["payload"])


@pytest.mark.parametrize("status,state", [(401, "blocked"), (429, "pending"), (413, "failed")])
def test_rejected_delivery_is_visible_and_preserved(telemetry, status, state):
    with telemetry.observe("test", root=True):
        pass
    Delivery(
        telemetry,
        httpx.MockTransport(lambda _: httpx.Response(status, headers={"Retry-After": "60"})),
    ).tick()
    row = rows(telemetry)[0]
    assert row["state"] == state and row["payload"]
    assert row["error"]


def test_interrupted_observation_is_recovered_and_delivery_uncertainty_preserved(telemetry):
    from gaiavfx.infrastructure.telemetry.delivery import finished_payload

    context = telemetry.new_context("restart")
    doc = {
        "id": "1" * 16,
        "name": "interrupted",
        "context": context,
        "metadata": {},
        "output": {"partial": "reply"},
    }
    telemetry.store.begin(doc)
    telemetry.store.enqueue(
        "2" * 16,
        context["trace_id"],
        context["session_id"],
        context["destination_id"],
        finished_payload({**doc, "id": "2" * 16}),
    )
    telemetry.store.update("observations", "2" * 16, state="sending")
    delivery = Delivery(telemetry)
    delivery.stop.set()  # Recover synchronously, without a network loop.
    delivery.start()
    delivery.close()
    records = {r["id"]: r for r in rows(telemetry)}
    assert records["2" * 16]["state"] == "uncertain"
    assert b"partial" in records["1" * 16]["payload"]
    assert b"interrupted" in records["1" * 16]["payload"]


def test_successful_reconciliation_does_not_send_again(telemetry):
    with telemetry.observe("test", root=True):
        pass
    row = rows(telemetry)[0]
    telemetry.store.update("observations", row["id"], state="uncertain")

    def respond(request):
        assert request.method == "GET"
        return httpx.Response(200, json={"data": [{"id": row["id"]}]})

    Delivery(telemetry, httpx.MockTransport(respond)).tick()
    assert rows(telemetry)[0]["state"] == "delivered"


def test_configuration_api_is_authenticated_write_only_and_separate(tmp_path, monkeypatch):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from gaiavfx.api.app import create_app

    app = create_app(
        tmp_path, Path(__file__).parents[1] / "migrations", "local-token", enable_runner=False
    )
    monkeypatch.setattr(app.state.workspace.telemetry, "start", lambda: None)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/v1/settings/telemetry").status_code == 401
        client.headers["Authorization"] = "Bearer local-token"
        response = client.put(
            "/api/v1/settings/telemetry",
            json={
                "enabled": True,
                "public_key": "pk-lf-config",
                "secret_key": "sk-lf-config",
            },
        )
        assert response.status_code == 200
        assert "sk-lf-config" not in response.text and '"public_key"' not in response.text
        assert response.json()["has_secret"]
        settings = client.get("/api/v1/settings/connections").json()
        assert not any("langfuse" in key or "telemetry" in key for key in settings)
        assert not rows(app.state.workspace.telemetry)
        rejected = client.put(
            "/api/v1/settings/telemetry", json={"base_url": "https://attacker.example"}
        )
        assert rejected.status_code == 422


def test_rebuilt_routes_trace_ids_without_review_or_model_content(telemetry):
    import asyncio
    from types import SimpleNamespace
    from uuid import uuid4

    from gaiavfx.api.telemetry import TraceMiddleware

    ids = {
        key: str(uuid4())
        for key in ("movie_id", "segment_id", "cut_id", "operation_id", "review_id", "session_id")
    }
    secret = "PRIVATE_REVEAL_AND_MODEL_CONTENT"
    received = []

    async def app(scope, receive, send):
        raw = bytearray()
        while True:
            message = await receive()
            raw.extend(message.get("body", b""))
            if not message.get("more_body"):
                break
        if raw:
            received.append(json.loads(raw))
        result = {
            "id": ids["review_id"],
            "members": [
                {
                    "job_id": secret,
                    "operation_id": secret,
                    "seed": 789123,
                    "technique": secret,
                    "provenance": {"hidden": secret},
                    "request": {"content": secret},
                }
            ],
            "model": {"request": secret},
            "secret": secret,
        }
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"content-type", b"application/json")],
            }
        )
        await send({"type": "http.response.body", "body": json.dumps(result).encode()})

    wrapped = TraceMiddleware(app, SimpleNamespace(telemetry=telemetry), "test-token")

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=wrapped), base_url="http://localhost"
        ) as client:
            for method, path in [
                ("POST", "/api/direction/movies"),
                ("GET", f"/api/production/reviews/{ids['review_id']}/reveal"),
                ("GET", f"/api/production/operations/{ids['operation_id']}"),
                ("POST", f"/api/agent/sessions/{ids['session_id']}/messages"),
                ("GET", "/api/agent/model"),
                ("POST", f"/api/export/movies/{ids['movie_id']}"),
            ]:
                response = await client.request(
                    method,
                    path,
                    headers={"Authorization": "Bearer test-token"},
                    json={
                        **ids,
                        "content": secret,
                        "request": {"messages": [secret]},
                        "api_key": secret,
                    },
                )
                assert response.status_code == 200
                assert "x-gaiavfx-trace-id" in response.headers
                assert secret in response.text  # The client receives the original reveal unchanged.
            await client.post("/api/export/movies/ignored", json={"content": secret})
            await client.get(
                "/api/agentic/not-a-route", headers={"Authorization": "Bearer test-token"}
            )

    asyncio.run(exercise())
    captured = rows(telemetry)
    assert len(captured) == 6
    encoded = b"".join(row["payload"] for row in captured)
    for forbidden in (
        secret,
        "789123",
        "job_id",
        "technique",
        "provenance",
        "api_key",
        "proposal_id",
        "task_id",
    ):
        assert forbidden.encode() not in encoded
    assert all(value.encode() in encoded for value in ids.values())
    assert all(row["session_id"] == captured[0]["session_id"] for row in captured)
    assert all(body["content"] == secret for body in received)
    for row in captured:
        recorded = span(row)
        assert recorded["endTimeUnixNano"] >= recorded["startTimeUnixNano"]


def test_api_exception_content_is_not_traced(telemetry):
    import asyncio
    from types import SimpleNamespace

    from gaiavfx.api.telemetry import TraceMiddleware

    async def app(scope, receive, send):
        raise RuntimeError("PRIVATE_PROVENANCE_ERROR")

    wrapped = TraceMiddleware(app, SimpleNamespace(telemetry=telemetry), "test-token")

    async def exercise():
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=wrapped), base_url="http://localhost"
        ) as client:
            with pytest.raises(RuntimeError, match="PRIVATE_PROVENANCE_ERROR"):
                await client.get("/api/agent/model", headers={"Authorization": "Bearer test-token"})

    asyncio.run(exercise())
    captured = rows(telemetry)
    assert len(captured) == 1
    assert b"PRIVATE_PROVENANCE_ERROR" not in captured[0]["payload"]


@pytest.mark.parametrize(
    "answer,finish,expected_error",
    [
        ('{"answer":"red bag"}', "stop", None),
        ('{"unfinished":', "stop", "invalid JSON"),
        ('{"unfinished":', "length", "output allowance"),
    ],
)
def test_remote_raw_model_answers_survive_parser_failures(
    telemetry, tmp_path, answer, finish, expected_error
):
    from gaiavfx.infrastructure.assistant_gateway import OpenAICompatibleAssistant
    from gaiavfx.shared.assistant import AssistantConnection
    from gaiavfx.shared.errors import ApplicationError

    connection = AssistantConnection(
        id="remote", name="Remote", base_url="https://models.example/v1", model="qwen"
    ).model_dump()
    config = {"assistant_connections": [connection], "assistant_agent_connection": "remote"}
    response = {
        "model": "actual-model",
        "choices": [
            {
                "finish_reason": finish,
                "message": {"content": answer, "reasoning_content": "provider exposed reasoning"},
            }
        ],
        "usage": {"prompt_tokens": 30, "completion_tokens": 12, "total_tokens": 42},
    }
    assistant = OpenAICompatibleAssistant(
        telemetry.credentials, httpx.MockTransport(lambda _: httpx.Response(200, json=response))
    )
    with telemetry.observe("refine", root=True):
        if expected_error:
            with pytest.raises(ApplicationError, match=expected_error):
                assistant.run(
                    config,
                    [{"type": "text", "text": "Exact authored request"}],
                    threading.Event(),
                    tmp_path,
                )
        else:
            result = assistant.run(
                config,
                [{"type": "text", "text": "Exact authored request"}],
                threading.Event(),
                tmp_path,
            )
            assert result["answer"] == "red bag"
    model = next(span(r) for r in rows(telemetry) if operation(span(r)) == "model.complete")
    attrs = {a["key"]: a["value"] for a in model["attributes"]}
    output = json.loads(attrs["langfuse.observation.output"]["stringValue"])
    assert output == response
    assert "Exact authored request" in attrs["langfuse.observation.input"]["stringValue"]
    assert json.loads(attrs["langfuse.observation.usage_details"]["stringValue"])["total"] == 42


def test_interrupted_stream_keeps_all_received_chunks(telemetry, tmp_path):
    from gaiavfx.infrastructure.assistant_gateway import OpenAICompatibleAssistant
    from gaiavfx.shared.assistant import AssistantConnection
    from gaiavfx.shared.errors import ApplicationError

    config = {
        "assistant_connections": [
            AssistantConnection(
                id="stream",
                name="stream",
                base_url="https://models.example/v1",
                model="qwen",
                streaming=True,
            ).model_dump()
        ],
        "assistant_agent_connection": "stream",
    }
    events = [
        {"choices": [{"delta": {"content": '{"partial":'}}]},
        {"error": {"message": "Provider interrupted"}},
    ]
    transport = httpx.MockTransport(
        lambda _: httpx.Response(
            200, text="".join("data: " + json.dumps(e) + "\n\n" for e in events)
        )
    )
    with (
        telemetry.observe("refine", root=True),
        pytest.raises(ApplicationError, match="interrupted"),
    ):
        OpenAICompatibleAssistant(telemetry.credentials, transport).run(
            config, [], threading.Event(), tmp_path
        )
    model = next(span(r) for r in rows(telemetry) if operation(span(r)) == "model.complete")
    attrs = {a["key"]: a["value"] for a in model["attributes"]}
    assert (
        json.loads(attrs["langfuse.observation.output"]["stringValue"])["stream_events"] == events
    )
    assert attrs["langfuse.observation.level"]["stringValue"] == "ERROR"


@pytest.mark.parametrize("answer", ['{"answer":"exact whitespace"}', '{"broken":'])
def test_local_qwen_response_is_captured_before_json_validation(
    telemetry, tmp_path, monkeypatch, answer
):
    from unittest.mock import Mock

    from gaiavfx.infrastructure import assistant as local
    from gaiavfx.shared.errors import ApplicationError

    identity = {"model": "fixture"}
    monkeypatch.setattr(local, "resource_identity", lambda settings: identity)
    response = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"content": answer, "reasoning_content": "exposed thought"},
            }
        ],
        "usage": {"prompt_tokens": 4, "completion_tokens": 6},
    }

    def respond(request):
        if request.url.path.endswith("completions"):
            return httpx.Response(200, json=response)
        if request.url.path == "/apply-template":
            return httpx.Response(200, json={"prompt": "template"})
        if request.url.path == "/tokenize":
            return httpx.Response(200, json={"tokens": [1, 2]})
        return httpx.Response(200, json={})

    real_client = httpx.Client
    monkeypatch.setattr(
        local.httpx,
        "Client",
        lambda **kwargs: real_client(**{"transport": httpx.MockTransport(respond), **kwargs}),
    )
    model = local.LocalAssistant()
    model.child = Mock()
    model.child.poll.return_value = None
    model.runtime = (json.dumps([identity, 8192], sort_keys=True), 19119)
    config = {
        "assistant_context": 8192,
        "assistant_model": "fixture.gguf",
        "assistant_executable": "fixture.exe",
        "assistant_projector": "fixture.gguf",
    }
    with telemetry.observe("local", root=True):
        if answer.endswith(":"):
            # Malformed JSON becomes the gateway's retryable error (the harness retries once).
            with pytest.raises(ApplicationError, match="invalid JSON"):
                model.run(config, [], threading.Event(), tmp_path / "local.log", keep_alive=True)
        else:
            assert (
                model.run(config, [], threading.Event(), tmp_path / "local.log", keep_alive=True)[
                    "answer"
                ]
                == "exact whitespace"
            )
    model_span = next(span(r) for r in rows(telemetry) if operation(span(r)) == "model.complete")
    attrs = {a["key"]: a["value"] for a in model_span["attributes"]}
    assert json.loads(attrs["langfuse.observation.output"]["stringValue"]) == response
