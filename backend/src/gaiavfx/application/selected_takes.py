"""Compose Editing's selection read through the public Direction and Generation readers."""

from gaiavfx.modules.direction.service import DirectionService, revision_restored
from gaiavfx.modules.editing.selections.service import SelectionService
from gaiavfx.modules.editing.selections.staleness import changed_since_take
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError


def selection_state(session, movie_id: str, segment_id: str) -> dict:
    selections = SelectionService()
    history = selections.history(session, movie_id, segment_id)
    current = selections.current(session, movie_id, segment_id)
    annotated = _annotator(session, movie_id, segment_id) if history else None
    return {
        "current": annotated(current) if current else None,
        "history": [annotated(row) for row in history],
        "revision": selections.revision(session, movie_id, segment_id),
    }


def current_take(session, movie_id: str, segment_id: str) -> dict | None:
    """The segment's current selection with the same staleness as `selection_state`."""
    current = SelectionService().current(session, movie_id, segment_id)
    return _annotator(session, movie_id, segment_id)(current) if current else None


def _annotator(session, movie_id: str, segment_id: str):
    direction = DirectionService()
    revisions = None
    try:
        segment = direction.read_segment(session, segment_id)
        subjects = {s["id"]: s["revision"] for s in direction.subjects(session, movie_id)}
        revisions = {
            "segment_revision": segment["revision"],
            "phase_revisions": {p["id"]: p["revision"] for p in segment["phases"]},
            "subject_revisions": {
                identity: subjects.get(identity) for identity in segment["content"]["subjects"]
            },
        }
    except ApplicationError as exc:
        if exc.status != 404:
            raise
    operations = OperationService()

    def annotated(row):
        candidate = operations.candidate(session, row["candidate_id"])
        operation = operations.operation(session, candidate["operation_id"])
        request = operation["request"]
        reasons = changed_since_take(
            request,
            revisions,
            segment_id=segment_id,
            unchanged=lambda kind, identity, now, then: revision_restored(
                session, kind, identity, now, then
            ),
        )
        if revisions is not None:
            inputs = {
                asset_id
                for job in operation["jobs"]
                if job["id"] == candidate["job_id"]
                for asset_id, value in job["inputs"].items()
                if "/keyframe_" in value.get("remote_name", "")
            }
            for cut in segment["cuts"]:
                pins = direction.keyframes(session, cut["id"])
                current_assets = {pin["asset_id"] for pin in pins if pin["approved"]}
                if any(
                    pin["approved_at"] and pin["asset_id"] in inputs - current_assets
                    for pin in pins
                ):
                    reasons.append("Pin replaced since this take.")
                    break
        return {**row, "stale": bool(reasons), "stale_reasons": reasons}

    return annotated
