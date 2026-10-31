"""Movie structure: each rule a movie's rows hold, checked once, on rows in and a problem out."""

import json

import pytest

from gaiavfx.modules.direction import structure


def rows(
    *, cuts=(), phases=(0,), delivered=96, subjects=("mara",), cast=("mara",), pins=(), judged=()
):
    found = {
        ("segment", "s"): {
            "id": "s",
            "ordinal": 0,
            "delivered_frames": delivered,
            "content": json.dumps(
                {
                    "subjects": list(subjects),
                    "place": {"noun": "yard", "description": "A yard.", "landmarks": ""},
                }
            ),
            "content_schema": 1,
        }
    }
    for subject in cast:
        found[("subject", subject)] = {"id": subject, "movie_id": "m"}
    for index, frame in enumerate(phases):
        found[("phase", f"p{index}")] = {"id": f"p{index}", "segment_id": "s", "start_frame": frame}
    for index, (frame, source, target) in enumerate(cuts):
        found[("cut", f"c{index}")] = {
            "id": f"c{index}",
            "segment_id": "s",
            "frame": frame,
            "from_angle_id": source,
            "to_angle_id": target,
        }
    for cut in pins:
        found[("keyframe", f"k-{cut}")] = {"id": f"k-{cut}", "cut_id": cut}
    for cut in judged:
        found[("suitability", f"j-{cut}")] = {"id": f"j-{cut}", "cut_id": cut}
    return found


@pytest.mark.parametrize(
    ("found", "code", "status"),
    [
        (rows(), None, None),
        (rows(cuts=[(36, "a", "b"), (60, "b", "a")]), None, None),
        (rows(phases=(0, 96)), "frame_outside", 422),
        (rows(cuts=[(0, "a", "b")]), "frame_outside", 422),
        (rows(phases=(0, 12, 12)), "phase_frame_taken", 409),
        (rows(cuts=[(36, "a", "b"), (36, "b", "c")]), "cut_frame_taken", 409),
        (rows(cuts=[(36, "a", "b"), (60, "c", "d")]), "cut_chain", 422),
    ],
)
def test_a_segment_holds_its_frames_and_its_cut_chain(found, code, status):
    problem = structure.segment_problem(found, "s")
    assert (problem and (problem.code, problem.status)) == ((code, status) if code else None)


@pytest.mark.parametrize(
    ("found", "kind", "record_id", "code"),
    [
        (rows(), "subject", "mara", "subject_in_use"),
        (rows(subjects=()), "subject", "mara", None),
        (rows(cuts=[(36, "a", "b")]), "segment", "s", "segment_has_cuts"),
        (rows(), "segment", "s", None),
        (rows(cuts=[(36, "a", "b")], pins=["c0"], judged=["c0"]), "cut", "c0", "cut_has_keyframes"),
        (rows(cuts=[(36, "a", "b")], judged=["c0"]), "cut", "c0", "cut_has_suitability_history"),
        (rows(cuts=[(36, "a", "b")]), "cut", "c0", None),
    ],
)
def test_a_removal_leaves_nothing_hanging(found, kind, record_id, code):
    problem = structure.removal_problem(found, kind, record_id)
    assert (problem and problem.code) == code


def test_a_segment_with_cuts_names_them_in_frame_order():
    found = rows(cuts=[(60, "b", "a"), (36, "a", "b")])
    assert structure.removal_problem(found, "segment", "s").ids == ("c1", "c0")


def test_undo_words_a_removal_as_its_command_and_any_other_break_as_a_later_change():
    created_subject = structure.undo_problem(rows(cast=()), [("subject", "mara")])
    assert created_subject.code == "subject_in_use"
    later = structure.undo_problem(rows(phases=(0, 12, 12)), [])
    assert (later.code, later.message) == ("changed_after", structure.CHANGED_AFTER)
    uncast = structure.undo_problem(rows(cast=()), [])
    assert uncast.code == "changed_after"
    assert structure.undo_problem(rows(), [("phase", "gone")]) is None


def test_a_command_checks_the_rows_it_would_leave():
    found = rows(cuts=[(36, "a", "b")])
    moved = structure.with_row(
        found,
        "cut",
        "new",
        {"segment_id": "s", "frame": 36, "from_angle_id": "b", "to_angle_id": "c"},
    )
    assert structure.segment_problem(moved, "s").code == "cut_frame_taken"
    assert structure.segment_problem(structure.without(found, "cut", "c0"), "s") is None
