"""Removal preparation, reviewed plates and strict authored request validation."""

import json
import subprocess
from copy import deepcopy
from pathlib import Path

import pytest
from fake_comfy import FakeComfy
from fastapi.testclient import TestClient
from test_production_api import AUTH, png, run_idle, setup, source_assets

from gaiavfx.application.authored import (
    load_insertion,
    load_segment,
    load_survey,
    segment_records,
)
from gaiavfx.application.operations import PRODUCERS, create_operation
from gaiavfx.application.prepare_references import (
    approve_plate,
    open_plate_review,
    start_removal,
)
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.integrations.minimax_h3.graph import render_presets
from gaiavfx.integrations.qwen_image_edit.prompt import compile_removal
from gaiavfx.modules.direction.insertion import Removal
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

WORDING = {
    "whom": "the man in the plaid shirt",
    "object_pronoun": "him",
    "behind": "the railing and the arcade",
}


def test_direct_empty_plate_api_fits_then_waits_for_human_review(removal_setup):
    app, base, sources = removal_setup
    body = {
        "place_id": base["place_id"],
        "angle_id": base["angle_id"],
        "source_asset": sources["image"],
        "empty": True,
        "request_key": "empty-plate",
    }
    client = TestClient(app, base_url="http://localhost", headers=AUTH)
    for invalid in (
        body | {"empty": False},
        body | {"whom": "Arin"},
        body | {"source_asset": sources["video"]},
    ):
        assert client.post("/api/production/plates", json=invalid).status_code == 422
    response = client.post("/api/production/plates", json=body)
    assert response.status_code == 201, response.text
    operation = response.json()
    assert operation["request"]["empty"] is True and operation["budget_reserved"] == 0
    assert [job["step"] for job in operation["jobs"]] == ["fit_frame"]
    retry = client.post("/api/production/plates", json=body)
    assert retry.status_code == 200 and retry.json()["id"] == operation["id"]
    run_idle(app)
    assert not app.state.operations.comfy.posts
    review = client.post(f"/api/production/removals/{operation['id']}/review")
    assert review.status_code == 200 and review.json()["purpose"] == "plate"
    member = review.json()["members"][0]
    with app.state.workspace.db.transaction() as session:
        assert DirectionService().read_place(session, base["place_id"])["plate_asset_id"] is None
        info = MediaLibrary.asset_info(session, member["asset_id"])["info"]
        assert (info["width"], info["height"]) == (768, 448)
    blind = client.get(f"/api/production/reviews/{review.json()['id']}").json()
    assert not {"request", "seed", "operation_id"} & blind["members"][0].keys()
    approved = client.post(
        "/api/production/plates/approve", json={"candidate_id": member["candidate_id"]}
    )
    assert approved.status_code == 200
    assert approved.json()["plate_asset_id"] == member["asset_id"]


@pytest.fixture(scope="module")
def take_file(tmp_path_factory):
    path = tmp_path_factory.mktemp("removal-source") / "take.mp4"
    subprocess.run(
        [
            str(MediaTools().ffmpeg),
            "-loglevel",
            "error",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=32x32:rate=24",
            "-frames:v",
            "124",
            "-c:v",
            "libx264",
            str(path),
        ],
        check=True,
    )
    return path


@pytest.fixture
def removal_setup(tmp_path, take_file):
    app = setup(tmp_path)

    def outputs(graph):
        return {"save": {"images": [{"filename": "plate.png", "type": "output"}]}}, {
            "save:images:0": png((graph["sample"]["inputs"]["seed"], 60, 80))
        }

    app.state.operations.comfy = FakeComfy(outputs, polls_to_complete=1)
    with TestClient(app, base_url="http://localhost"):
        assets, _ = source_assets(app, tmp_path)
        library = app.state.workspace.library
        prepared = library.prepare(take_file, "take.mp4")
        with app.state.workspace.db.transaction() as session:
            video = library.register(session, prepared, {"type": "test"})["id"]
            service = DirectionService()
            place = service.create_place(session, "Courtyard")
            angle = service.add_angle(session, place["id"], "Gate")
        yield (
            app,
            {
                "place_id": place["id"],
                "angle_id": angle["id"],
                "wording": WORDING,
                "seeds": [11, 12],
            },
            {"image": assets[0], "video": video},
        )


@pytest.mark.parametrize("kind", ["video", "image"])
def test_removal_flow_review_approval_and_retry(removal_setup, kind):
    app, base, sources = removal_setup
    db = app.state.workspace.db
    args = base | {
        "source_asset": sources[kind],
        "frame": 100 if kind == "video" else None,
        "request_key": "plate-request",
        "budget_limit": 2,
    }
    with db.transaction() as session:
        operation = start_removal(session, **args)
        assert operation.pop("_created")
        retry = start_removal(session, **args)
        assert not retry.pop("_created")
        assert retry == operation
        jobs = operation["jobs"]
        steps = [None, "fit_frame", None, "fit_frame"]
        dependencies = [None, 0, None, 2]
        if kind == "video":
            steps = ["extract_frames", *steps]
            dependencies = [None, 0, 1, 0, 3]
            assert jobs[0]["snapshot"]["step_spec"] == {
                "source": sources[kind],
                "frames": [["plate_source", 100]],
            }
            assert MediaLibrary.asset_info(session, sources[kind])["info"]["video_frames"] == 124
        assert [job["step"] for job in jobs] == steps
        assert [job["depends_on"] for job in jobs] == dependencies
        assert operation["budget_reserved"] == 2
        edits = [job for job in jobs if job["executor"] == "comfy"]
        for job in edits:
            graph = job["graph"]
            loads = [
                node["inputs"]["image"]
                for node in graph.values()
                if node["class_type"] == "LoadImage"
            ]
            assert (
                len(loads) == 1
                and loads[0].startswith("gaiavfx/")
                and loads[0].endswith("/view.png")
            )
            assert next(iter(job["inputs"].values()))["remote_name"] == loads[0]
            assert graph["positive"]["inputs"]["prompt"] == compile_removal(
                Removal(sources[kind], **WORDING)
            )
            assert job["snapshot"]["request"] == {"view_asset": sources[kind], **WORDING}
            assert job["snapshot"]["knowledge"]
        with pytest.raises(ApplicationError) as error:
            open_plate_review(session, operation["id"])
        assert error.value.status == 409
        with pytest.raises(ApplicationError) as error:
            start_removal(session, **(args | {"seeds": [13, 14]}))
        assert error.value.status == 409
    run_idle(app)
    with db.transaction() as session:
        finished = OperationService().operation(session, operation["id"])
        assert finished["state"] == "succeeded", finished
        assert DirectionService().read_place(session, base["place_id"])["plate_asset_id"] is None
        fitted = [c for c in finished["candidates"] if c["output_key"] == "fitted"]
        assert len(fitted) == 2
        for candidate in fitted:
            assert candidate["kind"] == "image"
            info = MediaLibrary.asset_info(session, candidate["asset_id"])["info"]
            assert (info["width"], info["height"]) == tuple(
                render_presets().delivery[k] for k in ("width", "height")
            )
        review = open_plate_review(session, operation["id"])
        assert review["purpose"] == "plate"
        assert review["context"] == {
            "place_id": base["place_id"],
            "angle_id": base["angle_id"],
            "operation_id": operation["id"],
        }
        assert {m["candidate_id"] for m in review["members"]} == {c["id"] for c in fitted}
        assert open_plate_review(session, operation["id"]) == review
        raw = next(c for c in finished["candidates"] if c["job_id"] == edits[0]["id"])
        with pytest.raises(ApplicationError) as error:
            approve_plate(session, candidate_id=raw["id"])
        assert error.value.status == 422
        approved = approve_plate(session, candidate_id=fitted[0]["id"])
        assert approved["plate_asset_id"] == fitted[0]["asset_id"]
        assert approved["plate_angle_id"] == base["angle_id"]
        assert approve_plate(session, candidate_id=fitted[0]["id"]) == approved
        with pytest.raises(ApplicationError, match="movie cut for its undo history") as error:
            approve_plate(session, candidate_id=fitted[1]["id"])
        assert error.value.status == 409
        replay = start_removal(session, **args)
        assert not replay["_created"] and replay["id"] == finished["id"]
        assert len(OperationService().recent(session, kind="removal")) == 1
    uploads = app.state.operations.comfy.uploads
    assert len(uploads) == 2 and all(name.endswith("/view.png") for _, name in uploads)


@pytest.mark.parametrize(
    ("kind", "changes"),
    [
        ("video", {"frame": None}),
        ("video", {"frame": -1}),
        ("video", {"frame": True}),
        ("video", {"frame": 1.5}),
        ("video", {"frame": 124}),
        ("image", {"frame": 0}),
        ("image", {"wording": WORDING | {"extra": "no"}}),
        ("image", {"wording": {"whom": "walker", "behind": "wall"}}),
        ("image", {"wording": WORDING | {"behind": ""}}),
        ("image", {"seeds": [1, 1]}),
        ("image", {"seeds": []}),
    ],
)
def test_removal_refusals_create_no_operation(removal_setup, kind, changes):
    app, base, sources = removal_setup
    with app.state.workspace.db.transaction() as session:
        with pytest.raises(ApplicationError) as error:
            start_removal(session, **(base | {"source_asset": sources[kind]} | changes))
        assert error.value.status == 422
        assert OperationService().recent(session, kind="removal") == []


def test_removal_foreign_angle_and_budget_refused(removal_setup):
    app, base, sources = removal_setup
    with app.state.workspace.db.transaction() as session:
        service = DirectionService()
        other = service.create_place(session, "Other")
        angle = service.add_angle(session, other["id"], "Foreign")
        with pytest.raises(ApplicationError) as error:
            start_removal(
                session, **(base | {"source_asset": sources["image"], "angle_id": angle["id"]})
            )
        assert error.value.status == 422
        with pytest.raises(ApplicationError) as error:
            start_removal(session, **base, source_asset=sources["image"], budget_limit=1)
        assert error.value.status == 409
        assert OperationService().recent(session, kind="removal") == []


def test_removal_without_probed_count_defers_bound_to_extraction(removal_setup, monkeypatch):
    app, base, sources = removal_setup
    original = MediaLibrary.asset_info

    def without_count(session, asset_id):
        info = original(session, asset_id)
        info["info"].pop("video_frames", None)
        return info

    monkeypatch.setattr(MediaLibrary, "asset_info", staticmethod(without_count))
    with app.state.workspace.db.transaction() as session:
        operation = start_removal(session, **base, source_asset=sources["video"], frame=124)
    run_idle(app)
    with app.state.workspace.db.transaction() as session:
        result = OperationService().operation(session, operation["id"])
        assert result["state"] == "failed"
        assert result["jobs"][0]["error"]
    assert not app.state.operations.comfy.posts


def test_partial_removal_reviews_surviving_fit_and_rejects_other_producers(removal_setup):
    app, base, sources = removal_setup
    db = app.state.workspace.db
    operations = OperationService()
    with db.transaction() as session:
        removal = start_removal(session, **base, source_asset=sources["image"])
        operations.transition(session, removal["jobs"][2]["id"], "failed", error="Test failure")
        source = MediaLibrary.asset_info(session, sources["image"])
        other = create_operation(
            session,
            operations,
            PRODUCERS,
            kind="survey",
            request={},
            budget_limit=None,
            jobs=[
                JobSpec(
                    "image",
                    "local",
                    "minimax_h3",
                    step="fit_frame",
                    inputs={source["id"]: {"sha256": source["sha256"]}},
                    snapshot={"step_spec": {"source": source["id"], **render_presets().delivery}},
                )
            ],
        )
    run_idle(app)
    with db.transaction() as session:
        assert operations.operation(session, removal["id"])["state"] == "partial"
        review = open_plate_review(session, removal["id"])
        assert len(review["members"]) == 1
        candidate = operations.operation(session, other["id"])["candidates"][0]
        assert candidate["output_key"] == "fitted"
        with pytest.raises(ApplicationError) as error:
            approve_plate(session, candidate_id=candidate["id"])
        assert error.value.status == 422


@pytest.mark.parametrize(
    ("reader", "document"),
    [
        (load_segment, {"subjects": [], "unexpected": True}),
        (load_survey, {"what": "place", "views": [], "unexpected": True}),
        (load_insertion, {"who": "walker", "unexpected": True}),
    ],
)
def test_authored_readers_reject_missing_and_extra_fields(tmp_path, reader, document):
    path = tmp_path / "request.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    with pytest.raises(ApplicationError) as error:
        reader(path)
    assert error.value.status == 422


def test_insertion_reader_checks_facing_specific_requirements(tmp_path):
    path = tmp_path / "request.json"
    path.write_text(
        json.dumps(
            {
                "who": "walker",
                "looks": "coat",
                "pronouns": "he",
                "facing": "toward_camera",
                "plate": "wall",
                "keep": "wall",
                "where": "near gate",
                "size": "half",
            }
        ),
        encoding="utf-8",
    )
    with pytest.raises(ApplicationError, match="behind") as error:
        load_insertion(path)
    assert error.value.status == 422


def segment_document():
    return json.loads((Path(__file__).parent / "fixtures/parity/authored/DZ.json").read_text())[
        "segment"
    ]


def test_segment_records_preserve_content_and_order(tmp_path):
    data = segment_document()
    data["subjects"][0]["identity_asset"] = None
    path = tmp_path / "segment.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    records = load_segment(path)
    assert records == segment_records(data)
    assert records["cast"][0] == {
        **{key: value for key, value in data["subjects"][0].items() if key != "identity_asset"},
        "identity_asset_id": None,
    }
    assert records["content"] == {"subjects": ["s1"], "place": data["place"]}
    assert records["phases"] == [(0, data["shots"][0]), (36, data["shots"][1])]


@pytest.mark.parametrize(
    ("part", "key", "value"),
    [
        (None, "extra", 1),
        ("place", "extra", 1),
        ("subject", "name", ""),
        ("shot", "description", 3),
        (None, "subjects", {}),
        (None, "shots", {}),
        (None, "cut_frames", {}),
        (None, "cut_frames", [True]),
        (None, "cut_frames", [0]),
        (None, "delivered_frames", True),
        (None, "continues", 1),
    ],
)
def test_segment_reader_delegates_strict_validation(tmp_path, part, key, value):
    data = deepcopy(segment_document())
    target = (
        data["subjects"][0]
        if part == "subject"
        else data["shots"][0]
        if part == "shot"
        else data[part]
        if part
        else data
    )
    target[key] = value
    path = tmp_path / "invalid.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(ApplicationError) as error:
        load_segment(path)
    assert error.value.status == 422
