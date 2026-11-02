"""Panoramas through the durable runner: two candidates drawn from words or grown from a picture, their seams closed
and checked, and a person's choice. A fake ComfyUI answers Krea, Fibo and MoGe on the synthetic room
(`panorama_answers.py`); the local steps are the real ones. No model, no GPU."""

from pathlib import Path

import numpy as np
import pytest
from fake_comfy import FakeComfy
from panorama_answers import HEIGHT, WIDTH, PanoramaAnswers
from PIL import Image

from gaiavfx.application import panoramas, review_approvals
from gaiavfx.application.operation_kinds import review_pending
from gaiavfx.application.operations import OperationRunner
from gaiavfx.infrastructure.database import Database
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService, MediaLibrary
from gaiavfx.modules.panoramas import erp
from gaiavfx.modules.panoramas.ask import DescribedPlace, Landmark, PicturedPlace
from gaiavfx.shared.errors import ApplicationError

OPERATIONS = OperationService()
HALL = DescribedPlace(
    "A narrow city-flat hall with worn parquet", (Landmark("Living-room doorway", 0),), "Overcast"
)


class PanoramaComfy(FakeComfy):
    def __init__(self):
        self.answers = PanoramaAnswers()
        super().__init__(self.answer, polls_to_complete=1)
        self.broken_seeds = set()

    def upload(self, url, source, filename, directory_type="input"):
        super().upload(url, source, filename, directory_type)
        self.answers.keep(filename, source)

    def answer(self, graph):
        sample = graph.get("sample", {}).get("inputs", {})
        if sample.get("seed") in self.broken_seeds and "ConditioningZeroOut" in {
            n["class_type"] for n in graph.values()
        }:
            return {}, {}  # Krea returned nothing for this seed
        found = self.answers.answer(graph)
        assert found is not None, sorted(n["class_type"] for n in graph.values())
        return found


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIAVFX_GPU_LOCK", str(tmp_path / "gpu.lock"))
    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Panoramas")
    db = Database(tmp_path)
    library = MediaLibrary(db, MediaTools())
    comfy = PanoramaComfy()
    run = OperationRunner(db, comfy, library, "http://fake", poll_seconds=0, sweep_seconds=0)
    yield db, library, comfy, run
    db.close()


def transact(db, function, *args, **kwargs):
    with db.transaction() as session:
        return function(session, *args, **kwargs)


def drain(run):
    for _ in range(80):
        if not run.run_once():
            break


def picture(db, library, tmp_path) -> str:
    """A letterboxed 16:9 still, as a film frame would be."""
    image = np.zeros((200, 320, 3), np.uint8)
    image[20:180] = 120  # grey, between black letterbox bars
    path = tmp_path / "frame.png"
    Image.fromarray(image).save(path)
    prepared = library.prepare(path, "frame.png")
    with db.transaction() as session:
        return library.register(session, prepared, {"type": "test"})["id"]


def test_words_become_two_panoramas_with_closed_seams(work):
    db, library, comfy, run = work
    started = transact(db, panoramas.start_panorama, library, described_place=HALL)
    assert started["_created"] and started["budget_reserved"] == 4
    again = transact(db, panoramas.start_panorama, library, described_place=HALL)
    assert again["id"] == started["id"] and not again["_created"]
    drain(run)
    operation = transact(db, OPERATIONS.operation, started["id"])
    assert operation["state"] == "succeeded", operation["review"]
    state = transact(db, panoramas.panorama_state, library, operation)
    assert state["state"] == "ready" and [m["letter"] for m in state["candidates"]] == ["A", "B"]
    for member in state["candidates"]:
        assert member["seam"]["outside_identical"] and member["seam"]["clean"]
        assert member["seam"]["edge_step_after"] < member["seam"]["edge_step_before"]
        assert member["plate"] is None
        path = transact(db, library.path, member["asset_id"])
        assert Image.open(path).size == (WIDTH, HEIGHT)
    prompts = [g["positive"]["inputs"]["text"] for g in comfy.answers.graphs if "positive" in g]
    assert len(prompts) == 2 and all(p.startswith("img-txt-2-360, ") for p in prompts)
    assert {g["sample"]["inputs"]["seed"] for g in comfy.answers.graphs if "positive" in g} == {
        7101,
        7102,
    }
    # Only the two closed panoramas are candidates; drawings and bands stay hidden intermediates.
    assert len(operation["candidates"]) == 2
    assert transact(db, review_pending, operation)


def test_a_seed_that_draws_nothing_leaves_one_panorama(work):
    db, library, comfy, run = work
    comfy.broken_seeds = {7102}
    started = transact(db, panoramas.start_panorama, library, described_place=HALL)
    drain(run)
    operation = transact(db, OPERATIONS.operation, started["id"])
    assert operation["state"] == "partial"
    state = transact(db, panoramas.panorama_state, library, operation)
    assert [m["letter"] for m in state["candidates"]] == ["A"]
    assert state["message"].startswith("One panorama stopped")


def test_a_picture_grows_into_panoramas_that_keep_it(work, tmp_path):
    db, library, comfy, run = work
    asset_id = picture(db, library, tmp_path)
    started = transact(
        db, panoramas.start_panorama, library, pictured=PicturedPlace(asset_id, place="A hall")
    )
    drain(run)
    operation = transact(db, OPERATIONS.operation, started["id"])
    assert operation["state"] == "succeeded", operation["review"]
    state = transact(db, panoramas.panorama_state, library, operation)
    assert len(state["candidates"]) == 2
    measured = [g for g in comfy.answers.graphs if "picture" in g]
    assert (
        measured
        and measured[0]["moge_model"]["inputs"]["model_name"] == "moge_3_vitg_fp16.safetensors"
    )
    for member in state["candidates"]:
        check = member["plate"]
        assert check["hfov"] == 70.0 and check["heading"] == 0.0 and check["green"] == 0.0
        image = np.asarray(
            Image.open(transact(db, library.path, member["asset_id"])).convert("RGB")
        )
        # The picture's own pixels are back inside its footprint, at the panorama's centre.
        assert erp.green_fraction(image) == 0.0
        assert image.shape == (1024, 2048, 3)  # a grown panorama has Krea's size
        assert np.abs(image[507:517, 1019:1029].astype(int) - 120).max() <= 2


def test_only_a_person_chooses_and_a_choice_ends_the_review(work):
    db, library, _, run = work
    started = transact(db, panoramas.start_panorama, library, described_place=HALL)
    drain(run)
    operation = transact(db, OPERATIONS.operation, started["id"])
    first, second = operation["candidates"]
    chosen = transact(db, review_approvals.choose_panorama, library, second["id"])
    assert chosen["state"] == "chosen" and chosen["chosen"] == second["id"]
    assert not transact(db, review_pending, transact(db, OPERATIONS.operation, started["id"]))
    again = transact(db, review_approvals.choose_panorama, library, first["id"])
    assert again["chosen"] == first["id"]
    with pytest.raises(ApplicationError, match="Only the user"):
        transact(
            db,
            panoramas.reviews.choose,
            started["id"],
            first["id"],
            chosen_by="agent",
        )


def test_two_more_draw_the_next_seeds(work):
    db, library, _, run = work
    first = transact(db, panoramas.start_panorama, library, described_place=HALL)
    drain(run)
    more = transact(db, panoramas.make_more, library, first["id"])
    assert more["_created"] and more["request"]["seeds"] == [7103, 7104]
    drain(run)
    older = transact(db, OPERATIONS.operation, first["id"])
    assert not transact(db, review_pending, older)  # a newer attempt supersedes its review
    listed = transact(db, panoramas.panoramas_for, library)
    assert [p["attempt"] for p in listed] == [1, 0]


def test_a_request_needs_words_or_a_picture(work):
    db, library, _, _ = work
    with pytest.raises(ApplicationError, match="Describe the place"):
        transact(db, panoramas.start_panorama, library)
    preview = transact(db, panoramas.preview, library, described_place=HALL)
    assert (
        preview["prompt"].startswith("img-txt-2-360, ") and "non-commercial" in preview["licence"]
    )
