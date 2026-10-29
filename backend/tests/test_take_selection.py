"""Explicit user and provisional selection with preserved history."""

from pathlib import Path

import pytest

from gaiavfx.application.candidate_review import open_review, select_take
from gaiavfx.infrastructure.database import Database
from gaiavfx.modules.editing.selections.service import SelectionService
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService
from gaiavfx.shared.errors import ApplicationError


@pytest.fixture
def takes(tmp_path):
    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Selection")
    db = Database(tmp_path)
    with db.transaction() as session:
        operation = OperationService().create(
            session,
            kind="render",
            request={},
            budget_limit=None,
            jobs=[JobSpec(kind="video", executor="local", integration="fake", step="render")],
        )
        job_id = operation["jobs"][0]["id"]
        candidates = [
            OperationService().add_candidate(session, job_id, f"asset-{i}", str(i), "video")
            for i in range(3)
        ]
    yield db, operation["id"], candidates
    db.close()


def test_user_supersedes_provisional_and_history_remains(takes):
    db, _, candidates = takes
    service = SelectionService()
    with db.transaction() as session:
        first = service.select(
            session,
            movie_id="m",
            segment_id="s",
            candidate_id=candidates[0]["id"],
            asset_id="asset-0",
            authority="provisional",
            basis="unjudged",
            expected_revision=0,
        )
        second = service.select(
            session,
            movie_id="m",
            segment_id="s",
            candidate_id=candidates[1]["id"],
            asset_id="asset-1",
            authority="user",
            expected_revision=1,
        )
        assert first["revision"] == 1 and second["revision"] == 2
        assert service.current(session, "m", "s")["candidate_id"] == candidates[1]["id"]
        assert len(service.history(session, "m", "s")) == 2
        assert service.history(session, "m", "s")[0]["superseded_at"]
        with pytest.raises(ApplicationError) as error:
            service.select(
                session,
                movie_id="m",
                segment_id="s",
                candidate_id=candidates[2]["id"],
                asset_id="asset-2",
                authority="provisional",
                basis="unjudged",
                expected_revision=2,
            )
        assert error.value.status == 409
        with pytest.raises(ApplicationError) as error:
            service.clear(
                session, movie_id="m", segment_id="s", authority="provisional", expected_revision=2
            )
        assert error.value.status == 409
        service.clear(session, movie_id="m", segment_id="s", authority="user", expected_revision=2)
        assert service.current(session, "m", "s") is None
        assert service.revision(session, "m", "s") == 3


def test_select_take_checks_membership_context_and_asset(takes):
    db, operation_id, candidates = takes
    with db.transaction() as session:
        review = open_review(
            session,
            purpose="segment_takes",
            operation_ids=[operation_id],
            context={"movie_id": "m", "segment_id": "s"},
        )
        selected = select_take(
            session,
            review_set_id=review["id"],
            candidate_id=candidates[1]["id"],
            authority="user",
            expected_revision=0,
        )
        assert selected["asset_id"] == "asset-1"
        with pytest.raises(ApplicationError) as stale:
            select_take(
                session,
                review_set_id=review["id"],
                candidate_id=candidates[0]["id"],
                authority="user",
                expected_revision=0,
            )
        assert stale.value.status == 409
        with pytest.raises(ApplicationError) as outside:
            select_take(
                session,
                review_set_id=review["id"],
                candidate_id="outside",
                authority="user",
                expected_revision=1,
            )
        assert outside.value.status == 422
        incomplete = open_review(
            session,
            purpose="segment_takes",
            operation_ids=[operation_id],
            context={"movie_id": "m"},
        )
        with pytest.raises(ApplicationError) as context:
            select_take(
                session,
                review_set_id=incomplete["id"],
                candidate_id=candidates[0]["id"],
                authority="user",
                expected_revision=1,
            )
        assert context.value.status == 422


def test_selection_basis_is_required_only_for_provisional(takes):
    db, _, candidates = takes
    service = SelectionService()
    values = {
        "movie_id": "m",
        "segment_id": "basis",
        "candidate_id": candidates[0]["id"],
        "asset_id": "asset-0",
        "expected_revision": 0,
    }
    with db.transaction() as session:
        for authority, basis in (
            ("user", "unjudged"),
            ("provisional", "unknown"),
            ("provisional", None),
        ):
            with pytest.raises(ApplicationError) as error:
                service.select(session, **values, authority=authority, basis=basis)
            assert error.value.status == 422
        row = service.select(session, **values, authority="provisional", basis="unjudged")
        assert row["basis"] == "unjudged"
        picked = service.select(session, **(values | {"expected_revision": 1}), authority="user")
        assert picked["basis"] is None
        with pytest.raises(ApplicationError, match="cannot replace") as error:
            service.select(
                session,
                **(values | {"expected_revision": 2}),
                authority="provisional",
                basis="prescreen",
            )
        assert error.value.status == 409
