"""Versioned authored documents and assembly from Direction records, without I/O."""

from collections.abc import Sequence
from dataclasses import asdict, dataclass

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

SEGMENT_SCHEMA = 2
PHASE_SCHEMA = 2
SEGMENT_ADDITIONS = frozenset({"style", "events", "speech", "states"})


def segment_schema(document: dict) -> int:
    """Minimum schema for a new write; reads always use the stored version."""
    return 2 if SEGMENT_ADDITIONS.intersection(document) else 1


def phase_schema(document: dict) -> int:
    return 2 if "cast" in document else 1


def phase_record(record: tuple) -> tuple[int, dict, int]:
    """Legacy imports use pairs; new later phases can carry an explicit schema."""
    if len(record) == 2:
        start, document = record
        return start, document, phase_schema(document)
    start, document, schema = record
    return start, document, schema


def _object(document, keys, label, optional=()):
    if not isinstance(document, dict):
        raise ValueError(f"{label} must be an object")  # noqa: TRY004 - document errors are ValueError
    for key in sorted(keys - document.keys()):
        raise ValueError(f"{label}.{key} is missing")
    for key in sorted(document.keys() - keys - set(optional), key=str):
        raise ValueError(f"Unknown key {label}.{key}")


def _text(value, key, *, nonempty=False):
    if not isinstance(value, str):
        raise ValueError(f"{key} must be a string")  # noqa: TRY004 - document errors are ValueError
    if nonempty and not value.strip():
        raise ValueError(f"{key} must be non-empty")
    return value


@dataclass(frozen=True)
class SegmentContent:
    subject_ids: tuple[str, ...]
    place: PlaceText
    style: str = ""
    events: tuple[Event, ...] = ()
    speech: tuple[Speech, ...] = ()
    states: tuple[State, ...] = ()


@dataclass(frozen=True)
class CastMember:
    id: str
    name: str
    description: str
    keeps: tuple[str, ...]
    identity_asset_id: str | None


def segment_content(document: dict, schema: int) -> SegmentContent:
    if type(schema) is not int or schema not in (1, SEGMENT_SCHEMA):
        raise ValueError(f"Unknown segment content schema {schema}")
    _object(document, {"subjects", "place"}, "segment", SEGMENT_ADDITIONS if schema == 2 else ())
    ids = document["subjects"]
    # An empty cast is a segment with no actors: a landscape, a vehicle, an establishing shot.
    if not isinstance(ids, list):
        raise ValueError("subjects must be a list")  # noqa: TRY004 - document errors are ValueError
    for subject_id in ids:
        _text(subject_id, "subjects id", nonempty=True)
    if len(set(ids)) != len(ids):
        raise ValueError("subjects contains a duplicate id")
    place = document["place"]
    _object(place, {"noun", "description", "landmarks"}, "place")
    for key in place:
        _text(place[key], f"place.{key}", nonempty=key != "landmarks")
    style = _text(document.get("style", ""), "segment.style")
    events = _timed(document, "events", Event, {"at_frame", "description"})
    speech = _timed(
        document,
        "speech",
        Speech,
        {"speaker_id", "at_frame", "text", "language"},
        {"delivery", "off_screen"},
    )
    states = _timed(document, "states", State, {"at_frame", "subject_id", "attribute", "value"})
    for item in speech:
        if item.speaker_id not in ids:
            raise ValueError("speech.speaker_id must name one of segment.subjects")
    for item in states:
        if item.subject_id not in ids:
            raise ValueError("states.subject_id must name one of segment.subjects")
    return SegmentContent(tuple(ids), PlaceText(**place), style, events, speech, states)


def split_content(document: dict, phases: list[dict], frame: int) -> tuple[dict, dict]:
    """Partition authored timing and keep the original ordering of referenced subjects."""
    from copy import deepcopy

    halves = []
    for right in (False, True):
        half = deepcopy(document)
        references = set()
        for phase in phases:
            if (phase["start_frame"] >= frame) == right:
                references.update(phase["content"].get("cast", document["subjects"]))
        for key in ("events", "speech", "states"):
            if key not in document:
                continue
            half[key] = [
                {**item, "at_frame": item["at_frame"] - (frame if right else 0)}
                for item in document[key]
                if (item["at_frame"] >= frame) == right
            ]
            reference = {"speech": "speaker_id", "states": "subject_id"}.get(key)
            if reference:
                references.update(item[reference] for item in half[key])
        half["subjects"] = [s for s in document["subjects"] if s in references] or document[
            "subjects"
        ][:]
        halves.append(half)
    return tuple(halves)


def _timed(document, key, kind, required, optional=()):
    items = document.get(key, [])
    if not isinstance(items, list):
        raise ValueError(f"{key} must be a list")  # noqa: TRY004
    result = []
    for index, item in enumerate(items):
        label = f"{key}[{index}]"
        _object(item, required, label, optional)
        for field, value in item.items():
            path = f"{label}.{field}"
            if field == "at_frame":
                if type(value) is not int:
                    raise ValueError(f"{path} must be an integer")
            elif field == "off_screen":
                if type(value) is not bool:
                    raise ValueError(f"{path} must be a boolean")
            else:
                _text(value, path, nonempty=field != "delivery")
        result.append(kind(**item))
    return tuple(result)


def phase_content(document: dict, schema: int) -> Shot:
    if type(schema) is not int or schema not in (1, PHASE_SCHEMA):
        raise ValueError(f"Unknown phase content schema {schema}")
    prose = {"description", "screen_direction", "sound"}
    _object(document, prose, "phase", {"cast"} if schema == 2 else ())
    for key in prose:
        _text(document[key], f"phase.{key}", nonempty=key == "description")
    cast = None
    if "cast" in document:
        if not isinstance(document["cast"], list):
            raise ValueError("phase.cast must be a list")
        cast = tuple(_text(value, "phase.cast id", nonempty=True) for value in document["cast"])
        if len(set(cast)) != len(cast):
            raise ValueError("phase.cast contains a duplicate id")
    return Shot(**{key: document[key] for key in prose}, cast=cast)


def cast_member(row: dict) -> CastMember:
    keys = {"id", "name", "description", "keeps", "identity_asset_id"}
    _object(row, keys, "subject")
    for key in keys - {"identity_asset_id", "keeps"}:
        _text(row[key], f"subject.{key}", nonempty=True)
    keeps = row["keeps"]
    # Legacy authored files and undo receipts keep their phrase as one item; never parse English.
    if isinstance(keeps, str):
        keeps = [keeps] if keeps else []
    if not isinstance(keeps, list):
        raise ValueError("subject.keeps must be a list of items")  # noqa: TRY004 - document validation
    subject = Subject(row["id"], row["name"], row["description"], tuple(keeps))
    for key in ("id", "name"):
        if len(row[key]) > 120:
            raise ValueError(f"subject.{key} exceeds 120 characters")
    if row["identity_asset_id"] is not None:
        _text(row["identity_asset_id"], "subject.identity_asset_id")
        if len(row["identity_asset_id"]) > 120:
            raise ValueError("subject.identity_asset_id exceeds 120 characters")
    return CastMember(**{**row, "keeps": subject.keeps})


def assemble(
    content: dict,
    content_schema: int,
    cast: Sequence[CastMember],
    phases: Sequence[tuple[int, dict, int]],
    cut_frames: Sequence[int],
    delivered_frames: int,
    continues: bool,
) -> tuple[Segment, tuple[Picture, ...]]:
    authored = segment_content(content, content_schema)
    members = {member.id: member for member in cast}
    subjects = []
    pictures = []
    for subject_id in authored.subject_ids:
        if subject_id not in members:
            raise ValueError(f"Subject {subject_id} is not in the movie's cast")
        member = members[subject_id]
        subjects.append(Subject(member.id, member.name, member.description, member.keeps))
        if member.identity_asset_id:
            pictures.append(Picture(PictureRole.IDENTITY, member.identity_asset_id, member.id))
    if type(delivered_frames) is not int or delivered_frames < 1:
        raise ValueError("delivered_frames must be a positive integer")
    if type(continues) is not bool:
        raise ValueError("continues must be a boolean")
    if not phases:
        raise ValueError("A segment needs at least one phase")
    starts = [start for start, _, _ in phases]
    for frame in starts:
        if type(frame) is not int or not 0 <= frame < delivered_frames:
            raise ValueError(f"Phase frame {frame} lies outside the segment")
    if starts[0] != 0:
        raise ValueError(f"First phase starts at frame {starts[0]}; it must start at 0")
    if starts != sorted(set(starts)):
        raise ValueError(f"Phase frames must be strictly increasing: {starts}")
    for frame in cut_frames:
        if type(frame) is not int or not 0 < frame < delivered_frames:
            raise ValueError(f"Cut frame {frame} lies outside the segment")
    missing_phases = sorted(set(cut_frames) - set(starts[1:]))
    if missing_phases:
        raise ValueError(f"Cut frames without phases: {missing_phases}")
    shots, later = [], []
    for index, (start, document, schema) in enumerate(phases):
        shot = phase_content(document, schema)
        if shot.cast is not None and set(shot.cast) - set(authored.subject_ids):
            raise ValueError("phase.cast must name only segment.subjects")
        if start == 0 or start in cut_frames:
            shots.append(shot)
        else:
            end = starts[index + 1] if index + 1 < len(starts) else delivered_frames
            later.append(Phase(start, end, shot.description, shot.screen_direction, shot.sound))
    return (
        Segment(
            tuple(subjects),
            authored.place,
            tuple(shots),
            tuple(cut_frames),
            delivered_frames,
            continues,
            authored.style,
            tuple(later),
            authored.events,
            authored.speech,
            authored.states,
        ),
        tuple(pictures),
    )


def assembled_to_dict(segment: Segment, pictures: Sequence[Picture]) -> dict:
    subjects = []
    for subject in segment.subjects:
        identities = [
            p for p in pictures if p.role is PictureRole.IDENTITY and p.subject_id == subject.id
        ]
        if len(identities) > 1:
            raise ValueError(f"Subject {subject.id} has more than one identity picture")
        subjects.append(
            {
                **asdict(subject),
                "keeps": list(subject.keeps),
                "identity_asset": identities[0].asset_id if identities else None,
            }
        )
    result = {
        "subjects": subjects,
        "place": asdict(segment.place),
        "shots": [
            {
                **{key: getattr(shot, key) for key in ("description", "screen_direction", "sound")},
                **({"cast": list(shot.cast)} if shot.cast is not None else {}),
            }
            for shot in segment.shots
        ],
        "cut_frames": list(segment.cut_frames),
        "delivered_frames": segment.delivered_frames,
        "continues": segment.continues,
    }
    if segment.style:
        result["style"] = segment.style
    for key in ("events", "speech", "states"):
        if items := getattr(segment, key):
            result[key] = [asdict(item) for item in items]
    if segment.phases:
        result["phases"] = [
            {key: value for key, value in asdict(phase).items() if key != "end_frame"}
            for phase in segment.phases
        ]
    return result
