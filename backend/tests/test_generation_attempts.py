"""Attempts and outcomes: which try at a product a producer reads, and why an operation stopped, in Generation's
kinds rather than each producer's matching of error text."""

import pytest

from gaiavfx.modules.generation.operations.attempts import attempts
from gaiavfx.modules.generation.operations.outcome import failure, settled


class Store:
    def __init__(self, states):
        self.operations = {
            f"key-{n}": {"id": f"op-{n}", "state": state} for n, state in enumerate(states, 1)
        }

    def for_request(self, session, key):
        return self.operations.get(key)


def chain(*states):
    return attempts(Store(states), None, lambda n: f"key-{n}")


def ids(found):
    operation, attempt = found
    return (operation and operation["id"], attempt)


def test_no_attempt_yet():
    empty = chain()
    assert empty.next == 1
    assert ids(empty.newest()) == (None, 1)
    assert ids(empty.in_flight()) == (None, 1)
    assert ids(empty.current(lambda op: True)) == (None, 1)
    assert empty.newest_settled() is None


@pytest.mark.parametrize(
    "states,newest,in_flight,current,settled_id",
    [
        # dialogue analysis reads the first attempt still under way or succeeded, else the newest
        (("failed", "succeeded", "failed"), ("op-3", 3), (None, 4), ("op-2", 2), "op-3"),
        (("failed", "failed"), ("op-2", 2), (None, 3), ("op-2", 2), "op-2"),
        # a stage build reads the one still under way; a seed also its newest settled attempt before it
        (("failed", "running"), ("op-2", 2), ("op-2", 2), ("op-2", 2), "op-1"),
        (("running",), ("op-1", 1), ("op-1", 1), ("op-1", 1), None),
    ],
)
def test_each_reading_of_an_attempt_chain(states, newest, in_flight, current, settled_id):
    attempts_ = chain(*states)
    assert attempts_.next == len(states) + 1
    assert ids(attempts_.newest()) == newest
    assert ids(attempts_.in_flight()) == in_flight
    assert ids(attempts_.current(lambda op: op["state"] == "succeeded")) == current
    found = attempts_.newest_settled()
    assert (found and found["id"]) == settled_id


def test_current_reads_good_only_until_one_is():
    read = []
    chain("failed", "succeeded", "succeeded").current(
        lambda op: read.append(op["id"]) or op["state"] == "succeeded"
    )
    assert read == ["op-1", "op-2"]


def operation(state, *jobs, error=None, cancel_requested=False):
    return {
        "state": state,
        "error": error,
        "cancel_requested": cancel_requested,
        "jobs": [{"state": s, "error": e} for s, e in jobs],
    }


@pytest.mark.parametrize(
    "op,kind",
    [
        (operation("running"), None),
        (operation("succeeded"), None),
        (operation("running", cancel_requested=True), "cancelled"),
        (operation("cancelled"), "cancelled"),
        (operation("failed", ("failed", "[WinError 10061] No connection")), "unreachable"),
        (
            operation("failed", ("failed", "Engine worldbuilder is not installed.")),
            "engine_missing",
        ),
        (operation("failed", ("failed", "Engine worker x is missing.")), "engine_missing"),
        (operation("failed", ("failed", "CUDA out of memory")), "out_of_memory"),
        (operation("failed", error="j1: No such file or directory"), "missing_file"),
        # A job that stopped because one it depends on did carries no cause of its own.
        (
            operation(
                "failed",
                ("failed", "Execution failed"),
                ("failed", "Input job j1 ended failed; connection refused upstream"),
            ),
            "other",
        ),
    ],
)
def test_why_an_operation_stopped(op, kind):
    assert failure(op) == kind
    assert settled(op) == (op["state"] != "running")


def test_the_operation_dto_says_whether_it_settled_and_why(tmp_path):
    from fastapi.testclient import TestClient
    from test_production_api import AUTH, setup

    from gaiavfx.modules.generation.operations.lifecycle import JobSpec
    from gaiavfx.modules.generation.operations.service import OperationService

    app = setup(tmp_path)
    with app.state.workspace.db.transaction() as session:
        service = OperationService()
        created = service.create(
            session,
            kind="render",
            request={},
            jobs=[JobSpec("image", "local", "fake", step="fixture")],
            budget_limit=None,
        )
        service.transition(session, created["jobs"][0]["id"], "failed", error="CUDA out of memory")
    with TestClient(app, base_url="http://localhost") as client:
        read = client.get(f"/api/production/operations/{created['id']}", headers=AUTH)
        assert read.status_code == 200, read.text
        assert read.json()["settled"] and read.json()["failure"] == "out_of_memory"
