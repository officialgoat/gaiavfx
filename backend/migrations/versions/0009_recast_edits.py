"""Editing-owned journal of the user's cast edits: stored undo and redo (ADR 0012 Amendment 7 §1)."""

import sqlalchemy as sa
from alembic import op

revision = "0009_recast_edits"
down_revision = "0008_recast_not_same"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "editing_recast_edits",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("recast_id", sa.String(36), sa.ForeignKey("editing_recasts.id"), nullable=False),
        sa.Column("command", sa.String(40), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("summary", sa.String(200), nullable=False),
        sa.Column("analysis_operation_id", sa.String(36)),
        sa.Column("recast_revision", sa.Integer(), nullable=False),
        sa.Column("undoes", sa.String(36)),
        sa.Column("undone_at", sa.String(40)),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.CheckConstraint("kind IN ('edit', 'undo', 'redo')", name="ck_recast_edit_kind"),
    )
    op.create_index(
        "ix_editing_recast_edits_recast_created",
        "editing_recast_edits",
        ["recast_id", "created_at"],
    )


def downgrade():
    op.drop_index("ix_editing_recast_edits_recast_created", table_name="editing_recast_edits")
    op.drop_table("editing_recast_edits")
