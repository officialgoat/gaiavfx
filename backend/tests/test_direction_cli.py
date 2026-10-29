"""The CLI persists a complete reverse-cut approval sequence in a portable library."""

import json
import subprocess
import sys
from pathlib import Path

from test_direction_store import make_segment

from gaiavfx.direction_cli import main
from gaiavfx.infrastructure.database import Database
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.library.application import LibraryService


def test_cli_reverse_cut_round_trip(tmp_path):
    LibraryService(tmp_path, Path(__file__).parents[1] / "migrations").create("CLI")
    db = Database(tmp_path)
    try:
        with db.transaction() as session:
            segment = make_segment(DirectionService(), session)
    finally:
        db.close()

    def run(*arguments, success=True):
        result = subprocess.run(
            [
                sys.executable,
                "-m",
                "gaiavfx.direction_cli",
                "--library",
                str(tmp_path),
                *arguments,
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        assert (result.returncode == 0) is success, result.stderr
        return json.loads(result.stdout if success else result.stderr)

    place = run("place", "add", "--name", "Courtyard")
    source = run("angle", "add", "--place-id", place["id"], "--name", "Gate")
    target = run("angle", "add", "--place-id", place["id"], "--name", "Arcade")
    cut = run(
        "cut",
        "add",
        "--segment-id",
        segment["id"],
        "--expected-revision",
        str(segment["revision"]),
        "--frame",
        "36",
        "--from-angle-id",
        source["id"],
        "--to-angle-id",
        target["id"],
        "--relation",
        "reverse",
        "--crosses-barrier",
    )
    assert cut["revision"] == 1
    fresh = run("cut", "inspect", cut["id"])
    assert fresh["assessment"]["coverage"] == "new"
    assert [step["action"] for step in fresh["recommended"]["actions"]] == [
        "view_angle",
        "insert_subject",
        "view_place",
    ]
    evidence = run(
        "evidence",
        "add",
        "--angle-id",
        target["id"],
        "--kind",
        "survey_view",
        "--asset-id",
        "view-asset",
    )
    keyframe = run(
        "keyframe",
        "add",
        "--cut-id",
        cut["id"],
        "--asset-id",
        "pin-asset",
        "--evidence-id",
        evidence["id"],
    )
    assert not evidence["approved"] and not keyframe["approved"]
    pending = run("cut", "inspect", cut["id"])
    assert [step["action"] for step in pending["recommended"]["actions"][:2]] == [
        "review_view",
        "review_keyframe",
    ]
    run("evidence", "approve", evidence["id"])
    run("keyframe", "approve", keyframe["id"])
    ready = run("cut", "inspect", cut["id"])
    assert ready["assessment"]["coverage"] == "established"
    assert ready["recommended"]["ready"] is True
    updated = run("cut", "set", cut["id"], "--expected-revision", "1", "--frame", "48")
    assert updated["revision"] == 2 and updated["frame"] == 48
    assert (
        "stale"
        in run(
            "cut",
            "set",
            cut["id"],
            "--expected-revision",
            "1",
            "--frame",
            "72",
            success=False,
        )["detail"]
    )


def test_cli_authored_commands_and_undo(tmp_path, capsys):
    def cli(*arguments, ok=True):
        result = main(["--library", str(tmp_path), *map(str, arguments)])
        output = capsys.readouterr()
        assert result == (0 if ok else 1), output.err
        return json.loads(output.out if ok else output.err)

    cli("library", "create", "--name", "Authoring")
    movie = cli("movie", "create", "--title", "Courtyard")
    mid = movie["id"]
    subject = cli(
        "subject",
        "add",
        "--movie-id",
        mid,
        "--expected-revision",
        1,
        "--id",
        "arin",
        "--name",
        "Arin",
        "--description",
        "A walker.",
        "--keeps",
        "coat",
        "bag",
        "--identity-asset",
        "identity",
    )
    assert subject["receipt"]["target_id"] == mid
    document = {
        "subjects": [
            {
                "id": "arin",
                "name": "Arin",
                "description": "A walker.",
                "keeps": ["coat", "bag"],
                "identity_asset": "identity",
            }
        ],
        "place": {"noun": "courtyard", "description": "A courtyard.", "landmarks": ""},
        "shots": [{"description": "Walk.", "screen_direction": "", "sound": ""}],
        "cut_frames": [],
        "delivered_frames": 102,
        "continues": True,
    }
    file = tmp_path / "authored.json"
    file.write_text(json.dumps(document), encoding="utf-8")
    first = cli("segment", "add", "--movie-id", mid, "--expected-revision", 2, "--file", file)
    second = cli(
        "segment", "add", "--movie-id", mid, "--expected-revision", 3, "--file", file, "--at", 0
    )
    assert cli("movie", "show", mid)["segments"][0]["id"] == second["id"]
    moved = cli("segment", "move", second["id"], "--expected-revision", 4, "--ordinal", 1)
    assert moved["ordinal"] == 1
    changed = cli(
        "segment",
        "set",
        first["id"],
        "--expected-revision",
        1,
        "--delivered-frames",
        126,
        "--no-continues",
    )
    assert changed["continues"] is False
    content = tmp_path / "content.json"
    content.write_text(json.dumps({"subjects": ["arin"], "place": document["place"]}))
    cli(
        "segment", "set", first["id"], "--expected-revision", 2, "--content", content, "--continues"
    )
    phase_file = tmp_path / "phase.json"
    phase_file.write_text(json.dumps(document["shots"][0]))
    phase = cli(
        "phase",
        "add",
        "--segment-id",
        first["id"],
        "--expected-revision",
        3,
        "--start-frame",
        36,
        "--content",
        phase_file,
    )
    changed = cli(
        "phase",
        "set",
        phase["id"],
        "--expected-revision",
        1,
        "--start-frame",
        48,
        "--content",
        phase_file,
    )
    assembled = cli("segment", "assembled", first["id"])
    assert assembled["shots"] == document["shots"]
    assert assembled["phases"] == [{"start_frame": 48, **document["shots"][0]}]
    cli("undo", changed["receipt"]["id"])
    removed = cli("phase", "remove", phase["id"], "--expected-revision", 4)
    assert removed["removed"]
    assert cli("segment", "assembled", first["id"])["delivered_frames"] == 126
    cli("segment", "remove", second["id"], "--expected-revision", 5)
    cli("segment", "remove", first["id"], "--expected-revision", 6)
    removed_subject = cli("subject", "remove", "arin", "--expected-revision", 7)
    assert removed_subject["removed"]
    assert cli("subject", "list", mid) == []
    cli("undo", removed_subject["receipt"]["id"])
    assert cli("subject", "list", mid)[0]["id"] == "arin"
    receipts = cli("receipts", mid, "--limit", 2)
    assert len(receipts) == 2 and receipts[0]["command"] == "undo"
    cli("subject", "set", "arin", "--expected-revision", 1, "--keeps")
    assert cli("subject", "list", mid)[0]["keeps"] == []
