"""ADR 0015: engine jobs through the durable runner (fake worker, fake ComfyUI, real Library, no GPU)."""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from contextlib import contextmanager
from pathlib import Path

import httpx
import pytest
from fake_comfy import FakeComfy
from sqlalchemy import func, select

from gaiavfx.application import engine_jobs
from gaiavfx.application.engine_jobs import EngineCommand, engine_job
from gaiavfx.application.operations import OperationRunner
from gaiavfx.infrastructure import engines
from gaiavfx.infrastructure.database import Database
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService, MediaLibrary
from gaiavfx.modules.library.assets import AssetRecord
from gaiavfx.shared.errors import ApplicationError

WORKER = Path(__file__).parent / "fixtures" / "engine_fake_worker.py"
REGISTRY = """
schema = 2
[engines.fake]
home = "fake"
environment = "env/python.exe"
worker = "fake_worker.py"
programs = { cli = "bin/cli.exe" }
[engines.fake.tasks.echo]
gpu = false
[engines.fake.tasks.gpu_echo]
gpu = true
vram_gb = 4
[engines.fake.tasks.report]
gpu = false
[engines.fake.tasks.slow]
gpu = false
[engines.fake.tasks.crash]
gpu = false
[engines.fake.tasks.chunks]
gpu = false
[engines.fake.tasks.engine_info]
gpu = false
[engines.fake.tasks.scratch_note]
gpu = false
[engines.fake.tasks.env_check]
gpu = false
"""
COMMANDS = {
    f"fake.{task}": EngineCommand((sys.executable, str(WORKER)))
    for task in ("echo", "gpu_echo", "report", "slow", "crash", "chunks", "scratch_note")
}
COMMANDS["fake.engine_info"] = EngineCommand(
    (sys.executable, str(WORKER)),
    home="H:/engines/fake",
    programs={"cli": "H:/engines/fake/bin/cli.exe"},
)


@pytest.fixture
def work(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIAVFX_GPU_LOCK", str(tmp_path / "gpu.lock"))
    monkeypatch.setattr(engines, "free_vram_gb", lambda: None)
    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Engines")
    db = Database(tmp_path)
    yield db, MediaLibrary(db, MediaTools())
    db.close()


def job(step, **params):
    return engine_job(step, params=params, registry_text=REGISTRY)


def create(db, *jobs):
    with db.transaction() as session:
        return OperationService().create(
            session, kind="voice_test", request={}, jobs=list(jobs), budget_limit=None
        )


def read(db, operation_id):
    with db.transaction() as session:
        return OperationService().operation(session, operation_id)


def runner(work, comfy=None, endpoint="http://fake", stop=None):
    db, library = work
    return OperationRunner(
        db, comfy or FakeComfy(), library, endpoint, poll_seconds=0, engines=COMMANDS, stop=stop
    )


def no_endpoint():
    raise AssertionError("CPU engine tasks must not resolve the ComfyUI endpoint")


def test_engine_jobs_are_validated(work):
    db, _ = work
    good = job("fake.echo")
    assert good.snapshot["engine"] == {
        "step": "fake.echo",
        "gpu": False,
        "vram_gb": 0.0,
        "registry": engine_jobs.registry.fingerprint("fake", REGISTRY),
        "worker_sha256": None,
        "spec": engine_jobs.voice_spec.fingerprint(),
    }
    for bad in (
        JobSpec("audio", "engine", "voice", step="fake.echo", snapshot={}),
        JobSpec(
            "audio", "engine", "voice", step="fake.echo", graph={"1": {}}, snapshot=good.snapshot
        ),
        JobSpec("audio", "engine", "voice", step="fake.report", snapshot=good.snapshot),
        JobSpec("audio", "engine", "voice", step="echo", snapshot={"engine": {"step": "echo"}}),
    ):
        with pytest.raises(ApplicationError, match="Engine jobs need"):
            create(db, bad)
    with pytest.raises(ApplicationError, match="No registered engine task"):
        job("fake.nothing")


def test_a_cpu_task_publishes_an_audio_candidate_with_engine_provenance(work):
    db, library = work
    operation = create(db, job("fake.echo"))
    assert operation["budget_reserved"] == 0  # engine work never draws on the render budget
    runner(work, endpoint=no_endpoint).run_once()
    saved = read(db, operation["id"])
    assert saved["state"] == "succeeded"
    assert saved["jobs"][0]["endpoint"] is None
    [candidate] = saved["candidates"]
    assert candidate["kind"] == "audio" and candidate["output_key"] == "line"
    with db.transaction() as session:
        asset = library.get(session, candidate["asset_id"])
    assert asset["kind"] == "audio"
    [provenance] = asset["provenance"]
    assert provenance["type"] == "generation_candidate"
    assert provenance["engine"]["step"] == "fake.echo"
    assert provenance["operation_id"] == operation["id"]
    assert not (db.root / "staging" / "operations" / saved["jobs"][0]["id"]).exists()


def test_an_artifact_output_becomes_a_library_artifact(work):
    db, library = work
    operation = create(db, job("fake.report"))
    runner(work, endpoint=no_endpoint).run_once()
    saved = read(db, operation["id"])
    assert saved["state"] == "succeeded" and not saved["candidates"]
    [output] = saved["jobs"][0]["outputs"]
    with db.transaction() as session:
        artifact = library.artifact(session, output["artifact_id"])
    assert artifact["kind"] == "analysis_report"


def marked(step="fake.echo", params=None, **options):
    return engine_job(step, params=params or {}, registry_text=REGISTRY, **options)


def test_an_engine_job_may_name_an_artifact_kind_and_a_stage():
    """ADR 0016 P5: the app decides which outputs are hidden material; a staged operation finds its jobs."""
    plain = job("fake.echo")
    assert "artifact_kind" not in plain.snapshot["engine"] and "stage" not in plain.snapshot
    stage = {"name": "material", "unit": "fish_audio"}
    spec = marked(artifact_kind="voice_material", stage=stage)
    assert spec.snapshot["engine"]["artifact_kind"] == "voice_material"
    assert spec.snapshot["stage"] == {"name": "material", "unit": "fish_audio"}
    stage["unit"] = "maya1"
    assert spec.snapshot["stage"]["unit"] == "fish_audio", "the stage is copied"
    assert "artifact_kind" not in marked(stage=stage).snapshot["engine"]
    assert "stage" not in marked(artifact_kind="voice_weights").snapshot
    for kind in ("audio", "audio_candidate", "voice"):
        with pytest.raises(ApplicationError, match="not a managed artifact kind") as refused:
            marked(artifact_kind=kind)
        assert refused.value.status == 422


def assets(db):
    with db.transaction() as session:
        return session.scalar(select(func.count()).select_from(AssetRecord))


def test_an_artifact_kind_keeps_job_kind_outputs_as_hidden_artifacts(work):
    db, library = work
    operation = create(db, marked(artifact_kind="voice_material"))
    runner(work, endpoint=no_endpoint).run_once()
    saved = read(db, operation["id"])
    assert saved["state"] == "succeeded"
    assert saved["candidates"] == [], "no candidate, so no prescreen"
    [output] = saved["jobs"][0]["outputs"]
    assert output["output_key"] == "line" and output["artifact_id"] and output["sha256"]
    with db.transaction() as session:
        artifact = library.artifact(session, output["artifact_id"])
    assert artifact["kind"] == "voice_material"
    assert artifact["info"]["seconds"] == 0.25 and artifact["info"]["size"] > 0
    assert assets(db) == 0, "hidden material never becomes a Library asset"


def test_a_managed_output_kind_is_an_artifact_with_or_without_the_flag(work):
    db, library = work
    for options in ({}, {"artifact_kind": "voice_material"}):
        operation = create(db, marked(params={"kind": "voice_weights"}, **options))
        runner(work, endpoint=no_endpoint).run_once()
        saved = read(db, operation["id"])
        assert saved["state"] == "succeeded" and saved["candidates"] == []
        [output] = saved["jobs"][0]["outputs"]
        with db.transaction() as session:
            assert library.artifact(session, output["artifact_id"])["kind"] == "voice_weights"
    assert assets(db) == 0


def test_an_unmanaged_output_kind_still_fails_the_job_with_the_flag(work):
    db, _ = work
    operation = create(db, marked(params={"kind": "image"}, artifact_kind="voice_material"))
    runner(work, endpoint=no_endpoint).run_once()
    [saved] = read(db, operation["id"])["jobs"]
    assert saved["state"] == "failed" and "Local output kind differs" in saved["error"]


def test_a_gpu_task_takes_the_lease_and_asks_comfy_to_free_its_models(work, monkeypatch):
    db, _ = work
    leases = []

    @contextmanager
    def lease(endpoint, cancelled, waiting=lambda: None):
        leases.append(endpoint)
        yield

    monkeypatch.setattr(engine_jobs, "gpu_lease", lease)
    comfy = FakeComfy()
    operation = create(db, job("fake.gpu_echo"))
    runner(work, comfy=comfy).run_once()
    assert read(db, operation["id"])["state"] == "succeeded"
    assert leases == ["http://fake"]
    assert ("POST", "/free", {"json": {"unload_models": True, "free_memory": True}}) in comfy.calls


def test_an_offline_comfy_does_not_stop_a_gpu_task(work):
    db, _ = work
    comfy = FakeComfy()

    def offline():
        raise httpx.ConnectError("refused")

    comfy.on_request = offline
    operation = create(db, job("fake.gpu_echo"))
    runner(work, comfy=comfy).run_once()
    assert read(db, operation["id"])["state"] == "succeeded"


def test_a_gpu_task_waits_for_free_memory(work, monkeypatch):
    db, _ = work
    readings = iter([1.0, 2.0, 24.0])
    monkeypatch.setattr(engines, "free_vram_gb", lambda: next(readings))
    monkeypatch.setattr(engine_jobs, "VRAM_WAIT_SECONDS", 0)
    operation = create(db, job("fake.gpu_echo"))
    runner(work).run_once()
    assert read(db, operation["id"])["state"] == "succeeded"
    assert next(readings, None) is None


def test_gpu_demand_counts_gpu_engine_tasks_only(work):
    db, _ = work
    create(db, job("fake.echo"))
    create(db, job("fake.gpu_echo"))
    assert runner(work).jobs_waiting_for_gpu() == 1


def test_cancel_before_start_never_launches(work):
    db, _ = work
    operation = create(db, job("fake.echo"))
    with db.transaction() as session:
        OperationService().request_cancel(session, operation["id"])
    runner(work, endpoint=no_endpoint).run_once()
    saved = read(db, operation["id"])
    assert saved["jobs"][0]["state"] == "cancelled"
    assert not (db.root / "logs" / "engines" / f"{saved['jobs'][0]['id']}.log").exists()


def test_cancel_while_running_cancels_the_job_and_records_progress(work):
    db, _ = work
    operation = create(db, job("fake.slow"))
    seen = {}

    def cancel_once_running():
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            saved = read(db, operation["id"])
            outputs = saved["jobs"][0]["outputs"]
            if outputs and outputs[0].get("progress", 0) > 0:
                seen["progress"] = outputs[0]["progress"]
                with db.transaction() as session:
                    OperationService().request_cancel(session, operation["id"])
                return
            time.sleep(0.1)

    thread = threading.Thread(target=cancel_once_running)
    thread.start()
    runner(work, endpoint=no_endpoint).run_once()
    thread.join()
    assert 0 < seen["progress"] < 1
    assert read(db, operation["id"])["jobs"][0]["state"] == "cancelled"


def test_a_worker_failure_fails_the_job_with_its_log(work):
    db, _ = work
    operation = create(db, job("fake.crash"))
    runner(work, endpoint=no_endpoint).run_once()
    saved = read(db, operation["id"])
    assert saved["jobs"][0]["state"] == "failed"
    assert "boom: the model file is missing" in saved["jobs"][0]["error"]


def test_shutdown_keeps_the_job_running_and_a_new_runner_relaunches_it(work):
    db, _ = work
    operation = create(db, job("fake.chunks", n=3, hang_at=1))
    stop = threading.Event()
    job_id = read(db, operation["id"])["jobs"][0]["id"]
    first_part = db.root / "staging" / "operations" / job_id / "outputs" / "part0.wav"

    def stop_after_first_part():
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline and not first_part.exists():
            time.sleep(0.05)
        stop.set()

    thread = threading.Thread(target=stop_after_first_part)
    thread.start()
    assert runner(work, endpoint=no_endpoint, stop=stop).run_once() is False
    thread.join()
    assert read(db, operation["id"])["jobs"][0]["state"] == "running"
    runner(work, endpoint=no_endpoint).run_once()  # claim_next resumes running jobs first
    saved = read(db, operation["id"])
    assert saved["state"] == "succeeded"
    assert sorted(c["output_key"] for c in saved["candidates"]) == ["part0", "part1", "part2"]
    log = (db.root / "logs" / "engines" / f"{job_id}.log").read_text(encoding="utf-8")
    assert log.count("--- launch") == 2 and "skipped 1" in log


def test_resolve_builds_the_command_from_the_engine_home(tmp_path, monkeypatch):
    root, workers = tmp_path / "engines", tmp_path / "workers"
    (root / "fake" / "env").mkdir(parents=True)
    workers.mkdir()
    monkeypatch.setenv("GAIAVFX_ENGINES_ROOT", str(root))
    monkeypatch.setenv("GAIAVFX_ENGINE_WORKERS", str(workers))
    media = tmp_path / "media"
    monkeypatch.setenv("GAIAVFX_MEDIA_DIR", str(media))
    with pytest.raises(ApplicationError, match="not installed"):
        engine_jobs.resolve("fake.echo", REGISTRY)
    (root / "fake" / "env" / "python.exe").write_bytes(b"")
    with pytest.raises(ApplicationError, match="worker .* is missing"):
        engine_jobs.resolve("fake.echo", REGISTRY)
    (workers / "fake_worker.py").write_text("", encoding="utf-8")
    command = engine_jobs.resolve("fake.echo", REGISTRY)
    assert command.command == (
        str(root / "fake" / "env" / "python.exe"),
        str(workers / "fake_worker.py"),
    )
    assert command.home == str(root / "fake")
    # Missing override tools may now fall back to the repo runtime or PATH.
    assert command.programs["cli"] == str(root / "fake" / "bin" / "cli.exe")
    # The app's media tools, once installed, are every engine's ffmpeg and ffprobe too.
    tools = MediaTools(media)
    media.mkdir()
    tools.ffmpeg.write_bytes(b"")
    tools.ffprobe.write_bytes(b"")
    assert engine_jobs.resolve("fake.echo", REGISTRY).programs == {
        "cli": str(root / "fake" / "bin" / "cli.exe"),
        "ffmpeg": str(tools.ffmpeg),
        "ffprobe": str(tools.ffprobe),
    }
    assert command.env["HF_HUB_OFFLINE"] == "1"
    assert command.env["TMP"] == str(root / "tmp" / "fake") and (root / "tmp" / "fake").is_dir()
    monkeypatch.delenv("GAIAVFX_ENGINES_ROOT")
    with pytest.raises(ApplicationError, match="No engines root"):
        engine_jobs.resolve("fake.echo", REGISTRY)


def test_the_job_document_hands_the_worker_its_home_and_programs(work):
    db, _ = work
    operation = create(
        db,
        job("fake.engine_info", home="H:/engines/fake", cli="H:/engines/fake/bin/cli.exe"),
    )
    runner(work, endpoint=no_endpoint).run_once()
    assert read(db, operation["id"])["state"] == "succeeded"


def test_readiness_reads_the_home_manifest_and_probe(tmp_path, monkeypatch):
    monkeypatch.delenv("GAIAVFX_ENGINES_ROOT", raising=False)
    root = tmp_path / "engines"
    assert engine_jobs.readiness("", REGISTRY)["engines"][0]["installed"] is False
    home = root / "fake"
    (home / "env").mkdir(parents=True)
    (home / "env" / "python.exe").write_bytes(b"")
    (home / "manifest.json").write_text(
        json.dumps({"files": [{"state": "ok"}, {"state": "missing"}], "verified_at": "t1"}),
        encoding="utf-8",
    )
    (home / "probe.json").write_text(
        json.dumps(
            {
                "state": "ok",
                "python": "3.11.15",
                "cuda": {"available": True, "device": "RTX 5090"},
                "drift": {"torch": {"pinned": "2.7.1", "installed": "2.7.0"}},
                "probed_at": "t2",
            }
        ),
        encoding="utf-8",
    )
    report = engine_jobs.readiness(str(root), REGISTRY)
    assert (report["root"], report["root_source"]) == (str(root), "setting")
    [fake] = report["engines"]
    assert fake["installed"] and fake["home"] == str(home)
    assert fake["files"] == {"ok": 1, "missing": 1, "mismatch": 0}
    assert (fake["probe"], fake["python"], fake["cuda_device"]) == ("ok", "3.11.15", "RTX 5090")
    assert fake["drift"] == ["torch"] and fake["gpu_tasks"] == ["gpu_echo"]


def test_jobs_of_one_operation_share_a_scratch_folder_that_outlives_their_work(work):
    db, _ = work
    first = job("fake.scratch_note", name="first", expect=[])
    second = engine_job(
        "fake.scratch_note",
        params={"name": "second", "expect": ["first"]},
        registry_text=REGISTRY,
        depends_on=0,
    )
    operation = create(db, first, second)
    runner(work, endpoint=no_endpoint).run_once()
    runner(work, endpoint=no_endpoint).run_once()
    saved = read(db, operation["id"])
    assert [j["state"] for j in saved["jobs"]] == ["succeeded", "succeeded"]
    scratch = db.root / "staging" / "scratch" / operation["id"]
    assert (scratch / "notes.txt").read_text(encoding="utf-8").split() == ["first", "second"]
    assert not (db.root / "staging" / "operations" / saved["jobs"][0]["id"]).exists()


def test_the_worker_environment_strips_app_python_and_works_offline(work, tmp_path):
    db, _ = work
    base = {**os.environ, "PYTHONPATH": "X:/app/backend/src", "VIRTUAL_ENV": "X:/app/.venv"}
    root = (
        tmp_path / "engines root é"
    )  # spaces and non-ASCII in the path reach a real child process
    env = engine_jobs.worker_environment(root, "fake", base=base)
    assert "PYTHONPATH" not in env and "VIRTUAL_ENV" not in env
    assert env["PYTHONNOUSERSITE"] == "1" and env["HF_DATASETS_OFFLINE"] == "1"
    assert env["PYTHONDONTWRITEBYTECODE"] == "1", "bytecode caches would land in the engine home"
    assert env["NUMBA_CACHE_DIR"].startswith(env["TMP"]), (
        "numba caches go to the engine tmp, not the home"
    )
    assert env.get("SYSTEMROOT") or env.get("SystemRoot") or os.name != "nt"
    temporary = str(root / "tmp" / "fake")
    assert env["TMP"] == env["TEMP"] == temporary
    commands = {
        "fake.env_check": EngineCommand((sys.executable, str(WORKER)), env=env),
    }
    operation = create(db, job("fake.env_check", tmp=temporary))
    _, library = work
    OperationRunner(
        db, FakeComfy(), library, no_endpoint, poll_seconds=0, engines=commands
    ).run_once()
    assert read(db, operation["id"])["state"] == "succeeded"
