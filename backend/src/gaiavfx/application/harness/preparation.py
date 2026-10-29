"""The preparation operations the agent starts and waits on, in one table (ADR 0019 §3, §7): the request field that
scopes each, and how a person's review of its candidates opens. The angle-view producers come from the
application's table, so a new producer joins without a new branch in the world state, the reviews or the retries."""

from dataclasses import dataclass

from gaiavfx.application.prepare_references import (
    VIEW_PRODUCERS,
    open_insertion_review,
    open_plate_review,
    open_views_review,
)


@dataclass(frozen=True)
class Preparation:
    scope: str  # the request field naming what it prepares
    review: object  # opens the review of its candidates; None when no person reviews them


PREPARATION = {
    "removal": Preparation("place_id", open_plate_review),
    **{kind: Preparation("place_id", open_views_review) for kind in VIEW_PRODUCERS},
    "insert": Preparation("cut_id", open_insertion_review),
    "assemble": Preparation("place_id", None),
}
VIEW_KINDS = frozenset(VIEW_PRODUCERS)  # their candidates are angle views, approved as evidence
REVIEWED = frozenset(kind for kind, preparation in PREPARATION.items() if preparation.review)
VIEW_ACTIONS = frozenset({"view_angle", "view_place"})  # Direction's actions that ask for views
