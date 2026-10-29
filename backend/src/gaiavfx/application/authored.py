"""Strict JSON readers for authored slice requests."""

import json
from dataclasses import fields
from pathlib import Path

from gaiavfx.modules.direction import content
from gaiavfx.modules.direction.content import assembled_to_dict as segment_to_dict
from gaiavfx.modules.direction.insertion import Insertion, Removal
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.direction.survey import SurveyPlan
from gaiavfx.shared.errors import ApplicationError

__all__ = [
    "import_segment",
    "load_insertion",
    "load_segment",
    "load_survey",
    "segment_records",
    "segment_to_dict",
    "validate_insertion",
    "validate_removal",
]


def _object(value, required, optional=()):
    if (
        not isinstance(value, dict)
        or not set(required) <= value.keys()
        or value.keys() - set(required) - set(optional)
    ):
        raise ApplicationError(
            f"Expected fields {sorted(required)}; unknown or missing fields found.", 422
        )
    return value


def _read(path):
    if isinstance(path, dict):
        return path
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ApplicationError(f"Invalid authored JSON: {exc}", 422) from exc


def segment_records(data: dict) -> dict:
    """Map authored files to records, validated only by Direction's content assembly."""
    data = _object(
        data,
        {"subjects", "place", "shots", "cut_frames", "delivered_frames", "continues"},
        {"style", "phases", "events", "speech", "states"},
    )
    try:
        for key in ("subjects", "shots", "cut_frames"):
            if not isinstance(data[key], list):
                raise TypeError(f"{key} must be a list")
        cast = []
        for item in data["subjects"]:
            _object(item, {"id", "name", "description", "keeps", "identity_asset"})
            member = {key: item[key] for key in ("id", "name", "description", "keeps")}
            member["identity_asset_id"] = item["identity_asset"]
            member["keeps"] = list(content.cast_member(member).keeps)
            cast.append(member)
        document = {"subjects": [member["id"] for member in cast], "place": data["place"]}
        document.update({key: data[key] for key in content.SEGMENT_ADDITIONS if key in data})
        if len(data["shots"]) != len(data["cut_frames"]) + 1:
            raise ValueError("every shot after the first begins at a cut")
        phases = list(zip([0, *data["cut_frames"]], data["shots"], strict=True))
        later = data.get("phases", [])
        if not isinstance(later, list):
            raise TypeError("phases must be a list")
        for phase in later:
            _object(phase, {"start_frame", "description"}, {"screen_direction", "sound"})
            prose = {"screen_direction": "", "sound": "", **phase}
            start = prose.pop("start_frame")
            phases.append((start, prose, content.PHASE_SCHEMA))
        phases.sort(key=lambda phase: phase[0])
        content.assemble(
            document,
            content.segment_schema(document),
            [content.cast_member(member) for member in cast],
            [content.phase_record(phase) for phase in phases],
            data["cut_frames"],
            data["delivered_frames"],
            data["continues"],
        )
        return {
            "cast": cast,
            "content": document,
            "phases": phases,
            "delivered_frames": data["delivered_frames"],
            "continues": data["continues"],
        }
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ApplicationError(f"Invalid segment: {exc}", 422) from exc


def load_segment(path: Path | str | dict) -> dict:
    return segment_records(_read(path))


def import_segment(session, movie_id, expected_revision, data_or_path, *, at=None) -> dict:
    """Import into one movie in the caller's transaction; cast identities are never overwritten."""
    direction = DirectionService()
    movie = direction.read_movie(session, movie_id)
    if type(expected_revision) is not int or expected_revision < 1:
        raise ApplicationError("expected_revision must be a positive integer.", 422)
    if movie["revision"] != expected_revision:
        raise ApplicationError("Movie revision is stale.", 409)
    data = load_segment(data_or_path)
    revision = expected_revision
    receipts = []
    for member in data["cast"]:
        try:
            existing = direction.read_subject(session, member["id"])
        except ApplicationError as exc:
            if exc.status != 404:
                raise
            existing = None
        if existing and existing["movie_id"] == movie_id:
            if any(existing[key] != value for key, value in member.items()):
                raise ApplicationError(
                    f"Subject {member['id']} is already defined differently in this movie.", 409
                )
            continue
        created = direction.add_subject(
            session,
            movie_id,
            revision,
            subject_id=member["id"],
            **{key: value for key, value in member.items() if key != "id"},
        )
        receipts.append(created["receipt"])
        revision = created["receipt"]["after"]["revision"]
    segment = direction.add_segment(
        session,
        movie_id,
        revision,
        at=at,
        **{key: value for key, value in data.items() if key != "cast"},
    )
    return {**segment, "subject_receipts": receipts}


def load_survey(path: Path | str | dict) -> SurveyPlan:
    data = _object(
        _read(path),
        {"what", "sound", "first_view", "first_angle_id", "first_frame_asset", "views", "overhead"},
    )
    try:
        views = tuple(tuple(item) for item in data["views"])
        if any(len(item) != 2 for item in views):
            raise ValueError("survey views need angle and description")
        return SurveyPlan(
            views=views, **{key: value for key, value in data.items() if key != "views"}
        )
    except (ValueError, TypeError, KeyError, AttributeError) as exc:
        raise ApplicationError(f"Invalid survey: {exc}", 422) from exc


def validate_insertion(data: dict) -> dict:
    required = {"who", "looks", "pronouns", "facing", "plate", "keep", "where", "size"}
    optional = (
        {field.name for field in fields(Insertion)} - required - {"view_asset", "subject_asset"}
    )
    _object(data, required, optional)
    from gaiavfx.modules.direction.service import validate_insertion_wording

    return validate_insertion_wording(data)


def load_insertion(path) -> dict:
    return validate_insertion(_read(path))


def validate_removal(data: dict) -> dict:
    _object(data, {"whom", "object_pronoun", "behind"})
    try:
        Removal(view_asset="placeholder", **data)
    except (ValueError, TypeError) as exc:
        raise ApplicationError(f"Invalid removal: {exc}", 422) from exc
    return data
