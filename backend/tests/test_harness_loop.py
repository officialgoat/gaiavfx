"""Recovery, live human controls and one-action turn guards on the new kernel."""

from contextlib import contextmanager

import harness_support
import pytest
from harness_support import action, run

from gaiavfx.application.harness.actions import Tool, ToolResult
from gaiavfx.application.harness.board import Whiteboard
from gaiavfx.application.harness.tools import DEFAULT_TOOLS
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError

kernel = harness_support.kernel


def test_requested_before_each_call_and_no_transaction_across_model(kernel, monkeypatch):
    k = kernel
    depth = 0
    original = k.db.transaction

    @contextmanager
    def tracked():
        nonlocal depth
        with original() as session:
            depth += 1
            try:
                yield session
            finally:
                depth -= 1

    monkeypatch.setattr(k.db, "transaction", tracked)

    def reply(messages, schema):
        assert depth == 0
        assert k.harness.turns(k.id)[-1]["state"] == "requested"
        assert k.harness.read(k.id)["document"]["budgets"]["turns_spent"] == 1
        return action("say", text="Ready.")

    assert run(k, reply)["state"] == "idle"
    assert len(k.model.calls) == 1
    assert not k.model.active and k.model.begins == k.model.ends == 1


def test_malformed_retries_and_repeated_invalid_yields(kernel):
    k = kernel
    row = run(k, {"not_an_action": 1}, action("say", text="Corrected."))
    assert row["state"] == "idle"
    turns = k.harness.turns(k.id)
    assert (
        turns[1]["state"] == "rejected" and "Malformed action" in turns[1]["result"]["discrepancy"]
    )
    row = run(k, action("invented"), action("invented"))
    assert row["state"] == "waiting_human"
    assert row["document"]["stop_reason"] == "repeated invalid action"


class Echo(Tool):
    name = "echo"
    description = "Echo a note."

    class Input(StrictModel):
        text: str

    def run(self, context, value):
        return ToolResult(payload={"text": value.text})


class Refuse(Echo):
    name = "refuse"

    def run(self, context, value):
        return ToolResult(ok=False, discrepancy=value.text)


def test_repeated_valid_gets_one_correction_then_yields(kernel):
    k = kernel
    k.harness.tools = (*DEFAULT_TOOLS, Echo())
    row = run(k, *[action("echo", text="same")] * 3)
    assert row["state"] == "waiting_human" and row["document"]["stop_reason"] == "repeated action"
    assert (
        sum(
            (t["provenance"] or {}).get("reason") == "repeat correction"
            for t in k.harness.turns(k.id)
        )
        == 1
    )


def test_stagnation_two_unchanged_or_repeated_identical_refusals(kernel):
    k = kernel
    k.harness.tools = (*DEFAULT_TOOLS, Echo(), Refuse())
    assert (
        run(k, action("echo", text="one"), action("echo", text="two"))["document"]["stop_reason"]
        == "no state change"
    )
    before = len(k.model.calls)
    row = run(
        k,
        *[action("refuse", text=str(i)) for i in range(4)],
        action("say", text="The intended shot needs a decision."),
    )
    assert row["state"] == "idle" and len(k.model.calls) - before == 5
    row = run(k, *[action("refuse", text="same problem")] * 3)
    assert row["state"] == "waiting_human" and row["document"]["stop_reason"] == "repeated action"


def test_turn_budget_stops_and_board_changes_are_adopted(kernel):
    k = kernel
    with k.db.transaction() as session:
        doc = k.row["document"]
        doc["budgets"]["max_turns_per_message"] = 1
        k.store.update_session(session, k.id, 1, state="idle", document=doc)
    row = run(k, action("read_movie"))
    assert row["state"] == "stopped" and "turn budget" in row["document"]["stop_reason"]
    row["document"]["budgets"]["max_turns_per_message"] = 24
    with k.db.transaction() as session:
        k.store.update_session(
            session, k.id, row["revision"], state="idle", document=row["document"]
        )

    def edit(messages, schema):
        current = k.harness.read(k.id)
        k.harness.edit_board(k.id, current["revision"], Whiteboard(brief="Keep the blue coat."))
        return action("read_movie")

    row = run(k, edit, action("say", text="Noted."))
    assert row["document"]["board"]["brief"] == "Keep the blue coat."
    assert "Keep the blue coat." in str(k.model.calls[-1][0])


def test_cancel_before_dispatch_and_ask_waits(kernel):
    k = kernel

    def cancel(messages, schema):
        k.harness.request_cancel(k.id)
        return action("board_write", section="decisions", text="Must not be written")

    row = run(k, cancel)
    assert row["state"] == "stopped" and row["document"]["stop_reason"] == "cancelled by the user"
    assert not row["document"]["board"]["decisions"]
    row = run(k, action("ask_human", question="Which place?"))
    assert row["state"] == "waiting_human" and not k.model.active


def test_gateway_error_after_cancellation_stops_and_other_errors_fail(kernel):
    k = kernel

    def cancel(messages, schema):
        k.harness.request_cancel(k.id)
        raise RuntimeError("The gateway was cancelled")

    row = run(k, cancel)
    assert row["state"] == "stopped" and row["document"]["stop_reason"] == "cancelled by the user"
    assert len(k.model.calls) == 1 and not k.model.active
    assert k.harness.turns(k.id)[-1]["state"] == "failed"

    def fail(messages, schema):
        raise RuntimeError("Provider unavailable")

    row = run(k, fail)
    assert row["state"] == "failed" and "Provider unavailable" in row["document"]["stop_reason"]
    assert len(k.model.calls) == 2 and not k.model.active


def test_crash_orphan_fails_closed_then_next_message_resumes(kernel):
    class Crash(BaseException):
        pass

    k = kernel

    def crash(messages, schema):
        raise Crash()

    with pytest.raises(Crash):
        run(k, crash)
    assert not k.model.active
    assert k.harness.turns(k.id)[-1]["state"] == "requested"
    k.runner.recover()
    k.runner.run_once()
    row = k.harness.read(k.id)
    assert (
        row["state"] == "failed"
        and row["document"]["stop_reason"] == "interrupted before its turn was retained"
    )
    assert row["document"]["budgets"]["turns_spent"] == 1
    assert len(k.model.calls) == 1
    assert run(k, action("say", text="Resumed."))["state"] == "idle"


@pytest.mark.parametrize(
    "prefix",
    ["The assistant returned invalid JSON", "The assistant exhausted its output allowance"],
)
def test_gateway_malformed_reply_retries_once_per_message(kernel, prefix):
    k = kernel

    def malformed(*args):
        assert k.harness.turns(k.id)[-1]["state"] == "requested"
        assert k.harness.payload(k.harness.read(k.id))["activity"] == "thinking"
        raise ApplicationError(prefix + ".")

    row = run(k, malformed, action("say", text="Corrected"))
    assert row["state"] == "idle" and row["document"]["model_retries"] == 1
    turns = k.harness.turns(k.id)
    assert [t["state"] for t in turns] == ["accepted", "rejected", "accepted", "accepted"]
    assert (
        turns[2]["action"]["message"]
        == "Your last reply was not one valid action. Answer with exactly one tool call."
    )
    assert row["document"]["budgets"]["request_turns"] == 2
    row = run(k, malformed, malformed)
    assert row["state"] == "failed" and row["document"]["model_retries"] == 1
    assert len(k.model.calls) == 4
    assert k.harness.turns(k.id)[-1]["state"] == "rejected"
    assert row["document"]["summary"].startswith("Messages: 2")


def test_retry_counts_against_turn_budget(kernel):
    k = kernel
    with k.db.transaction() as session:
        doc = k.row["document"]
        doc["budgets"]["max_turns_per_message"] = 1
        k.store.update_session(session, k.id, k.row["revision"], state="idle", document=doc)

    def malformed(*args):
        raise ApplicationError("The assistant returned invalid JSON.")

    row = run(k, malformed)
    assert row["state"] == "stopped" and len(k.model.calls) == 1
    assert "turn budget" in row["document"]["stop_reason"]


def test_interrupted_retry_transaction_leaves_an_orphan_that_fails_closed(kernel, monkeypatch):
    k = kernel

    class Crash(BaseException):
        pass

    original = k.store.update_session

    def interrupted(*args, **kwargs):
        if kwargs["document"]["model_retries"]:
            raise Crash()
        return original(*args, **kwargs)

    def malformed(*args):
        raise ApplicationError("The assistant returned invalid JSON.")

    monkeypatch.setattr(k.store, "update_session", interrupted)
    with pytest.raises(Crash):
        run(k, malformed)
    assert [t["state"] for t in k.harness.turns(k.id)] == ["accepted", "requested"]
    monkeypatch.setattr(k.store, "update_session", original)
    k.runner.recover()
    assert k.runner.run_once()
    assert len(k.model.calls) == 1 and k.harness.read(k.id)["state"] == "failed"


def test_activity_and_summary_at_terminal_and_guard_yield(kernel):
    k = kernel
    from gaiavfx.application.harness.session import AgentSession

    document = AgentSession()
    for state in ("queued", "waiting_operation", "waiting_human", "idle", "stopped", "failed"):
        assert k.harness.activity({"state": state}, document, []) == state
    assert k.harness.activity({"state": "running"}, document, []) == "running_tool"
    row = run(k, action("say", text="Answer"))
    assert "Messages: 1" in row["document"]["summary"]
    assert "Work on the movie." in row["document"]["summary"]
    row = run(k, action("invented"), action("invented"))
    assert row["state"] == "waiting_human" and "Messages: 2" in row["document"]["summary"]
    assert k.model.calls[-1][0][2]["content"].startswith("Summary so far:")
    assert k.harness.turns(k.id)[-2]["provenance"]["context"]["summary"] > 0
