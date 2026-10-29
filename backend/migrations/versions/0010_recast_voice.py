"""Editing-owned voice decisions (ADR 0016 §1): character voice columns, line decisions, line choices.

Editing stores decisions, never audio. The analysis's own line proposals are computed views; a row
exists only for an agent proposal or a user decision, keyed to one dialogue analysis operation.
"""

import sqlalchemy as sa
from alembic import op

revision = "0010_recast_voice"
down_revision = "0009_recast_edits"
branch_labels = None
depends_on = None

VOICE_COLUMNS = (
    ("voice_asset_ids", sa.JSON()),
    ("voice_state", sa.String(20)),
    ("voice_proposed_by", sa.String(20)),
    ("voice_model_state", sa.String(20)),
    ("voice_model_artifact_id", sa.String(120)),
)


def upgrade():
    for name, kind in VOICE_COLUMNS:
        op.add_column("editing_recast_characters", sa.Column(name, kind))
    op.add_column("editing_recast_edits", sa.Column("dialogue_operation_id", sa.String(36)))
    op.create_table(
        "editing_recast_lines",
        sa.Column(
            "recast_id", sa.String(36), sa.ForeignKey("editing_recasts.id"), primary_key=True
        ),
        sa.Column("dialogue_operation_id", sa.String(36), primary_key=True),
        sa.Column("segment_key", sa.String(40), primary_key=True),
        sa.Column("character_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("proposed_by", sa.String(20), nullable=False),
        sa.Column("start_sample", sa.Integer()),
        sa.Column("end_sample", sa.Integer()),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("decided_at", sa.String(40)),
        sa.CheckConstraint("state IN ('proposed', 'confirmed', 'rejected')", name="ck_line_state"),
        sa.CheckConstraint("proposed_by IN ('agent', 'user')", name="ck_line_proposer"),
        sa.CheckConstraint(
            "(state = 'proposed' AND decided_at IS NULL) OR "
            "(state IN ('confirmed', 'rejected') AND decided_at IS NOT NULL)",
            name="ck_line_decided",
        ),
        sa.CheckConstraint(
            "(start_sample IS NULL AND end_sample IS NULL) OR "
            "(start_sample IS NOT NULL AND end_sample IS NOT NULL "
            "AND start_sample >= 0 AND end_sample > start_sample)",
            name="ck_line_edges",
        ),
    )
    op.create_table(
        "editing_recast_line_choices",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("recast_id", sa.String(36), sa.ForeignKey("editing_recasts.id"), nullable=False),
        sa.Column("dialogue_operation_id", sa.String(36), nullable=False),
        sa.Column("segment_key", sa.String(40), nullable=False),
        sa.Column("version", sa.String(64), nullable=False),
        sa.Column("authority", sa.String(20), nullable=False),
        sa.Column("chosen_at", sa.String(40), nullable=False),
        sa.Column("recast_revision", sa.Integer(), nullable=False),
        sa.CheckConstraint("authority = 'user'", name="ck_line_choice_user"),
    )
    op.create_index(
        "ix_editing_recast_line_choices_recast_id", "editing_recast_line_choices", ["recast_id"]
    )


def downgrade():
    op.drop_index(
        "ix_editing_recast_line_choices_recast_id", table_name="editing_recast_line_choices"
    )
    op.drop_table("editing_recast_line_choices")
    op.drop_table("editing_recast_lines")
    with op.batch_alter_table("editing_recast_edits") as batch:
        batch.drop_column("dialogue_operation_id")
    with op.batch_alter_table("editing_recast_characters") as batch:
        for name, _kind in reversed(VOICE_COLUMNS):
            batch.drop_column(name)
