"""The rebuild has one explicit, reversible schema matching every kept owner."""

from importlib import import_module
from pathlib import Path

from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import inspect

from gaiavfx.infrastructure.database import Base, Database
from gaiavfx.modules.library.application import LibraryService

MIGRATIONS = Path(__file__).parents[1] / "migrations"
TABLES = {
    "library",
    "settings",
    "execution_requests",
    "library_assets",
    "library_collections",
    "library_collection_assets",
    "library_artifacts",
    "catalog_entities",
    "catalog_entity_assets",
    "direction_movies",
    "direction_subjects",
    "direction_segments",
    "direction_phases",
    "direction_receipts",
    "direction_places",
    "direction_angles",
    "direction_evidence",
    "direction_cuts",
    "direction_keyframes",
    "direction_cut_suitability",
    "generation_operations",
    "generation_jobs",
    "generation_candidates",
    "generation_review_sets",
    "generation_review_members",
    "generation_review_rankings",
    "editing_take_selections",
    "editing_recasts",
    "editing_recast_characters",
    "editing_recast_track_overrides",
    "editing_recast_appearance_links",
    "editing_recast_not_same",
    "editing_recast_edits",
    "editing_recast_lines",
    "editing_recast_line_choices",
    "editing_recast_shots",
    "editing_recast_choices",
    "agent_sessions",
    "agent_turns",
    "sets",
    "set_versions",
    "set_keyframes",
    "set_version_keyframes",
    "set_issues",
    "set_fixes",
    "set_hide_regions",
    "set_views",
}


def test_layers_upgrade_populates_existing_shots_and_downgrades(tmp_path):
    config = Config()
    config.set_main_option("script_location", MIGRATIONS.as_posix())
    config.set_main_option(
        "sqlalchemy.url", "sqlite:///" + (tmp_path / "library.sqlite3").as_posix()
    )
    command.upgrade(config, "0003_recast")
    db = Database(tmp_path)
    try:
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO editing_recasts (id, title, source_asset_id, revision, run_state, run_mode, render_budget, created_at, updated_at, request_key) VALUES ('r', 'film', 'f', 1, 'idle', 'draft', 20, 'now', 'now', 'request')"
            )
            connection.exec_driver_sql(
                "INSERT INTO editing_recast_shots (recast_id, shot_id, window_analysis_ops, window_replacement_ops) VALUES ('r', 's0001', '[]', '[]')"
            )
        command.upgrade(config, "head")
        with db.engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT layers FROM editing_recast_shots").scalar()
                == "[]"
            )
        command.downgrade(config, "0003_recast")
        with db.engine.connect() as connection:
            assert (
                connection.exec_driver_sql("SELECT shot_id FROM editing_recast_shots").scalar()
                == "s0001"
            )
            assert "layers" not in {
                c["name"] for c in inspect(connection).get_columns("editing_recast_shots")
            }
    finally:
        db.close()


def test_a_stage_fix_may_keep_a_still_and_the_downgrade_drops_kept_fixes(tmp_path):
    # ADR 0020 §8: SQLite holds the action's CHECK in the table, so 0013 copies the table with the new constraint.
    import pytest
    from sqlalchemy.exc import IntegrityError

    config = Config()
    config.set_main_option("script_location", MIGRATIONS.as_posix())
    config.set_main_option(
        "sqlalchemy.url", "sqlite:///" + (tmp_path / "library.sqlite3").as_posix()
    )
    command.upgrade(config, "0012_sets_in_scenes")
    db = Database(tmp_path)

    def fix(connection, fix_id, action):
        connection.exec_driver_sql(
            "INSERT INTO set_fixes (id, set_id, action, author, target, shows, state, created_at, "
            f"updated_at) VALUES ('{fix_id}', 's', '{action}', 'human', '{{}}', '', 'queued', 'now', 'now')"
        )

    try:
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO sets (id, name, place_text, revision, provenance, created_at, updated_at) "
                "VALUES ('s', 'Shack', '', 1, '{}', 'now', 'now')"
            )
            fix(connection, "a", "extend")
        with pytest.raises(IntegrityError), db.engine.begin() as connection:
            fix(connection, "b", "keep")
        command.upgrade(config, "head")
        with db.engine.begin() as connection:
            fix(connection, "b", "keep")
            assert connection.exec_driver_sql("SELECT count(*) FROM set_fixes").scalar() == 2
        with pytest.raises(IntegrityError), db.engine.begin() as connection:
            fix(connection, "c", "paint")
        command.downgrade(config, "0012_sets_in_scenes")
        with db.engine.connect() as connection:
            assert connection.exec_driver_sql("SELECT id FROM set_fixes").all() == [("a",)]
    finally:
        db.close()


def test_existing_segments_follow_the_continuation_rule_after_0014(tmp_path):
    config = Config()
    config.set_main_option("script_location", MIGRATIONS.as_posix())
    config.set_main_option(
        "sqlalchemy.url", "sqlite:///" + (tmp_path / "library.sqlite3").as_posix()
    )
    command.upgrade(config, "0013_set_fix_keep")
    db = Database(tmp_path)
    try:
        with db.engine.begin() as connection:
            connection.exec_driver_sql(
                "INSERT INTO direction_movies (id, title, revision, created_at, updated_at) "
                "VALUES ('m', 'Movie', 1, 'now', 'now')"
            )
            connection.exec_driver_sql(
                "INSERT INTO direction_segments (id, movie_id, ordinal, delivered_frames, continues, "
                "content, content_schema, revision, created_at, updated_at) "
                "VALUES ('s', 'm', 0, 48, 1, '{}', 1, 3, 'now', 'now')"
            )
        command.upgrade(config, "head")
        with db.engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT continuation_method, context_frames, revision FROM direction_segments"
            ).one() == (None, None, 3)
        command.downgrade(config, "0013_set_fix_keep")
        with db.engine.connect() as connection:
            columns = {c["name"] for c in inspect(connection).get_columns("direction_segments")}
            assert not {"continuation_method", "context_frames"} & columns
            assert connection.exec_driver_sql("SELECT id FROM direction_segments").all() == [("s",)]
    finally:
        db.close()


def test_baseline_matches_all_records_and_downgrades_to_empty(tmp_path):
    for module in (
        "infrastructure.records",
        "infrastructure.requests",
        "modules.library.persistence",
        "modules.library.assets",
        "modules.catalog.persistence",
        "modules.direction.persistence",
        "modules.generation.operations.persistence",
        "modules.editing.selections.persistence",
        "modules.editing.recast.persistence",
        "modules.agent.persistence",
        "modules.sets.persistence",
    ):
        import_module(f"gaiavfx.{module}")
    assert {path.name for path in (MIGRATIONS / "versions").glob("*.py")} == {
        "0001_baseline.py",
        "0002_review_backend.py",
        "0003_recast.py",
        "0004_recast_shot_layers.py",
        "0005_recast_character_reference.py",
        "0006_recast_shot_bridges.py",
        "0007_recast_appearance_links.py",
        "0008_recast_not_same.py",
        "0009_recast_edits.py",
        "0010_recast_voice.py",
        "0011_sets.py",
        "0012_sets_in_scenes.py",
        "0013_set_fix_keep.py",
        "0014_segment_continuation.py",
    }
    LibraryService(tmp_path, MIGRATIONS).create("Baseline")
    db = Database(tmp_path)
    config = Config()
    config.set_main_option("script_location", MIGRATIONS.as_posix())
    config.set_main_option("sqlalchemy.url", "sqlite:///" + db.path.as_posix().replace("%", "%%"))
    try:
        with db.engine.connect() as connection:
            assert connection.exec_driver_sql(
                "SELECT version_num FROM alembic_version"
            ).scalar() == ("0014_segment_continuation")
            assert set(inspect(connection).get_table_names()) == TABLES | {"alembic_version"}
            assert set(Base.metadata.tables) == TABLES
            context = MigrationContext.configure(connection)
            assert compare_metadata(context, Base.metadata) == []
            # Alembic's SQLite default comparator miscompares '' with the ORM's
            # empty string. Inspect all server defaults explicitly instead.
            defaults = {
                (table, column["name"]): column["default"]
                for table in TABLES
                for column in inspect(connection).get_columns(table)
                if column["default"] is not None
            }
            assert defaults == {
                ("editing_recast_shots", "layers"): "'[]'",
                ("editing_recast_shots", "bridge_ops"): "'[]'",
                ("direction_places", "description"): "''",
                ("direction_places", "sound"): "''",
                ("direction_places", "overhead"): "''",
                ("direction_places", "revision"): "'1'",
                ("direction_angles", "description"): "''",
                ("direction_angles", "revision"): "'1'",
            }
            receipt = next(
                col
                for col in inspect(connection).get_columns("direction_receipts")
                if col["name"] == "movie_id"
            )
            assert not receipt["nullable"]
            assert connection.exec_driver_sql("PRAGMA foreign_key_check").all() == []
        command.downgrade(config, "0001_baseline")
        assert "direction_cut_suitability" not in inspect(db.engine).get_table_names()
        for table, column in (
            ("generation_candidates", "prescreen"),
            ("direction_evidence", "inputs"),
            ("direction_keyframes", "inputs"),
            ("direction_keyframes", "subject_asset_id"),
        ):
            assert column not in {c["name"] for c in inspect(db.engine).get_columns(table)}
        command.upgrade(config, "head")
        with db.engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        command.downgrade(config, "base")
        assert inspect(db.engine).get_table_names() == ["alembic_version"]
        command.upgrade(config, "head")
        with db.engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    finally:
        db.close()
