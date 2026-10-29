"""Revision-checked Direction commands, structural identity, receipts and real Undo."""

from copy import deepcopy

import pytest
from sqlalchemy import select, update
from test_direction_store import make_segment

from gaiavfx.infrastructure.database import Database
from gaiavfx.modules.direction.persistence import (
    CutRecord,
    MovieRecord,
    PhaseRecord,
    ReceiptRecord,
    SegmentRecord,
    SubjectRecord,
)
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.shared.errors import ApplicationError

SHOT = {"description": "Wait.", "screen_direction": "", "sound": ""}
MODELS = (MovieRecord, SubjectRecord, SegmentRecord, PhaseRecord, CutRecord, ReceiptRecord)


@pytest.fixture
def store(tmp_path):
    # Commands use real SQLite and enabled FKs. The migration has its own round-trip tests;
    # replaying the legacy migration chain for every command case adds no coverage.
    db = Database(tmp_path)
    metadata = MovieRecord.metadata
    metadata.create_all(
        db.engine,
        tables=[table for table in metadata.sorted_tables if table.name.startswith("direction_")],
    )
    try:
        yield db, DirectionService()
    finally:
        db.close()


def snapshot(session):
    return {
        model.__tablename__: [
            deepcopy({c.key: getattr(row, c.key) for c in model.__table__.columns})
            for row in session.scalars(select(model).order_by(model.id))
        ]
        for model in MODELS
    }


@pytest.mark.parametrize("command", ["update", "remove"])
def test_undo_legacy_receipts_restores_keeps_as_items(store, command):
    db, service = store
    with db.transaction() as session:
        movie = service.create_movie(session, "Legacy receipts")
        subject = service.add_subject(
            session,
            movie["id"],
            1,
            name="Mara",
            description="Walker",
            keeps=["coat"],
            identity_asset_id=None,
        )
        if command == "update":
            result = service.update_subject(session, subject["id"], 1, keeps=["hat", "bag"])
        else:
            result = service.remove_subject(session, subject["id"], 2)
        receipt = session.get(ReceiptRecord, result["receipt"]["id"])
        before = deepcopy(receipt.before)
        target = before if command == "update" else before["record"]
        target["keeps"] = "coat and scarf"
        receipt.before = before
        session.flush()
        service.undo(session, receipt.id)
        assert service.read_subject(session, subject["id"])["keeps"] == ["coat and scarf"]
        assert receipt.before == before  # Historical evidence stays as written.


def receipt(result, kind, target):
    value = result["receipt"]
    assert value["target_kind"] == kind and value["target_id"] == target
    assert value["after"]["revision"] == value["before"]["revision"] + 1
    assert value["undoes"] is None and value["undone_at"] is None
    return value


def fail_without_write(session, status, message, command):
    before = snapshot(session)
    with pytest.raises(ApplicationError, match=message) as error:
        command()
    assert error.value.status == status
    assert snapshot(session) == before


def cut(service, session, segment, frame=36):
    place = service.create_place(session, "Courtyard")
    angle = service.add_angle(session, place["id"], "Front")
    target_angle = service.add_angle(session, place["id"], "Closer view")
    return service.create_cut(
        session,
        segment["id"],
        segment["revision"],
        frame=frame,
        from_angle_id=angle["id"],
        to_angle_id=target_angle["id"],
    )


def add_segment(service, session, segment, **changes):
    movie = service.read_movie(session, segment["movie_id"])
    return service.add_segment(
        session,
        movie["id"],
        movie["revision"],
        **{
            "content": segment["content"],
            "delivered_frames": 102,
            "continues": True,
            **changes,
        },
    )


def test_structure_parent_revisions_and_reader_order(store):
    db, service = store
    with db.transaction() as session:
        movie = service.create_movie(session, "Film")
        receipt(movie, "movie", movie["id"])
        subject = service.add_subject(
            session,
            movie["id"],
            1,
            subject_id="s1",
            name="Arin",
            description="A walker",
            keeps="coat",
            identity_asset_id=None,
        )
        receipt(subject, "movie", movie["id"])
        content = {
            "subjects": ["s1"],
            "place": {"noun": "street", "description": "A street.", "landmarks": ""},
        }
        first = service.add_segment(
            session, movie["id"], 2, content=content, delivered_frames=102, continues=True
        )
        second = service.add_segment(
            session, movie["id"], 3, content=content, delivered_frames=102, continues=True
        )
        for segment in (first, second):
            receipt(segment, "movie", movie["id"])
        assert service.read_movie(session, movie["id"])["revision"] == 4
        phase = service.add_phase(session, first["id"], 1, start_frame=36, content=SHOT)
        receipt(phase, "segment", first["id"])
        phase0 = service.add_phase(session, first["id"], 2, start_frame=0, content=SHOT)
        receipt(phase0, "segment", first["id"])
        pin = cut(service, session, service.read_segment(session, first["id"]))
        receipt(pin, "segment", first["id"])
        assert "movie_id" not in service.read_cut(session, pin["id"])
        movie_row = service.read_movie(session, movie["id"])
        assert [s["ordinal"] for s in movie_row["segments"]] == [0, 1]
        assert [p["start_frame"] for p in movie_row["segments"][0]["phases"]] == [0, 36]
        assert movie_row["segments"][0]["cuts"][0]["id"] == pin["id"]
        assert "receipt" not in movie_row and "receipt" not in service.read_subject(session, "s1")
        later = service.add_subject(
            session,
            movie["id"],
            4,
            name="Bea",
            description="A runner",
            keeps="",
            identity_asset_id="picture",
        )
        assert [s["id"] for s in service.subjects(session, movie["id"])] == ["s1", later["id"]]
        history = service.receipts(session, movie["id"], limit=2)
        assert [r["id"] for r in history] == [later["receipt"]["id"], pin["receipt"]["id"]]
        assert service.read_receipt(session, pin["receipt"]["id"]) == pin["receipt"]
        other_movie = service.create_movie(session, "Second film")
        assert [m["id"] for m in service.movies(session)] == [other_movie["id"], movie["id"]]


@pytest.mark.parametrize(
    "command",
    [
        "update_movie",
        "add_subject",
        "update_subject",
        "remove_subject",
        "add_segment",
        "update_segment",
        "remove_segment",
        "move_segment",
        "add_phase",
        "update_phase",
        "remove_phase",
        "create_cut",
        "update_cut",
    ],
)
def test_every_command_refuses_a_stale_revision_without_a_row_change(store, command):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        subject_id = segment["content"]["subjects"][0]
        phase_id = segment["phases"][0]["id"]
        stored_cut = cut(service, session, segment)
        service.update_cut(session, stored_cut["id"], 1, frame=48)
        service.update_subject(session, subject_id, 1, keeps="hat")
        service.update_phase(session, phase_id, 1, content=SHOT)
        movie_id = segment["movie_id"]
        calls = {
            "update_movie": lambda: service.update_movie(session, movie_id, 1, title="Stale"),
            "add_subject": lambda: service.add_subject(
                session,
                movie_id,
                1,
                name="Bea",
                description="Runner",
                keeps="",
                identity_asset_id=None,
            ),
            "update_subject": lambda: service.update_subject(session, subject_id, 1, keeps="stale"),
            "remove_subject": lambda: service.remove_subject(session, subject_id, 1),
            "add_segment": lambda: service.add_segment(
                session,
                movie_id,
                1,
                content=segment["content"],
                delivered_frames=102,
                continues=True,
            ),
            "update_segment": lambda: service.update_segment(
                session, segment["id"], 1, continues=False
            ),
            "remove_segment": lambda: service.remove_segment(session, segment["id"], 1),
            "move_segment": lambda: service.move_segment(session, segment["id"], 1, ordinal=0),
            "add_phase": lambda: service.add_phase(
                session, segment["id"], 1, start_frame=60, content=SHOT
            ),
            "update_phase": lambda: service.update_phase(session, phase_id, 1, content=SHOT),
            "remove_phase": lambda: service.remove_phase(session, phase_id, 1),
            "create_cut": lambda: service.create_cut(
                session,
                segment["id"],
                1,
                frame=60,
                from_angle_id=stored_cut["from_angle_id"],
                to_angle_id=stored_cut["to_angle_id"],
            ),
            "update_cut": lambda: service.update_cut(session, stored_cut["id"], 1, frame=60),
        }
        fail_without_write(session, 409, "revision is stale", calls[command])


def test_compare_and_set_catches_a_change_after_the_row_was_loaded(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        phase = session.get(PhaseRecord, segment["phases"][0]["id"])
        # Keep the ORM object at revision 1 while advancing the database revision.
        session.execute(
            update(PhaseRecord)
            .where(PhaseRecord.id == phase.id)
            .values(revision=2)
            .execution_options(synchronize_session=False)
        )
        assert phase.revision == 1
        before = service.receipts(session, segment["movie_id"])
        with pytest.raises(ApplicationError, match="revision is stale") as error:
            service.update_phase(session, phase.id, 1, content=SHOT)
        assert error.value.status == 409
        session.refresh(phase)
        assert phase.revision == 2 and phase.content == segment["phases"][0]["content"]
        assert service.receipts(session, segment["movie_id"]) == before


def test_subject_ids_membership_and_reference_removal(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        movie = service.read_movie(session, segment["movie_id"])
        member = movie["subjects"][0]
        other_movie = service.create_movie(session, "Other")
        for target in (movie, other_movie):
            fail_without_write(
                session,
                409,
                "already exists",
                lambda target=target: service.add_subject(
                    session,
                    target["id"],
                    target["revision"],
                    subject_id=member["id"],
                    name="Duplicate",
                    description="Person",
                    keeps="",
                    identity_asset_id=None,
                ),
            )
        fail_without_write(
            session,
            422,
            "Subject missing",
            lambda: add_segment(
                service, session, segment, content={**segment["content"], "subjects": ["missing"]}
            ),
        )
        fail_without_write(
            session,
            409,
            "appears in segment 0",
            lambda: service.remove_subject(session, member["id"], movie["revision"]),
        )
        replacement = service.add_subject(
            session,
            movie["id"],
            movie["revision"],
            name="Bea",
            description="Runner",
            keeps="",
            identity_asset_id=None,
        )
        service.update_segment(
            session,
            segment["id"],
            1,
            content={**segment["content"], "subjects": [replacement["id"]]},
        )
        removed = service.remove_subject(
            session, member["id"], replacement["receipt"]["after"]["revision"]
        )
        restored = service.undo(session, removed["receipt"]["id"])
        assert restored["record"] == member
        assert service.read_subject(session, member["id"]) == member
        redone = service.undo(session, restored["id"])
        assert redone["record"] == {"id": member["id"], "removed": True}


def test_cut_moves_preserve_phase_timing_and_refuse_invalid_frames(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        stored_cut = cut(service, session, segment)
        angles = {key: stored_cut[key] for key in ("from_angle_id", "to_angle_id")}
        for frame, status in ((0, 422), (102, 422), (36, 409)):
            fail_without_write(
                session,
                status,
                str(frame),
                lambda frame=frame: service.create_cut(
                    session, segment["id"], 2, frame=frame, **angles
                ),
            )
        fail_without_write(
            session,
            422,
            "36",
            lambda: service.update_segment(session, segment["id"], 2, delivered_frames=30),
        )
        fail_without_write(
            session,
            409,
            "Remove the segment's cuts first",
            lambda: service.remove_segment(session, segment["id"], 3),
        )
        assembled, _ = service.assemble_segment(session, segment["id"])
        assert assembled.cut_frames == (36,)
        changed = service.update_cut(session, stored_cut["id"], 1, frame=48)
        receipt(changed, "cut", stored_cut["id"])
        assert [p["start_frame"] for p in service.phases(session, segment["id"])] == [0, 48]
        assembled, _ = service.assemble_segment(session, segment["id"])
        assert assembled.cut_frames == (48,)
        fail_without_write(
            session,
            422,
            "segment_id",
            lambda: service.update_cut(session, stored_cut["id"], 2, segment_id="other"),
        )
        other = service.create_cut(
            session,
            segment["id"],
            3,
            frame=60,
            from_angle_id=angles["to_angle_id"],
            to_angle_id=angles["from_angle_id"],
        )
        fail_without_write(
            session, 409, "48", lambda: service.update_cut(session, other["id"], 1, frame=48)
        )
        assert [c["frame"] for c in service.cuts(session, segment["id"])] == [48, 60]


def test_incomplete_authoring_and_schema_errors_are_typed(store):
    db, service = store
    with db.transaction() as session:
        first = make_segment(service, session)
        segment = add_segment(service, session, first, phases=[])
        with pytest.raises(ApplicationError, match="at least one phase") as error:
            service.assemble_segment(session, segment["id"])
        assert error.value.status == 422
        service.add_phase(session, segment["id"], 1, start_frame=12, content=SHOT)
        with pytest.raises(ApplicationError, match="12"):
            service.assemble_segment(session, segment["id"])
        session.get(SegmentRecord, segment["id"]).content_schema = 3
        session.flush()
        with pytest.raises(ApplicationError, match="schema 3") as error:
            service.assemble_segment(session, segment["id"])
        assert error.value.status == 422


def test_schema_two_edits_use_existing_commands_and_undo(store):
    db, service = store
    with db.transaction() as session:
        original = make_segment(service, session)
        subject_id = original["content"]["subjects"][0]
        phase = original["phases"][0]
        segment_edit = service.update_segment(
            session,
            original["id"],
            1,
            content={
                **original["content"],
                "style": "Handheld.",
                "speech": [
                    {"speaker_id": subject_id, "at_frame": 0, "text": "Hi ", "language": "English"},
                ],
            },
        )
        phase_edit = service.update_phase(
            session,
            phase["id"],
            1,
            content={**phase["content"], "cast": [subject_id]},
        )
        assert segment_edit["content_schema"] == phase_edit["content_schema"] == 2
        assembled, _ = service.assemble_segment(session, original["id"])
        assert assembled.style == "Handheld." and assembled.speech[0].text == "Hi "
        assert assembled.shots[0].cast == (subject_id,)
        service.undo(session, phase_edit["receipt"]["id"])
        service.undo(session, segment_edit["receipt"]["id"])
        restored, _ = service.assemble_segment(session, original["id"])
        assert restored.style == "" and restored.speech == ()
        assert restored.shots[0].cast is None


def test_remove_and_undo_preserve_schema_two_without_optional_fields(store):
    db, service = store
    with db.transaction() as session:
        original = make_segment(service, session)
        # Explicit edits use the current schema even when the optional fields are absent.
        service.update_segment(session, original["id"], 1, content=original["content"])
        for phase in original["phases"]:
            service.update_phase(session, phase["id"], 1, content=phase["content"])
        before = service.read_segment(session, original["id"])
        assert before["content_schema"] == 2
        assert all(phase["content_schema"] == 2 for phase in before["phases"])
        movie = service.read_movie(session, original["movie_id"])
        removed = service.remove_segment(session, original["id"], movie["revision"])
        restored = service.undo(session, removed["receipt"]["id"])
        assert restored["record"] == before


def test_move_insert_remove_renumber_without_unique_collisions(store):
    db, service = store
    with db.transaction() as session:
        first = make_segment(service, session)
        second = add_segment(service, session, first)
        third = add_segment(service, session, first)
        moved = service.move_segment(session, first["id"], 5, ordinal=2)
        assert moved["ordinal"] == 2
        assert [s["id"] for s in service.read_movie(session, first["movie_id"])["segments"]] == [
            second["id"],
            third["id"],
            first["id"],
        ]
        inverse = service.undo(session, moved["receipt"]["id"])
        assert inverse["record"]["ordinal"] == 0
        redone = service.undo(session, inverse["id"])
        assert redone["record"]["ordinal"] == 2
        inserted = add_segment(service, session, first, at=1)
        removed = service.remove_segment(session, inserted["id"], 9)
        receipt(removed, "movie", first["movie_id"])
        assert [
            s["ordinal"] for s in service.read_movie(session, first["movie_id"])["segments"]
        ] == [0, 1, 2]


def test_phase_edit_undo_redo_receipts_are_new_and_revisions_advance(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        phase = segment["phases"][0]
        changed = service.update_phase(session, phase["id"], 1, content=SHOT)
        old_receipt = deepcopy(changed["receipt"])
        restored = service.undo(session, old_receipt["id"])
        assert restored["record"]["content"] == phase["content"]
        assert restored["record"]["revision"] == 3
        assert restored["command"] == "undo" and restored["undoes"] == old_receipt["id"]
        saved_receipt = service.read_receipt(session, old_receipt["id"])
        assert saved_receipt["undone_at"]
        assert saved_receipt == {**old_receipt, "undone_at": saved_receipt["undone_at"]}
        redone = service.undo(session, restored["id"])
        assert redone["record"]["content"] == SHOT and redone["record"]["revision"] == 4
        assert redone["undoes"] == restored["id"]
        fail_without_write(
            session, 409, "already undone", lambda: service.undo(session, old_receipt["id"])
        )
        service.update_phase(session, phase["id"], 4, start_frame=12)
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, redone["id"])
        )
        history = service.receipts(session, segment["movie_id"])
        assert len(history) == 7  # movie, subject, segment, edit, undo, redo, later edit


def test_subject_edit_and_nullable_identity_undo(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        subject_id = segment["content"]["subjects"][0]
        changed = service.update_subject(
            session, subject_id, 1, keeps="hat", identity_asset_id="picture"
        )
        result = service.undo(session, changed["receipt"]["id"])
        assert result["record"]["revision"] == 3 and result["record"]["keeps"] == ["coat"]
        assert result["record"]["identity_asset_id"] is None
        service.undo(session, result["id"])
        cleared = service.update_subject(session, subject_id, 4, identity_asset_id=None)
        assert cleared["identity_asset_id"] is None
        fail_without_write(
            session, 422, "at least one", lambda: service.update_subject(session, subject_id, 5)
        )


def test_remove_phase_and_segment_restore_all_ids_and_metadata(store):
    db, service = store
    with db.transaction() as session:
        first = make_segment(service, session)
        second = add_segment(service, session, first)
        phase = first["phases"][1]
        removed = service.remove_phase(session, phase["id"], 1)
        inverse = service.undo(session, removed["receipt"]["id"])
        assert inverse["record"] == phase
        before = service.read_segment(session, first["id"])
        removed = service.remove_segment(session, first["id"], 4)
        assert service.read_segment(session, second["id"])["ordinal"] == 0
        inverse = service.undo(session, removed["receipt"]["id"])
        assert inverse["record"] == before
        assert service.read_segment(session, second["id"])["ordinal"] == 1
        redone = service.undo(session, inverse["id"])
        assert redone["record"] == {"id": first["id"], "removed": True}
        assert service.read_segment(session, second["id"])["ordinal"] == 0


@pytest.mark.parametrize("kind", ["subject", "segment", "phase", "cut"])
def test_undo_creation_and_redo_preserve_identity(kind, store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        if kind == "subject":
            result = service.add_subject(
                session,
                segment["movie_id"],
                3,
                name="Bea",
                description="Runner",
                keeps="hat",
                identity_asset_id=None,
            )
        elif kind == "segment":
            result = add_segment(service, session, segment, phases=[(0, SHOT)])
        elif kind == "phase":
            result = service.add_phase(session, segment["id"], 1, start_frame=60, content=SHOT)
        else:
            result = cut(service, session, segment)
        before = {key: value for key, value in result.items() if key != "receipt"}
        inverse = service.undo(session, result["receipt"]["id"])
        assert inverse["record"] == {"id": result["id"], "removed": True}
        redo = service.undo(session, inverse["id"])
        assert redo["record"] == before
        assert redo["command"] == "undo"


@pytest.mark.parametrize("kind", ["subject", "segment", "phase", "cut"])
@pytest.mark.parametrize("legacy", [False, True])
def test_creation_undo_protects_edited_record_and_allows_ordered_undo(store, kind, legacy):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        if kind == "subject":
            created = service.add_subject(
                session,
                segment["movie_id"],
                3,
                name="Bea",
                description="Runner",
                keeps=[],
                identity_asset_id=None,
            )
            changed = service.update_subject(session, created["id"], 1, name="Updated")
        elif kind == "segment":
            created = segment
            changed = service.update_segment(session, created["id"], 1, delivered_frames=120)
        elif kind == "phase":
            created = service.add_phase(session, segment["id"], 1, start_frame=60, content=SHOT)
            changed = service.update_phase(
                session, created["id"], 1, content={**SHOT, "description": "Run."}
            )
        else:
            created = cut(service, session, segment)
            # Exercise child-only edits here; a frame move now also changes the
            # segment/phase revisions and has its own guarded inverse tests.
            changed = service.update_cut(session, created["id"], 1, fidelity=True)
        original = deepcopy(created["receipt"])
        assert original["after"]["created_revision"] == 1
        assert original["after"]["created_kind"] == kind
        if legacy:
            stored = session.get(ReceiptRecord, original["id"])
            stored.after = {
                key: value
                for key, value in stored.after.items()
                if key not in {"created_revision", "created_kind"}
            }
            session.flush()
        else:
            fail_without_write(
                session,
                409,
                "The record changed after this command; undo the later command first.",
                lambda: service.undo(session, original["id"]),
            )
            inverse = service.undo(session, changed["receipt"]["id"])
            assert inverse["record"]["revision"] == 3
            assert service.read_receipt(session, original["id"]) == original
        removed = service.undo(session, original["id"])
        assert removed["record"] == {"id": created["id"], "removed": True}


def test_segment_creation_undo_protects_initial_phase_edits(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        phase = segment["phases"][0]
        changed = service.update_phase(session, phase["id"], 1, content=SHOT)
        # Phase content owns its revision; the segment's own revision stays 1.
        assert service.read_segment(session, segment["id"])["revision"] == 1
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, segment["receipt"]["id"])
        )
        inverse = service.undo(session, changed["receipt"]["id"])
        assert inverse["record"]["revision"] == 3
        removed = service.undo(session, segment["receipt"]["id"])
        assert removed["record"] == {"id": segment["id"], "removed": True}


def test_creation_guard_does_not_mistake_redo_or_same_value_edits_for_undo(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        phase = segment["phases"][0]
        changed = service.update_phase(session, phase["id"], 1, content=SHOT)
        inverse = service.undo(session, changed["receipt"]["id"])
        redone = service.undo(session, inverse["id"])
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, segment["receipt"]["id"])
        )
        service.undo(session, redone["id"])
        # A fresh normal edit still blocks deletion, even when it restores identical prose.
        changed = service.update_phase(session, phase["id"], 5, content=phase["content"])
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, segment["receipt"]["id"])
        )
        service.undo(session, changed["receipt"]["id"])
        assert service.undo(session, segment["receipt"]["id"])["record"]["removed"]


def test_creation_undo_allows_reversed_child_commands_and_keeps_existing_phase(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        created_cut = cut(service, session, segment)
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, segment["receipt"]["id"])
        )
        service.undo(session, created_cut["receipt"]["id"])
        current = service.read_segment(session, segment["id"])
        assert current["revision"] == 3 and current["phases"] == segment["phases"]
        phase = service.add_phase(session, segment["id"], 3, start_frame=60, content=SHOT)
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, segment["receipt"]["id"])
        )
        service.undo(session, phase["receipt"]["id"])
        assert service.undo(session, segment["receipt"]["id"])["record"]["removed"]


@pytest.mark.parametrize("legacy", [False, True])
def test_creation_guard_requires_parent_undo_with_legacy_strict_revisions(store, legacy):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        if legacy:
            original = session.get(ReceiptRecord, segment["receipt"]["id"])
            original.after = {
                key: value
                for key, value in original.after.items()
                if key not in {"created_revision", "created_kind"}
            }
            session.flush()
        subject = service.add_subject(
            session,
            segment["movie_id"],
            3,
            name="Bea",
            description="Runner",
            keeps=[],
            identity_asset_id=None,
        )
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, segment["receipt"]["id"])
        )
        service.undo(session, subject["receipt"]["id"])
        if legacy:
            fail_without_write(
                session,
                409,
                "changed after",
                lambda: service.undo(session, segment["receipt"]["id"]),
            )
        else:
            assert service.undo(session, segment["receipt"]["id"])["record"]["removed"]


def test_storyline_creations_undo_in_order_with_monotonic_parent_revisions(store):
    db, service = store
    with db.transaction() as session:
        first = make_segment(service, session)
        second = add_segment(service, session, first)
        third = add_segment(service, session, first)
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, first["receipt"]["id"])
        )
        removed = service.undo(session, third["receipt"]["id"])
        assert service.read_movie(session, first["movie_id"])["revision"] == 6
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, first["receipt"]["id"])
        )
        restored = service.undo(session, removed["id"])
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, second["receipt"]["id"])
        )
        service.undo(session, restored["id"])
        service.undo(session, second["receipt"]["id"])
        movie = service.read_movie(session, first["movie_id"])
        # A no-op ordinary edit is still a new command; matching data alone does not count.
        edit = service.update_movie(session, movie["id"], movie["revision"], title=movie["title"])
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, first["receipt"]["id"])
        )
        service.undo(session, edit["receipt"]["id"])
        service.undo(session, first["receipt"]["id"])
        assert not service.read_movie(session, first["movie_id"])["segments"]


def test_guard_does_not_reuse_an_old_inverse_after_delete_and_restore(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        phase = segment["phases"][0]
        changed = service.update_phase(session, phase["id"], 1, content=SHOT)
        service.undo(session, changed["receipt"]["id"])
        removed = service.undo(session, segment["receipt"]["id"])
        restored = service.undo(session, removed["id"])
        current = restored["record"]["phases"][0]
        first = service.update_phase(session, phase["id"], current["revision"], content=SHOT)
        service.update_phase(session, phase["id"], first["revision"], content=current["content"])
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, restored["id"])
        )


def test_undo_refuses_movie_creation_referenced_subjects_and_recorded_keyframes(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        movie_id = segment["movie_id"]
        commands = service.receipts(session, movie_id)
        created_movie = next(r for r in commands if r["command"] == "create_movie")
        created_subject = next(r for r in commands if r["command"] == "add_subject")
        fail_without_write(
            session, 409, "movie is not removed", lambda: service.undo(session, created_movie["id"])
        )
        fail_without_write(
            session, 409, "changed after", lambda: service.undo(session, created_subject["id"])
        )
        # Adding a subject advances the movie; referencing it via an update only advances the segment.
        subject = service.add_subject(
            session, movie_id, 3, name="Bea", description="Runner", keeps="", identity_asset_id=None
        )
        updated = service.update_segment(
            session, segment["id"], 1, content={**segment["content"], "subjects": [subject["id"]]}
        )
        fail_without_write(
            session,
            409,
            "appears in segment 0",
            lambda: service.undo(session, subject["receipt"]["id"]),
        )
        pin = cut(service, session, updated)
        evidence = service.record_evidence(session, pin["to_angle_id"], "imported", "view")
        keyframe = service.record_keyframe(session, pin["id"], "pin", evidence["id"])
        fail_without_write(
            session, 409, "recorded keyframes", lambda: service.undo(session, pin["receipt"]["id"])
        )
        assert service.keyframes(session, pin["id"])[0]["id"] == keyframe["id"]


def test_transaction_rollback_includes_command_receipts(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        before = snapshot(session)
    with pytest.raises(ApplicationError), db.transaction() as session:
        service.update_phase(session, segment["phases"][0]["id"], 1, content=SHOT)
        service.update_segment(session, segment["id"], 1, content={})
    with db.transaction() as session:
        assert snapshot(session) == before


def test_undo_rolls_back_with_the_callers_transaction(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        changed = service.update_phase(session, segment["phases"][0]["id"], 1, content=SHOT)
        before = snapshot(session)
    with pytest.raises(ApplicationError), db.transaction() as session:
        # Undo is the first write, after a fresh SQLite connection's reads.
        service.undo(session, changed["receipt"]["id"])
        service.update_segment(session, segment["id"], 1, content={})
    with db.transaction() as session:
        assert snapshot(session) == before


@pytest.mark.parametrize("expected", [0, -1, True, "1", 1.0, None])
def test_revision_is_a_positive_integer(store, expected):
    db, service = store
    with db.transaction() as session:
        movie = service.create_movie(session, "Film")
        fail_without_write(
            session,
            422,
            "positive integer",
            lambda: service.update_movie(session, movie["id"], expected, title="Changed"),
        )


def test_empty_and_invalid_updates_leave_no_receipt(store):
    db, service = store
    with db.transaction() as session:
        segment = make_segment(service, session)
        for changes in ({}, {"continues": 1}, {"delivered_frames": True}, {"content": {}}):
            fail_without_write(
                session,
                422,
                ".",
                lambda changes=changes: service.update_segment(
                    session, segment["id"], 1, **changes
                ),
            )
        for changes, status in (
            ({}, 422),
            ({"start_frame": 36}, 409),
            ({"start_frame": 102}, 422),
            ({"content": {}}, 422),
        ):
            fail_without_write(
                session,
                status,
                ".",
                lambda changes=changes: service.update_phase(
                    session, segment["phases"][0]["id"], 1, **changes
                ),
            )
        fail_without_write(
            session,
            409,
            "36",
            lambda: service.add_phase(session, segment["id"], 1, start_frame=36, content=SHOT),
        )
        fail_without_write(
            session,
            422,
            "ordinal",
            lambda: service.move_segment(session, segment["id"], 3, ordinal=1),
        )
        fail_without_write(session, 422, "at", lambda: add_segment(service, session, segment, at=2))
        fail_without_write(session, 404, "not found", lambda: service.undo(session, "missing"))
