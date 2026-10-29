"""Recovery and collection through the in-process ComfyUI contract."""

from __future__ import annotations

import hashlib
import io
import json
import struct
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from dataclasses import replace
from pathlib import Path

import httpx
import pytest
from fake_comfy import Crash, FakeComfy
from filelock import FileLock
from PIL import Image
from sqlalchemy import text

from gaiavfx.application.operations import (
    OperationRunner,
    create_operation,
    knowledge_snapshot,
)
from gaiavfx.infrastructure.database import Database
from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.infrastructure.views_sheet import render_views_sheet
from gaiavfx.integrations import minimax_h3
from gaiavfx.integrations.minimax_h3.sheet import views_sheet_layout
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService, MediaLibrary
from gaiavfx.shared.errors import ApplicationError


@pytest.fixture
def work(tmp_path):
    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Runner")
    db = Database(tmp_path)
    yield db, MediaLibrary(db, MediaTools())
    db.close()


def png(rgb=(40, 80, 120)):
    stream = io.BytesIO()
    Image.new("RGB", (16, 12), rgb).save(stream, "PNG")
    return stream.getvalue()


def output(graph):
    key = "save:images:0"
    return (
        {"save": {"images": [{"filename": "made.png", "type": "output"}]}},
        {key: png((graph["seed"], 80, 120))},
    )


def spec(seed=1, kind="image", integration="fake"):
    return JobSpec(
        kind=kind,
        executor="comfy",
        integration=integration,
        graph={"seed": seed},
        inputs={},
        snapshot={"knowledge": {"pack.toml": "full text\n"}},
        seed=seed,
    )


def create(db, jobs):
    with db.transaction() as session:
        return OperationService().create(
            session, kind="render", request={}, jobs=jobs, budget_limit=None
        )


def read(db, operation_id):
    with db.transaction() as session:
        return OperationService().operation(session, operation_id)


def runner(work, fake):
    db, library = work
    return OperationRunner(db, fake, library, "http://fake", poll_seconds=0)


def test_gpu_demand_reader_includes_claimed_and_recovery_not_local_or_collecting(work):
    db, _ = work
    op = create(db, [spec()])
    run = runner(work, FakeComfy(output))
    service = OperationService()
    assert run.jobs_waiting_for_gpu() == 1
    with db.transaction() as session:
        job = service.claim_next(session)
    assert run.jobs_waiting_for_gpu() == 1
    for state in ("submitting", "running", "collecting", "succeeded"):
        with db.transaction() as session:
            service.transition(session, job["id"], state, submitted=True)
        assert run.jobs_waiting_for_gpu() == (state in {"submitting", "running"})
    create(
        db,
        [
            JobSpec(
                kind="image",
                executor="local",
                integration="fake",
                step="fit_frame",
                inputs={},
                snapshot={},
            )
        ],
    )
    assert run.jobs_waiting_for_gpu() == 0
    cancelled = create(db, [spec()])
    with db.transaction() as session:
        service.request_cancel(session, cancelled["id"])
    assert run.jobs_waiting_for_gpu() == 0
    assert op["id"] != cancelled["id"]


@pytest.mark.parametrize("error", [Crash, httpx.ConnectError])
def test_accepted_post_is_reconciled_once(work, error):
    db, _ = work
    op = create(db, [spec()])
    fake = FakeComfy(output)
    fake.crash_after_accept = error
    first = runner(work, fake)
    if error is Crash:
        with pytest.raises(Crash):
            first.run_once()
        assert read(db, op["id"])["jobs"][0]["state"] == "submitting"
        second = runner(work, fake)
        second.recover()
        second.run_once()
    else:
        first.run_once()
    saved = read(db, op["id"])
    assert saved["state"] == "succeeded"
    assert len(saved["candidates"]) == len(fake.posts) == 1


def test_lost_submission_needs_attention(work):
    db, _ = work
    op = create(db, [spec()])
    fake = FakeComfy(output)
    fake.drop_prompt = True
    fake.crash_after_accept = httpx.ConnectError
    runner(work, fake).run_once()
    saved = read(db, op["id"])
    assert saved["state"] == saved["jobs"][0]["state"] == "needs_attention"
    assert len(fake.posts) == 1


def test_accepted_prompt_missing_from_queue_and_history_needs_attention(work):
    db, _ = work
    op = create(db, [spec()])
    fake = FakeComfy(output)

    def drop_after_accept():
        if fake.calls[-1][:2] == ("GET", "/history/1"):
            fake.prompts.clear()
            fake.on_request = None

    fake.on_request = drop_after_accept
    runner(work, fake).run_once()
    saved = read(db, op["id"])
    assert saved["state"] == "needs_attention"
    assert "not resubmitted" in saved["jobs"][0]["error"]
    assert len(fake.posts) == 1


def test_restart_running_does_not_upload_or_post_again(work):
    db, _ = work
    op = create(db, [spec()])
    fake = FakeComfy(output)
    fake.crash_on_poll = 1
    with pytest.raises(Crash):
        runner(work, fake).run_once()
    assert read(db, op["id"])["jobs"][0]["state"] == "running"
    resumed = runner(work, fake)
    resumed.recover()
    resumed.run_once()
    assert read(db, op["id"])["state"] == "succeeded"
    assert len(fake.posts) == 1


@pytest.mark.parametrize("crash_index", [0, 1])
def test_collection_resumes_without_duplicate_candidates(work, crash_index):
    db, _ = work
    op = create(db, [spec()])

    def two(_graph):
        return (
            {
                "save": {
                    "images": [
                        {"filename": "a.png", "type": "output"},
                        {"filename": "b.png", "type": "output"},
                    ]
                }
            },
            {"save:images:0": png((1, 2, 3)), "save:images:1": png((4, 5, 6))},
        )

    fake = FakeComfy(two)
    original = fake.download

    def crash_download(url, descriptor, target):
        if descriptor["output_key"].endswith(f":{crash_index}"):
            fake.download = original
            raise Crash("collection crash")
        original(url, descriptor, target)

    fake.download = crash_download
    with pytest.raises(Crash):
        runner(work, fake).run_once()
    interrupted = read(db, op["id"])
    assert interrupted["jobs"][0]["state"] == "collecting"
    assert len(interrupted["candidates"]) == crash_index
    runner(work, fake).run_once()
    saved = read(db, op["id"])
    assert saved["state"] == "succeeded"
    assert len(saved["candidates"]) == 2
    assert fake.downloads.count("save:images:0") == 1


def test_n_candidates_unreviewed_and_declared_kind(work):
    db, library = work
    with db.transaction() as session:
        with pytest.raises(ApplicationError) as error:
            create_operation(
                session,
                OperationService(),
                {"fake": frozenset({"video"})},
                kind="render",
                request={},
                jobs=[spec()],
                budget_limit=None,
            )
        assert error.value.status == 422
    op = create(db, [spec(seed) for seed in (1, 2, 3)])
    fake = FakeComfy(output)
    worker = runner(work, fake)
    for _ in range(3):
        worker.run_once()
    saved = read(db, op["id"])
    assert saved["state"] == "succeeded"
    assert len(saved["candidates"]) == 3
    with db.transaction() as session:
        for candidate in saved["candidates"]:
            asset = library.get(session, candidate["asset_id"])
            assert asset["review"] == "unreviewed"
            assert asset["provenance"][0]["seed"] in (1, 2, 3)
            assert "selected" not in asset["provenance"][0]


def test_local_sheet_and_knowledge(work):
    db, library = work
    ids = []
    for color in ((1, 2, 3), (4, 5, 6), (7, 8, 9)):
        source = db.root / f"{color[0]}.png"
        source.write_bytes(png(color))
        prepared = library.prepare(source, source.name)
        with db.transaction() as session:
            ids.append(library.register(session, prepared, {"type": "fixture"}))
    inputs = {asset["id"]: {"sha256": asset["sha256"]} for asset in ids}
    op = create(
        db,
        [
            JobSpec(
                kind="image",
                executor="local",
                integration="fake",
                step="views_sheet",
                inputs=inputs,
                snapshot={
                    "step_spec": {
                        "tiles": [asset["id"] for asset in ids],
                        "columns": 2,
                        "gap": 2,
                        "gap_rgb": [120, 120, 120],
                    }
                },
            )
        ],
    )
    fake = FakeComfy()
    runner(work, fake).run_once()
    saved = read(db, op["id"])
    assert saved["state"] == "succeeded" and len(saved["candidates"]) == 1
    assert not fake.calls
    with db.transaction() as session:
        actual = library.get(session, saved["candidates"][0]["asset_id"])["sha256"]
        paths = [library.path(session, asset["id"]) for asset in ids]
    expected = db.root / "direct.png"
    render_views_sheet(paths, views_sheet_layout([(16, 12)] * 3, 2, 2), (120, 120, 120)).save(
        expected
    )
    assert actual == hashlib.sha256(expected.read_bytes()).hexdigest()
    knowledge = knowledge_snapshot(minimax_h3)
    assert {"pack.toml", "wording.toml", "render.toml"} <= knowledge.keys()
    assert knowledge["pack.toml"] == (
        Path(minimax_h3.__file__).parent / "knowledge" / "pack.toml"
    ).read_text(encoding="utf-8")
    frozen = create(
        db,
        [replace(spec(kind="video", integration="minimax_h3"), snapshot={"knowledge": knowledge})],
    )
    assert read(db, frozen["id"])["jobs"][0]["snapshot"]["knowledge"] == knowledge


def test_cancel_before_claim_and_while_pending(work):
    db, _ = work
    op = create(db, [spec()])
    fake = FakeComfy(output)
    with db.transaction() as session:
        OperationService().request_cancel(session, op["id"])
    assert not runner(work, fake).run_once()
    assert read(db, op["id"])["state"] == "cancelled"
    assert not fake.posts

    op = create(db, [spec()])

    def cancel_on_first_poll():
        if fake.calls[-1][:2] == ("GET", "/history/1"):
            with db.transaction() as session:
                OperationService().request_cancel(session, op["id"])
            fake.on_request = None

    fake.on_request = cancel_on_first_poll
    runner(work, fake).run_once()
    assert read(db, op["id"])["state"] == "cancelled"
    assert any(call[:2] == ("POST", "/queue") for call in fake.calls)
    assert not any(call[:2] == ("POST", "/interrupt") for call in fake.calls)


def test_cancel_running_sends_targeted_interrupt(work):
    db, _ = work
    op = create(db, [spec()])
    fake = FakeComfy(output, polls_to_complete=5)

    def cancel_when_running():
        if fake.calls[-1][:2] == ("GET", "/history/1") and fake._polls == 1:
            with db.transaction() as session:
                OperationService().request_cancel(session, op["id"])
            fake.on_request = None

    fake.on_request = cancel_when_running
    runner(work, fake).run_once()
    saved = read(db, op["id"])
    assert saved["state"] == "cancelled" and saved["budget_released_at"]
    interrupts = [call for call in fake.calls if call[:2] == ("POST", "/interrupt")]
    assert len(interrupts) == 1 and interrupts[0][2]["json"] == {"prompt_id": "1"}
    assert not saved["candidates"]


def test_image_and_video_candidates_and_mismatched_output(work):
    db, library = work
    clip = db.root / "tiny.mp4"
    library.media.run(
        library.media.ffmpeg,
        [
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=32x32:rate=24:duration=0.25",
            "-c:v",
            "libx264",
            str(clip),
        ],
    )

    def outputs(graph):
        if graph["seed"] == 1:
            return (
                {"save": {"videos": [{"filename": "tiny.mp4", "type": "output"}]}},
                {"save:videos:0": clip.read_bytes()},
            )
        return output(graph)

    op = create(db, [spec(1, "video"), spec(2, "image")])
    fake = FakeComfy(outputs)
    worker = runner(work, fake)
    worker.run_once()
    worker.run_once()
    saved = read(db, op["id"])
    assert saved["state"] == "succeeded"
    assert {candidate["kind"] for candidate in saved["candidates"]} == {"image", "video"}
    with db.transaction() as session:
        assert {
            library.get(session, candidate["asset_id"])["kind"] for candidate in saved["candidates"]
        } == {"image", "video"}

    bad = create(db, [spec(3, "video")])
    wrong = FakeComfy(
        lambda _graph: (
            {"save": {"videos": [{"filename": "wrong.png", "type": "output"}]}},
            {"save:videos:0": png()},
        )
    )
    runner(work, wrong).run_once()
    failed = read(db, bad["id"])
    assert failed["state"] == "failed"
    assert (
        db.root / "staging" / "operations" / failed["jobs"][0]["id"] / "save-videos-0.png"
    ).is_file()


def test_external_calls_are_outside_transactions_and_lease_stops_before_download(work, monkeypatch):
    from contextlib import contextmanager

    from gaiavfx.application import operations

    db, library = work
    source = db.root / "source.png"
    source.write_bytes(png())
    prepared = library.prepare(source, source.name)
    with db.transaction() as session:
        asset = library.register(session, prepared, {"type": "fixture"})
    job = replace(
        spec(),
        inputs={asset["id"]: {"sha256": asset["sha256"], "remote_name": "inputs/source.png"}},
    )
    op = create(db, [job])
    fake = FakeComfy(output)
    order = []

    @contextmanager
    def lease(_endpoint, _cancelled):
        order.append("lease-enter")
        yield
        order.append("lease-exit")

    monkeypatch.setattr(operations, "gpu_lease", lease)

    register = library.register

    def record_register(*args, **kwargs):
        order.append("register")
        return register(*args, **kwargs)

    monkeypatch.setattr(library, "register", record_register)

    def probe():
        with db.transaction() as session:
            session.execute(
                text("UPDATE generation_operations SET updated_at = updated_at WHERE id = :id"),
                {"id": op["id"]},
            )
        order.append(fake.calls[-1][0] + " " + fake.calls[-1][1])

    fake.on_request = probe

    class ProbedStop:
        def is_set(self):
            return False

        def wait(self, _seconds):
            probe()
            order.append("wait")

    OperationRunner(db, fake, library, "http://fake", poll_seconds=0, stop=ProbedStop()).run_once()
    assert order.index("UPLOAD inputs/source.png") < order.index("lease-enter")
    assert order.index("lease-enter") < order.index("POST /prompt")
    assert order.index("POST /prompt") < order.index("GET /history/1")
    assert "wait" in order
    assert order.index("lease-exit") < order.index("DOWNLOAD save:images:0")
    assert order.index("DOWNLOAD save:images:0") < order.index("register")
    assert read(db, op["id"])["state"] == "succeeded"


def context_payload():
    header = json.dumps(
        {
            "__metadata__": {"format": "h3_motion_context_av_v1"},
            "video": {"shape": [1, 2, 3, 4, 5]},
            "audio": {"shape": [1, 2, 3, 4]},
        }
    ).encode()
    return struct.pack("<Q", len(header)) + header


def test_artifact_upload_and_tamper_refusal(work):
    db, library = work
    source = db.root / "context.safetensors"
    source.write_bytes(context_payload())
    prepared = library.prepare_artifact(source)
    with db.transaction() as session:
        artifact = library.register_artifact(session, prepared)
    inputs = {
        artifact["id"]: {
            "source": "artifact",
            "sha256": artifact["sha256"],
            "remote_name": "gaiavfx/t/context.safetensors",
            "directory": "output",
        }
    }
    op = create(db, [replace(spec(), inputs=inputs)])
    fake = FakeComfy(output)
    runner(work, fake).run_once()
    assert read(db, op["id"])["state"] == "succeeded"
    assert fake.upload_directories == ["output"]
    with db.transaction() as session:
        library.artifact_path(session, artifact["id"]).write_bytes(b"tampered")
    bad = create(db, [replace(spec(), inputs=inputs)])
    untouched = FakeComfy(output)
    runner(work, untouched).run_once()
    assert "A frozen continuation artifact changed" in read(db, bad["id"])["jobs"][0]["error"]
    assert not untouched.posts


def test_context_capture_recovers_without_redownloading_video(work):
    db, library = work
    clip = db.root / "tiny.mp4"
    library.media.run(
        library.media.ffmpeg,
        [
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=32x32:rate=24:duration=0.25",
            "-c:v",
            "libx264",
            str(clip),
        ],
    )
    descriptor = {
        "output_key": "context",
        "filename": "context_00001.safetensors",
        "subfolder": "gaiavfx/t",
        "type": "output",
    }
    job = replace(
        spec(kind="video"),
        snapshot={
            "knowledge": {"pack.toml": "text"},
            "capture_context": descriptor,
        },
    )
    op = create(db, [job])
    fake = FakeComfy(
        lambda _: (
            {"save": {"videos": [{"filename": "tiny.mp4", "type": "output"}]}},
            {"save:videos:0": clip.read_bytes(), "context": context_payload()},
        )
    )
    original = fake.download

    def interrupted(url, requested, target):
        if requested["output_key"] == "context":
            fake.download = original
            raise Crash("context download interrupted")
        return original(url, requested, target)

    fake.download = interrupted
    with pytest.raises(Crash):
        runner(work, fake).run_once()
    interrupted_op = read(db, op["id"])
    assert interrupted_op["jobs"][0]["state"] == "collecting"
    assert len(interrupted_op["candidates"]) == 1
    runner(work, fake).run_once()
    saved = read(db, op["id"])
    candidate = saved["candidates"][0]
    assert saved["state"] == "succeeded" and candidate["artifact_id"]
    assert fake.downloads.count("save:videos:0") == 1
    with db.transaction() as session:
        artifact = library.artifact(session, candidate["artifact_id"])
        assert artifact["kind"] == "h3_context"
        assert (
            library.artifact_path(session, artifact["id"]).name
            == artifact["sha256"] + ".safetensors"
        )


def test_ambiguous_job_output_fails_dependent(work):
    db, _ = work
    jobs = [
        JobSpec("image", "local", "minimax_h3", step="views_sheet"),
        JobSpec(
            "image",
            "local",
            "minimax_h3",
            step="fit_frame",
            depends_on=0,
            inputs={"source": {"source": "job_output"}},
            snapshot={"step_spec": {"source": "source", "width": 8, "height": 8}},
        ),
    ]
    op = create(db, jobs)
    with db.transaction() as session:
        service = OperationService()
        first = op["jobs"][0]
        service.transition(session, first["id"], "claimed")
        service.transition(session, first["id"], "running")
        service.transition(session, first["id"], "collecting", outputs=[])
        service.add_candidate(session, first["id"], "asset-a", "a", "image")
        service.add_candidate(session, first["id"], "asset-b", "b", "image")
        service.transition(session, first["id"], "succeeded")
    runner(work, FakeComfy()).run_once()
    saved = read(db, op["id"])
    assert saved["jobs"][1]["state"] == "failed"
    assert "The input job produced no usable output (2 candidates)." == saved["jobs"][1]["error"]


def test_create_operation_accepts_local_image_for_h3(work):
    db, _ = work
    with db.transaction() as session:
        operation = create_operation(
            session,
            OperationService(),
            {"minimax_h3": frozenset({"video"})},
            kind="fit",
            request={},
            budget_limit=None,
            jobs=[JobSpec("image", "local", "minimax_h3", step="fit_frame")],
        )
        assert operation["jobs"][0]["kind"] == "image"


def test_transient_poll_error_keeps_the_accepted_render(work):
    db, _ = work
    op = create(db, [spec()])
    fake = FakeComfy(output)
    raised = []

    def flaky():
        method, route, _ = fake.calls[-1]
        if method == "GET" and route.startswith("/history/") and not raised:
            raised.append(route)
            raise httpx.ReadTimeout("slow")

    fake.on_request = flaky
    worker = runner(work, fake)
    worker.run_once()
    job = read(db, op["id"])["jobs"][0]
    assert raised and job["state"] == "running" and job["remote_id"]
    worker.run_once()
    saved = read(db, op["id"])
    assert saved["state"] == "succeeded"
    assert len(saved["candidates"]) == len(fake.posts) == 1


def test_cancel_after_restart_interrupts_the_remote_prompt(work):
    db, _ = work
    op = create(db, [spec()])
    fake = FakeComfy(output, polls_to_complete=10)
    fake.crash_on_poll = 2
    with pytest.raises(Crash):
        runner(work, fake).run_once()
    assert read(db, op["id"])["jobs"][0]["state"] == "running"
    with db.transaction() as session:
        OperationService().request_cancel(session, op["id"])
    resumed = runner(work, fake)
    resumed.recover()
    resumed.run_once()
    saved = read(db, op["id"])
    assert saved["state"] == saved["jobs"][0]["state"] == "cancelled"
    assert saved["budget_released_at"] is not None
    assert any(call[:2] == ("POST", "/interrupt") for call in fake.calls)
    assert len(fake.posts) == 1


def test_handlers_read_the_real_save_video_shape():
    from gaiavfx.application.operations import HANDLERS

    history = {
        "release": {"text": ["11,213,078,568x23,869,337,692"]},
        "save_video": {
            "images": [{"filename": "delivered_00001_.mp4", "subfolder": "x", "type": "output"}],
            "animated": [True],
        },
        "still": {"images": [{"filename": "frame.png", "type": "output"}]},
    }
    videos = HANDLERS["video"].descriptors(history)
    images = HANDLERS["image"].descriptors(history)
    assert [d["output_key"] for d in videos] == ["save_video:images:0"]
    assert [d["output_key"] for d in images] == ["still:images:0"]


@pytest.mark.parametrize("interrupted_state", ["submitting", "running", "collecting"])
def test_resumed_job_keeps_submitted_endpoint(work, monkeypatch, interrupted_state):
    db, library = work
    op = create(db, [spec()])
    fake = FakeComfy(output)
    calls = []
    current_endpoint = "http://endpoint-a"
    resolutions = []

    def endpoint():
        resolutions.append(current_endpoint)
        return current_endpoint

    request, download = fake.request, fake.download
    interrupted = False

    def record_request(url, method, route, **kwargs):
        nonlocal interrupted
        calls.append((url, method, route))
        if interrupted_state == "running" and route.startswith("/history/") and not interrupted:
            interrupted = True
            raise httpx.ReadTimeout("transient poll failure")
        return request(url, method, route, **kwargs)

    def record_download(url, descriptor, target):
        nonlocal interrupted
        calls.append((url, "DOWNLOAD", descriptor["output_key"]))
        if interrupted_state == "collecting" and not interrupted:
            interrupted = True
            raise httpx.ReadTimeout("transient download failure")
        return download(url, descriptor, target)

    monkeypatch.setattr(fake, "request", record_request)
    monkeypatch.setattr(fake, "download", record_download)
    worker = OperationRunner(db, fake, library, endpoint, poll_seconds=0)
    if interrupted_state == "submitting":
        fake.crash_after_accept = Crash
        with pytest.raises(Crash):
            worker.run_once()
    else:
        worker.run_once()
    job = read(db, op["id"])["jobs"][0]
    assert job["state"] == interrupted_state
    assert job["submitted"] and job["endpoint"] == current_endpoint
    current_endpoint = "http://endpoint-b"
    resumed = OperationRunner(db, fake, library, endpoint, poll_seconds=0)
    resumed.recover()
    resumed.run_once()
    saved = read(db, op["id"])
    assert saved["state"] == "succeeded"
    assert len(saved["candidates"]) == len(fake.posts) == 1
    assert resolutions == ["http://endpoint-a"]
    assert {call[0] for call in calls} == {"http://endpoint-a"}
    assert any(call[1] == "DOWNLOAD" for call in calls)


@pytest.mark.parametrize("already_running", [False, True])
def test_shutdown_while_waiting_for_lease_preserves_job(work, monkeypatch, already_running):
    from gaiavfx.application import operations
    from gaiavfx.infrastructure.coordination import gpu_lease

    db, _ = work
    op = create(db, [spec()])
    fake = FakeComfy(output)
    worker = runner(work, fake)
    if already_running:
        fake.crash_on_poll = 1
        with pytest.raises(Crash):
            worker.run_once()
    lock_path = db.root / "test-gpu.lock"
    monkeypatch.setenv("GAIAVFX_GPU_LOCK", str(lock_path))
    waiting = threading.Event()

    @contextmanager
    def observed_lease(endpoint, cancelled):
        with gpu_lease(endpoint, cancelled, waiting=waiting.set):
            yield

    monkeypatch.setattr(operations, "gpu_lease", observed_lease)
    with ThreadPoolExecutor(max_workers=1) as pool, FileLock(str(lock_path)):
        future = pool.submit(worker.run_once)
        assert waiting.wait(3), "runner did not wait for the held lease"
        before = read(db, op["id"])["jobs"][0]
        started = time.monotonic()
        worker.stop.set()
        assert future.result(timeout=1) is False
        assert time.monotonic() - started < 1
        assert read(db, op["id"])["jobs"][0] == before
        assert before["state"] == ("running" if already_running else "claimed")
        assert before["submitted"] is already_running
        assert len(fake.posts) == int(already_running)
    resumed = runner(work, fake)
    resumed.recover()
    resumed.run_once()
    assert read(db, op["id"])["state"] == "succeeded"
    assert len(fake.posts) == 1


def test_shutdown_after_lease_acquired_never_submits(work, monkeypatch):
    from gaiavfx.application import operations

    db, _ = work
    op = create(db, [spec()])
    fake = FakeComfy(output)
    worker = runner(work, fake)

    @contextmanager
    def stop_at_acquisition(_endpoint, cancelled):
        assert not cancelled()
        worker.stop.set()
        yield

    monkeypatch.setattr(operations, "gpu_lease", stop_at_acquisition)
    assert worker.run_once() is False
    job = read(db, op["id"])["jobs"][0]
    assert job["state"] == "claimed" and not job["submitted"]
    assert job["endpoint"] is None
    assert not fake.posts


def test_local_job_never_resolves_or_records_endpoint(work):
    db, library = work
    op = create(db, [JobSpec("image", "local", "fake", step="empty")])

    def endpoint():
        raise AssertionError("local jobs must not resolve Settings")

    worker = OperationRunner(db, FakeComfy(), library, endpoint, steps={"empty": lambda _: []})
    worker.run_once()
    saved = read(db, op["id"])
    assert saved["state"] == "succeeded"
    assert saved["jobs"][0]["endpoint"] is None


@pytest.mark.parametrize("poll", [1, 2])
def test_cancel_after_endpoint_change_targets_original_server(work, monkeypatch, poll):
    db, library = work
    op = create(db, [spec()])
    fake = FakeComfy(output, polls_to_complete=10)
    fake.crash_on_poll = poll
    endpoint = "http://endpoint-a"
    worker = OperationRunner(db, fake, library, lambda: endpoint, poll_seconds=0)
    with pytest.raises(Crash):
        worker.run_once()
    endpoint = "http://endpoint-b"
    with db.transaction() as session:
        OperationService().request_cancel(session, op["id"])
    request = fake.request
    urls = []

    def record(url, method, route, **kwargs):
        urls.append(url)
        return request(url, method, route, **kwargs)

    monkeypatch.setattr(fake, "request", record)
    worker.run_once()
    assert read(db, op["id"])["state"] == "cancelled"
    assert set(urls) == {"http://endpoint-a"}
    route = "/queue" if poll == 1 else "/interrupt"
    assert any(call[:2] == ("POST", route) for call in fake.calls)
    assert len(fake.posts) == 1
