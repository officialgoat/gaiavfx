"""S7h: long conversations preserve current work and fit the transport budget."""

import json

import pytest

from gaiavfx.application.harness.board import Whiteboard
from gaiavfx.application.harness.context import assemble, turn_messages
from gaiavfx.shared.errors import ApplicationError


def pack(turns, **kwargs):
    return assemble(
        "System",
        "Brief",
        "Tools",
        {},
        Whiteboard(),
        "Active plan: create cut; pending review A/B",
        turn_messages(turns),
        32768,
        output=2400,
        summary="Earlier decisions and receipts",
        **kwargs,
    )


def test_forty_messages_two_hundred_turns_every_request_fits():
    turns = []
    dropped = 0
    for message in range(40):
        turns.append(
            {
                "role": "human",
                "state": "accepted",
                "action": {
                    "message": f"Message {message}: preserve this whole request " + "雨🌧" * 200,
                    "attachments": [{"asset_id": "abc", "kind": "image", "name": "Courtyard"}],
                },
            }
        )
        for step in range(5):
            turns.append(
                {
                    "role": "agent",
                    "state": "accepted",
                    "action": {"tool": "read_movie", "input": {"step": step}},
                    "result": {"payload": "record 01234567-89ab-cdef-0123-456789abcdef\n" * 24},
                }
            )
            history = turn_messages(turns)
            result = pack(turns)
            current = history[history.starts[-1] :]
            assert result.messages[-len(current) - 1 : -1] == current
            assert result.report["total_reserved"] <= 32768
            transport = 512 + sum(
                len(json.dumps(m, ensure_ascii=False).encode()) // 3 for m in result.messages
            )
            assert transport + 2400 + 256 <= 32768
            assert result.messages[2]["content"].startswith("Summary so far:")
            assert "Active plan" in result.messages[-1]["content"]
            assert "pending review" in result.messages[-1]["content"]
            if result.report["dropped_messages"]:
                dropped += 1
                start = history.starts[result.report["dropped_messages"]]
                assert result.messages[3:-1] == history[start:]
                assert result.report["dropped_turns"] == start
    assert len(turns) == 240 and dropped > 100


def test_current_message_too_large_refuses_before_send_without_truncation():
    with pytest.raises(ApplicationError, match="They were not truncated"):
        pack([{"role": "human", "state": "accepted", "action": {"message": "x" * 70000}}])


def test_all_active_steps_and_pending_reviews_survive_large_view():
    from gaiavfx.application.harness.context import VIEW_CEILING, estimate
    from gaiavfx.application.harness.session import AgentSession, Plan, PlanStep
    from gaiavfx.application.harness.view import render_view
    from gaiavfx.application.harness.world import WorldState

    doc = AgentSession(
        plan=Plan(
            id="plan",
            revision=1,
            state="executing",
            goal="Film",
            proposed_turn=0,
            budget_jobs=0,
            steps=[PlanStep(kind="edit", title="🌧" * 200) for _ in range(12)],
        )
    )
    state = WorldState(
        movie={"id": "movie", "revision": 1, "title": "🌧" * 120},
        operations=[
            {
                "id": f"pending-{i:036}",
                "kind": "survey",
                "review_set_id": f"review-{i:036}",
                "review_pending": True,
            }
            for i in range(24)
        ],
    )
    view = render_view(state, "off", 0, 0, document=doc)
    assert estimate(view) <= VIEW_CEILING
    assert all(f"step {i} [edit]" in view for i in range(12))
    assert all(f"review-{i:036}" in view for i in range(24))


def test_whole_messages_drop_before_any_partial_turn():
    old = {"role": "human", "state": "accepted", "action": {"message": "old " * 20000}}
    newest = {"role": "human", "state": "accepted", "action": {"message": "current " * 1000}}
    result = pack([old, newest])
    assert result.report["dropped_messages"] == 1
    assert result.messages[-2]["content"] == newest["action"]["message"]


def test_runtime_tokenizer_backstop_drops_only_older_messages():
    from types import SimpleNamespace

    from gaiavfx.application.harness.context import call_with_window

    turns = [
        {"role": "human", "state": "accepted", "action": {"message": f"human {i}"}}
        for i in range(3)
    ]
    assembly = pack(turns)
    reports = []

    def call(settings, messages, schema, cancelled, log_path):
        if any(m["content"] == "human 0" for m in messages):
            raise ApplicationError(
                "Request budget 33649 exceeds context 32768: text 30993 (runtime_text_template)"
            )
        return "reply"

    assert (
        call_with_window(
            SimpleNamespace(call=call), {}, assembly, {}, lambda: False, None, reports.append
        )
        == "reply"
    )
    assert assembly.messages[-2]["content"] == "human 2"
    assert reports[0]["dropped_messages"] == reports[0]["tokenizer_rewindows"] == 1
    assert "Active plan" in assembly.messages[-1]["content"]


def test_runtime_tokenizer_cannot_drop_current_message_and_other_errors_are_not_retried():
    from types import SimpleNamespace

    from gaiavfx.application.harness.context import call_with_window

    def too_large(*args):
        raise ApplicationError("Request budget 33649 exceeds context 32768")

    assembly = pack([{"role": "human", "state": "accepted", "action": {"message": "current"}}])
    with pytest.raises(ApplicationError, match="They were not truncated"):
        call_with_window(
            SimpleNamespace(call=too_large), {}, assembly, {}, lambda: False, None, lambda _: None
        )
    assert assembly.messages[-2]["content"] == "current"

    def unrelated(*args):
        raise ApplicationError("The assistant exhausted its output allowance")

    with pytest.raises(ApplicationError, match="output allowance"):
        call_with_window(
            SimpleNamespace(call=unrelated), {}, assembly, {}, lambda: False, None, lambda _: None
        )


def test_live_eval_records_recovered_admission_separately_from_served_calls(monkeypatch):
    from types import SimpleNamespace

    from harness_eval_live import RecordedAssistant

    from gaiavfx.application.harness.context import call_with_window
    from gaiavfx.application.harness.model import AssistantModel

    def call(self, settings, messages, schema, cancelled, log_path):
        if any(m["content"] == "old" for m in messages):
            raise ApplicationError("Request budget 33649 exceeds context 32768")
        return SimpleNamespace(raw={}, input_tokens=10, output_tokens=2, metrics={})

    monkeypatch.setattr(AssistantModel, "call", call)
    recorder = SimpleNamespace(calls=[])
    model = RecordedAssistant(None, None, recorder)
    assembly = pack(
        [{"role": "human", "state": "accepted", "action": {"message": m}} for m in ("old", "new")]
    )
    call_with_window(model, {}, assembly, {}, lambda: False, None, lambda _: None)
    assert model.failure == ""
    assert len(recorder.calls) == 1 and recorder.calls[0]["conforms"]
    assert model.admission_refusals == [
        {"before_call": 0, "error": "Request budget 33649 exceeds context 32768"}
    ]
