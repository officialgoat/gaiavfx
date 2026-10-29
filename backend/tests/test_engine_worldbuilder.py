"""The engine registry as one (ADR 0015 §2 amended by ADR 0018 §4) and the worldbuilder worker's file protocol, with a
fake splat fit so the protocol runs in the app's environment."""

import hashlib
import importlib.util
import json
import sys
import types
from pathlib import Path

import pytest

from gaiavfx.application import engine_jobs, engine_registry
from gaiavfx.integrations.voice import engines as voice_engines
from gaiavfx.integrations.worldbuilder import engines as worldbuilder_engines
from gaiavfx.shared.errors import ApplicationError

WORKERS = Path(__file__).parents[2] / "engine_workers"


def test_the_registry_is_the_union_of_every_integrations_file():
    engines = engine_registry.load()
    assert set(voice_engines.load()) <= set(engines)
    assert {"worldbuilder"} <= set(engines)
    assert engine_registry.origin("worldbuilder") == "worldbuilder"
    assert engine_registry.origin("analysis") == "voice"
    expected = hashlib.sha256(worldbuilder_engines.registry_text().encode()).hexdigest()
    assert engine_registry.fingerprint("worldbuilder") == expected
    # A voice job keeps its own file's fingerprint: adding engines elsewhere changes no voice identity.
    assert engine_registry.fingerprint("analysis") == voice_engines.fingerprint()


def test_two_integrations_may_not_register_one_engine(monkeypatch):
    monkeypatch.setitem(engine_registry.SOURCES, "twin", voice_engines.registry_text)
    with pytest.raises(ValueError, match="registered by both"):
        engine_registry.load()


def test_a_worldbuilder_job_names_its_integration_and_registry():
    job = engine_jobs.engine_job(
        "worldbuilder.splat_init_atlas", params={"name": "v1", "steps": 3000}, kind="analysis"
    )
    engine = job.snapshot["engine"]
    assert job.integration == "worldbuilder" and engine["gpu"] and engine["vram_gb"] == 8
    assert engine["registry"] == engine_registry.fingerprint("worldbuilder")
    assert engine["spec"] is None
    with pytest.raises(ApplicationError, match="No registered engine task"):
        engine_jobs.engine_job("worldbuilder.splat_dream", params={}, kind="analysis")


def test_readiness_lists_the_worldbuilder_engine(tmp_path):
    rows = {row["name"]: row for row in engine_jobs.readiness(str(tmp_path))["engines"]}
    assert rows["worldbuilder"]["worker"] is True
    assert rows["worldbuilder"]["gpu_tasks"] == [
        "fill_paths",
        "render_views",
        "splat_atlas_metrics",
        "splat_extend",
        "splat_init_atlas",
    ]
    assert rows["worldbuilder"]["installed"] is False


@pytest.fixture
def worker(monkeypatch):
    calls = []

    def fake_run(args):
        calls.append(args)
        args.out.mkdir(parents=True, exist_ok=True)
        if args.command != "atlas-metrics":
            (args.out / "splat.ply").write_bytes(b"ply\n" + args.command.encode())
        return {"command": args.command, "gaussians": 5, "heldout_psnr_refined": 21.8}

    def fake_fit_frames(out, atlas, steps):
        return fake_run(
            types.SimpleNamespace(
                command="init-frames", out=Path(out), atlas=Path(atlas), steps=str(steps)
            )
        )

    def fake_extend_frames(out, source, atlas, steps):
        return fake_run(
            types.SimpleNamespace(
                command="extend-frames",
                out=Path(out),
                state=Path(source),
                atlas=Path(atlas),
                steps=str(steps),
            )
        )

    parser = types.SimpleNamespace(parse_args=lambda argv: _namespace(argv))
    splat2 = types.SimpleNamespace(run=fake_run, parser=lambda: parser)
    # The research package's own modules, with only its GPU fits faked: splat2, and the frames path's fit.
    package = types.ModuleType("worldbuilder_splat")
    package.__path__ = [str(WORKERS / "worldbuilder_splat")]
    package.splat2 = splat2
    monkeypatch.setitem(sys.modules, "worldbuilder_splat", package)
    monkeypatch.setitem(sys.modules, "worldbuilder_splat.splat2", splat2)
    monkeypatch.syspath_prepend(str(WORKERS))
    frames = importlib.import_module("worldbuilder_splat.frames")
    monkeypatch.setattr(frames, "fit_frames", fake_fit_frames)
    monkeypatch.setattr(frames, "extend_frames", fake_extend_frames)
    spec = importlib.util.spec_from_file_location(
        "worldbuilder_worker", WORKERS / "worldbuilder.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return types.SimpleNamespace(module=module, calls=calls)


def _namespace(argv):
    values = {"command": argv[0]}
    for key, value in zip(argv[1::2], argv[2::2], strict=True):
        values[key.lstrip("-").replace("-", "_")] = (
            Path(value) if key in ("--out", "--state", "--atlas") else value
        )
    return types.SimpleNamespace(**values)


PANORAMA_ATLAS = json.dumps({"keyframes": [{"id": "pano", "kind": "erp", "role": "observed"}]})
FRAMES_ATLAS = json.dumps({"keyframes": [{"id": "k01", "kind": "pinhole", "role": "generated"}]})


def job(tmp_path, task, params, atlas_text=PANORAMA_ATLAS):
    work = tmp_path / "work" / task
    work.mkdir(parents=True, exist_ok=True)
    atlas = tmp_path / "atlas.json"
    atlas.write_text(atlas_text, encoding="utf-8")
    (work / "job.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "job_id": "j",
                "step": f"worldbuilder.{task}",
                "task": task,
                "params": params,
                "inputs": {"atlas": str(atlas)},
                "scratch": str(tmp_path / "scratch"),
            }
        ),
        encoding="utf-8",
    )
    return work


def run_job(worker, work):
    return worker.module.run(worker.module.TASKS, argv=["--job", str(work / "job.json")])


def test_the_worker_fits_into_scratch_publishes_and_skips_a_finished_fit(worker, tmp_path):
    work = job(tmp_path, "splat_init_atlas", {"name": "v1", "steps": 6000})
    assert run_job(worker, work) == 0
    result = json.loads((work / "result.json").read_text(encoding="utf-8"))
    assert [(o["key"], o["kind"]) for o in result["outputs"]] == [
        ("summary", "analysis_report"),
        ("splat", "set_splat"),
    ]
    assert (tmp_path / "scratch" / "splat" / "v1" / "splat.ply").is_file()
    assert worker.calls[0].steps == "6000"
    # A relaunch with the same atlas and settings reuses the fit.
    assert run_job(worker, work) == 0 and len(worker.calls) == 1
    # Other settings fit again.
    again = job(tmp_path, "splat_init_atlas", {"name": "v1", "steps": 3000})
    assert run_job(worker, again) == 0 and len(worker.calls) == 2


def test_extend_needs_its_source_state_and_names_are_plain(worker, tmp_path):
    missing = job(tmp_path, "splat_extend", {"from": "v1", "name": "v2"})
    assert run_job(worker, missing) == 1
    assert run_job(worker, job(tmp_path, "splat_init_atlas", {"name": "v1"})) == 0
    assert run_job(worker, job(tmp_path, "splat_extend", {"from": "v1", "name": "v2"})) == 0
    assert worker.calls[-1].state == tmp_path / "scratch" / "splat" / "v1"
    escape = job(tmp_path, "splat_init_atlas", {"name": "../outside"})
    assert run_job(worker, escape) == 1


def test_an_atlas_without_a_panorama_is_fitted_by_the_frames_path(worker, tmp_path):
    # A set seeded from a take (ADR 0019 §4): splat2's panorama path stays the research's, untouched.
    work = job(tmp_path, "splat_init_atlas", {"name": "seed"}, atlas_text=FRAMES_ATLAS)
    assert run_job(worker, work) == 0
    assert [c.command for c in worker.calls] == ["init-frames"]
    assert (tmp_path / "scratch" / "splat" / "seed" / "splat.ply").is_file()
    assert run_job(worker, job(tmp_path, "splat_init_atlas", {"name": "pano"})) == 0
    assert worker.calls[-1].command == "init-atlas"


def test_a_takes_state_is_extended_by_the_frames_path(worker, tmp_path):
    # A fill round's extension (ADR 0020 §4) of a take's fit: splat2's extend stays the panorama's.
    seed = job(tmp_path, "splat_init_atlas", {"name": "seed"}, atlas_text=FRAMES_ATLAS)
    assert run_job(worker, seed) == 0
    work = job(tmp_path, "splat_extend", {"from": "seed", "name": "r1"}, atlas_text=FRAMES_ATLAS)
    assert run_job(worker, work) == 0
    call = worker.calls[-1]
    assert (call.command, call.state, call.steps) == (
        "extend-frames",
        tmp_path / "scratch" / "splat" / "seed",
        "1500",
    )
    assert (tmp_path / "scratch" / "splat" / "r1" / "splat.ply").is_file()
