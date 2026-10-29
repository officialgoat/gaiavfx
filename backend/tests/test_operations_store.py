"""Durable Generation reservations, child transitions and review pauses."""

from dataclasses import replace
from pathlib import Path

import pytest
from sqlalchemy import func, select

from gaiavfx.infrastructure.database import Database
from gaiavfx.modules.generation.operations.lifecycle import JobSpec, settle
from gaiavfx.modules.generation.operations.persistence import (
    CandidateRecord,
    JobRecord,
    OperationRecord,
)
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService
from gaiavfx.shared.errors import ApplicationError


@pytest.fixture
def store(tmp_path):
    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Operations")
    db = Database(tmp_path)
    yield db, OperationService()
    db.close()


def comfy(seed=1, knowledge="line 1\nline 2\n"):
    return JobSpec(
        kind="video",
        executor="comfy",
        integration="fake",
        graph={"seed": seed},
        inputs={"asset": {"sha256": "abc", "remote_name": "asset.png"}},
        snapshot={"knowledge": {"pack.toml": knowledge}, "endpoint": "http://fake"},
        seed=seed,
    )


def local():
    return JobSpec(
        kind="image",
        executor="local",
        integration="fake",
        step="views_sheet",
        inputs={"tile": {"sha256": "def"}},
        snapshot={},
    )


def create(session, service, jobs=None, **kwargs):
    return service.create(
        session,
        kind="render",
        request={"cut": "c1"},
        jobs=jobs or [comfy()],
        budget_limit=kwargs.pop("budget_limit", None),
        **kwargs,
    )


def run_to_success(session, service, job):
    service.transition(session, job["id"], "submitting", submitted=True)
    service.transition(session, job["id"], "running", remote_id="remote")
    service.transition(session, job["id"], "collecting", outputs=[])
    service.transition(session, job["id"], "succeeded")


def test_create_reserves_atomically_and_freezes_knowledge(store):
    db, service = store
    text = "[render]\nname = 'é'\n"
    with db.transaction() as session:
        with pytest.raises(ApplicationError) as exhausted:
            create(session, service, [comfy(1, text), comfy(2), local()], budget_limit=1)
        assert exhausted.value.status == 409
        assert session.scalar(select(func.count()).select_from(OperationRecord)) == 0
        created = create(session, service, [comfy(1, text), comfy(2), local()], budget_limit=2)
        assert created["state"] == "queued" and created["budget_reserved"] == 2
        assert [job["ordinal"] for job in created["jobs"]] == [0, 1, 2]
    with db.transaction() as session:
        saved = service.operation(session, created["id"])
        assert saved["jobs"][0]["snapshot"]["knowledge"]["pack.toml"] == text
        assert len(saved["jobs"]) == 3


def test_create_rolls_back_parent_children_and_reservation_together(store):
    db, service = store
    with pytest.raises(RuntimeError), db.transaction() as session:
        create(session, service, [comfy(), local()])
        raise RuntimeError("abort caller transaction")
    with db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(OperationRecord)) == 0
        assert session.scalar(select(func.count()).select_from(JobRecord)) == 0
        assert create(session, service, budget_limit=1)["budget_reserved"] == 1


@pytest.mark.parametrize(
    "spec",
    [
        JobSpec(kind="video", executor="comfy", integration="fake", graph={"x": 1}, snapshot={}),
        JobSpec(kind="image", executor="local", integration="fake", step="sheet", graph={"x": 1}),
        # audio became a job kind with ADR 0015; text is still unknown
        JobSpec(kind="text", executor="local", integration="fake", step="sheet"),
    ],
)
def test_invalid_specs_leave_no_rows(store, spec):
    db, service = store
    with db.transaction() as session:
        with pytest.raises(ApplicationError) as error:
            create(session, service, [spec])
        assert error.value.status == 422
        assert session.scalar(select(func.count()).select_from(OperationRecord)) == 0


def test_claim_resume_precedence_and_invalid_transitions(store):
    db, service = store
    with db.transaction() as session:
        op = create(session, service, [comfy(1), comfy(2)])
        with pytest.raises(ApplicationError) as error:
            service.transition(session, op["jobs"][0]["id"], "running")
        assert error.value.status == 409
        first = service.claim_next(session)
        assert first["id"] == op["jobs"][0]["id"]
    with db.transaction() as session:
        second = service.claim_next(session)
        assert second["id"] == op["jobs"][1]["id"]
        service.transition(session, first["id"], "submitting", submitted=True)
    with db.transaction() as session:
        assert service.claim_next(session)["id"] == first["id"]
        service.transition(session, first["id"], "running", remote_id="remote")
        service.transition(session, first["id"], "collecting")
        service.transition(session, first["id"], "succeeded")
        with pytest.raises(ApplicationError) as error:
            service.transition(session, first["id"], "failed")
        assert error.value.status == 409


def test_settle_cancel_and_attention(store):
    db, service = store
    with db.transaction() as session:
        op = create(session, service, [comfy(1), comfy(2), local()])
        first = service.claim_next(session)
        service.transition(session, op["jobs"][1]["id"], "claimed")
        service.transition(session, first["id"], "submitting", submitted=True)
        service.transition(session, first["id"], "running", remote_id="remote")
        cancelled = service.request_cancel(session, op["id"])
        assert cancelled["state"] == "running"
        assert [j["state"] for j in cancelled["jobs"]] == ["running", "cancelled", "cancelled"]
        assert op["jobs"][1]["id"] == cancelled["jobs"][1]["id"]
        service.transition(session, first["id"], "cancelled")
        finished = service.operation(session, op["id"])
        assert finished["state"] == "cancelled" and finished["budget_released_at"]
    assert settle(["failed", "needs_attention"], False) == "needs_attention"


def test_attention_beats_failure_and_releases_budget(store):
    db, service = store
    with db.transaction() as session:
        op = create(session, service, [comfy(1), comfy(2)])
        service.transition(session, op["jobs"][0]["id"], "claimed")
        service.transition(session, op["jobs"][0]["id"], "failed", error="bad graph")
        service.transition(session, op["jobs"][1]["id"], "claimed")
        service.transition(session, op["jobs"][1]["id"], "submitting", submitted=True)
        service.transition(session, op["jobs"][1]["id"], "needs_attention")
        result = service.operation(session, op["id"])
        assert result["state"] == "needs_attention" and result["budget_released_at"]


def test_review_resume_budget_partial_and_finish(store):
    db, service = store
    with db.transaction() as session:
        op = create(session, service, awaits_review=True, budget=3)
        job = service.claim_next(session)
        run_to_success(session, service, job)
        waiting = service.operation(session, op["id"])
        assert waiting["state"] == "awaiting_review" and waiting["budget_released_at"] is None
        with pytest.raises(ApplicationError) as error:
            service.resume(session, op["id"], {"pick": 1}, [comfy(2), comfy(3), comfy(4)])
        assert error.value.status == 409
        resumed = service.resume(session, op["id"], {"pick": 1}, [comfy(2), comfy(3)])
        assert resumed["state"] == "queued" and resumed["review"] == [{"pick": 1}]
        assert [j["ordinal"] for j in resumed["jobs"]] == [0, 1, 2]
        second = service.claim_next(session)
        run_to_success(session, service, second)
        third = service.claim_next(session)
        service.transition(session, third["id"], "failed", error="bad graph")
        partial = service.operation(session, op["id"])
        assert partial["state"] == "partial" and partial["budget_released_at"]
        assert third["id"] in partial["error"]
        with pytest.raises(ApplicationError) as error:
            service.resume(session, op["id"], {})
        assert error.value.status == 409


def test_a_job_retried_in_place_no_longer_decides_the_operation(store):
    db, service = store
    with db.transaction() as session:
        op = create(session, service, [comfy(1), comfy(2)], awaits_review=True, budget=3)
        run_to_success(session, service, service.claim_next(session))
        stopped = service.claim_next(session)
        service.transition(session, stopped["id"], "failed", error="out of memory")
        assert service.operation(session, op["id"])["state"] == "awaiting_review"
        retry = comfy(2)
        retry = replace(retry, snapshot={**retry.snapshot, "retry_of": stopped["id"]})
        service.resume(session, op["id"], {"retry": True}, [retry], awaits_review=True)
        run_to_success(session, service, service.claim_next(session))
        assert service.operation(session, op["id"])["state"] == "awaiting_review"
        done = service.resume(session, op["id"], {"stage": "done"})
        # The voice build's retry in place (P5): the failed original stays failed, the operation succeeded.
        assert done["state"] == "succeeded" and done["error"] is None
        assert [j["state"] for j in done["jobs"]] == ["succeeded", "failed", "succeeded"]


def test_resume_without_jobs_and_cancel_during_review(store):
    db, service = store
    with db.transaction() as session:
        first = create(session, service, awaits_review=True)
        second = create(session, service, awaits_review=True)
        for op in (first, second):
            service.transition(session, op["jobs"][0]["id"], "claimed")
            run_to_success(session, service, op["jobs"][0])
        done = service.resume(session, first["id"], {"accepted": True})
        cancelled = service.request_cancel(session, second["id"])
        assert done["state"] == "succeeded" and done["budget_released_at"]
        assert cancelled["state"] == "cancelled" and cancelled["budget_released_at"]


def test_candidates_are_idempotent_and_unselected(store):
    db, service = store
    with db.transaction() as session:
        op = create(session, service)
        job_id = op["jobs"][0]["id"]
        first = service.add_candidate(session, job_id, "asset-1", "node:0", "video")
        same = service.add_candidate(session, job_id, "asset-1", "node:0", "video")
        assert same["id"] == first["id"]
        assert len(service.candidates(session, op["id"])) == 1
        assert "selected" not in first and "review" not in first and "rank" not in first
        assert session.scalar(select(func.count()).select_from(CandidateRecord)) == 1
        assert session.scalar(select(func.count()).select_from(JobRecord)) == 1


def test_dependency_gate_and_terminal_propagation(store):
    db, service = store
    with db.transaction() as session:
        op = create(session, service, [comfy(), replace(local(), depends_on=0)])
        first, second = op["jobs"]
        assert service.claim_next(session)["id"] == first["id"]
        assert service.claim_next(session) is None
        run_to_success(session, service, first)
        assert service.claim_next(session)["id"] == second["id"]
        failed = create(session, service, [comfy(), replace(local(), depends_on=0)])
        head = failed["jobs"][0]
        service.transition(session, head["id"], "claimed")
        service.transition(session, head["id"], "failed", error="bad input")
        service.claim_next(session)
        saved = service.operation(session, failed["id"])
        assert saved["state"] == "failed"
        assert saved["jobs"][1]["state"] == "failed"
        assert (
            f"Input job {head['id']} ended failed; this job did not run."
            == saved["jobs"][1]["error"]
        )
        cancelled = create(session, service, [comfy(), replace(local(), depends_on=0)])
        service.request_cancel(session, cancelled["id"])
        assert service.operation(session, cancelled["id"])["jobs"][1]["state"] == "cancelled"


def test_invalid_dependencies_and_job_output_leave_no_rows(store):
    db, service = store
    with db.transaction() as session:
        before = session.scalar(select(func.count()).select_from(OperationRecord))
        for specs in (
            [replace(comfy(), depends_on=0)],
            [comfy(), replace(local(), depends_on=1)],
            [replace(local(), inputs={"source_video": {"source": "job_output"}})],
        ):
            with pytest.raises(ApplicationError) as error:
                create(session, service, specs)
            assert error.value.status == 422
        assert session.scalar(select(func.count()).select_from(OperationRecord)) == before


def test_candidate_artifact_link_is_idempotent(store):
    db, service = store
    with db.transaction() as session:
        op = create(session, service)
        candidate = service.add_candidate(session, op["jobs"][0]["id"], "asset", "video", "video")
        assert (
            service.link_artifact(session, candidate["id"], "artifact-a")["artifact_id"]
            == "artifact-a"
        )
        assert (
            service.link_artifact(session, candidate["id"], "artifact-a")["artifact_id"]
            == "artifact-a"
        )
        with pytest.raises(ApplicationError) as error:
            service.link_artifact(session, candidate["id"], "artifact-b")
        assert error.value.status == 409
