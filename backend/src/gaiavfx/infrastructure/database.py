from __future__ import annotations

import contextlib
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from alembic import command
from alembic.config import Config
from alembic.script import ScriptDirectory
from sqlalchemy import create_engine, event
from sqlalchemy.orm import DeclarativeBase, Session

from gaiavfx.shared.errors import ApplicationError


class Base(DeclarativeBase):
    pass


_clock = threading.Lock()
_last_timestamp: datetime | None = None


def timestamp() -> str:
    """A strictly increasing UTC time for this process.

    Records are ordered by these values. The Windows clock in Python 3.12 advances in
    steps of about 15 ms, so equal readings would leave the order of commands written
    in one burst to their random ids.
    """
    global _last_timestamp
    with _clock:
        now = datetime.now(UTC)
        if _last_timestamp is not None and now <= _last_timestamp:
            now = _last_timestamp + timedelta(microseconds=1)
        _last_timestamp = now
    return now.isoformat(timespec="microseconds")


def new_id() -> str:
    return str(uuid.uuid4())


class Database:
    def __init__(self, root: Path):
        self.root = root.resolve()
        self.path = self.root / "library.sqlite3"
        self.engine = create_engine("sqlite:///" + self.path.as_posix())

        @event.listens_for(self.engine, "connect")
        def configure(connection, _record):
            connection.execute("PRAGMA foreign_keys=ON")
            connection.execute("PRAGMA busy_timeout=5000")

    @contextlib.contextmanager
    def transaction(self):
        if not self.path.is_file():
            raise ApplicationError("Create a library before using this workspace.")
        with Session(self.engine, expire_on_commit=False) as session, session.begin():
            yield session

    def migrate(self, migrations: Path) -> None:
        if not self.path.exists():
            return
        config = Config()
        config.set_main_option("script_location", migrations.as_posix())
        scripts = ScriptDirectory.from_config(config)
        with contextlib.closing(sqlite3.connect(self.path)) as original:
            try:
                revision = original.execute("SELECT version_num FROM alembic_version").fetchone()[0]
            except (sqlite3.Error, TypeError) as exc:
                raise RuntimeError("This folder contains an unrecognized database.") from exc
            try:
                scripts.get_revision(revision)
            except Exception as exc:
                raise RuntimeError("This library uses an unsupported schema.") from exc
            if revision == scripts.get_current_head():
                return
            backup_dir = self.root / "backups"
            backup_dir.mkdir(exist_ok=True)
            backup = backup_dir / f"{datetime.now(UTC):%Y%m%dT%H%M%S}-{new_id()}.sqlite3"
            with contextlib.closing(sqlite3.connect(backup)) as destination:
                original.backup(destination)
            staged = self.root / ".migration.sqlite3"
            staged.unlink(missing_ok=True)
            with contextlib.closing(sqlite3.connect(staged)) as destination:
                original.backup(destination)
        try:
            config.set_main_option(
                "sqlalchemy.url", "sqlite:///" + staged.as_posix().replace("%", "%%")
            )
            command.upgrade(config, "head")
            with contextlib.closing(sqlite3.connect(staged)) as connection:
                if connection.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
                    raise RuntimeError("The upgraded library failed its integrity check.")
            self.engine.dispose()
            os.replace(staged, self.path)
        finally:
            staged.unlink(missing_ok=True)

    def close(self):
        self.engine.dispose()
