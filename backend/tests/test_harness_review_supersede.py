"""E2e run 4: a wrong survey view that the user never approves must not block the cut for good."""

from harness_support import world  # noqa: F401 (fixture)
from test_harness_tools import context_for

from gaiavfx.application.authored import load_survey
from gaiavfx.application.harness.tools.prepare import PrepareCut
from gaiavfx.application.harness.world import operations, review_pending
from gaiavfx.application.prepare_references import (
    approve_view,
    open_views_review,
    start_survey,
)


def test_a_newer_survey_supersedes_an_unapproved_older_one(world):  # noqa: F811
    from harness_eval_tasks import run_idle

    work, setup = world
    prepare = PrepareCut()
    first = prepare.run(context_for(work, setup), prepare.Input(cut_id=setup["cut_id"], seed=31))
    assert first.ok and first.waits_on
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        old = operations.operation(session, first.waits_on)
        # The user judges the views wrong and approves none of them.
        assert review_pending(session, old)
    assert prepare.narrow(context_for(work, setup)) is None
    with work.db.transaction() as session:
        # The user surveys again (the Prepare form) with corrected angle text.
        newer = start_survey(
            session,
            place_id=old["request"]["place_id"],
            plan=load_survey(old["request"]["plan"]),
            seed=32,
        )
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        assert not review_pending(session, operations.operation(session, old["id"]))
        assert review_pending(session, operations.operation(session, newer["id"]))
        for member in open_views_review(session, newer["id"])["members"]:
            approve_view(session, candidate_id=member["candidate_id"])
        assert not review_pending(session, operations.operation(session, newer["id"]))
    # The cut is preparable again: the pin is the next step.
    assert prepare.narrow(context_for(work, setup)) is not None
