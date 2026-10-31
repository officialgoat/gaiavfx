"""The Direction journal, one table over every authored command: Undo puts every Direction row back exactly (only
revisions move forward), undoing the Undo redoes exactly, and a later edit of a row the command changed refuses the
Undo without writing."""

import pytest
from test_direction_split import seed, tables

from gaiavfx.shared.errors import ApplicationError

SHOT = {"description": "A new beat", "screen_direction": "", "sound": "", "cast": ["a"]}


def first_cut(service, session, segment):
    return service.read_segment(session, segment["id"])["cuts"][0]


def movie_revision(service, session, segment):
    return service.read_movie(session, segment["movie_id"])["revision"]


COMMANDS = {
    "update_movie": lambda s, x, seg: s.update_movie(
        x, seg["movie_id"], movie_revision(s, x, seg), title="Renamed"
    ),
    "add_subject": lambda s, x, seg: s.add_subject(
        x,
        seg["movie_id"],
        movie_revision(s, x, seg),
        name="Cy",
        description="A runner",
        keeps=["scarf"],
        identity_asset_id=None,
    ),
    "update_subject": lambda s, x, seg: s.update_subject(
        x, "unused", s.read_subject(x, "unused")["revision"], description="Changed"
    ),
    "remove_subject": lambda s, x, seg: s.remove_subject(x, "spare", movie_revision(s, x, seg)),
    "add_segment": lambda s, x, seg: s.add_segment(
        x,
        seg["movie_id"],
        movie_revision(s, x, seg),
        content={**seg["content"], "events": [], "speech": [], "states": []},
        delivered_frames=48,
        continues=False,
        phases=[(0, SHOT)],
        at=0,
    ),
    "remove_segment": lambda s, x, seg: s.remove_segment(
        x, last_segment(s, x, seg)["id"], movie_revision(s, x, seg)
    ),
    "update_segment": lambda s, x, seg: s.update_segment(
        x, seg["id"], s.read_segment(x, seg["id"])["revision"], delivered_frames=110
    ),
    "move_segment": lambda s, x, seg: s.move_segment(
        x, seg["id"], movie_revision(s, x, seg), ordinal=1
    ),
    "split_segment": lambda s, x, seg: s.split_segment(
        x, seg["id"], s.read_segment(x, seg["id"])["revision"], 45
    ),
    "add_phase": lambda s, x, seg: s.add_phase(
        x, seg["id"], s.read_segment(x, seg["id"])["revision"], start_frame=50, content=SHOT
    ),
    "update_phase": lambda s, x, seg: s.update_phase(
        x, seg["phases"][1]["id"], seg["phases"][1]["revision"], start_frame=40
    ),
    "remove_phase": lambda s, x, seg: s.remove_phase(
        x, seg["phases"][1]["id"], s.read_segment(x, seg["id"])["revision"]
    ),
    "create_cut": lambda s, x, seg: s.create_cut(
        x,
        last_segment(s, x, seg)["id"],
        last_segment(s, x, seg)["revision"],
        frame=30,
        from_angle_id=first_cut(s, x, seg)["to_angle_id"],
        to_angle_id=first_cut(s, x, seg)["from_angle_id"],
    ),
    "update_cut": lambda s, x, seg: s.update_cut(
        x,
        first_cut(s, x, seg)["id"],
        first_cut(s, x, seg)["revision"],
        relation="reverse",
        crosses_barrier=False,
        relation_source="authored",
    ),
    "move_cut": lambda s, x, seg: s.update_cut(
        x, first_cut(s, x, seg)["id"], first_cut(s, x, seg)["revision"], frame=20
    ),
    "remove_cut": lambda s, x, seg: s.remove_cut(
        x, seg["cuts"][-1]["id"], s.read_segment(x, seg["id"])["revision"]
    ),
    "update_place": lambda s, x, seg: s.update_place(
        x,
        place(s, x, seg)["id"],
        place(s, x, seg)["revision"],
        description="Empty room, wooden door",
        movie_id=seg["movie_id"],
    ),
    "update_angle": lambda s, x, seg: s.update_angle(
        x,
        first_cut(s, x, seg)["to_angle_id"],
        s.read_angle(x, first_cut(s, x, seg)["to_angle_id"])["revision"],
        description="The empty doorway",
        movie_id=seg["movie_id"],
    ),
}


# A command that needs one more record to act on gets it before the history it is judged against.
PREPARE = {
    "remove_subject": lambda s, x, seg: s.add_subject(
        x,
        seg["movie_id"],
        movie_revision(s, x, seg),
        subject_id="spare",
        name="Spare",
        description="Leaves",
        keeps=[],
        identity_asset_id=None,
    ),
}


def last_segment(service, session, segment):
    return service.read_movie(session, segment["movie_id"])["segments"][-1]


def place(service, session, segment):
    angle = service.read_angle(session, first_cut(service, session, segment)["to_angle_id"])
    return service.read_place(session, angle["place_id"])


@pytest.mark.parametrize("command", sorted(COMMANDS))
def test_undo_puts_every_row_back_and_undoing_it_redoes(store, command):
    db, service = store
    with db.transaction() as session:
        segment = seed(service, session)
        if command in PREPARE:
            PREPARE[command](service, session, segment)
        before = tables(session, revisions=False, receipts=False)
        done = COMMANDS[command](service, session, segment)
        after = tables(session, revisions=False, receipts=False)
        assert after != before
        inverse = service.undo(session, done["receipt"]["id"])
        assert tables(session, revisions=False, receipts=False) == before
        redone = service.undo(session, inverse["id"])
        assert tables(session, revisions=False, receipts=False) == after
        service.undo(session, redone["id"])
        assert tables(session, revisions=False, receipts=False) == before


@pytest.mark.parametrize(
    "command", ["update_movie", "update_segment", "add_phase", "update_cut", "update_place"]
)
def test_a_later_edit_refuses_the_undo_without_writing(store, command):
    db, service = store
    with db.transaction() as session:
        segment = seed(service, session)
        done = COMMANDS[command](service, session, segment)
        if command == "add_phase":
            service.update_phase(session, done["id"], done["revision"], content=SHOT)
        else:
            # The same command again is a later edit of the same rows.
            COMMANDS[command](service, session, service.read_segment(session, segment["id"]))
        before = tables(session)
        with pytest.raises(ApplicationError, match="changed after") as refused:
            service.undo(session, done["receipt"]["id"])
        assert refused.value.status == 409
        assert tables(session) == before


def test_undo_and_redo_targets_follow_the_history(store):
    db, service = store
    with db.transaction() as session:
        segment = seed(service, session)
        movie_id = segment["movie_id"]
        first = COMMANDS["update_movie"](service, session, segment)
        second = COMMANDS["update_segment"](service, session, segment)
        assert service.undo_targets(session, movie_id) == {
            "undo": second["receipt"]["id"],
            "redo": None,
        }
        undone = service.undo(session, second["receipt"]["id"])
        assert service.undo_targets(session, movie_id) == {
            "undo": first["receipt"]["id"],
            "redo": undone["id"],
        }
        redone = service.undo(session, undone["id"])
        assert service.undo_targets(session, movie_id) == {"undo": redone["id"], "redo": None}
        again = service.undo(session, redone["id"])
        # A new edit ends what could be redone.
        third = COMMANDS["add_phase"](
            service, session, service.read_segment(session, segment["id"])
        )
        assert service.undo_targets(session, movie_id) == {
            "undo": third["receipt"]["id"],
            "redo": None,
        }
        assert again["id"] != third["receipt"]["id"]
