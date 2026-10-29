"""Revision-checked commands for the selected take of each segment."""

from __future__ import annotations

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from gaiavfx.infrastructure.database import new_id, timestamp
from gaiavfx.modules.editing.selections.persistence import TakeSelectionRecord
from gaiavfx.shared.errors import ApplicationError


def _record(row: TakeSelectionRecord) -> dict:
    return {column.key: getattr(row, column.key) for column in row.__table__.columns}


class SelectionService:
    def history(self, session: Session, movie_id: str, segment_id: str) -> list[dict]:
        return [
            _record(row)
            for row in session.scalars(
                select(TakeSelectionRecord)
                .where(
                    TakeSelectionRecord.movie_id == movie_id,
                    TakeSelectionRecord.segment_id == segment_id,
                )
                .order_by(TakeSelectionRecord.revision, TakeSelectionRecord.id)
            )
        ]

    def current(self, session: Session, movie_id: str, segment_id: str) -> dict | None:
        row = session.scalars(
            select(TakeSelectionRecord).where(
                TakeSelectionRecord.movie_id == movie_id,
                TakeSelectionRecord.segment_id == segment_id,
                TakeSelectionRecord.superseded_at.is_(None),
            )
        ).first()
        return _record(row) if row is not None else None

    def revision(self, session: Session, movie_id: str, segment_id: str) -> int:
        rows = self.history(session, movie_id, segment_id)
        return max((row["revision"] for row in rows), default=0)

    def select(
        self,
        session: Session,
        *,
        movie_id: str,
        segment_id: str,
        candidate_id: str,
        asset_id: str,
        authority: str,
        expected_revision: int,
        basis: str | None = None,
    ) -> dict:
        if authority not in {"user", "provisional"} or any(
            not isinstance(item, str) or not item or len(item) > limit
            for item, limit in (
                (movie_id, 120),
                (segment_id, 120),
                (candidate_id, 36),
                (asset_id, 120),
            )
        ):
            raise ApplicationError("Invalid take selection.", 422)
        if authority == "user" and basis is not None:
            raise ApplicationError("A user selection must not specify a basis.", 422)
        if authority == "provisional" and basis not in {"user_ranking", "unjudged", "prescreen"}:
            raise ApplicationError("A provisional selection needs a valid basis.", 422)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ApplicationError("Invalid selection revision.", 422)
        if self.revision(session, movie_id, segment_id) != expected_revision:
            raise ApplicationError("Selection revision is stale.", 409)
        old = self.current(session, movie_id, segment_id)
        if old and old["authority"] == "user" and authority == "provisional":
            raise ApplicationError("A provisional pick cannot replace a user selection.", 409)
        now = timestamp()
        if old:
            result = session.execute(
                update(TakeSelectionRecord)
                .where(
                    TakeSelectionRecord.id == old["id"],
                    TakeSelectionRecord.revision == expected_revision,
                    TakeSelectionRecord.superseded_at.is_(None),
                )
                .values(superseded_at=now)
                .execution_options(synchronize_session=False)
            )
            if result.rowcount != 1:
                raise ApplicationError("Selection revision is stale.", 409)
        row = TakeSelectionRecord(
            id=new_id(),
            movie_id=movie_id,
            segment_id=segment_id,
            candidate_id=candidate_id,
            asset_id=asset_id,
            authority=authority,
            basis=basis,
            selected_at=now,
            revision=expected_revision + 1,
        )
        session.add(row)
        session.flush()
        return _record(row)

    def clear(
        self,
        session: Session,
        *,
        movie_id: str,
        segment_id: str,
        authority: str,
        expected_revision: int,
    ) -> dict | None:
        if authority not in {"user", "provisional"}:
            raise ApplicationError("Invalid selection authority.", 422)
        if type(expected_revision) is not int or expected_revision < 0:
            raise ApplicationError("Invalid selection revision.", 422)
        if self.revision(session, movie_id, segment_id) != expected_revision:
            raise ApplicationError("Selection revision is stale.", 409)
        old = self.current(session, movie_id, segment_id)
        if old is None:
            return None
        if old["authority"] == "user" and authority != "user":
            raise ApplicationError("Only the user can clear a user selection.", 409)
        result = session.execute(
            update(TakeSelectionRecord)
            .where(
                TakeSelectionRecord.id == old["id"],
                TakeSelectionRecord.revision == expected_revision,
                TakeSelectionRecord.superseded_at.is_(None),
            )
            .values(superseded_at=timestamp(), revision=expected_revision + 1)
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise ApplicationError("Selection revision is stale.", 409)
        session.expire_all()
        return _record(session.get(TakeSelectionRecord, old["id"]))
