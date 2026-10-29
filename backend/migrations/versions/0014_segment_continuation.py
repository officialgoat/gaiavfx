"""Per-segment continuation: the handoff method and the motion-context length of a continuing segment.

- `direction_segments.continuation_method`: the integration's handoff method (H3: `paired` or `masked`).
- `direction_segments.context_frames`: the motion-context length in model-window frames.

Both are nullable: null follows the integration's rule and default. Existing segments keep their renders.
"""

import sqlalchemy as sa
from alembic import op

revision = "0014_segment_continuation"
down_revision = "0013_set_fix_keep"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("direction_segments") as table:
        table.add_column(sa.Column("continuation_method", sa.String(16), nullable=True))
        table.add_column(sa.Column("context_frames", sa.Integer(), nullable=True))


def downgrade():
    with op.batch_alter_table("direction_segments") as table:
        table.drop_column("context_frames")
        table.drop_column("continuation_method")
