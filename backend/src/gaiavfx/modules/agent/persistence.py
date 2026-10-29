"""Conversation persistence, independent of movie and production tables."""

from sqlalchemy import JSON, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from gaiavfx.infrastructure.database import Base, timestamp


class SessionRecord(Base):
    __tablename__ = "agent_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    movie_id: Mapped[str] = mapped_column(String(120), index=True)
    scope_kind: Mapped[str] = mapped_column(String(20))
    scope_id: Mapped[str] = mapped_column(String(120))
    state: Mapped[str] = mapped_column(String(30))
    revision: Mapped[int] = mapped_column(Integer)
    document: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)
    updated_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class TurnRecord(Base):
    __tablename__ = "agent_turns"
    __table_args__ = (UniqueConstraint("session_id", "ordinal", name="uq_agent_turn_ordinal"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("agent_sessions.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    role: Mapped[str] = mapped_column(String(20))
    state: Mapped[str] = mapped_column(String(20))
    action: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    result: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    receipts: Mapped[list] = mapped_column(JSON, default=list)
    provenance: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)
    updated_at: Mapped[str] = mapped_column(String(40), default=timestamp)
