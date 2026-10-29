"""Stored Direction facts drive the pure planner and preserve review boundaries."""

from pathlib import Path

import pytest
from sqlalchemy import func, select

from gaiavfx.application.direction_plans import plan_for_cut, plan_to_dict
from gaiavfx.infrastructure.database import Database
from gaiavfx.modules.direction.persistence import CutRecord, PlaceRecord
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.library.application import LibraryService
from gaiavfx.shared.errors import ApplicationError


@pytest.fixture
def store(tmp_path):
    migrations = Path(__file__).parents[1] / "migrations"
    LibraryService(tmp_path, migrations).create("Direction")
    db = Database(tmp_path)
    yield db, DirectionService()
    db.close()


def make_segment(service, session, movie_id=None):
    movie = (
        service.read_movie(session, movie_id)
        if movie_id
        else service.create_movie(session, "Movie")
    )
    subject = service.add_subject(
        session,
        movie["id"],
        movie["revision"],
        name="Arin",
        description="A walker",
        keeps="coat",
        identity_asset_id=None,
    )
    return service.add_segment(
        session,
        movie["id"],
        subject["receipt"]["after"]["revision"],
        content={
            "subjects": [subject["id"]],
            "place": {"noun": "courtyard", "description": "A courtyard.", "landmarks": ""},
        },
        delivered_frames=102,
        continues=True,
        phases=[
            (0, {"description": "Walk.", "screen_direction": "forward", "sound": "steps"}),
            (36, {"description": "Turn.", "screen_direction": "left", "sound": "steps"}),
        ],
    )


def make_cut(service, session, relation="reverse", crosses=True, receives=False):
    place = service.create_place(session, "Courtyard")
    source = service.add_angle(session, place["id"], "Facing gate")
    target = service.add_angle(session, place["id"], "Facing arcade")
    segment = make_segment(service, session)
    cut = service.create_cut(
        session,
        segment_id=segment["id"],
        expected_revision=segment["revision"],
        frame=36,
        from_angle_id=source["id"],
        to_angle_id=target["id"],
        relation=relation,
        crosses_barrier=crosses,
        relation_source="authored",
        receives_predecessor=receives,
    )
    return place, source, target, cut


def test_direction_readers_return_written_rows(store):
    db, service = store
    with db.transaction() as session:
        place, source, target, cut = make_cut(service, session)
        evidence = service.record_evidence(session, target["id"], "survey_view", "view")
        keyframe = service.record_keyframe(session, cut["id"], "pin", evidence["id"])
        assert service.read_place(session, place["id"])["id"] == place["id"]
        assert {row["id"] for row in service.angles(session, place["id"])} == {
            source["id"],
            target["id"],
        }
        assert service.read_angle(session, target["id"])["place_id"] == place["id"]
        assert service.evidence(session, target["id"])[0]["id"] == evidence["id"]
        assert service.keyframes(session, cut["id"])[0]["id"] == keyframe["id"]
        with pytest.raises(ApplicationError) as error:
            service.evidence(session, "missing")
        assert error.value.status == 404


def actions(plan):
    return [action["action"] for action in plan["recommended"]["actions"]]


def test_run5_create_and_angle_updates_refuse_same_angle_without_writes(store):
    db, service = store
    with db.transaction() as session:
        _, source, target, cut = make_cut(service, session)
        segment = service.read_segment(session, cut["segment_id"])
        before = service.receipts(session, segment["movie_id"])
        with pytest.raises(ApplicationError, match="same_angle_cut"):
            service.create_cut(
                session,
                segment["id"],
                segment["revision"],
                frame=48,
                from_angle_id=source["id"],
                to_angle_id=source["id"],
                relation="reverse",
                crosses_barrier=True,
                relation_source="authored",
            )
        for changes in (
            {"from_angle_id": target["id"]},
            {"to_angle_id": source["id"]},
            {"from_angle_id": source["id"], "to_angle_id": source["id"]},
        ):
            with pytest.raises(ApplicationError, match="same_angle_cut"):
                service.update_cut(session, cut["id"], cut["revision"], **changes)
        assert service.read_segment(session, segment["id"]) == segment
        assert service.receipts(session, segment["movie_id"]) == before


def test_run5_stored_same_angle_cut_has_typed_plan_refusal_without_rewrite(store):
    from gaiavfx.application.direction_plans import plan_for_segment

    db, service = store
    with db.transaction() as session:
        _, source, _, cut = make_cut(service, session)
        legacy = session.get(CutRecord, cut["id"])
        legacy.to_angle_id = source["id"]
        legacy.receives_predecessor = True
        session.flush()
        before = service.read_cut(session, cut["id"])
        plan = plan_for_cut(session, cut["id"])
        assert plan.recommended is None and plan.gaps[0].code == "same_angle_cut"
        segment = plan_for_segment(session, cut["segment_id"])
        assert not segment.ready
        assert any(
            r.code == "technique_unavailable" and "different target angle" in r.message
            for r in segment.refusals
        )
        assert service.read_cut(session, cut["id"]) == before


def test_set_plate_is_idempotent_and_checks_place_membership(store):
    db, service = store
    with db.transaction() as session:
        place = service.create_place(session, "Courtyard")
        assert place["plate_asset_id"] is None and place["plate_angle_id"] is None
        gate = service.add_angle(session, place["id"], "Gate")
        reverse = service.add_angle(session, place["id"], "Reverse")
        other = service.create_place(session, "Other")
        foreign = service.add_angle(session, other["id"], "Gate")
        with pytest.raises(ApplicationError) as error:
            service.set_plate(session, place["id"], asset_id="plate", angle_id=foreign["id"])
        assert error.value.status == 422
        approved = service.set_plate(session, place["id"], asset_id="plate", angle_id=gate["id"])
        assert approved["plate_asset_id"] == "plate" and approved["plate_angle_id"] == gate["id"]
        assert (
            service.set_plate(session, place["id"], asset_id="plate", angle_id=reverse["id"])
            == approved
        )
        with pytest.raises(ApplicationError, match="already has a different plate") as error:
            service.set_plate(session, place["id"], asset_id="other", angle_id=gate["id"])
        assert error.value.status == 409
    with db.transaction() as session:
        assert service.read_place(session, place["id"]) == approved
        assert next(p for p in service.places(session) if p["id"] == place["id"]) == approved


def test_reverse_plan_moves_through_review_to_ready(store):
    db, service = store
    with db.transaction() as session:
        _, _, target, cut = make_cut(service, session)
        fresh = plan_to_dict(plan_for_cut(session, cut["id"]))
        assert fresh["assessment"]["coverage"] == "new"
        assert fresh["recommended"]["technique_id"] == "views_and_pin"
        assert actions(fresh) == ["view_angle", "insert_subject", "view_place"]
        evidence = service.record_evidence(session, target["id"], "survey_view", "asset-view")
        keyframe = service.record_keyframe(session, cut["id"], "asset-pin", evidence["id"])
        assert evidence["approved"] is False and keyframe["approved"] is False
        pending = plan_to_dict(plan_for_cut(session, cut["id"]))
        assert pending["assessment"]["coverage"] == "new"
        assert actions(pending)[:2] == ["review_view", "review_keyframe"]
        with pytest.raises(ApplicationError) as premature:
            service.approve_keyframe(session, keyframe["id"])
        assert premature.value.status == 422
        assert service.approve_evidence(session, evidence["id"])["approved_by"] == "user"
        assert service.approve_keyframe(session, keyframe["id"])["approved_by"] == "user"
        ready = plan_to_dict(plan_for_cut(session, cut["id"]))
        assert ready["assessment"]["coverage"] == "established"
        assert ready["recommended"]["ready"] is True
        assert actions(ready) == []
        context, available, _bindings = service.context_and_available(session, cut["id"])
        assert available.place_views == 1
        assert target["id"] in available.approved_view_angles
        assert context.evidence[0].id == evidence["id"]
    with db.transaction() as session:
        assert plan_for_cut(session, cut["id"]).recommended.ready


def test_same_direction_cut_continues_without_evidence(store):
    db, service = store
    with db.transaction() as session:
        _, _, _, cut = make_cut(service, session, "same_direction", False, True)
        plan = plan_to_dict(plan_for_cut(session, cut["id"]))
        assert plan["assessment"]["coverage"] == "continuing"
        assert plan["recommended"]["technique_id"] == "text"
        assert plan["recommended"]["ready"] is True


@pytest.mark.parametrize("angle_field", ["from_angle_id", "to_angle_id"])
def test_retargeting_a_cut_clears_stale_relation_facts(store, angle_field):
    db, service = store
    with db.transaction() as session:
        place, _, _, cut = make_cut(service, session, "same_direction", False, True)
        reverse = service.add_angle(session, place["id"], "Reverse angle")
        before = plan_to_dict(plan_for_cut(session, cut["id"]))
        assert before["recommended"]["technique_id"] == "text"
        changed = service.update_cut(session, cut["id"], 1, **{angle_field: reverse["id"]})
        assert changed["revision"] == 2
        assert (changed["relation"], changed["crosses_barrier"], changed["relation_source"]) == (
            None,
            None,
            "unknown",
        )
        plan = plan_to_dict(plan_for_cut(session, cut["id"]))
        assert plan["assessment"]["coverage"] == "unknown"
        assert plan["recommended"] is None
        assert plan["gaps"][0]["code"] == "coverage_unknown"


def test_stale_revision_and_invalid_relation_write_nothing(store):
    db, service = store
    with db.transaction() as session:
        _, _, _, cut = make_cut(service, session)
        changed = service.update_cut(session, cut["id"], 1, frame=48)
        assert changed["revision"] == 2
        with pytest.raises(ApplicationError) as stale:
            service.update_cut(session, cut["id"], 1, frame=72)
        assert stale.value.status == 409
        with pytest.raises(ApplicationError) as invalid:
            service.update_cut(session, cut["id"], 2, relation="side", crosses_barrier=None)
        assert invalid.value.status == 422
        assert service.read_cut(session, cut["id"])["frame"] == 48
        assert service.read_cut(session, cut["id"])["revision"] == 2
        assert session.scalar(select(func.count()).select_from(CutRecord)) == 1
    with pytest.raises(ApplicationError), db.transaction() as session:
        service.create_place(session, "Rolled back")
        service.add_angle(session, "missing", "Invalid")
    with db.transaction() as session:
        assert session.scalar(select(func.count()).select_from(PlaceRecord)) == 1


def test_references_and_integration_errors_are_typed(store):
    db, service = store
    with db.transaction() as session:
        _, _, target, cut = make_cut(service, session)
        other = service.add_angle(session, service.create_place(session, "Other")["id"], "Other")
        evidence = service.record_evidence(session, other["id"], "imported", "asset")
        with pytest.raises(ApplicationError) as mismatch:
            service.record_keyframe(session, cut["id"], "asset", evidence["id"])
        assert mismatch.value.status == 422
        with pytest.raises(ApplicationError) as missing:
            service.read_cut(session, "missing")
        assert missing.value.status == 404
        with pytest.raises(ApplicationError) as unknown:
            plan_for_cut(session, cut["id"], "unknown")
        assert unknown.value.status == 422
        service.record_evidence(session, target["id"], "survey_view", "asset")


def test_place_views_count_approved_evidence_across_all_place_angles(store):
    db, service = store
    with db.transaction() as session:
        _, source, target, cut = make_cut(service, session)
        other_view = service.record_evidence(session, source["id"], "survey_view", "other-view")
        service.approve_evidence(session, other_view["id"])
        _, available, _bindings = service.context_and_available(session, cut["id"])
        assert available.place_views == 1
        assert source["id"] in available.approved_view_angles
        assert target["id"] not in available.approved_view_angles
        assert actions(plan_to_dict(plan_for_cut(session, cut["id"]))) == [
            "view_angle",
            "insert_subject",
        ]
