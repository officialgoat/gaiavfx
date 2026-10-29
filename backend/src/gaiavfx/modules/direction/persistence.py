"""Direction-owned generation identity, authored records, commands and approvals."""

from sqlalchemy import JSON, Boolean, ForeignKey, Index, Integer, String, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column

from gaiavfx.infrastructure.database import Base, timestamp


class MovieRecord(Base):
    __tablename__ = "direction_movies"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)
    updated_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class SubjectRecord(Base):
    __tablename__ = "direction_subjects"

    id: Mapped[str] = mapped_column(String(120), primary_key=True)
    movie_id: Mapped[str] = mapped_column(ForeignKey("direction_movies.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String)
    keeps: Mapped[list[str]] = mapped_column(JSON)
    identity_asset_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)
    updated_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class SegmentRecord(Base):
    __tablename__ = "direction_segments"
    __table_args__ = (
        UniqueConstraint("movie_id", "ordinal", name="uq_direction_segments_movie_ordinal"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    movie_id: Mapped[str] = mapped_column(ForeignKey("direction_movies.id"), index=True)
    ordinal: Mapped[int] = mapped_column(Integer)
    delivered_frames: Mapped[int] = mapped_column(Integer)
    continues: Mapped[bool] = mapped_column(Boolean)
    content: Mapped[dict] = mapped_column(JSON)
    content_schema: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    # The first shot (ADR 0019): with cuts, the first cut's from_angle_id is the opening angle.
    opening_angle_id: Mapped[str | None] = mapped_column(
        ForeignKey("direction_angles.id"), nullable=True, default=None
    )
    opening_technique: Mapped[str | None] = mapped_column(String(80), nullable=True, default=None)
    # A continuation's handoff (0014): null follows the integration's rule and default context.
    continuation_method: Mapped[str | None] = mapped_column(String(16), nullable=True, default=None)
    context_frames: Mapped[int | None] = mapped_column(Integer, nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)
    updated_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class PhaseRecord(Base):
    __tablename__ = "direction_phases"
    __table_args__ = (
        UniqueConstraint("segment_id", "start_frame", name="uq_direction_phases_segment_start"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    segment_id: Mapped[str] = mapped_column(ForeignKey("direction_segments.id"), index=True)
    start_frame: Mapped[int] = mapped_column(Integer)
    content: Mapped[dict] = mapped_column(JSON)
    content_schema: Mapped[int] = mapped_column(Integer)
    revision: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)
    updated_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class ReceiptRecord(Base):
    __tablename__ = "direction_receipts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    movie_id: Mapped[str] = mapped_column(ForeignKey("direction_movies.id"), index=True)
    command: Mapped[str] = mapped_column(String(40))
    target_kind: Mapped[str] = mapped_column(String(20))
    target_id: Mapped[str] = mapped_column(String(120))
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    undoes: Mapped[str | None] = mapped_column(String(36), nullable=True)
    undone_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class PlaceRecord(Base):
    __tablename__ = "direction_places"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String, default="", server_default="")
    sound: Mapped[str] = mapped_column(String, default="", server_default="")
    overhead: Mapped[str] = mapped_column(String, default="", server_default="")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    plate_asset_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    plate_angle_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # The set this place is built as (ADR 0019): an id in the sets module.
    set_id: Mapped[str | None] = mapped_column(String(36), nullable=True, default=None)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class AngleRecord(Base):
    __tablename__ = "direction_angles"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    place_id: Mapped[str] = mapped_column(ForeignKey("direction_places.id"), index=True)
    name: Mapped[str] = mapped_column(String(120))
    description: Mapped[str] = mapped_column(String, default="", server_default="")
    revision: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    placement: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class EvidenceRecord(Base):
    __tablename__ = "direction_evidence"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    angle_id: Mapped[str] = mapped_column(ForeignKey("direction_angles.id"), index=True)
    kind: Mapped[str] = mapped_column(String(20))
    asset_id: Mapped[str] = mapped_column(String(120))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    inputs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class CutRecord(Base):
    __tablename__ = "direction_cuts"
    __table_args__ = (
        UniqueConstraint("segment_id", "frame", name="uq_direction_cuts_segment_frame"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    segment_id: Mapped[str] = mapped_column(ForeignKey("direction_segments.id"), index=True)
    frame: Mapped[int] = mapped_column(Integer)
    from_angle_id: Mapped[str] = mapped_column(ForeignKey("direction_angles.id"))
    to_angle_id: Mapped[str] = mapped_column(ForeignKey("direction_angles.id"))
    relation: Mapped[str | None] = mapped_column(String(20), nullable=True)
    crosses_barrier: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    relation_source: Mapped[str] = mapped_column(String(20), default="unknown")
    receives_predecessor: Mapped[bool] = mapped_column(Boolean, default=False)
    side_facts: Mapped[bool] = mapped_column(Boolean, default=False)
    fidelity: Mapped[bool] = mapped_column(Boolean, default=False)
    override_technique: Mapped[str | None] = mapped_column(String(80), nullable=True)
    insertion: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    revision: Mapped[int] = mapped_column(Integer, default=1)
    updated_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class KeyframeRecord(Base):
    __tablename__ = "direction_keyframes"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cut_id: Mapped[str] = mapped_column(ForeignKey("direction_cuts.id"), index=True)
    asset_id: Mapped[str] = mapped_column(String(120))
    subject_asset_id: Mapped[str | None] = mapped_column(String(120), nullable=True)
    evidence_id: Mapped[str] = mapped_column(ForeignKey("direction_evidence.id"))
    approved: Mapped[bool] = mapped_column(Boolean, default=False)
    approved_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(20), nullable=True)
    inputs: Mapped[dict | None] = mapped_column(JSON, nullable=True)
    created_at: Mapped[str] = mapped_column(String(40), default=timestamp)


class SuitabilityRecord(Base):
    __tablename__ = "direction_cut_suitability"
    __table_args__ = (
        Index(
            "uq_direction_cut_suitability_current",
            "cut_id",
            "reference_kind",
            "reference_id",
            unique=True,
            sqlite_where=text("superseded_at IS NULL"),
        ),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    cut_id: Mapped[str] = mapped_column(ForeignKey("direction_cuts.id"), index=True)
    reference_kind: Mapped[str] = mapped_column(String(10))
    reference_id: Mapped[str] = mapped_column(String(120))
    verdict: Mapped[str] = mapped_column(String(10))
    note: Mapped[str] = mapped_column(String(500))
    inputs: Mapped[dict | list | None] = mapped_column(JSON, nullable=True)
    judged_at: Mapped[str] = mapped_column(String(40))
    judged_by: Mapped[str] = mapped_column(String(20))
    superseded_at: Mapped[str | None] = mapped_column(String(40), nullable=True)
