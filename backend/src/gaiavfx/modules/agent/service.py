"""Revision-checked agent records; callers own transactions and document meaning."""

from copy import deepcopy
from datetime import datetime, timedelta

from sqlalchemy import select, update

from gaiavfx.infrastructure.database import new_id, timestamp
from gaiavfx.modules.agent.persistence import SessionRecord, TurnRecord
from gaiavfx.shared.errors import ApplicationError

STATES = {"idle", "queued", "running", "waiting_human", "waiting_operation", "stopped", "failed"}
TURN_STATES = {"requested", "accepted", "rejected", "failed"}


def _record(row):
    return deepcopy({column.key: getattr(row, column.key) for column in row.__table__.columns})


def _get(session, model, record_id):
    row = session.get(model, record_id)
    if row is None:
        raise ApplicationError(f"Agent record {record_id} was not found.", 404)
    return row


class AgentStore:
    def create_session(self, session, *, movie_id, scope_kind, scope_id, document):
        if scope_kind not in {"movie", "segment", "cut", "film"}:
            raise ApplicationError("Invalid agent scope.", 422)
        created_at = timestamp()
        previous = session.scalar(
            select(SessionRecord.created_at).order_by(SessionRecord.created_at.desc()).limit(1)
        )
        if previous and created_at <= previous:
            created_at = (datetime.fromisoformat(previous) + timedelta(microseconds=1)).isoformat()
        row = SessionRecord(
            id=new_id(),
            movie_id=movie_id,
            scope_kind=scope_kind,
            scope_id=scope_id,
            document=deepcopy(document),
            state="idle",
            revision=1,
            created_at=created_at,
        )
        session.add(row)
        session.flush()
        return _record(row)

    def read_session(self, session, session_id):
        return _record(_get(session, SessionRecord, session_id))

    def sessions(self, session, movie_id):
        return [
            _record(row)
            for row in session.scalars(
                select(SessionRecord)
                .where(SessionRecord.movie_id == movie_id)
                .order_by(SessionRecord.created_at.desc(), SessionRecord.id.desc())
            )
        ]

    def scope_sessions(self, session, scope_kind, scope_id):
        """A scope's conversations, newest first, as sessions() orders a movie's."""
        return [
            _record(row)
            for row in session.scalars(
                select(SessionRecord)
                .where(SessionRecord.scope_kind == scope_kind, SessionRecord.scope_id == scope_id)
                .order_by(SessionRecord.created_at.desc(), SessionRecord.id.desc())
            )
        ]

    def find_session(self, session, movie_id, scope_kind, scope_id):
        row = session.scalars(
            select(SessionRecord)
            .where(
                SessionRecord.movie_id == movie_id,
                SessionRecord.scope_kind == scope_kind,
                SessionRecord.scope_id == scope_id,
                SessionRecord.state != "stopped",
            )
            .order_by(SessionRecord.created_at.desc(), SessionRecord.id.desc())
        ).first()
        return _record(row) if row else None

    def update_session(self, session, session_id, expected_revision, *, state, document):
        _get(session, SessionRecord, session_id)
        if state not in STATES:
            raise ApplicationError("Invalid agent state.", 422)
        changed = session.execute(
            update(SessionRecord)
            .where(
                SessionRecord.id == session_id,
                SessionRecord.revision == expected_revision,
            )
            .values(
                state=state,
                document=deepcopy(document),
                revision=expected_revision + 1,
                updated_at=timestamp(),
            )
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:
            raise ApplicationError("Agent session changed.", 409)
        session.expire_all()
        return self.read_session(session, session_id)

    def claim_session(self, session, session_id, from_states):
        changed = session.execute(
            update(SessionRecord)
            .where(
                SessionRecord.id == session_id,
                SessionRecord.state.in_(from_states),
            )
            .values(state="running", revision=SessionRecord.revision + 1, updated_at=timestamp())
            .execution_options(synchronize_session=False)
        )
        session.expire_all()
        return self.read_session(session, session_id) if changed.rowcount == 1 else None

    def claimable(self, session):
        return self.in_states(session, {"queued", "waiting_operation"})

    def in_states(self, session, states):
        return [
            _record(row)
            for row in session.scalars(
                select(SessionRecord)
                .where(SessionRecord.state.in_(states))
                .order_by(SessionRecord.updated_at, SessionRecord.id)
            )
        ]

    def turns(self, session, session_id):
        _get(session, SessionRecord, session_id)
        return [
            _record(row)
            for row in session.scalars(
                select(TurnRecord)
                .where(TurnRecord.session_id == session_id)
                .order_by(TurnRecord.ordinal)
            )
        ]

    def create_turn(
        self,
        session,
        session_id,
        ordinal,
        role,
        state,
        *,
        action=None,
        result=None,
        receipts=(),
        provenance=None,
        turn_id=None,
    ):
        _get(session, SessionRecord, session_id)
        if role not in {"human", "agent", "system"} or state not in TURN_STATES:
            raise ApplicationError("Invalid agent turn.", 422)
        row = TurnRecord(
            id=turn_id or new_id(),
            session_id=session_id,
            ordinal=ordinal,
            role=role,
            state=state,
            action=deepcopy(action),
            result=deepcopy(result),
            receipts=deepcopy(list(receipts)),
            provenance=deepcopy(provenance),
        )
        session.add(row)
        session.flush()
        return _record(row)

    def update_turn(self, session, turn_id, state, *, action=None, result=None, receipts=None):
        row = _get(session, TurnRecord, turn_id)
        if state not in TURN_STATES:
            raise ApplicationError("Invalid agent turn state.", 422)
        row.state, row.updated_at = state, timestamp()
        for key, value in (("action", action), ("result", result), ("receipts", receipts)):
            if value is not None:
                setattr(row, key, deepcopy(value))
        session.flush()
        return _record(row)
