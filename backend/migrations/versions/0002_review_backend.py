"""Candidate measurements and contextual reference judgments.

Revision ID: 0002_review_backend
Revises: 0001_baseline
"""

import sqlalchemy as sa
from alembic import op

revision = "0002_review_backend"
down_revision = "0001_baseline"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column("generation_candidates", sa.Column("prescreen", sa.JSON(), nullable=True))
    op.add_column("direction_evidence", sa.Column("inputs", sa.JSON(), nullable=True))
    op.add_column("direction_keyframes", sa.Column("inputs", sa.JSON(), nullable=True))
    op.add_column(
        "direction_keyframes", sa.Column("subject_asset_id", sa.String(120), nullable=True)
    )
    op.create_table(
        "direction_cut_suitability",
        sa.Column("id", sa.String(36), primary_key=True),
        sa.Column("cut_id", sa.String(36), sa.ForeignKey("direction_cuts.id"), nullable=False),
        sa.Column("reference_kind", sa.String(10), nullable=False),
        sa.Column("reference_id", sa.String(120), nullable=False),
        sa.Column("verdict", sa.String(10), nullable=False),
        sa.Column("note", sa.String(500), nullable=False),
        sa.Column("inputs", sa.JSON(), nullable=True),
        sa.Column("judged_at", sa.String(40), nullable=False),
        sa.Column("judged_by", sa.String(20), nullable=False),
        sa.Column("superseded_at", sa.String(40), nullable=True),
    )
    op.create_index("ix_direction_cut_suitability_cut_id", "direction_cut_suitability", ["cut_id"])
    op.create_index(
        "uq_direction_cut_suitability_current",
        "direction_cut_suitability",
        ["cut_id", "reference_kind", "reference_id"],
        unique=True,
        sqlite_where=sa.text("superseded_at IS NULL"),
    )


def downgrade():
    op.drop_table("direction_cut_suitability")
    op.drop_column("direction_keyframes", "subject_asset_id")
    op.drop_column("direction_keyframes", "inputs")
    op.drop_column("direction_evidence", "inputs")
    op.drop_column("generation_candidates", "prescreen")
