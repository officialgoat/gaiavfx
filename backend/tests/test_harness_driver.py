"""The driver releases model resources on success, crash and cancellation."""

from contextlib import contextmanager
from types import SimpleNamespace

import harness_support
import pytest
from harness_support import action

from gaiavfx.application.harness.model import AssistantModel

kernel = harness_support.kernel


@pytest.fixture
def warm_model(kernel, monkeypatch, tmp_path):
    import gaiavfx.application.harness.model as module

    monkeypatch.setenv("GAIAVFX_GPU_LOCK", str(tmp_path / "gpu.lock"))
    monkeypatch.setattr(
        module, "resource_identity", lambda settings: {"model": settings.get("assistant_model")}
    )
    events = []
    now = [0.0]
    gateway = SimpleNamespace(
        run=lambda *args, **kwargs: events.append("call") or action("say", text="Ready"),
        stop=lambda: events.append("stop"),
    )
    comfy = SimpleNamespace(request=lambda *args, **kwargs: events.append("free"))
    model = AssistantModel(gateway, comfy, clock=lambda: now[0])
    kernel.harness.model = model
    # Frozen session settings, as in the app.
    row = kernel.harness.read(kernel.id)
    row["document"]["settings"]["comfy_url"] = "http://fake"
    with kernel.db.transaction() as session:
        kernel.store.update_session(
            session, row["id"], row["revision"], state="idle", document=row["document"]
        )
    yield model, events, now
    model.end()


def test_warm_messages_reuse_and_render_demand_yields(kernel, warm_model):
    model, events, _ = warm_model
    for key in ("one", "two"):
        kernel.harness.post_message(kernel.id, "Report", key)
        assert kernel.runner.run_once()
        assert model.status()["state"] == "ready"
        assert model.status()["session_id"] == kernel.id
    assert events == ["free", "call", "call"]
    kernel.runner.gpu_wanted = lambda: True
    kernel.harness.post_message(kernel.id, "Report", "three")
    assert not kernel.runner.run_once()  # the already queued render gets priority
    assert events[-1] == "stop" and model.status()["state"] == "unloaded"
    kernel.runner.gpu_wanted = lambda: False
    assert kernel.runner.run_once()
    assert events.count("free") == 2


@pytest.mark.parametrize(
    "trigger", ["expiry", "zero", "stop", "recover", "cancel", "parked_cancel"]
)
def test_park_release_policy(kernel, warm_model, trigger):
    model, events, now = warm_model
    kernel.harness.post_message(kernel.id, "Report", "one")
    assert kernel.runner.run_once()
    if trigger == "expiry":
        now[0] = 179
        assert not kernel.runner.run_once() and model.active
        now[0] = 180
    elif trigger == "zero":
        kernel.settings["agent_keep_warm_seconds"] = 0
    elif trigger == "stop":
        kernel.runner.stop.set()
    elif trigger == "recover":
        kernel.runner.recover()
    elif trigger == "parked_cancel":
        kernel.harness.request_cancel(kernel.id)
    else:
        kernel.harness.post_message(kernel.id, "Report", "cancel")
        kernel.harness.request_cancel(kernel.id)
    kernel.runner.run_once()
    assert model.status()["state"] == "unloaded" and events.count("stop") == 1


def test_zero_releases_after_each_message_and_settings_changes_reload(kernel, warm_model):
    model, events, _ = warm_model
    kernel.settings["agent_keep_warm_seconds"] = 0
    for key in ("one", "two"):
        kernel.harness.post_message(kernel.id, "Report", key)
        assert kernel.runner.run_once()
        assert not model.active
    assert events.count("free") == events.count("stop") == 2
    settings = {"comfy_url": "http://fake", "assistant_context": 8192}
    model.begin(settings, lambda: False)
    model.call(settings, [], {}, lambda: False, kernel.db.root / "test.log")
    model.park()
    model.begin(settings | {"assistant_context": 16384}, lambda: False)
    assert events.count("free") == 4 and events.count("stop") == 3


def test_waiting_operation_releases_immediately(kernel, warm_model, monkeypatch):
    from gaiavfx.application.harness.session import AgentSession

    model, events, _ = warm_model
    kernel.harness.post_message(kernel.id, "Prepare", "one")

    def yield_operation(session_id):
        document = AgentSession.model_validate(kernel.harness.read(session_id)["document"])
        kernel.harness._save(session_id, "waiting_operation", document)

    monkeypatch.setattr(kernel.harness, "advance", yield_operation)
    assert kernel.runner.run_once()
    assert not model.active and events == ["free", "stop"]


def test_cancel_during_real_lease_wait(kernel, warm_model):
    import threading

    from filelock import FileLock

    from gaiavfx.infrastructure.coordination import gpu_lock_path

    model, events, _ = warm_model
    entered = threading.Event()
    original = model._state

    def state(value, **fields):
        original(value, **fields)
        if value == "waiting_gpu" and not fields:  # callback after a lock timeout
            entered.set()

    model._state = state
    kernel.harness.post_message(kernel.id, "Report", "one")
    with FileLock(str(gpu_lock_path("http://fake"))):
        thread = threading.Thread(target=kernel.runner.run_once)
        thread.start()
        assert entered.wait(5)
        assert kernel.harness.payload(kernel.harness.read(kernel.id))["activity"] == "waiting_gpu"
        kernel.harness.request_cancel(kernel.id)
        thread.join(5)
        assert not thread.is_alive()
    row = kernel.harness.read(kernel.id)
    assert row["state"] == "stopped" and row["document"]["stop_reason"] == "cancelled by the user"
    assert "call" not in events and not model.active


def test_operation_thread_acquires_parked_models_real_lock(kernel, warm_model, monkeypatch):
    import threading

    from fake_comfy import FakeComfy
    from test_operations_runner import create, output, spec

    from gaiavfx.application.operations import OperationRunner
    from gaiavfx.infrastructure.coordination import gpu_lease
    from gaiavfx.infrastructure.media import MediaTools
    from gaiavfx.modules.library.media_application import MediaLibrary

    model, events, _ = warm_model
    kernel.harness.post_message(kernel.id, "Report", "one")
    kernel.runner.run_once()
    fake = FakeComfy(output)
    operations = OperationRunner(
        kernel.db, fake, MediaLibrary(kernel.db, MediaTools()), "http://fake", poll_seconds=0
    )
    kernel.runner.gpu_wanted = operations.jobs_waiting_for_gpu
    waiting = threading.Event()
    monkeypatch.setattr(
        "gaiavfx.application.operations.gpu_lease",
        lambda endpoint, cancelled: gpu_lease(endpoint, cancelled, waiting=waiting.set),
    )
    create(kernel.db, [spec()])
    thread = threading.Thread(target=operations.run_once)
    thread.start()
    try:
        assert waiting.wait(5)
        assert operations.jobs_waiting_for_gpu() == 1
        assert not fake.posts
        assert not kernel.runner.run_once()
        thread.join(15)
        assert not thread.is_alive()
        assert len(fake.posts) == 1 and not model.active and events[-1] == "stop"
    finally:
        operations.stop.set()
        thread.join(5)


def test_two_drivers_claim_only_once_and_cancel_before_begin(kernel):
    from gaiavfx.application.harness.driver import AgentRunner

    k = kernel
    k.model.replies = iter([action("say", text="Done")])
    k.harness.post_message(k.id, "Report", "one")
    other = AgentRunner(k.db, k.harness)
    assert k.runner.run_once()
    assert not other.run_once()
    assert len(k.model.calls) == 1
    k.harness.post_message(k.id, "Report", "two")
    k.harness.request_cancel(k.id)
    assert other.run_once()
    assert k.harness.read(k.id)["state"] == "stopped"
    assert k.model.begins == 1


def test_model_local_lease_spans_message_and_remote_uses_none(monkeypatch, tmp_path):
    import gaiavfx.application.harness.model as module

    events = []

    @contextmanager
    def lease(endpoint, cancelled, waiting):
        assert not cancelled()
        events.append("lease")
        try:
            yield
        finally:
            events.append("release")

    def call(settings, content, cancellation, log_path, **kwargs):
        assert content is None and kwargs["keep_alive"] and settings["assistant_phase"] == "agent"
        assert not cancellation.is_set()
        assert not cancellation.wait(0)
        events.append("call")
        return {
            **action("say", text="Ready"),
            "metrics": {"usage": {"prompt_tokens": 10, "completion_tokens": 4}},
        }

    gateway = SimpleNamespace(run=call, stop=lambda: events.append("stop"))
    comfy = SimpleNamespace(request=lambda *args, **kw: events.append((args, kw)))
    monkeypatch.setattr(module, "gpu_lease", lease)
    monkeypatch.setattr(module, "resource_identity", lambda settings: {})
    monkeypatch.setattr(
        module, "connection_for", lambda settings, phase: {"provider": settings["provider"]}
    )
    model = AssistantModel(gateway, comfy)
    settings = {"provider": "managed_local", "comfy_url": "http://fake"}
    model.begin(settings, lambda: False)
    reply = model.call(settings, [], {}, lambda: False, tmp_path / "model.log")
    model.call(settings, [], {}, lambda: False, tmp_path / "model.log")
    assert "release" not in events and events.count("lease") == 1
    model.end()
    assert events[-2:] == ["stop", "release"]
    assert reply.input_tokens == 10 and reply.output_tokens == 4
    assert events[1] == (
        ("http://fake", "POST", "/free"),
        {"json": {"unload_models": True, "free_memory": True}},
    )
    events.clear()
    model.begin({"provider": "remote"}, lambda: False)
    model.end()
    assert events == []


def test_a_dead_comfy_free_keeps_the_lease_and_the_model_starts(monkeypatch, caplog):
    """A stopped ComfyUI (a copy's dead port, 23 Oct) is tolerated as an engine job's /free is; end still releases."""
    import logging

    import gaiavfx.application.harness.model as module

    events = []

    @contextmanager
    def lease(*args, **kwargs):
        events.append("lease")
        try:
            yield
        finally:
            events.append("release")

    def refused(*args, **kwargs):
        events.append("free")
        raise ConnectionRefusedError("[WinError 10061] No connection could be made")

    monkeypatch.setattr(module, "gpu_lease", lease)
    monkeypatch.setattr(module, "resource_identity", lambda settings: {})
    model = AssistantModel(
        SimpleNamespace(stop=lambda: events.append("stop")), SimpleNamespace(request=refused)
    )
    try:
        with caplog.at_level(logging.INFO, logger=module.__name__):
            model.begin({"comfy_url": "http://127.0.0.1:9"}, lambda: False)
        assert events == ["lease", "free"]
        assert model.active and model.status()["state"] == "loading"
        assert "ComfyUI did not answer /free at http://127.0.0.1:9" in caplog.text
    finally:
        model.end()
    assert events == ["lease", "free", "stop", "release"]
    assert model.status()["state"] == "unloaded"


def test_local_cancellation_wait_responds_without_a_watcher_thread():
    from gaiavfx.application.harness.model import Cancellation

    assert Cancellation(lambda: True).wait(10)
    assert not Cancellation(lambda: False).wait(0)


@pytest.mark.parametrize(
    ("kind", "wait_state"), [("removal", "stopped"), ("insert", "waiting_human")]
)
def test_user_plate_and_pin_review_resumes_only_executing_plan(
    tmp_path, monkeypatch, kind, wait_state
):
    from harness_eval import ScriptedModel, drive
    from harness_eval_scripts import SAY, STEP_DONE, candidates, plate, prepare
    from harness_eval_tasks import close, dz_library

    from gaiavfx.application.harness.driver import AgentRunner
    from gaiavfx.application.harness.loop import Harness
    from gaiavfx.application.prepare_references import approve_pin, approve_plate
    from gaiavfx.modules.agent.service import AgentStore
    from gaiavfx.modules.direction.service import DirectionService
    from gaiavfx.modules.generation.operations.review import ReviewService

    work, setup = dz_library(tmp_path, plate=kind != "removal", autonomy="off", budget=0)
    try:
        if kind == "insert":
            with work.db.transaction() as session:
                for angle in setup["angles"]:
                    evidence = DirectionService().record_evidence(
                        session, angle, "imported", setup["plate"]
                    )
                    DirectionService().approve_evidence(session, evidence["id"])
        model = ScriptedModel(
            [
                action(
                    "propose_plan",
                    goal="Prepare references",
                    steps=[
                        {
                            "kind": "prepare",
                            "title": "Prepare plate" if kind == "removal" else "Insert pin",
                            "target": setup["cut_id"],
                            "jobs": 2,
                        },
                    ],
                ),
                plate if kind == "removal" else prepare,
                candidates,
                SAY,
                STEP_DONE,
                action(
                    "plan_step",
                    step=0,
                    status="skipped",
                    note="Requested reference reviewed; remaining cut preparation is outside this limited plan.",
                ),
                SAY,
            ],
            work,
            setup,
        )
        harness = Harness(work.db, AgentStore(), model, work.settings)
        runner = AgentRunner(work.db, harness)
        row = harness.open(setup["movie_id"])
        setup["session_id"] = row["id"]
        harness.post_message(row["id"], "Prepare references", "message")
        assert runner.run_once()
        harness.approve_plan(row["id"], harness.read(row["id"])["revision"])
        drive(work, setup, harness, runner)
        row = harness.read(row["id"])
        plan = row["document"]["plan"]
        assert plan["steps"][0]["status"] == "awaiting_user", row
        assert not runner.run_once()
        with work.db.transaction() as session:
            harness.store.update_session(
                session, row["id"], row["revision"], state=wait_state, document=row["document"]
            )
            review = ReviewService().blind(session, plan["steps"][0]["review_of"])
            (approve_plate if kind == "removal" else approve_pin)(
                session, candidate_id=review["members"][0]["candidate_id"]
            )
        # A superseded plan never resumes, even when its review has been completed.
        row = harness.read(row["id"])
        with work.db.transaction() as session:
            doc = row["document"]
            doc["plan"]["state"] = "superseded"
            harness.store.update_session(
                session, row["id"], row["revision"], state=wait_state, document=doc
            )
        assert not runner.run_once()
        row = harness.read(row["id"])
        with work.db.transaction() as session:
            doc = row["document"]
            doc["plan"]["state"] = "executing"
            harness.store.update_session(
                session, row["id"], row["revision"], state=wait_state, document=doc
            )
        from gaiavfx.shared.errors import ApplicationError

        original = harness.store.update_session

        def stale_once(*args, **kwargs):
            monkeypatch.setattr(harness.store, "update_session", original)
            raise ApplicationError("Agent session changed.", 409)

        monkeypatch.setattr(harness.store, "update_session", stale_once)
        assert not runner.run_once()
        assert harness.read(row["id"])["document"]["plan"]["steps"][0]["status"] == "awaiting_user"
        assert not any(
            t["action"].get("message", "").startswith("The user reviewed")
            for t in harness.turns(row["id"])
            if t["role"] == "system"
        )
        assert runner.run_once()
        final = harness.read(row["id"])
        assert final["state"] == "idle" and final["document"]["plan"]["state"] == "done"
        assert final["document"]["plan"]["steps"][0]["status"] == "skipped"
        assert final["document"]["plan"]["grant"] == plan["grant"]
        assert any(
            (t.get("result") or {}).get("discrepancy", "").startswith("preparation_incomplete:")
            for t in harness.turns(row["id"])
        )
        assert any(
            t["action"].get("message", "").startswith(f"The user reviewed {kind}")
            for t in harness.turns(row["id"])
            if t["role"] == "system"
        )
    finally:
        close(work)


@pytest.mark.parametrize("decision", ["rank", "select", "early_rank"])
def test_either_take_ranking_or_user_selection_resumes_and_continue_wins(tmp_path, decision):
    from harness_eval import ScriptedModel, drive
    from harness_eval_scripts import SAY, STEP_DONE, candidates, one_render, smaller_render_plan
    from harness_eval_tasks import TASKS, close, run_idle

    from gaiavfx.application.candidate_review import select_take
    from gaiavfx.application.harness.driver import AgentRunner
    from gaiavfx.application.harness.loop import Harness
    from gaiavfx.application.render_cut import open_takes_review
    from gaiavfx.modules.agent.service import AgentStore
    from gaiavfx.modules.generation.operations.review import ReviewService

    task = next(t for t in TASKS if t.id == "plan-revise-and-render")
    work, setup = task.build(tmp_path)
    try:
        model = ScriptedModel(
            [smaller_render_plan, one_render, candidates, STEP_DONE, SAY, SAY], work, setup
        )
        harness = Harness(work.db, AgentStore(), model, work.settings)
        runner = AgentRunner(work.db, harness)
        row = harness.open(setup["movie_id"])
        setup["session_id"] = row["id"]
        harness.post_message(row["id"], "Render one take", "first")
        runner.run_once()
        harness.approve_plan(row["id"], harness.read(row["id"])["revision"])
        if decision == "early_rank":
            assert runner.run_once()
            operation_id = harness.read(row["id"])["document"]["waiting_on"]
            run_idle(work, setup["fake"])
            with work.db.transaction() as session:
                review = open_takes_review(session, operation_id)
                ReviewService().save_ranking(
                    session,
                    review["id"],
                    expected_revision=0,
                    tiers=[[review["members"][0]["candidate_id"]]],
                    usable_tiers=1,
                    notes={},
                    tags={},
                    general="",
                )
            assert runner.run_once()
        else:
            drive(work, setup, harness, runner)
        row = harness.read(row["id"])
        review_id = row["document"]["plan"]["steps"][1]["review_of"]
        assert review_id
        if decision != "early_rank":
            assert not runner.run_once()
        with work.db.transaction() as session:
            review = ReviewService().blind(session, review_id)
            candidate = review["members"][0]["candidate_id"]
            if decision == "rank":
                ReviewService().save_ranking(
                    session,
                    review_id,
                    expected_revision=0,
                    tiers=[[candidate]],
                    usable_tiers=1,
                    notes={},
                    tags={},
                    general="",
                )
            elif decision == "select":
                select_take(
                    session,
                    review_set_id=review_id,
                    candidate_id=candidate,
                    authority="provisional",
                    expected_revision=0,
                    basis="unjudged",
                )
        if decision == "select":
            assert not runner.run_once()  # The agent's provisional pick is never a review.
            with work.db.transaction() as session:
                select_take(
                    session,
                    review_set_id=review_id,
                    candidate_id=candidate,
                    authority="user",
                    expected_revision=1,
                )
            # A message arriving ahead of the polling driver wins without a duplicate resume.
            harness.post_message(row["id"], "Continue.", "continue")
            assert not harness.resume_reviewed(row["id"])
        assert runner.run_once()
        assert harness.read(row["id"])["document"]["plan"]["state"] == "done"
        markers = [
            t
            for t in harness.turns(row["id"])
            if t["role"] == "system"
            and t["action"].get("message", "").startswith("The user reviewed render")
        ]
        assert len(markers) == 1
        outcome = markers[0]["action"]["message"]
        if decision == "select":
            assert "selected A for segment 2" in outcome
        else:
            assert "ranked A" in outcome and "selection pending" in outcome
    finally:
        close(work)
