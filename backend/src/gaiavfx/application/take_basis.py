"""Take basis: what a take was rendered from, and whether the authored movie still matches it.

A render records its basis with its request: the segment's, its phases', its cast's and its cuts' revisions, the
opening angle and the pin each cut bound. A selected take is current while all of that still holds; an Undo that put
a revision back counts as unchanged. Program, Export, the Join, Gaia and the stage seed read this one answer, worded
by Editing's staleness rule.

A take rendered before cut revisions, the opening angle and pins were recorded is compared on what it recorded; its
pins are the approved pins its job sent.
"""

from gaiavfx.modules.direction.service import DirectionService, revision_restored
from gaiavfx.modules.editing.selections.staleness import changed_since_take
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

# Only Direction's own records are read: no sheet or stage-view readers are needed (and Export, which reads takes,
# is imported before the configured service).
direction = DirectionService()


def record(session, row, keyframes) -> dict:
    """The basis a render of segment `row` records: `keyframes` are the pinned keyframes its window binds."""
    return {
        "segment_revision": row["revision"],
        "phase_revisions": {phase["id"]: phase["revision"] for phase in row["phases"]},
        "subject_revisions": _subject_revisions(session, row),
        "cut_revisions": {cut["id"]: cut["revision"] for cut in row["cuts"]},
        "opening_angle_id": row["opening_angle_id"],
        # A keyframe's cut index counts shots from the opening: the shot after cut n is shot n + 1.
        "pins": {row["cuts"][k.cut_index - 1]["id"]: k.asset_id for k in keyframes},
    }


def judge(session, segment_id):
    """Why a take of the segment has changed since it was rendered, by its candidate id: an empty list for a current
    take. The movie is read once for every take judged."""
    now = _current(session, segment_id)
    operations = OperationService()

    def reasons(candidate_id):
        candidate = operations.candidate(session, candidate_id)
        request = operations.request_snapshot(session, candidate["operation_id"])
        if "pins" not in request and now is not None:
            sent = {
                asset_id
                for job in operations.operation(session, candidate["operation_id"])["jobs"]
                if job["id"] == candidate["job_id"]
                for asset_id in job["inputs"]
            }
            request = {**request, "pins": _sent_pins(session, now, sent)}
        return changed_since_take(
            request,
            now,
            segment_id=segment_id,
            unchanged=lambda kind, identity, current, then: revision_restored(
                session, kind, identity, current, then
            ),
        )

    return reasons


def _current(session, segment_id):
    """The basis the segment has now, or None once it no longer exists."""
    try:
        row = direction.read_segment(session, segment_id)
    except ApplicationError as exc:
        if exc.status != 404:
            raise
        return None
    return {
        "segment_revision": row["revision"],
        "phase_revisions": {phase["id"]: phase["revision"] for phase in row["phases"]},
        "subject_revisions": _subject_revisions(session, row),
        "cut_revisions": {cut["id"]: cut["revision"] for cut in row["cuts"]},
        "opening_angle_id": row["opening_angle_id"],
        "pins": {
            cut["id"]: next(
                (
                    pin["asset_id"]
                    for pin in direction.keyframes(session, cut["id"])
                    if pin["approved"]
                ),
                None,
            )
            for cut in row["cuts"]
        },
    }


def _subject_revisions(session, row):
    cast = {
        subject["id"]: subject["revision"]
        for subject in direction.subjects(session, row["movie_id"])
    }
    return {identity: cast.get(identity) for identity in row["content"]["subjects"]}


def _sent_pins(session, now, sent):
    """The pins an older take bound, from the assets its job sent: a cut's pin that was approved when it was sent."""
    return {
        cut_id: pin["asset_id"]
        for cut_id in now["pins"]
        for pin in direction.keyframes(session, cut_id)
        if pin["approved_at"] and pin["asset_id"] in sent
    }
