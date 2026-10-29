"""Model-free segment invariants and compatibility with existing positional constructors."""

from dataclasses import replace

import pytest

from gaiavfx.modules.direction.segment import (
    Event,
    MediaGuide,
    Phase,
    PlaceText,
    Segment,
    Shot,
    Speech,
    State,
    Subject,
)


@pytest.mark.parametrize(
    "keeps", ["coat", ["coat"], ("",), (" coat",), ("coat ",), (1,), ("coat", "coat")]
)
def test_subject_keeps_rejects_non_items_and_unstripped_or_duplicate_items(keeps):
    with pytest.raises(ValueError, match="keeps"):
        Subject("mara", "Mara", "A walker", keeps)


def test_subject_keeps_are_immutable_authored_items():
    assert Subject("mara", "Mara", "A walker").keeps == ()
    assert Subject("mara", "Mara", "A walker", ("coat", "bag")).keeps == ("coat", "bag")


def segment():
    return Segment(
        (Subject("mara", "Mara", "a walker"),),
        PlaceText("pier", "An empty pier.", ""),
        (Shot("She walks.", "", ""),),
        (),
        120,
        False,
    )


def test_positional_construction_keeps_additive_defaults():
    authored = segment()
    assert authored.subjects[0].keeps == ()
    assert authored.shots[0].cast is None
    assert authored.style == ""
    assert authored.phases == authored.events == authored.speech == authored.states == ()


@pytest.mark.parametrize("cast", [None, (), ("mara",)])
def test_cast_can_be_unauthored_empty_or_named(cast):
    authored = replace(segment(), shots=(Shot("She walks.", "", "", cast),))
    assert authored.shots[0].cast == cast


@pytest.mark.parametrize(
    ("cast", "message"),
    [(("nobody",), "subjects in the segment"), (("mara", "mara"), "duplicate")],
)
def test_cast_requires_known_distinct_subjects(cast, message):
    with pytest.raises(ValueError, match=message):
        replace(segment(), shots=(Shot("She walks.", "", "", cast),))


@pytest.mark.parametrize(
    ("changes", "message"),
    [
        ({"shots": ()}, "one shot"),
        ({"cut_frames": (36,)}, "every shot"),
        ({"subjects": (Subject("mara", "Mara", ""),) * 2}, "subject ids are unique"),
        (
            {"shots": (Shot("Walk.", "", ""),) * 3, "cut_frames": (60, 36)},
            "strictly increasing",
        ),
        (
            {"shots": (Shot("Walk.", "", ""),) * 3, "cut_frames": (36, 36)},
            "strictly increasing",
        ),
        ({"shots": (Shot("Walk.", "", ""),) * 2, "cut_frames": (0,)}, "inside"),
        ({"shots": (Shot("Walk.", "", ""),) * 2, "cut_frames": (120,)}, "inside"),
    ],
)
def test_existing_segment_rules_remain(changes, message):
    with pytest.raises(ValueError, match=message):
        replace(segment(), **changes)


def test_a_segment_may_have_no_cast():
    authored = replace(segment(), subjects=(), shots=(Shot("A ship passes.", "", "", ()),))
    assert authored.subjects == ()


@pytest.mark.parametrize(
    "changes",
    [
        {"speech": (Speech("mara", 0, "Hi.", "English"),)},
        {"states": (State(0, "mara", "hood", "up"),)},
        {"shots": (Shot("She walks.", "", "", ("mara",)),)},
    ],
)
def test_a_segment_with_no_cast_names_nobody(changes):
    with pytest.raises(ValueError, match="subject"):
        replace(segment(), subjects=(), **changes)


@pytest.mark.parametrize("kind", ["video", "audio"])
@pytest.mark.parametrize("frame", [0, 48])
def test_media_guides_keep_their_delivered_frame(kind, frame):
    guide = MediaGuide("source", kind, frame)
    assert (guide.asset_id, guide.kind, guide.at_frame) == ("source", kind, frame)


@pytest.mark.parametrize("frame", [-1, 0.5, True])
def test_media_guides_require_nonnegative_whole_frames(frame):
    with pytest.raises(ValueError, match="nonnegative whole delivered frame"):
        MediaGuide("source", "video", frame)


def test_media_guides_require_a_media_kind():
    with pytest.raises(ValueError, match="video or audio"):
        MediaGuide("source", "image", 0)


@pytest.mark.parametrize(
    "phases",
    [
        (Phase(0, 60, "Walk."),),
        (Phase(60, 60, "Walk."),),
        (Phase(60, 121, "Walk."),),
        (Phase(60.5, 120, "Walk."),),
        (Phase(True, 120, "Walk."),),
        (Phase(60, 120, " "),),
        (Phase(60, 90, "Walk."), Phase(80, 120, "Stop.")),
        (Phase(80, 120, "Stop."), Phase(60, 80, "Walk.")),
    ],
)
def test_invalid_phase_ranges_and_text(phases):
    with pytest.raises(ValueError):
        replace(segment(), phases=phases)


@pytest.mark.parametrize("phase", [Phase(36, 120, "Walk."), Phase(12, 60, "Walk.")])
def test_phase_cannot_open_or_span_cut(phase):
    with pytest.raises(ValueError, match="cut"):
        replace(segment(), shots=(Shot("Walk.", "", ""),) * 2, cut_frames=(36,), phases=(phase,))


def test_phases_may_end_at_cut_and_touch_each_other():
    authored = replace(
        segment(),
        shots=(Shot("Walk.", "", ""),) * 2,
        cut_frames=(36,),
        phases=(Phase(12, 36, "Walk."), Phase(60, 90, "Turn."), Phase(90, 120, "Stop.")),
    )
    assert len(authored.phases) == 3


@pytest.mark.parametrize(
    "name, make",
    [
        ("events", lambda frame: Event(frame, "Bell.")),
        ("speech", lambda frame: Speech("mara", frame, "Hi.", "English")),
        ("states", lambda frame: State(frame, "mara", "hood", "up")),
    ],
)
def test_timed_items_need_frames_inside_clip_and_order(name, make):
    for frame in (-1, 120, 121, 1.5, True):
        with pytest.raises(ValueError, match="frame"):
            replace(segment(), **{name: (make(frame),)})
    with pytest.raises(ValueError, match="order"):
        replace(segment(), **{name: (make(20), make(10))})
    assert len(getattr(replace(segment(), **{name: (make(0), make(0), make(119))}), name)) == 3


@pytest.mark.parametrize(
    "name, item, problem",
    [
        ("events", Event(0, ""), "description"),
        ("speech", Speech("unknown", 0, "Hi.", "English"), "speaker_id"),
        ("speech", Speech("mara", 0, " ", "English"), "text"),
        ("speech", Speech("mara", 0, "Hi.", ""), "language"),
        ("speech", Speech("mara", 0, "Hi.", "English", off_screen=1), "off_screen"),
        ("states", State(0, "unknown", "hood", "up"), "subject_id"),
        ("states", State(0, "mara", "", "up"), "attribute"),
        ("states", State(0, "mara", "hood", ""), "value"),
    ],
)
def test_timed_items_need_known_subjects_and_nonempty_text(name, item, problem):
    with pytest.raises(ValueError, match=problem):
        replace(segment(), **{name: (item,)})
