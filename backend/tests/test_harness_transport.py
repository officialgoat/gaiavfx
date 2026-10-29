"""Managed-local transport recovery: all gateways/processes/GPU access are stubs."""

import errno
import sys
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

from harness_eval import provider_failed
from harness_eval_live import RecordedAssistant

from gaiavfx.application.harness.model import AssistantModel, connection_dropped
from gaiavfx.shared.errors import ApplicationError


@pytest.mark.parametrize(
    "failure",
    [
        ConnectionResetError(errno.ECONNRESET, "reset"),
        ConnectionRefusedError(errno.ECONNREFUSED, "refused"),
        httpx.ReadError(
            "[WinError 10054] An existing connection was forcibly closed by the remote host"
        ),
        httpx.ConnectError("[WinError 10061] No connection could be made"),
    ],
)
def test_managed_call_restarts_once_and_retries_the_identical_six_frame_read(tmp_path, failure):
    calls, stops = [], []

    def run(*args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) == 1:
            raise failure
        return {"notes": "Six samples read.", "metrics": {"usage": {"prompt_tokens": 123}}}

    model = AssistantModel(SimpleNamespace(run=run, stop=lambda: stops.append(True)), None)
    model.local = True  # Do not load a model, acquire a GPU lease or contact ComfyUI.
    messages = [
        {
            "role": "user",
            "content": [{"type": "image_url", "image_url": {"url": "data:image/jpeg;base64,TEST"}}]
            * 6,
        }
    ]
    schema = {"type": "object", "properties": {"notes": {"type": "string"}}}
    reply = model.call({}, messages, schema, lambda: False, tmp_path / "read.log")
    assert reply.raw == {"notes": "Six samples read."}
    assert reply.metrics["server_restarts"] == 1 and reply.input_tokens == 123
    assert len(calls) == 2 and stops == [True]
    assert calls[0][1] == calls[1][1] and calls[0][1]["messages"] is messages
    assert calls[0][0][0] == calls[1][0][0] and calls[0][0][3] == calls[1][0][3]
    assert model.status()["state"] == "ready"


def test_second_drop_remains_a_provider_failure_and_keeps_restart_metrics(tmp_path):
    attempts, stops = [], []

    def run(*args, **kwargs):
        attempts.append(True)
        raise ConnectionResetError(errno.ECONNRESET, "reset")

    recorder = SimpleNamespace(calls=[])
    model = RecordedAssistant(
        SimpleNamespace(run=run, stop=lambda: stops.append(True)), None, recorder
    )
    model.local = True
    with pytest.raises(ConnectionResetError):
        model.call({}, [], {}, lambda: False, tmp_path / "read.log")
    assert len(attempts) == 2 and len(stops) == 1
    assert len(recorder.calls) == 1  # one harness call, one internal retry
    assert recorder.calls[0]["metrics"]["server_restarts"] == 1
    assert model.failure and provider_failed({"provider_ok": False, "error": model.failure})


@pytest.mark.parametrize(
    ("local", "cancelled", "error"),
    [
        (False, False, ConnectionResetError("reset")),
        (True, True, ConnectionRefusedError("refused")),
        (True, False, httpx.ReadTimeout("timeout")),
        (True, False, ApplicationError("HTTP 503")),
        (True, False, ApplicationError("The assistant returned invalid JSON")),
    ],
)
def test_no_retry_for_remote_cancellation_timeout_or_model_errors(
    tmp_path, local, cancelled, error
):
    calls = []

    def run(*args, **kwargs):
        calls.append(True)
        raise error

    model = AssistantModel(
        SimpleNamespace(run=run, stop=lambda: pytest.fail("must not restart")), None
    )
    model.local = local
    with pytest.raises(type(error)):
        model.call({}, [], {}, lambda: cancelled, tmp_path / "read.log")
    assert len(calls) == 1 and model.call_metrics["server_restarts"] == 0


def test_error_cause_is_inspected_without_looping():
    outer = httpx.ReadError("transport failed")
    outer.__cause__ = ConnectionResetError(errno.ECONNRESET, "reset")
    assert connection_dropped(outer)
    outer.__cause__ = outer
    assert not connection_dropped(outer)
