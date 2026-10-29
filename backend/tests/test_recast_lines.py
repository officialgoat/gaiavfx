"""ADR 0016 §1-§3 and §8: line decisions on the real Editing store (A9-A15).

The `voice` fixture extends the recast test world with a fake dialogue analysis module (as the world fakes
film_analysis): the shared operation is a real Generation operation found by its content key, settled by the world,
and its report is a six-segment fixture whose embeddings make the two characters separable.
"""

import json
import math
import sys
from importlib import import_module
from types import ModuleType, SimpleNamespace

import pytest
from test_recast_service import MIGRATIONS, edit
from test_recast_service import recast_template as recast_template  # noqa: PLC0414 - shared fixture
from test_recast_service import world as world  # noqa: PLC0414 - shared fixture

from gaiavfx.api.recast import LinesDTO, RecastDTO
from gaiavfx.application import recast, recast_edits, recast_lines
from gaiavfx.application import recast_projection as p
from gaiavfx.modules.direction import dialogue as rule
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.assets import AssetRecord
from gaiavfx.shared.errors import ApplicationError

LATER = recast_edits.LATER
REANALYSED = recast_edits.REANALYSED
# (start, end, text, speaker): A is seen in shots 1-2 (0-6.08 s), B in shots 3-4 (6.08-12.17 s).
LINES = [
    (0.20, 1.00, "Hello there.", "A"),
    (1.50, 2.40, "How are you today?", "A"),
    (3.30, 4.20, "I am fine.", "A"),
    (4.60, 5.60, "Let us go to the market.", "A"),
    (6.30, 7.30, "Not now.", "B"),
    (9.30, 10.30, "Later, then.", "B"),
]
KEYS = [f"{i:05d}-{round(start * 1000)}" for i, (start, *_rest) in enumerate(LINES)]


def embedding(speaker, index):
    vector = [0.01 * math.sin(index * 7 + d) for d in range(192)]
    vector[0 if speaker == "A" else 1] = 1.0
    return vector


def fixture_report():
    db = [-80.0] * 1217
    segments, speakers = [], {}
    for index, (start, end, text, speaker) in enumerate(LINES):
        words = text.split()
        step = (end - start) / len(words)
        segments.append(
            {
                "key": KEYS[index],
                "index": index,
                "start": start,
                "end": end,
                "text": text,
                "words": [
                    {
                        "start": round(start + k * step, 3),
                        "end": round(start + (k + 1) * step, 3),
                        "word": word,
                    }
                    for k, word in enumerate(words)
                ],
            }
        )
        speakers[KEYS[index]] = {"ecapa": embedding(speaker, index), "median_hz": 100.0 + index}
        db[round(start * 100) : round(end * 100)] = [-20.0] * (
            round(end * 100) - round(start * 100)
        )
    return {
        "version": 1,
        "sample_rate": 48000,
        "offset_samples": 0,
        "samples": 584_000,
        "channels": 2,
        "segments": segments,
        "speakers": speakers,
        "whisper_spans": [[9.2, 10.4]],
        "envelope": {"hop": 480, "db": db},
    }


@pytest.fixture
def voice(world, monkeypatch):
    real = import_module("gaiavfx.application.dialogue_analysis")
    fake = ModuleType(real.__name__)
    for name in ("NAME", "DIALOGUE_VERSION", "SAMPLE_RATE", "STEPS", "latest", "audio_gap"):
        setattr(fake, name, getattr(real, name))
    fake.dialogue_view = real.dialogue_view
    report = fixture_report()
    calls = []

    def start_dialogue_analysis(session, library, asset_id, *, retry_failed=False):
        calls.append((asset_id, retry_failed))
        asset = library.get(session, asset_id)
        gap = real.audio_gap(asset)
        if gap:
            raise ApplicationError(gap["message"], 422)
        existing, attempt = real.latest(session, asset)
        if existing:
            settled = existing["state"] in OPERATION_TERMINAL and existing["state"] != "succeeded"
            if not (retry_failed and settled):
                return existing | {"_created": False}
            attempt += 1
        request = {"source_asset": asset_id, "version": real.DIALOGUE_VERSION, "attempt": attempt}
        return world.create_op(
            session, real.NAME, real._key(asset, attempt), request, steps=list(real.STEPS)
        )

    def dialogue_report(session, library, operation_id):
        operation = world.ops.operation(session, operation_id)
        return report if operation["state"] == "succeeded" else None

    fake.start_dialogue_analysis = start_dialogue_analysis
    fake.dialogue_report = dialogue_report
    monkeypatch.setitem(sys.modules, real.__name__, fake)
    return SimpleNamespace(real=real, fake=fake, report=report, calls=calls)


def checked(state):
    RecastDTO.model_validate(state)
    return state


def invoke(world, function, state, *args, **kwargs):
    return checked(
        world.invoke(function, state["id"], *args, expected_revision=state["revision"], **kwargs)
    )


def lines(world, state):
    view = world.invoke(recast_lines.lines_state, state["id"])
    LinesDTO.model_validate(view)
    return view


def line(world, state, key):
    return next(item for item in lines(world, state)["lines"] if item["segment_key"] == key)


def settle_dialogue(world, state):
    with world.db.transaction() as session:
        world.settle(session, state["dialogue"]["operation_id"])
    return checked(world.invoke(recast.recast_state, state["id"]))


def cast(world):
    """A recast whose film shows A in shots 1-2 and B in shots 3-4."""
    state = world.create()
    state = edit(world, state, recast.move_tracks, track_ids=["t3", "t4"], to="new")
    a, b = (c["id"] for c in state["characters"])
    state = edit(world, state, recast.patch_character, a, label="Mara")
    state = edit(world, state, recast.patch_character, b, label="Tom")
    return state, a, b


def analysed(world):
    state, a, b = cast(world)
    state = invoke(world, recast_lines.start_dialogue_analysis, state)
    return settle_dialogue(world, state), a, b


def rows(world, state):
    with world.db.transaction() as session:
        return {r["segment_key"]: r for r in p.store.load(session, state["id"])["lines"]}


def entry(world, edit_id):
    with world.db.transaction() as session:
        return p.store.edit(session, edit_id)


def undo(world, state, item):
    return invoke(world, recast_edits.undo_edit, state, item["id"])


def refused(world, state, function, *args, **kwargs):
    with pytest.raises(ApplicationError) as error:
        world.invoke(function, state["id"], *args, expected_revision=state["revision"], **kwargs)
    return error.value


def dialogue_operations(world):
    with world.db.transaction() as session:
        return OperationService().recent(session, kind="dialogue_analysis")


# --- A9 never automatic ---------------------------------------------------------------------------------------------


def test_only_the_human_command_starts_the_shared_analysis(world):
    state = world.ready()
    world.invoke(recast.feed)
    state = checked(world.invoke(recast.recast_state, state["id"]))
    state = edit(world, state, recast.plan_recast, range=None)
    world.invoke(recast.list_recasts)
    assert lines(world, state)["lines"] == []
    assert dialogue_operations(world) == []
    assert state["dialogue"] == {
        "operation_id": None,
        "state": "not_started",
        "stage": None,
        "done": 0,
        "total": 0,
        "error": None,
        "attempt": 1,
        "version": 1,
        "sample_rate": 48000,
        "reason": None,
        "gap": None,
    }
    assert state["lines"] == {"segments": 0, "proposed": 0, "confirmed": 0, "rejected": 0}
    state = invoke(world, recast_lines.start_dialogue_analysis, state)
    [operation] = dialogue_operations(world)
    assert state["dialogue"]["operation_id"] == operation["id"]
    assert state["dialogue"]["state"] == "running" and state["dialogue"]["total"] == 6
    assert [j["step"] for j in operation["jobs"]] == [
        "dialogue_audio",
        "analysis.separate",
        "analysis.transcribe",
    ]
    # Starting again finds it; a second recast of the film reads it and starts nothing.
    again = invoke(world, recast_lines.start_dialogue_analysis, state)
    assert again["dialogue"]["operation_id"] == operation["id"]
    second = world.create("second")
    assert second["dialogue"]["operation_id"] == operation["id"]
    world.invoke(recast.feed)
    assert len(dialogue_operations(world)) == 1
    nothing = refused(world, state, recast_lines.retry_dialogue_analysis)
    assert nothing.status == 409


def test_the_voice_step_says_why_it_cannot_start(world, voice):
    state = checked(
        world.invoke(recast.create_recast, source_asset_id="film", title=None, request_key="w")
    )
    assert state["dialogue"]["reason"] == "Wait for the film analysis to finish."
    early = refused(world, state, recast_lines.start_dialogue_analysis)
    assert (early.status, str(early)) == (409, "Wait for the film analysis to finish.")
    with world.db.transaction() as session:
        session.add(
            AssetRecord(
                id="silent",
                sha256="silent",
                name="silent",
                kind="video",
                path="silent",
                mime="application/octet-stream",
                info={"width": 1920, "height": 1080, "frame_rate": "24/1", "has_audio": False},
            )
        )
    silent = world.invoke(
        recast.create_recast, source_asset_id="silent", title=None, request_key="silent"
    )
    with world.db.transaction() as session:
        world.settle(session, silent["analysis"]["operation_id"])
    silent = checked(world.invoke(recast.recast_state, silent["id"]))
    assert silent["dialogue"]["gap"] == {
        "code": "no_audio",
        "message": "This film has no audio track.",
    }
    assert silent["dialogue"]["reason"] == "This film has no audio track."
    gap = refused(world, silent, recast_lines.start_dialogue_analysis)
    assert (gap.status, str(gap)) == (422, "This film has no audio track.")
    assert voice.calls == []


# --- A10 migration ----------------------------------------------------------------------------------------------------


def test_voice_migration_constraints_round_trip_and_rollback(world):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from sqlalchemy.exc import IntegrityError

    from gaiavfx.modules.editing.recast.persistence import LineChoiceRecord, LineRecord

    state = world.create()
    state = edit(world, state, recast.patch_character, state["characters"][0]["id"], label="Nell")
    config = Config()
    config.set_main_option("script_location", MIGRATIONS.as_posix())
    config.set_main_option("sqlalchemy.url", "sqlite:///" + world.db.path.as_posix())
    voice_columns = {
        "voice_asset_ids",
        "voice_state",
        "voice_proposed_by",
        "voice_model_state",
        "voice_model_artifact_id",
    }
    command.downgrade(config, "0009_recast_edits")
    found = inspect(world.db.engine)
    assert not {"editing_recast_lines", "editing_recast_line_choices"} & set(
        found.get_table_names()
    )
    assert not voice_columns & {c["name"] for c in found.get_columns("editing_recast_characters")}
    assert "dialogue_operation_id" not in {
        c["name"] for c in found.get_columns("editing_recast_edits")
    }
    # The batch downgrade keeps what the tables had: the journal's check and index, the cast's unique key.
    assert {c["name"] for c in found.get_check_constraints("editing_recast_edits")} == {
        "ck_recast_edit_kind"
    }
    assert "ix_editing_recast_edits_recast_created" in {
        i["name"] for i in found.get_indexes("editing_recast_edits")
    }
    assert [
        u["column_names"] for u in found.get_unique_constraints("editing_recast_characters")
    ] == [["recast_id", "cluster_id"]]
    command.upgrade(config, "head")
    found = inspect(world.db.engine)
    assert voice_columns <= {c["name"] for c in found.get_columns("editing_recast_characters")}
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        assert stored["lines"] == [] and stored["line_choices"] == []
        assert all(stored["characters"][0][k] is None for k in voice_columns)
    row = {
        "recast_id": state["id"],
        "dialogue_operation_id": "dialogue-1",
        "segment_key": KEYS[0],
        "character_id": state["characters"][0]["id"],
        "state": "proposed",
        "proposed_by": "agent",
        "start_sample": None,
        "end_sample": None,
        "evidence": {"why": "named"},
        "created_at": "now",
        "decided_at": None,
    }
    for invalid in (
        {"state": "maybe"},
        {"proposed_by": "analysis"},
        {"start_sample": 10, "end_sample": 10},
        {"start_sample": -1, "end_sample": 10},
        {"start_sample": 10},
        {"state": "confirmed", "decided_at": None},
        {"decided_at": "now"},
    ):
        with pytest.raises(IntegrityError), world.db.transaction() as session:
            session.add(LineRecord(**{**row, **invalid}))
            session.flush()
    choice = {
        "id": "choice-1",
        "recast_id": state["id"],
        "dialogue_operation_id": "dialogue-1",
        "segment_key": KEYS[0],
        "version": "original",
        "authority": "agent",
        "chosen_at": "now",
        "recast_revision": 1,
    }
    with pytest.raises(IntegrityError), world.db.transaction() as session:
        session.add(LineChoiceRecord(**choice))
        session.flush()
    with world.db.transaction() as session:
        session.add(LineChoiceRecord(**{**choice, "authority": "user"}))
    with pytest.raises(RuntimeError, match="rollback"), world.db.transaction() as session:
        aggregate = p.store.load(session, state["id"])
        aggregate["lines"].append(row)
        p.store.save(session, aggregate)
        raise RuntimeError("rollback")
    with world.db.transaction() as session:
        aggregate = p.store.load(session, state["id"])
        assert aggregate["lines"] == []
        assert [c["authority"] for c in aggregate["line_choices"]] == ["user"]
        aggregate["lines"].append(row)
        p.store.save(session, aggregate)
    with world.db.transaction() as session:
        aggregate = p.store.load(session, state["id"])
        assert aggregate["lines"] == [row]
        aggregate["lines"][0].update(
            state="confirmed", decided_at="later", start_sample=5, end_sample=9
        )
        p.store.save(session, aggregate)
    with world.db.transaction() as session:
        assert p.store.load(session, state["id"])["lines"][0]["end_sample"] == 9


# --- A11 decisions ----------------------------------------------------------------------------------------------------


def test_analysis_proposals_are_computed_views(world, voice):
    state, a, b = analysed(world)
    assert state["dialogue"]["state"] == "succeeded" and state["dialogue"]["stage"] == "done"
    assert state["lines"] == {"segments": 6, "proposed": 6, "confirmed": 0, "rejected": 0}
    counts = {c["id"]: (c["lines_proposed"], c["lines_confirmed"]) for c in state["characters"]}
    assert counts == {a: (4, 0), b: (2, 0)}
    view = lines(world, state)
    assert view["dialogue_operation_id"] == state["dialogue"]["operation_id"]
    assert view["rule_version"] == 1 and view["sample_rate"] == 48000
    assert [item["segment_key"] for item in view["lines"]] == KEYS
    assert [item["character_id"] for item in view["lines"]] == [a, a, a, a, b, b]
    assert {item["proposed_by"] for item in view["lines"]} == {"analysis"}
    first = view["lines"][0]
    assert (first["start_sample"], first["end_sample"]) == (7200, 50400)  # the padded words
    assert (first["start_frame"], first["end_frame"]) == (3, 26)
    assert first["shot_id"] == "s0001" and first["edges"] == "rule"
    assert first["words"][0] == {"start_sample": 9600, "end_sample": 28800, "word": "Hello"}
    assert view["lines"][5]["whispered"] and not first["whispered"]
    assert view["lines"][4]["median_hz"] == 104.0
    assert view["profiles"] == {
        a: {"segments": 4, "seconds": pytest.approx(4.0)},
        b: {"segments": 2, "seconds": pytest.approx(2.2)},
    }
    assert rows(world, state) == {}  # proposals are never stored
    # A second recast of the same film shows the analysis as done without asking.
    second = world.create("second")
    assert second["dialogue"]["operation_id"] == state["dialogue"]["operation_id"]
    assert second["dialogue"]["state"] == "succeeded" and len(voice.calls) == 1


def test_line_decisions_on_the_store(world, voice):
    state, a, b = analysed(world)
    state = invoke(world, recast_lines.confirm_line, state, KEYS[0], character_id=a)
    stored = rows(world, state)[KEYS[0]]
    assert (stored["state"], stored["proposed_by"], stored["character_id"]) == (
        "confirmed",
        "user",
        a,
    )
    assert (
        stored["decided_at"]
        and stored["dialogue_operation_id"] == state["dialogue"]["operation_id"]
    )
    created = stored["created_at"]
    state = invoke(world, recast_lines.reject_line, state, KEYS[0], character_id=a)
    stored = rows(world, state)[KEYS[0]]
    assert stored["state"] == "rejected" and stored["created_at"] == created
    assert line(world, state, KEYS[0])["state"] == "rejected"
    state = invoke(world, recast_lines.clear_line, state, KEYS[0])
    assert KEYS[0] not in rows(world, state)
    assert line(world, state, KEYS[0])["proposed_by"] == "analysis"
    # Confirming for another character replaces the row.
    state = invoke(world, recast_lines.confirm_line, state, KEYS[0], character_id=a)
    state = invoke(world, recast_lines.confirm_line, state, KEYS[0], character_id=b)
    assert rows(world, state)[KEYS[0]]["character_id"] == b
    assert line(world, state, KEYS[0])["character_id"] == b
    # Edges on a bare segment confirm it for the proposed character.
    state = invoke(
        world, recast_lines.edge_line, state, KEYS[1], start_sample=70_000, end_sample=115_000
    )
    stored = rows(world, state)[KEYS[1]]
    assert (stored["state"], stored["character_id"]) == ("confirmed", a)
    assert (stored["start_sample"], stored["end_sample"]) == (70_000, 115_000)
    edged = line(world, state, KEYS[1])
    assert edged["edges"] == "user" and (edged["start_sample"], edged["end_sample"]) == (
        70_000,
        115_000,
    )
    state = invoke(
        world, recast_lines.edge_line, state, KEYS[1], start_sample=None, end_sample=None
    )
    stored = rows(world, state)[KEYS[1]]
    assert stored["state"] == "confirmed" and stored["start_sample"] is None
    assert line(world, state, KEYS[1])["edges"] == "rule"
    # Refusals, before any write.
    state = invoke(world, recast_lines.reject_line, state, KEYS[2], character_id=a)
    for args, status in (
        ((KEYS[2], 160_000, 200_000), 409),  # a rejected line
        ((KEYS[3], 0, 600_000), 422),  # outside the film (584 000 samples)
        ((KEYS[3], 230_000, 230_000), 422),  # start not before end
        ((KEYS[3], 300_000, 320_000), 422),  # not on the line's words
        ((KEYS[3], 230_000, None), 422),  # one edge
        ((KEYS[1], 40_000, 100_000), 409),  # over the confirmed line 00000-200
        (("99999-0", 1, 2), 404),
    ):
        error = refused(
            world, state, recast_lines.edge_line, args[0], start_sample=args[1], end_sample=args[2]
        )
        assert error.status == status, (args, str(error))
        if args[0] == KEYS[1]:
            assert KEYS[0] in str(error)
    assert world.invoke(recast.recast_state, state["id"])["revision"] == state["revision"]
    assert refused(world, state, recast_lines.confirm_line, "99999-0", character_id=a).status == 404
    assert refused(world, state, recast_lines.confirm_line, KEYS[3], character_id="x").status == 404
    assert refused(world, state, recast_lines.clear_line, KEYS[3]).status == 404
    with pytest.raises(ApplicationError) as error:
        world.invoke(
            recast_lines.confirm_line, state["id"], KEYS[3], character_id=a, expected_revision=1
        )
    assert error.value.status == 409
    hidden = edit(world, state, recast.patch_character, b, hidden=True)
    assert refused(world, hidden, recast_lines.confirm_line, KEYS[4], character_id=b).status == 422
    state = edit(world, hidden, recast.patch_character, b, hidden=False)


def test_restored_edges_never_overlap_a_confirmed_line(world, voice):
    """Astra R-P4: a rejected line keeps its edges; confirming it again, or undoing the rejection, must not bring
    them back over a line confirmed since. Both refuse before any write."""
    state, a, _b = analysed(world)
    state = invoke(
        world, recast_lines.edge_line, state, KEYS[1], start_sample=70_000, end_sample=160_000
    )
    state = invoke(world, recast_lines.reject_line, state, KEYS[1], character_id=a)
    rejection = state["edits"][0]
    state = invoke(
        world, recast_lines.edge_line, state, KEYS[2], start_sample=150_000, end_sample=200_000
    )
    before = rows(world, state)
    error = refused(world, state, recast_lines.confirm_line, KEYS[1], character_id=a)
    assert error.status == 409 and KEYS[2] in str(error)
    error = refused(world, state, recast_edits.undo_edit, rejection["id"])
    assert error.status == 409 and KEYS[2] in str(error) and KEYS[1] in str(error)
    assert rows(world, state) == before
    assert world.invoke(recast.recast_state, state["id"])["revision"] == state["revision"]


def test_a_line_covered_by_confirmed_edges_is_not_proposed_or_confirmed(world, voice):
    """Astra R-P4: edges that cover another segment leave it no audio: no proposal, and no confirmation."""
    state, a, _b = analysed(world)
    assert line(world, state, KEYS[2])["character_id"] == a
    state = invoke(
        world, recast_lines.edge_line, state, KEYS[1], start_sample=70_000, end_sample=210_000
    )
    covered = line(world, state, KEYS[2])
    assert covered["character_id"] is None and covered["state"] is None
    assert covered["evidence"]["reason"] == "covered"
    error = refused(world, state, recast_lines.confirm_line, KEYS[2], character_id=a)
    assert error.status == 409 and str(error) == recast_lines.COVERED
    state = invoke(world, recast_lines.confirm_lines, state, character_id=a)
    assert KEYS[2] not in rows(world, state)


def test_bulk_confirmation(world, voice):
    state, a, b = analysed(world)
    state = invoke(world, recast_lines.confirm_line, state, KEYS[0], character_id=a)
    state = invoke(world, recast_lines.reject_line, state, KEYS[2], character_id=a)
    state = invoke(world, recast_lines.confirm_lines, state, character_id=a)
    stored = rows(world, state)
    assert {k: r["state"] for k, r in stored.items()} == {
        KEYS[0]: "confirmed",
        KEYS[1]: "confirmed",
        KEYS[2]: "rejected",
        KEYS[3]: "confirmed",
    }
    assert state["lines"] == {"segments": 6, "proposed": 2, "confirmed": 3, "rejected": 1}
    assert refused(world, state, recast_lines.confirm_lines, character_id=a).status == 409
    wrong = refused(
        world, state, recast_lines.confirm_lines, character_id=b, segment_keys=[KEYS[1]]
    )
    assert wrong.status == 422 and KEYS[1] in str(wrong)
    missing = refused(world, state, recast_lines.confirm_lines, character_id=b, segment_keys=["x"])
    assert missing.status == 404
    state = invoke(world, recast_lines.confirm_lines, state, character_id=b, segment_keys=[KEYS[5]])
    assert rows(world, state)[KEYS[5]]["state"] == "confirmed" and KEYS[4] not in rows(world, state)


def test_agent_proposals_need_the_users_naming_and_never_override_a_decision(world, voice):
    state, a, b = analysed(world)
    unnamed = refused(
        world,
        state,
        recast_lines.propose_line,
        KEYS[0],
        b,
        proposed_by="agent",
        evidence={"why": "voice"},
        user_message="whose line is this?",
    )
    assert unnamed.status == 422
    edits = len(state["edits"])
    state = invoke(
        world,
        recast_lines.propose_line,
        state,
        KEYS[0],
        b,
        proposed_by="agent",
        evidence={"why": "voice"},
        user_message="That first line is Tom's.",
    )
    assert len(state["edits"]) == edits  # a proposal is not the user's edit
    proposed = line(world, state, KEYS[0])
    assert (proposed["character_id"], proposed["state"], proposed["proposed_by"]) == (
        b,
        "proposed",
        "agent",
    )
    assert state["lines"]["proposed"] == 6  # the agent's proposal replaced nothing but the view
    state = invoke(world, recast_lines.confirm_line, state, KEYS[0], character_id=b)
    assert rows(world, state)[KEYS[0]]["state"] == "confirmed"
    over = refused(
        world,
        state,
        recast_lines.propose_line,
        KEYS[0],
        a,
        proposed_by="agent",
        evidence={},
        user_message="It is Mara's.",
    )
    assert over.status == 409
    bad = refused(
        world,
        state,
        recast_lines.propose_line,
        KEYS[1],
        a,
        proposed_by="agent",
        evidence={"score": float("nan")},
        user_message="Mara says it.",
    )
    assert bad.status == 422


def test_every_line_command_waits_for_a_succeeded_analysis(world, voice):
    state, a, _b = cast(world)
    commands = (
        (recast_lines.confirm_line, (KEYS[0],), {"character_id": a}),
        (recast_lines.reject_line, (KEYS[0],), {"character_id": a}),
        (recast_lines.clear_line, (KEYS[0],), {}),
        (recast_lines.edge_line, (KEYS[0],), {"start_sample": 1, "end_sample": 9}),
        (recast_lines.confirm_lines, (), {"character_id": a}),
    )
    for function, args, kwargs in commands:
        error = refused(world, state, function, *args, **kwargs)
        assert (error.status, str(error)) == (409, "Analyse the dialogue before deciding lines.")
    state = invoke(world, recast_lines.start_dialogue_analysis, state)  # running, not succeeded
    for function, args, kwargs in commands:
        assert refused(world, state, function, *args, **kwargs).status == 409
    assert lines(world, state)["lines"] == []


# --- A12 the journal --------------------------------------------------------------------------------------------------


def test_line_decisions_are_journaled_and_undo_as_guarded_inverses(world, voice, monkeypatch):
    state, a, b = analysed(world)
    operation = state["dialogue"]["operation_id"]
    rename = state["edits"][0]
    assert entry(world, rename["id"])["dialogue_operation_id"] is None  # a cast edit after P4
    steps = [
        (
            recast_lines.confirm_line,
            (KEYS[0],),
            {"character_id": a},
            "Confirmed “Hello there.” for Mara",
        ),
        (
            recast_lines.reject_line,
            (KEYS[0],),
            {"character_id": a},
            "Rejected “Hello there.” for Mara",
        ),
        (recast_lines.clear_line, (KEYS[0],), {}, "Cleared the decision on “Hello there.”"),
        (
            recast_lines.edge_line,
            (KEYS[1],),
            {"start_sample": 70_000, "end_sample": 115_000},
            "Set the edges of “How are you today?”",
        ),
        (
            recast_lines.edge_line,
            (KEYS[1],),
            {"start_sample": None, "end_sample": None},
            "Removed the edges of “How are you today?”",
        ),
    ]
    for function, args, kwargs, summary in steps:
        before = len(state["edits"])
        state = invoke(world, function, state, *args, **kwargs)
        assert state["edits"][0]["summary"] == summary and state["edits"][0]["kind"] == "edit"
        assert len(state["edits"]) == min(before + 1, 20)
        assert entry(world, state["edits"][0]["id"])["dialogue_operation_id"] == operation
    long = "A line that runs far longer than forty characters in all."
    voice.report["segments"][3]["text"] = long
    state = invoke(world, recast_lines.confirm_line, state, KEYS[3], character_id=a)
    assert (
        state["edits"][0]["summary"]
        == "Confirmed “A line that runs far longer than forty…” for Mara"
    )
    voice.report["segments"][3]["text"] = LINES[3][2]
    # Undo removes the row; undo of the undo (redo) puts it back.
    confirm = state["edits"][0]
    state = undo(world, state, confirm)
    assert KEYS[3] not in rows(world, state)
    assert state["edits"][0]["kind"] == "undo"
    assert entry(world, state["edits"][0]["id"])["dialogue_operation_id"] == operation
    state = undo(world, state, state["edits"][0])
    assert rows(world, state)[KEYS[3]]["state"] == "confirmed"
    assert state["edits"][0]["kind"] == "redo"
    # A bulk confirmation is one entry; a later decision on one of its lines makes its undo refuse.
    state = invoke(world, recast_lines.clear_line, state, KEYS[3])
    state = invoke(world, recast_lines.confirm_lines, state, character_id=a)
    bulk = state["edits"][0]
    assert bulk["summary"] == "Confirmed 3 lines for Mara"  # 00001 is confirmed already
    assert sum(1 for r in rows(world, state).values() if r["state"] == "confirmed") == 4
    undone = undo(world, state, bulk)
    assert {k for k, r in rows(world, undone).items()} == {KEYS[1]}
    state = undo(world, undone, undone["edits"][0])
    redone = state["edits"][0]
    assert redone["kind"] == "redo" and redone["summary"] == "Redo: Confirmed 3 lines for Mara"
    state = invoke(world, recast_lines.reject_line, state, KEYS[2], character_id=a)
    error = refused(world, state, recast_edits.undo_edit, redone["id"])
    assert (error.status, str(error)) == (409, LATER)
    # After a new analysis the line entries refuse; the cast edit from before still undoes.
    monkeypatch.setattr(voice.real, "DIALOGUE_VERSION", 2)
    state = invoke(world, recast_lines.start_dialogue_analysis, state)
    assert state["dialogue"]["operation_id"] != operation
    state = settle_dialogue(world, state)
    reject = state["edits"][0]
    error = refused(world, state, recast_edits.undo_edit, reject["id"])
    assert (error.status, str(error)) == (409, REANALYSED)
    assert world.invoke(recast.recast_state, state["id"])["revision"] == state["revision"]
    # Old rows are kept but shown nowhere: the new analysis proposes again.
    assert len(rows(world, state)) == 4
    assert {item["proposed_by"] for item in lines(world, state)["lines"]} == {"analysis"}
    assert state["lines"]["confirmed"] == 0
    state = undo(world, state, rename)
    assert next(c for c in state["characters"] if c["id"] == b)["label"] != "Tom"


def test_an_entry_written_before_the_voice_columns_still_undoes(world):
    from gaiavfx.modules.editing.recast.persistence import RecastEditRecord

    state, a, _b = cast(world)
    state = edit(world, state, recast.patch_character, a, label="Nell")
    rename = state["edits"][0]
    with world.db.transaction() as session:
        row = session.get(RecastEditRecord, rename["id"])
        images = []
        for side in (row.before, row.after):
            images.append(
                {
                    "characters": {
                        k: {f: v for f, v in c.items() if not f.startswith("voice_")}
                        for k, c in side["characters"].items()
                    }
                }
            )
        row.before, row.after = images
        row.dialogue_operation_id = None
    state = undo(world, state, rename)
    assert next(c for c in state["characters"] if c["id"] == a)["label"] == "Mara"
    with world.db.transaction() as session:
        restored = next(c for c in p.store.load(session, state["id"])["characters"] if c["id"] == a)
    assert restored["voice_state"] is None and restored["label"] == "Mara"


# --- A13 merge --------------------------------------------------------------------------------------------------------


def test_merge_moves_lines_and_undoes_with_them(world, voice):
    state, a, b = analysed(world)
    state = invoke(world, recast_lines.confirm_line, state, KEYS[4], character_id=b)
    state = invoke(world, recast_lines.reject_line, state, KEYS[0], character_id=a)
    state = edit(world, state, recast.merge_characters, into=a, sources=[b])
    merge = state["edits"][0]
    assert rows(world, state)[KEYS[4]]["character_id"] == a
    assert entry(world, merge["id"])["dialogue_operation_id"] == state["dialogue"]["operation_id"]
    state = undo(world, state, merge)
    stored = rows(world, state)
    assert stored[KEYS[4]]["character_id"] == b and stored[KEYS[0]]["character_id"] == a
    # Redo would delete the source again, but a line decided since names it: refused.
    state = invoke(world, recast_lines.confirm_line, state, KEYS[5], character_id=b)
    error = refused(world, state, recast_edits.undo_edit, state["edits"][1]["id"])
    assert (error.status, str(error)) == (409, LATER)
    # The same for an undo that would delete a character a later line decision names.
    state = edit(world, state, recast.move_tracks, track_ids=["t2"], to="new")
    move = state["edits"][0]
    new = next(c["id"] for c in state["characters"] if c["id"] not in {a, b})
    state = invoke(world, recast_lines.confirm_line, state, KEYS[2], character_id=new)
    error = refused(world, state, recast_edits.undo_edit, move["id"])
    assert (error.status, str(error)) == (409, LATER)


def test_merge_keeps_the_stronger_decision_on_one_segment():
    def row(character, state, key="k"):
        return {
            "dialogue_operation_id": "op",
            "segment_key": key,
            "character_id": character,
            "state": state,
        }

    merged = recast_lines.merge_rows(
        [row("into", "proposed"), row("source", "confirmed"), row("source", "rejected", "j")],
        {"source"},
        "into",
    )
    assert merged == [row("into", "confirmed"), row("into", "rejected", "j")]
    assert recast_lines.merge_rows(
        [row("source", "rejected"), row("into", "confirmed")], {"source"}, "into"
    ) == [row("into", "confirmed")]
    tie = recast_lines.merge_rows(
        [{**row("source", "proposed"), "n": 1}, {**row("into", "proposed"), "n": 2}],
        {"source"},
        "into",
    )
    assert tie == [{**row("into", "proposed"), "n": 2}]


# --- A14 the cache ----------------------------------------------------------------------------------------------------


def test_proposals_are_cached_against_their_inputs(world, voice, monkeypatch):
    state, a, b = analysed(world)
    calls = []
    real = rule.attribute

    def counted(*args, **kwargs):
        calls.append(1)
        return real(*args, **kwargs)

    monkeypatch.setattr(rule, "attribute", counted)
    p.clear_caches(world.library)
    for _ in range(10):
        world.invoke(recast.recast_state, state["id"])
    assert len(calls) == 1
    state = invoke(world, recast_lines.confirm_line, state, KEYS[0], character_id=a)
    assert len(calls) == 2
    world.invoke(recast.recast_state, state["id"])
    assert len(calls) == 2
    state = edit(world, state, recast.move_tracks, track_ids=["t2"], to=b)
    assert len(calls) == 3
    state = edit(world, state, recast.patch_character, b, hidden=True)
    assert len(calls) == 4
    from gaiavfx.modules.generation.operations.persistence import OperationRecord

    with world.db.transaction() as session:
        session.get(OperationRecord, state["dialogue"]["operation_id"]).updated_at = "2099-01-01"
    world.invoke(recast.recast_state, state["id"])
    assert len(calls) == 5


# --- A15 blind-safe ---------------------------------------------------------------------------------------------------


def test_the_state_and_lines_carry_no_artifact_ids_or_paths(world, voice):
    from gaiavfx.modules.generation.operations.persistence import JobRecord

    state, a, _b = analysed(world)
    operation = state["dialogue"]["operation_id"]
    secrets = []
    with world.db.transaction() as session:
        for job in world.ops.operation(session, operation)["jobs"]:
            secret = f"artifact-{job['step']}-secret"
            secrets.append(secret)
            session.get(JobRecord, job["id"]).outputs = [
                {"output_key": "x", "artifact_id": secret, "sha256": "sha", "path": "C:/library/x"}
            ]
        jobs = [j["id"] for j in world.ops.operation(session, operation)["jobs"]]
    state = invoke(world, recast_lines.confirm_line, state, KEYS[0], character_id=a)
    for text in (json.dumps(state), json.dumps(lines(world, state))):
        assert not any(secret in text for secret in secrets)
        assert not any(job in text for job in jobs)
        assert str(world.db.root) not in text and "C:/library" not in text
        assert text.count(operation) == 1
