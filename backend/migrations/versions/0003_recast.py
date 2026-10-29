"""Editing-owned film recasts and append-only human choices."""

import sqlalchemy as sa
from alembic import op

revision = "0003_recast"
down_revision = "0002_review_backend"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "editing_recasts",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column("title", sa.String(length=120), primary_key=False, nullable=False),
        sa.Column("source_asset_id", sa.String(length=120), primary_key=False, nullable=False),
        sa.Column("analysis_operation_id", sa.String(length=36), primary_key=False, nullable=True),
        sa.Column("revision", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("run_state", sa.String(length=20), primary_key=False, nullable=False),
        sa.Column("run_reason", sa.String(), nullable=True),
        sa.Column("run_mode", sa.String(length=10), primary_key=False, nullable=False),
        sa.Column("run_range_start", sa.Integer(), primary_key=False, nullable=True),
        sa.Column("run_range_end", sa.Integer(), primary_key=False, nullable=True),
        sa.Column("render_budget", sa.Integer(), primary_key=False, nullable=False),
        sa.Column("created_at", sa.String(length=40), primary_key=False, nullable=False),
        sa.Column("updated_at", sa.String(length=40), primary_key=False, nullable=False),
        sa.Column("request_key", sa.String(length=80), primary_key=False, nullable=False),
        sa.UniqueConstraint("request_key"),
    )
    op.create_table(
        "editing_recast_characters",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "recast_id",
            sa.String(length=36),
            sa.ForeignKey("editing_recasts.id"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column("cluster_id", sa.String(length=120), primary_key=False, nullable=True),
        sa.Column("label", sa.String(length=80), primary_key=False, nullable=False),
        sa.Column("hidden", sa.Boolean(), primary_key=False, nullable=False),
        sa.Column("target_asset_id", sa.String(length=120), primary_key=False, nullable=True),
        sa.Column("target_label", sa.String(length=80), primary_key=False, nullable=True),
        sa.Column("target_state", sa.String(length=20), primary_key=False, nullable=True),
        sa.Column("target_proposed_by", sa.String(length=20), primary_key=False, nullable=True),
        sa.Column("ordinal", sa.Integer(), primary_key=False, nullable=False),
        sa.UniqueConstraint("recast_id", "cluster_id"),
    )
    op.create_index(
        "ix_editing_recast_characters_recast_id", "editing_recast_characters", ["recast_id"]
    )
    op.create_table(
        "editing_recast_track_overrides",
        sa.Column(
            "recast_id",
            sa.String(length=36),
            sa.ForeignKey("editing_recasts.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("track_id", sa.String(length=120), primary_key=True, nullable=False),
        sa.Column("character_id", sa.String(length=36), primary_key=False, nullable=False),
        sa.Column("created_at", sa.String(length=40), primary_key=False, nullable=False),
    )
    op.create_table(
        "editing_recast_shots",
        sa.Column(
            "recast_id",
            sa.String(length=36),
            sa.ForeignKey("editing_recasts.id"),
            primary_key=True,
            nullable=False,
        ),
        sa.Column("shot_id", sa.String(length=120), primary_key=True, nullable=False),
        sa.Column("included", sa.Boolean(), primary_key=False, nullable=True),
        sa.Column("priority", sa.Integer(), primary_key=False, nullable=True),
        sa.Column("plan", sa.JSON(), primary_key=False, nullable=True),
        sa.Column("window_analysis_ops", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("window_replacement_ops", sa.JSON(), primary_key=False, nullable=False),
        sa.Column("assembly_op", sa.String(length=36), primary_key=False, nullable=True),
        sa.Column("mode", sa.String(length=10), primary_key=False, nullable=True),
    )
    op.create_table(
        "editing_recast_choices",
        sa.Column("id", sa.String(length=36), primary_key=True, nullable=False),
        sa.Column(
            "recast_id",
            sa.String(length=36),
            sa.ForeignKey("editing_recasts.id"),
            primary_key=False,
            nullable=False,
        ),
        sa.Column("shot_id", sa.String(length=120), primary_key=False, nullable=False),
        sa.Column("version", sa.String(length=64), primary_key=False, nullable=False),
        sa.Column("authority", sa.String(length=20), primary_key=False, nullable=False),
        sa.Column("chosen_at", sa.String(length=40), primary_key=False, nullable=False),
        sa.Column("recast_revision", sa.Integer(), primary_key=False, nullable=False),
        sa.CheckConstraint("authority = 'user'", name="ck_recast_choice_user"),
    )
    op.create_index("ix_editing_recast_choices_recast_id", "editing_recast_choices", ["recast_id"])


def downgrade():
    op.drop_table("editing_recast_choices")
    op.drop_table("editing_recast_shots")
    op.drop_table("editing_recast_track_overrides")
    op.drop_table("editing_recast_characters")
    op.drop_table("editing_recasts")
