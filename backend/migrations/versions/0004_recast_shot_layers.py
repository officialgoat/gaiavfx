"""Editing-owned derived shot versions; pixels remain in Library."""

import sqlalchemy as sa
from alembic import op

revision = "0004_recast_shot_layers"
down_revision = "0003_recast"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "editing_recast_shots", sa.Column("layers", sa.JSON(), nullable=False, server_default="[]")
    )


def downgrade():
    with op.batch_alter_table("editing_recast_shots") as batch:
        batch.drop_column("layers")
