"""The Join: how a continuing segment takes over its predecessor's motion (docs/CONTINUATION.md).

This module finds the predecessor and the take it hands over, gathers the facts the integration's rule reads,
stores and judges a choice, and resolves the handoff the plan, Properties, Gaia and the run evidence report. The
rule and its wording stay in the integration.

A choice is a revision-checked Direction edit, so Undo and Redo go through its receipt. A choice the rule cannot
render is refused with the reason; null returns a field to the rule, and the default context length is stored as
null so the join follows the default again.
"""

from gaiavfx.application.direction_reading import DIRECTION
from gaiavfx.application.selected_takes import current_take
from gaiavfx.integrations import minimax_h3
from gaiavfx.integrations.minimax_h3 import continuation as continuity
from gaiavfx.integrations.minimax_h3.continuation import ContinuationFacts, Handoff
from gaiavfx.modules.direction.service import last_angle
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError, StaleRevision

METHODS = continuity.METHODS
FIELDS = {"method": "continuation_method", "context_frames": "context_frames"}


def grid():
    """The frame grid segments render on: H3's, the one model Direction plans for."""
    return minimax_h3.capabilities().frame_grid


def source(session, row, context_artifact=None):
    """The motion context a continuing segment reads, and where it came from: an explicit artifact, or the
    predecessor's current take. A segment that does not continue reads none."""
    if not row["continues"]:
        return None, None
    if context_artifact:
        context_source = {"kind": "artifact"}
    else:
        predecessor = DIRECTION.predecessor(session, row)
        if predecessor is None:
            raise ApplicationError(
                "The first segment has no predecessor; give --context-artifact.", 422
            )
        number = predecessor["ordinal"] + 1
        selection = current_take(session, row["movie_id"], predecessor["id"])
        if selection is None:
            raise ApplicationError(f"Select a take of segment {number} first.", 422)
        # The take Program marks "Changed since this take" no longer matches its segment
        # (a split, an edit, a replaced pin): its motion context would continue the old one.
        if selection["stale"]:
            raise ApplicationError(f"Select a current take of segment {number} first.", 422)
        candidate = OperationService().candidate(session, selection["candidate_id"])
        context_artifact = candidate["artifact_id"]
        if context_artifact is None:
            raise ApplicationError(
                f"The selected take of segment {number} captured no continuation context.", 422
            )
        context_source = {
            "kind": "selection",
            "segment_id": predecessor["id"],
            "selection_id": selection["id"],
            "candidate_id": candidate["id"],
            "authority": selection["authority"],
            "basis": selection["basis"],
        }
    artifact = MediaLibrary.artifact(session, context_artifact)
    if artifact["kind"] != "h3_context":
        raise ApplicationError("The continuation artifact must be H3 context.", 422)
    return artifact, context_source


def angle_changes(row, predecessor):
    """Whether the segment opens on another angle than its predecessor ends on: a cut at the join. It agrees with
    coverage (`assess_opening`): after a predecessor that names no angle, a named opening angle is a change."""
    if predecessor is None or row["opening_angle_id"] is None:
        return False
    return last_angle(predecessor) != row["opening_angle_id"]


def facts(session, row, *, pins, artifact=None):
    """What the rule reads about one join. `pins` counts the window's planned pinned keyframes: a technique's
    planned pin counts before it is bound (docs/CONTINUATION.md)."""
    return ContinuationFacts(
        continues=row["continues"],
        cuts=len(row["cuts"]),
        guides=pins,
        source_frames=(
            continuity.source_frames(artifact.get("info"), grid()) if artifact else None
        ),
        angle_changes=row["continues"] and angle_changes(row, DIRECTION.predecessor(session, row)),
    )


def resolve(session, row, *, pins, artifact=None) -> Handoff | None:
    """The handoff a continuing segment renders with and the rule's hint; None when it does not continue."""
    if not row["continues"]:
        return None
    return continuity.resolve(
        facts(session, row, pins=pins, artifact=artifact),
        method=row["continuation_method"],
        context_frames=row["context_frames"],
        grid=grid(),
    )


def stored(choice):
    """A choice as Direction stores it: the default context length is null, so the join follows the default."""
    default = grid().context_default
    return {
        key: None if key == "context_frames" and value == default else value
        for key, value in choice.items()
    }


def _problem(session, row, choice):
    """Why the rule refuses `choice` for this join now, judged with its cuts, planned pins and source take."""
    # The planner reads this module; planning a join without its window imports it here.
    from gaiavfx.application.direction_plans import planned_pins

    try:
        artifact, _ = source(session, row)
    except ApplicationError:
        artifact = None  # no selected take yet: the render plan checks the source
    known = facts(session, row, pins=planned_pins(session, row), artifact=artifact)
    return continuity.choice_problem(
        known,
        grid(),
        method=choice.get("method", row["continuation_method"]),
        context_frames=choice.get("context_frames", row["context_frames"]),
        changing=frozenset(choice),
    )


def set_continuation(session, segment_id, expected_revision, **choice):
    """`choice` holds `method`, `context_frames` or both. Returns the edited segment with its receipt, or None
    when the choice is already stored: no receipt records a change that did not happen."""
    if not choice or set(choice) - set(FIELDS):
        raise ApplicationError("Choose a continuation method or a context length.", 422)
    if type(expected_revision) is not int or expected_revision < 1:
        raise ApplicationError("expected_revision must be a positive integer.", 422)
    row = DIRECTION.read_segment(session, segment_id)
    if row["revision"] != expected_revision:
        raise StaleRevision("Segment", segment_id, row["revision"])
    changes = {key: value for key, value in stored(choice).items() if value != row[FIELDS[key]]}
    if not changes:
        return None
    if problem := _problem(session, row, changes):
        raise ApplicationError(problem, 422)
    return DIRECTION.update_segment(
        session,
        segment_id,
        expected_revision,
        **{FIELDS[key]: value for key, value in changes.items()},
    )


def judge(session, segment_id):
    """Refuse a new segment's stored choice the rule cannot render, once its cuts and pins exist; the caller's
    transaction then rolls the whole addition back."""
    row = DIRECTION.read_segment(session, segment_id)
    choice = {key: row[field] for key, field in FIELDS.items() if row[field] is not None}
    if choice and (problem := _problem(session, row, choice)):
        raise ApplicationError(problem, 422)
