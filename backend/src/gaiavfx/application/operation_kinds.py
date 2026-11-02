"""Each operation kind's review in one table: what the kind prepares, how a person's review of its candidates opens,
whether that review still waits on the person, and whether its candidates are Recast renders a film keeps blind.

Gaia, the API and the renderer read this table; approving a candidate is a person's act and lives apart, in
`review_approvals`. A new producer joins here without a new branch in Gaia's world state, its tools or the routes.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from gaiavfx.application.candidate_review import awaits_ranking, existing_review
from gaiavfx.application.panoramas import open_panorama_review, panorama_review_pending
from gaiavfx.application.prepare_references import (
    VIEW_PRODUCERS,
    insertion_review_pending,
    open_insertion_review,
    open_plate_review,
    open_views_review,
    plate_review_pending,
    views_review_pending,
)
from gaiavfx.application.render_cut import open_takes_review
from gaiavfx.application.replace_character import open_replacement_review
from gaiavfx.shared.errors import ApplicationError

FINISHED = frozenset({"succeeded", "partial"})


@dataclass(frozen=True)
class OperationKind:
    kind: str
    scope: str | None = (
        None  # the request field naming what it prepares, for a reference preparation
    )
    opens: Callable | None = (
        None  # (session, operation_id) -> the review of its candidates, opened once
    )
    pending: Callable | None = (
        None  # (session, operation) -> whether that review still waits on a person
    )
    gaia_opens: bool = (
        True  # Gaia's read_candidates may open the review; a stage's views open in Stages
    )
    blind_render: bool = (
        False  # a Recast render: in an open film it reads alike whatever its method
    )


KINDS = {
    kind.kind: kind
    for kind in (
        OperationKind("removal", "place_id", open_plate_review, plate_review_pending),
        *(
            OperationKind(
                name,
                "place_id",
                open_views_review,
                views_review_pending,
                gaia_opens=name == "survey",
            )
            for name in VIEW_PRODUCERS
        ),
        OperationKind("insert", "cut_id", open_insertion_review, insertion_review_pending),
        OperationKind("assemble", "place_id"),
        OperationKind("render", opens=open_takes_review, pending=awaits_ranking),
        OperationKind(
            "character_replacement",
            opens=open_replacement_review,
            pending=awaits_ranking,
            blind_render=True,
        ),
        OperationKind("shot_assembly", blind_render=True),
        # Panoramas are chosen in Stages, never by Gaia.
        OperationKind(
            "panorama",
            "place_id",
            open_panorama_review,
            panorama_review_pending,
            gaia_opens=False,
        ),
    )
}
PREPARATION = {
    name: kind for name, kind in KINDS.items() if kind.scope
}  # what Gaia prepares and waits on
REVIEWED = frozenset(name for name, kind in PREPARATION.items() if kind.opens)  # reference reviews
VIEW_KINDS = frozenset(VIEW_PRODUCERS)  # their candidates are angle views, approved as evidence


def kind_of(name: str) -> OperationKind:
    return KINDS.get(name) or OperationKind(name)


def open_review(session, operation) -> dict:
    """Open (or return) the review of a finished operation's candidates."""
    kind = kind_of(operation["kind"])
    if kind.opens is None:
        raise ApplicationError("This operation has no candidate review.", 422)
    return kind.opens(session, operation["id"])


def review_pending(session, operation) -> bool:
    """Whether a finished operation's candidates still wait on a person. A reference review the person rejected as a
    whole waits no longer."""
    kind = kind_of(operation["kind"])
    if operation["state"] not in FINISHED or kind.pending is None:
        return False
    review = existing_review(session, operation["id"])
    if review and review.get("rejection"):
        return False
    return kind.pending(session, operation)
