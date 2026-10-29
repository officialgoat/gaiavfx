"""Versioned Direction content preserves S2 wording and exposes assembly gaps."""

import json
from copy import deepcopy
from dataclasses import asdict
from pathlib import Path

import pytest

from gaiavfx.application.authored import load_segment, segment_records, segment_to_dict
from gaiavfx.modules.direction.content import (
    CastMember,
    assemble,
    assembled_to_dict,
    cast_member,
    phase_content,
    phase_record,
    phase_schema,
    segment_content,
    segment_schema,
)
from gaiavfx.modules.direction.segment import (
    Event,
    Phase,
    Picture,
    PictureRole,
    PlaceText,
    Segment,
    Shot,
    Speech,
    State,
    Subject,
)
from gaiavfx.shared.errors import ApplicationError

AUTHORED = Path(__file__).parent / "fixtures" / "parity" / "authored"
PLACE = {"noun": "courtyard", "description": "A courtyard.", "landmarks": ""}
CONTENT = {"subjects": ["s1"], "place": PLACE}
SHOT = {"description": "Walk.", "screen_direction": "", "sound": ""}
MEMBER = {
    "id": "s1",
    "name": "Arin",
    "description": "A walker",
    "keeps": "coat",
    "identity_asset_id": None,
}


@pytest.mark.parametrize("arm", ["DZ", "EH", "EG", "TC"])
def test_assembly_matches_s2_segment_and_serialization(arm):
    data = json.loads((AUTHORED / f"{arm}.json").read_text(encoding="utf-8"))["segment"]
    data["subjects"][0]["identity_asset"] = "identity"
    expected = (
        Segment(
            tuple(
                Subject(
                    **{k: v for k, v in row.items() if k not in {"identity_asset", "keeps"}},
                    keeps=tuple(row["keeps"]),
                )
                for row in data["subjects"]
            ),
            PlaceText(**data["place"]),
            tuple(Shot(**row) for row in data["shots"]),
            tuple(data["cut_frames"]),
            data["delivered_frames"],
            data["continues"],
        ),
        tuple(
            Picture(PictureRole.IDENTITY, row["identity_asset"], row["id"])
            for row in data["subjects"]
        ),
    )
    content = {"subjects": [s["id"] for s in data["subjects"]], "place": data["place"]}
    members = [
        cast_member(
            {
                **{key: s[key] for key in ("id", "name", "description", "keeps")},
                "identity_asset_id": s["identity_asset"],
            }
        )
        for s in data["subjects"]
    ]
    phases = [
        (frame, shot, 1)
        for frame, shot in zip([0, *data["cut_frames"]], data["shots"], strict=True)
    ]
    actual = assemble(
        content, 1, members, phases, data["cut_frames"], data["delivered_frames"], True
    )
    assert actual == expected
    assert assembled_to_dict(*actual) == data
    assert segment_to_dict is assembled_to_dict


@pytest.mark.parametrize(
    ("document", "schema", "problem"),
    [
        ({"subjects": ["s1"]}, 1, "place"),
        ({**CONTENT, "extra": 1}, 1, "extra"),
        ({**CONTENT, "subjects": []}, 1, "subjects"),
        ({**CONTENT, "subjects": ["s1", "s1"]}, 1, "duplicate"),
        ({**CONTENT, "subjects": [2]}, 1, "id"),
        ({**CONTENT, "subjects": [""]}, 1, "id"),
        ({**CONTENT, "place": {**PLACE, "noun": ""}}, 1, "noun"),
        ({**CONTENT, "place": {**PLACE, "landmarks": None}}, 1, "landmarks"),
        ({**CONTENT, "place": {**PLACE, "extra": ""}}, 1, "extra"),
        ({**CONTENT, "place": {"noun": "courtyard", "description": "Outside"}}, 1, "landmarks"),
        ({**CONTENT, "place": []}, 1, "place"),
        (CONTENT, 3, "schema 3"),
        (CONTENT, True, "schema"),
    ],
)
def test_segment_content_rejects_invalid_documents(document, schema, problem):
    with pytest.raises(ValueError, match=problem):
        segment_content(document, schema)


@pytest.mark.parametrize(
    ("document", "schema", "problem"),
    [
        ({"description": "Walk", "sound": ""}, 1, "screen_direction"),
        ({**SHOT, "extra": ""}, 1, "extra"),
        ({**SHOT, "description": ""}, 1, "description"),
        ({**SHOT, "sound": 1}, 1, "sound"),
        ({**SHOT, "screen_direction": None}, 1, "screen_direction"),
        (SHOT, 3, "schema 3"),
    ],
)
def test_phase_content_rejects_invalid_documents(document, schema, problem):
    with pytest.raises(ValueError, match=problem):
        phase_content(document, schema)


@pytest.mark.parametrize(
    ("changes", "problem"),
    [
        ({"name": ""}, "name"),
        ({"description": ""}, "description"),
        ({"identity_asset_id": False}, "identity_asset_id"),
        ({"keeps": None}, "keeps"),
        ({"id": " "}, "id"),
    ],
)
def test_cast_validation(changes, problem):
    with pytest.raises(ValueError, match=problem):
        cast_member({**MEMBER, **changes})


@pytest.mark.parametrize("keeps", [[""], [" coat"], ["coat "], ["coat", "coat"], [1], {}])
def test_cast_keeps_item_validation(keeps):
    with pytest.raises(ValueError, match="keeps"):
        cast_member({**MEMBER, "keeps": keeps})


def test_legacy_keeps_phrase_is_one_item_without_english_parsing():
    data = extended_document()
    data["subjects"][0]["keeps"] = "coat, hat and bag"
    records = load_segment(data)
    assert records["cast"][0]["keeps"] == ["coat, hat and bag"]
    data["subjects"][0]["keeps"] = ""
    assert load_segment(data)["cast"][0]["keeps"] == []


def test_cast_order_is_authored_and_unpictured_members_are_supported():
    first = cast_member(MEMBER)
    second = CastMember("s2", "Bea", "A runner", ("hat",), "picture-bea")
    segment, pictures = assemble(
        {**CONTENT, "subjects": ["s2", "s1"]}, 1, [first, second], [(0, SHOT, 1)], [], 102, True
    )
    assert [s.id for s in segment.subjects] == ["s2", "s1"]
    assert [(p.subject_id, p.asset_id) for p in pictures] == [("s2", "picture-bea")]
    assert assembled_to_dict(segment, pictures)["subjects"][1]["identity_asset"] is None
    assert asdict(first) == {**MEMBER, "keeps": ("coat",)}


@pytest.mark.parametrize(
    ("ids", "phases", "cuts", "problems"),
    [
        (["unknown"], [(0, SHOT, 1)], [], ["Subject unknown", "cast"]),
        (["s1"], [], [], ["phase"]),
        (["s1"], [(12, SHOT, 1)], [], ["12", "0"]),
        (["s1"], [(0, SHOT, 1), (48, SHOT, 1)], [36], ["36"]),
        (["s1"], [(0, SHOT, 1)], [36], ["36"]),
        (["s1"], [(0, SHOT, 1), (102, SHOT, 1)], [102], ["102"]),
        (["s1"], [(0, SHOT, 1), (36, SHOT, 1), (48, SHOT, 1)], [48, 36], ["strictly increasing"]),
    ],
)
def test_assembly_gaps_name_subjects_and_frames(ids, phases, cuts, problems):
    with pytest.raises(ValueError) as error:
        assemble({**CONTENT, "subjects": ids}, 1, [cast_member(MEMBER)], phases, cuts, 102, True)
    for problem in problems:
        assert problem in str(error.value)


def extended_document():
    return {
        "subjects": [
            {
                **{k: v for k, v in MEMBER.items() if k != "identity_asset_id"},
                "identity_asset": None,
            }
        ],
        "place": PLACE,
        "shots": [{**SHOT, "cast": ["s1"]}, {**SHOT, "cast": []}],
        "cut_frames": [36],
        "delivered_frames": 120,
        "continues": False,
        "style": "Handheld super 16.",
        "phases": [{"start_frame": 60, "description": "Stop."}],
        "events": [{"at_frame": 48, "description": "Bell."}],
        "speech": [
            {"speaker_id": "s1", "at_frame": 12, "text": "@Bea  wait ", "language": "English"}
        ],
        "states": [
            {"at_frame": 36, "subject_id": "s1", "attribute": "left hand", "value": "raised"}
        ],
    }


def assemble_records(records, cuts):
    return assemble(
        records["content"],
        segment_schema(records["content"]),
        [cast_member(member) for member in records["cast"]],
        [phase_record(phase) for phase in records["phases"]],
        cuts,
        records["delivered_frames"],
        records["continues"],
    )


def test_schema_two_reader_assembly_and_serialization_round_trip(tmp_path):
    data = extended_document()
    original = deepcopy(data)
    path = tmp_path / "segment.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    records = load_segment(path)
    assert records == segment_records(data)
    assert segment_schema(records["content"]) == 2
    assert [phase_record(record)[2] for record in records["phases"]] == [2, 2, 2]
    segment, pictures = assemble_records(records, data["cut_frames"])
    assert segment.style == "Handheld super 16."
    assert [shot.cast for shot in segment.shots] == [("s1",), ()]
    assert segment.phases == (Phase(60, 120, "Stop."),)
    assert segment.events == (Event(48, "Bell."),)
    assert segment.speech == (Speech("s1", 12, "@Bea  wait ", "English"),)
    assert segment.states == (State(36, "s1", "left hand", "raised"),)
    assert pictures == ()
    serialized = assembled_to_dict(segment, pictures)
    assert "end_frame" not in serialized["phases"][0]
    assert serialized["shots"][1]["cast"] == []
    assert assemble_records(segment_records(serialized), data["cut_frames"]) == (segment, pictures)
    assert data == original  # Reading/assembly never rewrites a document.


@pytest.mark.parametrize("arm", ["DZ", "EH", "EG", "TC"])
def test_legacy_file_shapes_still_select_schema_one(arm):
    data = json.loads((AUTHORED / f"{arm}.json").read_text(encoding="utf-8"))["segment"]
    data["subjects"][0]["identity_asset"] = "identity"
    records = segment_records(data)
    assert segment_schema(records["content"]) == 1
    assert all(phase_record(phase)[2] == 1 for phase in records["phases"])
    assert phase_content(records["phases"][0][1], 1).cast is None


def test_schema_versions_are_selected_per_document():
    data = extended_document()
    del data["shots"][1]["cast"]
    records = segment_records(data)
    assert [phase_record(record)[2] for record in records["phases"]] == [2, 1, 2]
    assert phase_schema({**SHOT, "cast": []}) == 2
    assert segment_schema({**CONTENT, "style": ""}) == 2


@pytest.mark.parametrize("identity", [None, ""])
@pytest.mark.parametrize("cuts, starts", [([], [0, 60]), ([36], [0, 36, 60])])
def test_later_phase_and_unbound_member(identity, cuts, starts):
    segment, pictures = assemble(
        CONTENT,
        2,
        [cast_member({**MEMBER, "identity_asset_id": identity})],
        [(start, SHOT, 2) for start in starts],
        cuts,
        120,
        False,
    )
    assert len(segment.shots) == len(cuts) + 1
    assert segment.phases == (Phase(60, 120, **SHOT),)
    assert pictures == ()


@pytest.mark.parametrize(
    "key, value, problem",
    [
        ("extra", 0, "extra"),
        ("style", None, "style"),
        ("events", {}, "events"),
        ("events", [{"at_frame": 0, "description": "Bell.", "extra": True}], "extra"),
        ("events", [{"at_frame": 0}], "description"),
        ("events", [{"at_frame": True, "description": "Bell."}], "at_frame"),
        ("events", [{"at_frame": 0, "description": ""}], "description"),
        (
            "speech",
            [{"speaker_id": "no", "at_frame": 0, "text": "Hi", "language": "English"}],
            "speaker_id",
        ),
        ("speech", [{"speaker_id": "s1", "at_frame": 0, "text": "Hi", "language": 1}], "language"),
        (
            "speech",
            [
                {
                    "speaker_id": "s1",
                    "at_frame": 0,
                    "text": "Hi",
                    "language": "English",
                    "off_screen": 1,
                }
            ],
            "off_screen",
        ),
        (
            "speech",
            [
                {
                    "speaker_id": "s1",
                    "at_frame": 0,
                    "text": "Hi",
                    "language": "English",
                    "delivery": None,
                }
            ],
            "delivery",
        ),
        (
            "states",
            [{"at_frame": 0, "subject_id": "no", "attribute": "hood", "value": "up"}],
            "subject_id",
        ),
        ("states", [{"at_frame": 0, "subject_id": "s1", "attribute": "hood", "value": 1}], "value"),
    ],
)
def test_schema_two_strict_keys_and_types(key, value, problem):
    with pytest.raises(ValueError, match=problem):
        segment_content({**CONTENT, key: value}, 2)


def test_timed_frame_bounds_deferred_until_assembly():
    document = {**CONTENT, "events": [{"at_frame": 120, "description": "Bell."}]}
    assert segment_content(document, 2).events[0].at_frame == 120
    with pytest.raises(ValueError, match="frame"):
        assemble(document, 2, [cast_member(MEMBER)], [(0, SHOT, 1)], [], 120, False)


@pytest.mark.parametrize("cast", [None, "s1", [1], ["s1", "s1"]])
def test_schema_two_cast_types(cast):
    with pytest.raises(ValueError, match="cast"):
        phase_content({**SHOT, "cast": cast}, 2)


def test_schema_one_still_rejects_new_keys():
    for key, value in (("style", ""), ("events", []), ("speech", []), ("states", [])):
        with pytest.raises(ValueError, match=key):
            segment_content({**CONTENT, key: value}, 1)
    with pytest.raises(ValueError, match="cast"):
        phase_content({**SHOT, "cast": []}, 1)


@pytest.mark.parametrize(
    "part, key, value",
    [
        (None, "unknown", 1),
        (None, "phases", {}),
        ("phase", "end_frame", 120),
        ("phase", "start_frame", 36),
        ("shot", "cast", ["unknown"]),
        ("speech", "text", ""),
        ("speech", "at_frame", 120),
        ("speech", "speaker_id", "unknown"),
        ("speech", "unknown", 1),
    ],
)
def test_extended_import_errors_are_422(part, key, value):
    data = extended_document()
    target = (
        data
        if part is None
        else data[{"phase": "phases", "shot": "shots", "speech": "speech"}[part]][0]
    )
    target[key] = value
    with pytest.raises(ApplicationError) as error:
        segment_records(data)
    assert error.value.status == 422


def test_cast_is_checked_on_later_phases_too():
    with pytest.raises(ValueError, match="cast"):
        assemble(
            CONTENT,
            2,
            [cast_member(MEMBER)],
            [(0, SHOT, 1), (60, {**SHOT, "cast": ["unknown"]}, 2)],
            [],
            120,
            False,
        )
