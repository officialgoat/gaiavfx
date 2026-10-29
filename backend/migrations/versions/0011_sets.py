"""Sets (ADR 0018): versioned atlases with their keyframes, issues, fixes, hidden regions and saved views.

Files stay in Library (artifacts and image assets); these tables hold identities, poses and decisions.
"""

import sqlalchemy as sa
from alembic import op

revision = "0011_sets"
down_revision = "0010_recast_voice"
branch_labels = None
depends_on = None


def _set_id():
    return sa.Column("set_id", sa.String(36), sa.ForeignKey("sets.id"), nullable=False)


def upgrade():
    op.create_table(
        "sets",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("place_text", sa.String(), nullable=False),
        sa.Column("seed", sa.Integer()),
        sa.Column("current_version_id", sa.String(36)),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
    )
    op.create_table(
        "set_versions",
        sa.Column("id", sa.String(36), primary_key=True),
        _set_id(),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("parent_id", sa.String(36)),
        sa.Column("state", sa.String(20), nullable=False),
        sa.Column("origin", sa.String(20), nullable=False),
        sa.Column("operation_id", sa.String(36)),
        sa.Column("atlas_artifact_id", sa.String(36), nullable=False),
        sa.Column("map_artifact_id", sa.String(36)),
        sa.Column("splat_artifact_id", sa.String(36)),
        sa.Column("map_meta", sa.JSON(), nullable=False),
        sa.Column("metrics", sa.JSON(), nullable=False),
        sa.Column("note", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("decided_at", sa.String(40)),
        sa.UniqueConstraint("set_id", "ordinal"),
        sa.CheckConstraint(
            "state IN ('proposed', 'accepted', 'discarded')", name="ck_set_version_state"
        ),
        sa.CheckConstraint(
            "origin IN ('import', 'seed', 'expand', 'repair', 'human')",
            name="ck_set_version_origin",
        ),
    )
    op.create_index("ix_set_versions_set_id", "set_versions", ["set_id"])
    op.create_table(
        "set_keyframes",
        sa.Column("id", sa.String(36), primary_key=True),
        _set_id(),
        sa.Column("key", sa.String(80), nullable=False),
        sa.Column("kind", sa.String(10), nullable=False),
        sa.Column("role", sa.String(20), nullable=False),
        sa.Column("viewmat", sa.JSON(), nullable=False),
        sa.Column("intrinsics", sa.JSON()),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("heading", sa.Float()),
        sa.Column("image_asset_id", sa.String(36), nullable=False),
        sa.Column("depth_artifact_id", sa.String(36), nullable=False),
        sa.Column("supersedes_id", sa.String(36)),
        sa.Column("info", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.CheckConstraint("kind IN ('erp', 'pinhole')", name="ck_set_keyframe_kind"),
        sa.CheckConstraint(
            "role IN ('observed', 'generated', 'trajectory', 'appearance-only')",
            name="ck_set_keyframe_role",
        ),
    )
    op.create_index("ix_set_keyframes_set_id", "set_keyframes", ["set_id"])
    op.create_table(
        "set_version_keyframes",
        sa.Column("version_id", sa.String(36), sa.ForeignKey("set_versions.id"), primary_key=True),
        sa.Column(
            "keyframe_id", sa.String(36), sa.ForeignKey("set_keyframes.id"), primary_key=True
        ),
        sa.Column("label", sa.String(10), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.UniqueConstraint("version_id", "label"),
    )
    op.create_table(
        "set_issues",
        sa.Column("id", sa.String(36), primary_key=True),
        _set_id(),
        sa.Column("found_in_id", sa.String(36), nullable=False),
        sa.Column("resolved_in_id", sa.String(36)),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("author", sa.String(10), nullable=False),
        sa.Column("status", sa.String(10), nullable=False),
        sa.Column("cells", sa.JSON(), nullable=False),
        sa.Column("cameras", sa.JSON(), nullable=False),
        sa.Column("region", sa.JSON()),
        sa.Column("why", sa.String(), nullable=False),
        sa.Column("note", sa.String(), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.CheckConstraint("author IN ('human', 'agent', 'judge')", name="ck_set_issue_author"),
        sa.CheckConstraint(
            "status IN ('open', 'queued', 'fixed', 'dismissed', 'failed')",
            name="ck_set_issue_status",
        ),
    )
    op.create_index("ix_set_issues_set_id", "set_issues", ["set_id"])
    op.create_table(
        "set_fixes",
        sa.Column("id", sa.String(36), primary_key=True),
        _set_id(),
        sa.Column("issue_id", sa.String(36)),
        sa.Column("action", sa.String(10), nullable=False),
        sa.Column("author", sa.String(10), nullable=False),
        sa.Column("target", sa.JSON(), nullable=False),
        sa.Column("shows", sa.String(), nullable=False),
        sa.Column("state", sa.String(10), nullable=False),
        sa.Column("operation_id", sa.String(36)),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("updated_at", sa.String(40), nullable=False),
        sa.CheckConstraint("action IN ('reshoot', 'extend', 'retract')", name="ck_set_fix_action"),
        sa.CheckConstraint(
            "state IN ('queued', 'running', 'done', 'failed', 'cancelled')", name="ck_set_fix_state"
        ),
        sa.CheckConstraint("author IN ('human', 'agent', 'judge')", name="ck_set_fix_author"),
    )
    op.create_index("ix_set_fixes_set_id", "set_fixes", ["set_id"])
    op.create_table(
        "set_hide_regions",
        sa.Column("id", sa.String(36), primary_key=True),
        _set_id(),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("shape", sa.String(10), nullable=False),
        sa.Column("params", sa.JSON(), nullable=False),
        sa.Column("label", sa.String(80), nullable=False),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("removed_at", sa.String(40)),
        sa.CheckConstraint("shape IN ('box', 'sphere')", name="ck_set_hide_shape"),
    )
    op.create_index("ix_set_hide_regions_set_id", "set_hide_regions", ["set_id"])
    op.create_table(
        "set_views",
        sa.Column("id", sa.String(36), primary_key=True),
        _set_id(),
        sa.Column("version_id", sa.String(36), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("viewmat", sa.JSON(), nullable=False),
        sa.Column("fov", sa.Float(), nullable=False),
        sa.Column("width", sa.Integer(), nullable=False),
        sa.Column("height", sa.Integer(), nullable=False),
        sa.Column("hide_region_ids", sa.JSON(), nullable=False),
        sa.Column("author", sa.String(10), nullable=False),
        sa.Column("asset_id", sa.String(36)),
        sa.Column("created_at", sa.String(40), nullable=False),
        sa.Column("removed_at", sa.String(40)),
    )
    op.create_index("ix_set_views_set_id", "set_views", ["set_id"])


def downgrade():
    for table in (
        "set_views",
        "set_hide_regions",
        "set_fixes",
        "set_issues",
        "set_version_keyframes",
        "set_keyframes",
        "set_versions",
        "sets",
    ):
        op.drop_table(table)
