"""Public aggregate persistence boundary. Callers receive detached plain data."""

from copy import deepcopy
from datetime import datetime, timedelta

from sqlalchemy import select, update

from gaiavfx.infrastructure.database import new_id, timestamp
from gaiavfx.modules.editing.recast.persistence import (
    AppearanceLinkRecord,
    CharacterRecord,
    ChoiceRecord,
    LineChoiceRecord,
    LineRecord,
    NotSameRecord,
    RecastEditRecord,
    RecastRecord,
    ShotRecord,
    TrackOverrideRecord,
)
from gaiavfx.shared.errors import ApplicationError


def _data(row):
    return {c.key: deepcopy(getattr(row, c.key)) for c in row.__table__.columns}


class RecastService:
    def ids(self, session):
        return list(
            session.scalars(select(RecastRecord.id).order_by(RecastRecord.created_at.desc()))
        )

    def for_request(self, session, key):
        return session.scalar(select(RecastRecord.id).where(RecastRecord.request_key == key))

    def create(self, session, *, source_asset_id, title, request_key):
        now = timestamp()
        row = RecastRecord(
            id=new_id(),
            title=title,
            source_asset_id=source_asset_id,
            request_key=request_key,
            revision=0,
            run_state="idle",
            run_mode="draft",
            render_budget=0,
            created_at=now,
            updated_at=now,
        )
        session.add(row)
        session.flush()
        return self.load(session, row.id)

    def lock(self, session, recast_id, expected_revision=None):
        """Serialize feed and human edits before reading their state (SQLite writer lock)."""
        statement = update(RecastRecord).where(RecastRecord.id == recast_id)
        if expected_revision is not None:
            statement = statement.where(RecastRecord.revision == expected_revision)
        result = session.execute(statement.values(revision=RecastRecord.revision))
        if not result.rowcount:
            self.load(session, recast_id)  # distinguish missing from stale
            raise ApplicationError("The recast changed; reload before editing.", 409)

    def load(self, session, recast_id):
        row = session.get(RecastRecord, recast_id, populate_existing=True)
        if row is None:
            raise ApplicationError("Recast not found.", 404)
        result = _data(row)
        for key, model in (
            ("characters", CharacterRecord),
            ("shots", ShotRecord),
            ("overrides", TrackOverrideRecord),
            ("appearance_links", AppearanceLinkRecord),
            ("not_same", NotSameRecord),
            ("lines", LineRecord),
            ("choices", ChoiceRecord),
            ("line_choices", LineChoiceRecord),
        ):
            result[key] = [
                _data(r) for r in session.scalars(select(model).where(model.recast_id == recast_id))
            ]
        result["characters"].sort(key=lambda c: c["ordinal"])
        result["choices"].sort(key=lambda c: c["chosen_at"])
        result["line_choices"].sort(key=lambda c: c["chosen_at"])
        return result

    def save(self, session, recast):
        row = session.get(RecastRecord, recast["id"])
        for c in row.__table__.columns:
            setattr(row, c.key, deepcopy(recast[c.key]))
        # Choices and line choices deliberately have no update/delete path.
        for key, model in (
            ("characters", CharacterRecord),
            ("shots", ShotRecord),
            ("overrides", TrackOverrideRecord),
            ("appearance_links", AppearanceLinkRecord),
            ("not_same", NotSameRecord),
            ("lines", LineRecord),
        ):
            primary = [c.key for c in model.__table__.primary_key]
            existing = {
                tuple(getattr(row, key) for key in primary): row
                for row in session.scalars(select(model).where(model.recast_id == recast["id"]))
            }
            kept = {tuple(value[key] for key in primary) for value in recast[key]}
            gone = [row for identity, row in existing.items() if identity not in kept]
            for row in gone:
                session.delete(row)
            if gone:
                # Delete before insert: an undo may put a character back in place of the row that
                # took its cluster (a unique key), and the unit of work inserts before it deletes.
                session.flush()
            for value in recast[key]:
                identity = tuple(value[key] for key in primary)
                row = existing.get(identity)
                if row is None:
                    session.add(model(**deepcopy(value)))
                else:
                    for field, data in value.items():
                        if getattr(row, field) != data:
                            setattr(row, field, deepcopy(data))
        session.flush()

    def choose(self, session, recast, shot_id, version):
        row = ChoiceRecord(
            id=new_id(),
            recast_id=recast["id"],
            shot_id=shot_id,
            version=version,
            authority="user",
            chosen_at=timestamp(),
            recast_revision=recast["revision"],
        )
        session.add(row)
        session.flush()
        recast["choices"].append(_data(row))

    # The cast-edit journal (ADR 0012 Amendment 7 §1) lives outside load/save: the aggregate dict
    # never carries its rows, so the generic sync above never touches them.

    def record_edit(self, session, recast_id, **fields):
        created_at = timestamp()
        previous = session.scalar(
            select(RecastEditRecord.created_at)
            .where(RecastEditRecord.recast_id == recast_id)
            .order_by(RecastEditRecord.created_at.desc())
            .limit(1)
        )
        if previous and created_at <= previous:
            # Strictly increasing per recast, so the order never depends on a clock tick.
            created_at = (datetime.fromisoformat(previous) + timedelta(microseconds=1)).isoformat(
                timespec="microseconds"
            )
        row = RecastEditRecord(
            **{"undoes": None, "dialogue_operation_id": None, **deepcopy(fields)},
            id=new_id(),
            recast_id=recast_id,
            undone_at=None,
            created_at=created_at,
        )
        session.add(row)
        session.flush()
        return _data(row)

    def edits(self, session, recast_id, limit=20):
        """The newest entries first, without their row images."""
        rows = session.execute(
            select(
                RecastEditRecord.id,
                RecastEditRecord.command,
                RecastEditRecord.kind,
                RecastEditRecord.summary,
                RecastEditRecord.created_at,
                RecastEditRecord.undone_at,
            )
            .where(RecastEditRecord.recast_id == recast_id)
            .order_by(RecastEditRecord.created_at.desc())
            .limit(limit)
        )
        return [dict(row._mapping) for row in rows]

    def edit(self, session, edit_id):
        row = session.get(RecastEditRecord, edit_id, populate_existing=True)
        return _data(row) if row else None

    def mark_undone(self, session, edit_id):
        row = session.get(RecastEditRecord, edit_id)
        row.undone_at = timestamp()
        session.flush()
