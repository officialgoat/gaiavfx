"""Editing-owned human decisions about window person tracks."""

import sqlalchemy as sa
from alembic import op

revision = "0007_recast_appearance_links"
down_revision = "0006_recast_shot_bridges"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "editing_recast_appearance_links",
        sa.Column(
            "recast_id", sa.String(36), sa.ForeignKey("editing_recasts.id"), primary_key=True
        ),
        sa.Column("analysis_operation_id", sa.String(36), primary_key=True),
        sa.Column("track_id", sa.String(120), primary_key=True),
        sa.Column("shot_id", sa.String(120), nullable=False),
        sa.Column("character_id", sa.String(36), nullable=False),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("proposed_by", sa.String(20), nullable=False),
        sa.Column("evidence", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("decided_at", sa.String(40)),
        sa.CheckConstraint(
            "state IN ('proposed', 'confirmed', 'rejected')", name="ck_appearance_state"
        ),
        sa.CheckConstraint(
            "proposed_by IN ('analysis', 'agent', 'user')", name="ck_appearance_proposer"
        ),
        sa.CheckConstraint(
            "(state = 'proposed' AND decided_at IS NULL) OR "
            "(state IN ('confirmed', 'rejected') AND decided_at IS NOT NULL)",
            name="ck_appearance_decided",
        ),
    )


def downgrade():
    op.drop_table("editing_recast_appearance_links")
