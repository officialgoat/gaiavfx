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
