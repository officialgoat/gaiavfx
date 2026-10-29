"""Editing-owned bridge operation identities; media remains in Library."""

import sqlalchemy as sa
from alembic import op

revision = "0006_recast_shot_bridges"
down_revision = "0005_recast_character_reference"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "editing_recast_shots",
        sa.Column("bridge_ops", sa.JSON(), nullable=False, server_default="[]"),
    )


def downgrade():
    with op.batch_alter_table("editing_recast_shots") as batch:
        batch.drop_column("bridge_ops")
