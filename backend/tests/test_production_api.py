"""The rebuilt production slice is usable through the running API."""

import io
import json
import struct
import subprocess
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from fake_comfy import FakeComfy
from fastapi.testclient import TestClient
from PIL import Image
from sqlalchemy import event, text

from gaiavfx.api.app import create_app
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService
from gaiavfx.shared.errors import ApplicationError

MIGRATIONS = Path(__file__).parents[1] / "migrations"
AUTH = {"Authorization": "Bearer test-token"}


def png(color):
    stream = io.BytesIO()
    Image.new("RGB", (32, 24), color).save(stream, "PNG")
    return stream.getvalue()


def context():
    header = json.dumps(
        {
            "__metadata__": {"format": "h3_motion_context_av_v1"},
            "video": {"shape": [1, 1, 1, 1, 1]},
            "audio": {"shape": [1, 1, 1, 1]},
        }
    ).encode()
    return struct.pack("<Q", len(header)) + header


def post(client, path, body=None, status=201):
    response = client.post(path, json=body, headers=AUTH)
    assert response.status_code == status, response.text
    return response.json()


def get(client, path, params=None, status=200):
    response = client.get(path, params=params, headers=AUTH)
    assert response.status_code == status, response.text
    return response.json()


def post_idempotently(client, path, body):
    body = body | {"request_key": path}
    first = post(client, path, body)
    assert post(client, path, body, status=200) == first
    return first


def run_idle(app):
    app.state.operations.poll_seconds = 0
    for _ in range(30):
        if not app.state.operations.run_once():
            return
    raise AssertionError("operation queue did not drain")


def setup(tmp_path, *, enable_runner=False):
    LibraryService(tmp_path, MIGRATIONS).create("Production API")
    app = create_app(tmp_path, MIGRATIONS, "test-token", enable_runner=enable_runner)
    return app


def source_assets(app, tmp_path):
    assets = []
    for index in range(3):
        source = tmp_path / f"source-{index}.png"
        source.write_bytes(png((index * 40, 20, 100)))
        prepared = app.state.workspace.library.prepare(source, source.name)
        with app.state.workspace.db.transaction() as session:
            assets.append(
                app.state.workspace.library.register(session, prepared, {"type": "test"})["id"]
            )
    latent = tmp_path / "context.safetensors"
    latent.write_bytes(context())
    prepared = app.state.workspace.library.prepare_artifact(latent)
    with app.state.workspace.db.transaction() as session:
        artifact = app.state.workspace.library.register_artifact(session, prepared)["id"]
    return assets, artifact


def test_plate_removal_review_approval_and_survey_http(tmp_path):
    app = setup(tmp_path)
    app.state.operations.comfy = FakeComfy(
        lambda graph: (
            {"save": {"images": [{"filename": "plate.png", "type": "output"}]}},
            {"save:images:0": png((graph["sample"]["inputs"]["seed"], 70, 80))},
        ),
        polls_to_complete=1,
    )
    with TestClient(app, base_url="http://localhost") as client:
        assets, _ = source_assets(app, tmp_path)
        place = post(client, "/api/direction/places", {"name": "Courtyard"})
        gate, reverse = [
            post(client, f"/api/direction/places/{place['id']}/angles", {"name": name})["id"]
            for name in ("Gate", "Reverse")
        ]
        body = {
            "place_id": place["id"],
            "angle_id": gate,
            "source_asset": assets[0],
            "wording": {"whom": "the walker", "object_pronoun": "him", "behind": "the rail"},
            "seeds": [21, 22],
            "request_key": "removal-http",
        }
        for invalid in (
            body | {"extra": True},
            body | {"frame": 0},
            body | {"frame": True},
            body | {"wording": {"whom": "walker"}},
        ):
            post(client, "/api/production/removals", invalid, status=422)
        removal = post(client, "/api/production/removals", body)
        assert post(client, "/api/production/removals", body, status=200) == removal
        post(client, "/api/production/removals", body | {"seeds": [23]}, status=409)
        review_path = f"/api/production/removals/{removal['id']}/review"
        post(client, review_path, status=409)
        run_idle(app)
        review = post(client, review_path, status=200)
        assert review["purpose"] == "plate" and len(review["members"]) == 2
        assert post(client, review_path, status=200) == review
        assert all("seed" not in member and "job_id" not in member for member in review["members"])
        operation = get(client, f"/api/production/operations/{removal['id']}")
        raw = next(c for c in operation["candidates"] if c["output_key"] != "fitted")
        post(client, "/api/production/plates/approve", {"candidate_id": raw["id"]}, status=422)
        picked, other = review["members"]
        assert get(client, f"/api/direction/places/{place['id']}")["plate_asset_id"] is None
        approved = post(
            client,
            "/api/production/plates/approve",
            {"candidate_id": picked["candidate_id"]},
            status=200,
        )
        assert (
            approved["plate_asset_id"] == picked["asset_id"] and approved["plate_angle_id"] == gate
        )
        assert (
            post(
                client,
                "/api/production/plates/approve",
                {"candidate_id": picked["candidate_id"]},
                status=200,
            )
            == approved
        )
        post(
            client,
            "/api/production/plates/approve",
            {"candidate_id": other["candidate_id"]},
            status=409,
        )
        assert get(client, f"/api/direction/places/{place['id']}") == approved
        survey = post(
            client,
            "/api/production/surveys",
            {
                "place_id": place["id"],
                "seed": 1,
                "plan": {
                    "what": "an empty courtyard",
                    "sound": "wind",
                    "first_view": "gate",
                    "first_angle_id": gate,
                    "first_frame_asset": approved["plate_asset_id"],
                    "views": [[reverse, "reverse view"]],
                    "overhead": "straight down",
                },
            },
        )
        assert survey["state"] == "queued"
        with app.state.workspace.db.transaction() as session:
            stored = OperationService().operation(session, survey["id"])
            assert set(stored["jobs"][0]["inputs"]) == {approved["plate_asset_id"]}
        post(client, f"/api/production/removals/{survey['id']}/review", status=409)


def test_complete_http_slice_and_guards(tmp_path):
    app = setup(tmp_path)
    video = tmp_path / "take.mp4"
    subprocess.run(
        [
            str(MediaTools().ffmpeg),
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=32x32:rate=24:duration=5.2",
            "-c:v",
            "libx264",
            str(video),
        ],
        check=True,
    )

    def outputs(graph):
        if "TextEncodeQwenImageEditPlus" in {node["class_type"] for node in graph.values()}:
            seed = graph["sample"]["inputs"]["seed"]
            return {"save": {"images": [{"filename": "edit.png", "type": "output"}]}}, {
                "save:images:0": png((seed, 40, 60))
            }
        return {
            "save_video": {
                "images": [{"filename": "take.mp4", "type": "output"}],
                "animated": [True],
            }
        }, {"save_video:images:0": video.read_bytes(), "context": context()}

    app.state.operations.comfy = FakeComfy(outputs, polls_to_complete=1)
    with TestClient(app, base_url="http://localhost") as client:
        assets, artifact = source_assets(app, tmp_path)
        place = post(client, "/api/direction/places", {"name": "Courtyard"})["id"]
        assert get(client, "/api/direction/places")[0]["id"] == place
        gate, along, reverse = [
            post(client, f"/api/direction/places/{place}/angles", {"name": name})["id"]
            for name in ("Gate", "Along", "Reverse")
        ]
        segment = {
            "subjects": [
                {
                    "id": "arin",
                    "name": "Arin",
                    "description": "a walker",
                    "keeps": ["grey coat"],
                    "identity_asset": assets[0],
                }
            ],
            "place": {
                "noun": "courtyard",
                "description": "A quiet courtyard.",
                "landmarks": "rail and gate",
            },
            "shots": [
                {"description": "Walk.", "screen_direction": "forward", "sound": "steps"},
                {"description": "Turn.", "screen_direction": "left", "sound": "steps"},
            ],
            "cut_frames": [36],
            "delivered_frames": 102,
            "continues": True,
        }
        movie = post(client, "/api/direction/movies", {"title": "Courtyard"})
        subject = post(
            client,
            f"/api/direction/movies/{movie['id']}/subjects",
            {
                "expected_revision": movie["revision"],
                "id": "arin",
                "name": "Arin",
                "description": "a walker",
                "keeps": ["grey coat"],
                "identity_asset_id": assets[0],
            },
        )
        stored_segment = post(
            client,
            f"/api/direction/movies/{movie['id']}/segments",
            {
                "expected_revision": subject["receipt"]["after"]["revision"],
                "content": {"subjects": ["arin"], "place": segment["place"]},
                "delivered_frames": 102,
                "continues": True,
                "phases": [
                    {"start_frame": start, "content": shot}
                    for start, shot in zip([0, 36], segment["shots"], strict=True)
                ],
            },
        )
        cut = post(
            client,
            "/api/direction/cuts",
            {
                "segment_id": stored_segment["id"],
                "expected_revision": stored_segment["revision"],
                "frame": 36,
                "from_angle_id": gate,
                "to_angle_id": reverse,
                "relation": "reverse",
                "crosses_barrier": True,
                "relation_source": "authored",
                "receives_predecessor": True,
            },
        )
        assert get(client, f"/api/direction/cuts/{cut['id']}/plan")["recommended"]["ready"] is False
        assert (
            client.patch(
                f"/api/direction/cuts/{cut['id']}",
                json={"expected_revision": 2, "fidelity": True},
                headers=AUTH,
            ).status_code
            == 409
        )
        assert get(client, f"/api/direction/segments/{stored_segment['id']}/assembled") == segment
        assert (
            client.post(
                "/api/production/renders",
                headers=AUTH,
                json={"cut_id": cut["id"], "seeds": [1], "segment": segment},
            ).status_code
            == 422
        )
        early = client.post(
            "/api/production/renders",
            json={
                "cut_id": cut["id"],
                "seeds": [1],
                "context_artifact": artifact,
            },
            headers=AUTH,
        )
        assert early.status_code == 409 and "cut_not_ready: Cut " in early.json()["detail"]
        survey = post_idempotently(
            client,
            "/api/production/surveys",
            {
                "place_id": place,
                "seed": 1,
                "plan": {
                    "what": "an empty courtyard",
                    "sound": "wind",
                    "first_view": "gate",
                    "first_angle_id": gate,
                    "first_frame_asset": assets[1],
                    "views": [[along, "along the wall"], [reverse, "reverse view"]],
                    "overhead": "straight down",
                },
            },
        )
        assert "graph" not in json.dumps(survey)
        assert "inputs" not in json.dumps(survey)
        assert "snapshot" not in json.dumps(survey)
        assert set(survey["jobs"][0]) == {
            "ordinal",
            "kind",
            "executor",
            "integration",
            "step",
            "state",
            "seed",
            "error",
        }
        assert get(client, f"/api/v1/assets/{assets[0]}")["id"] == assets[0]
        run_idle(app)
        saved = get(client, f"/api/production/operations/{survey['id']}")
        assert saved["state"] == "succeeded" and len(saved["candidates"]) == 6
        candidate_asset = saved["candidates"][0]["asset_id"]
        assert get(client, f"/api/v1/assets/{candidate_asset}")["id"] == candidate_asset
        assert "original" in get(client, f"/api/v1/assets/{candidate_asset}/urls")
        assert (
            get(client, "/api/production/operations", {"kind": "survey"})[0]["id"] == survey["id"]
        )
        views = post(client, f"/api/production/surveys/{survey['id']}/review", status=200)
        assert len(views["members"]) == 3
        extracted = {item["output_key"]: item for item in saved["candidates"]}
        target = post(
            client,
            "/api/production/views/approve",
            {"candidate_id": extracted[reverse]["id"]},
            status=200,
        )
        along_view = post(
            client,
            "/api/production/views/approve",
            {"candidate_id": extracted[along]["id"]},
            status=200,
        )
        wording = {
            "who": "Arin",
            "looks": "grey coat",
            "pronouns": "he",
            "facing": "screen_left",
            "plate": "empty courtyard",
            "keep": "walls",
            "where": "by rail",
            "size": "one third",
            "distance_description": "mid-distance",
            "along": "the rail",
            "barrier": "the rail",
            "feet_placement": "Both feet on stone.",
            "occlude": "Do not occlude.",
        }
        insertion = post_idempotently(
            client,
            "/api/production/insertions",
            {
                "cut_id": cut["id"],
                "evidence_id": target["id"],
                "subject_asset": assets[0],
                "wording": wording,
                "seeds": [2, 3],
            },
        )
        run_idle(app)
        pins = post(client, f"/api/production/insertions/{insertion['id']}/review", status=200)
        post(
            client,
            "/api/production/pins/approve",
            {"candidate_id": pins["members"][0]["candidate_id"]},
            status=200,
        )
        sheet = post_idempotently(
            client,
            "/api/production/sheets",
            {
                "place_id": place,
                "tiles": [along_view["id"], target["id"], [32, 32]],
                "origin": "survey",
            },
        )
        run_idle(app)
        sheet_asset = get(client, f"/api/production/operations/{sheet['id']}")["candidates"][0][
            "asset_id"
        ]
        assert get(client, f"/api/direction/cuts/{cut['id']}/plan")["recommended"]["ready"]
        rendered = post_idempotently(
            client,
            "/api/production/renders",
            {
                "cut_id": cut["id"],
                "seeds": [1, 2, 3],
                "sheet_asset": sheet_asset,
                "context_artifact": artifact,
            },
        )
        run_idle(app)
        assert get(client, f"/api/production/operations/{rendered['id']}")["state"] == "succeeded"
        review = post(client, f"/api/production/renders/{rendered['id']}/review", status=200)
        assert all("graph" not in member for member in review["members"])
        review_id = review["id"]
        assert get(client, f"/api/production/reviews/{review_id}/reveal", status=409)["detail"]
        candidates = [member["candidate_id"] for member in review["members"]]
        ranking_body = {
            "expected_revision": 0,
            "tiers": [[item] for item in candidates],
            "usable_tiers": 1,
            "notes": {},
            "tags": {},
            "general": "",
        }
        response = client.put(
            f"/api/production/reviews/{review_id}/ranking", json=ranking_body, headers=AUTH
        )
        assert response.status_code == 200, response.text
        assert (
            client.put(
                f"/api/production/reviews/{review_id}/ranking", json=ranking_body, headers=AUTH
            ).status_code
            == 409
        )
        assert get(client, f"/api/production/reviews/{review_id}/reveal")["members"][0]["graph"]
        selected = post(
            client,
            f"/api/production/reviews/{review_id}/select",
            {"candidate_id": candidates[0], "expected_revision": 0},
            status=200,
        )
        assert selected["authority"] == "user"
        assert (
            client.post(
                f"/api/production/reviews/{review_id}/select",
                json={"candidate_id": candidates[1], "expected_revision": 0},
                headers=AUTH,
            ).status_code
            == 409
        )
        selection = get(
            client,
            "/api/production/selections",
            {"movie_id": stored_segment["movie_id"], "segment_id": stored_segment["id"]},
        )
        assert selection["current"]["candidate_id"] == candidates[0]
        assert selection["revision"] == 1
        queued = post(
            client,
            "/api/production/surveys",
            {
                "place_id": place,
                "seed": 4,
                "plan": {
                    "what": "courtyard",
                    "sound": "wind",
                    "first_view": "gate",
                    "first_angle_id": gate,
                    "first_frame_asset": assets[1],
                    "views": [[reverse, "reverse"]],
                    "overhead": "above",
                },
            },
        )
        cancelled = post(client, f"/api/production/operations/{queued['id']}/cancel", status=200)
        assert cancelled["state"] == "cancelled" and cancelled["budget_released_at"]


def test_operation_thread_claims_and_stops(tmp_path):
    app = setup(tmp_path, enable_runner=True)
    app.state.operations.poll_seconds = 0.01
    app.state.operations.steps = {"empty": lambda _context: []}
    with app.state.workspace.db.transaction() as session:
        queued = OperationService().create(
            session,
            kind="test",
            request={},
            jobs=[JobSpec("image", "local", "minimax_h3", step="empty")],
            budget_limit=None,
        )
    with TestClient(app, base_url="http://localhost") as client:
        thread = app.state.operations_thread
        assert thread is not None and thread.is_alive()
        for _ in range(100):
            if get(client, f"/api/production/operations/{queued['id']}")["state"] == "succeeded":
                break
            time.sleep(0.01)
        else:
            raise AssertionError("queued job was not claimed after startup")
    assert not thread.is_alive()


def test_callable_endpoint_follows_settings_for_next_job(tmp_path):
    app = setup(tmp_path)

    class RecordingComfy(FakeComfy):
        def __init__(self):
            super().__init__(
                lambda _graph: (
                    {"save": {"images": [{"filename": "image.png", "type": "output"}]}},
                    {"save:images:0": png((20, 30, 40))},
                ),
                polls_to_complete=1,
            )
            self.submitted_urls = []

        def request(self, url, method, route, **kwargs):
            if method == "POST" and route == "/prompt":
                self.submitted_urls.append(url)
            return super().request(url, method, route, **kwargs)

    fake = RecordingComfy()
    app.state.operations.comfy = fake
    app.state.operations.poll_seconds = 0
    with TestClient(app, base_url="http://localhost"):

        def queue():
            with app.state.workspace.db.transaction() as session:
                return OperationService().create(
                    session,
                    kind="test",
                    request={},
                    jobs=[
                        JobSpec(
                            "image",
                            "comfy",
                            "minimax_h3",
                            graph={"save": {"class_type": "SaveImage", "inputs": {}}},
                            snapshot={"knowledge": {"test": "known"}},
                        )
                    ],
                    budget_limit=None,
                )

        first = queue()
        run_idle(app)
        with app.state.workspace.db.transaction() as session:
            assert OperationService().operation(session, first["id"])["state"] == "succeeded"
        app.state.workspace.save_settings(
            {**app.state.workspace.settings(), "comfy_url": "http://other-host:8188"}
        )
        second = queue()
        run_idle(app)
        with app.state.workspace.db.transaction() as session:
            assert OperationService().operation(session, second["id"])["state"] == "succeeded"
        assert fake.submitted_urls == ["http://127.0.0.1:8188", "http://other-host:8188"]


def test_create_library_starts_operation_thread(tmp_path):
    app = create_app(tmp_path, MIGRATIONS, "test-token", enable_runner=True)
    with TestClient(app, base_url="http://localhost") as client:
        assert app.state.operations_thread is None
        created = post(client, "/api/v1/library", {"name": "New Library"})
        assert created["name"] == "New Library"
        thread = app.state.operations_thread
        assert thread is not None and thread.is_alive()
    assert not thread.is_alive()


def test_survey_request_keys_replay_conflict_and_allow_unkeyed_posts(tmp_path):
    app = setup(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        assets, _ = source_assets(app, tmp_path)
        place = post(client, "/api/direction/places", {"name": "Courtyard"})["id"]
        angles = [
            post(client, f"/api/direction/places/{place}/angles", {"name": name})["id"]
            for name in ("Gate", "Reverse")
        ]
        body = {
            "place_id": place,
            "seed": 4,
            "plan": {
                "what": "courtyard",
                "sound": "wind",
                "first_view": "gate",
                "first_angle_id": angles[0],
                "first_frame_asset": assets[0],
                "views": [[angles[1], "reverse"]],
                "overhead": "above",
            },
        }
        path = "/api/production/surveys"
        keyed = body | {"request_key": "survey-once"}
        first = post(client, path, keyed)
        replay = post(client, path, keyed, status=200)
        assert replay == first
        post(client, path, keyed | {"seed": 5}, status=409)
        assert len(get(client, "/api/production/operations")) == 1
        with app.state.workspace.db.transaction() as session:
            assert session.scalar(text("SELECT count(*) FROM generation_jobs")) == 3
        unkeyed = [post(client, path, body), post(client, path, body)]
        assert len({first["id"], *(operation["id"] for operation in unkeyed)}) == 3
        for invalid in ("", "x" * 81):
            post(client, path, body | {"request_key": invalid}, status=422)


@pytest.mark.parametrize("conflict", [None, "kind", "request"])
def test_concurrent_request_key_insert_is_atomic(tmp_path, conflict):
    app = setup(tmp_path)
    db = app.state.workspace.db
    service = OperationService()
    barrier = threading.Barrier(2)

    def synchronize_inserts(_connection, _cursor, statement, _parameters, _context, _many):
        if statement.startswith("INSERT INTO generation_operations"):
            barrier.wait(timeout=5)

    def create(index):
        with db.transaction() as session:
            try:
                return service.create(
                    session,
                    kind="other" if conflict == "kind" and index else "test",
                    request={"value": index if conflict == "request" else 0},
                    jobs=[JobSpec("image", "local", "minimax_h3", step="empty")],
                    budget_limit=None,
                    request_key="concurrent",
                )
            except ApplicationError as exc:
                assert exc.status == 409
                # The losing insert must leave the transaction usable.
                assert session.scalar(text("SELECT count(*) FROM generation_operations")) == 1
                return exc

    event.listen(db.engine, "before_cursor_execute", synchronize_inserts)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(create, (0, 1)))
        if conflict:
            assert sum(isinstance(result, ApplicationError) for result in results) == 1
        else:
            assert results[0]["id"] == results[1]["id"]
            assert {result["_created"] for result in results} == {True, False}
        with db.transaction() as session:
            assert session.scalar(text("SELECT count(*) FROM generation_operations")) == 1
            assert session.scalar(text("SELECT count(*) FROM generation_jobs")) == 1
    finally:
        event.remove(db.engine, "before_cursor_execute", synchronize_inserts)
        db.close()


def test_keyed_creation_rolls_back_with_caller(tmp_path):
    app = setup(tmp_path)
    db = app.state.workspace.db
    try:
        with pytest.raises(RuntimeError), db.transaction() as session:
            OperationService().create(
                session,
                kind="test",
                request={},
                jobs=[JobSpec("image", "local", "minimax_h3", step="empty")],
                budget_limit=None,
                request_key="rolled-back",
            )
            raise RuntimeError("abort caller transaction")
        with db.transaction() as session:
            assert session.scalar(text("SELECT count(*) FROM generation_operations")) == 0
            assert session.scalar(text("SELECT count(*) FROM generation_jobs")) == 0
    finally:
        db.close()
