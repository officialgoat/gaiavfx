"""A person approves one candidate, by the kind of operation that made it: a plate, an angle view, a pin or a
replacement composite. Only the person's routes call this: Gaia never approves (test_boundaries keeps the harness
from importing it)."""

from __future__ import annotations

from gaiavfx.application.prepare_references import (
    VIEW_PRODUCERS,
    approve_pin,
    approve_plate,
    approve_view,
)
from gaiavfx.application.replace_character import approve_replacement
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

APPROVERS = {
    "removal": lambda session, candidate_id, replace: approve_plate(
        session, candidate_id=candidate_id
    ),
    **{
        kind: lambda session, candidate_id, replace: approve_view(
            session, candidate_id=candidate_id
        )
        for kind in VIEW_PRODUCERS
    },
    "insert": lambda session, candidate_id, replace: approve_pin(
        session, candidate_id=candidate_id, replace=replace
    ),
    "character_replacement": lambda session, candidate_id, replace: approve_replacement(
        session, candidate_id
    ),
}


def approve(session, candidate_id: str, *, replace: bool = False) -> dict:
    """Approve the candidate as what its operation prepares; a pin replaces an approved one only with `replace`.
    Returns what the approval changed: the place, the view's evidence, the pin or the derivative asset."""
    operations = OperationService()
    kind = operations.operation(
        session, operations.candidate(session, candidate_id)["operation_id"]
    )["kind"]
    approver = APPROVERS.get(kind)
    if approver is None:
        raise ApplicationError("This candidate is selected or kept, not approved.", 422)
    return approver(session, candidate_id, replace)
