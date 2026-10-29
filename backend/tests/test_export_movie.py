"""Movie export through the real API, SQLite and FFmpeg; no inference."""

import json
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gaiavfx.api.app import create_app
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.editing.selections.service import SelectionService
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService


@pytest.fixture
def movie(tmp_path):
    migrations = Path(__file__).parents[1] / "migrations"
    LibraryService(tmp_path, migrations).create("Export")
    app = create_app(tmp_path, migrations, "test-token", enable_runner=False)
    with TestClient(
        app, base_url="http://localhost", headers={"Authorization": "Bearer test-token"}
    ) as client:
        w = app.state.workspace
        direction, operations, selections = (
            DirectionService(),
            OperationService(),
            SelectionService(),
        )
        with w.db.transaction() as session:
            m = direction.create_movie(session, "Two scenes")
            subject = direction.add_subject(
                session,
                m["id"],
                1,
                name="Walker",
                description="A walker",
                keeps=[],
                identity_asset_id=None,
            )
        segments, chosen = [], []
        for index, frames in enumerate((25, 37)):
            source = tmp_path / f"take-{index}.mp4"
            subprocess.run(
                [
                    str(w.media.ffmpeg),
                    "-v",
                    "error",
                    "-f",
                    "lavfi",
                    "-i",
                    f"color=c={'red' if index == 0 else 'blue'}:s=160x96:r=24:d=2",
                    "-f",
                    "lavfi",
                    "-i",
                    "sine=frequency=440:sample_rate=48000:duration=2",
                    "-c:v",
                    "libx264",
                    "-c:a",
                    "aac",
                    "-shortest",
                    str(source),
                ],
                check=True,
            )
            prepared = w.library.prepare(source, source.name)
            with w.db.transaction() as session:
                segment = direction.add_segment(
                    session,
                    m["id"],
                    index + 2,
                    content={
                        "subjects": [subject["id"]],
                        "place": {"noun": "room", "description": "A room", "landmarks": ""},
                    },
                    delivered_frames=frames,
                    continues=False,
                )
                asset = w.library.register(session, prepared, {"type": "test"})
                op = operations.create(
                    session,
                    kind="render",
                    request={
                        "segment_revision": segment["revision"],
                        "phase_revisions": {},
                        "subject_revisions": {subject["id"]: subject["revision"]},
                    },
                    budget_limit=None,
                    jobs=[JobSpec(kind="video", executor="local", integration="test", step="test")],
                )
                candidate = operations.add_candidate(
                    session, op["jobs"][0]["id"], asset["id"], "video", "video"
                )
                operations.request_cancel(session, op["id"])
                chosen.append(
                    selections.select(
                        session,
                        movie_id=m["id"],
                        segment_id=segment["id"],
                        candidate_id=candidate["id"],
                        asset_id=asset["id"],
                        authority="user",
                        expected_revision=0,
                    )
                )
                segments.append(segment)
        yield app, client, m, segments, chosen


def submit(movie, **body):
    _, client, m, _, _ = movie
    return client.post(f"/api/export/movies/{m['id']}", json={"request_key": "export-test", **body})


def state(movie):
    return movie[1].get(f"/api/export/movies/{movie[2]['id']}").json()


def test_export_exact_frames_audio_provenance_and_replay(movie):
    app, client, m, segments, chosen = movie
    response = submit(movie)
    assert response.status_code == 201, response.text
    operation_id = response.json()["id"]
    assert submit(movie).status_code == 200
    # The snapshot survives subsequent direction edits and a request-key replay.
    with app.state.workspace.db.transaction() as session:
        DirectionService().update_segment(session, segments[0]["id"], 1, delivered_frames=30)
    assert submit(movie).json()["id"] == operation_id
    assert app.state.operations.run_once()
    latest = state(movie)["latest"]
    assert latest["state"] == "succeeded", latest
    assert latest["progress"] == 1
    operation = client.get(f"/api/production/operations/{operation_id}").json()
    assert operation["state"] == "succeeded" and operation["candidates"] == []
    with app.state.workspace.db.transaction() as session:
        asset = app.state.workspace.library.get(session, latest["asset_id"])
        path = app.state.workspace.library.path(session, asset["id"])
        for segment, selection in zip(segments, chosen, strict=True):
            assert SelectionService().current(session, m["id"], segment["id"]) == selection
    assert asset["kind"] == "video"
    provenance = next(p for p in asset["provenance"] if p["type"] == "movie_export")
    assert provenance["movie_revision"] == 4
    assert [s["selection_id"] for s in provenance["selections"]] == [s["id"] for s in chosen]
    assert [s["asset_id"] for s in provenance["selections"]] == [s["asset_id"] for s in chosen]
    probe = json.loads(
        subprocess.check_output(
            [
                str(app.state.workspace.media.ffprobe),
                "-v",
                "error",
                "-count_frames",
                "-show_streams",
                "-of",
                "json",
                str(path),
            ]
        )
    )
    assert (
        int(next(s for s in probe["streams"] if s["codec_type"] == "video")["nb_read_frames"]) == 62
    )
    assert len([s for s in probe["streams"] if s["codec_type"] == "audio"]) == 1
    assert submit(movie, allow_stale=True).status_code == 409


def test_missing_and_provisional_selections_refuse_without_operation(movie):
    app, _, m, segments, chosen = movie
    with app.state.workspace.db.transaction() as session:
        for segment, selection in zip(segments, chosen, strict=True):
            SelectionService().clear(
                session,
                movie_id=m["id"],
                segment_id=segment["id"],
                authority="user",
                expected_revision=1,
            )
        SelectionService().select(
            session,
            movie_id=m["id"],
            segment_id=segments[0]["id"],
            candidate_id=chosen[0]["candidate_id"],
            asset_id=chosen[0]["asset_id"],
            authority="provisional",
            basis="unjudged",
            expected_revision=2,
        )
    response = submit(movie)
    assert response.status_code == 422
    assert all(s["id"] in response.text for s in segments)
    assert state(movie)["latest"] is None
    assert all(s["selection_id"] is None for s in state(movie)["segments"])


def test_stale_requires_confirmation_and_records_reasons(movie):
    app, _, _, segments, _ = movie
    with app.state.workspace.db.transaction() as session:
        DirectionService().update_segment(session, segments[0]["id"], 1, delivered_frames=26)
    response = submit(movie)
    assert response.status_code == 422 and segments[0]["id"] in response.text
    assert "Segment changed" in response.text
    assert state(movie)["latest"] is None
    assert submit(movie, allow_stale=True).status_code == 201
    app.state.operations.run_once()
    latest = state(movie)["latest"]
    assert latest["state"] == "succeeded", latest
    with app.state.workspace.db.transaction() as session:
        asset = app.state.workspace.library.get(session, latest["asset_id"])
    assert asset["provenance"][0]["stale_segments"] == [
        {"segment_id": segments[0]["id"], "reasons": ["Segment changed since this take."]}
    ]


@pytest.mark.parametrize("when", ["queued", "encoding", "registration"])
def test_cancel_leaves_no_asset(movie, monkeypatch, when):
    app, client, _, _, _ = movie
    w = app.state.workspace
    response = submit(movie)
    assert response.status_code == 201
    operation_id = response.json()["id"]

    def cancel():
        assert client.post(f"/api/production/operations/{operation_id}/cancel").status_code == 200

    if when == "queued":
        cancel()
    elif when == "encoding":
        real = w.media.export_sequence

        def encode(items, output, width, height, cancelled, progress):
            def tick(value):
                progress(value)
                assert 0 < state(movie)["latest"]["progress"] < 1
                cancel()

            return real(items, output, width, height, cancelled, tick)

        monkeypatch.setattr(w.media, "export_sequence", encode)
    else:
        real = w.library.prepare

        def prepare(*args, **kwargs):
            result = real(*args, **kwargs)
            cancel()
            return result

        monkeypatch.setattr(w.library, "prepare", prepare)
    app.state.operations.run_once()
    latest = state(movie)["latest"]
    assert latest["state"] == "cancelled", latest
    assert latest["asset_id"] is None
    with w.db.transaction() as session:
        assert w.library.browse(session)["total"] == 2


def test_export_restart_resumes_frozen_inputs(movie, monkeypatch):
    app = movie[0]
    assert submit(movie).status_code == 201
    media = app.state.workspace.media
    real = media.export_sequence

    def pause(items, output, width, height, cancelled, progress):
        def tick(value):
            progress(value)
            app.state.operations.stop.set()

        return real(items, output, width, height, cancelled, tick)

    monkeypatch.setattr(media, "export_sequence", pause)
    assert not app.state.operations.run_once()
    assert state(movie)["latest"]["state"] == "running"
    monkeypatch.setattr(media, "export_sequence", real)
    app.state.operations.stop.clear()
    app.state.operations.recover()
    app.state.operations.run_once()
    assert state(movie)["latest"]["state"] == "succeeded"


def test_mixed_resolution_and_short_take_refuse(movie, monkeypatch):
    app, _, _, _, chosen = movie
    library = app.state.workspace.library
    real = library.get
    for patch, reason in [({"width": 320}, "mixed resolutions"), ({"duration": 0.1}, "shorter")]:

        def get(session, asset_id, patch=patch):
            asset = real(session, asset_id)
            return (
                {**asset, "info": {**asset["info"], **patch}}
                if asset_id == chosen[0]["asset_id"]
                else asset
            )

        monkeypatch.setattr(library, "get", get)
        response = submit(movie)
        assert response.status_code == 422 and reason in response.text
    with app.state.workspace.db.transaction() as session:
        assert OperationService().recent(session, kind="export") == []


def test_registration_failure_rolls_back_asset_and_retry_key(movie, monkeypatch):
    app = movie[0]
    assert submit(movie).status_code == 201
    real = app.state.workspace.library.register

    def register(*args, **kwargs):
        real(*args, **kwargs)
        raise RuntimeError("registration failed")

    monkeypatch.setattr(app.state.workspace.library, "register", register)
    app.state.operations.run_once()
    assert state(movie)["latest"]["state"] == "failed"
    with app.state.workspace.db.transaction() as session:
        assert app.state.workspace.library.browse(session)["total"] == 2


def test_cancel_racing_publication_cannot_cancel_a_published_asset(movie, monkeypatch):
    import threading
    from concurrent.futures import ThreadPoolExecutor

    from sqlalchemy import event

    app, client, _, _, _ = movie
    w = app.state.workspace
    operation_id = submit(movie).json()["id"]
    publishing, cancel_writing, release = threading.Event(), threading.Event(), threading.Event()
    register = w.library.register

    def paused_register(*args, **kwargs):
        publishing.set()
        assert release.wait(10)
        return register(*args, **kwargs)

    def writing(_connection, _cursor, statement, _parameters, _context, _many):
        if (
            statement.startswith("UPDATE generation_operations SET")
            and "cancel_requested" in statement
        ):
            cancel_writing.set()

    monkeypatch.setattr(w.library, "register", paused_register)
    event.listen(w.db.engine, "before_cursor_execute", writing)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            export = pool.submit(app.state.operations.run_once)
            assert publishing.wait(15)
            cancel = pool.submit(client.post, f"/api/production/operations/{operation_id}/cancel")
            try:
                assert cancel_writing.wait(5)
            finally:
                release.set()
            assert export.result(timeout=15)
            response = cancel.result(timeout=15)
            assert response.status_code == 200
            assert response.json()["state"] == "succeeded"
            assert response.json()["cancel_requested"] is False
        latest = state(movie)["latest"]
        assert latest["state"] == "succeeded" and latest["asset_id"]
    finally:
        release.set()
        event.remove(w.db.engine, "before_cursor_execute", writing)
