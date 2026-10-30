"""Direction use cases. Callers provide the transaction's SQLAlchemy session."""

from __future__ import annotations

from copy import deepcopy
from datetime import datetime, timedelta

from sqlalchemy import null, or_, select, update
from sqlalchemy.orm import Session

from gaiavfx.infrastructure.database import new_id, timestamp
from gaiavfx.modules.direction import content as authored
from gaiavfx.modules.direction.application import default_policy
from gaiavfx.modules.direction.coverage import (
    AngleRelation,
    CutContext,
    Evidence,
    OpeningContext,
    Relation,
)
from gaiavfx.modules.direction.insertion import Insertion, validate_scene_keep
from gaiavfx.modules.direction.persistence import (
    AngleRecord,
    CutRecord,
    EvidenceRecord,
    KeyframeRecord,
    MovieRecord,
    PhaseRecord,
    PlaceRecord,
    ReceiptRecord,
    SegmentRecord,
    SubjectRecord,
    SuitabilityRecord,
)
from gaiavfx.modules.direction.planning import Available, Binding
from gaiavfx.shared.errors import ApplicationError


def _required(value: str, label: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ApplicationError(f"{label} is required.", 422)
    return value.strip()


def _record(row) -> dict:
    return deepcopy({column.key: getattr(row, column.key) for column in row.__table__.columns})


def _get(session: Session, model, record_id: str):
    row = session.get(model, record_id)
    if row is None:
        raise ApplicationError(f"{model.__tablename__} record {record_id} was not found.", 404)
    return row


def _placement(value: dict | None) -> dict | None:
    if value is None:
        return None
    if not isinstance(value, dict) or value.get("source") not in {"authored", "unknown"}:
        raise ApplicationError("Placement needs an authored or unknown source.", 422)
    if set(value) - {"source", "x", "y", "yaw", "fov"}:
        raise ApplicationError("Placement contains unknown fields.", 422)
    coordinates = {"x", "y", "yaw", "fov"}
    if value["source"] == "authored" and not coordinates <= set(value):
        raise ApplicationError("Authored placement needs x, y, yaw and fov.", 422)
    if value["source"] == "unknown" and coordinates & set(value):
        raise ApplicationError("Unknown placement cannot assert coordinates.", 422)
    if any(type(value[key]) not in {int, float} for key in coordinates & set(value)):
        raise ApplicationError("Placement coordinates must be numbers.", 422)
    if "fov" in value and not 0 < value["fov"] < 180:
        raise ApplicationError("Placement fov must be between 0 and 180 degrees.", 422)
    return value


def _relation(value: str | None, crosses: bool | None, source: str) -> None:
    try:
        if source not in {"authored", "inferred", "unknown"}:
            raise ValueError("relation_source must be authored, inferred or unknown")
        if crosses is not None and type(crosses) is not bool:
            raise ValueError("crosses_barrier must be a boolean")
        AngleRelation(Relation(value) if value is not None else None, crosses, source)
    except (ValueError, TypeError) as exc:
        raise ApplicationError(f"Invalid angle relation: {exc}", 422) from exc


def _cut_values(values: dict) -> dict:
    allowed = {
        "frame",
        "from_angle_id",
        "to_angle_id",
        "relation",
        "crosses_barrier",
        "relation_source",
        "receives_predecessor",
        "side_facts",
        "fidelity",
        "override_technique",
        "insertion",
    }
    if set(values) - allowed:
        raise ApplicationError(f"Unknown cut fields: {sorted(set(values) - allowed)}", 422)
    for key in ("from_angle_id", "to_angle_id"):
        values[key] = _required(values.get(key), key)
    if values["from_angle_id"] == values["to_angle_id"]:
        raise ApplicationError("same_angle_cut: A cut needs a different target angle.", 422)
    if type(values.get("frame")) is not int or values["frame"] < 0:
        raise ApplicationError("frame must be a nonnegative delivered frame.", 422)
    for key in ("receives_predecessor", "side_facts", "fidelity"):
        if type(values.get(key)) is not bool:
            raise ApplicationError(f"{key} must be a boolean.", 422)
    if values.get("override_technique") is not None:
        values["override_technique"] = _required(values["override_technique"], "override_technique")
        try:
            default_policy().technique(values["override_technique"])
        except KeyError as exc:
            raise ApplicationError("Unknown direction technique.", 422) from exc
    _relation(values.get("relation"), values.get("crosses_barrier"), values["relation_source"])
    if values.get("insertion") is not None:
        values["insertion"] = validate_insertion_wording(values["insertion"])
    return values


def validate_insertion_wording(wording, *, keeps=()):
    """The same insertion invariant applies to persisted cuts and producer requests."""
    try:
        Insertion(view_asset="-", subject_asset="-", **wording)
        validate_scene_keep(wording["keep"], wording["looks"], keeps)
    except (ValueError, TypeError) as exc:
        raise ApplicationError(f"Invalid insertion: {exc}", 422) from exc
    return deepcopy(wording)


_MODELS = {
    "movie": MovieRecord,
    "subject": SubjectRecord,
    "segment": SegmentRecord,
    "phase": PhaseRecord,
    "cut": CutRecord,
    "angle": AngleRecord,
}
_CREATIONS = {
    "add_subject": "subject",
    "add_segment": "segment",
    "add_phase": "phase",
    "create_cut": "cut",
}
_CHANGED_AFTER = "The record changed after this command; undo the later command first."
_UNSET = object()
_SUBJECT_FIELDS = ("id", "name", "description", "keeps", "identity_asset_id")


class SegmentSplitRefusal(ApplicationError):
    """A stable machine-readable reason alongside the user-facing recovery."""

    def __init__(self, code: str, message: str):
        super().__init__(message, 422)
        self.code = code


def _validated(function, *args):
    try:
        return function(*args)
    except ValueError as exc:
        raise ApplicationError(str(exc), 422) from exc


def _check_revision(row, expected):
    if type(expected) is not int or expected < 1:
        raise ApplicationError("expected_revision must be a positive integer.", 422)
    if row.revision != expected:
        raise ApplicationError(
            f"{row.__class__.__name__.removesuffix('Record')} revision is stale.", 409
        )


def _bump(session, row, expected, **values):
    _check_revision(row, expected)
    model = type(row)
    result = session.execute(
        update(model)
        .where(model.id == row.id, model.revision == expected)
        .values(**deepcopy(values), revision=expected + 1, updated_at=timestamp())
        .execution_options(synchronize_session=False)
    )
    if result.rowcount != 1:
        raise ApplicationError(f"{model.__name__.removesuffix('Record')} revision is stale.", 409)
    session.refresh(row)


def _receipt(session, movie_id, command, kind, target_id, before, after, undoes=None):
    if command in _CREATIONS:
        after = {
            **after,
            "created_revision": after["record"]["revision"],
            "created_kind": _CREATIONS[command],
        }
    row = ReceiptRecord(
        id=new_id(),
        movie_id=movie_id,
        command="undo" if undoes else command,
        target_kind=kind,
        target_id=target_id,
        before=deepcopy(before),
        after=deepcopy(after),
        undoes=undoes,
        undone_at=None,
        created_at=timestamp(),
    )
    session.add(row)
    session.flush()
    return _record(row)


def _revision_restored(session, kind, record_id, current, expected):
    """Only explicit inverse receipts can bridge monotonically advancing revisions.

    Matching content is insufficient: a later ordinary edit, even to the same value,
    still protects the record from deletion. Undoing an Undo restores that protection.
    """
    if current == expected:
        return True

    def revision(receipt, side):
        state = getattr(receipt, side)
        if receipt.target_kind == kind and receipt.target_id == record_id:
            return state["revision"]
        return state.get("revision_proofs", {}).get(kind, {}).get(record_id)

    history = [
        r
        for r in session.scalars(
            select(ReceiptRecord).order_by(ReceiptRecord.created_at, ReceiptRecord.id)
        )
        if revision(r, "after") is not None
    ]
    indices = {r.id: index for index, r in enumerate(history)}
    position = len(history) - 1
    while current > expected:
        if position < 0:
            return False
        inverse = history[position]
        original_index = indices.get(inverse.undoes)
        if (
            revision(inverse, "after") != current
            or original_index is None
            or original_index >= position
        ):
            return False
        original = history[original_index]
        if original.undone_at is None:
            return False
        previous = revision(original, "before")
        if previous is None or previous >= current:
            return False
        current = previous
        position = original_index - 1
    return current == expected


def revision_restored(session, kind, record_id, current, expected):
    """Public inverse-receipt proof for readers comparing frozen Direction revisions."""
    if type(current) is not int or type(expected) is not int:
        return False
    return _revision_restored(session, kind, record_id, current, expected)


def _updated(session, row, expected, changes, movie_id, kind, undoes):
    if not changes:
        raise ApplicationError(f"Specify at least one {kind} field to change.", 422)
    before = {"revision": expected, **{key: deepcopy(getattr(row, key)) for key in changes}}
    _bump(session, row, expected, **changes)
    receipt = _receipt(
        session,
        movie_id,
        f"update_{kind}",
        kind,
        row.id,
        before,
        {"revision": expected + 1, **changes},
        undoes,
    )
    return {**_record(row), "receipt": receipt}


def _frames(value):
    if type(value) is not int or value < 1:
        raise ApplicationError("delivered_frames must be a positive integer.", 422)


def _frame(value, delivered, *, cut=False):
    if type(value) is not int or not (1 if cut else 0) <= value < delivered:
        raise ApplicationError(
            f"{'Cut' if cut else 'Phase'} frame {value} lies outside the segment.", 422
        )


def _continues(value):
    if type(value) is not bool:
        raise ApplicationError("continues must be a boolean.", 422)


_HANDOFF = ("continuation_method", "context_frames")


def _handoff(continuation_method, context_frames):
    """Shape only: the integration owns the method names, the context grid and when each qualifies."""
    if continuation_method is not None and not (
        isinstance(continuation_method, str)
        and 0 < len(continuation_method) <= 16
        and continuation_method.isascii()
        and continuation_method.replace("_", "").isalpha()
        and continuation_method.islower()
    ):
        raise ApplicationError("continuation_method names a handoff method.", 422)
    if context_frames is not None and (type(context_frames) is not int or context_frames < 1):
        raise ApplicationError("context_frames must be a positive integer.", 422)


def _title(value):
    value = _required(value, "title")
    if len(value) > 120:
        raise ApplicationError("Movie title exceeds 120 characters.", 422)
    return value


def _new_record(values, restore=None):
    """Undo reuses identity and metadata, after the ordinary command validated the values."""
    return {
        "id": new_id(),
        "revision": 1,
        "created_at": timestamp(),
        "updated_at": timestamp(),
        **(restore or {}),
        **deepcopy(values),
    }


class DirectionService:
    def __init__(self, *, sheet_reader=None, view_reader=None):
        # The application supplies Generation/Library facts; Direction owns the judgment. A set view's inputs (its
        # camera and hidden regions in the set) live in the sets module: `view_reader(session, evidence)`.
        self.sheet_reader = sheet_reader
        self.view_reader = view_reader

    def _view_inputs(self, session, row):
        if row.kind == "set_view":
            if self.view_reader is None:
                raise ApplicationError("Set views need the application's view reader.", 422)
            return self.view_reader(session, _record(row))
        angle = _get(session, AngleRecord, row.angle_id)
        place = _get(session, PlaceRecord, angle.place_id)
        return {
            "plate_asset_id": place.plate_asset_id,
            "plate_angle_id": place.plate_angle_id,
            "place": {key: getattr(place, key) for key in ("description", "sound", "overhead")},
            "angle": {"description": angle.description},
        }

    def _set_view_staleness(self, recorded, current):
        """A set view follows its own inputs (ADR 0019 §3): the place's set, the angle's camera in it and the hidden
        regions of the version it was rendered at. A newer accepted version is not staleness."""
        if "view_id" not in recorded:
            return True, (
                "it was approved before stage views recorded their camera; render it again",
            )
        reasons = []
        if recorded["set_id"] != current["set_id"]:
            reasons.append(
                "the place is built as another stage now"
                if current["set_id"]
                else "the place is no longer built as a stage"
            )
        elif recorded["view_id"] != current["view_id"] or (
            recorded["viewmat"],
            recorded["fov"],
        ) != (current["viewmat"], current["fov"]):
            reasons.append("the angle's camera in the stage changed since approval")
        if recorded["hide_region_ids"] != current["hide_region_ids"]:
            reasons.append("what the stage hides changed since approval")
        return bool(reasons), tuple(reasons)

    def _view_staleness(self, session, row):
        if row.inputs is None:
            return False, ("inputs were not recorded at approval",)
        current = self._view_inputs(session, row)
        if row.kind == "set_view":
            return self._set_view_staleness(row.inputs, current)
        reasons = []
        if row.inputs["plate_asset_id"] != current["plate_asset_id"]:
            reasons.append("the place's plate changed since approval")
        if (
            "plate_angle_id" in row.inputs
            and row.inputs["plate_angle_id"] != current["plate_angle_id"]
        ):
            reasons.append("the plate's angle changed since approval")
        for owner, keys in (
            ("place", ("description", "sound", "overhead")),
            ("angle", ("description",)),
        ):
            for key in keys:
                if row.inputs[owner][key] != current[owner][key]:
                    reasons.append(f"the {owner}'s {key} changed since approval")
        return bool(reasons), tuple(reasons)

    def _pin_inputs(self, session, row):
        cut = _get(session, CutRecord, row.cut_id)
        return {
            "evidence_id": row.evidence_id,
            "to_angle_id": cut.to_angle_id,
            "insertion": deepcopy(cut.insertion),
            "subject_asset_id": row.subject_asset_id,
        }

    def _pin_staleness(self, session, row):
        if row.inputs is None:
            return False, ("inputs were not recorded at approval",)
        cut = _get(session, CutRecord, row.cut_id)
        evidence = _get(session, EvidenceRecord, row.evidence_id)
        stale, view_reasons = self._view_staleness(session, evidence)
        reasons = (
            [f"the pin's evidence is stale: {reason}" for reason in view_reasons] if stale else []
        )
        if not evidence.approved:
            reasons.append("the pin's evidence is no longer approved")
        if cut.to_angle_id != row.inputs["to_angle_id"] or evidence.angle_id != cut.to_angle_id:
            reasons.append("the cut's target angle changed")
        if cut.insertion != row.inputs["insertion"]:
            reasons.append("the insertion wording changed")
        if row.inputs["subject_asset_id"] is not None:
            segment = _get(session, SegmentRecord, cut.segment_id)
            content = _validated(authored.segment_content, segment.content, segment.content_schema)
            identities = {
                s.identity_asset_id
                for s in session.scalars(
                    select(SubjectRecord).where(SubjectRecord.id.in_(content.subject_ids))
                )
            }
            if row.inputs["subject_asset_id"] not in identities:
                reasons.append("the subject's identity picture changed")
        return bool(reasons), tuple(reasons)

    def _reference_inputs(self, session, cut, kind, reference_id):
        if kind == "view":
            row = _get(session, EvidenceRecord, reference_id)
            if row.angle_id != cut.to_angle_id:
                raise ApplicationError("The view is not a binding of this cut's target angle.", 422)
            return row.inputs
        if kind == "pin":
            row = _get(session, KeyframeRecord, reference_id)
            if row.cut_id != cut.id:
                raise ApplicationError("The pinned frame is not a binding of this cut.", 422)
            return row.inputs
        if kind == "sheet":
            if self.sheet_reader is None:
                raise ApplicationError(
                    "Sheet judgments require the application's sheet reader.", 422
                )
            angle = _get(session, AngleRecord, cut.to_angle_id)
            return self.sheet_reader(session, reference_id, angle.place_id)["tiles"]
        raise ApplicationError("Unknown reference kind.", 422)

    def judge_suitability(self, session, cut_id, *, reference_kind, reference_id, verdict, note=""):
        cut = _get(session, CutRecord, cut_id)
        if (
            not isinstance(reference_kind, str)
            or reference_kind not in {"view", "pin", "sheet"}
            or not isinstance(verdict, str)
            or verdict not in {"suits", "unsuitable"}
            or not isinstance(note, str)
            or len(note) > 500
            or not isinstance(reference_id, str)
            or not reference_id
        ):
            raise ApplicationError("Invalid reference suitability judgment.", 422)
        inputs = self._reference_inputs(session, cut, reference_kind, reference_id)
        now = timestamp()
        session.execute(
            update(SuitabilityRecord)
            .where(
                SuitabilityRecord.cut_id == cut_id,
                SuitabilityRecord.reference_kind == reference_kind,
                SuitabilityRecord.reference_id == reference_id,
                SuitabilityRecord.superseded_at.is_(None),
            )
            .values(superseded_at=now)
        )
        row = SuitabilityRecord(
            id=new_id(),
            cut_id=cut_id,
            reference_kind=reference_kind,
            reference_id=reference_id,
            verdict=verdict,
            note=note,
            inputs=deepcopy(inputs),
            judged_at=now,
            judged_by="user",
        )
        session.add(row)
        session.flush()
        return _record(row) | {"stale": False}

    def suitability(self, session, cut_id):
        cut = _get(session, CutRecord, cut_id)
        results = []
        for row in session.scalars(
            select(SuitabilityRecord)
            .where(SuitabilityRecord.cut_id == cut_id, SuitabilityRecord.superseded_at.is_(None))
            .order_by(SuitabilityRecord.judged_at, SuitabilityRecord.id)
        ):
            try:
                inputs = self._reference_inputs(session, cut, row.reference_kind, row.reference_id)
                stale = row.inputs != inputs
            except ApplicationError as exc:
                if exc.status not in {404, 422}:
                    raise
                stale = True
            results.append(_record(row) | {"stale": stale})
        return results

    def reference_binding(self, session, kind, reference_id, judgments=()):
        if kind not in {"view", "pin"}:
            raise ApplicationError(
                "Direction bindings are views or pins; the application composes sheets.", 422
            )
        model = EvidenceRecord if kind == "view" else KeyframeRecord
        row = _get(session, model, reference_id)
        stale, reasons = (
            self._view_staleness(session, row)
            if kind == "view"
            else self._pin_staleness(session, row)
        )
        judgment = next(
            (j for j in judgments if j["reference_kind"] == kind and j["reference_id"] == row.id),
            None,
        )
        return Binding(
            kind,
            row.id,
            row.asset_id,
            row.approved_at,
            stale,
            reasons,
            judgment["verdict"] if judgment and not judgment["stale"] else "unjudged",
            judgment["note"] if judgment else "",
        )

    def movies(self, session):
        return [
            _record(row)
            for row in session.scalars(
                select(MovieRecord).order_by(MovieRecord.created_at.desc(), MovieRecord.id.desc())
            )
        ]

    def read_movie(self, session, movie_id):
        return {
            **_record(_get(session, MovieRecord, movie_id)),
            "subjects": self.subjects(session, movie_id),
            "segments": [
                self.read_segment(session, row.id) for row in self._segments(session, movie_id)
            ],
        }

    def subjects(self, session, movie_id):
        _get(session, MovieRecord, movie_id)
        return [
            _record(row)
            for row in session.scalars(
                select(SubjectRecord)
                .where(SubjectRecord.movie_id == movie_id)
                .order_by(SubjectRecord.created_at, SubjectRecord.id)
            )
        ]

    def read_subject(self, session, subject_id):
        return _record(_get(session, SubjectRecord, subject_id))

    def _segments(self, session, movie_id):
        return list(
            session.scalars(
                select(SegmentRecord)
                .where(SegmentRecord.movie_id == movie_id)
                .order_by(SegmentRecord.ordinal)
            )
        )

    def read_segment(self, session, segment_id):
        return {
            **_record(_get(session, SegmentRecord, segment_id)),
            "phases": self.phases(session, segment_id),
            "cuts": self.cuts(session, segment_id),
        }

    def _sync_opening(self, session, segment_id):
        """With cuts, the first cut's from_angle_id is the opening angle (ADR 0019 §1): every cut command ends by
        keeping the segment's copy equal to it. A segment that loses its last cut keeps the angle it opened on."""
        session.flush()
        first = session.scalar(
            select(CutRecord.from_angle_id)
            .where(CutRecord.segment_id == segment_id)
            .order_by(CutRecord.frame)
            .limit(1)
        )
        row = _get(session, SegmentRecord, segment_id)
        if first is not None and row.opening_angle_id != first:
            row.opening_angle_id = first
            session.flush()

    def _reopen_first_cut(self, session, cut, angle_id):
        """The first cut starts from the opening angle; a new angle resets its relation, as update_cut does."""
        row = _get(session, CutRecord, cut["id"])
        _bump(
            session,
            row,
            row.revision,
            from_angle_id=angle_id,
            relation=None,
            crosses_barrier=None,
            relation_source="unknown",
        )

    def phases(self, session, segment_id):
        _get(session, SegmentRecord, segment_id)
        return [
            _record(row)
            for row in session.scalars(
                select(PhaseRecord)
                .where(PhaseRecord.segment_id == segment_id)
                .order_by(PhaseRecord.start_frame)
            )
        ]

    def cuts(self, session, segment_id):
        _get(session, SegmentRecord, segment_id)
        return [
            _record(row)
            for row in session.scalars(
                select(CutRecord)
                .where(CutRecord.segment_id == segment_id)
                .order_by(CutRecord.frame)
            )
        ]

    def receipts(self, session, movie_id, limit=50):
        _get(session, MovieRecord, movie_id)
        if type(limit) is not int or limit < 1:
            raise ApplicationError("limit must be a positive integer.", 422)
        return [
            _record(row)
            for row in session.scalars(
                select(ReceiptRecord)
                .where(ReceiptRecord.movie_id == movie_id)
                .order_by(ReceiptRecord.created_at.desc(), ReceiptRecord.id.desc())
                .limit(limit)
            )
        ]

    def read_receipt(self, session, receipt_id):
        return _record(_get(session, ReceiptRecord, receipt_id))

    def assemble_segment(self, session, segment_id):
        segment = self.read_segment(session, segment_id)
        cast = [
            _validated(authored.cast_member, {key: row[key] for key in _SUBJECT_FIELDS})
            for row in self.subjects(session, segment["movie_id"])
        ]
        return _validated(
            authored.assemble,
            segment["content"],
            segment["content_schema"],
            cast,
            [(p["start_frame"], p["content"], p["content_schema"]) for p in segment["phases"]],
            [cut["frame"] for cut in segment["cuts"]],
            segment["delivered_frames"],
            segment["continues"],
        )

    def _segment_content(self, session, movie_id, document):
        content = _validated(authored.segment_content, document, authored.SEGMENT_SCHEMA)
        cast = {row["id"] for row in self.subjects(session, movie_id)}
        for subject_id in content.subject_ids:
            if subject_id not in cast:
                raise ApplicationError(f"Subject {subject_id} is not in the movie's cast.", 422)

    def create_movie(self, session, title):
        row = MovieRecord(**_new_record({"title": _title(title)}))
        session.add(row)
        session.flush()
        receipt = _receipt(
            session,
            row.id,
            "create_movie",
            "movie",
            row.id,
            {"revision": 0},
            {"revision": 1, "record": _record(row)},
        )
        return {**_record(row), "receipt": receipt}

    def update_movie(self, session, movie_id, expected_revision, *, title, _undoes=None):
        row = _get(session, MovieRecord, movie_id)
        _check_revision(row, expected_revision)
        return _updated(
            session, row, expected_revision, {"title": _title(title)}, movie_id, "movie", _undoes
        )

    def add_subject(
        self,
        session,
        movie_id,
        expected_revision,
        *,
        name,
        description,
        keeps,
        identity_asset_id,
        subject_id=None,
        _restore=None,
        _undoes=None,
    ):
        movie = _get(session, MovieRecord, movie_id)
        _check_revision(movie, expected_revision)
        values = {
            "id": new_id() if subject_id is None else subject_id,
            "name": name,
            "description": description,
            "keeps": keeps,
            "identity_asset_id": identity_asset_id,
        }
        values["keeps"] = list(_validated(authored.cast_member, values).keeps)
        if session.get(SubjectRecord, values["id"]) is not None:
            raise ApplicationError(f"Subject id {values['id']} already exists.", 409)
        _bump(session, movie, expected_revision)
        row = SubjectRecord(**_new_record({**values, "movie_id": movie_id}, _restore))
        session.add(row)
        receipt = _receipt(
            session,
            movie_id,
            "add_subject",
            "movie",
            movie_id,
            {"revision": expected_revision},
            {"revision": expected_revision + 1, "record": _record(row)},
            _undoes,
        )
        return {**_record(row), "receipt": receipt}

    def update_subject(
        self,
        session,
        subject_id,
        expected_revision,
        *,
        name=None,
        description=None,
        keeps=None,
        identity_asset_id=_UNSET,
        _undoes=None,
    ):
        row = _get(session, SubjectRecord, subject_id)
        _check_revision(row, expected_revision)
        changes = {
            key: value
            for key, value in {"name": name, "description": description, "keeps": keeps}.items()
            if value is not None
        }
        if identity_asset_id is not _UNSET:
            changes["identity_asset_id"] = identity_asset_id
        member = _validated(
            authored.cast_member, {**{key: getattr(row, key) for key in _SUBJECT_FIELDS}, **changes}
        )
        if "keeps" in changes:
            changes["keeps"] = list(member.keeps)
        return _updated(session, row, expected_revision, changes, row.movie_id, "subject", _undoes)

    def remove_subject(self, session, subject_id, expected_revision, *, _undoes=None):
        row = _get(session, SubjectRecord, subject_id)
        movie = _get(session, MovieRecord, row.movie_id)
        _check_revision(movie, expected_revision)
        for segment in self._segments(session, movie.id):
            content = _validated(authored.segment_content, segment.content, segment.content_schema)
            if subject_id in content.subject_ids:
                raise ApplicationError(
                    f"Subject {subject_id} appears in segment {segment.ordinal}; remove the reference first.",
                    409,
                )
        before = _record(row)
        _bump(session, movie, expected_revision)
        session.delete(row)
        receipt = _receipt(
            session,
            movie.id,
            "remove_subject",
            "movie",
            movie.id,
            {"revision": expected_revision, "record": before},
            {"revision": expected_revision + 1},
            _undoes,
        )
        return {"id": subject_id, "removed": True, "receipt": receipt}

    def _reorder(self, session, rows):
        # Vacate every occupied ordinal first; SQLite checks uniqueness per row, not per statement.
        for index, row in enumerate(rows):
            row.ordinal = -index - 1
        session.flush()
        for index, row in enumerate(rows):
            row.ordinal = index
        session.flush()

    def add_segment(
        self,
        session,
        movie_id,
        expected_revision,
        *,
        content,
        delivered_frames,
        continues,
        phases=(),
        at=None,
        continuation_method=None,
        context_frames=None,
        _restore=None,
        _undoes=None,
    ):
        movie = _get(session, MovieRecord, movie_id)
        _check_revision(movie, expected_revision)
        self._segment_content(session, movie_id, content)
        _frames(delivered_frames)
        _continues(continues)
        _handoff(continuation_method, context_frames)
        handoff = {
            key: value
            for key, value in zip(_HANDOFF, (continuation_method, context_frames))
            if value is not None
        }
        if handoff and not continues:
            raise ApplicationError("Only a continuing segment hands over motion context.", 422)
        starts = set()
        phases = [authored.phase_record(phase) for phase in phases]
        for start, document, schema in phases:
            _validated(authored.phase_content, document, schema)
            _frame(start, delivered_frames)
            if start in starts:
                raise ApplicationError(f"Phase frame {start} already exists.", 409)
            starts.add(start)
        rows = self._segments(session, movie_id)
        at = len(rows) if at is None else at
        if type(at) is not int or not 0 <= at <= len(rows):
            raise ApplicationError("at must be an ordinal between 0 and the segment count.", 422)
        _bump(session, movie, expected_revision)
        restored = {
            key: value for key, value in (_restore or {}).items() if key not in {"phases", "cuts"}
        }
        row = SegmentRecord(
            **_new_record(
                {
                    "movie_id": movie_id,
                    "ordinal": len(rows),
                    "delivered_frames": delivered_frames,
                    "continues": continues,
                    "content": content,
                    "content_schema": restored.get(
                        "content_schema", authored.segment_schema(content)
                    ),
                    # Absent, a restored segment keeps the handoff it was removed with.
                    **handoff,
                },
                restored,
            )
        )
        session.add(row)
        session.flush()
        rows.insert(at, row)
        self._reorder(session, rows)
        restored_phases = {p["start_frame"]: p for p in (_restore or {}).get("phases", [])}
        for start, document, schema in phases:
            session.add(
                PhaseRecord(
                    **_new_record(
                        {
                            "segment_id": row.id,
                            "start_frame": start,
                            "content": document,
                            "content_schema": schema,
                        },
                        restored_phases.get(start),
                    )
                )
            )
        session.flush()
        result = self.read_segment(session, row.id)
        receipt = _receipt(
            session,
            movie_id,
            "add_segment",
            "movie",
            movie_id,
            {"revision": expected_revision},
            {"revision": expected_revision + 1, "record": result},
            _undoes,
        )
        return {**result, "receipt": receipt}

    def update_segment(
        self,
        session,
        segment_id,
        expected_revision,
        *,
        content=None,
        delivered_frames=None,
        continues=None,
        opening_angle_id=_UNSET,
        opening_technique=_UNSET,
        continuation_method=_UNSET,
        context_frames=_UNSET,
        _undoes=None,
    ):
        row = _get(session, SegmentRecord, segment_id)
        _check_revision(row, expected_revision)
        changes = {}
        if content is not None:
            self._segment_content(session, row.movie_id, content)
            changes.update(content=content, content_schema=authored.SEGMENT_SCHEMA)
        if delivered_frames is not None:
            _frames(delivered_frames)
            for phase in self.phases(session, segment_id):
                _frame(phase["start_frame"], delivered_frames)
            for cut in self.cuts(session, segment_id):
                _frame(cut["frame"], delivered_frames, cut=True)
            changes["delivered_frames"] = delivered_frames
        if continues is not None:
            _continues(continues)
            changes["continues"] = continues
        if opening_angle_id is not _UNSET:
            cuts = self.cuts(session, segment_id)
            if opening_angle_id is None:
                if cuts:
                    raise ApplicationError(
                        "A segment with cuts opens on its first cut's starting angle.", 422
                    )
            else:
                _get(session, AngleRecord, _required(opening_angle_id, "opening_angle_id"))
                if cuts and cuts[0]["from_angle_id"] != opening_angle_id:
                    self._reopen_first_cut(session, cuts[0], opening_angle_id)
            changes["opening_angle_id"] = opening_angle_id
        if opening_technique is not _UNSET:
            if opening_technique is not None and (
                not isinstance(opening_technique, str) or not 0 < len(opening_technique) <= 80
            ):
                raise ApplicationError("opening_technique names a technique.", 422)
            changes["opening_technique"] = opening_technique
        for key, value in zip(_HANDOFF, (continuation_method, context_frames)):
            if value is not _UNSET:
                _handoff(**{k: value if k == key else None for k in _HANDOFF})
                changes[key] = value
        if not changes.get("continues", row.continues):
            if any(changes.get(key) is not None for key in _HANDOFF):
                raise ApplicationError("Only a continuing segment hands over motion context.", 422)
            # A segment that stops continuing drops its handoff in the same receipt, so Undo restores both.
            changes |= {key: None for key in _HANDOFF if getattr(row, key) is not None}
        result = _updated(
            session, row, expected_revision, changes, row.movie_id, "segment", _undoes
        )
        return {**self.read_segment(session, segment_id), "receipt": result["receipt"]}

    def remove_segment(self, session, segment_id, expected_revision, *, _undoes=None):
        row = _get(session, SegmentRecord, segment_id)
        movie = _get(session, MovieRecord, row.movie_id)
        _check_revision(movie, expected_revision)
        before = self.read_segment(session, segment_id)
        if before["cuts"]:
            raise ApplicationError("Remove the segment's cuts first.", 409)
        _bump(session, movie, expected_revision)
        for phase in before["phases"]:
            session.delete(_get(session, PhaseRecord, phase["id"]))
        session.flush()
        session.delete(row)
        session.flush()
        self._reorder(session, self._segments(session, movie.id))
        receipt = _receipt(
            session,
            movie.id,
            "remove_segment",
            "movie",
            movie.id,
            {"revision": expected_revision, "record": before},
            {"revision": expected_revision + 1},
            _undoes,
        )
        return {"id": segment_id, "removed": True, "receipt": receipt}

    def move_segment(self, session, segment_id, expected_revision, *, ordinal, _undoes=None):
        row = _get(session, SegmentRecord, segment_id)
        movie = _get(session, MovieRecord, row.movie_id)
        _check_revision(movie, expected_revision)
        rows = self._segments(session, movie.id)
        if type(ordinal) is not int or not 0 <= ordinal < len(rows):
            raise ApplicationError("ordinal must be between 0 and the last segment ordinal.", 422)
        previous = row.ordinal
        _bump(session, movie, expected_revision)
        rows.remove(row)
        rows.insert(ordinal, row)
        self._reorder(session, rows)
        receipt = _receipt(
            session,
            movie.id,
            "move_segment",
            "movie",
            movie.id,
            {"revision": expected_revision, "segment_id": segment_id, "ordinal": previous},
            {"revision": expected_revision + 1, "segment_id": segment_id, "ordinal": ordinal},
            _undoes,
        )
        return {**self.read_segment(session, segment_id), "receipt": receipt}

    def _phase_values(self, session, segment, start_frame, content, phase_id=None):
        _validated(authored.phase_content, content, authored.PHASE_SCHEMA)
        _frame(start_frame, segment.delivered_frames)
        if session.scalar(
            select(PhaseRecord.id).where(
                PhaseRecord.segment_id == segment.id,
                PhaseRecord.start_frame == start_frame,
                PhaseRecord.id != phase_id,
            )
        ):
            raise ApplicationError(f"Phase frame {start_frame} already exists.", 409)

    def split_points(self, session, segment_id):
        """Authoritative boundary availability, shared by the command and the editor."""
        segment = self.read_segment(session, segment_id)
        cuts = {cut["frame"]: cut for cut in segment["cuts"]}
        frames = sorted(set(cuts) | {p["start_frame"] for p in segment["phases"]})
        points = []
        for frame in frames:
            if not 0 < frame < segment["delivered_frames"]:
                continue
            code = reason = None
            cut = cuts.get(frame)
            if cut and self.keyframes(session, cut["id"]):
                code = "cut_has_keyframes"
                reason = "This cut has keyframes that require a cut anchor. Split at another phase boundary instead."
            elif cut and session.scalar(
                select(SuitabilityRecord.id).where(SuitabilityRecord.cut_id == cut["id"]).limit(1)
            ):
                code = "cut_has_suitability_history"
                reason = "This cut has suitability history that must keep its cut. Split at another phase boundary instead."
            if not segment["phases"] or segment["phases"][0]["start_frame"] != 0:
                code = "missing_initial_phase"
                reason = "Add a phase at frame 0 before splitting this segment."
            points.append({"frame": frame, "code": code, "reason": reason})
        return points

    def _split_state(self, session, movie_id, segment_ids):
        segments = [
            self.read_segment(session, sid)
            for sid in segment_ids
            if session.get(SegmentRecord, sid) is not None
        ]
        cuts = [c for s in segments for c in s["cuts"]]
        return {
            "movie": _record(_get(session, MovieRecord, movie_id)),
            "segment_ids": segment_ids,
            "segments": segments,
            "order": [s.id for s in self._segments(session, movie_id)],
            # SQL NULL and JSON null both read as None; preserve the stored representation.
            "null_insertions": sorted(
                session.scalars(
                    select(CutRecord.id).where(
                        CutRecord.id.in_([c["id"] for c in cuts]), CutRecord.insertion.is_(None)
                    )
                )
            ),
            "keyframes": sorted(
                [k for c in cuts for k in self.keyframes(session, c["id"])], key=lambda k: k["id"]
            ),
            "suitability": [
                _record(j)
                for j in session.scalars(
                    select(SuitabilityRecord)
                    .where(SuitabilityRecord.cut_id.in_([c["id"] for c in cuts]))
                    .order_by(SuitabilityRecord.id)
                )
            ],
        }

    @staticmethod
    def _split_rows(state):
        rows = {("movie", state["movie"]["id"]): state["movie"]}
        for segment in state["segments"]:
            rows["segment", segment["id"]] = {
                k: v for k, v in segment.items() if k not in {"phases", "cuts"}
            }
            for collection, kind in (("phases", "phase"), ("cuts", "cut")):
                rows.update({(kind, row["id"]): row for row in segment[collection]})
        return rows

    def _guard_split_state(self, session, expected, current):
        for key in ("order", "keyframes", "suitability"):
            if current[key] != expected[key]:
                raise ApplicationError(_CHANGED_AFTER, 409)
        rows = self._split_rows(current)
        expected_rows = self._split_rows(expected)
        if rows.keys() != expected_rows.keys():
            raise ApplicationError(_CHANGED_AFTER, 409)
        for (kind, rid), before in expected_rows.items():
            row = rows[kind, rid]
            if any(row[k] != v for k, v in before.items() if k not in {"revision", "updated_at"}):
                raise ApplicationError(_CHANGED_AFTER, 409)
            if not _revision_restored(session, kind, rid, row["revision"], before["revision"]):
                raise ApplicationError(_CHANGED_AFTER, 409)

    def _apply_split_state(self, session, current, desired):
        """Apply an already validated split/merge, preserving identities and metadata."""
        previous = self._split_rows(current)
        wanted = self._split_rows(desired)
        # Vacate child positions before moving rows between the halves (SQLite uniqueness).
        for (kind, rid), row in previous.items():
            if kind in {"phase", "cut"}:
                setattr(
                    _get(session, _MODELS[kind], rid),
                    "start_frame" if kind == "phase" else "frame",
                    -row.get("start_frame", row.get("frame")) - 1,
                )
        session.flush()
        for kind in ("movie", "segment", "phase", "cut"):
            for (row_kind, rid), values in wanted.items():
                if row_kind != kind:
                    continue
                row = session.get(_MODELS[kind], rid)
                if row is None:
                    values = deepcopy(values)
                    if kind == "cut" and rid in desired["null_insertions"]:
                        values["insertion"] = null()
                    if kind == "segment":
                        values["ordinal"] = len(current["order"])
                    session.add(_MODELS[kind](**values))
                    session.flush()
                    continue
                changes = {
                    k: v for k, v in values.items() if k not in {"id", "revision", "updated_at"}
                }
                if kind == "segment":
                    changes.pop("ordinal")  # order is restored below
                changed = any(
                    previous[kind, rid][k] != v for k, v in changes.items() if k != "updated_at"
                )
                if kind == "cut" and rid in desired["null_insertions"]:
                    changes["insertion"] = null()
                if changed or kind == "movie":
                    _bump(session, row, row.revision, **changes)
                else:
                    for key, value in changes.items():
                        setattr(row, key, deepcopy(value))
                # Undo restores authored metadata exactly; only revision counters advance.
                row.updated_at = values["updated_at"]
                session.flush()
        for kind in ("cut", "phase", "segment"):
            for row_kind, rid in previous.keys() - wanted.keys():
                if row_kind == kind:
                    session.delete(_get(session, _MODELS[kind], rid))
            session.flush()
        self._reorder(session, [_get(session, SegmentRecord, sid) for sid in desired["order"]])

    def _split_receipt(self, session, segment_id, before, after, undoes=None):
        previous, current = self._split_rows(before), self._split_rows(after)
        proofs = [{}, {}]
        for kind, rid in previous.keys() | current.keys():
            old = previous.get((kind, rid), {}).get("revision", 0)
            new = current.get((kind, rid), {}).get("revision", 0)
            if old != new:
                for proof, rev in zip(proofs, (old, new), strict=True):
                    proof.setdefault(kind, {})[rid] = rev
        return _receipt(
            session,
            before["movie"]["id"],
            "split_segment",
            "segment",
            segment_id,
            {
                "revision": previous["segment", segment_id]["revision"],
                "split_state": before,
                "revision_proofs": proofs[0],
            },
            {
                "revision": current["segment", segment_id]["revision"],
                "split_state": after,
                "revision_proofs": proofs[1],
            },
            undoes,
        )

    def split_segment(self, session, segment_id, expected_revision, frame):
        row = _get(session, SegmentRecord, segment_id)
        _check_revision(row, expected_revision)
        point = next(
            (
                p
                for p in self.split_points(session, segment_id)
                if type(frame) is int and p["frame"] == frame
            ),
            None,
        )
        if point is None:
            raise SegmentSplitRefusal("not_a_boundary", "Split at a cut or a phase boundary")
        if point["code"]:
            raise SegmentSplitRefusal(point["code"], point["reason"])
        new_segment_id = new_id()
        before = self._split_state(session, row.movie_id, [segment_id, new_segment_id])
        desired = deepcopy(before)
        left = desired["segments"][0]
        right = deepcopy(left)
        # A cut can be authored before its phase; carry the active direction into frame 0.
        if not any(p["start_frame"] == frame for p in right["phases"]):
            active = max(
                (p for p in right["phases"] if p["start_frame"] < frame),
                key=lambda p: p["start_frame"],
            )
            right["phases"].append(
                {
                    **deepcopy(active),
                    "id": new_id(),
                    "start_frame": frame,
                    "revision": 1,
                    "created_at": timestamp(),
                    "updated_at": timestamp(),
                }
            )
        left["content"], right["content"] = authored.split_content(
            left["content"], right["phases"], frame
        )
        right.update(
            id=new_segment_id,
            ordinal=left["ordinal"] + 1,
            delivered_frames=left["delivered_frames"] - frame,
            continues=True,
            revision=1,
            created_at=timestamp(),
            updated_at=timestamp(),
        )
        left.update(delivered_frames=frame, updated_at=timestamp())
        earlier = [c for c in left["cuts"] if c["frame"] <= frame]
        right.update(
            opening_angle_id=earlier[-1]["to_angle_id"] if earlier else left["opening_angle_id"],
            opening_technique=None,
            # The new join follows the continuation rule until someone chooses otherwise.
            continuation_method=None,
            context_frames=None,
        )
        for half, is_right in ((left, False), (right, True)):
            for collection, key in (("phases", "start_frame"), ("cuts", "frame")):
                half[collection] = [
                    {
                        **p,
                        "segment_id": half["id"],
                        key: p[key] - (frame if is_right else 0),
                        **({"updated_at": timestamp()} if is_right else {}),
                    }
                    for p in half[collection]
                    if (p[key] >= frame) == is_right
                    and not (collection == "cuts" and p[key] == frame)
                ]
            self._segment_content(session, row.movie_id, half["content"])
        desired["segments"].append(right)
        desired["order"].insert(left["ordinal"] + 1, new_segment_id)
        desired["movie"]["updated_at"] = timestamp()
        self._apply_split_state(session, before, desired)
        after = self._split_state(session, row.movie_id, before["segment_ids"])
        receipt = self._split_receipt(session, segment_id, before, after)
        return {"segments": after["segments"], "receipt": receipt}

    def _merge_split(self, session, receipt):
        expected = receipt.after["split_state"]
        current = self._split_state(session, receipt.movie_id, expected["segment_ids"])
        self._guard_split_state(session, expected, current)
        self._apply_split_state(session, current, receipt.before["split_state"])
        after = self._split_state(session, receipt.movie_id, expected["segment_ids"])
        inverse = self._split_receipt(session, receipt.target_id, current, after, receipt.id)
        return {"segments": after["segments"], "receipt": inverse}

    def add_phase(
        self,
        session,
        segment_id,
        expected_revision,
        *,
        start_frame,
        content,
        _restore=None,
        _undoes=None,
    ):
        segment = _get(session, SegmentRecord, segment_id)
        _check_revision(segment, expected_revision)
        self._phase_values(session, segment, start_frame, content)
        _bump(session, segment, expected_revision)
        row = PhaseRecord(
            **_new_record(
                {
                    "segment_id": segment_id,
                    "start_frame": start_frame,
                    "content": content,
                    "content_schema": (_restore or {}).get("content_schema", authored.PHASE_SCHEMA),
                },
                _restore,
            )
        )
        session.add(row)
        receipt = _receipt(
            session,
            segment.movie_id,
            "add_phase",
            "segment",
            segment_id,
            {"revision": expected_revision},
            {"revision": expected_revision + 1, "record": _record(row)},
            _undoes,
        )
        return {**_record(row), "receipt": receipt}

    def update_phase(
        self, session, phase_id, expected_revision, *, start_frame=None, content=None, _undoes=None
    ):
        row = _get(session, PhaseRecord, phase_id)
        _check_revision(row, expected_revision)
        segment = _get(session, SegmentRecord, row.segment_id)
        self._phase_values(
            session,
            segment,
            row.start_frame if start_frame is None else start_frame,
            row.content if content is None else content,
            phase_id,
        )
        changes = {}
        if content is not None:
            changes.update(content=content, content_schema=authored.PHASE_SCHEMA)
        if start_frame is not None:
            changes["start_frame"] = start_frame
        return _updated(
            session, row, expected_revision, changes, segment.movie_id, "phase", _undoes
        )

    def remove_phase(self, session, phase_id, expected_revision, *, _undoes=None):
        row = _get(session, PhaseRecord, phase_id)
        segment = _get(session, SegmentRecord, row.segment_id)
        _check_revision(segment, expected_revision)
        before = _record(row)
        _bump(session, segment, expected_revision)
        session.delete(row)
        receipt = _receipt(
            session,
            segment.movie_id,
            "remove_phase",
            "segment",
            segment.id,
            {"revision": expected_revision, "record": before},
            {"revision": expected_revision + 1},
            _undoes,
        )
        return {"id": phase_id, "removed": True, "receipt": receipt}

    def places(self, session: Session) -> list[dict]:
        return [
            _record(row)
            for row in session.scalars(
                select(PlaceRecord).order_by(PlaceRecord.created_at, PlaceRecord.id)
            )
        ]

    def read_place(self, session: Session, place_id: str) -> dict:
        return _record(_get(session, PlaceRecord, place_id))

    def _update_world_text(self, session, model, record_id, expected_revision, changes):
        row = _get(session, model, record_id)
        _check_revision(row, expected_revision)
        if not changes:
            raise ApplicationError("Specify at least one field to change.", 422)
        for key, value in changes.items():
            if key != "placement" and not isinstance(value, str):
                raise ApplicationError(f"{key} must be text.", 422)
        changed = session.execute(
            update(model)
            .where(
                model.id == record_id,
                model.revision == expected_revision,
            )
            .values(**deepcopy(changes), revision=expected_revision + 1)
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise ApplicationError(
                f"{model.__name__.removesuffix('Record')} revision is stale.", 409
            )
        session.refresh(row)
        return _record(row)

    def update_place(
        self,
        session,
        place_id,
        expected_revision,
        *,
        description=None,
        sound=None,
        overhead=None,
        movie_id=None,
        _undoes=None,
    ):
        changes = {
            key: value
            for key, value in (
                ("description", description),
                ("sound", sound),
                ("overhead", overhead),
            )
            if value is not None
        }
        before = self.read_place(session, place_id)
        if movie_id is not None:
            _get(session, MovieRecord, movie_id)
        result = self._update_world_text(session, PlaceRecord, place_id, expected_revision, changes)
        if movie_id is not None:
            result["receipt"] = _receipt(
                session,
                movie_id,
                "update_place",
                "place",
                place_id,
                {"revision": expected_revision, **{key: before[key] for key in changes}},
                {"revision": result["revision"], **changes},
                _undoes,
            )
        return result

    def update_angle(
        self,
        session,
        angle_id,
        expected_revision,
        *,
        description=None,
        placement=None,
        name=None,
        movie_id=None,
        _undoes=None,
    ):
        changes = {}
        if name is not None:
            changes["name"] = _required(name, "name")
            if len(changes["name"]) > 120:
                raise ApplicationError("Angle name exceeds 120 characters.", 422)
        if description is not None:
            changes["description"] = description
        if placement is not None:
            changes["placement"] = _placement(placement)
        before = self.read_angle(session, angle_id)
        if movie_id is not None:
            _get(session, MovieRecord, movie_id)
        result = self._update_world_text(session, AngleRecord, angle_id, expected_revision, changes)
        if movie_id is not None:
            result["receipt"] = _receipt(
                session,
                movie_id,
                "update_angle",
                "angle",
                angle_id,
                {"revision": expected_revision, **{key: before[key] for key in changes}},
                {"revision": result["revision"], **changes},
                _undoes,
            )
        return result

    def set_plate(self, session: Session, place_id: str, *, asset_id: str, angle_id: str) -> dict:
        place = _get(session, PlaceRecord, place_id)
        angle = _get(session, AngleRecord, angle_id)
        if angle.place_id != place_id:
            raise ApplicationError("The plate angle must belong to this place.", 422)
        asset_id = _required(asset_id, "asset_id")
        if place.plate_asset_id is not None:
            if place.plate_asset_id == asset_id:
                return _record(place)
            raise ApplicationError("This place already has a different plate.", 409)
        place.plate_asset_id = asset_id
        place.plate_angle_id = angle_id
        session.flush()
        return _record(place)

    def segments_opening_in(self, session, place_id) -> list[dict]:
        """The segments whose first shot opens on an angle of this place, with their movie's title."""
        rows = session.execute(
            select(SegmentRecord, MovieRecord.title)
            .join(AngleRecord, AngleRecord.id == SegmentRecord.opening_angle_id)
            .join(MovieRecord, MovieRecord.id == SegmentRecord.movie_id)
            .where(AngleRecord.place_id == place_id)
            .order_by(MovieRecord.title, SegmentRecord.ordinal)
        )
        return [
            {"segment_id": s.id, "movie_id": s.movie_id, "movie": title, "ordinal": s.ordinal}
            for s, title in rows
        ]

    def set_place_set(self, session, place_id, expected_revision, *, set_id):
        """Name the set this place is built as, or None. The application checks that the set exists."""
        row = _get(session, PlaceRecord, place_id)
        _check_revision(row, expected_revision)
        if set_id is not None:
            set_id = _required(set_id, "set_id")
        # Places keep no updated_at (their text edits share this compare-and-set, `_update_world_text`).
        changed = session.execute(
            update(PlaceRecord)
            .where(PlaceRecord.id == place_id, PlaceRecord.revision == expected_revision)
            .values(set_id=set_id, revision=expected_revision + 1)
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise ApplicationError("Place revision is stale.", 409)
        session.refresh(row)
        return _record(row)

    def angles(self, session: Session, place_id: str) -> list[dict]:
        _get(session, PlaceRecord, place_id)
        return [
            _record(row)
            for row in session.scalars(
                select(AngleRecord)
                .where(AngleRecord.place_id == place_id)
                .order_by(AngleRecord.created_at, AngleRecord.id)
            )
        ]

    def approve_plate_replacement(
        self, session, place_id, expected_revision, *, asset_id, angle_id, approved_by
    ):
        """Human plate review only; preserve prior bindings and their approval inputs.

        Receipts belong to movies. Anchor shared-place changes to the first movie
        using the place, without introducing another receipt/DTO target kind.
        """
        if approved_by != "user":
            raise ApplicationError("Only user approval can replace a plate.", 422)
        place = _get(session, PlaceRecord, place_id)
        _check_revision(place, expected_revision)
        angle = _get(session, AngleRecord, angle_id)
        if angle.place_id != place_id:
            raise ApplicationError("The plate angle must belong to this place.", 422)
        asset_id = _required(asset_id, "asset_id")
        if place.plate_asset_id is None:
            raise ApplicationError("Approve an initial plate before replacing it.", 409)
        movie_id = session.scalar(
            select(SegmentRecord.movie_id)
            .outerjoin(CutRecord, CutRecord.segment_id == SegmentRecord.id)
            .join(
                AngleRecord,
                or_(
                    AngleRecord.id == SegmentRecord.opening_angle_id,
                    AngleRecord.id == CutRecord.from_angle_id,
                ),
            )
            .where(AngleRecord.place_id == place_id)
            .order_by(SegmentRecord.created_at, SegmentRecord.id)
            .limit(1)
        )
        if movie_id is None:
            raise ApplicationError(
                "Plate replacement needs a movie using this place for its undo history.", 409
            )
        return self._replace_plate(session, place, asset_id, angle_id, movie_id)

    def _replace_plate(self, session, place, asset_id, angle_id, movie_id, undoes=None):
        movie = _get(session, MovieRecord, movie_id)

        def snapshot():
            return {
                "revision": movie.revision,
                "plate": {
                    "place_id": place.id,
                    "revision": place.revision,
                    "asset_id": place.plate_asset_id,
                    "angle_id": place.plate_angle_id,
                },
            }

        before = snapshot()
        expected = place.revision
        result = session.execute(
            update(PlaceRecord)
            .where(PlaceRecord.id == place.id, PlaceRecord.revision == expected)
            .values(plate_asset_id=asset_id, plate_angle_id=angle_id, revision=expected + 1)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ApplicationError("Place revision is stale.", 409)
        session.refresh(place)
        _bump(session, movie, movie.revision)
        receipt = _receipt(
            session, movie_id, "replace_plate", "movie", movie_id, before, snapshot(), undoes
        )
        return {**_record(place), "receipt": receipt}

    def read_angle(self, session: Session, angle_id: str) -> dict:
        return _record(_get(session, AngleRecord, angle_id))

    def evidence(self, session: Session, angle_id: str) -> list[dict]:
        _get(session, AngleRecord, angle_id)
        return [
            _record(row)
            for row in session.scalars(
                select(EvidenceRecord)
                .where(EvidenceRecord.angle_id == angle_id)
                .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
            )
        ]

    def read_evidence(self, session: Session, evidence_id: str) -> dict:
        return _record(_get(session, EvidenceRecord, evidence_id))

    def evidence_for_assets(self, session: Session, asset_ids) -> list[dict]:
        """The evidence showing any of these assets, with its angle and place (a set view's references)."""
        rows = session.execute(
            select(EvidenceRecord, AngleRecord, PlaceRecord)
            .join(AngleRecord, AngleRecord.id == EvidenceRecord.angle_id)
            .join(PlaceRecord, PlaceRecord.id == AngleRecord.place_id)
            .where(EvidenceRecord.asset_id.in_(list(asset_ids)))
            .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
        )
        return [
            {
                **_record(evidence),
                "angle_name": angle.name,
                "place_id": place.id,
                "place_name": place.name,
            }
            for evidence, angle, place in rows
        ]

    def keyframes(self, session: Session, cut_id: str) -> list[dict]:
        _get(session, CutRecord, cut_id)
        return [
            _record(row)
            for row in session.scalars(
                select(KeyframeRecord)
                .where(KeyframeRecord.cut_id == cut_id)
                .order_by(KeyframeRecord.created_at, KeyframeRecord.id)
            )
        ]

    def create_place(self, session: Session, name: str) -> dict:
        name = _required(name, "name")
        if len(name) > 120:
            raise ApplicationError("Place name exceeds 120 characters.", 422)
        row = PlaceRecord(id=new_id(), name=name, created_at=timestamp())
        session.add(row)
        session.flush()
        return _record(row)

    def add_angle(
        self, session: Session, place_id: str, name: str, placement: dict | None = None
    ) -> dict:
        _get(session, PlaceRecord, place_id)
        name = _required(name, "name")
        if len(name) > 120:
            raise ApplicationError("Angle name exceeds 120 characters.", 422)
        created_at = timestamp()
        previous = session.scalar(
            select(AngleRecord.created_at)
            .where(AngleRecord.place_id == place_id)
            .order_by(AngleRecord.created_at.desc())
            .limit(1)
        )
        # Survey and sheet order is creation order. A coarse workstation clock can
        # assign equal timestamps to consecutive inserts; UUIDs cannot break that tie.
        if previous is not None and created_at <= previous:
            created_at = (datetime.fromisoformat(previous) + timedelta(microseconds=1)).isoformat()
        row = AngleRecord(
            id=new_id(),
            place_id=place_id,
            name=name,
            placement=_placement(placement),
            created_at=created_at,
        )
        session.add(row)
        session.flush()
        return _record(row)

    def record_evidence(self, session: Session, angle_id: str, kind: str, asset_id: str) -> dict:
        _get(session, AngleRecord, angle_id)
        if kind not in {"survey_view", "take_frame", "imported", "set_view"}:
            raise ApplicationError("Unknown evidence kind.", 422)
        row = EvidenceRecord(
            id=new_id(),
            angle_id=angle_id,
            kind=kind,
            asset_id=_required(asset_id, "asset_id"),
            approved=False,
            created_at=timestamp(),
        )
        session.add(row)
        session.flush()
        return _record(row)

    def approve_evidence(
        self, session: Session, evidence_id: str, approved_by: str = "user"
    ) -> dict:
        if approved_by != "user":
            raise ApplicationError("Only user approval is supported.", 422)
        row = _get(session, EvidenceRecord, evidence_id)
        if row.approved and not self._view_staleness(session, row)[0]:
            raise ApplicationError("Evidence is already approved.", 409)
        row.inputs = self._view_inputs(session, row)
        row.approved = True
        row.approved_at = timestamp()
        row.approved_by = approved_by
        session.flush()
        return _record(row)

    def record_keyframe(
        self,
        session: Session,
        cut_id: str,
        asset_id: str,
        evidence_id: str,
        subject_asset_id: str | None = None,
    ) -> dict:
        cut = _get(session, CutRecord, cut_id)
        evidence = _get(session, EvidenceRecord, evidence_id)
        if evidence.angle_id != cut.to_angle_id:
            raise ApplicationError("Keyframe evidence must show the cut's target angle.", 422)
        row = KeyframeRecord(
            id=new_id(),
            cut_id=cut_id,
            asset_id=_required(asset_id, "asset_id"),
            evidence_id=evidence_id,
            subject_asset_id=_required(subject_asset_id, "subject_asset_id")
            if subject_asset_id is not None
            else None,
            approved=False,
            created_at=timestamp(),
        )
        session.add(row)
        session.flush()
        return _record(row)

    def approve_keyframe(
        self, session: Session, keyframe_id: str, approved_by: str = "user"
    ) -> dict:
        if approved_by != "user":
            raise ApplicationError("Only user approval is supported.", 422)
        row = _get(session, KeyframeRecord, keyframe_id)
        if any(k["approved"] and k["id"] != row.id for k in self.keyframes(session, row.cut_id)):
            raise ApplicationError(
                "This cut already has a different approved keyframe. Use replace: true to replace it.",
                409,
            )
        return self._approve_keyframe(session, row, approved_by)

    def _approve_keyframe(self, session, row, approved_by):
        cut = _get(session, CutRecord, row.cut_id)
        evidence = _get(session, EvidenceRecord, row.evidence_id)
        if not evidence.approved or evidence.angle_id != cut.to_angle_id:
            raise ApplicationError("Approve the target angle's evidence first.", 422)
        if self._view_staleness(session, evidence)[0]:
            raise ApplicationError("Re-approve the stale target angle's evidence first.", 422)
        if row.approved and not self._pin_staleness(session, row)[0]:
            raise ApplicationError("Keyframe is already approved.", 409)
        row.inputs = self._pin_inputs(session, row)
        row.approved = True
        row.approved_at = timestamp()
        row.approved_by = approved_by
        session.flush()
        return _record(row)

    def approve_keyframe_replacement(self, session, keyframe_id, *, approved_by):
        """Replace only on explicit human approval; retain both approval snapshots."""
        if approved_by != "user":
            raise ApplicationError("Only user approval can replace a pin.", 422)
        row = _get(session, KeyframeRecord, keyframe_id)
        current = [k for k in self.keyframes(session, row.cut_id) if k["approved"]]
        if len(current) != 1 or current[0]["id"] == keyframe_id:
            raise ApplicationError("Pin replacement needs a different current pin.", 409)
        self._approve_keyframe(session, row, approved_by)
        # Preserve the new approval even when Undo later supersedes this pin.
        row.approved = False
        session.flush()
        return self._replace_keyframe(session, current[0]["id"], row.id)

    def _replace_keyframe(self, session, old_id, new_id, undoes=None):
        old = _get(session, KeyframeRecord, old_id)
        new = _get(session, KeyframeRecord, new_id)
        cut = _get(session, CutRecord, old.cut_id)
        segment = _get(session, SegmentRecord, cut.segment_id)

        def snapshot():
            return {"revision": cut.revision, "pins": [_record(old), _record(new)]}

        before = snapshot()
        _bump(session, cut, cut.revision)
        old.approved, new.approved = False, True
        session.flush()
        receipt = _receipt(
            session, segment.movie_id, "replace_pin", "cut", cut.id, before, snapshot(), undoes
        )
        return {**_record(new), "receipt": receipt}

    def create_cut(
        self,
        session: Session,
        segment_id: str,
        expected_revision: int,
        *,
        frame: int,
        from_angle_id: str,
        to_angle_id: str,
        relation: str | None = None,
        crosses_barrier: bool | None = None,
        relation_source: str = "unknown",
        receives_predecessor: bool = False,
        side_facts: bool = False,
        fidelity: bool = False,
        override_technique: str | None = None,
        _restore=None,
        _undoes=None,
    ) -> dict:
        segment = _get(session, SegmentRecord, segment_id)
        _check_revision(segment, expected_revision)
        values = _cut_values(
            {
                "frame": frame,
                "from_angle_id": from_angle_id,
                "to_angle_id": to_angle_id,
                "relation": relation,
                "crosses_barrier": crosses_barrier,
                "relation_source": relation_source,
                "receives_predecessor": receives_predecessor,
                "side_facts": side_facts,
                "fidelity": fidelity,
                "override_technique": override_technique,
            }
        )
        _get(session, AngleRecord, values["from_angle_id"])
        _get(session, AngleRecord, values["to_angle_id"])
        self._cut_frame(session, segment, frame)
        self._cut_chain(session, segment_id, values)
        _bump(session, segment, expected_revision)
        row = CutRecord(
            **{
                "id": new_id(),
                "revision": 1,
                "updated_at": timestamp(),
                **(_restore or {}),
                "segment_id": segment_id,
                **values,
            }
        )
        session.add(row)
        self._sync_opening(session, segment_id)
        receipt = _receipt(
            session,
            segment.movie_id,
            "create_cut",
            "segment",
            segment_id,
            {"revision": expected_revision},
            {"revision": expected_revision + 1, "record": _record(row)},
            _undoes,
        )
        return {**_record(row), "receipt": receipt}

    def _cut_frame(self, session, segment, frame, cut_id=None):
        _frame(frame, segment.delivered_frames, cut=True)
        if session.scalar(
            select(CutRecord.id).where(
                CutRecord.segment_id == segment.id, CutRecord.frame == frame, CutRecord.id != cut_id
            )
        ):
            raise ApplicationError(f"Cut frame {frame} already exists.", 409)

    def _cut_chain(self, session, segment_id, replacement=None, cut_id=None):
        cuts = [c for c in self.cuts(session, segment_id) if c["id"] != cut_id]
        if replacement is not None:
            cuts.append(replacement)
        cuts.sort(key=lambda c: c["frame"])
        for index, current in enumerate(cuts[1:]):
            previous = cuts[index]
            if current["from_angle_id"] != previous["to_angle_id"]:
                raise ApplicationError(
                    f"Cuts chain: the cut at frame {current['frame']} must start from the "
                    f"previous cut's target angle {previous['to_angle_id']}. "
                    "Move or remove the existing cut instead of creating a duplicate reverse.",
                    422,
                )

    def validate_cut_insertion(self, session, cut_id, wording):
        """Scene retention must exclude the cast's personal keeps as well as clothing."""
        cut = self.read_cut(session, cut_id)
        segment = self.read_segment(session, cut["segment_id"])
        keeps = [
            item
            for subject_id in segment["content"]["subjects"]
            for item in self.read_subject(session, subject_id)["keeps"]
        ]
        return validate_insertion_wording(wording, keeps=keeps)

    def update_cut(
        self, session: Session, cut_id: str, expected_revision: int, *, _undoes=None, **changes
    ) -> dict:
        row = _get(session, CutRecord, cut_id)
        _check_revision(row, expected_revision)
        old = self.read_cut(session, cut_id)
        if changes.get("insertion") is not None:
            changes["insertion"] = self.validate_cut_insertion(
                session, cut_id, changes["insertion"]
            )
        if not changes:
            raise ApplicationError("Specify at least one cut field to change.", 422)
        angle_changed = any(
            key in changes and changes[key] != old[key] for key in ("from_angle_id", "to_angle_id")
        )
        if (
            angle_changed
            and not {
                "relation",
                "crosses_barrier",
                "relation_source",
            }
            <= changes.keys()
        ):
            changes = {
                **changes,
                "relation": None,
                "crosses_barrier": None,
                "relation_source": "unknown",
            }
        values = _cut_values(
            {
                **{
                    key: old[key]
                    for key in (
                        "frame",
                        "from_angle_id",
                        "to_angle_id",
                        "relation",
                        "crosses_barrier",
                        "relation_source",
                        "receives_predecessor",
                        "side_facts",
                        "fidelity",
                        "override_technique",
                    )
                },
                **changes,
            }
        )
        _get(session, AngleRecord, values["from_angle_id"])
        _get(session, AngleRecord, values["to_angle_id"])
        segment = _get(session, SegmentRecord, row.segment_id)
        self._cut_frame(session, segment, values["frame"], cut_id)
        self._cut_chain(session, segment.id, values, cut_id)
        if values["frame"] != old["frame"]:
            result = self._move_cut(session, row, segment, values, _undoes)
        else:
            result = _updated(
                session,
                row,
                expected_revision,
                {key: values[key] for key in changes},
                segment.movie_id,
                "cut",
                _undoes,
            )
        self._sync_opening(session, segment.id)
        return result

    def _cut_move_state(self, session, row, segment):
        return {
            "revision": row.revision,
            "cut": _record(row),
            "segment_revision": segment.revision,
            "phases": self.phases(session, segment.id),
        }

    def _cut_move_receipt(self, session, row, segment, before, undoes):
        after = self._cut_move_state(session, row, segment)

        def state(value):
            return {
                "revision": value["revision"],
                "cut_move": value,
                "revision_proofs": {
                    "segment": {segment.id: value["segment_revision"]},
                    "phase": {p["id"]: p["revision"] for p in value["phases"]},
                },
            }

        return _receipt(
            session,
            segment.movie_id,
            "move_cut",
            "cut",
            row.id,
            state(before),
            state(after),
            undoes,
        )

    def _move_cut(self, session, row, segment, values, undoes):
        before = self._cut_move_state(session, row, segment)
        phases = before["phases"]
        target = next((p for p in phases if p["start_frame"] == values["frame"]), None)
        source = next((p for p in phases if p["start_frame"] == row.frame), None)
        active = source or next((p for p in reversed(phases) if p["start_frame"] < row.frame), None)
        if active is None:
            raise ApplicationError("Add a phase describing the cut's shot before moving it.", 422)
        if target and any(target[k] != active[k] for k in ("content", "content_schema")):
            raise ApplicationError(
                "A phase already exists at the new cut frame; moving would overwrite its direction. "
                "Choose a free frame or edit/remove that phase first.",
                409,
            )
        if target:
            pass  # An identical authored phase already preserves the cut's direction.
        elif source is None:
            session.add(
                PhaseRecord(
                    **{
                        **active,
                        "id": new_id(),
                        "start_frame": values["frame"],
                        "revision": 1,
                        "updated_at": timestamp(),
                    }
                )
            )
        else:
            _bump(
                session,
                _get(session, PhaseRecord, source["id"]),
                source["revision"],
                start_frame=values["frame"],
            )
        _bump(session, segment, segment.revision)
        _bump(session, row, row.revision, **values)
        session.flush()
        receipt = self._cut_move_receipt(session, row, segment, before, undoes)
        return {**_record(row), "receipt": receipt}

    def _undo_cut_move(self, session, receipt):
        expected = receipt.after["cut_move"]
        desired = receipt.before["cut_move"]
        row = _get(session, CutRecord, receipt.target_id)
        segment = _get(session, SegmentRecord, row.segment_id)
        current = self._cut_move_state(session, row, segment)
        for kind, actual, recorded in (
            ("segment", segment.revision, expected["segment_revision"]),
            ("cut", row.revision, expected["revision"]),
        ):
            if not _revision_restored(
                session, kind, segment.id if kind == "segment" else row.id, actual, recorded
            ):
                raise ApplicationError(_CHANGED_AFTER, 409)
        actual_phases = {p["id"]: p for p in current["phases"]}
        if actual_phases.keys() != {p["id"] for p in expected["phases"]}:
            raise ApplicationError(_CHANGED_AFTER, 409)
        for phase in expected["phases"]:
            actual = actual_phases[phase["id"]]
            if not _revision_restored(
                session, "phase", phase["id"], actual["revision"], phase["revision"]
            ):
                raise ApplicationError(_CHANGED_AFTER, 409)
        self._cut_frame(session, segment, desired["cut"]["frame"], row.id)
        self._cut_chain(session, segment.id, desired["cut"], row.id)
        wanted = {p["id"]: p for p in desired["phases"]}
        for phase in current["phases"]:
            child = _get(session, PhaseRecord, phase["id"])
            if phase["id"] not in wanted:
                session.delete(child)
            elif phase["start_frame"] != wanted[phase["id"]]["start_frame"]:
                _bump(
                    session, child, child.revision, start_frame=wanted[phase["id"]]["start_frame"]
                )
        for phase in desired["phases"]:
            if phase["id"] not in actual_phases:
                session.add(PhaseRecord(**phase))
        changes = {
            k: v
            for k, v in desired["cut"].items()
            if k not in {"id", "revision", "updated_at", "segment_id"}
        }
        _bump(session, row, row.revision, **changes)
        _bump(session, segment, segment.revision)
        self._sync_opening(session, segment.id)
        inverse = self._cut_move_receipt(session, row, segment, current, receipt.id)
        return {**_record(row), "receipt": inverse}

    def remove_cut(self, session, cut_id, expected_revision, *, _undoes=None):
        """Remove with the segment revision; keep authored phases and dependent history."""
        row = _get(session, CutRecord, cut_id)
        segment = _get(session, SegmentRecord, row.segment_id)
        _check_revision(segment, expected_revision)
        if self.keyframes(session, cut_id):
            raise ApplicationError("A cut with recorded keyframes cannot be removed.", 409)
        if session.scalar(
            select(SuitabilityRecord.id).where(SuitabilityRecord.cut_id == cut_id).limit(1)
        ):
            raise ApplicationError("A cut with suitability history cannot be removed.", 409)
        self._cut_chain(session, segment.id, cut_id=cut_id)
        before = _record(row)
        _bump(session, segment, expected_revision)
        session.delete(row)
        self._sync_opening(session, segment.id)
        receipt = _receipt(
            session,
            segment.movie_id,
            "remove_cut",
            "segment",
            segment.id,
            {"revision": expected_revision, "record": before},
            {"revision": expected_revision + 1},
            _undoes,
        )
        return {"id": cut_id, "removed": True, "receipt": receipt}

    def undo(self, session, receipt_id):
        original = _get(session, ReceiptRecord, receipt_id)
        if original.undone_at is not None:
            raise ApplicationError("This command was already undone.", 409)
        if "record" in original.after and "title" in original.after["record"]:
            raise ApplicationError("A movie is not removed by undo.", 409)
        model = PlaceRecord if original.target_kind == "place" else _MODELS[original.target_kind]
        target = session.get(model, original.target_id)
        guarded_creation = "created_revision" in original.after
        guarded_split = "split_state" in original.after or "cut_move" in original.after
        guarded_pins = "pins" in original.after
        # Later creations may already have been undone. Parent revisions remain
        # monotonic; use the same inverse-receipt proof as the child guard.
        if target is None or not (
            _revision_restored(
                session,
                original.target_kind,
                original.target_id,
                target.revision,
                original.after["revision"],
            )
            if guarded_creation or guarded_split or guarded_pins
            else target.revision == original.after["revision"]
        ):
            raise ApplicationError(_CHANGED_AFTER, 409)
        if guarded_creation:
            self._check_created(session, original)
        # The inverse validates before writing and shares the caller's transaction, just
        # like any command. In particular it must never commit via an outermost savepoint.
        result = self._inverse(session, original, target.revision)
        original.undone_at = timestamp()
        session.flush()
        receipt = result.pop("receipt")
        return {**receipt, "record": result}

    def _check_created(self, session, receipt):
        kind = receipt.after["created_kind"]
        before = receipt.after["record"]
        row = session.get(_MODELS[kind], before["id"])
        if row is None or not _revision_restored(
            session, kind, before["id"], row.revision, receipt.after["created_revision"]
        ):
            raise ApplicationError(_CHANGED_AFTER, 409)
        if kind == "segment":
            # add_segment creates its initial phases in the same command. Phase/cut
            # prose edits do not bump their parent, so protect those children too.
            current = self.read_segment(session, row.id)
            for collection, child_kind in (("phases", "phase"), ("cuts", "cut")):
                children = {child["id"]: child for child in current[collection]}
                if set(children) != {child["id"] for child in before[collection]}:
                    raise ApplicationError(_CHANGED_AFTER, 409)
                for child in before[collection]:
                    if not _revision_restored(
                        session,
                        child_kind,
                        child["id"],
                        children[child["id"]]["revision"],
                        child["revision"],
                    ):
                        raise ApplicationError(_CHANGED_AFTER, 409)

    def _inverse(self, session, receipt, revision):
        options = {"_undoes": receipt.id}
        if "pins" in receipt.after:
            old, new = receipt.after["pins"]
            if any(
                _record(_get(session, KeyframeRecord, pin["id"])) != pin for pin in (old, new)
            ) or [k["id"] for k in self.keyframes(session, receipt.target_id) if k["approved"]] != [
                new["id"]
            ]:
                raise ApplicationError(_CHANGED_AFTER, 409)
            return self._replace_keyframe(session, new["id"], old["id"], receipt.id)
        if "cut_move" in receipt.after:
            return self._undo_cut_move(session, receipt)
        if receipt.target_kind == "place":
            return self.update_place(
                session,
                receipt.target_id,
                revision,
                movie_id=receipt.movie_id,
                **{k: v for k, v in receipt.before.items() if k != "revision"},
                **options,
            )
        if "plate" in receipt.after:
            previous, current = receipt.before["plate"], receipt.after["plate"]
            place = _get(session, PlaceRecord, current["place_id"])
            if (
                place.revision != current["revision"]
                or place.plate_asset_id != current["asset_id"]
                or place.plate_angle_id != current["angle_id"]
            ):
                raise ApplicationError(_CHANGED_AFTER, 409)
            return self._replace_plate(
                session,
                place,
                previous["asset_id"],
                previous["angle_id"],
                receipt.movie_id,
                receipt.id,
            )
        if receipt.target_kind == "angle":
            return self.update_angle(
                session,
                receipt.target_id,
                revision,
                movie_id=receipt.movie_id,
                **{k: v for k, v in receipt.before.items() if k != "revision"},
                **options,
            )
        if "split_state" in receipt.after:
            return self._merge_split(session, receipt)
        if "record" in receipt.after:
            record = receipt.after["record"]
            if "ordinal" in record:
                return self.remove_segment(session, record["id"], revision, **options)
            if "keeps" in record:
                return self.remove_subject(session, record["id"], revision, **options)
            if "start_frame" in record:
                return self.remove_phase(session, record["id"], revision, **options)
            return self.remove_cut(session, record["id"], revision, **options)
        if "record" in receipt.before:
            record = receipt.before["record"]
            options["_restore"] = record
            if "ordinal" in record:
                return self.add_segment(
                    session,
                    record["movie_id"],
                    revision,
                    content=record["content"],
                    delivered_frames=record["delivered_frames"],
                    continues=record["continues"],
                    at=record["ordinal"],
                    phases=[
                        (p["start_frame"], p["content"], p["content_schema"])
                        for p in record["phases"]
                    ],
                    **options,
                )
            if "keeps" in record:
                return self.add_subject(
                    session,
                    record["movie_id"],
                    revision,
                    subject_id=record["id"],
                    **{key: record[key] for key in _SUBJECT_FIELDS if key != "id"},
                    **options,
                )
            if "start_frame" in record:
                return self.add_phase(
                    session,
                    record["segment_id"],
                    revision,
                    start_frame=record["start_frame"],
                    content=record["content"],
                    **options,
                )
            return self.create_cut(
                session,
                record["segment_id"],
                revision,
                **{
                    key: value
                    for key, value in record.items()
                    if key not in {"id", "segment_id", "revision", "updated_at", "insertion"}
                },
                **options,
            )
        if "ordinal" in receipt.before:
            return self.move_segment(
                session,
                receipt.before["segment_id"],
                revision,
                ordinal=receipt.before["ordinal"],
                **options,
            )
        changes = {
            key: value
            for key, value in receipt.before.items()
            if key not in {"revision", "content_schema"}
        }
        commands = {
            "movie": self.update_movie,
            "subject": self.update_subject,
            "segment": self.update_segment,
            "phase": self.update_phase,
            "cut": self.update_cut,
        }
        return commands[receipt.target_kind](
            session, receipt.target_id, revision, **changes, **options
        )

    def read_cut(self, session: Session, cut_id: str) -> dict:
        return _record(_get(session, CutRecord, cut_id))

    def _place_evidence(self, session, place_id, inactive_view_assets):
        angle_ids = select(AngleRecord.id).where(AngleRecord.place_id == place_id)
        rows = session.scalars(
            select(EvidenceRecord)
            .where(EvidenceRecord.angle_id.in_(angle_ids))
            .order_by(EvidenceRecord.created_at, EvidenceRecord.id)
        ).all()
        # A declined/superseded candidate is retained for history, but no longer
        # asks for review. An explicit approval always remains authoritative.
        return [row for row in rows if row.approved or row.asset_id not in inactive_view_assets]

    def opening_context_and_available(
        self, session: Session, segment_id: str, *, inactive_view_assets=frozenset()
    ) -> tuple[OpeningContext, Available, tuple[Binding, ...]] | None:
        """The opening shot's coverage facts (ADR 0019 §1), or None while the segment names no opening angle.
        Openings carry no suitability judgments yet."""
        segment = self.read_segment(session, segment_id)
        angle_id = segment["opening_angle_id"]
        if angle_id is None:
            return None
        target = _get(session, AngleRecord, angle_id)
        place_evidence = self._place_evidence(session, target.place_id, inactive_view_assets)
        previous = None
        if segment["continues"] and segment["ordinal"] > 0:
            before = self._segments(session, segment["movie_id"])[segment["ordinal"] - 1]
            before = self.read_segment(session, before.id)
            previous = (
                before["cuts"][-1]["to_angle_id"] if before["cuts"] else before["opening_angle_id"]
            )
        bindings = {
            row.id: self.reference_binding(session, "view", row.id)
            for row in place_evidence
            if row.approved
        }

        def usable(row):
            return row.approved and not bindings[row.id].stale

        target_evidence = [row for row in place_evidence if row.angle_id == angle_id]
        context = OpeningContext(
            segment_id=segment_id,
            angle=angle_id,
            previous_angle=previous,
            receives_predecessor=segment["continues"],
            evidence=tuple(
                Evidence(
                    row.id,
                    row.angle_id,
                    row.kind,
                    row.asset_id,
                    row.approved,
                    bindings[row.id].stale if row.approved else False,
                )
                for row in target_evidence
            ),
        )
        available = Available(
            approved_view_angles=frozenset(row.angle_id for row in place_evidence if usable(row)),
            pending_view_angles=frozenset(
                row.angle_id for row in place_evidence if not row.approved
            ),
            place_views=sum(usable(row) for row in place_evidence),
        )
        return (
            context,
            available,
            tuple(bindings[row.id] for row in target_evidence if row.approved),
        )

    def context_and_available(
        self,
        session: Session,
        cut_id: str,
        *,
        inactive_view_assets=frozenset(),
        inactive_pin_assets=frozenset(),
    ) -> tuple[CutContext, Available, tuple[Binding, ...]]:
        cut = self.read_cut(session, cut_id)
        target = _get(session, AngleRecord, cut["to_angle_id"])
        place_evidence = self._place_evidence(session, target.place_id, inactive_view_assets)
        target_evidence = tuple(row for row in place_evidence if row.angle_id == target.id)
        keyframes = session.scalars(
            select(KeyframeRecord)
            .where(KeyframeRecord.cut_id == cut_id)
            .order_by(KeyframeRecord.created_at, KeyframeRecord.id)
        ).all()
        current_evidence_ids = {row.id for row in target_evidence}
        valid_keyframes = [
            row
            for row in keyframes
            if row.evidence_id in current_evidence_ids
            and (row.approved or row.approved_at is None)
            and (row.approved or row.asset_id not in inactive_pin_assets)
        ]
        judgments = self.suitability(session, cut_id)
        view_bindings = {
            row.id: self.reference_binding(session, "view", row.id, judgments)
            for row in place_evidence
            if row.approved
        }
        pin_bindings = {
            row.id: self.reference_binding(session, "pin", row.id, judgments)
            for row in keyframes
            if row.approved
        }

        def usable(binding):
            return not binding.stale and binding.suitability != "unsuitable"

        bindings = tuple(view_bindings[row.id] for row in target_evidence if row.approved) + tuple(
            pin_bindings.values()
        )
        context = CutContext(
            cut_id=cut_id,
            from_angle=cut["from_angle_id"],
            to_angle=target.id,
            relation=AngleRelation(
                Relation(cut["relation"]) if cut["relation"] else None,
                cut["crosses_barrier"],
                cut["relation_source"],
            ),
            receives_predecessor=cut["receives_predecessor"],
            evidence=tuple(
                Evidence(
                    row.id,
                    row.angle_id,
                    row.kind,
                    row.asset_id,
                    row.approved,
                    view_bindings[row.id].stale if row.approved else False,
                )
                for row in target_evidence
            ),
            side_facts=cut["side_facts"],
        )
        available = Available(
            approved_view_angles=frozenset(
                row.angle_id
                for row in place_evidence
                if row.approved and usable(view_bindings[row.id])
            ),
            pending_view_angles=frozenset(
                row.angle_id for row in place_evidence if not row.approved
            ),
            approved_keyframe_cuts=frozenset(
                {cut_id}
                if any(row.approved and usable(pin_bindings[row.id]) for row in valid_keyframes)
                else ()
            ),
            pending_keyframe_cuts=frozenset(
                {cut_id} if any(not row.approved for row in valid_keyframes) else ()
            ),
            place_views=sum(
                row.approved and usable(view_bindings[row.id]) for row in place_evidence
            ),
        )
        return context, available, bindings
