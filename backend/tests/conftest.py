"""Refuse to test a gaiavfx package imported from another checkout.

The root `.venv` holds an editable install pinned to the main checkout, so a git
worktree that reuses it would silently test the main checkout's code.
"""

from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / "src"


def pytest_configure(config):
    import gaiavfx

    imported = Path(gaiavfx.__file__).resolve()
    if not imported.is_relative_to(SOURCE):
        raise pytest.UsageError(
            f"gaiavfx was imported from {imported.parent}, not from {SOURCE}. "
            "Install this checkout in its own virtual environment or set "
            f"PYTHONPATH={SOURCE}."
        )


@pytest.fixture(autouse=True)
def isolated_setup_file(tmp_path, monkeypatch):
    # The developer's own .runtime/setup.json must not seed the libraries tests create.
    monkeypatch.setenv("GAIAVFX_SETUP_FILE", str(tmp_path / "setup.json"))
    # Nor may a test wait on the machine's GPU lease, which a running app or another test process holds.
    monkeypatch.setenv("GAIAVFX_GPU_LOCK", str(tmp_path / "gpu.lock"))


@pytest.fixture
def store(tmp_path):
    """A migrated library with real Direction, Generation and Library rows; a file that needs another service
    defines its own `store`."""
    from gaiavfx.infrastructure.database import Database
    from gaiavfx.modules.direction.service import DirectionService
    from gaiavfx.modules.library.application import LibraryService

    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("Tests")
    db = Database(tmp_path)
    yield db, DirectionService()
    db.close()


@pytest.fixture
def media(monkeypatch):
    """Mock byte descriptors only; planning, Direction and Generation stores stay real."""
    from gaiavfx.modules.library.media_application import MediaLibrary

    monkeypatch.setattr(MediaLibrary, "asset_info", lambda session, asset: {"sha256": asset})
    monkeypatch.setattr(
        MediaLibrary,
        "artifact",
        lambda session, artifact: {
            "id": artifact,
            "sha256": artifact,
            "kind": "h3_context",
        },
    )


@pytest.fixture
def sheets(monkeypatch):
    """Stand in for the sheet media lookups alone: each place's current sheet is `sheet`, fresh, with the given
    panels. Assembly, binding judgments, lowering and H3's capability checks run for real."""
    from gaiavfx.application import direction_plans, shot_references
    from gaiavfx.application.direction_reading import DIRECTION

    def use(panels):
        for module in (direction_plans, shot_references):
            monkeypatch.setattr(module, "current_sheet", lambda *args: "sheet")
        monkeypatch.setattr(shot_references, "latest_sheet_asset", lambda *args: "sheet")

        def recipe(session, asset_id, place_id):
            # Every approved view of the place, as a fresh assembly would hold them.
            tiles = [
                evidence["id"]
                for angle in DIRECTION.angles(session, place_id)
                for evidence in DIRECTION.evidence(session, angle["id"])
                if evidence["approved"]
            ]
            return {"tiles": tiles, "panels": panels, "blank_panels": 0, "origin": "survey"}

        monkeypatch.setattr(DIRECTION, "sheet_reader", recipe)

    return use
