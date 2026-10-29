"""Cross-module candidate review and explicit Editing take selection."""

from sqlalchemy.orm import Session

from gaiavfx.infrastructure.database import timestamp
from gaiavfx.modules.editing.selections.service import SelectionService
from gaiavfx.modules.generation.operations.lifecycle import JOB_TERMINAL
from gaiavfx.modules.generation.operations.prescreen import (
    BLACK_THRESHOLDS,
    FREEZE_THRESHOLDS,
    VERSION,
    evaluate,
)
from gaiavfx.modules.generation.operations.review import ReviewService, failure_tags
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError


def _candidate_ids(session: Session, operation_ids: list[str]) -> list[str]:
    if (
        not isinstance(operation_ids, list)
        or not operation_ids
        or any(not isinstance(item, str) or not item for item in operation_ids)
        or len(operation_ids) != len(set(operation_ids))
    ):
        raise ApplicationError("Distinct operation ids are required.", 422)
    operations = OperationService()
    return [
        candidate["id"]
        for operation_id in operation_ids
        for candidate in operations.candidates(session, operation_id)
    ]


def open_review(
    session: Session,
    *,
    purpose: str,
    operation_ids: list[str],
    context: dict,
    set_id: str | None = None,
) -> dict:
    return ReviewService().create(
        session,
        purpose=purpose,
        candidate_ids=_candidate_ids(session, operation_ids),
        context=context,
        set_id=set_id,
    )


def add_to_review(
    session: Session,
    review_set_id: str,
    *,
    operation_ids: list[str] | None = None,
    candidate_ids: list[str] | None = None,
    expected_revision: int,
) -> dict:
    if (operation_ids is None) == (candidate_ids is None):
        raise ApplicationError("Supply operations or candidates to add.", 422)
    ids = _candidate_ids(session, operation_ids) if operation_ids is not None else candidate_ids
    return ReviewService().add(session, review_set_id, ids, expected_revision=expected_revision)


def blind(session: Session, review_set_id: str) -> dict:
    return ReviewService().blind(session, review_set_id)


def reveal(session: Session, review_set_id: str) -> dict:
    return ReviewService().reveal(session, review_set_id)


def save_ranking(session: Session, review_set_id: str, **document) -> dict:
    return ReviewService().save_ranking(session, review_set_id, **document)


def select_take(
    session: Session,
    *,
    review_set_id: str,
    candidate_id: str,
    authority: str,
    expected_revision: int,
    basis: str | None = None,
) -> dict:
    """`expected_revision` is the segment's selection revision the caller saw (0 before any selection)."""
    review = ReviewService().blind(session, review_set_id)
    context = review["context"]
    if review["purpose"] != "segment_takes" or any(
        not isinstance(context.get(key), str) or not context[key]
        for key in ("movie_id", "segment_id")
    ):
        raise ApplicationError("Take selection needs a segment review context.", 422)
    member = next(
        (item for item in review["members"] if item["candidate_id"] == candidate_id), None
    )
    if member is None:
        raise ApplicationError("Candidate does not belong to this review set.", 422)
    editing = SelectionService()
    return editing.select(
        session,
        movie_id=context["movie_id"],
        segment_id=context["segment_id"],
        candidate_id=candidate_id,
        asset_id=member["asset_id"],
        authority=authority,
        basis=basis,
        expected_revision=expected_revision,
    )


def review_taxonomy() -> dict:
    return {"schema": 2, "tags": [{"id": key, **value} for key, value in failure_tags().items()]}


def measure_candidate(session_factory, library, candidate, expect) -> dict:
    """All media work happens outside the transaction, and cannot fail a collected job."""
    kind = candidate["kind"]
    try:
        with session_factory() as session:
            path = library.path(session, candidate["asset_id"])
        measured = (
            library.media.scan_video(
                path,
                fps=(expect or {}).get("fps"),
                black=BLACK_THRESHOLDS,
                frozen=FREEZE_THRESHOLDS,
            )
            if kind == "video"
            else library.media.probe(path)
        )
        checks = evaluate(kind, expect, measured)
    except Exception as exc:  # noqa: BLE001 - measurement must never fail collection
        checks = evaluate(kind, expect, {"error": str(exc)[:300]})
    return {"version": VERSION, "measured_at": timestamp(), "checks": checks}


def prescreen_candidate(session_factory, library, candidate_id) -> dict:
    operations = OperationService()
    with session_factory() as session:
        candidate = operations.candidate(session, candidate_id)
        operation = operations.operation(session, candidate["operation_id"])
        job = next(j for j in operation["jobs"] if j["id"] == candidate["job_id"])
        if job["state"] not in JOB_TERMINAL:
            raise ApplicationError(
                "Wait until the candidate's job is terminal before prescreening.", 409
            )
    document = measure_candidate(session_factory, library, candidate, job["snapshot"].get("expect"))
    with session_factory() as session:
        return operations.record_prescreen(session, candidate_id, document)
