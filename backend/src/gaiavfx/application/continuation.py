"""Choosing a continuing segment's handoff (docs/CONTINUATION.md).

The choice is a revision-checked Direction edit, so Undo and Redo go through its receipt. A choice the
integration's rule cannot render is refused with the reason; null returns a field to the rule's default.
"""

from gaiavfx.application.direction_plans import (
    DEFAULT_INTEGRATION,
    INTEGRATIONS,
    continuation_facts,
)
from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.integrations.minimax_h3 import continuation as continuity
from gaiavfx.integrations.minimax_h3.continuation import ContinuationFacts
from gaiavfx.shared.errors import ApplicationError

FIELDS = {"method": "continuation_method", "context_frames": "context_frames"}


def context_lengths(integration=DEFAULT_INTEGRATION):
    """Every context length the integration's grid offers; a join's own plan narrows it to its source take."""
    return INTEGRATIONS[integration].capabilities().frame_grid.context_lengths()


def require_new_choice(*, continues, method, context_frames, integration=DEFAULT_INTEGRATION):
    """A segment being added has no cuts, pins or selected take yet: its plan checks those once it has them."""
    choice = {
        key: value
        for key, value in (("method", method), ("context_frames", context_frames))
        if value is not None
    }
    if choice and (
        problem := continuity.choice_problem(
            ContinuationFacts(continues=continues, cuts=0, guides=0),
            INTEGRATIONS[integration].capabilities().frame_grid,
            method=method,
            context_frames=context_frames,
            changing=frozenset(choice),
        )
    ):
        raise ApplicationError(problem, 422)


def set_continuation(
    session, segment_id, expected_revision, *, integration=DEFAULT_INTEGRATION, **choice
):
    """`choice` holds `method`, `context_frames` or both."""
    if not choice or set(choice) - set(FIELDS):
        raise ApplicationError("Choose a continuation method or a context length.", 422)
    if type(expected_revision) is not int or expected_revision < 1:
        raise ApplicationError("expected_revision must be a positive integer.", 422)
    row, facts = continuation_facts(session, segment_id, integration)
    if row["revision"] != expected_revision:
        raise ApplicationError("Segment revision is stale.", 409)
    if problem := continuity.choice_problem(
        facts,
        INTEGRATIONS[integration].capabilities().frame_grid,
        method=choice.get("method", row["continuation_method"]),
        context_frames=choice.get("context_frames", row["context_frames"]),
        changing=frozenset(choice),
    ):
        raise ApplicationError(problem, 422)
    return DIRECTION.update_segment(
        session,
        segment_id,
        expected_revision,
        **{FIELDS[key]: value for key, value in choice.items()},
    )
