"""Every retained v11 construct through the authored import and content assembly path."""

import json
import os
from dataclasses import replace
from pathlib import Path

import pytest
from prompt_invariants import assert_prompt_invariants
from wording_fixtures import FIXTURES, GOLDEN, GRID, compile_fixture

from gaiavfx.application.authored import import_segment
from gaiavfx.infrastructure.database import Database
from gaiavfx.integrations.minimax_h3.graph import build_graph
from gaiavfx.integrations.minimax_h3.prompt import compile_prompt, wording
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.library.application import LibraryService

MIGRATIONS = Path(__file__).parents[1] / "migrations"


def snapshot_matches(path, text):
    if os.environ.get("UPDATE_GOLDEN") == "1":
        path.write_bytes(text.encode("utf-8"))
    assert path.exists(), f"No snapshot at {path}; run with UPDATE_GOLDEN=1."
    assert path.read_bytes() == text.encode("utf-8")


def test_all_retained_inputs_and_snapshots_are_accounted_for():
    assert {path.stem for path in (GOLDEN / "inputs").glob("*.json")} == set(FIXTURES)
    # Missing files fail in snapshot_matches; allow the explicit update run to create them.
    assert {path.stem for path in GOLDEN.glob("*.txt")} <= set(FIXTURES)


@pytest.mark.parametrize("name", FIXTURES)
def test_retained_golden_and_invariants(name):
    fixture, segment, _, compiled = compile_fixture(GOLDEN / "inputs" / f"{name}.json")
    assert compiled.window_frames == GRID.window_for(
        segment.delivered_frames, fixture["context_frames"]
    )
    assert_prompt_invariants(compiled.text, segment, GRID, fixture["context_frames"])
    snapshot_matches(GOLDEN / f"{name}.txt", compiled.text)


def test_missing_snapshot_fails_without_update(tmp_path, monkeypatch):
    monkeypatch.delenv("UPDATE_GOLDEN", raising=False)
    with pytest.raises(AssertionError, match="No snapshot"):
        snapshot_matches(tmp_path / "missing.txt", "prompt")


@pytest.mark.parametrize("name", ["one-phase", "single-continuation"])
def test_motion_context_and_trim_only_when_continuing(name):
    _, segment, _, compiled = compile_fixture(GOLDEN / "inputs" / f"{name}.json")
    graph = build_graph(
        compiled,
        files={asset: f"{asset}.png" for asset in compiled.slots},
        seed=42,
        width=768,
        height=448,
        fps=GRID.fps,
        context_latent="context.safetensors" if segment.continues else None,
        output_prefix="test/golden",
    )
    for key in ("motion_context", "load_context", "trim"):
        assert (key in graph) == segment.continues


@pytest.mark.parametrize("name", ["dialogue", "two-phases"])
def test_import_and_persisted_assembly_equal_the_pure_assembly(tmp_path, name):
    fixture, segment, pictures, _ = compile_fixture(GOLDEN / "inputs" / f"{name}.json")
    authored_file = tmp_path / "segment.json"
    authored_file.write_text(json.dumps(fixture["segment"]), encoding="utf-8")
    LibraryService(tmp_path, MIGRATIONS).create("Golden round trip")
    database = Database(tmp_path)
    direction = DirectionService()
    try:
        with database.transaction() as session:
            movie = direction.create_movie(session, "Golden")
            imported = import_segment(session, movie["id"], movie["revision"], authored_file)
            sid = imported["id"]
            if segment.cut_frames:
                place = direction.create_place(session, "Pier")
                angle = direction.add_angle(session, place["id"], "Window")
                target_angle = direction.add_angle(session, place["id"], "Closer view")
                for frame in segment.cut_frames:
                    direction.create_cut(
                        session,
                        segment_id=sid,
                        expected_revision=direction.read_segment(session, sid)["revision"],
                        frame=frame,
                        from_angle_id=angle["id"],
                        to_angle_id=target_angle["id"],
                    )
        with database.transaction() as session:
            assert direction.assemble_segment(session, sid) == (segment, pictures)
    finally:
        database.close()


def test_invariant_lint_catches_broken_wording():
    fixture, segment, pictures, _ = compile_fixture(GOLDEN / "inputs" / "lab-courtyard-rich.json")
    words = wording()
    broken = replace(words, lines={**words.lines, "sound_clause": "Sound:  {sound}"})
    compiled = compile_prompt(segment, pictures, (), GRID, fixture["context_frames"], broken)
    with pytest.raises(AssertionError, match="Doubled space"):
        assert_prompt_invariants(compiled.text, segment, GRID, fixture["context_frames"])
