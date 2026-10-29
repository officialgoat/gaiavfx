"""Fake ComfyUI operation, replay, artifact schema, and stage-boundary recovery."""

import json

import pytest
from PIL import Image
from pydantic import BaseModel, ConfigDict, Field
from test_replacement_runner import drain, read
from test_replacement_runner import work as work  # noqa: PLC0414 - shared fixture

from gaiavfx.application.film_analysis import (
    film_analysis_report,
    film_faces,
    start_film_analysis,
)
from gaiavfx.application.operations import OperationRunner
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class Shot(StrictModel):
    index: int
    start: int
    end: int
    poster_artifact: str | None


class Track(StrictModel):
    id: str
    shot: int
    frames: list[int]
    faces: list[int]
    boxes: list[list[float]]
    face_count: int
    quality: float
    crop_artifact: str | None


class Cluster(StrictModel):
    id: str
    track_ids: list[str]
    screen_time_frames: int
    shot_count: int
    presence: list[list[int]]
    face_artifact: str | None


class FilmReport(StrictModel):
    schema_: int = Field(alias="schema")
    source_asset: str
    fps: list[int]
    frame_count: int
    width: int
    height: int
    letterbox: list[int] | None
    shots: list[Shot]
    tracks: list[Track]
    clusters: list[Cluster]
    unknown_track_ids: list[str]
    suggestions: list[dict]
    params: dict


def start(work, key="film"):
    db, library, _, assets, _ = work
    with db.transaction() as session:
        return start_film_analysis(session, library, assets[0], request_key=key)


def test_film_report_schema_replay_and_artifacts(work, monkeypatch):
    db, library, runner, assets, _ = work
    passes = []
    stream_frames = library.media.stream_frames

    def stream(*args, **kwargs):
        passes.append(kwargs)
        yield from stream_frames(*args, **kwargs)

    monkeypatch.setattr(library.media, "stream_frames", stream)
    monkeypatch.setattr(library.media, "frame", lambda *args: pytest.fail("per-crop seek"))
    operation = start(work)
    assert start(work)["id"] == operation["id"]
    with db.transaction() as session:
        assert film_analysis_report(session, library, operation["id"]) is None
        with pytest.raises(ApplicationError, match="not complete"):
            film_faces(session, library, operation["id"])
    drain(runner)
    operation = read(db, operation["id"])
    assert operation["state"] == "succeeded", operation
    assert operation["budget_reserved"] == 0 and not operation["candidates"]
    # shots (quarter scale), posters, the sampled proxy (frames piped from a decode), crops
    assert [(p["width"], p["height"]) for p in passes] == [
        (128, 80),
        (480, 300),
        (512, 320),
        (512, 320),
    ]
    assert [j["step"] for j in operation["jobs"]] == ["film_shots", "film_proxy", None, "film_cast"]
    with db.transaction() as session:
        report = film_analysis_report(session, library, operation["id"])
        FilmReport.model_validate(report)
        assert report["source_asset"] == assets[0]
        assert report["frame_count"] == 53 and report["fps"] == [24, 1]
        assert all(abs(rows - 20) <= 4 for rows in report["letterbox"])
        assert report["params"]["detection"]["source"] == "sampled_proxy"
        assert all(n < report["params"]["max_upload_bytes"] for n in runner.comfy.upload_sizes)
        assert all(name.endswith("/proxy.mp4") for _, name in runner.comfy.uploads)
        assert all(
            j["inputs"]["proxy"]["source"] == "job_artifact"
            for j in operation["jobs"]
            if j["executor"] == "comfy"
        )
        faces = film_faces(session, library, operation["id"])
        assert faces["frame"].tolist() == list(range(0, 53, 4))
        for group, key, size in (
            (report["tracks"], "crop_artifact", 128),
            (report["clusters"], "face_artifact", 256),
        ):
            for item in group:
                artifact = library.artifact(session, item[key])
                assert artifact["kind"] == "visual_evidence"
                with Image.open(library.artifact_path(session, item[key])) as image:
                    assert image.size == (size, size)
        cast_id = next(
            o["artifact_id"] for o in operation["jobs"][-1]["outputs"] if o["output_key"] == "cast"
        )
        cast_bytes = library.read_blob(session, cast_id)
        # No detection arrays leaked into operation snapshots or other JSON tables.
        assert '"embedding": [' not in json.dumps(operation)
        assert '"pixels":' not in json.dumps(operation)
    assert start(work)["id"] == operation["id"]
    second = start(work, "film-again")
    drain(runner)
    with db.transaction() as session:
        again = read(db, second["id"])
        second_cast = next(
            o["artifact_id"] for o in again["jobs"][-1]["outputs"] if o["output_key"] == "cast"
        )
        assert library.read_blob(session, second_cast) == cast_bytes


def test_restart_after_shots_appends_scan_once(work, monkeypatch):
    db, library, runner, _, _ = work
    operation = start(work)
    with monkeypatch.context() as patch:
        patch.setattr(runner, "_advance", lambda *args: False)
        assert runner.run_once()
    assert read(db, operation["id"])["state"] == "awaiting_review"
    restarted = OperationRunner(db, runner.comfy, library, "http://fake", poll_seconds=0)
    restarted.recover()
    restarted.recover()
    assert len(read(db, operation["id"])["jobs"]) == 3
    drain(restarted)
    assert read(db, operation["id"])["state"] == "succeeded"
    assert len(runner.comfy.posts) == 1


def test_scan_failure_and_cancellation_do_not_publish_report(work):
    db, library, runner, _, _ = work
    operation = start(work)
    runner.comfy.execution_error = True
    drain(runner)
    with db.transaction() as session:
        assert film_analysis_report(session, library, operation["id"]) is None
    failed = read(db, operation["id"])
    assert failed["jobs"][-1]["state"] == "failed"
    # Regression (8 Oct real-film trial): a stopped stage settles; it never waits forever.
    assert failed["state"] in {"failed", "partial"} and not failed["awaits_review"]
    cancelled = start(work, "cancel")
    with db.transaction() as session:
        OperationService().request_cancel(session, cancelled["id"])
    drain(runner)
    assert read(db, cancelled["id"])["state"] == "cancelled"


def test_non_video_and_conflicting_request_rejected(work):
    db, library, _, assets, _ = work
    start(work)
    with db.transaction() as session:
        with pytest.raises(ApplicationError, match="different operation"):
            start_film_analysis(session, library, assets[1], request_key="film")
        with pytest.raises(ApplicationError, match="requires a Library video"):
            start_film_analysis(session, library, assets[1], request_key="image")


def test_multiple_proxy_chunks_merge_and_shutdown_resume(work, monkeypatch):
    from gaiavfx.application import film_analysis
    from gaiavfx.application.recast_steps import film_proxy

    db, library, runner, _, _ = work
    parse = film_analysis.parse_film_policy
    monkeypatch.setattr(
        film_analysis, "parse_film_policy", lambda text: {**parse(text), "scan_chunk_frames": 5}
    )
    operation = start(work)
    assert runner.run_once()  # shots append three proxy/scan pairs
    stopped = False

    def shutdown(context):
        nonlocal stopped
        if not stopped:
            stopped = True
            runner.stop.set()
            raise ApplicationError("media stopped")
        return film_proxy(context)

    runner.steps = {**runner.steps, "film_proxy": shutdown}
    assert not runner.run_once()
    assert read(db, operation["id"])["jobs"][1]["state"] == "running"
    restarted = OperationRunner(db, runner.comfy, library, "http://fake", poll_seconds=0)
    restarted.recover()
    drain(restarted)
    operation = read(db, operation["id"])
    assert operation["state"] == "succeeded", operation
    assert len(operation["jobs"]) == 8
    assert len(runner.comfy.uploads) == 3
    with db.transaction() as session:
        assert film_faces(session, library, operation["id"])["frame"].tolist() == list(
            range(0, 53, 4)
        )
        assert (
            film_analysis_report(session, library, operation["id"])["params"]["detection"]["chunks"]
            == 3
        )
        with pytest.raises(ApplicationError, match="incomplete stage"):
            film_analysis.advance_film_analysis(
                session, {"jobs": [{"state": "failed"}, {"state": "running"}]}, library=library
            )
        assert OperationService().ready_to_advance(session, ("unregistered",)) == []
