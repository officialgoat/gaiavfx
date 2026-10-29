"""Editorial decisions only; analysis and rendered media belong to Library/Generation."""

from sqlalchemy import (
    JSON,
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from gaiavfx.infrastructure.database import Base


class RecastRecord(Base):
    __tablename__ = "editing_recasts"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    title: Mapped[str] = mapped_column(String(120))
    source_asset_id: Mapped[str] = mapped_column(String(120))
    analysis_operation_id: Mapped[str | None] = mapped_column(String(36))
    revision: Mapped[int] = mapped_column(Integer)
    run_state: Mapped[str] = mapped_column(String(20))
    run_reason: Mapped[str | None] = mapped_column(String)
    run_mode: Mapped[str] = mapped_column(String(10))
    run_range_start: Mapped[int | None] = mapped_column(Integer)
    run_range_end: Mapped[int | None] = mapped_column(Integer)
    render_budget: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[str] = mapped_column(String(40))
    updated_at: Mapped[str] = mapped_column(String(40))
    request_key: Mapped[str] = mapped_column(String(80), unique=True)


class CharacterRecord(Base):
    __tablename__ = "editing_recast_characters"
    __table_args__ = (UniqueConstraint("recast_id", "cluster_id"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recast_id: Mapped[str] = mapped_column(ForeignKey("editing_recasts.id"), index=True)
    cluster_id: Mapped[str | None] = mapped_column(String(120))
    label: Mapped[str] = mapped_column(String(80))
    hidden: Mapped[bool] = mapped_column(Boolean)
    target_asset_id: Mapped[str | None] = mapped_column(String(120))
    target_label: Mapped[str | None] = mapped_column(String(80))
    target_state: Mapped[str | None] = mapped_column(String(20))
    target_proposed_by: Mapped[str | None] = mapped_column(String(20))
    reference_asset_id: Mapped[str | None] = mapped_column(String(120))
    reference_state: Mapped[str | None] = mapped_column(String(20))
    ordinal: Mapped[int] = mapped_column(Integer)
    # Voice decisions (ADR 0016 §1); P4 adds the columns, P5 the commands that set them.
    voice_asset_ids: Mapped[list | None] = mapped_column(JSON)
    voice_state: Mapped[str | None] = mapped_column(String(20))
    voice_proposed_by: Mapped[str | None] = mapped_column(String(20))
    voice_model_state: Mapped[str | None] = mapped_column(String(20))
    voice_model_artifact_id: Mapped[str | None] = mapped_column(String(120))


class TrackOverrideRecord(Base):
    __tablename__ = "editing_recast_track_overrides"

    recast_id: Mapped[str] = mapped_column(ForeignKey("editing_recasts.id"), primary_key=True)
    track_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    character_id: Mapped[str] = mapped_column(String(36))  # or "unknown"
    created_at: Mapped[str] = mapped_column(String(40))


class AppearanceLinkRecord(Base):
    __tablename__ = "editing_recast_appearance_links"
    __table_args__ = (
        CheckConstraint(
            "state IN ('proposed', 'confirmed', 'rejected')", name="ck_appearance_state"
        ),
        CheckConstraint(
            "proposed_by IN ('analysis', 'agent', 'user')", name="ck_appearance_proposer"
        ),
        CheckConstraint(
            "(state = 'proposed' AND decided_at IS NULL) OR "
            "(state IN ('confirmed', 'rejected') AND decided_at IS NOT NULL)",
            name="ck_appearance_decided",
        ),
    )

    recast_id: Mapped[str] = mapped_column(ForeignKey("editing_recasts.id"), primary_key=True)
    analysis_operation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    track_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    shot_id: Mapped[str] = mapped_column(String(120))
    character_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(20))
    proposed_by: Mapped[str] = mapped_column(String(20))
    evidence: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))
    decided_at: Mapped[str | None] = mapped_column(String(40))


class NotSameRecord(Base):
    """A pair the user set aside as "not the same person" (ADR 0017, CP3): its suggestion stays hidden."""

    __tablename__ = "editing_recast_not_same"
    __table_args__ = (CheckConstraint("character_a < character_b", name="ck_not_same_order"),)

    recast_id: Mapped[str] = mapped_column(ForeignKey("editing_recasts.id"), primary_key=True)
    character_a: Mapped[str] = mapped_column(String(36), primary_key=True)
    character_b: Mapped[str] = mapped_column(String(36), primary_key=True)
    created_at: Mapped[str] = mapped_column(String(40))


class RecastEditRecord(Base):
    """One journaled cast edit (ADR 0012 Amendment 7 §1): the rows it touched, before and after.

    Written only through RecastService, outside the aggregate's load/save. `undone_at` is never cleared;
    an undo or redo is a new entry naming the one it inverted.
    """

    __tablename__ = "editing_recast_edits"
    __table_args__ = (
        CheckConstraint("kind IN ('edit', 'undo', 'redo')", name="ck_recast_edit_kind"),
        # The recast's newest entries are read on every poll: by index, never by loading the table.
        Index("ix_editing_recast_edits_recast_created", "recast_id", "created_at"),
    )

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recast_id: Mapped[str] = mapped_column(ForeignKey("editing_recasts.id"))
    command: Mapped[str] = mapped_column(String(40))
    kind: Mapped[str] = mapped_column(String(10))
    summary: Mapped[str] = mapped_column(String(200))
    analysis_operation_id: Mapped[str | None] = mapped_column(String(36))
    recast_revision: Mapped[int] = mapped_column(Integer)
    undoes: Mapped[str | None] = mapped_column(String(36))
    undone_at: Mapped[str | None] = mapped_column(String(40))
    created_at: Mapped[str] = mapped_column(String(40))
    # Changed keys only, last in the row so the newest-entries read never walks the images.
    before: Mapped[dict] = mapped_column(JSON)
    after: Mapped[dict] = mapped_column(JSON)
    # The dialogue analysis current when the entry was written; line images undo only against it.
    dialogue_operation_id: Mapped[str | None] = mapped_column(String(36))


class LineRecord(Base):
    """A line decision or an agent proposal on one transcript segment (ADR 0016 §1).

    The dialogue analysis's own proposals are computed views and never stored. Edges are the
    user's, in samples at 48 kHz; null when the rule's edges apply.
    """

    __tablename__ = "editing_recast_lines"
    __table_args__ = (
        CheckConstraint("state IN ('proposed', 'confirmed', 'rejected')", name="ck_line_state"),
        CheckConstraint("proposed_by IN ('agent', 'user')", name="ck_line_proposer"),
        CheckConstraint(
            "(state = 'proposed' AND decided_at IS NULL) OR "
            "(state IN ('confirmed', 'rejected') AND decided_at IS NOT NULL)",
            name="ck_line_decided",
        ),
        CheckConstraint(
            "(start_sample IS NULL AND end_sample IS NULL) OR "
            "(start_sample IS NOT NULL AND end_sample IS NOT NULL "
            "AND start_sample >= 0 AND end_sample > start_sample)",
            name="ck_line_edges",
        ),
    )

    recast_id: Mapped[str] = mapped_column(ForeignKey("editing_recasts.id"), primary_key=True)
    dialogue_operation_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    segment_key: Mapped[str] = mapped_column(String(40), primary_key=True)
    character_id: Mapped[str] = mapped_column(String(36))
    state: Mapped[str] = mapped_column(String(20))
    proposed_by: Mapped[str] = mapped_column(String(20))
    start_sample: Mapped[int | None] = mapped_column(Integer)
    end_sample: Mapped[int | None] = mapped_column(Integer)
    evidence: Mapped[dict] = mapped_column(JSON)
    created_at: Mapped[str] = mapped_column(String(40))
    decided_at: Mapped[str | None] = mapped_column(String(40))


class LineChoiceRecord(Base):
    """Append-only human choice of a line's version (a candidate or "original"); P6 writes it."""

    __tablename__ = "editing_recast_line_choices"
    __table_args__ = (CheckConstraint("authority = 'user'", name="ck_line_choice_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recast_id: Mapped[str] = mapped_column(ForeignKey("editing_recasts.id"), index=True)
    dialogue_operation_id: Mapped[str] = mapped_column(String(36))
    segment_key: Mapped[str] = mapped_column(String(40))
    version: Mapped[str] = mapped_column(String(64))
    authority: Mapped[str] = mapped_column(String(20))
    chosen_at: Mapped[str] = mapped_column(String(40))
    recast_revision: Mapped[int] = mapped_column(Integer)


class ShotRecord(Base):
    __tablename__ = "editing_recast_shots"

    recast_id: Mapped[str] = mapped_column(ForeignKey("editing_recasts.id"), primary_key=True)
    shot_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    included: Mapped[bool | None] = mapped_column(Boolean)
    priority: Mapped[int | None] = mapped_column(Integer)
    plan: Mapped[dict | None] = mapped_column(JSON)
    window_analysis_ops: Mapped[list] = mapped_column(JSON)
    window_replacement_ops: Mapped[list] = mapped_column(JSON)
    layers: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    assembly_op: Mapped[str | None] = mapped_column(String(36))
    bridge_ops: Mapped[list] = mapped_column(JSON, default=list, server_default="[]")
    mode: Mapped[str | None] = mapped_column(String(10))


class ChoiceRecord(Base):
    __tablename__ = "editing_recast_choices"
    __table_args__ = (CheckConstraint("authority = 'user'", name="ck_recast_choice_user"),)

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    recast_id: Mapped[str] = mapped_column(ForeignKey("editing_recasts.id"), index=True)
    shot_id: Mapped[str] = mapped_column(String(120))
    version: Mapped[str] = mapped_column(String(64))
    authority: Mapped[str] = mapped_column(String(20))
    chosen_at: Mapped[str] = mapped_column(String(40))
    recast_revision: Mapped[int] = mapped_column(Integer)
