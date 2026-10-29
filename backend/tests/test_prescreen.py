"""Honest measurements, frozen expectations, collection recovery and blind review."""

import json
import subprocess
import time
from contextlib import contextmanager
from copy import deepcopy

import pytest
import test_review_sets as review_fixtures
from fake_comfy import Crash, FakeComfy
from fastapi.testclient import TestClient
from test_production_api import AUTH, context, setup, source_assets
from test_render_cut import add_cut, document

from gaiavfx.application.authored import import_segment
from gaiavfx.application.candidate_review import review_taxonomy
from gaiavfx.application.render_cut import start_render
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.prescreen import (
    BLACK_THRESHOLDS,
    CHECKS,
    FREEZE_THRESHOLDS,
    VERSION,
    evaluate,
)
from gaiavfx.modules.generation.operations.review import PURPOSES, ReviewService, failure_tags
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

prepared = review_fixtures.prepared

EXPECT = {"kind": "video", "frames": 102, "fps": 24, "width": 768, "height": 448, "audio": True}
MEASURED = {
    "kind": "video",
    "frames_counted": 102,
    "frames_declared": 102,
    "duration": 102 / 24,
    "width": 768,
    "height": 448,
    "has_audio": True,
    "black_frames": [],
    "frozen_frames": [],
}
CALIBRATED = {"decodes", "frame_count", "duration", "resolution", "audio_present"}


def indexed(kind="video", expect=EXPECT, measured=MEASURED):
    return {c["check"]: c for c in evaluate(kind, expect, measured)}


def prescreen_document(measured=MEASURED):
    return {
        "version": VERSION,
        "measured_at": "2026-10-02T10:00:00+00:00",
        "checks": evaluate("video", EXPECT, measured),
    }


def test_calibrated_checks_and_evidence():
    checks = indexed()
    assert set(checks) == set(CHECKS)
    assert {k for k, c in checks.items() if c["verdict"] == "pass"} == CALIBRATED
    assert all(c["verdict"] == "unsupported" for k, c in checks.items() if k not in CALIBRATED)
    assert checks["duration"]["evidence"] == {
        "expected_seconds": 4.25,
        "measured_seconds": 4.25,
        "fps": 24,
    }
    assert checks["decodes"]["evidence"] == {"frames_counted": 102, "frames_declared": 102}
    assert checks["frame_count"]["evidence"] == {"expected": 102, "measured": 102}
    assert checks["resolution"]["evidence"] == {"expected": [768, 448], "measured": [768, 448]}
    assert checks["audio_present"]["evidence"] == {"expected": True, "measured": True}
    assert checks["black_frames"]["evidence"] == {
        "ranges": [],
        "detector": "blackdetect",
        "thresholds": BLACK_THRESHOLDS,
    }
    assert checks["frozen_frames"]["evidence"] == {
        "ranges": [],
        "detector": "freezedetect",
        "thresholds": FREEZE_THRESHOLDS,
    }


@pytest.mark.parametrize(
    ("change", "failed"),
    [
        (
            {"frames_counted": 101, "frames_declared": 101, "duration": 101 / 24},
            {"frame_count", "duration"},
        ),
        ({"frames_counted": 101}, {"decodes", "frame_count"}),
        ({"has_audio": False}, {"audio_present"}),
        ({"width": 32}, {"resolution"}),
    ],
)
def test_exact_comparisons(change, failed):
    assert {
        k for k, c in indexed(measured=MEASURED | change).items() if c["verdict"] == "fail"
    } == failed


def test_duration_strict_half_frame_boundary():
    # Exactly representable values distinguish '<' from '<=' without float roundoff.
    expect = EXPECT | {"frames": 8, "fps": 4}
    assert (
        indexed(expect=expect, measured=MEASURED | {"duration": 1.875})["duration"]["verdict"]
        == "fail"
    )
    assert (
        indexed(expect=expect, measured=MEASURED | {"duration": 1.876})["duration"]["verdict"]
        == "pass"
    )


def test_missing_expectations_images_and_measurement_errors():
    checks = indexed(expect=None)
    assert checks["decodes"]["verdict"] == "pass"
    for check in CALIBRATED - {"decodes"}:
        assert checks[check]["verdict"] == "unsupported"
        assert checks[check]["evidence"] == {"reason": "no expectation recorded"}
    image = indexed(
        "image", {"width": 32, "height": 24}, {"kind": "image", "width": 32, "height": 24}
    )
    assert set(image) == {"decodes", "resolution", "facing", "markers_visible"}
    assert image["decodes"]["verdict"] == image["resolution"]["verdict"] == "pass"
    errors = indexed(measured={"error": "x" * 400})
    for check, row in errors.items():
        assert row["verdict"] == "unsupported"
        assert row["evidence"] == (
            {"error": "x" * 300}
            if check in CALIBRATED | {"black_frames", "frozen_frames"}
            else checks[check]["evidence"]
        )


def test_taxonomy_purposes_and_ranking(prepared):
    tags = failure_tags()
    replacement = {k for k, row in tags.items() if row["applies_to"] == ["character_replacement"]}
    # ADR 0011: six character-replacement tags apply only to that review purpose.
    assert len(replacement) == 6
    assert len(tags) == 25 + len(replacement)
    assert {
        "wrong_direction",
        "wrong_side",
        "markers_visible",
        "layout_inconsistent",
        "position_reset",
        "placement",
        "duplicate_subject",
        "extra_cut",
        "pre_cut_disturbed",
        "detail_drift",
    } <= tags.keys()
    assert all(
        row["label"] and set(row["applies_to"]) <= PURPOSES and row["applies_to"]
        for row in tags.values()
    )
    assert review_taxonomy()["schema"] == 2
    db, _, ids = prepared
    with db.transaction() as session:
        service = ReviewService()
        for purpose in ("segment_takes", "angle_views"):
            review = service.create(session, purpose=purpose, candidate_ids=ids[:1], context={})
            values = {
                "expected_revision": 0,
                "tiers": [ids[:1]],
                "usable_tiers": 0,
                "notes": {},
                "tags": {ids[0]: ["penetration"]},
                "general": "",
            }
            if purpose == "segment_takes":
                assert (
                    service.save_ranking(session, review["id"], **values)["tags"] == values["tags"]
                )
            else:
                with pytest.raises(ApplicationError, match="penetration.*angle_views") as error:
                    service.save_ranking(session, review["id"], **values)
                assert error.value.status == 422


@pytest.mark.parametrize(
    "rows",
    [
        'id = "same"\nlabel = "wrong"\napplies_to = ["unknown"]',
        'id = "same"\nlabel = " "\napplies_to = ["segment_takes"]',
        'id = "same"\nlabel = "wrong"\napplies_to = []',
        'id = "same"\nlabel = "wrong"\napplies_to = ["segment_takes"]\n[[tag]]\nid = "same"\nlabel = "duplicate"\napplies_to = ["segment_takes"]',
    ],
)
def test_taxonomy_rejects_invalid_schema_two_rows(monkeypatch, rows):
    from gaiavfx.modules.generation.operations import review

    class TaxonomyFile:
        def joinpath(self, _name):
            return self

        def read_text(self, _encoding):
            return "schema = 2\n[[tag]]\n" + rows

    monkeypatch.setattr(review.resources, "files", lambda _package: TaxonomyFile())
    failure_tags.cache_clear()
    try:
        with pytest.raises(ApplicationError) as error:
            failure_tags()
        assert error.value.status == 422
    finally:
        failure_tags.cache_clear()


def test_blind_members_and_ranking_ignore_failed_prescreens(prepared):
    db, _, ids = prepared
    with db.transaction() as session:
        service = ReviewService()
        before = service.create(session, purpose="segment_takes", candidate_ids=ids, context={})
        document = prescreen_document(MEASURED | {"frames_counted": 1, "duration": 1 / 24})
        OperationService().record_prescreen(session, ids[0], document)
        after = service.blind(session, before["id"])
        measured = next(m for m in after["members"] if m["candidate_id"] == ids[0])["prescreen"]
        assert measured == document
        assert not any(
            f'"{key}"' in json.dumps(measured)
            for key in (
                "job_id",
                "operation_id",
                "seed",
                "integration",
                "technique",
                "graph",
                "request",
            )
        )
        for member in after["members"]:
            member["prescreen"] = None
        assert before == after
        with pytest.raises(ApplicationError, match="Rank every candidate"):
            service.reveal(session, before["id"])


def test_prescreen_validation_and_rollback(prepared):
    db, _, ids = prepared
    service = OperationService()
    invalid = [
        None,
        {},
        prescreen_document() | {"version": True},
        prescreen_document() | {"measured_at": "not a timestamp"},
        prescreen_document() | {"checks": []},
    ]
    for key, value in (
        ("check", "unknown"),
        ("verdict", "probably"),
        ("evidence", []),
        ("evidence", {"nested": [{"seed": 1}]}),
        ("evidence", {"value": float("nan")}),
    ):
        document = prescreen_document()
        document["checks"][0][key] = value
        invalid.append(document)
    for document in invalid:
        with db.transaction() as session:
            with pytest.raises(ApplicationError) as error:
                service.record_prescreen(session, ids[0], document)
            assert error.value.status == 422
            assert service.candidate(session, ids[0])["prescreen"] is None
    with pytest.raises(RuntimeError), db.transaction() as session:
        service.record_prescreen(session, ids[0], prescreen_document())
        raise RuntimeError("rollback")
    with db.transaction() as session:
        assert service.candidate(session, ids[0])["prescreen"] is None
        with pytest.raises(ApplicationError) as error:
            service.record_prescreen(session, "unknown", prescreen_document())
        assert error.value.status == 404


def clip(path, frames, *, source="testsrc2=size=768x448:rate=24", audio_seconds=None):
    subprocess.run(
        [
            str(MediaTools().ffmpeg),
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            source,
            "-f",
            "lavfi",
            "-i",
            f"anullsrc=r=48000:cl=stereo:d={audio_seconds or frames / 24}",
            "-frames:v",
            str(frames),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-c:a",
            "aac",
            str(path),
        ],
        check=True,
    )
    return path


def test_scan_uses_picture_duration_and_reports_uncalibrated_ranges(tmp_path):
    path = clip(
        tmp_path / "black.mp4", 72, source="color=black:size=32x24:rate=24", audio_seconds=5
    )
    media = MediaTools()
    extended = tmp_path / "long-audio.mp4"
    subprocess.run(
        [
            str(media.ffmpeg),
            "-v",
            "error",
            "-i",
            str(path),
            "-f",
            "lavfi",
            "-i",
            "anullsrc=r=48000:cl=stereo:d=5",
            "-map",
            "0:v:0",
            "-map",
            "1:a:0",
            "-c:v",
            "copy",
            "-c:a",
            "aac",
            str(extended),
        ],
        check=True,
    )
    streams = json.loads(
        media.run(media.ffprobe, ["-v", "error", "-show_streams", "-of", "json", str(extended)])
    )["streams"]
    assert float(next(s["duration"] for s in streams if s["codec_type"] == "audio")) == 5
    measured = media.scan_video(extended, fps=24)
    assert measured["duration"] == 3
    assert measured["frames_counted"] == measured["frames_declared"] == 72
    for check in ("black_frames", "frozen_frames"):
        assert measured[check] and measured[check][0][0] == 0
        assert indexed(measured=measured)[check]["verdict"] == "unsupported"
    expected = EXPECT | {"frames": 73}
    assert indexed(expect=expected, measured=measured)["duration"]["verdict"] == "fail"
    corrupt = tmp_path / "corrupt.mp4"
    corrupt.write_bytes(b"not a video")
    with pytest.raises(ApplicationError):
        media.scan_video(corrupt, fps=24)


def test_scan_396_frame_cost(tmp_path):
    path = clip(tmp_path / "long.mp4", 396)
    start = time.perf_counter()
    measured = MediaTools().scan_video(path, fps=24)
    elapsed = time.perf_counter() - start
    print(f"scan_video 396 frames: {elapsed:.3f}s")
    assert measured["frames_counted"] == 396


def test_render_collection_resume_errors_and_on_demand(tmp_path, monkeypatch):
    paths = {frames: clip(tmp_path / f"clip-{frames}.mp4", frames) for frames in (107, 106)}
    app = setup(tmp_path / "library")
    with TestClient(app, base_url="http://localhost") as client:
        assets, _ = source_assets(app, tmp_path)
        db, library = app.state.workspace.db, app.state.workspace.library
        service = OperationService()
        with db.transaction() as session:
            direction = DirectionService()
            movie = direction.create_movie(session, "Measure")
            authored = deepcopy(document())
            authored["subjects"][0]["identity_asset"] = assets[0]
            authored["continues"] = False
            authored["delivered_frames"] = 100
            segment = import_segment(session, movie["id"], 1, authored)
            cut = add_cut(direction, session, segment)
            operations = [
                start_render(session, cut_id=cut["id"], seeds=[seed]) for seed in (1, 2, 3)
            ]
        for operation in operations:
            assert operation["jobs"][0]["snapshot"]["expect"] == EXPECT | {"frames": 107}
            assert "expect" not in operation["request"]
        calls = []
        scan = library.media.scan_video
        transaction = db.transaction
        active = 0

        @contextmanager
        def tracked_transaction():
            nonlocal active
            active += 1
            try:
                with transaction() as session:
                    yield session
            finally:
                active -= 1

        monkeypatch.setattr(db, "transaction", tracked_transaction)

        def measured(path, **kwargs):
            assert active == 0, "measurement must run outside database transactions"
            calls.append(path)
            if len(calls) == 3:
                raise OSError("missing measurement tool")
            return scan(path, **kwargs)

        monkeypatch.setattr(library.media, "scan_video", measured)

        def outputs(graph):
            seed = graph["noise"]["inputs"]["noise_seed"]
            return (
                {"save": {"gifs": [{"filename": "clip.mp4", "type": "output"}]}},
                {
                    "save:gifs:0": paths[106 if seed == 2 else 107].read_bytes(),
                    "context": context(),
                },
            )

        app.state.operations.comfy = FakeComfy(outputs, polls_to_complete=1)
        app.state.operations.poll_seconds = 0
        # Stop after measurement but before the final transition, then resume collection.
        transition = app.state.operations._transition

        def crash(job, state, **kwargs):
            if state == "succeeded":
                raise Crash("after prescreen")
            return transition(job, state, **kwargs)

        monkeypatch.setattr(app.state.operations, "_transition", crash)
        with pytest.raises(Crash):
            app.state.operations.run_once()
        with db.transaction() as session:
            candidate = service.candidates(session, operations[0]["id"])[0]
        route = f"/api/production/candidates/{candidate['id']}/prescreen"
        assert client.post(route, headers=AUTH).status_code == 409
        monkeypatch.setattr(app.state.operations, "_transition", transition)
        for _ in operations:
            app.state.operations.run_once()
        assert len(calls) == 3
        with db.transaction() as session:
            saved = [service.operation(session, op["id"]) for op in operations]
        assert all(op["state"] == "succeeded" and len(op["candidates"]) == 1 for op in saved)
        documents = [op["candidates"][0]["prescreen"] for op in saved]
        assert {c["check"] for c in documents[0]["checks"] if c["verdict"] == "pass"} == CALIBRATED
        assert {c["check"] for c in documents[1]["checks"] if c["verdict"] == "fail"} == {
            "frame_count",
            "duration",
        }
        assert all(c["verdict"] == "unsupported" for c in documents[2]["checks"])
        response = client.post(route, headers=AUTH)
        assert response.status_code == 200, response.text
        assert len(calls) == 4
        assert response.json()["measured_at"] != documents[0]["measured_at"]
        assert (
            client.post("/api/production/candidates/unknown/prescreen", headers=AUTH).status_code
            == 404
        )
        assert client.get("/api/production/review-tags", headers=AUTH).json() == review_taxonomy()

        def missing_file(_session, _asset_id):
            raise ApplicationError("This media file is missing. Restore its library copy.", 404)

        monkeypatch.setattr(library, "path", missing_file)
        response = client.post(route, headers=AUTH)
        assert response.status_code == 200, response.text
        checks = response.json()["checks"]
        assert all(check["verdict"] == "unsupported" for check in checks)
        assert "media file is missing" in checks[0]["evidence"]["error"]
