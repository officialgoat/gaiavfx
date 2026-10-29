"""Human pin replacement, guarded history and frozen take dependencies."""

import harness_support
import pytest
from harness_eval_tasks import run_idle

from gaiavfx.application.prepare_references import (
    approve_pin,
    open_insertion_review,
    start_insertion,
)
from gaiavfx.application.selected_takes import selection_state
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.editing.selections.service import SelectionService
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

world = harness_support.world
direction = DirectionService()


def candidates(work, setup):
    with work.db.transaction() as session:
        cut = direction.read_cut(session, setup["cut_id"])
        view = direction.record_evidence(session, cut["to_angle_id"], "imported", setup["plate"])
        direction.approve_evidence(session, view["id"])
        operation = start_insertion(
            session,
            cut_id=cut["id"],
            evidence_id=view["id"],
            subject_asset=setup["identity"],
            wording=setup["wording"],
            seeds=[71, 72, 73],
        )
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        review = open_insertion_review(session, operation["id"])
        return review["id"], [m["candidate_id"] for m in review["members"]]


def take(session, setup, pin, *, dependent=True):
    segment = direction.read_segment(session, setup["segment_id"])
    operations = OperationService()
    operation = operations.create(
        session,
        kind="render",
        budget_limit=None,
        request={
            "segment_revision": segment["revision"],
            "phase_revisions": {p["id"]: p["revision"] for p in segment["phases"]},
            "subject_revisions": {
                s: direction.read_subject(session, s)["revision"]
                for s in segment["content"]["subjects"]
            },
        },
        jobs=[
            JobSpec(
                "video",
                "local",
                "fixture",
                step="render",
                inputs={
                    pin["asset_id"]: {"sha256": "frozen", "remote_name": "gaiavfx/keyframe_0.png"}
                }
                if dependent
                else {},
            )
        ],
    )
    candidate = operations.add_candidate(
        session, operation["jobs"][0]["id"], setup["take"], "take", "video"
    )
    selections = SelectionService()
    return selections.select(
        session,
        movie_id=setup["movie_id"],
        segment_id=setup["segment_id"],
        candidate_id=candidate["id"],
        asset_id=setup["take"],
        authority="user",
        expected_revision=selections.revision(session, setup["movie_id"], setup["segment_id"]),
    )


def test_explicit_replacement_retains_history_stales_only_dependencies_and_undoes(world):
    work, setup = world
    review_id, ids = candidates(work, setup)
    with work.db.transaction() as session:
        old = approve_pin(session, candidate_id=ids[0])
        take(session, setup, old, dependent=False)
        chosen = take(session, setup, old)
        review_before = ReviewService().blind(session, review_id)
        receipts_before = direction.receipts(session, setup["movie_id"])
    with (
        pytest.raises(ApplicationError, match="replace: true") as error,
        work.db.transaction() as session,
    ):
        approve_pin(session, candidate_id=ids[1])
    assert error.value.status == 409
    with work.db.transaction() as session:
        assert direction.keyframes(session, setup["cut_id"]) == [old]
        assert direction.receipts(session, setup["movie_id"]) == receipts_before
        assert ReviewService().blind(session, review_id) == review_before
        new = approve_pin(session, candidate_id=ids[1], replace=True)
        assert new["receipt"]["command"] == "replace_pin"
        pins = direction.keyframes(session, setup["cut_id"])
        assert next(p for p in pins if p["id"] == old["id"]) == old | {"approved": False}
        assert [p["id"] for p in pins if p["approved"]] == [new["id"]]
        state = selection_state(session, setup["movie_id"], setup["segment_id"])
        assert state["current"]["id"] == chosen["id"]
        assert state["current"]["stale_reasons"] == ["Pin replaced since this take."]
        assert [s["stale"] for s in state["history"]] == [False, True]
        inverse = direction.undo(session, new["receipt"]["id"])
        pins = direction.keyframes(session, setup["cut_id"])
        assert next(p for p in pins if p["id"] == old["id"]) == old
        assert next(p for p in pins if p["id"] == new["id"])["approved_at"] == new["approved_at"]
        assert not selection_state(session, setup["movie_id"], setup["segment_id"])["current"][
            "stale"
        ]
        direction.undo(session, inverse["id"])
        assert selection_state(session, setup["movie_id"], setup["segment_id"])["current"]["stale"]


def test_replacement_authority_undo_guards_and_transaction_rollback(world):
    work, setup = world
    _, ids = candidates(work, setup)
    with work.db.transaction() as session:
        old = approve_pin(session, candidate_id=ids[0])
        row = direction.record_keyframe(session, old["cut_id"], "other", old["evidence_id"])
        with pytest.raises(ApplicationError, match="Only user"):
            direction.approve_keyframe_replacement(session, row["id"], approved_by="agent")
        with pytest.raises(ApplicationError, match="replace: true"):
            direction.approve_keyframe(session, row["id"])
        before = direction.keyframes(session, old["cut_id"])
        receipts = direction.receipts(session, setup["movie_id"])
    with pytest.raises(RuntimeError), work.db.transaction() as session:
        approve_pin(session, candidate_id=ids[1], replace=True)
        raise RuntimeError("rollback")
    with work.db.transaction() as session:
        assert direction.keyframes(session, old["cut_id"]) == before
        assert direction.receipts(session, setup["movie_id"]) == receipts
        first = approve_pin(session, candidate_id=ids[1], replace=True)
        second = approve_pin(session, candidate_id=ids[2], replace=True)
        with pytest.raises(ApplicationError, match="changed"):
            direction.undo(session, first["receipt"]["id"])
        assert (
            next(p for p in direction.keyframes(session, old["cut_id"]) if p["approved"])["id"]
            == second["id"]
        )
        direction.undo(session, second["receipt"]["id"])
        direction.undo(session, first["receipt"]["id"])
        assert next(p for p in direction.keyframes(session, old["cut_id"]) if p["approved"]) == old
