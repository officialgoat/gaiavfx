import json
import shutil
import sqlite3
from contextlib import closing
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from gaiavfx.api.app import create_app
from gaiavfx.modules.library.application import LibraryError, LibraryService

MIGRATIONS = Path(__file__).parents[1] / "migrations"
TOKEN = "test-session-secret"
AUTH = {"Authorization": f"Bearer {TOKEN}"}


def client(root):
    return TestClient(
        create_app(root, MIGRATIONS, TOKEN, browser_origins=("http://localhost:5173",)),
        base_url="http://localhost",
    )


def test_create_reopen_and_relocate(tmp_path):
    root = tmp_path / "Original library"
    with client(root) as api:
        assert api.get("/api/v1/library", headers=AUTH).json() == {
            "library": None,
            "archived": None,
        }
        created = api.post("/api/v1/library", headers=AUTH, json={"name": "  My films  "})
        assert created.status_code == 201
        assert created.json()["name"] == "My films"
        assert (
            api.post("/api/v1/library", headers=AUTH, json={"name": "Replace"}).status_code == 409
        )
    relocated = tmp_path / "Films – déplacé"
    shutil.copytree(root, relocated)
    with client(relocated) as api:
        assert api.get("/api/v1/library", headers=AUTH).json()["library"] == created.json()


def test_auth_origin_and_bootstrap(tmp_path):
    with client(tmp_path / "library") as api:
        assert api.get("/api/v1/library").status_code == 401
        assert (
            api.get(
                "/api/v1/library", headers={**AUTH, "Origin": "https://untrusted.test"}
            ).status_code
            == 403
        )
        assert api.post("/api/v1/session").status_code == 403
        assert (
            api.post(
                "/api/v1/session",
                headers={"Origin": "http://localhost:5173", "X-GAIAVFX-Bootstrap": "1"},
            ).json()["token"]
            == TOKEN
        )
        assert (
            api.post(
                "/api/v1/session",
                headers={"Origin": "https://untrusted.test", "X-GAIAVFX-Bootstrap": "1"},
            ).status_code
            == 403
        )
        assert (
            api.get("/api/v1/health", headers={**AUTH, "Host": "evil.example"}).status_code == 400
        )


def test_unsupported_schema_preserved(tmp_path):
    library = LibraryService(tmp_path, MIGRATIONS)
    library.create("First")
    with sqlite3.connect(library.database) as connection:
        connection.execute("UPDATE alembic_version SET version_num='future'")
    before = library.database.read_bytes()
    with pytest.raises(LibraryError, match="unsupported schema"):
        library.get()
    assert library.database.read_bytes() == before


def test_failed_creation_can_retry_without_published_database(tmp_path, monkeypatch):
    from gaiavfx.modules.library import application

    library = LibraryService(tmp_path, MIGRATIONS)
    upgrade = application.command.upgrade
    monkeypatch.setattr(
        application.command,
        "upgrade",
        lambda *_args: (_ for _ in ()).throw(RuntimeError("disk error")),
    )
    with pytest.raises(RuntimeError, match="disk error"):
        library.create("Failed")
    assert not library.database.exists()
    monkeypatch.setattr(application.command, "upgrade", upgrade)
    assert library.create("Retry").name == "Retry"


def test_one_process_per_library(tmp_path):
    from filelock import FileLock

    tmp_path.mkdir(exist_ok=True)
    with (
        FileLock(str(tmp_path / ".service.lock")),
        pytest.raises(RuntimeError, match="already open"),
        client(tmp_path),
    ):
        pass


def test_blank_name_does_not_publish(tmp_path):
    with client(tmp_path) as api:
        assert api.post("/api/v1/library", headers=AUTH, json={"name": "   "}).status_code == 409
        assert not (tmp_path / "library.sqlite3").exists()


def test_concurrent_creation_publishes_exactly_one_library(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    library = LibraryService(tmp_path, MIGRATIONS)
    barrier = Barrier(2)

    def create(name):
        barrier.wait()
        try:
            return library.create(name)
        except LibraryError:
            return None

    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(create, ("First", "Second")))
    successful = [value for value in results if value is not None]
    assert len(successful) == 1
    assert library.get() == successful[0]


def set_foreign_revision(root):
    with closing(sqlite3.connect(root / "library.sqlite3")) as connection, connection:
        connection.execute("UPDATE alembic_version SET version_num='0022_agent_and_place_text'")


def files_under(root):
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file() and path.name != ".service.lock"
    }


def test_api_archives_every_entry_and_creates_a_fresh_library(tmp_path):
    from gaiavfx.infrastructure.telemetry.store import Store

    root = tmp_path / "Films and pictures"
    with client(root) as api:
        old = api.post("/api/v1/library", headers=AUTH, json={"name": "Old"}).json()
    (root / "originals" / "keep.png").write_bytes(b"original image")
    (root / "cache" / "preview").write_bytes(b"cached preview")
    (root / ".hidden").write_bytes(b"hidden file")
    (root / "credentials").mkdir()
    (root / "credentials" / "assistant.json.enc").write_bytes(b"encrypted credentials")
    # Composition must neither open this journal nor carry its settings into the new library.
    Store(root).write_config({"enabled": True, "destination_id": "old-project"}, None)
    set_foreign_revision(root)
    before = files_under(root)
    directories = {path.relative_to(root) for path in root.rglob("*") if path.is_dir()}
    reopened = client(root)
    assert files_under(root) == before
    with reopened as api:
        state = api.get("/api/v1/library", headers=AUTH).json()
        assert state["library"] is None
        archived = state["archived"]
        archive = root.with_name(root.name + ".pre-rebuild-2026-09")
        assert archived["path"] == archive.as_posix()
        assert archived["revision"] == "0022_agent_and_place_text"
        assert datetime.fromisoformat(archived["archived_at"]).tzinfo is not None
        assert {path.name for path in root.iterdir()} == {".service.lock"}
        assert all((archive / path).is_dir() for path in directories)
        saved = files_under(archive)
        note = json.loads(saved.pop("ARCHIVED.json"))
        assert saved == before
        assert note == {
            "revision": archived["revision"],
            "archived_at": archived["archived_at"],
            "opened_by": "gaiavfx 0.1.0 (rebuild)",
            "note": "Open with the build tagged pre-rebuild-2026-09.",
        }
        assert not api.app.state.workspace.telemetry.enabled
        fresh = api.post("/api/v1/library", headers=AUTH, json={"name": "Fresh"})
        assert fresh.status_code == 201
        assert fresh.json()["id"] != old["id"]
    with client(root) as api:
        assert api.get("/api/v1/library", headers=AUTH).json() == {
            "library": fresh.json(),
            "archived": None,
        }
    set_foreign_revision(root)
    with client(root) as api:
        archived_again = api.get("/api/v1/library", headers=AUTH).json()["archived"]
        assert archived_again["path"] == archive.with_name(archive.name + "-2").as_posix()
    assert files_under(archive) == {
        **before,
        "ARCHIVED.json": (archive / "ARCHIVED.json").read_bytes(),
    }


def test_archive_does_not_touch_missing_or_current_database(tmp_path):
    library = LibraryService(tmp_path, MIGRATIONS)
    (tmp_path / "unrelated.txt").write_text("keep", encoding="utf-8")
    assert library.archive_foreign() is None
    assert files_under(tmp_path) == {"unrelated.txt": b"keep"}
    library.create("Current")
    before = files_under(tmp_path)
    assert library.archive_foreign() is None
    assert files_under(tmp_path) == before


def test_archive_leaves_known_older_revision_for_migration(tmp_path):
    root = tmp_path / "library"
    LibraryService(root, MIGRATIONS).create("Known")
    migrations = tmp_path / "migrations"
    shutil.copytree(MIGRATIONS, migrations)
    (migrations / "versions" / "0004_future.py").write_text(
        "revision = '0004_future'\ndown_revision = '0003_recast'\n"
        "def upgrade(): pass\ndef downgrade(): pass\n",
        encoding="utf-8",
    )
    before = files_under(root)
    assert LibraryService(root, migrations).archive_foreign() is None
    assert files_under(root) == before


@pytest.mark.parametrize("version_table", [False, True])
def test_archive_refuses_unrecognized_database(tmp_path, version_table):
    library = LibraryService(tmp_path, MIGRATIONS)
    with closing(sqlite3.connect(library.database)) as connection, connection:
        if version_table:
            connection.execute("CREATE TABLE alembic_version (version_num TEXT)")
        else:
            connection.execute("CREATE TABLE unrelated (id TEXT)")
    before = files_under(tmp_path)
    with pytest.raises(LibraryError, match="unrecognized database"):
        library.archive_foreign()
    assert files_under(tmp_path) == before


@pytest.mark.parametrize("failure", ["move", "note"])
def test_archive_failure_restores_all_original_contents(tmp_path, monkeypatch, failure):
    from gaiavfx.modules.library import application

    root = tmp_path / "library"
    library = LibraryService(root, MIGRATIONS)
    library.create("Original")
    (root / "originals" / "keep").write_bytes(b"keep")
    (root / ".service.lock").touch()
    set_foreign_revision(root)
    if failure == "note":
        # Even an existing file with our note's name must never be overwritten.
        (root / "ARCHIVED.json").write_bytes(b"user file")
    before = files_under(root)
    replace = application.os.replace
    moves = []

    def fail_second_move(source, destination):
        if Path(source).parent == root:
            moves.append(source)
            if len(moves) == 2:
                raise PermissionError("locked file")
        return replace(source, destination)

    if failure == "move":
        monkeypatch.setattr(application.os, "replace", fail_second_move)
    with pytest.raises(OSError):
        library.archive_foreign()
    assert files_under(root) == before
    assert not root.with_name(root.name + ".pre-rebuild-2026-09").exists()


def test_cli_refuses_foreign_library_without_archiving(tmp_path, capsys):
    from gaiavfx.direction_cli import main

    library = LibraryService(tmp_path, MIGRATIONS)
    library.create("Old")
    set_foreign_revision(tmp_path)
    before = files_under(tmp_path)
    assert main(["--library", str(tmp_path), "place", "add", "--name", "Unused"]) == 1
    assert "unsupported schema" in capsys.readouterr().err
    assert files_under(tmp_path) == before
