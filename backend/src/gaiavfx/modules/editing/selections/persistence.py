"""Editing-owned take selection history."""

from sqlalchemy import Index, Integer, String, text
from sqlalchemy.orm import Mapped, mapped_column

from gaiavfx.infrastructure.database import Base, timestamp


class TakeSelectionRecord(Base):
    __tablename__ = "editing_take_selections"
    __table_args__ = (
        Index("ix_editing_take_selections_segment", "movie_id", "segment_id"),
        Index(
            "uq_editing_take_selections_current",
            "movie_id",
            "segment_id",
            unique=True,
            sqlite_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    movie_id: Mapped[str] = mapped_column(String(120))
    segment_id: Mapped[str] = mapped_column(String(120))
    candidate_id: Mapped[str] = mapped_column(String(36))
    asset_id: Mapped[str] = mapped_column(String(120))
    authority: Mapped[str] = mapped_column(String(20))
    basis: Mapped[str | None] = mapped_column(String(20), nullable=True)
    selected_at: Mapped[str] = mapped_column(String(40), default=timestamp)
    superseded_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    revision: Mapped[int] = mapped_column(Integer)
