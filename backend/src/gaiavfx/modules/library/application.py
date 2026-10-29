"""Local library lifecycle. This module owns its database records."""

from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from alembic.script.revision import ResolutionError
from sqlalchemy import create_engine, event, inspect, select
from sqlalchemy.orm import Session

from .media_application import MediaLibrary, PreparedAsset
from .persistence import LibraryRecord

__all__ = [
    "ArchivedLibrary",
    "LibraryError",
    "LibraryInfo",
    "LibraryService",
    "MediaLibrary",
    "PreparedAsset",
]


class LibraryError(Exception):
    pass


@dataclass(frozen=True)
class LibraryInfo:
    id: str
    name: str
    created_at: str


@dataclass(frozen=True)
class ArchivedLibrary:
    path: str
    revision: str
    archived_at: str


class LibraryService:
    def __init__(self, root: Path, migrations: Path):
        self.root = root.resolve()
        self.migrations = migrations
        self.database = self.root / "library.sqlite3"
        self._creation_lock = Lock()

    def _engine(self, path: Path | None = None):
        engine = create_engine("sqlite:///" + (path or self.database).as_posix())

        @event.listens_for(engine, "connect")
        def configure(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=3000")

        return engine

    def get(self) -> LibraryInfo | None:
        if not self.database.exists():
            return None
        engine = self._engine()
        try:
            with engine.connect() as connection:
                if "alembic_version" not in inspect(connection).get_table_names():
                    raise LibraryError("This folder contains an unrecognized database.")
                revision = connection.exec_driver_sql(
                    "SELECT version_num FROM alembic_version"
                ).scalar()
                config = Config()
                config.set_main_option("script_location", self.migrations.as_posix())
                if revision != ScriptDirectory.from_config(config).get_current_head():
                    raise LibraryError(
                        "This library uses an unsupported schema. Open it with its matching app version."
                    )
            with Session(engine) as session:
                row = session.scalar(select(LibraryRecord))
                if row is None:
                    raise LibraryError("The library metadata is missing.")
                return LibraryInfo(row.id, row.name, row.created_at)
        finally:
            engine.dispose()

    def create(self, name: str) -> LibraryInfo:
        with self._creation_lock:
            return self._create(name)

    def archive_foreign(self) -> ArchivedLibrary | None:
        """Archive an old build's library while the caller holds the service lock.

        Close every database handle before moving contents: the root and its lock
        stay in place for the desktop shell. Known revisions belong to migrate().
        """
        with self._creation_lock:
            if not self.database.exists():
                return None
            engine = self._engine()
            try:
                with engine.connect() as connection:
                    if "alembic_version" not in inspect(connection).get_table_names():
                        raise LibraryError("This folder contains an unrecognized database.")
                    revisions = list(
                        connection.exec_driver_sql(
                            "SELECT version_num FROM alembic_version"
                        ).scalars()
                    )
                    if len(revisions) != 1 or not revisions[0]:
                        raise LibraryError("This folder contains an unrecognized database.")
                    revision = revisions[0]
            finally:
                engine.dispose()
            config = Config()
            config.set_main_option("script_location", self.migrations.as_posix())
            scripts = ScriptDirectory.from_config(config)
            try:
                scripts.revision_map.get_revision(revision)
            except ResolutionError:
                pass
            else:
                return None

            archive = self.root.with_name(self.root.name + ".pre-rebuild-2026-09")
            suffix = 1
            while True:
                try:
                    archive.mkdir()
                    break
                except FileExistsError:
                    suffix += 1
                    archive = self.root.with_name(f"{self.root.name}.pre-rebuild-2026-09-{suffix}")
            result = ArchivedLibrary(archive.as_posix(), revision, datetime.now(UTC).isoformat())
            moved = []
            note = archive / "ARCHIVED.json"
            wrote_note = False
            try:
                for entry in self.root.iterdir():
                    if entry.name != ".service.lock":
                        os.replace(entry, archive / entry.name)
                        moved.append(entry.name)
                # Never overwrite an existing user file, including an archive note.
                with note.open("x", encoding="utf-8") as stream:
                    wrote_note = True
                    json.dump(
                        {
                            "revision": result.revision,
                            "archived_at": result.archived_at,
                            "opened_by": "gaiavfx 0.1.0 (rebuild)",
                            "note": "Open with the build tagged pre-rebuild-2026-09.",
                        },
                        stream,
                        indent=2,
                    )
                    stream.write("\n")
            except Exception:
                if wrote_note:
                    note.unlink()
                for name in reversed(moved):
                    os.replace(archive / name, self.root / name)
                archive.rmdir()
                raise
            return result

    def _create(self, name: str) -> LibraryInfo:
        name = name.strip()
        if not name or len(name) > 120:
            raise LibraryError("Give the library a name between 1 and 120 characters.")
        if self.database.exists():
            raise LibraryError("A library already exists here.")
        self.root.mkdir(parents=True, exist_ok=True)
        staging = self.root / ".library-create.sqlite3"
        # A previous interrupted creation is disposable; a published library is never replaced.
        staging.unlink(missing_ok=True)
        config = Config()
        config.set_main_option("script_location", self.migrations.as_posix())
        config.set_main_option(
            "sqlalchemy.url", "sqlite:///" + staging.as_posix().replace("%", "%%")
        )
        try:
            command.upgrade(config, "head")
            engine = self._engine(staging)
            result = LibraryInfo(str(uuid.uuid4()), name, datetime.now(UTC).isoformat())
            try:
                with Session(engine) as session:
                    session.add(
                        LibraryRecord(id=result.id, name=name, created_at=result.created_at)
                    )
                    session.commit()
            finally:
                engine.dispose()
            for folder in ("originals", "generated", "continuations", "exports", "cache"):
                (self.root / folder).mkdir(exist_ok=True)
            # Windows rename fails if the destination exists. The single-process lock is also held.
            staging.rename(self.database)
            return result
        except Exception:
            staging.unlink(missing_ok=True)
            raise

    def backup(self, target: Path) -> None:
        """Use SQLite's backup API before future schema upgrades."""
        import sqlite3

        with sqlite3.connect(self.database) as source, sqlite3.connect(target) as destination:
            source.backup(destination)
