"""Human decisions about generated target references; assets remain in Library."""

import sqlalchemy as sa
from alembic import op

revision = "0005_recast_character_reference"
down_revision = "0004_recast_shot_layers"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("editing_recast_characters", sa.Column("reference_asset_id", sa.String(120)))
    op.add_column("editing_recast_characters", sa.Column("reference_state", sa.String(20)))


def downgrade():
    with op.batch_alter_table("editing_recast_characters") as batch:
        batch.drop_column("reference_state")
        batch.drop_column("reference_asset_id")
