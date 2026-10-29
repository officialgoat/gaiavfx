"""Exercise the frozen HTTP routes against real Editing/Generation persistence."""

import json
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient
from test_recast_feed import settle_all, waiting_reference
from test_recast_lines import KEYS
from test_recast_lines import voice as voice  # noqa: PLC0414
from test_recast_service import recast_template as recast_template  # noqa: PLC0414
from test_recast_service import world as world  # noqa: PLC0414
from test_recast_voice import (
    audition,
    build_operation,
    character,
    checked,
    drive,
    feed_writes,
    mara,
    succeed,
)
from test_recast_voice import builds as builds  # noqa: PLC0414

from gaiavfx.api.recast import LinesDTO, RecastDTO, recast_router
from gaiavfx.application import recast
from gaiavfx.application import recast_projection as p
from gaiavfx.shared.errors import ApplicationError


def workspace(world, settings=None):
    """The router's workspace: the world's database and Library, and Settings (a dict the test may change) with the
    expressive recordings folder."""
    settings = {"expressive_library": ""} if settings is None else settings
    return SimpleNamespace(db=world.db, library=world.library, settings=lambda: dict(settings))


def test_every_recast_route(world, tmp_path, monkeypatch):
    app = FastAPI()
    routes, media = recast_router(workspace(world), lambda: None, "test-token")
    app.include_router(routes)
    app.include_router(media)

    @app.exception_handler(ApplicationError)
    async def error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    with TestClient(app) as client:
        body = {"source_asset_id": "film", "request_key": "http"}
        response = client.post("/api/recasts", json=body)
        assert response.status_code == 201, response.text
        state = response.json()
        base = "/api/recasts/" + state["id"]
        assert client.post("/api/recasts", json=body).status_code == 200
        assert client.get("/api/recasts").json()[0]["id"] == state["id"]
        with world.db.transaction() as session:
            world.settle(session, state["analysis"]["operation_id"])
        state = client.get(base).json()
        cid = state["characters"][0]["id"]
        assert [track["id"] for track in state["characters"][0]["tracks"]] == [
            "t1",
            "t2",
            "t3",
            "t4",
        ]

        def command(method, path, **body):
            nonlocal state
            response = client.request(
                method, base + path, json={"expected_revision": state["revision"], **body}
            )
            assert response.status_code == 200, response.text
            state = response.json()
            return state

        command("PATCH", f"/characters/{cid}", label="Lead", hidden=True)
        assert state["characters"][0]["hidden"]
        command("PATCH", f"/characters/{cid}", hidden=False)
        command("POST", "/tracks/move", track_ids=["t2"], to="new")
        second = state["characters"][1]["id"]
        # CP3: a pair set aside as not the same person is stored in the recast.
        command("POST", "/not-same", pairs=[{"character_a": second, "character_b": cid}])
        assert state["not_same"] == [
            {"character_a": min(cid, second), "character_b": max(cid, second)}
        ]
        empty = client.post(
            base + "/not-same", json={"expected_revision": state["revision"], "pairs": []}
        )
        assert empty.status_code == 422
        command("POST", "/characters/merge", into=cid, sources=[second])
        assert state["not_same"] == []
        command("PUT", f"/characters/{cid}/target", asset_id="picture", label="Target")
        command("POST", f"/characters/{cid}/target/confirm")
        response = client.delete(
            base + f"/characters/{cid}/target", params={"expected_revision": state["revision"]}
        )
        assert response.status_code == 200
        state = response.json()
        command("PUT", f"/characters/{cid}/target", asset_id="picture", label="Target")
        invalid = client.put(
            base + f"/characters/{cid}/target",
            json={"expected_revision": state["revision"], "asset_id": "film", "label": "Bad"},
        )
        assert invalid.status_code == 422
        stale = client.patch(
            base + f"/characters/{cid}", json={"expected_revision": 0, "label": "Stale"}
        )
        assert stale.status_code == 409
        assert client.get(base).json()["characters"][0]["target"]["face"] == "checking"
        world.settle_target()
        assert client.get(base).json()["characters"][0]["target"]["face"] == "face"
        state, _, _ = waiting_reference(world, state)
        command("POST", f"/characters/{cid}/reference/approve")
        assert state["characters"][0]["target"]["reference"]["state"] == "approved"
        command("PUT", f"/characters/{cid}/target", asset_id="picture2", label="Other")
        world.settle_target("picture2")
        state, _, _ = waiting_reference(world, state)
        command("POST", f"/characters/{cid}/reference/reject")
        assert state["characters"][0]["target"]["reference"]["state"] == "rejected"
        command("PUT", f"/characters/{cid}/target", asset_id="picture", label="Target")
        command("PATCH", "/shots/s0001", priority=10)
        command("PATCH", "/shots/s0001", included=False)
        assert state["shots"][0]["priority"] == 10
        assert state["shots"][0]["status"] == "excluded"
        command("PATCH", "/shots/s0001", included=None, priority=None)
        assert state["shots"][0]["included"] and state["shots"][0]["priority"] is None
        command("POST", "/plan")
        assert state["shots"][0]["plan"]["canvas_scale"] == 0.5
        # The estimate of a range before a run starts: renders and time for that range and mode.
        draft = client.get(base + "/estimate", params={"mode": "draft", "start": 0, "end": 73})
        assert draft.status_code == 200, draft.text
        assert draft.json()["shots"] == 1 and draft.json()["renders"] > 0
        final = client.get(base + "/estimate", params={"mode": "final", "start": 0, "end": 73})
        assert final.json()["renders"] >= draft.json()["renders"]
        assert (
            client.get(
                base + "/estimate", params={"mode": "draft", "start": 10**9, "end": 10**9 + 1}
            ).status_code
            == 422
        )
        # One end alone is refused, not read as the whole film (Astra R5).
        for one_end in ({"start": 10**9}, {"end": 10**9}):
            refused = client.get(base + "/estimate", params={"mode": "draft", **one_end})
            assert refused.status_code == 422
        command("POST", "/run", render_budget=20, range={"start": 0, "end": 73})
        command("POST", "/run/pause")
        command("POST", "/run", render_budget=20, range={"start": 0, "end": 73})
        world.invoke(recast.feed)
        settle_all(world, "character_replacement")
        world.invoke(recast.feed)
        settle_all(world, "shot_assembly")
        world.invoke(recast.feed)
        state = client.get(base).json()
        version = state["shots"][0]["versions"][0]["id"]
        unknown = client.post(
            base + "/shots/s0001/head-pass",
            json={
                "expected_revision": state["revision"],
                "version": "missing",
                "character_id": cid,
            },
        )
        assert unknown.status_code == 422
        command("POST", "/shots/s0001/head-pass", version=version, character_id=cid)
        assert state["shots"][0]["layers"][0]["renders_reserved"] == 1
        settle_all(world, "character_replacement")
        world.invoke(recast.feed)
        settle_all(world, "shot_assembly")
        command("POST", "/shots/s0001/choice", version=version)
        command("POST", "/shots/s0001/retake")
        settle_all(world, "character_replacement")
        world.invoke(recast.feed)
        settle_all(world, "shot_assembly")
        reveal = client.get(base + "/shots/s0001/reveal")
        assert reveal.status_code == 200
        assert len(reveal.json()["versions"]) == 3
        command("POST", "/shots/s0001/choice", version="original")
        command("POST", "/shots/s0001/choice", version="unchosen")
        assert state["shots"][0]["choice"] is None
        assert state["shots"][0]["status"] == "needs_review"
        command("POST", "/run/cancel")
        nothing = client.post(base + "/conform", json={"request_key": "nothing"})
        assert nothing.status_code == 409 and "Nothing to conform" in nothing.text
        command("POST", "/shots/s0001/choice", version=version)
        monkeypatch.setattr(
            world.library,
            "artifact",
            lambda session, id: {"id": id, "kind": "replacement_patch", "sha256": "patch-sha"},
        )
        conform = client.post(base + "/conform", json={"request_key": "conform"})
        assert conform.status_code == 201, conform.text
        assert conform.json()["kind"] == "recast_conform"
        assert (
            client.post(
                base + "/plan",
                json={"expected_revision": state["revision"], "range": {"start": 100, "end": 10}},
            ).status_code
            == 422
        )
        assert client.get("/api/recasts/missing").status_code == 404
        image = tmp_path / "face.png"
        image.write_bytes(b"face fixture")
        world.report["tracks"][0]["crop_artifact"] = "face-artifact"
        monkeypatch.setattr(world.library, "artifact_path", lambda session, artifact: image)
        state = client.get(base).json()
        face = state["characters"][0]["face"]
        assert face["track_id"] == "t1"
        assert state["characters"][0]["tracks"][0] == {
            "id": "t1",
            "shot_id": "s0001",
            "frames_with_face": 15,
            "face": face,
        }
        assert client.get(face["url"]).content == b"face fixture"
        # Face images are signed links (an <img> sends no bearer token).
        assert "signature=" in face["url"] and "expires=" in face["url"]
        unauthenticated = TestClient(client.app)
        assert unauthenticated.get(face["url"]).content == b"face fixture"
        assert unauthenticated.get(face["url"].split("?")[0]).status_code == 422
        tampered = face["url"].replace("signature=", "signature=0")
        assert unauthenticated.get(tampered).status_code == 401
        other = face["url"].replace("/faces/face-artifact", "/faces/not-this-film")
        assert unauthenticated.get(other).status_code == 401
        command("POST", "/tracks/move", track_ids=["t1"], to="unknown")
        assert state["unknown_faces"] == [face]
        command("POST", "/tracks/move", track_ids=[state["unknown_faces"][0]["track_id"]], to=cid)
        assert state["unknown_faces"] == []
        assert state["characters"][0]["tracks"][0]["face"] == face

        def no_write(*args, **kwargs):
            raise AssertionError("GET must not lock or save the recast")

        monkeypatch.setattr(p.store, "lock", no_write)
        monkeypatch.setattr(p.store, "save", no_write)
        for url in (base, "/api/recasts", base + "/shots/s0001/reveal", face["url"]):
            assert client.get(url).status_code == 200


@pytest.mark.parametrize("failed", [False, True])
def test_head_pass_409_commits_missing_or_retried_target_report(world, monkeypatch, failed):
    from test_recast_feed import drain, run

    from gaiavfx.application import target_faces

    state = drain(world, run(world, world.ready())["id"])
    monkeypatch.setattr(target_faces, "SCAN_VERSION", 2)
    if failed:
        world.settle_target(state="failed")
        world.invoke(recast.feed)
        assert world.invoke(target_faces.start_target_report, "picture")["request"]["attempt"] == 1
    app = FastAPI()
    routes, _ = recast_router(workspace(world), lambda: None, "test-token")
    app.include_router(routes)

    @app.exception_handler(ApplicationError)
    async def error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    with TestClient(app) as client:
        response = client.post(
            f"/api/recasts/{state['id']}/shots/s0001/head-pass",
            json={
                "expected_revision": state["revision"],
                "version": state["shots"][0]["versions"][0]["id"],
                "character_id": state["characters"][0]["id"],
            },
        )
        assert response.status_code == 409
        assert response.json()["detail"] == "Checking the target picture; try again in a moment"
    assert world.invoke(target_faces.target_report, "picture") == {"state": "checking"}
    assert world.invoke(target_faces.start_target_report, "picture")["request"]["attempt"] == (
        2 if failed else 1
    )
    current = world.invoke(recast.recast_state, state["id"])
    assert current["revision"] == state["revision"]
    assert current["shots"][0]["layers"] == []
    assert current["run"]["renders_used"] == state["run"]["renders_used"]


@pytest.mark.parametrize("legacy", [False, True])
def test_handoff_provenance_is_only_in_reveal(world, monkeypatch, legacy):
    from test_recast_feed import drain, long_shots, run

    state = drain(world, run(world, long_shots(world), budget=8)["id"])
    if legacy:
        versions = world.window_api.shot_versions

        def stored_chain(*args, **kwargs):
            rows = versions(*args, **kwargs)
            for row in rows:
                row.pop("bridges", None)
                row.pop("joins", None)
                for index, window in enumerate(row["windows"]):
                    window.pop("handoff", None)
                    window["chained_from"] = "candidate" if index else None
            return rows

        monkeypatch.setattr(world.window_api, "shot_versions", stored_chain)
    app = FastAPI()
    routes, _ = recast_router(workspace(world), lambda: None, "test-token")
    app.include_router(routes)
    with TestClient(app) as client:
        base = "/api/recasts/" + state["id"]
        blind = client.get(base)
        assert blind.status_code == 200 and "chained_from" not in blind.text
        assert '"bridges"' not in blind.text and '"joins"' not in blind.text
        reveal = client.get(base + "/shots/s0001/reveal")
        assert reveal.status_code == 200
        version = reveal.json()["versions"][0]
        if legacy:
            assert [w["chained_from"] for w in version["windows"]] == [
                None,
                "candidate",
                "candidate",
            ]
            assert "bridges" not in version and "joins" not in version
        else:
            assert len(version["bridges"]) == 2
            assert version["joins"] == [
                {"join": 1, "handoff": "bridge"},
                {"join": 2, "handoff": "bridge"},
            ]
            assert all(w["handoff"] is None for w in version["windows"])


def test_appearance_api_is_additive_and_decisions_are_human(world, monkeypatch):
    from gaiavfx.application import recast_appearance

    state = world.ready()
    app = FastAPI()
    router, _ = recast_router(workspace(world), lambda: None, "token")
    app.include_router(router)

    @app.exception_handler(ApplicationError)
    async def error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    operation = world.calls["analysis"][0]
    character = state["characters"][0]["id"]
    base = f"/api/recasts/{state['id']}/shots/s0001/appearances"
    path = f"{base}/{operation}/person0"
    with TestClient(app) as client:
        assert (
            client.get(f"/api/recasts/{state['id']}").json()["shots"][0]["appearance_links"] == []
        )
        body = {"expected_revision": state["revision"], "character_id": character}
        assert (
            client.post(path + "/propose", json={**body, "proposed_by": "agent"}).status_code == 422
        )
        for action in ("propose", "confirm", "reject"):
            body = {"expected_revision": state["revision"], "character_id": character}
            assert (
                client.post(path + "/" + action, json={**body, "authority": "agent"}).status_code
                == 422
            )
            response = client.post(path + "/" + action, json=body)
            assert response.status_code == 200, response.text
            state = response.json()
            assert (
                state["shots"][0]["appearance_links"][0]["state"]
                == {"propose": "proposed", "confirm": "confirmed", "reject": "rejected"}[action]
            )
        assert (
            client.post(
                path + "/propose",
                json={"expected_revision": state["revision"], "character_id": character},
            ).status_code
            == 409
        )
        response = client.delete(path, params={"expected_revision": state["revision"]})
        assert response.status_code == 200, response.text
        state = response.json()
        assert not state["shots"][0]["appearance_links"]
        # The route passes the database, not a held writer session, to the orchestration.
        calls = []

        def suggest(database, library, *args, **kwargs):
            calls.append((database, library, args, kwargs))
            return state

        monkeypatch.setattr(recast_appearance, "suggest_appearances", suggest)
        response = client.post(base + "/suggest", json={"expected_revision": state["revision"]})
        assert response.status_code == 200, response.text
        assert calls[0][:2] == (world.db, world.library)


def test_cast_edit_undo_route_and_the_newest_twenty_entries(world):
    """CP3 B2: the journal reaches the browser as summaries only; undo is one revision-checked route."""
    state = world.create()
    app = FastAPI()
    router, _ = recast_router(workspace(world), lambda: None, "token")
    app.include_router(router)

    @app.exception_handler(ApplicationError)
    async def error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    base = f"/api/recasts/{state['id']}"
    character = state["characters"][0]["id"]
    with TestClient(app) as client:
        for index in range(22):
            response = client.patch(
                f"{base}/characters/{character}",
                json={"expected_revision": state["revision"], "label": f"Name {index}"},
            )
            assert response.status_code == 200, response.text
            state = response.json()
        edits = client.get(base).json()["edits"]
        assert len(edits) == 20
        assert edits[0]["summary"] == "Renamed Name 20 to Name 21"
        assert edits[-1]["summary"] == "Renamed Name 1 to Name 2"
        assert [e["created_at"] for e in edits] == sorted(
            (e["created_at"] for e in edits), reverse=True
        )
        # Blind: no row images, asset or operation ids leave the backend.
        assert all(
            set(e) == {"id", "command", "kind", "summary", "created_at", "undone_at"} for e in edits
        )
        newest = edits[0]["id"]
        undo = f"{base}/edits/{newest}/undo"
        stale = client.post(undo, json={"expected_revision": state["revision"] - 1})
        assert stale.status_code == 409
        assert stale.json()["detail"] == "The recast changed; reload before editing."
        missing = client.post(
            f"{base}/edits/nothing/undo", json={"expected_revision": state["revision"]}
        )
        assert missing.status_code == 404
        response = client.post(undo, json={"expected_revision": state["revision"]})
        assert response.status_code == 200, response.text
        state = response.json()
        assert state["characters"][0]["label"] == "Name 20"
        assert len(state["edits"]) == 20
        assert (state["edits"][0]["kind"], state["edits"][0]["summary"]) == (
            "undo",
            "Undo: Renamed Name 20 to Name 21",
        )
        assert state["edits"][1]["id"] == newest and state["edits"][1]["undone_at"]
        again = client.post(undo, json={"expected_revision": state["revision"]})
        assert again.status_code == 409
        assert again.json()["detail"] == "This edit was already undone."


def test_dialogue_and_line_routes(world, voice):
    """ADR 0016 P4: the Voice step's routes; line decisions are revision-checked human routes."""
    from gaiavfx.modules.library.assets import AssetRecord

    app = FastAPI()
    router, _ = recast_router(workspace(world), lambda: None, "token")
    app.include_router(router)

    @app.exception_handler(ApplicationError)
    async def error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    state = world.create()
    base = f"/api/recasts/{state['id']}"
    with TestClient(app) as client:

        def command(method, path, status=200, **body):
            nonlocal state
            response = client.request(
                method, base + path, json={"expected_revision": state["revision"], **body}
            )
            assert response.status_code == status, response.text
            if status == 200:
                previous = state["revision"]
                RecastDTO.model_validate(response.json())
                state = response.json()
                assert state["revision"] == previous + 1
            return response

        command("POST", "/tracks/move", track_ids=["t3", "t4"], to="new")
        a, b = (c["id"] for c in state["characters"])
        assert client.get(base + "/lines").json()["lines"] == []
        command("POST", "/lines/confirm", 409, character_id=a)  # not analysed yet
        command("POST", "/dialogue")
        operation = state["dialogue"]["operation_id"]
        assert operation and state["dialogue"]["state"] == "running"
        command("POST", "/dialogue/retry", 409)  # nothing failed
        with world.db.transaction() as session:
            world.settle(session, operation)
        state = client.get(base).json()
        assert state["dialogue"]["state"] == "succeeded" and state["lines"]["proposed"] == 6
        lines = client.get(base + "/lines")
        assert lines.status_code == 200
        assert LinesDTO.model_validate(lines.json()).dialogue_operation_id == operation
        command("POST", f"/lines/{KEYS[0]}/confirm", character_id=a)
        command("POST", f"/lines/{KEYS[0]}/reject", character_id=a)
        command("PUT", f"/lines/{KEYS[1]}/edges", start_sample=70_000, end_sample=115_000)
        command("PUT", f"/lines/{KEYS[1]}/edges", start_sample=None, end_sample=None)
        response = client.delete(
            base + f"/lines/{KEYS[0]}", params={"expected_revision": state["revision"]}
        )
        assert response.status_code == 200, response.text
        state = response.json()
        command("POST", "/lines/confirm", character_id=b)
        command("POST", "/lines/confirm", character_id=a, segment_keys=[KEYS[0]])
        assert state["lines"]["confirmed"] == 4 and state["edits"][0]["command"] == "confirm_lines"
        stale = client.post(
            base + f"/lines/{KEYS[2]}/confirm", json={"expected_revision": 0, "character_id": a}
        )
        assert stale.status_code == 409
        command("POST", "/lines/99999-0/confirm", 404, character_id=a)
        command("PUT", f"/lines/{KEYS[2]}/edges", 422, start_sample=10, end_sample=10)
        command("PUT", f"/lines/{KEYS[2]}/edges", 422, start_sample=-1, end_sample=10)
        command("POST", f"/lines/{KEYS[2]}/confirm", 422, character_id=a, authority="user")
        view = client.get(base + "/lines").json()
        assert [line["state"] for line in view["lines"]] == [
            "confirmed",
            "confirmed",
            "proposed",
            "proposed",
            "confirmed",
            "confirmed",
        ]
        # A film without audio never gets an analysis: a typed gap, and the start refuses.
        with world.db.transaction() as session:
            session.add(
                AssetRecord(
                    id="silent",
                    sha256="silent",
                    name="silent",
                    kind="video",
                    path="silent",
                    mime="application/octet-stream",
                    info={"width": 1920, "height": 1080, "frame_rate": "24/1", "has_audio": False},
                )
            )
        silent = client.post(
            "/api/recasts", json={"source_asset_id": "silent", "request_key": "silent"}
        ).json()
        with world.db.transaction() as session:
            world.settle(session, silent["analysis"]["operation_id"])
        refused = client.post(
            f"/api/recasts/{silent['id']}/dialogue", json={"expected_revision": silent["revision"]}
        )
        assert refused.status_code == 422
        assert refused.json()["detail"] == "This film has no audio track."
        silent = client.get(f"/api/recasts/{silent['id']}").json()
        assert silent["dialogue"]["gap"]["code"] == "no_audio"
        assert silent["dialogue"]["operation_id"] is None


def test_voice_routes(world, builds, tmp_path):
    settings = {"expressive_library": ""}
    app = FastAPI()
    router, _ = recast_router(workspace(world, settings), lambda: None, "token")
    app.include_router(router)

    @app.exception_handler(ApplicationError)
    async def error(request, exc):
        return JSONResponse({"detail": str(exc)}, status_code=exc.status)

    state, cid = mara(world)
    base = f"/api/recasts/{state['id']}"
    voice_path = f"/characters/{cid}/voice"
    with TestClient(app) as client:

        def command(method, path, status=200, **body):
            nonlocal state
            response = client.request(
                method, base + path, json={"expected_revision": state["revision"], **body}
            )
            assert response.status_code == status, response.text
            if status == 200:
                previous = state["revision"]
                state = checked(response.json())
                assert state["revision"] == previous + 1
            return response

        def view():
            return character(state, cid)["voice"]

        command("PUT", voice_path, 422, asset_ids=[])
        command("PUT", voice_path, 422, asset_ids=["short"])
        command("PUT", voice_path, asset_ids=["rec_a", "rec_b"])
        assert view()["samples"][0]["asset_id"] == "rec_a"
        assert view()["blocked"] is False  # Python decides what blocks Build voice (P5d)
        command("POST", voice_path + "/confirm", 409)
        response = client.delete(base + voice_path, params={"expected_revision": state["revision"]})
        assert response.status_code == 200, response.text
        state = checked(response.json())
        assert view() is None
        command("PUT", voice_path, asset_ids=["rec_a", "rec_b"])
        command("POST", voice_path + "/approve", 409)
        command("POST", voice_path + "/reject", 409)
        command("POST", voice_path + "/retry", 409)  # nothing built yet
        command("POST", voice_path + "/build")
        assert view()["model_state"] == "making"
        assert builds.calls["start"] == [{"ids": ["rec_a", "rec_b"], "attempt": 1, "digest": ""}]
        command("POST", voice_path + "/build", 409)
        build = build_operation(world, ["rec_a", "rec_b"])
        drive(world, build["id"], ["succeeded", "failed"])
        command("POST", voice_path + "/retry")
        assert builds.calls["retry"] == [build["id"]]
        with world.db.transaction() as session:
            world.ops.resume(session, build["id"], {"stage": "done"}, jobs=[])
        command("POST", voice_path + "/build", rebuild=False)  # the partial build's next attempt
        second = build_operation(world, ["rec_a", "rec_b"], attempt=2)
        succeed(world, second["id"])
        audition(world, state, cid, second, builds=builds)
        feed_writes(
            world,
            state,
            cid,
            voice_model_state="waiting",
            voice_model_artifact_id="hidden-artifact-" + second["id"],
        )
        page = client.get(base)
        assert page.status_code == 200
        state = checked(page.json())
        assert view()["audition_asset_id"] == "reel" and not view()["library_changed"]
        # GET passes the Settings folder: a folder whose recordings differ from the build's flips the hint.
        folder = tmp_path / "expressive"
        (folder / "sobs" / "alex").mkdir(parents=True)
        (folder / "sobs" / "alex" / "b.flac").write_bytes(b"sob")
        settings["expressive_library"] = str(folder)
        page = client.get(base).json()
        assert character(page, cid)["voice"]["library_changed"]
        assert str(folder) not in json.dumps(page)
        assert json.dumps(str(folder))[1:-1] not in json.dumps(page)
        command("POST", voice_path + "/build", 409)  # changed, but not asked to build again
        command("POST", voice_path + "/approve")
        assert view()["model_state"] == "approved"
        assert not view()["library_changed"]  # a command's response does not scan the folder
        feed_writes(world, state, cid, voice_model_state="waiting")
        state = client.get(base).json()
        command("POST", voice_path + "/reject")
        assert view()["model_state"] == "rejected"
        stale = client.put(base + voice_path, json={"expected_revision": 0, "asset_ids": ["rec_c"]})
        assert stale.status_code == 409
        extra = client.put(
            base + voice_path,
            json={
                "expected_revision": state["revision"],
                "asset_ids": ["rec_c"],
                "authority": "user",
            },
        )
        assert extra.status_code == 422
        command("POST", voice_path + "/build", rebuild=True)  # build again with the new recordings
        assert builds.calls["start"][-1]["attempt"] == 3
        assert view()["model_state"] == "making"


def test_estimate_leaves_out_shots_the_feeder_skips():
    """A shot whose plan has a gap is never rendered, so the estimate does not price it (Astra R5)."""
    from gaiavfx.application.recast_run import estimate_run

    plan = {"performers": ["c1"], "windows": [{"length": 56, "start": 0, "end": 50}], "gaps": []}
    shot = {"id": "s1", "start": 0, "end": 50, "included": True, "plan": plan}
    state = {"film": {"frame_count": 100}, "shots": [shot]}
    gapped = {**shot, "plan": {**plan, "gaps": [{"code": "unbound_performer", "message": "x"}]}}
    estimate = estimate_run({**state, "shots": [gapped]}, mode="draft")[1]
    assert estimate["renders"] == 0 and estimate["shots"] == 0
    # A plan still being analysed has no windows to price yet (usability round 2, C6).
    analysing = {**shot, "plan": {**plan, "windows": [], "_ready": False}}
    assert estimate_run({**state, "shots": [analysing]}, mode="draft")[1]["shots"] == 0


def test_conform_errors_reach_the_film_as_sentences():
    """No job id, path or FFmpeg output reaches the blind film page (Astra R5)."""
    from gaiavfx.application.recast_conform import _conform_error

    raw = "job 1f2e: ffmpeg exited 1: X:/library/media/ab12.mkv: Invalid data found"
    assert _conform_error({"state": "running", "error": raw}) is None
    for error in (raw, None):
        message = _conform_error({"state": "failed", "error": error})
        assert message.startswith("Encoding failed.") and "1f2e" not in message
    missing = _conform_error({"state": "failed", "error": "job 9: No such file: X:/x.mkv"})
    assert missing.startswith("A file the conform needs is missing.")
