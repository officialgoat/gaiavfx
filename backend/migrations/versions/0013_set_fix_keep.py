"""Keep this still (ADR 0020 §8): a stage's fix may keep a probe still that consolidation set aside.

`set_fixes.action` gains `keep`. SQLite holds the CHECK in the table's definition, so the table is copied with the
new constraint (batch mode, from the table as 0011 created it).
"""

import sqlalchemy as sa
from alembic import op

revision = "0013_set_fix_keep"
down_revision = "0012_sets_in_scenes"
branch_labels = None
depends_on = None

BEFORE = "action IN ('reshoot', 'extend', 'retract')"
AFTER = "action IN ('reshoot', 'extend', 'retract', 'keep')"


def _fixes(action: str) -> sa.Table:
    return sa.Table(
        "set_fixes",
        sa.MetaData(),
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("set_id", sa.String(36), sa.ForeignKey("sets.id"), nullable=False),
        sa.Column("issue_id", sa.String(36)),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("author", sa.String(10), nullable=False),
        sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("shows", sa.String(), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("operation_id", sa.String(36)),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.CheckConstraint(action, name="ck_set_fix_action"),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'done', 'failed', 'cancelled')", name="ck_set_fix_state"
        ),
        sa.CheckConstraint("author IN ('human', 'agent', 'judge')", name="ck_set_fix_author"),
        sa.Index("ix_set_fixes_set_id", "set_id"),
    )


def _replace(before: str, after: str) -> None:
    with op.batch_alter_table("set_fixes", copy_from=_fixes(before), recreate="always") as table:
        table.drop_constraint("ck_set_fix_action", type_="check")
        table.create_check_constraint("ck_set_fix_action", after)


def upgrade():
    _replace(BEFORE, AFTER)


def downgrade():
    # The older schema cannot hold a kept still's fix.
    op.execute("DELETE FROM set_fixes WHERE action = 'keep'")
    _replace(AFTER, BEFORE)
