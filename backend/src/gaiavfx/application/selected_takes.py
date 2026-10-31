"""Compose Editing's selections with whether each take is still current (application/take_basis.py)."""

from gaiavfx.application import take_basis
from gaiavfx.modules.editing.selections.service import SelectionService


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
    reasons = take_basis.judge(session, segment_id)

    def annotated(row):
        changed = reasons(row["candidate_id"])
        return {**row, "stale": bool(changed), "stale_reasons": changed}

    return annotated
