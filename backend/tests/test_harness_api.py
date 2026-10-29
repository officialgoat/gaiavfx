"""The new API and its thread are usable beside the old routes."""

import pytest
from fastapi.testclient import TestClient
from harness_support import ScriptedClient, action
from test_production_api import AUTH, MIGRATIONS, setup

from gaiavfx.api.app import create_app
from gaiavfx.modules.direction.service import DirectionService


def test_model_status_and_runtime_activity_are_process_state(tmp_path):
    app = setup(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        assert client.get("/api/agent/model").status_code == 401
        status = client.get("/api/agent/model", headers=AUTH)
        assert status.status_code == 200
        assert status.json()["state"] == "unloaded"
        assert status.json()["keep_warm_seconds"] == 180
        harness = app.state.agent.harness
        with harness.db.transaction() as session:
            movie = DirectionService().create_movie(session, "Status")
        row = harness.open(movie["id"])
        with harness.db.transaction() as session:
            row = harness.store.update_session(
                session, row["id"], row["revision"], state="running", document=row["document"]
            )
        harness.model.session_id = row["id"]
        for state, activity in (("waiting_gpu", "waiting_gpu"), ("loading", "loading_model")):
            harness.model._state(state)
            assert client.get("/api/agent/model", headers=AUTH).json()["state"] == state
            assert harness.payload(row)["activity"] == activity
        harness.model.session_id = "another-session"
        harness.model._state("loading")
        assert harness.payload(row)["activity"] == "running_tool"
        from pydantic import ValidationError

        from gaiavfx.api.settings import ConnectionSettings

        for invalid in (-1, 1801, True):
            with pytest.raises(ValidationError):
                ConnectionSettings(agent_keep_warm_seconds=invalid)


def test_session_messages_board_cancel_and_scopes(tmp_path):
    app = setup(tmp_path)
    harness = app.state.agent.harness
    model = ScriptedClient([action("say", text="Ready")])
    harness.model = model
    with TestClient(app, base_url="http://localhost") as client:
        with app.state.workspace.db.transaction() as session:
            movie = DirectionService().create_movie(session, "Agent API")
        body = {"movie_id": movie["id"], "scope_kind": "movie", "scope_id": movie["id"]}
        assert client.post("/api/agent/sessions", json=body).status_code == 401
        response = client.post("/api/agent/sessions", json=body, headers=AUTH)
        assert response.status_code == 201, response.text
        row = response.json()
        assert client.post("/api/agent/sessions", json=body, headers=AUTH).status_code == 200
        assert (
            client.post("/api/agent/sessions", json=body | {"extra": 1}, headers=AUTH).status_code
            == 422
        )
        url = "/api/agent/sessions/" + row["id"]
        message = {"text": "Report", "request_key": "message"}
        first = client.post(url + "/messages", json=message, headers=AUTH)
        assert first.status_code == 202, first.text
        assert client.post(url + "/messages", json=message, headers=AUTH).json() == first.json()
        assert app.state.agent.run_once()
        state = client.get(url, headers=AUTH).json()
        assert (
            state["session"]["state"] == "idle"
            and state["turns"][-1]["provenance"]["context"]["state"] == "movie/plan/planning"
        )
        assert state["turns"][-1]["receipts"] == []
        assert len(client.get(url + "?after=0", headers=AUTH).json()["turns"]) == 1
        board = state["session"]["board"] | {"brief": "Keep it quiet"}
        update = {"expected_revision": state["session"]["revision"], "board": board}
        assert client.put(url + "/board", json=update, headers=AUTH).status_code == 200
        assert client.put(url + "/board", json=update, headers=AUTH).status_code == 409
        assert client.post(url + "/cancel", headers=AUTH).status_code == 200
        assert harness.read(row["id"])["document"]["cancel_requested"]
        with harness.db.transaction() as session:
            current = harness.store.read_session(session, row["id"])
            harness.store.update_session(
                session,
                row["id"],
                current["revision"],
                state="running",
                document=current["document"],
            )
        assert (
            client.post(
                url + "/messages", json=message | {"request_key": "new"}, headers=AUTH
            ).status_code
            == 409
        )
        listed = client.get(
            "/api/agent/sessions", params={"movie_id": movie["id"]}, headers=AUTH
        ).json()
        assert len(listed) == 1


def test_a_film_has_its_own_conversation_and_always_plans(tmp_path, monkeypatch):
    from test_harness_film import FILM_TOOLS, RECAST, film_recast

    from gaiavfx.application.harness import scope

    real = scope.recast_state
    monkeypatch.setattr(
        scope,
        "recast_state",
        lambda session, library, recast_id, **kw: (
            film_recast() if recast_id == RECAST else real(session, library, recast_id, **kw)
        ),
    )
    app = setup(tmp_path)
    harness = app.state.agent.harness
    harness.model = ScriptedClient([action("say", text="Three people appear in this film.")])
    with TestClient(app, base_url="http://localhost") as client:
        with harness.db.transaction() as session:
            movie = DirectionService().create_movie(session, "Beside the film")
        sessions = "/api/agent/sessions"
        body = {"scope_kind": "film", "scope_id": RECAST}
        created = client.post(sessions, json=body, headers=AUTH)
        assert created.status_code == 201, created.text
        row = created.json()
        assert (row["movie_id"], row["scope_kind"], row["scope_id"]) == ("", "film", RECAST)
        assert row["plan_mode"] and row["autonomy"] == "off"
        again = client.post(sessions, json=body, headers=AUTH)
        assert again.status_code == 200 and again.json()["id"] == row["id"]
        for invalid in (body | {"movie_id": movie["id"]}, {"scope_kind": "film"}, {}):
            assert client.post(sessions, json=invalid, headers=AUTH).status_code == 422
        missing = client.post(sessions, json=body | {"scope_id": "no-such-recast"}, headers=AUTH)
        assert missing.status_code == 404, missing.text
        assert (
            client.post(sessions, json={"movie_id": movie["id"]}, headers=AUTH).status_code == 201
        )

        def listed(**params):
            return client.get(sessions, params=params, headers=AUTH)

        assert [s["id"] for s in listed(recast_id=RECAST).json()] == [row["id"]]
        assert [s["scope_kind"] for s in listed(movie_id=movie["id"]).json()] == ["movie"]
        for params in ({}, {"movie_id": movie["id"], "recast_id": RECAST}, {"movie_id": ""}):
            assert listed(**params).status_code == 422, params
        url = f"{sessions}/{row['id']}"
        refused = client.put(
            url + "/plan-mode",
            json={"expected_revision": row["revision"], "enabled": False},
            headers=AUTH,
        )
        assert refused.status_code == 409
        assert refused.json()["detail"] == "A film's conversation always plans before it renders."
        unchanged = client.get(url, headers=AUTH).json()["session"]
        assert unchanged["revision"] == row["revision"] and unchanged["plan_mode"]
        sent = client.post(
            url + "/messages", json={"text": "Who is in it?", "request_key": "film"}, headers=AUTH
        )
        assert sent.status_code == 202, sent.text
        assert app.state.agent.run_once()
        state = client.get(url, headers=AUTH).json()
        provenance = state["turns"][-1]["provenance"]
        assert provenance["context"]["state"] == "film/plan/planning"
        assert set(provenance["tools"]) <= FILM_TOOLS
        assert state["session"]["state"] == "idle"
        fresh = client.post(
            url + "/new", json={"expected_revision": state["session"]["revision"]}, headers=AUTH
        )
        assert fresh.status_code == 201, fresh.text
        assert (fresh.json()["movie_id"], fresh.json()["scope_kind"]) == ("", "film")
        assert fresh.json()["scope_id"] == RECAST and fresh.json()["plan_mode"]
        assert [s["id"] for s in listed(recast_id=RECAST).json()] == [fresh.json()["id"], row["id"]]
        # The earlier film conversation is read-only, as a movie's is.
        late = client.post(
            url + "/messages", json={"text": "Again", "request_key": "late"}, headers=AUTH
        )
        assert late.status_code == 409


@pytest.mark.parametrize("existing", [True, False])
def test_agent_thread_starts_with_library_and_joins(tmp_path, existing):
    if existing:
        app = setup(tmp_path, enable_runner=True)
    else:
        app = create_app(tmp_path, MIGRATIONS, "test-token", enable_runner=True)
    with TestClient(app, base_url="http://localhost") as client:
        if not existing:
            assert app.state.agent_thread is None
            response = client.post("/api/v1/library", json={"name": "New"}, headers=AUTH)
            assert response.status_code == 201
        thread = app.state.agent_thread
        assert thread is not None and thread.is_alive()
    assert app.state.agent.stop.is_set() and not thread.is_alive()


def test_plan_routes_revisions_dtos_and_attachment_validation(tmp_path):
    app = setup(tmp_path)
    harness = app.state.agent.harness

    def proposed(title):
        return action(
            "propose_plan", goal="Answer the request", steps=[{"kind": "answer", "title": title}]
        )

    harness.model = ScriptedClient(
        [
            proposed("Answer"),
            proposed("Discuss two choices"),
            action("plan_step", step=0, status="done"),
            action("say", text="Done"),
            proposed("Answer again"),
        ]
    )
    with TestClient(app, base_url="http://localhost") as client:
        with harness.db.transaction() as session:
            movie = DirectionService().create_movie(session, "Plan API")
        row = client.post(
            "/api/agent/sessions", json={"movie_id": movie["id"]}, headers=AUTH
        ).json()
        assert row["plan_mode"] and row["plan"] is None and row["plan_jobs_max"] == 50
        url = f"/api/agent/sessions/{row['id']}"
        assert (
            client.post(
                url + "/messages",
                json={"text": "Use it", "request_key": "unknown", "attachments": ["unknown"]},
                headers=AUTH,
            ).status_code
            == 422
        )
        assert (
            client.post(
                url + "/messages",
                json={"text": "Use them", "request_key": "many", "attachments": ["id"] * 7},
                headers=AUTH,
            ).status_code
            == 422
        )
        client.post(
            url + "/messages", json={"text": "Make a plan", "request_key": "first"}, headers=AUTH
        )
        app.state.agent.run_once()
        row = client.get(url, headers=AUTH).json()["session"]
        assert row["activity"] == "awaiting_approval" and row["plan"]["revision"] == 1
        assert row["summary"] and row["plan"]["steps"][0]["status"] == "pending"
        assert (
            client.post(
                url + "/plan/revise",
                json={"expected_revision": row["revision"], "note": " "},
                headers=AUTH,
            ).status_code
            == 422
        )
        revision = client.post(
            url + "/plan/revise",
            json={"expected_revision": row["revision"], "note": "Discuss two choices."},
            headers=AUTH,
        )
        assert revision.status_code == 202, revision.text
        assert revision.json()["session"]["plan"]["revision_requested"]
        assert (
            client.post(
                url + "/plan/approve", json={"expected_revision": row["revision"]}, headers=AUTH
            ).status_code
            == 409
        )
        app.state.agent.run_once()
        row = client.get(url, headers=AUTH).json()["session"]
        assert row["plan"]["revision"] == 2 and row["plan_history"][0]["state"] == "revised"
        approved = client.post(
            url + "/plan/approve", json={"expected_revision": row["revision"]}, headers=AUTH
        )
        assert approved.status_code == 202, approved.text
        assert approved.json()["session"]["plan"]["grant"]["step_kinds"] == ["answer"]
        app.state.agent.run_once()
        row = client.get(url, headers=AUTH).json()["session"]
        assert row["plan"]["state"] == "done"
        client.post(
            url + "/messages", json={"text": "Another plan", "request_key": "second"}, headers=AUTH
        )
        app.state.agent.run_once()
        row = client.get(url, headers=AUTH).json()["session"]
        rejected = client.post(
            url + "/plan/reject",
            json={"expected_revision": row["revision"], "note": "Not now."},
            headers=AUTH,
        )
        assert rejected.status_code == 200, rejected.text
        row = rejected.json()["session"]
        assert row["plan"] is None and row["plan_history"][-1]["state"] == "rejected"
        assert (
            client.put(
                url + "/plan-mode",
                json={"expected_revision": row["revision"], "enabled": False, "extra": True},
                headers=AUTH,
            ).status_code
            == 422
        )
        disabled = client.put(
            url + "/plan-mode",
            json={"expected_revision": row["revision"], "enabled": False},
            headers=AUTH,
        )
        assert disabled.status_code == 200 and not disabled.json()["plan_mode"]
        assert (
            client.put(
                url + "/plan-mode",
                json={"expected_revision": row["revision"], "enabled": True},
                headers=AUTH,
            ).status_code
            == 409
        )
