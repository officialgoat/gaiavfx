"""A stage built from a chosen panorama, through the durable runner: the fake ComfyUI draws and closes the panorama
and answers MoGe with the synthetic room's exact distance (`panorama_answers.py`), the real worldbuilder worker
judges it and lays out K0, and a fake splat fit stands in for gsplat. No model, no GPU."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest
import synthetic_room as room
from fill_answers import FillAnswers
from test_panoramas import HALL, PanoramaComfy
from worldbuilder_splat import erp
from worldbuilder_splat import geometry as geo

from gaiavfx.application import panoramas, review_approvals, set_fill, set_panorama, set_seed, sets
from gaiavfx.application.engine_jobs import EngineCommand
from gaiavfx.application.operations import OperationRunner
from gaiavfx.infrastructure import engines
from gaiavfx.infrastructure.database import Database
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService, MediaLibrary
from gaiavfx.shared.errors import ApplicationError

OPERATIONS = OperationService()
FAKE = Path(__file__).parent / "fixtures" / "worldbuilder_fake_worker.py"


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIAVFX_GPU_LOCK", str(tmp_path / "gpu.lock"))
    monkeypatch.setattr(engines, "free_vram_gb", lambda: None)
    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Panorama stages")
    db = Database(tmp_path)
    library = MediaLibrary(db, MediaTools())
    comfy = PanoramaComfy()
    worker = EngineCommand((sys.executable, str(FAKE)))
    run = OperationRunner(
        db,
        comfy,
        library,
        "http://fake",
        poll_seconds=0,
        sweep_seconds=0,
        engines={step: worker for step in (set_panorama.ATLAS, set_seed.SPLAT)},
    )
    yield db, library, comfy, run
    db.close()


def transact(db, function, *args, **kwargs):
    with db.transaction() as session:
        return function(session, *args, **kwargs)


def drain(run):
    for _ in range(80):
        if not run.run_once():
            break


def chosen_panorama(db, library, run) -> tuple[dict, dict]:
    started = transact(db, panoramas.start_panorama, library, described_place=HALL)
    drain(run)
    operation = transact(db, OPERATIONS.operation, started["id"])
    first = operation["candidates"][0]
    transact(db, review_approvals.choose_panorama, library, first["id"])
    return operation, first


def test_only_the_chosen_panorama_is_built(work):
    db, library, _, run = work
    started = transact(db, panoramas.start_panorama, library, described_place=HALL)
    drain(run)
    second = transact(db, OPERATIONS.operation, started["id"])["candidates"][1]
    with pytest.raises(ApplicationError, match="Choose this panorama first"):
        transact(db, set_panorama.start_panorama_stage, library, candidate_id=second["id"])


def test_a_chosen_panorama_becomes_a_stage_whose_k0_is_the_panorama(work):
    db, library, comfy, run = work
    attempt, candidate = chosen_panorama(db, library, run)
    preview = transact(db, set_panorama.stage_preview, library, candidate["id"], fill_unseen=False)
    assert preview["name"].startswith("A narrow city-flat hall") and "MoGe-3" in preview["what"]
    build = transact(
        db,
        set_panorama.start_panorama_stage,
        library,
        candidate_id=candidate["id"],
        name="Hall",
        fill_unseen=False,
    )
    assert build["request"]["mode"] == "seed" and build["request"]["source"] == "panorama"
    again = transact(
        db,
        set_panorama.start_panorama_stage,
        library,
        candidate_id=candidate["id"],
        name="Hall",
        fill_unseen=False,
    )
    assert again["id"] == build["id"] and not again["_created"]
    state = transact(db, panoramas.read_panorama, library, attempt["id"])
    assert state["build"]["state"] == "building"
    assert state["build"]["label"] == "Building from the panorama…"
    drain(run)
    done = transact(db, OPERATIONS.operation, build["id"])
    assert [j["state"] for j in done["jobs"]] == ["succeeded"] * 3, [
        j["error"] for j in done["jobs"]
    ]
    geometry = next(g for g in comfy.answers.graphs if "panorama" in g)
    assert geometry["moge_model"]["inputs"]["model_name"] == "moge_3_vitg_fp16.safetensors"
    decision = done["review"][-1]
    assert decision["stage"] == "done"
    with db.transaction() as session:
        row = sets.STORE.get(session, decision["set_id"])
        version = row["versions"][0]
        assert (version["origin"], version["state"]) == ("seed", "proposed")
        assert version["note"] == "Built from a panorama."
        provenance = row["provenance"]
        assert provenance["source"] == "panorama" and provenance["candidate_id"] == candidate["id"]
        assert provenance["layout"] == "panorama" and provenance["gate"]["passed"]
        (keyframe,) = sets.STORE.keyframes(session, version["id"])
        assert (keyframe["kind"], keyframe["role"], keyframe["label"]) == ("erp", "observed", "K0")
        image = library.get(session, keyframe["image_asset_id"])
        assert image["sha256"] == library.get(session, candidate["asset_id"])["sha256"]
    ready = transact(db, panoramas.read_panorama, library, attempt["id"])
    assert ready["build"]["state"] == "done" and ready["build"]["waiting"]
    assert ready["build"]["set_id"] == decision["set_id"]


def test_a_panorama_of_open_sky_is_refused_in_words(work, monkeypatch):
    db, library, comfy, run = work
    attempt, candidate = chosen_panorama(db, library, run)
    answer = comfy.answers.answer

    def open_sky(graph):
        found = answer(graph)
        if found and "MoGePanoramaInference" in {n["class_type"] for n in graph.values()}:
            picture = comfy.answers._input(graph["panorama"]["inputs"]["image"])
            _, distance = room.panorama(picture.shape[1], picture.shape[0])
            sky = np.zeros(distance.shape, bool)
            sky[: int(0.8 * distance.shape[0])] = True
            distance[sky] *= 20  # far, and found by SAM: really sky
            found = (found[0], {"save:geometry:0": room.panorama_npz(distance, sky=sky)})
        return found

    monkeypatch.setattr(comfy.answers, "answer", open_sky)
    build = transact(
        db,
        set_panorama.start_panorama_stage,
        library,
        candidate_id=candidate["id"],
        fill_unseen=False,
    )
    drain(run)
    done = transact(db, OPERATIONS.operation, build["id"])
    decision = done["review"][-1]
    assert decision["stage"] == "refused" and decision["label"] == set_seed.PANORAMA_SHORT_REFUSAL
    assert "open sky or water" in decision["message"]
    state = transact(db, panoramas.read_panorama, library, attempt["id"])
    assert state["build"]["state"] == "refused"


def test_a_stopped_build_is_retried_from_its_panorama(work):
    db, library, comfy, run = work
    attempt, candidate = chosen_panorama(db, library, run)
    comfy.fail_graph = lambda graph: (
        "MoGePanoramaInference" in {n["class_type"] for n in graph.values()}
    )
    build = transact(
        db,
        set_panorama.start_panorama_stage,
        library,
        candidate_id=candidate["id"],
        fill_unseen=False,
    )
    drain(run)
    stopped = transact(db, OPERATIONS.operation, build["id"])
    state = transact(db, panoramas.read_panorama, library, attempt["id"])
    assert state["build"]["state"] == "failed" and state["build"]["can_retry"]
    comfy.fail_graph = None
    retried = transact(db, set_seed.retry_seed, library, stopped["id"])
    assert retried["id"] != stopped["id"]
    assert retried["request"]["attempt"] == stopped["request"]["attempt"] + 1 == 2
    drain(run)
    assert transact(db, OPERATIONS.operation, retried["id"])["review"][-1]["stage"] == "done"


class FillingComfy(PanoramaComfy):
    """Panoramas' answers, then a fill round's (`fill_answers.py`): H3's probe renders its memory, MoGe measures each
    still as the room's true depth. The panorama's distance hides the room behind a pillar, so a round has somewhere
    to look."""

    def __init__(self, root):
        super().__init__()
        self.fill = FillAnswers(root)

    def upload(self, url, source, filename, directory_type="input"):
        super().upload(url, source, filename, directory_type)
        self.fill.keep(filename, source)

    def answer(self, graph):
        kinds = {n["class_type"] for n in graph.values()}
        if "MoGePanoramaInference" in kinds:
            picture = self.answers._input(graph["panorama"]["inputs"]["image"])
            height, width = picture.shape[:2]
            _, distance = room.panorama(width, height)
            lon = (np.arange(width) + 0.5) / width * 360 - 180
            lat = 90 - (np.arange(height) + 0.5) / height * 180
            pillar = (np.abs(np.abs(lon) - 180) < 30)[None, :] & (lat < 40)[:, None]
            rays = erp.directions(width, height) @ geo.F
            near = 1.0 / np.maximum(-rays[..., 2], 1e-6)
            distance = np.where(pillar, np.minimum(distance, near), distance).astype(np.float32)
            return {
                "save": {
                    "geometry": [{"filename": "geometry.npz", "subfolder": "x", "type": "output"}]
                }
            }, {"save:geometry:0": room.panorama_npz(distance)}
        return self.fill.answer(graph) or super().answer(graph)


@pytest.mark.eval
def test_a_panorama_stage_fills_from_its_crops(work, tmp_path):
    """R5's expandable splat: the round plans from the panorama's crops, H3 renders, the stills consolidate and the
    splat extends; v1 is written from the round's checkpoint."""
    db, library, _, run = work
    comfy = FillingComfy(tmp_path)
    run.comfy = comfy
    run.engines.update(
        {
            step: run.engines[set_seed.SPLAT]
            for step in (
                set_fill.EXPLORE,
                set_fill.PATHS,
                set_fill.STILLS,
                set_fill.CONSOLIDATE,
                set_fill.EXTEND,
            )
        }
    )
    attempt, candidate = chosen_panorama(db, library, run)
    build = transact(
        db, set_panorama.start_panorama_stage, library, candidate_id=candidate["id"], name="Hall"
    )
    for _ in range(8):
        drain(run)
    done = transact(db, OPERATIONS.operation, build["id"])
    decision = done["review"][-1]
    assert decision["stage"] == "done", done["review"]
    explore = [j for j in done["jobs"] if j["step"] == set_fill.EXPLORE]
    assert explore, "the build filled"
    with db.transaction() as session:
        plan = json.loads(
            library.read_blob(session, set_fill.output(explore[0], "summary")["artifact_id"])
        )
    assert plan["probes"] and all("@" in probe["start"] for probe in plan["probes"])
    state = transact(db, panoramas.read_panorama, library, attempt["id"])
    assert state["build"]["state"] == "done"
