"""Blind contextual review, stable letters and full-document ranking validation."""

from pathlib import Path

import pytest

from gaiavfx.application import candidate_review
from gaiavfx.infrastructure.database import Database
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.review import ReviewService, failure_tags
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService
from gaiavfx.modules.library.assets import AssetRecord
from gaiavfx.shared.errors import ApplicationError


def test_plate_purpose_accepted_and_unknown_refused(prepared):
    db, _, ids = prepared
    with db.transaction() as session:
        review = ReviewService().create(session, purpose="plate", candidate_ids=ids[:2], context={})
        assert review["purpose"] == "plate"
        with pytest.raises(ApplicationError) as error:
            ReviewService().create(session, purpose="unknown", candidate_ids=ids[:2], context={})
        assert error.value.status == 422


@pytest.fixture
def prepared(tmp_path):
    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Review sets")
    db = Database(tmp_path)
    with db.transaction() as session:
        ids = []
        operations = []
        for group, count in enumerate((3, 3)):
            operation = OperationService().create(
                session,
                kind="render",
                request={"secret": group},
                jobs=[
                    JobSpec(
                        kind="video",
                        executor="local",
                        integration="fake",
                        step="render",
                        inputs={},
                        snapshot={},
                        seed=group,
                    )
                ],
                budget_limit=None,
            )
            operations.append(operation["id"])
            for index in range(count):
                asset_id = f"asset-{group}-{index}"
                session.add(
                    AssetRecord(
                        id=asset_id,
                        sha256=f"{group}{index}".ljust(64, "0"),
                        name=asset_id,
                        kind="video",
                        path=asset_id,
                        mime="video/mp4",
                        info={},
                        provenance=[],
                        review="preferred",
                    )
                )
                candidate = OperationService().add_candidate(
                    session, operation["jobs"][0]["id"], asset_id, str(index), "video"
                )
                ids.append(candidate["id"])
    yield db, operations, ids
    db.close()


def test_stable_blind_letters_and_reveal(prepared):
    db, operations, ids = prepared
    with db.transaction() as session:
        orders = []
        for index in range(10):
            review = candidate_review.open_review(
                session,
                purpose="segment_takes",
                operation_ids=operations,
                context={"movie_id": "m", "segment_id": "s"},
                set_id=f"fixed-{index}",
            )
            orders.append(tuple(item["candidate_id"] for item in review["members"]))
            assert [item["letter"] for item in review["members"]] == list("ABCDEF")
            assert set(review["members"][0]) == {
                "letter",
                "candidate_id",
                "asset_id",
                "kind",
                "prescreen",
            }
            with pytest.raises(ApplicationError) as hidden:
                candidate_review.reveal(session, review["id"])
            assert hidden.value.status == 409
        assert len(set(orders)) > 1
        assert orders[0] == tuple(
            item["candidate_id"] for item in candidate_review.blind(session, "fixed-0")["members"]
        )
        ranking = candidate_review.save_ranking(
            session,
            "fixed-0",
            expected_revision=0,
            tiers=[[ids[0], ids[1]], [ids[2]], [ids[3], ids[4]], [ids[5]]],
            usable_tiers=2,
            notes={ids[0]: "Wrong side"},
            tags={ids[0]: ["wrong_side"]},
            general="Usable first two tiers",
        )
        assert ranking["revision"] == 1
        revealed = candidate_review.reveal(session, "fixed-0")
        assert revealed["members"][0]["integration"] == "fake"
        assert "seed" in revealed["members"][0] and "graph" in revealed["members"][0]
        assert "request" in revealed["members"][0]
        assert all(
            session.get(AssetRecord, f"asset-{g}-{i}").review == "preferred"
            for g in range(2)
            for i in range(3)
        )


def test_add_member_keeps_existing_letters(prepared):
    db, _, ids = prepared
    with db.transaction() as session:
        review = ReviewService().create(
            session, purpose="sheet", candidate_ids=ids[:5], context={}, set_id="fifth"
        )
        ReviewService().save_ranking(
            session,
            "fifth",
            expected_revision=0,
            tiers=[ids[:5]],
            usable_tiers=1,
            notes={},
            tags={},
            general="Before the late candidate",
        )
        old = {item["candidate_id"]: item["letter"] for item in review["members"]}
        updated = ReviewService().add(session, "fifth", [ids[5]], expected_revision=1)
        assert updated["ranking"]["tiers"] == [ids[:5]]
        assert updated["ranking"]["general"] == "Before the late candidate"
        assert updated["unranked"] == [ids[5]]
        assert updated["ranking"]["revision"] == 2
        assert {
            item["candidate_id"]: item["letter"]
            for item in updated["members"]
            if item["candidate_id"] in old
        } == old
        assert (
            next(item["letter"] for item in updated["members"] if item["candidate_id"] == ids[5])
            == "F"
        )


def test_letters_continue_after_z(prepared):
    db, operations, ids = prepared
    with db.transaction() as session:
        job_id = OperationService().operation(session, operations[0])["jobs"][0]["id"]
        extra = [
            OperationService().add_candidate(
                session, job_id, f"extra-{index}", f"extra-{index}", "video"
            )["id"]
            for index in range(21)
        ]
        review = ReviewService().create(
            session, purpose="sheet", candidate_ids=ids + extra, context={}
        )
        assert [item["letter"] for item in review["members"]] == [
            *[chr(65 + index) for index in range(26)],
            "AA",
        ]


@pytest.mark.parametrize(
    "change",
    [
        {"tiers": [["first"]]},
        {"tiers": [["first", "first"], ["second"]]},
        {"tiers": [["first"], ["unknown"]]},
        {"tags": {"first": ["unknown"]}},
        {"usable_tiers": 3},
        {"expected_revision": 7},
    ],
)
def test_invalid_ranking_is_rejected(prepared, change):
    db, _, ids = prepared
    with db.transaction() as session:
        ReviewService().create(
            session, purpose="sheet", candidate_ids=ids[:2], context={}, set_id="ranking"
        )
        values = {
            "expected_revision": 0,
            "tiers": [[ids[0]], [ids[1]]],
            "usable_tiers": 1,
            "notes": {},
            "tags": {},
            "general": "",
        }
        if "tiers" in change:
            values["tiers"] = [
                [
                    ids[0] if item == "first" else ids[1] if item == "second" else item
                    for item in tier
                ]
                for tier in change["tiers"]
            ]
        elif "tags" in change:
            values["tags"] = {ids[0]: ["unknown"]}
        else:
            values.update(change)
        with pytest.raises(ApplicationError) as error:
            ReviewService().save_ranking(session, "ranking", **values)
        assert error.value.status == (409 if "expected_revision" in change else 422)


def test_taxonomy_is_strict(monkeypatch):
    from gaiavfx.modules.generation.operations import review

    class BadFile:
        def read_text(self, _encoding):
            return 'schema = 1\nunknown = true\n[tags]\nwrong_side = "wrong"'

    class BadResources:
        def joinpath(self, _name):
            return BadFile()

    monkeypatch.setattr(review.resources, "files", lambda _package: BadResources())
    failure_tags.cache_clear()
    with pytest.raises(ApplicationError) as error:
        failure_tags()
    assert error.value.status == 422
    failure_tags.cache_clear()


def test_reveal_waits_until_late_members_are_ranked(prepared):
    db, _, ids = prepared
    with db.transaction() as session:
        ReviewService().create(
            session, purpose="sheet", candidate_ids=ids[:2], context={}, set_id="late"
        )
        ReviewService().save_ranking(
            session,
            "late",
            expected_revision=0,
            tiers=[ids[:2]],
            usable_tiers=1,
            notes={},
            tags={},
            general="",
        )
        ReviewService().add(session, "late", [ids[2]], expected_revision=1)
        with pytest.raises(ApplicationError) as error:
            ReviewService().reveal(session, "late")
        assert error.value.status == 409
