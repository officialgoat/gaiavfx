import json
import shutil
import threading

import httpx
import pytest

from gaiavfx.infrastructure.assistant_gateway import AssistantGateway, OpenAICompatibleAssistant
from gaiavfx.infrastructure.credentials import CredentialStore
from gaiavfx.shared.assistant import AssistantConnection, connection_for
from gaiavfx.shared.errors import ApplicationError


def settings(**overrides):
    connection = AssistantConnection(
        id="remote",
        name="Compatible API",
        base_url="http://127.0.0.1:9000/v1",
        model="vision-model",
        **overrides,
    ).model_dump()
    return {
        "assistant_connections": [connection],
        "assistant_agent_connection": "remote",
    }


def test_portable_credentials_require_unlock_and_never_store_plaintext(tmp_path):
    store = CredentialStore(tmp_path / "original")
    store.unlock("correct passphrase")
    store.save("remote", "private-api-key-value")
    assert store.get("remote") == "private-api-key-value"
    assert "private-api-key-value" not in store.path.read_text()
    assert "correct passphrase" not in store.path.read_text()
    shutil.copytree(tmp_path / "original", tmp_path / "moved")
    moved = CredentialStore(tmp_path / "moved")
    with pytest.raises(ApplicationError, match="Unlock"):
        moved.get("remote")
    with pytest.raises(ApplicationError, match="passphrase"):
        moved.unlock("incorrect passphrase")
    assert not moved.status()["unlocked"]
    moved.unlock("correct passphrase")
    assert moved.get("remote") == "private-api-key-value"
    moved.save("remote", "")
    assert moved.status()["credential_ids"] == []


def test_compatible_wire_omits_local_parameters_and_credentials(tmp_path):
    store = CredentialStore(tmp_path)
    store.unlock("correct passphrase")
    store.save("remote", "private-api-key-value")
    requests = []

    def respond(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer private-api-key-value"
        return httpx.Response(
            200,
            json={
                "model": "resolved-model",
                "choices": [
                    {"finish_reason": "stop", "message": {"content": '{"observations":[]}'}}
                ],
                "usage": {"total_tokens": 42},
            },
        )

    assistant = OpenAICompatibleAssistant(store, httpx.MockTransport(respond))
    config = settings(credential_id="remote")
    content = [
        {"type": "text", "text": "Observe."},
        {"type": "image_url", "image_url": {"url": "data:image/png;base64,fixture"}},
    ]
    result = assistant.run(config, content, threading.Event(), tmp_path, {"type": "object"})
    body = json.loads(requests[0].content)
    assert str(requests[0].url) == "http://127.0.0.1:9000/v1/chat/completions"
    assert body["messages"][1]["content"] == content
    assert body["response_format"]["type"] == "json_schema"
    assert not {"top_k", "chat_template_kwargs", "seed"} & body.keys()
    assert "private-api-key-value" not in json.dumps(result)
    assert result["model"] == "resolved-model"
    assert connection_for(config)["provider"] == "openai_compatible"
    assert connection_for({})["provider"] == "managed_local"


def test_multi_turn_string_messages_reach_the_provider(tmp_path):
    """The agent sends a conversation, not one parts-bearing user message.

    The request recorder assumed a parts list at ``messages[1]["content"]``, so every
    remote agent turn failed with ``'str' object has no attribute 'get'`` before the
    reply could be parsed. No agent session had ever run on a remote connection.
    """
    seen = []

    def respond(request):
        seen.append(json.loads(request.content))
        return httpx.Response(
            200,
            json={
                "model": "resolved-model",
                "choices": [
                    {
                        "finish_reason": "stop",
                        "message": {"content": '{"thought":"look","tool":"read_movie","input":{}}'},
                    }
                ],
                "usage": {"prompt_tokens": 20, "completion_tokens": 8},
            },
        )

    assistant = OpenAICompatibleAssistant(CredentialStore(tmp_path), httpx.MockTransport(respond))
    messages = [
        {"role": "system", "content": "You author a segment."},
        {"role": "user", "content": "Whiteboard:\n(empty)\n\nCurrent view:\nMovie 'Harbor'"},
        {"role": "user", "content": "Author the second phase."},
        {"role": "assistant", "content": '{"tool":"read_segment"}'},
    ]
    result = assistant.run(
        settings(),
        None,
        threading.Event(),
        tmp_path,
        response_schema={"oneOf": [{"type": "object"}]},
        messages=messages,
    )
    assert result["tool"] == "read_movie"
    assert seen[0]["messages"] == messages
    assert [m["content"] for m in result["request"]["messages"]] == [m["content"] for m in messages]


@pytest.mark.parametrize("status", [400, 401, 403, 429, 503])
def test_provider_error_does_not_leak_body_or_retry(tmp_path, status):
    seen = []

    def respond(request):
        seen.append(request)
        return httpx.Response(status, text="secret echoed by provider")

    assistant = OpenAICompatibleAssistant(CredentialStore(tmp_path), httpx.MockTransport(respond))
    with pytest.raises(ApplicationError, match=f"HTTP {status}") as error:
        assistant.run(settings(), [], threading.Event(), tmp_path)
    assert len(seen) == 1 and "secret" not in str(error.value)


@pytest.mark.parametrize("content", ["[]", "not json", '{"unfinished":'])
def test_invalid_json_is_recoverable(tmp_path, content):
    transport = httpx.MockTransport(
        lambda _: httpx.Response(200, json={"choices": [{"message": {"content": content}}]})
    )
    with pytest.raises(ApplicationError, match="invalid JSON"):
        OpenAICompatibleAssistant(CredentialStore(tmp_path), transport).run(
            settings(), [], threading.Event(), tmp_path
        )


def test_cancelled_and_nonvision_requests_are_not_sent(tmp_path):
    def unexpected(_):
        pytest.fail("No network request expected")

    assistant = OpenAICompatibleAssistant(
        CredentialStore(tmp_path), httpx.MockTransport(unexpected)
    )
    cancelled = threading.Event()
    cancelled.set()
    with pytest.raises(ApplicationError, match="stopped"):
        assistant.run(settings(), [], cancelled, tmp_path)
    with pytest.raises(ApplicationError, match="vision"):
        assistant.run(settings(vision=False), [{"type": "image_url"}], threading.Event(), tmp_path)
    with pytest.raises(ApplicationError, match="context budget"):
        assistant.run(
            settings(context_tokens=4096),
            [{"type": "text", "text": "x" * 12000}],
            threading.Event(),
            tmp_path,
        )


def test_inflight_remote_call_can_be_cancelled_without_retry(tmp_path):
    import asyncio
    import time

    cancellation = threading.Event()
    attempts = []

    async def delayed(request):
        attempts.append(request)
        cancellation.set()
        await asyncio.sleep(10)
        return httpx.Response(200, json={})

    assistant = OpenAICompatibleAssistant(CredentialStore(tmp_path), httpx.MockTransport(delayed))
    started = time.monotonic()
    with pytest.raises(ApplicationError, match="stopped"):
        assistant.run(settings(), [], cancellation, tmp_path)
    assert time.monotonic() - started < 2
    assert len(attempts) == 1


def test_gateway_routes_remote_calls_without_starting_local_runtime(tmp_path, monkeypatch):
    gateway = AssistantGateway(CredentialStore(tmp_path))
    seen = []
    monkeypatch.setattr(
        gateway.local, "run", lambda *a, **k: pytest.fail("No local inference expected")
    )
    monkeypatch.setattr(gateway.remote, "run", lambda *a, **k: seen.append(a[0]) or {"ok": True})
    assert gateway.run(settings(), [], threading.Event(), tmp_path) == {"ok": True}
    assert seen == [settings()]


def test_credential_api_is_authenticated_and_write_only(tmp_path):
    from pathlib import Path

    from fastapi.testclient import TestClient

    from gaiavfx.api.app import create_app
    from gaiavfx.modules.library.application import LibraryService

    migrations = Path(__file__).parents[1] / "migrations"
    LibraryService(tmp_path, migrations).create("Credential API")
    app = create_app(tmp_path, migrations, "test-token", acquire_lock=False, enable_runner=False)
    with TestClient(app, base_url="http://localhost") as client:
        url = "/api/v1/settings/assistant/credentials"
        assert client.get(url).status_code == 401
        client.headers["Authorization"] = "Bearer test-token"
        assert client.put(url, json={"id": "api", "value": "secret-key"}).status_code == 422
        assert (
            client.post(url + "/unlock", json={"passphrase": "correct passphrase"}).status_code
            == 200
        )
        saved = client.put(url, json={"id": "api", "value": "secret-key"})
        assert saved.status_code == 200
        assert saved.json()["credential_ids"] == ["api"]
        assert "secret-key" not in saved.text
        assert "secret-key" not in client.get("/api/v1/settings/connections").text
        assert client.post(url + "/lock").json()["unlocked"] is False


def test_a_complete_answer_is_kept_even_at_the_output_allowance(tmp_path):
    """Padding past a finished answer is not a reason to throw the answer away.

    Some providers keep emitting after a schema-constrained response is complete, so the
    finish reason is "length" although the JSON parses. Three recorded agent sessions were
    killed mid-run while holding a usable action. Output that does not parse still reports
    the allowance, because there the truncation is real.
    """
    complete = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "model": "resolved-model",
                "choices": [
                    {
                        "finish_reason": "length",
                        "message": {"content": '{"thought":"go","tool":"read_movie","input":{}}'},
                    }
                ],
                "usage": {"prompt_tokens": 10, "completion_tokens": 4096},
            },
        )
    )
    result = OpenAICompatibleAssistant(CredentialStore(tmp_path), complete).run(
        settings(), [], threading.Event(), tmp_path
    )
    assert result["tool"] == "read_movie"
    assert result["metrics"]["finish_reason"] == "length"

    truncated = httpx.MockTransport(
        lambda _: httpx.Response(
            200,
            json={
                "choices": [{"finish_reason": "length", "message": {"content": '{"tool":"read_mo'}}]
            },
        )
    )
    with pytest.raises(ApplicationError, match="exhausted its output allowance") as error:
        OpenAICompatibleAssistant(CredentialStore(tmp_path), truncated).run(
            settings(), [], threading.Event(), tmp_path / "agent.log"
        )
    # How much came back, so a near-complete answer and a page of prose that was never
    # going to parse are not the same line in a journal.
    assert "16 characters" in str(error.value)
    # The diagnostic used to be written only when the answer stopped for some other
    # reason, so the failure that most needs explaining recorded nothing at all.
    recorded = json.loads((tmp_path / "agent.invalid-response.json").read_text(encoding="utf-8"))
    assert recorded["finish_reason"] == "length"
    assert recorded["raw_response"] == '{"tool":"read_mo'
