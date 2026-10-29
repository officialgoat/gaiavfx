"""Session-taking use cases for durable Generation operations."""

from __future__ import annotations

import json
from copy import deepcopy
from datetime import datetime

from sqlalchemy import and_, func, insert, or_, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from gaiavfx.infrastructure.database import new_id, timestamp
from gaiavfx.modules.generation.operations.lifecycle import (
    JOB_TERMINAL,
    JOB_TRANSITIONS,
    OPERATION_TERMINAL,
    JobSpec,
    settle,
)
from gaiavfx.modules.generation.operations.persistence import (
    CandidateRecord,
    JobRecord,
    OperationRecord,
)
from gaiavfx.modules.generation.operations.prescreen import CHECKS, VERDICTS
from gaiavfx.shared.errors import ApplicationError


def _record(row) -> dict:
    return {column.key: deepcopy(getattr(row, column.key)) for column in row.__table__.columns}


def _get(session: Session, model, record_id: str):
    row = session.get(model, record_id)
    if row is None:
        raise ApplicationError(f"{model.__tablename__} record {record_id} was not found.", 404)
    return row


def _validate(spec: JobSpec) -> None:
    if not isinstance(spec, JobSpec):
        raise ApplicationError("A job must be a JobSpec.", 422)
    if spec.kind not in {"image", "video", "analysis", "audio"}:
        raise ApplicationError("Unknown output kind.", 422)
    if not isinstance(spec.integration, str) or not spec.integration or len(spec.integration) > 40:
        raise ApplicationError("Job integration is required and limited to 40 characters.", 422)
    if not isinstance(spec.inputs, dict):
        raise ApplicationError("Every input needs an asset id and sha256.", 422)
    for input_key, value in spec.inputs.items():
        if not isinstance(input_key, str) or not input_key or not isinstance(value, dict):
            raise ApplicationError("Every input needs an asset id and sha256.", 422)
        if value.get("source") in {"job_output", "job_artifact"}:
            if spec.depends_on is None or (
                "output_key" in value
                and (not isinstance(value["output_key"], str) or not value["output_key"])
            ):
                raise ApplicationError(
                    "A job output input needs a dependency and a valid output key.", 422
                )
            if value.get("source") == "job_artifact" and not value.get("output_key"):
                raise ApplicationError("A job artifact needs an output key.", 422)
        elif not isinstance(value.get("sha256"), str) or not value["sha256"]:
            raise ApplicationError("Every input needs an asset id and sha256.", 422)
        if value.get("source") == "artifact" and value.get("directory") not in {"input", "output"}:
            raise ApplicationError("Artifact inputs need an input or output directory.", 422)
    if not isinstance(spec.snapshot, dict):
        raise ApplicationError("A job snapshot must be an object.", 422)
    if spec.seed is not None and type(spec.seed) is not int:
        raise ApplicationError("A seed must be an integer.", 422)
    if spec.executor == "comfy":
        knowledge = spec.snapshot.get("knowledge")
        if (
            not isinstance(spec.graph, dict)
            or not spec.graph
            or spec.step is not None
            or not isinstance(knowledge, dict)
            or not knowledge
            or any(not isinstance(k, str) or not isinstance(v, str) for k, v in knowledge.items())
        ):
            raise ApplicationError("Comfy jobs need a graph and non-empty knowledge text.", 422)
    elif spec.executor == "local":
        if not isinstance(spec.step, str) or not spec.step or spec.graph is not None:
            raise ApplicationError("Local jobs need a step and no graph.", 422)
    elif spec.executor == "engine":
        # ADR 0015: one registered engine task; its GPU need is frozen in the snapshot.
        engine = spec.snapshot.get("engine")
        if (
            not isinstance(spec.step, str)
            or "." not in spec.step.strip(".")
            or spec.graph is not None
            or not isinstance(engine, dict)
            or engine.get("step") != spec.step
            or type(engine.get("gpu")) is not bool
            or not isinstance(engine.get("vram_gb"), (int, float))
        ):
            raise ApplicationError(
                "Engine jobs need an engine task step, its engine snapshot and no graph.", 422
            )
    else:
        raise ApplicationError("Unknown job executor.", 422)


def _insert_jobs(session: Session, operation_id: str, jobs: list[JobSpec], start: int) -> None:
    for ordinal, spec in enumerate(jobs, start):
        session.add(
            JobRecord(
                id=new_id(),
                operation_id=operation_id,
                ordinal=ordinal,
                depends_on=spec.depends_on,
                kind=spec.kind,
                executor=spec.executor,
                integration=spec.integration,
                step=spec.step,
                graph=deepcopy(spec.graph),
                inputs=deepcopy(spec.inputs),
                seed=spec.seed,
                snapshot=deepcopy(spec.snapshot),
                state="queued",
                submitted=False,
                outputs=[],
                created_at=timestamp(),
                updated_at=timestamp(),
            )
        )
    session.flush()


class OperationService:
    def jobs_waiting_for_gpu(self, session: Session) -> int:
        """Comfy jobs and GPU engine tasks before the lease, including resumable submitted work.

        Queued dependencies are included conservatively; local, CPU engine and
        collecting jobs need no GPU. Submitted cancellations still need the lease to reconcile.
        """
        return session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .join(OperationRecord, JobRecord.operation_id == OperationRecord.id)
            .where(
                or_(
                    JobRecord.executor == "comfy",
                    and_(
                        JobRecord.executor == "engine",
                        JobRecord.snapshot["engine"]["gpu"].as_boolean().is_(True),
                    ),
                ),
                JobRecord.state.in_(("queued", "claimed", "submitting", "running")),
                or_(OperationRecord.cancel_requested.is_(False), JobRecord.submitted.is_(True)),
            )
        )

    def create(
        self,
        session: Session,
        *,
        kind: str,
        request: dict,
        jobs: list[JobSpec],
        budget_limit: int | None,
        awaits_review: bool = False,
        budget: int | None = None,
        request_key: str | None = None,
    ) -> dict:
        """Create once per key; `_created` reports this call's outcome, never persisted."""
        if not isinstance(kind, str) or not kind or len(kind) > 40:
            raise ApplicationError("Operation kind is required and limited to 40 characters.", 422)
        if not isinstance(request, dict) or not isinstance(jobs, list) or not jobs:
            raise ApplicationError("An operation needs a request and jobs.", 422)
        if request_key is not None and (
            not isinstance(request_key, str) or not request_key or len(request_key) > 80
        ):
            raise ApplicationError("Request key must contain 1 to 80 characters.", 422)
        # Compare the persisted JSON shape (authored dataclasses can contain tuples).
        request = json.loads(json.dumps(request))
        if request_key is not None:
            existing = self._replay(session, request_key, kind, request)
            if existing is not None:
                return existing
        if type(awaits_review) is not bool:
            raise ApplicationError("awaits_review must be a boolean.", 422)
        for ordinal, spec in enumerate(jobs):
            _validate(spec)
            if spec.depends_on is not None and (
                type(spec.depends_on) is not int or not 0 <= spec.depends_on < ordinal
            ):
                raise ApplicationError("Job dependency must name an earlier ordinal.", 422)
        required = sum(
            spec.executor == "comfy" and spec.kind in {"image", "video"} for spec in jobs
        )
        reserved = required if budget is None else budget
        if type(reserved) is not int or reserved < required or reserved < 0:
            raise ApplicationError("Budget must cover the ComfyUI jobs.", 422)
        if budget_limit is not None:
            if type(budget_limit) is not int or budget_limit < 0:
                raise ApplicationError("Budget limit must be nonnegative.", 422)
            held = session.scalar(
                select(func.coalesce(func.sum(OperationRecord.budget_reserved), 0)).where(
                    OperationRecord.budget_released_at.is_(None)
                )
            )
            if held + reserved > budget_limit:
                raise ApplicationError("Operation budget is exhausted.", 409)
        now = timestamp()
        operation_id = new_id()
        values = {
            "id": operation_id,
            "kind": kind,
            "state": "queued",
            "awaits_review": awaits_review,
            "request": deepcopy(request),
            "request_key": request_key,
            "budget_reserved": reserved,
            "cancel_requested": False,
            "created_at": now,
            "updated_at": now,
        }
        try:
            session.execute(insert(OperationRecord).values(**values))
        except IntegrityError:
            # SQLite rolls back the failed statement, leaving the caller's
            # transaction usable. A Core insert avoids a failed ORM flush.
            if request_key is not None:
                existing = self._replay(session, request_key, kind, request)
                if existing is not None:
                    return existing
            raise
        _insert_jobs(session, operation_id, jobs, 0)
        return self.operation(session, operation_id) | {"_created": True}

    def _replay(self, session, request_key, kind, request):
        row = session.scalar(
            select(OperationRecord).where(OperationRecord.request_key == request_key)
        )
        if row is None:
            return None
        if row.kind != kind or row.request != request:
            raise ApplicationError(
                "Request key already belongs to a different operation request.", 409
            )
        return self.operation(session, row.id) | {"_created": False}

    def claim_next(self, session: Session) -> dict | None:
        resumed = session.scalars(
            select(JobRecord)
            .where(JobRecord.state.in_(("submitting", "running", "collecting")))
            .order_by(JobRecord.created_at, JobRecord.ordinal, JobRecord.id)
        ).first()
        if resumed is not None:
            return _record(resumed)
        while True:
            rows = session.scalars(
                select(JobRecord)
                .where(JobRecord.state == "queued")
                .order_by(JobRecord.created_at, JobRecord.ordinal, JobRecord.id)
            ).all()
            if not rows:
                return None
            changed = False
            for row in rows:
                parent = _get(session, OperationRecord, row.operation_id)
                if parent.cancel_requested:
                    self.transition(session, row.id, "cancelled")
                    changed = True
                    continue
                if row.depends_on is not None:
                    dependency = session.scalar(
                        select(JobRecord).where(
                            JobRecord.operation_id == row.operation_id,
                            JobRecord.ordinal == row.depends_on,
                        )
                    )
                    if dependency.state == "cancelled":
                        self.transition(session, row.id, "cancelled")
                        changed = True
                        continue
                    if dependency.state in {"failed", "needs_attention"}:
                        self.transition(
                            session,
                            row.id,
                            "failed",
                            error=f"Input job {dependency.id} ended {dependency.state}; this job did not run.",
                        )
                        changed = True
                        continue
                    if dependency.state != "succeeded":
                        continue
                result = session.execute(
                    update(JobRecord)
                    .where(JobRecord.id == row.id, JobRecord.state == "queued")
                    .values(state="claimed", updated_at=timestamp())
                    .execution_options(synchronize_session=False)
                )
                if result.rowcount != 1:
                    session.expire_all()
                    changed = True
                    break
                session.expire_all()
                self.settle_operation(session, row.operation_id)
                return _record(_get(session, JobRecord, row.id))
            if not changed:
                return None

    def local_progress(self, session: Session, job_id: str, progress: float) -> None:
        """Persist local execution progress in its mutable output manifest."""
        row = _get(session, JobRecord, job_id)
        if (
            row.executor not in {"local", "engine"}
            or row.state != "running"
            or not 0 <= progress < 1
        ):
            raise ApplicationError("Invalid local job progress.", 409)
        row.outputs = [{"progress": progress}]
        row.updated_at = timestamp()
        session.flush()

    def begin_local_completion(self, session: Session, job_id: str) -> bool:
        """Acquire the writer boundary before checking cancellation or publishing output.

        SQLite's legacy driver does not begin a transaction for SELECT alone.
        The write serializes publication against request_cancel's conditional write.
        """
        job = _get(session, JobRecord, job_id)
        if job.executor != "local" or job.state != "running":
            raise ApplicationError("Local job is not running.", 409)
        operation_id = job.operation_id
        session.execute(
            update(OperationRecord)
            .where(OperationRecord.id == operation_id)
            .values(updated_at=timestamp())
            .execution_options(synchronize_session=False)
        )
        session.expire_all()
        return not _get(session, OperationRecord, operation_id).cancel_requested

    def transition(self, session: Session, job_id: str, to: str, **fields) -> dict:
        row = _get(session, JobRecord, job_id)
        if to not in JOB_TRANSITIONS.get(row.state, ()):
            raise ApplicationError(f"Invalid job transition: {row.state} -> {to}.", 409)
        unknown = set(fields) - {"submitted", "endpoint", "remote_id", "outputs", "error"}
        if unknown:
            raise ApplicationError(f"Unknown job transition fields: {sorted(unknown)}.", 422)
        if "submitted" in fields and type(fields["submitted"]) is not bool:
            raise ApplicationError("submitted must be a boolean.", 422)
        if "outputs" in fields and not isinstance(fields["outputs"], list):
            raise ApplicationError("outputs must be a list.", 422)
        values = {key: deepcopy(value) for key, value in fields.items()}
        result = session.execute(
            update(JobRecord)
            .where(JobRecord.id == job_id, JobRecord.state == row.state)
            .values(state=to, updated_at=timestamp(), **values)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ApplicationError("Job state changed before the transition.", 409)
        session.expire_all()
        self.settle_operation(session, row.operation_id)
        return _record(_get(session, JobRecord, job_id))

    def settle_operation(self, session: Session, operation_id: str) -> dict:
        row = _get(session, OperationRecord, operation_id)
        children = session.scalars(
            select(JobRecord)
            .where(JobRecord.operation_id == operation_id)
            .order_by(JobRecord.ordinal)
        ).all()
        # A job that a later job retries in place (its snapshot's `retry_of`) no longer decides the parent.
        superseded = {(child.snapshot or {}).get("retry_of") for child in children}
        children = [child for child in children if child.id not in superseded]
        state = settle([child.state for child in children], row.cancel_requested, row.awaits_review)
        if state != row.state:
            row.state = state
            row.updated_at = timestamp()
        if state in OPERATION_TERMINAL and row.budget_released_at is None:
            row.budget_released_at = timestamp()
        if state in {"failed", "partial"}:
            failures = [
                f"{child.id}: {child.error or child.state}"
                for child in children
                if child.state == "failed"
            ]
            row.error = "; ".join(failures) or None
        session.flush()
        return _record(row)

    def request_cancel(self, session: Session, operation_id: str) -> dict:
        row = _get(session, OperationRecord, operation_id)
        if row.state in OPERATION_TERMINAL:
            return self.operation(session, operation_id)
        result = session.execute(
            update(OperationRecord)
            .where(
                OperationRecord.id == operation_id, OperationRecord.state.not_in(OPERATION_TERMINAL)
            )
            .values(cancel_requested=True, updated_at=timestamp())
            .execution_options(synchronize_session=False)
        )
        session.expire_all()
        if result.rowcount != 1:
            return self.operation(session, operation_id)
        session.execute(
            update(JobRecord)
            .where(
                JobRecord.operation_id == operation_id, JobRecord.state.in_(("queued", "claimed"))
            )
            .values(state="cancelled", updated_at=timestamp())
            .execution_options(synchronize_session=False)
        )
        session.expire_all()
        self.settle_operation(session, operation_id)
        return self.operation(session, operation_id)

    def resume(
        self,
        session: Session,
        operation_id: str,
        decision: dict,
        jobs: list[JobSpec] = (),
        awaits_review: bool = False,
    ) -> dict:
        row = _get(session, OperationRecord, operation_id)
        if row.state != "awaiting_review":
            raise ApplicationError("Operation is not awaiting review.", 409)
        if not isinstance(decision, dict) or type(awaits_review) is not bool:
            raise ApplicationError("Review decision and awaits_review are invalid.", 422)
        if row.cancel_requested:
            raise ApplicationError("Cancelled operations cannot advance.", 409)
        if decision.get("decided_by") == "advancer":
            required = {"decided_by", "round", "arm", "gaps", "reasons", "seeds"}
            if (
                row.kind != "character_replacement"
                or not required <= set(decision)
                or set(decision) - (required | {"attempts"})
                or not isinstance(decision.get("attempts", []), list)
                or len(decision.get("attempts", [])) > 3
                or any(
                    not isinstance(a, dict) or set(a) != {"arm", "takes"}
                    for a in decision.get("attempts", [])
                )
                or type(decision["round"]) is not int
                or not 0 <= decision["round"] <= 3
                or not isinstance(decision["gaps"], list)
                or not isinstance(decision["reasons"], list)
                or not all(isinstance(s, str) for s in decision["reasons"])
                or not isinstance(decision["seeds"], list)
                or any(type(s) is not int or not 0 <= s < 2**64 for s in decision["seeds"])
                or (decision["arm"] is not None and not isinstance(decision["arm"], dict))
            ):
                raise ApplicationError("Invalid advancer decision.", 422)
            if any(
                not isinstance(g, dict)
                or set(g) != {"code", "message"}
                or any(not isinstance(v, str) or not v for v in g.values())
                for g in decision["gaps"]
            ):
                raise ApplicationError("Invalid advancer gap records.", 422)
            render_count = sum(j.executor == "comfy" and j.kind in {"image", "video"} for j in jobs)
            if jobs and (
                not decision["arm"]
                or decision["arm"].get("seed_count") != render_count
                or len(decision["seeds"]) != render_count
            ):
                raise ApplicationError("Invalid advancer render/seed count.", 422)
            previous = [d for d in (row.review or []) if d.get("decided_by") == "advancer"]
            if decision["round"] != len(previous) + 1:
                raise ApplicationError("Advancer rounds must be contiguous and unique.", 409)
            if jobs and (
                decision["round"] >= min(3, row.request["policy_limits"]["max_rounds"])
                or decision["arm"] is None
                or decision["arm"].get("round") != decision["round"]
                or not awaits_review
            ):
                raise ApplicationError("The replacement round exceeds its frozen bounds.", 409)
            if not jobs and (awaits_review or decision["arm"] is not None or decision["seeds"]):
                raise ApplicationError("Closing the loop needs no arm, seeds or review gate.", 422)
            try:
                json.dumps(decision, allow_nan=False)
            except (TypeError, ValueError) as exc:
                raise ApplicationError("Invalid advancer JSON.", 422) from exc
        next_ordinal = (
            session.scalar(
                select(func.coalesce(func.max(JobRecord.ordinal), -1)).where(
                    JobRecord.operation_id == operation_id
                )
            )
            + 1
        )
        for ordinal, spec in enumerate(jobs, next_ordinal):
            _validate(spec)
            if spec.depends_on is not None and (
                type(spec.depends_on) is not int or not 0 <= spec.depends_on < ordinal
            ):
                raise ApplicationError("Job dependency must name an earlier ordinal.", 422)
        existing = session.scalar(
            select(func.count())
            .select_from(JobRecord)
            .where(
                JobRecord.operation_id == operation_id,
                JobRecord.executor == "comfy",
                JobRecord.kind.in_(("image", "video")),
            )
        )
        if (
            existing
            + sum(spec.executor == "comfy" and spec.kind in {"image", "video"} for spec in jobs)
            > row.budget_reserved
        ):
            raise ApplicationError("Operation reservation cannot cover these jobs.", 409)
        row.review = [*(row.review or []), deepcopy(decision)]
        row.awaits_review = awaits_review
        row.updated_at = timestamp()
        _insert_jobs(session, row.id, list(jobs), next_ordinal)
        self.settle_operation(session, operation_id)
        return self.operation(session, operation_id)

    def ready_to_advance(self, session, kinds):
        pending = (
            select(JobRecord.id)
            .where(
                JobRecord.operation_id == OperationRecord.id,
                JobRecord.state.not_in(JOB_TERMINAL),
            )
            .exists()
        )
        return list(
            session.scalars(
                select(OperationRecord.id).where(
                    OperationRecord.kind.in_(kinds),
                    OperationRecord.state == "awaiting_review",
                    OperationRecord.cancel_requested.is_(False),
                    ~pending,
                )
            )
        )

    def lock_for_advance(self, session, operation_id):
        session.execute(
            update(OperationRecord)
            .where(OperationRecord.id == operation_id)
            .values(updated_at=OperationRecord.updated_at)
        )
        session.expire_all()

    def record_advance_error(self, session, operation_id, error):
        row = _get(session, OperationRecord, operation_id)
        if row.state == "awaiting_review" and not row.cancel_requested:
            entry = {"decided_by": "advancer_error", "error": str(error)}
            if not row.review or row.review[-1] != entry:
                row.review = [*(row.review or []), entry]
                row.updated_at = timestamp()
                session.flush()

    def record_outputs(self, session, job_id, outputs):
        """Checkpoint collected artifacts/assets without changing the job state."""
        row = _get(session, JobRecord, job_id)
        if row.state != "collecting":
            raise ApplicationError("Only collecting jobs can checkpoint outputs.", 409)
        row.outputs = deepcopy(outputs)
        session.flush()

    def add_candidate(
        self, session: Session, job_id: str, asset_id: str, output_key: str, kind: str
    ) -> dict:
        job = _get(session, JobRecord, job_id)
        if (
            kind not in {"image", "video", "audio"}
            or kind != job.kind
            or not asset_id
            or not output_key
        ):
            raise ApplicationError("Candidate kind, asset id or output key is invalid.", 422)
        existing = session.scalars(
            select(CandidateRecord).where(
                CandidateRecord.job_id == job_id, CandidateRecord.output_key == output_key
            )
        ).first()
        if existing is not None:
            return _record(existing)
        row = CandidateRecord(
            id=new_id(),
            job_id=job_id,
            operation_id=job.operation_id,
            asset_id=asset_id,
            output_key=output_key,
            kind=kind,
            created_at=timestamp(),
        )
        session.add(row)
        session.flush()
        return _record(row)

    def record_prescreen(self, session: Session, candidate_id: str, document: dict) -> dict:
        row = _get(session, CandidateRecord, candidate_id)

        def safe(value):
            if isinstance(value, dict):
                return all(
                    isinstance(k, str)
                    and k
                    not in {
                        "job_id",
                        "seed",
                        "operation_id",
                        "integration",
                        "technique",
                        "graph",
                        "request",
                    }
                    and safe(v)
                    for k, v in value.items()
                )
            if isinstance(value, list):
                return all(safe(v) for v in value)
            return value is None or type(value) in {str, int, float, bool}

        valid = (
            isinstance(document, dict)
            and set(document) == {"version", "measured_at", "checks"}
            and type(document["version"]) is int
            and document["version"] > 0
            and isinstance(document["measured_at"], str)
            and bool(document["measured_at"])
            and isinstance(document["checks"], list)
        )
        if valid:
            try:
                valid = datetime.fromisoformat(document["measured_at"]).tzinfo is not None
            except ValueError:
                valid = False
        if valid:
            expected = {key for key, kinds in CHECKS.items() if row.kind in kinds}
            checks = document["checks"]
            valid = all(
                isinstance(c, dict)
                and set(c) == {"check", "verdict", "evidence"}
                and isinstance(c["check"], str)
                and c["check"] in expected
                and isinstance(c["verdict"], str)
                and c["verdict"] in VERDICTS
                and isinstance(c["evidence"], dict)
                and safe(c["evidence"])
                for c in checks
            )
            valid = (
                valid and len(checks) == len(expected) and {c["check"] for c in checks} == expected
            )
        try:
            json.dumps(document, allow_nan=False)
        except (TypeError, ValueError):
            valid = False
        if not valid:
            raise ApplicationError("Invalid candidate prescreen document.", 422)
        row.prescreen = deepcopy(document)
        session.flush()
        return deepcopy(row.prescreen)

    def link_artifact(self, session: Session, candidate_id: str, artifact_id: str) -> dict:
        row = _get(session, CandidateRecord, candidate_id)
        if not isinstance(artifact_id, str) or not artifact_id:
            raise ApplicationError("Artifact id is required.", 422)
        if row.artifact_id not in (None, artifact_id):
            raise ApplicationError("Candidate already has a different continuation artifact.", 409)
        row.artifact_id = artifact_id
        session.flush()
        return _record(row)

    def candidates(self, session: Session, operation_id: str) -> list[dict]:
        _get(session, OperationRecord, operation_id)
        return [
            _record(row)
            for row in session.scalars(
                select(CandidateRecord)
                .where(CandidateRecord.operation_id == operation_id)
                .order_by(CandidateRecord.created_at, CandidateRecord.id)
            )
        ]

    def candidate(self, session: Session, candidate_id: str) -> dict:
        return _record(_get(session, CandidateRecord, candidate_id))

    def candidates_for_asset(self, session: Session, asset_id: str) -> list[dict]:
        return [
            _record(row)
            for row in session.scalars(
                select(CandidateRecord).where(CandidateRecord.asset_id == asset_id)
            )
        ]

    def operation(self, session: Session, operation_id: str) -> dict:
        row = _get(session, OperationRecord, operation_id)
        result = _record(row)
        result["jobs"] = [
            _record(job)
            for job in session.scalars(
                select(JobRecord)
                .where(JobRecord.operation_id == operation_id)
                .order_by(JobRecord.ordinal)
            )
        ]
        result["candidates"] = self.candidates(session, operation_id)
        return result

    def request_snapshot(self, session: Session, operation_id: str) -> dict:
        """Read frozen request evidence without loading the operation's candidates."""
        return deepcopy(_get(session, OperationRecord, operation_id).request)

    def for_request(self, session: Session, request_key: str) -> dict | None:
        """Find a committed intent after its caller was interrupted before retaining the receipt."""
        row = session.scalar(
            select(OperationRecord).where(OperationRecord.request_key == request_key)
        )
        return self.operation(session, row.id) if row is not None else None

    def recent(
        self,
        session: Session,
        *,
        kind: str | None = None,
        state: str | None = None,
        limit: int = 50,
        scope: dict[str, list[str]] | None = None,
    ) -> list[dict]:
        query = select(OperationRecord)
        if kind:
            query = query.where(OperationRecord.kind == kind)
        if state:
            query = query.where(OperationRecord.state == state)
        if scope is not None:
            # Scope is plain request identity, not a join into Direction's tables.
            # Filter before LIMIT so another movie cannot hide an outstanding review.
            if set(scope) - {
                "cut_id",
                "place_id",
                "segment_id",
                "movie_id",
                "source_asset",
                "recast_id",
            }:
                raise ApplicationError("Unknown operation scope field.", 422)
            clauses = [
                OperationRecord.request[key].as_string().in_(values)
                for key, values in scope.items()
                if values
            ]
            if not clauses:
                return []
            query = query.where(or_(*clauses))
        query = query.order_by(OperationRecord.created_at.desc(), OperationRecord.id.desc()).limit(
            limit
        )
        return [self.operation(session, row.id) for row in session.scalars(query)]
