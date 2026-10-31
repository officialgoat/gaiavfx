"""A conversation's durable record: turns in order, Gaia's saves keep what the user changed meanwhile, a human
command's save keeps its own decision, and leaving running refreshes the summary."""

from harness_support import action, kernel, run  # noqa: F401 (fixture)

from gaiavfx.application.harness import loop
from gaiavfx.application.harness.board import Whiteboard
from gaiavfx.application.harness.session import AgentSession


def stored(kernel):  # noqa: F811
    return AgentSession.model_validate(kernel.harness.read(kernel.id)["document"])


def edit_board(kernel, brief):  # noqa: F811
    row = kernel.harness.read(kernel.id)
    board = stored(kernel).board.model_copy(update={"brief": brief})
    return kernel.harness.edit_board(kernel.id, row["revision"], board)


def test_turns_are_appended_in_order(kernel):  # noqa: F811
    with kernel.db.transaction() as session:
        record = kernel.harness._record(session, kernel.id)
        first = record.turn("human", action={"message": "One."})
        second = record.turn("system", action={"message": "Two."})
    assert (first["ordinal"], second["ordinal"]) == (0, 1)


def test_gaias_save_keeps_a_newer_board_and_a_cancel(kernel):  # noqa: F811
    document = stored(kernel)
    edit_board(kernel, "The user's brief")
    kernel.harness.request_cancel(kernel.id)
    with kernel.db.transaction() as session:
        record = kernel.harness._record(session, kernel.id)
        assert record.changed_since(document)
        record.save(document, "idle")
    saved = stored(kernel)
    assert saved.board.brief == "The user's brief" and saved.cancel_requested


def test_a_human_commands_save_keeps_its_own_decision(kernel):  # noqa: F811
    kernel.harness.request_cancel(kernel.id)
    with kernel.db.transaction() as session:
        record = kernel.harness._record(session, kernel.id)
        document = record.stored()
        document.cancel_requested = False
        record.save(document, "queued", adopt=False)
    assert not stored(kernel).cancel_requested


def test_leaving_running_refreshes_the_summary(kernel, monkeypatch):  # noqa: F811
    refreshed = []
    monkeypatch.setattr(
        kernel.harness, "_summarize", lambda session, session_id, document: refreshed.append(1)
    )
    document = stored(kernel)
    for state in ("running", "idle"):
        with kernel.db.transaction() as session:
            kernel.harness._record(session, kernel.id).save(document, state)
    assert refreshed == [1]


def test_a_rewindowed_context_report_is_kept_on_its_turn(kernel, monkeypatch):  # noqa: F811
    """The model call re-windows the context and reports it before the reply; the report replaces the requested
    turn's provenance instead of failing the call."""
    original = loop.call_with_window

    def rewindowed(model, settings, assembly, schema, cancelled, log_path, retain_report):
        retain_report({"rewindowed": True})
        return original(model, settings, assembly, schema, cancelled, log_path, retain_report)

    monkeypatch.setattr(loop, "call_with_window", rewindowed)
    run(kernel, action("say", text="Done."))
    agent = next(t for t in kernel.harness.turns(kernel.id) if t["role"] == "agent")
    assert agent["state"] == "accepted"
    assert agent["provenance"]["context"] == {"rewindowed": True}
    assert kernel.harness.read(kernel.id)["state"] == "idle"


def test_the_runner_records_a_failure_keeping_the_users_board(kernel, monkeypatch):  # noqa: F811
    kernel.harness.post_message(kernel.id, "Work.", "one")
    board = Whiteboard(brief="Kept")

    def broken(session_id):
        row = kernel.harness.read(session_id)
        kernel.harness.edit_board(session_id, row["revision"], board)
        raise RuntimeError("worker crashed")

    monkeypatch.setattr(kernel.harness, "advance", broken)
    kernel.runner.run_once()
    saved = stored(kernel)
    assert kernel.harness.read(kernel.id)["state"] == "failed"
    assert saved.stop_reason == "worker crashed" and saved.board.brief == "Kept"
