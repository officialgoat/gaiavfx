"""Human recovery preserves history and revokes execution authority."""

import pytest
from fastapi.testclient import TestClient
from harness_support import ScriptedClient, action
from test_production_api import AUTH, setup

from gaiavfx.modules.direction.service import DirectionService


def test_find_session_returns_newest_open_conversation(tmp_path):
    app = setup(tmp_path)
    h = app.state.agent.harness
    with h.db.transaction() as db:
        movie = DirectionService().create_movie(db, "Current conversation")
    original = h.open(movie["id"])
    with h.db.transaction() as db:
        newer = h.store.create_session(
            db,
            movie_id=movie["id"],
            scope_kind="movie",
            scope_id=movie["id"],
            document=original["document"],
        )
        assert h.store.find_session(db, movie["id"], "movie", movie["id"])["id"] == newer["id"]
        h.store.update_session(
            db, newer["id"], newer["revision"], state="stopped", document=newer["document"]
        )
        assert h.store.find_session(db, movie["id"], "movie", movie["id"])["id"] == original["id"]


@pytest.mark.parametrize("state", ["failed", "idle", "waiting_operation", "queued", "stopped"])
def test_new_conversation_closes_history_and_open_returns_current(tmp_path, state):
    app = setup(tmp_path)
    h = app.state.agent.harness
    with TestClient(app, base_url="http://localhost") as client:
        with h.db.transaction() as db:
            movie = DirectionService().create_movie(db, "Recovery")
        old = h.open(movie["id"])
        h.post_message(old["id"], "Remember this conversation", "first")
        with h.db.transaction() as db:
            row = h.store.read_session(db, old["id"])
            old = h.store.update_session(
                db, old["id"], row["revision"], state=state, document=row["document"]
            )
        url = f"/api/agent/sessions/{old['id']}"
        body = {"expected_revision": old["revision"]}
        assert client.post(url + "/new", json=body).status_code == 401
        fresh = client.post(url + "/new", json=body, headers=AUTH)
        assert fresh.status_code == 201, fresh.text
        fresh = fresh.json()
        assert fresh["id"] != old["id"] and fresh["state"] == "idle"
        assert old["id"] in fresh["summary"]
        assert h.open(movie["id"])["id"] == fresh["id"]
        assert h.sessions(movie["id"])[0]["id"] == fresh["id"]
        history = client.get(url, headers=AUTH).json()
        assert history["session"]["state"] == "stopped"
        assert history["turns"][0]["action"]["message"] == "Remember this conversation"
        assert client.post(url + "/new", json=body, headers=AUTH).status_code == 409
        assert (
            client.post(
                url + "/messages",
                json={"text": "Cannot revive", "request_key": "later"},
                headers=AUTH,
            ).status_code
            == 409
        )
        assert len(h.sessions(movie["id"])) == 2


def test_stop_plan_allows_new_proposal_and_does_not_resume_old_review(tmp_path):
    app = setup(tmp_path)
    h = app.state.agent.harness
    proposal = lambda goal: action(
        "propose_plan", goal=goal, steps=[{"kind": "answer", "title": "Answer"}]
    )
    h.model = ScriptedClient([proposal("First plan"), proposal("Replacement plan")])
    with TestClient(app, base_url="http://localhost") as client:
        with h.db.transaction() as db:
            movie = DirectionService().create_movie(db, "Plan recovery")
        row = h.open(movie["id"])
        h.post_message(row["id"], "Plan this", "one")
        assert app.state.agent.run_once()
        row = h.read(row["id"])
        approved = h.approve_plan(row["id"], row["revision"])
        url = f"/api/agent/sessions/{row['id']}/plan/stop"
        assert (
            client.post(url, json={"expected_revision": row["revision"]}, headers=AUTH).status_code
            == 409
        )
        stopped = client.post(url, json={"expected_revision": approved["revision"]}, headers=AUTH)
        assert stopped.status_code == 200, stopped.text
        state = stopped.json()
        assert state["session"]["state"] == "idle"
        assert state["session"]["plan"] is None
        assert state["session"]["plan_history"][-1]["state"] == "superseded"
        assert state["turns"][-1]["role"] == "system"
        assert "stopped plan revision 1" in state["turns"][-1]["action"]["message"]
        assert (
            state["session"]["plan_history"][-1]["id"]
            not in state["turns"][-1]["action"]["message"]
        )
        assert not app.state.agent.run_once()
        h.post_message(row["id"], "A different plan", "two")
        assert app.state.agent.run_once()
        assert h.read(row["id"])["document"]["plan"]["goal"] == "Replacement plan"


def test_running_recovery_refuses_without_mutation(tmp_path):
    app = setup(tmp_path)
    h = app.state.agent.harness
    with h.db.transaction() as db:
        movie = DirectionService().create_movie(db, "Busy")
    row = h.open(movie["id"])
    with h.db.transaction() as db:
        row = h.store.claim_session(db, row["id"], {"idle"})
    from gaiavfx.shared.errors import ApplicationError

    for command in (h.new_conversation, h.stop_plan):
        with pytest.raises(ApplicationError, match="still working"):
            command(row["id"], row["revision"])
    assert h.read(row["id"]) == row
    assert len(h.sessions(movie["id"])) == 1
