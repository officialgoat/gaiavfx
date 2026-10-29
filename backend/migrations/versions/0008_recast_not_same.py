"""Editing-owned pairs the user set aside as "not the same person" (ADR 0017, CP3)."""

import sqlalchemy as sa
from alembic import op

revision = "0008_recast_not_same"
down_revision = "0007_recast_appearance_links"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "editing_recast_not_same",
        sa.Column(
            "recast_id", sa.String(36), sa.ForeignKey("editing_recasts.id"), primary_key=True
        ),
        sa.Column("character_a", sa.String(36), primary_key=True),
        sa.Column("character_b", sa.String(36), primary_key=True),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.CheckConstraint("character_a < character_b", name="ck_not_same_order"),
    )


def downgrade():
    op.drop_table("editing_recast_not_same")
