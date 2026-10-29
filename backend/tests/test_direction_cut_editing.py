"""S7o cut removal/moves are atomic, revision guarded and undoable."""

from copy import deepcopy

import pytest
import test_direction_segments
from test_direction_segments import cut, fail_without_write, snapshot
from test_direction_store import make_segment

store = test_direction_segments.store


def test_remove_undo_redo_preserves_cut_identity_and_phase(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        original = cut(service, session, segment)
        removed = service.remove_cut(session, original["id"], 2)
        assert removed["receipt"]["command"] == "remove_cut"
        assert not service.cuts(session, segment["id"])
        assert service.phases(session, segment["id"]) == segment["phases"]
        inverse = service.undo(session, removed["receipt"]["id"])
        restored = service.read_cut(session, original["id"])
        assert restored == {k: v for k, v in original.items() if k != "receipt"}
        service.undo(session, inverse["id"])
        assert not service.cuts(session, segment["id"])


def test_move_moves_phase_and_one_undo_restores_both(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        original = cut(service, session, segment)
        moved = service.update_cut(session, original["id"], 1, frame=60)
        phases = service.phases(session, segment["id"])
        assert [p["start_frame"] for p in phases] == [0, 60]
        assert phases[1]["id"] == segment["phases"][1]["id"]
        service.assemble_segment(session, segment["id"])
        inverse = service.undo(session, moved["receipt"]["id"])
        assert service.read_cut(session, original["id"])["frame"] == 36
        assert [p["start_frame"] for p in service.phases(session, segment["id"])] == [0, 36]
        service.undo(session, inverse["id"])
        assert service.read_cut(session, original["id"])["frame"] == 60
        assert [p["start_frame"] for p in service.phases(session, segment["id"])] == [0, 60]


def test_move_can_create_missing_phase_from_active_direction(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        original = cut(service, session, segment, frame=96)
        moved = service.update_cut(session, original["id"], 1, frame=72)
        phases = service.phases(session, segment["id"])
        assert [p["start_frame"] for p in phases] == [0, 36, 72]
        assert phases[-1]["content"] == segment["phases"][-1]["content"]
        service.undo(session, moved["receipt"]["id"])
        assert service.phases(session, segment["id"]) == segment["phases"]


def test_move_can_use_identical_authored_phase_without_overwriting_it(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        original = cut(service, session, segment)
        source = segment["phases"][-1]
        service.update_phase(session, source["id"], source["revision"], content=source["content"])
        phase = service.add_phase(
            session, segment["id"], 2, start_frame=60, content=segment["phases"][-1]["content"]
        )
        before = service.phases(session, segment["id"])
        moved = service.update_cut(session, original["id"], 1, frame=60)
        assert service.phases(session, segment["id"]) == before
        assert any(p["id"] == phase["id"] for p in before)
        service.undo(session, moved["receipt"]["id"])
        assert service.phases(session, segment["id"]) == before


def test_move_collision_and_intervening_phase_edit_refuse_without_writes(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        original = cut(service, session, segment)
        service.add_phase(
            session,
            segment["id"],
            2,
            start_frame=60,
            content={"description": "A different moment", "screen_direction": "", "sound": ""},
        )
        fail_without_write(
            session, 409, "phase", lambda: service.update_cut(session, original["id"], 1, frame=60)
        )
        moved = service.update_cut(session, original["id"], 1, frame=72)
        phase = service.phases(session, segment["id"])[-1]
        service.update_phase(
            session,
            phase["id"],
            phase["revision"],
            content={"description": "Later", "screen_direction": "", "sound": ""},
        )
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, moved["receipt"]["id"])
        )


def test_move_transaction_rollback(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        original = cut(service, session, segment)
        before = deepcopy(snapshot(session))
    with pytest.raises(RuntimeError), db.transaction() as session:
        service.update_cut(session, original["id"], 1, frame=72)
        raise RuntimeError("abort")
    with db.transaction() as session:
        assert snapshot(session) == before


@pytest.mark.parametrize("dependency", ["keyframe", "suitability"])
def test_remove_preserves_dependent_history(store, dependency):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        original = cut(service, session, segment)
        evidence = service.record_evidence(session, original["to_angle_id"], "imported", "view")
        if dependency == "keyframe":
            service.record_keyframe(session, original["id"], "pin", evidence["id"])
        else:
            service.judge_suitability(
                session,
                original["id"],
                reference_kind="view",
                reference_id=evidence["id"],
                verdict="suits",
            )
        fail_without_write(
            session, 409, dependency, lambda: service.remove_cut(session, original["id"], 2)
        )


def test_cut_chain_guards_create_retarget_move_and_remove(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        first = cut(service, session, segment)
        place_id = service.read_angle(session, first["to_angle_id"])["place_id"]
        third = service.add_angle(session, place_id, "Third camera")
        fail_without_write(
            session,
            422,
            "Cuts chain",
            lambda: service.create_cut(
                session,
                segment["id"],
                2,
                frame=72,
                from_angle_id=first["from_angle_id"],
                to_angle_id=first["to_angle_id"],
            ),
        )
        second = service.create_cut(
            session,
            segment["id"],
            2,
            frame=72,
            from_angle_id=first["to_angle_id"],
            to_angle_id=third["id"],
        )
        fail_without_write(
            session,
            422,
            "Cuts chain",
            lambda: service.update_cut(
                session, second["id"], 1, from_angle_id=first["from_angle_id"]
            ),
        )
        fail_without_write(
            session,
            422,
            "Cuts chain",
            lambda: service.update_cut(session, second["id"], 1, frame=24),
        )
        removed = service.remove_cut(session, second["id"], 3)
        service.undo(session, removed["receipt"]["id"])
        assert [c["id"] for c in service.cuts(session, segment["id"])] == [
            first["id"],
            second["id"],
        ]
