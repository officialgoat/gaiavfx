"""Sets in scenes (ADR 0019): a place's set, a segment's opening shot, and the angle a saved set view aims.

- `direction_places.set_id`: the set the place is built as (an id in the sets module; no foreign key across
  modules).
- `direction_segments.opening_angle_id` and `opening_technique`: the first shot's angle and technique override.
  Segments with cuts read their opening from the first cut's `from_angle_id`; the stored value is backfilled from
  it here, so a segment that loses its last cut keeps the angle it opened on.
- `set_views.angle_id`: the Direction angle whose camera this view is.
"""

import sqlalchemy as sa
from alembic import op

revision = "0012_sets_in_scenes"
down_revision = "0011_sets"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("direction_places") as table:
        table.add_column(sa.Column("set_id", sa.String(36), nullable=True))
    with op.batch_alter_table("direction_segments") as table:
        table.add_column(
            sa.Column(
                "opening_angle_id",
                sa.String(36),
                sa.ForeignKey("direction_angles.id", name="fk_direction_segments_opening_angle"),
                nullable=True,
            )
        )
        table.add_column(sa.Column("opening_technique", sa.String(80), nullable=True))
    with op.batch_alter_table("set_views") as table:
        table.add_column(sa.Column("angle_id", sa.String(36), nullable=True))
    op.execute(
        """
        UPDATE direction_segments SET opening_angle_id = (
            SELECT from_angle_id FROM direction_cuts
            WHERE direction_cuts.segment_id = direction_segments.id
            ORDER BY frame LIMIT 1
        )
        """
    )


def downgrade():
    with op.batch_alter_table("set_views") as table:
        table.drop_column("angle_id")
    with op.batch_alter_table("direction_segments") as table:
        table.drop_column("opening_technique")
        table.drop_column("opening_angle_id")
    with op.batch_alter_table("direction_places") as table:
        table.drop_column("set_id")
