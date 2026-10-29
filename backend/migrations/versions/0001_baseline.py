"""Rebuild baseline; archived libraries open with tag pre-rebuild-2026-09."""

import sqlalchemy as sa
from alembic import op

revision = "0001_baseline"
down_revision = None
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "agent_sessions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("movie_id", sa.String(length=120), nullable=False),
        sa.Column("scope_kind", sa.String(length=20), nullable=False),
        sa.Column("scope_id", sa.String(length=120), nullable=False),
        sa.Column("state", sa.String(length=30), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("document", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_agent_sessions_movie_id"), "agent_sessions", ["movie_id"], unique=False
    )
    op.create_table(
        "catalog_entities",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_catalog_entities_kind"), "catalog_entities", ["kind"], unique=False)
    op.create_table(
        "direction_movies",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("title", sa.String(length=120), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "direction_places",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(), server_default="", nullable=False),
        sa.Column("sound", sa.String(), server_default="", nullable=False),
        sa.Column("overhead", sa.String(), server_default="", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("plate_asset_id", sa.String(length=120), nullable=True),
        sa.Column("plate_angle_id", sa.String(length=36), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "editing_take_selections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("movie_id", sa.String(length=120), nullable=False),
        sa.Column("segment_id", sa.String(length=120), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=120), nullable=False),
        sa.Column("authority", sa.String(length=20), nullable=False),
        sa.Column("basis", sa.String(length=20), nullable=True),
        sa.Column("selected_at", sa.String(length=40), nullable=False),
        sa.Column("superseded_at", sa.String(length=40), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        "ix_editing_take_selections_segment",
        "editing_take_selections",
        ["movie_id", "segment_id"],
        unique=False,
    )
    op.create_index(
        "uq_editing_take_selections_current",
        "editing_take_selections",
        ["movie_id", "segment_id"],
        unique=True,
        sqlite_where=sa.text("superseded_at IS NULL"),
    )
    op.create_table(
        "execution_requests",
        sa.Column("key", sa.String(length=160), nullable=False),
        sa.Column("operation", sa.String(length=160), nullable=False),
        sa.Column("fingerprint", sa.String(length=64), nullable=False),
        sa.Column("resource_id", sa.String(length=36), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "generation_operations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=40), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("awaits_review", sa.Boolean(), nullable=False),
        sa.Column("request", sa.JSON(), nullable=False),
        sa.Column("request_key", sa.String(length=80), nullable=True),
        sa.Column("budget_reserved", sa.Integer(), nullable=False),
        sa.Column("budget_released_at", sa.String(length=40), nullable=True),
        sa.Column("cancel_requested", sa.Boolean(), nullable=False),
        sa.Column("review", sa.JSON(), nullable=True),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_generation_operations_request_key"),
        "generation_operations",
        ["request_key"],
        unique=True,
    )
    op.create_table(
        "generation_review_sets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("purpose", sa.String(length=40), nullable=False),
        sa.Column("context", sa.JSON(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "library",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "library_artifacts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("kind", sa.String(length=30), nullable=False),
        sa.Column("info", sa.JSON(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256"),
    )
    op.create_table(
        "library_assets",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("sha256", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=12), nullable=False),
        sa.Column("path", sa.String(), nullable=False),
        sa.Column("mime", sa.String(length=100), nullable=False),
        sa.Column("info", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=False),
        sa.Column("review", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("sha256"),
    )
    op.create_index(
        op.f("ix_library_assets_created_at"), "library_assets", ["created_at"], unique=False
    )
    op.create_index(op.f("ix_library_assets_kind"), "library_assets", ["kind"], unique=False)
    op.create_table(
        "library_collections",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "settings",
        sa.Column("key", sa.String(length=50), nullable=False),
        sa.Column("value", sa.JSON(), nullable=False),
        sa.PrimaryKeyConstraint("key"),
    )
    op.create_table(
        "agent_turns",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("session_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("action", sa.JSON(), nullable=True),
        sa.Column("result", sa.JSON(), nullable=True),
        sa.Column("receipts", sa.JSON(), nullable=False),
        sa.Column("provenance", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["session_id"],
            ["agent_sessions.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("session_id", "ordinal", name="uq_agent_turn_ordinal"),
    )
    op.create_index(op.f("ix_agent_turns_session_id"), "agent_turns", ["session_id"], unique=False)
    op.create_table(
        "catalog_entity_assets",
        sa.Column("entity_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=30), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["library_assets.id"],
        ),
        sa.ForeignKeyConstraint(
            ["entity_id"],
            ["catalog_entities.id"],
        ),
        sa.PrimaryKeyConstraint("entity_id", "asset_id"),
    )
    op.create_table(
        "direction_angles",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("place_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(), server_default="", nullable=False),
        sa.Column("revision", sa.Integer(), server_default="1", nullable=False),
        sa.Column("placement", sa.JSON(), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["place_id"],
            ["direction_places.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_direction_angles_place_id"), "direction_angles", ["place_id"], unique=False
    )
    op.create_table(
        "direction_receipts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("movie_id", sa.String(length=36), nullable=False),
        sa.Column("command", sa.String(length=40), nullable=False),
        sa.Column("target_kind", sa.String(length=20), nullable=False),
        sa.Column("target_id", sa.String(length=120), nullable=False),
        sa.Column("before", sa.JSON(), nullable=False),
        sa.Column("after", sa.JSON(), nullable=False),
        sa.Column("undoes", sa.String(length=36), nullable=True),
        sa.Column("undone_at", sa.String(length=40), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["movie_id"],
            ["direction_movies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_direction_receipts_movie_id"), "direction_receipts", ["movie_id"], unique=False
    )
    op.create_table(
        "direction_segments",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("movie_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("delivered_frames", sa.Integer(), nullable=False),
        sa.Column("continues", sa.Boolean(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("content_schema", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["movie_id"],
            ["direction_movies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("movie_id", "ordinal", name="uq_direction_segments_movie_ordinal"),
    )
    op.create_index(
        op.f("ix_direction_segments_movie_id"), "direction_segments", ["movie_id"], unique=False
    )
    op.create_table(
        "direction_subjects",
        sa.Column("id", sa.String(length=120), nullable=False),
        sa.Column("movie_id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(length=120), nullable=False),
        sa.Column("description", sa.String(), nullable=False),
        sa.Column("keeps", sa.JSON(), nullable=False),
        sa.Column("identity_asset_id", sa.String(length=120), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["movie_id"],
            ["direction_movies.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_direction_subjects_movie_id"), "direction_subjects", ["movie_id"], unique=False
    )
    op.create_table(
        "generation_jobs",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("depends_on", sa.Integer(), nullable=True),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("executor", sa.String(length=10), nullable=False),
        sa.Column("integration", sa.String(length=40), nullable=False),
        sa.Column("step", sa.String(length=40), nullable=True),
        sa.Column("graph", sa.JSON(), nullable=True),
        sa.Column("inputs", sa.JSON(), nullable=False),
        sa.Column("seed", sa.Integer(), nullable=True),
        sa.Column("snapshot", sa.JSON(), nullable=False),
        sa.Column("state", sa.String(length=20), nullable=False),
        sa.Column("submitted", sa.Boolean(), nullable=False),
        sa.Column("endpoint", sa.String(), nullable=True),
        sa.Column("remote_id", sa.String(length=100), nullable=True),
        sa.Column("outputs", sa.JSON(), nullable=False),
        sa.Column("error", sa.String(), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["generation_operations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_generation_jobs_operation_id"), "generation_jobs", ["operation_id"], unique=False
    )
    op.create_table(
        "generation_review_rankings",
        sa.Column("review_set_id", sa.String(length=36), nullable=False),
        sa.Column("tiers", sa.JSON(), nullable=False),
        sa.Column("usable_tiers", sa.Integer(), nullable=False),
        sa.Column("notes", sa.JSON(), nullable=False),
        sa.Column("tags", sa.JSON(), nullable=False),
        sa.Column("general", sa.Text(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["review_set_id"],
            ["generation_review_sets.id"],
        ),
        sa.PrimaryKeyConstraint("review_set_id"),
    )
    op.create_table(
        "library_collection_assets",
        sa.Column("collection_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=36), nullable=False),
        sa.ForeignKeyConstraint(
            ["asset_id"],
            ["library_assets.id"],
        ),
        sa.ForeignKeyConstraint(
            ["collection_id"],
            ["library_collections.id"],
        ),
        sa.PrimaryKeyConstraint("collection_id", "asset_id"),
    )
    op.create_table(
        "direction_cuts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("segment_id", sa.String(length=36), nullable=False),
        sa.Column("frame", sa.Integer(), nullable=False),
        sa.Column("from_angle_id", sa.String(length=36), nullable=False),
        sa.Column("to_angle_id", sa.String(length=36), nullable=False),
        sa.Column("relation", sa.String(length=20), nullable=True),
        sa.Column("crosses_barrier", sa.Boolean(), nullable=True),
        sa.Column("relation_source", sa.String(length=20), nullable=False),
        sa.Column("receives_predecessor", sa.Boolean(), nullable=False),
        sa.Column("side_facts", sa.Boolean(), nullable=False),
        sa.Column("fidelity", sa.Boolean(), nullable=False),
        sa.Column("override_technique", sa.String(length=80), nullable=True),
        sa.Column("insertion", sa.JSON(), nullable=True),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["from_angle_id"],
            ["direction_angles.id"],
        ),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["direction_segments.id"],
        ),
        sa.ForeignKeyConstraint(
            ["to_angle_id"],
            ["direction_angles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("segment_id", "frame", name="uq_direction_cuts_segment_frame"),
    )
    op.create_index(
        op.f("ix_direction_cuts_segment_id"), "direction_cuts", ["segment_id"], unique=False
    )
    op.create_table(
        "direction_evidence",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("angle_id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False),
        sa.Column("asset_id", sa.String(length=120), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("approved_at", sa.String(length=40), nullable=True),
        sa.Column("approved_by", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["angle_id"],
            ["direction_angles.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_direction_evidence_angle_id"), "direction_evidence", ["angle_id"], unique=False
    )
    op.create_table(
        "direction_phases",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("segment_id", sa.String(length=36), nullable=False),
        sa.Column("start_frame", sa.Integer(), nullable=False),
        sa.Column("content", sa.JSON(), nullable=False),
        sa.Column("content_schema", sa.Integer(), nullable=False),
        sa.Column("revision", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.Column("updated_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["segment_id"],
            ["direction_segments.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("segment_id", "start_frame", name="uq_direction_phases_segment_start"),
    )
    op.create_index(
        op.f("ix_direction_phases_segment_id"), "direction_phases", ["segment_id"], unique=False
    )
    op.create_table(
        "generation_candidates",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("job_id", sa.String(length=36), nullable=False),
        sa.Column("operation_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=120), nullable=False),
        sa.Column("artifact_id", sa.String(length=36), nullable=True),
        sa.Column("output_key", sa.String(), nullable=False),
        sa.Column("kind", sa.String(length=10), nullable=False),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["job_id"],
            ["generation_jobs.id"],
        ),
        sa.ForeignKeyConstraint(
            ["operation_id"],
            ["generation_operations.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("job_id", "output_key"),
    )
    op.create_index(
        op.f("ix_generation_candidates_job_id"), "generation_candidates", ["job_id"], unique=False
    )
    op.create_index(
        op.f("ix_generation_candidates_operation_id"),
        "generation_candidates",
        ["operation_id"],
        unique=False,
    )
    op.create_table(
        "direction_keyframes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("cut_id", sa.String(length=36), nullable=False),
        sa.Column("asset_id", sa.String(length=120), nullable=False),
        sa.Column("evidence_id", sa.String(length=36), nullable=False),
        sa.Column("approved", sa.Boolean(), nullable=False),
        sa.Column("approved_at", sa.String(length=40), nullable=True),
        sa.Column("approved_by", sa.String(length=20), nullable=True),
        sa.Column("created_at", sa.String(length=40), nullable=False),
        sa.ForeignKeyConstraint(
            ["cut_id"],
            ["direction_cuts.id"],
        ),
        sa.ForeignKeyConstraint(
            ["evidence_id"],
            ["direction_evidence.id"],
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(
        op.f("ix_direction_keyframes_cut_id"), "direction_keyframes", ["cut_id"], unique=False
    )
    op.create_table(
        "generation_review_members",
        sa.Column("review_set_id", sa.String(length=36), nullable=False),
        sa.Column("candidate_id", sa.String(length=36), nullable=False),
        sa.Column("letter", sa.String(length=4), nullable=False),
        sa.ForeignKeyConstraint(
            ["candidate_id"],
            ["generation_candidates.id"],
        ),
        sa.ForeignKeyConstraint(
            ["review_set_id"],
            ["generation_review_sets.id"],
        ),
        sa.PrimaryKeyConstraint("review_set_id", "candidate_id"),
        sa.UniqueConstraint("review_set_id", "candidate_id"),
        sa.UniqueConstraint("review_set_id", "letter"),
    )


def downgrade():
    op.drop_table("generation_review_members")
    op.drop_index(op.f("ix_direction_keyframes_cut_id"), table_name="direction_keyframes")
    op.drop_table("direction_keyframes")
    op.drop_index(op.f("ix_generation_candidates_operation_id"), table_name="generation_candidates")
    op.drop_index(op.f("ix_generation_candidates_job_id"), table_name="generation_candidates")
    op.drop_table("generation_candidates")
    op.drop_index(op.f("ix_direction_phases_segment_id"), table_name="direction_phases")
    op.drop_table("direction_phases")
    op.drop_index(op.f("ix_direction_evidence_angle_id"), table_name="direction_evidence")
    op.drop_table("direction_evidence")
    op.drop_index(op.f("ix_direction_cuts_segment_id"), table_name="direction_cuts")
    op.drop_table("direction_cuts")
    op.drop_table("library_collection_assets")
    op.drop_table("generation_review_rankings")
    op.drop_index(op.f("ix_generation_jobs_operation_id"), table_name="generation_jobs")
    op.drop_table("generation_jobs")
    op.drop_index(op.f("ix_direction_subjects_movie_id"), table_name="direction_subjects")
    op.drop_table("direction_subjects")
    op.drop_index(op.f("ix_direction_segments_movie_id"), table_name="direction_segments")
    op.drop_table("direction_segments")
    op.drop_index(op.f("ix_direction_receipts_movie_id"), table_name="direction_receipts")
    op.drop_table("direction_receipts")
    op.drop_index(op.f("ix_direction_angles_place_id"), table_name="direction_angles")
    op.drop_table("direction_angles")
    op.drop_table("catalog_entity_assets")
    op.drop_index(op.f("ix_agent_turns_session_id"), table_name="agent_turns")
    op.drop_table("agent_turns")
    op.drop_table("settings")
    op.drop_table("library_collections")
    op.drop_index(op.f("ix_library_assets_kind"), table_name="library_assets")
    op.drop_index(op.f("ix_library_assets_created_at"), table_name="library_assets")
    op.drop_table("library_assets")
    op.drop_table("library_artifacts")
    op.drop_table("library")
    op.drop_table("generation_review_sets")
    op.drop_index(op.f("ix_generation_operations_request_key"), table_name="generation_operations")
    op.drop_table("generation_operations")
    op.drop_table("execution_requests")
    op.drop_index(
        "uq_editing_take_selections_current",
        table_name="editing_take_selections",
        sqlite_where=sa.text("superseded_at IS NULL"),
    )
    op.drop_index("ix_editing_take_selections_segment", table_name="editing_take_selections")
    op.drop_table("editing_take_selections")
    op.drop_table("direction_places")
    op.drop_table("direction_movies")
    op.drop_index(op.f("ix_catalog_entities_kind"), table_name="catalog_entities")
    op.drop_table("catalog_entities")
    op.drop_index(op.f("ix_agent_sessions_movie_id"), table_name="agent_sessions")
    op.drop_table("agent_sessions")
