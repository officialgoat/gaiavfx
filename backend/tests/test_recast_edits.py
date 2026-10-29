"""Cast decisions the user can take back (ADR 0017, CP3; ADR 0012 Amendment 7), on the real recast store."""

from copy import deepcopy

import pytest
from sqlalchemy import select
from test_recast_feed import drain, run, waiting_reference
from test_recast_service import MIGRATIONS, edit
from test_recast_service import recast_template as recast_template  # noqa: PLC0414
from test_recast_service import world as world  # noqa: PLC0414

from gaiavfx.api.recast import RecastDTO
from gaiavfx.application import recast, recast_edits
from gaiavfx.application import recast_projection as p
from gaiavfx.application.recast_edits import LATER
from gaiavfx.modules.editing.recast.persistence import RecastEditRecord
from gaiavfx.shared.errors import ApplicationError


def three_clusters(world):
    world.report["clusters"] = [
        {"id": "a", "track_ids": ["t1", "t2"]},
        {"id": "b", "track_ids": ["t3"]},
        {"id": "c", "track_ids": ["t4"]},
    ]
    world.report["suggestions"] = [
        {"a": "a", "b": "b", "max_similarity": 0.8},
        {"a": "a", "b": "c", "max_similarity": 0.7},
        {"a": "b", "b": "c", "max_similarity": 0.75},
    ]
    state = world.create()
    return state, [v["id"] for v in state["characters"]]


def similar(state, character_id):
    character = next(c for c in state["characters"] if c["id"] == character_id)
    return [s["character_id"] for s in character["similar"]]


def test_a_set_aside_pair_is_hidden_both_ways_for_good(world):
    state, (a, b, c) = three_clusters(world)
    fingerprints = [s["plan"] and s["plan"].get("_fingerprint") for s in state["shots"]]
    state = edit(world, state, recast.set_not_same, pairs=[(b, a)])
    assert b not in similar(state, a) and a not in similar(state, b)
    assert c in similar(state, a)
    assert state["not_same"] == [{"character_a": min(a, b), "character_b": max(a, b)}]
    RecastDTO.model_validate(state)
    # Stored in the recast: a fresh read (another browser) sees the same.
    fresh = world.invoke(recast.recast_state, state["id"])
    assert b not in similar(fresh, a)
    # Setting the same pair aside again changes nothing; plans do not depend on it.
    again = edit(world, fresh, recast.set_not_same, pairs=[(a, b)])
    assert again["not_same"] == state["not_same"]
    assert [s["plan"] and s["plan"].get("_fingerprint") for s in again["shots"]] == fingerprints


def test_a_merge_carries_set_aside_pairs_to_the_character_that_remains(world):
    state, (a, b, c) = three_clusters(world)
    state = edit(world, state, recast.set_not_same, pairs=[(a, b), (c, b)])
    # a is c, and a is not b, so c is not b: one pair remains, and none pairs c with itself.
    state = edit(world, state, recast.merge_characters, into=c, sources=[a])
    assert state["not_same"] == [{"character_a": min(b, c), "character_b": max(b, c)}]
    assert similar(state, c) == [] and similar(state, b) == []
    # Merging the other side in removes the pair that would name one character twice.
    state = edit(world, state, recast.merge_characters, into=c, sources=[b])
    assert state["not_same"] == []


def test_set_aside_refusals(world):
    state, (a, b, _c) = three_clusters(world)
    with pytest.raises(ApplicationError) as missing:
        world.invoke(
            recast.set_not_same,
            state["id"],
            expected_revision=state["revision"],
            pairs=[(a, "nobody")],
        )
    assert missing.value.status == 404
    with pytest.raises(ApplicationError) as itself:
        world.invoke(
            recast.set_not_same,
            state["id"],
            expected_revision=state["revision"],
            pairs=[(a, a)],
        )
    assert itself.value.status == 422
    edit(world, state, recast.set_not_same, pairs=[(a, b)])
    with pytest.raises(ApplicationError) as stale:
        world.invoke(
            recast.set_not_same,
            state["id"],
            expected_revision=state["revision"],
            pairs=[(a, b)],
        )
    assert stale.value.status == 409


def test_not_same_migration_round_trip_order_and_rollback(world):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from sqlalchemy.exc import IntegrityError

    from gaiavfx.modules.editing.recast.persistence import NotSameRecord

    state = world.ready()
    config = Config()
    config.set_main_option("script_location", MIGRATIONS.as_posix())
    config.set_main_option("sqlalchemy.url", "sqlite:///" + world.db.path.as_posix())
    command.downgrade(config, "0007_recast_appearance_links")
    assert "editing_recast_not_same" not in inspect(world.db.engine).get_table_names()
    command.upgrade(config, "head")
    with world.db.transaction() as session:
        assert p.store.load(session, state["id"])["not_same"] == []
    row = {"recast_id": state["id"], "character_a": "a", "character_b": "b", "created_at": "now"}
    with pytest.raises(IntegrityError), world.db.transaction() as session:
        session.add(NotSameRecord(**{**row, "character_a": "b", "character_b": "a"}))
        session.flush()
    with pytest.raises(RuntimeError, match="rollback"), world.db.transaction() as session:
        aggregate = p.store.load(session, state["id"])
        aggregate["not_same"].append(row)
        p.store.save(session, aggregate)
        raise RuntimeError("rollback")
    with world.db.transaction() as session:
        aggregate = p.store.load(session, state["id"])
        assert aggregate["not_same"] == []
        aggregate["not_same"].append(row)
        p.store.save(session, aggregate)
    with world.db.transaction() as session:
        assert p.store.load(session, state["id"])["not_same"] == [row]


# The cast-edit journal (CP3 B2): undo is a guarded inverse of the rows an edit touched.


def undo(world, state, entry):
    return edit(world, state, recast_edits.undo_edit, entry["id"])


def stored(world, recast_id):
    with world.db.transaction() as session:
        return p.store.load(session, recast_id)


def saved_cast(world):
    """The three-cluster cast, saved once (reads alone never store what `sync` creates)."""
    state, ids = three_clusters(world)
    return edit(world, state, recast.patch_shot, "s0001", priority=1), ids


def operations(world):
    with world.db.transaction() as session:
        return sorted(
            (o["id"], o["kind"], o["state"], o["cancel_requested"])
            for o in world.ops.recent(session, limit=100000)
        )


def journal(world):
    with world.db.transaction() as session:
        rows = session.scalars(select(RecastEditRecord).order_by(RecastEditRecord.created_at))
        return [{c.key: deepcopy(getattr(r, c.key)) for c in r.__table__.columns} for r in rows]


def snapshot(world, recast_id):
    """What a refused undo must leave exactly as it was: rows, revision, operations, journal."""
    return stored(world, recast_id), operations(world), journal(world)


def refused(world, state, entry, status=409):
    before = snapshot(world, state["id"])
    with pytest.raises(ApplicationError) as exc:
        undo(world, state, entry)
    assert exc.value.status == status
    assert snapshot(world, state["id"]) == before
    return str(exc.value)


def owners(world, aggregate):
    return p.track_characters(aggregate, world.report)


def cast_rows(aggregate):
    return {
        key: aggregate[key]
        for key in ("characters", "overrides", "appearance_links", "not_same", "shots")
    }


def first_analysis(world, recast_id, shot_id):
    return next(s for s in stored(world, recast_id)["shots"] if s["shot_id"] == shot_id)[
        "window_analysis_ops"
    ][0]


def test_undo_a_merge_puts_every_row_back_and_redo_merges_again(world):
    state, (a, b, c) = three_clusters(world)
    state = edit(world, state, recast.set_target, a, asset_id="picture", label="portrait.png")
    state = edit(world, state, recast.set_target, b, asset_id="picture2", label="other.png")
    world.settle_target()
    world.settle_target("picture2")
    state = edit(world, state, recast.plan_recast, range=None)
    # An override with its own created_at, a link and a set-aside pair, all rewritten by the merge.
    state = edit(world, state, recast.move_tracks, track_ids=["t2"], to=b)
    analysis = first_analysis(world, state["id"], "s0003")
    state = edit(
        world,
        state,
        recast.propose_appearance,
        "s0003",
        analysis,
        "person0",
        b,
        proposed_by="analysis",
        evidence={"votes": 3},
    )
    state = edit(world, state, recast.set_not_same, pairs=[(b, c)])
    before, ops = stored(world, state["id"]), operations(world)
    assert [s["plan"] is not None for s in before["shots"]] == [True, True, True, False]

    state = edit(world, state, recast.merge_characters, into=a, sources=[b])
    merge = state["edits"][0]
    assert merge["kind"] == "edit" and merge["undone_at"] is None
    assert merge["summary"] == "Merged Character 2 into Character 1"
    merged = stored(world, state["id"])
    assert b not in {ch["id"] for ch in merged["characters"]}
    assert merged["shots"][2]["plan"]["performers"] == [a]

    revision = state["revision"]
    state = undo(world, state, merge)
    assert state["revision"] == revision + 1
    after = stored(world, state["id"])
    # Ids, labels, targets, override created_at, link proposer and dates, the pair, and every plan.
    assert cast_rows(after) == cast_rows(before)
    assert after["shots"][2]["plan"]["_fingerprint"] == before["shots"][2]["plan"]["_fingerprint"]
    assert owners(world, after) == owners(world, before)
    assert operations(world) == ops
    assert [(e["kind"], e["summary"]) for e in state["edits"][:2]] == [
        ("undo", "Undo: Merged Character 2 into Character 1"),
        ("edit", "Merged Character 2 into Character 1"),
    ]
    assert state["edits"][0]["undone_at"] is None and state["edits"][1]["undone_at"]
    RecastDTO.model_validate(state)

    state = undo(world, state, state["edits"][0])
    assert [(e["kind"], e["summary"]) for e in state["edits"][:3]] == [
        ("redo", "Redo: Merged Character 2 into Character 1"),
        ("undo", "Undo: Merged Character 2 into Character 1"),
        ("edit", "Merged Character 2 into Character 1"),
    ]
    again = stored(world, state["id"])
    assert cast_rows(again) == cast_rows(merged)
    assert owners(world, again) == owners(world, merged)
    assert operations(world) == ops


def test_undo_a_move_to_a_new_character_deletes_it_only_while_unreferenced(world):
    state, (a, b, c) = saved_cast(world)
    original = stored(world, state["id"])
    state = edit(world, state, recast.move_tracks, track_ids=["t2"], to="new")
    move = state["edits"][0]
    assert move["summary"] == "Moved 1 face track to a new character"
    new = next(ch["id"] for ch in state["characters"] if ch["id"] not in {a, b, c})
    state = edit(world, state, recast.patch_character, new, label="Nell")
    assert state["edits"][0]["summary"] == "Renamed Character 4 to Nell"
    assert refused(world, state, move) == LATER
    state = undo(world, state, state["edits"][0])
    state = undo(world, state, move)
    assert [ch["id"] for ch in state["characters"]] == [a, b, c]
    assert cast_rows(stored(world, state["id"])) == cast_rows(original)

    # A later move of another track into the new character keeps it: the undo refuses.
    state = edit(world, state, recast.move_tracks, track_ids=["t2"], to="new")
    move = state["edits"][0]
    new = next(ch["id"] for ch in state["characters"] if ch["id"] not in {a, b, c})
    state = edit(world, state, recast.move_tracks, track_ids=["t4"], to=new)
    assert state["edits"][0]["summary"] == "Moved 1 face track to Character 4"
    assert refused(world, state, move) == LATER


def test_out_of_order_undo_is_refused_until_the_newer_edit_is_undone(world):
    state, (_a, b, _c) = saved_cast(world)
    original = stored(world, state["id"])
    state = edit(world, state, recast.move_tracks, track_ids=["t2"], to=b)
    older = state["edits"][0]
    state = edit(world, state, recast.move_tracks, track_ids=["t2"], to="unknown")
    newer = state["edits"][0]
    assert newer["summary"] == "Moved 1 face track to the unknown faces"
    assert refused(world, state, older) == LATER
    state = undo(world, state, newer)
    state = undo(world, state, older)
    after = stored(world, state["id"])
    assert after["overrides"] == [] and owners(world, after) == owners(world, original)


def test_undo_clear_target_restores_an_approved_sheet_without_new_work(world):
    state = world.ready()
    cid = state["characters"][0]["id"]
    state, _, sheet = waiting_reference(world, state)
    state = edit(world, state, recast.approve_reference, cid)
    target = state["characters"][0]["target"]
    state = edit(world, state, recast.clear_target, cid)
    assert state["edits"][0]["summary"] == "Cleared Character 1's picture"
    ops = operations(world)
    state = undo(world, state, state["edits"][0])
    assert state["characters"][0]["target"] == target
    assert target["reference"] == {
        "asset_id": sheet["candidates"][0]["asset_id"],
        "state": "approved",
    }
    assert operations(world) == ops


def test_undo_clear_target_restarts_a_sheet_the_clear_cancelled(world):
    state = world.ready()
    cid = state["characters"][0]["id"]
    world.invoke(recast.feed)
    state = world.invoke(recast.recast_state, state["id"])
    assert state["characters"][0]["target"]["reference"] == {"state": "making", "asset_id": None}
    state = edit(world, state, recast.clear_target, cid)
    with world.db.transaction() as session:
        [cancelled] = world.ops.recent(session, kind="face_portraits")
    assert cancelled["state"] == "cancelled"
    count = len(operations(world))
    state = undo(world, state, state["edits"][0])
    assert state["characters"][0]["target"]["state"] == "confirmed"
    assert state["characters"][0]["target"]["reference"] == {"state": "making", "asset_id": None}
    assert len(operations(world)) == count + 1
    with world.db.transaction() as session:
        attempts = world.ops.recent(session, kind="face_portraits")
    assert sorted(o["request"]["attempt"] for o in attempts) == [1, 2]


def test_undo_a_reject_restores_the_agent_proposal_and_starts_nothing(world):
    state = world.create()
    cid = state["characters"][0]["id"]
    state = edit(world, state, recast.propose_target, cid, asset_id="picture", label="portrait.png")
    assert state["edits"] == []  # an agent proposal is not the user's edit
    proposed = state["characters"][0]["target"]
    state = edit(world, state, recast.clear_target, cid)
    assert state["edits"][0]["summary"] == "Rejected the proposed picture for Character 1"
    ops = operations(world)
    state = undo(world, state, state["edits"][0])
    assert state["characters"][0]["target"] == proposed
    assert proposed["state"] == "proposed" and proposed["proposed_by"] == "agent"
    assert operations(world) == ops
    world.settle_target()
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        assert world.ops.recent(session, kind="face_portraits") == []


def test_undo_set_target_cancels_the_new_sheet_and_restores_the_earlier_target(world):
    state = world.ready()
    cid = state["characters"][0]["id"]
    state, _, _ = waiting_reference(world, state)
    earlier = state["characters"][0]["target"]
    assert earlier["reference"]["state"] == "waiting"
    state = edit(world, state, recast.set_target, cid, asset_id="picture2", label="other.png")
    assert state["edits"][0]["summary"] == "Set Character 1's picture to other.png"
    world.settle_target("picture2")
    world.invoke(recast.feed)
    with world.db.transaction() as session:
        newer = next(
            o
            for o in world.ops.recent(session, kind="face_portraits")
            if o["request"]["target_asset"] == "picture2"
        )
    assert newer["state"] == "queued"
    count = len(operations(world))
    state = undo(world, state, state["edits"][0])
    assert state["characters"][0]["target"] == earlier
    assert len(operations(world)) == count
    with world.db.transaction() as session:
        newer = world.ops.operation(session, newer["id"])
    assert newer["cancel_requested"] and newer["state"] == "cancelled"


def test_choices_feed_and_shot_patches_do_not_block_an_undo(world):
    state = world.ready()
    cid = state["characters"][0]["id"]
    state = edit(world, state, recast.patch_character, cid, label="Nell")
    rename = state["edits"][0]
    world.invoke(recast.feed)  # the feed moves the reference on without a revision
    state = world.invoke(recast.recast_state, state["id"])
    state = edit(world, state, recast.patch_shot, "s0002", included=False)
    state = edit(world, state, recast.choose_version, "s0003", version="original")
    state = undo(world, state, rename)
    assert state["characters"][0]["label"] == "Character 1"
    assert state["characters"][0]["target"]["reference"]["state"] == "making"
    assert state["shots"][1]["included"] is False
    assert state["shots"][2]["choice"]["version"] == "original"


def test_a_source_renamed_after_an_undone_merge_refuses_the_redo(world):
    state, (a, b, _c) = three_clusters(world)
    state = edit(world, state, recast.merge_characters, into=a, sources=[b])
    state = undo(world, state, state["edits"][0])
    inverse = state["edits"][0]
    state = edit(world, state, recast.patch_character, b, label="Nell")
    assert refused(world, state, inverse) == LATER


def test_a_new_analysis_refuses_every_earlier_edit(world, monkeypatch):
    # The fixture reads the report of any analysis, so the failed one can be retried with a cast.
    monkeypatch.setattr(
        world.film_api,
        "film_analysis_report",
        lambda session, library, operation_id: deepcopy(world.report),
    )
    state = world.invoke(
        recast.create_recast, source_asset_id="film", title=None, request_key="again"
    )
    with world.db.transaction() as session:
        world.settle(session, state["analysis"]["operation_id"], "failed")
    state = world.invoke(recast.recast_state, state["id"])
    state = edit(world, state, recast.patch_character, state["characters"][0]["id"], label="Nell")
    rename = state["edits"][0]
    state = edit(world, state, recast.retry_analysis)
    assert state["edits"][0] == rename  # analysis is not a cast edit
    assert (
        refused(world, state, rename) == "The film was analysed again; this edit cannot be undone."
    )


def test_only_changed_cast_edits_are_journaled(world):
    state = world.create()
    cid = state["characters"][0]["id"]
    revision = state["revision"]
    state = edit(world, state, recast.patch_character, cid, label="Character 1")
    assert state["revision"] == revision + 1 and state["edits"] == []
    state = edit(world, state, recast.propose_target, cid, asset_id="picture", label="portrait.png")
    state = edit(world, state, recast.confirm_target, cid)
    world.settle_target()
    state = edit(world, state, recast.plan_recast, range=None)
    state, _, _ = waiting_reference(world, state)
    state = edit(world, state, recast.approve_reference, cid)
    analysis = first_analysis(world, state["id"], "s0001")
    state = edit(world, state, recast.confirm_appearance, "s0001", analysis, "person0", cid)
    state = edit(world, state, recast.reject_appearance, "s0001", analysis, "person0", cid)
    state = edit(world, state, recast.patch_shot, "s0002", included=False)
    state = edit(world, state, recast.choose_version, "s0003", version="original")
    assert state["edits"] == [] and journal(world) == []


def test_journal_kinds_and_refusals(world):
    state, (a, _b, _c) = three_clusters(world)
    state = edit(world, state, recast.patch_character, a, label="Nell")
    first = state["edits"][0]
    for _ in range(3):
        state = undo(world, state, state["edits"][0])
    assert [(e["kind"], e["summary"]) for e in state["edits"]] == [
        ("undo", "Undo: Renamed Character 1 to Nell"),
        ("redo", "Redo: Renamed Character 1 to Nell"),
        ("undo", "Undo: Renamed Character 1 to Nell"),
        ("edit", "Renamed Character 1 to Nell"),
    ]
    assert [e["undone_at"] is None for e in state["edits"]] == [True, False, False, False]
    rows = {r["id"]: r for r in journal(world)}
    chain = [state["edits"][i]["id"] for i in range(4)]
    assert [rows[i]["undoes"] for i in chain] == [*chain[1:], None]
    assert [rows[i]["recast_revision"] for i in chain] == sorted(
        (rows[i]["recast_revision"] for i in chain), reverse=True
    )
    assert state["characters"][0]["label"] == "Character 1"
    assert refused(world, state, first) == "This edit was already undone."
    other = world.create("other")
    other = edit(world, other, recast.patch_character, other["characters"][0]["id"], hidden=True)
    assert other["edits"][0]["summary"] == "Hid Character 1"
    assert refused(world, state, other["edits"][0], 404) == "Edit not found in this recast."
    with pytest.raises(ApplicationError) as stale:
        world.invoke(
            recast_edits.undo_edit,
            state["id"],
            state["edits"][0]["id"],
            expected_revision=state["revision"] - 1,
        )
    assert (
        stale.value.status == 409
        and str(stale.value) == "The recast changed; reload before editing."
    )


def test_undo_never_writes_a_started_shot(world):
    state = drain(world, run(world, world.ready())["id"])
    state = edit(world, state, recast.choose_version, "s0001", version="original")
    before = stored(world, state["id"])
    assert all(s["window_replacement_ops"] for s in before["shots"])
    state = edit(world, state, recast.move_tracks, track_ids=["t1"], to="new")
    ops = operations(world)
    state = undo(world, state, state["edits"][0])
    after = stored(world, state["id"])
    assert after["shots"] == before["shots"] and after["choices"] == before["choices"]
    assert cast_rows(after) == cast_rows(before)
    assert operations(world) == ops


def test_undo_refuses_a_target_picture_gone_from_the_library(world):
    from gaiavfx.modules.library.assets import AssetRecord

    state = world.create()
    cid = state["characters"][0]["id"]
    state = edit(world, state, recast.set_target, cid, asset_id="picture2", label="other.png")
    state = edit(world, state, recast.clear_target, cid)
    with world.db.transaction() as session:
        session.delete(session.get(AssetRecord, "picture2"))
    assert (
        refused(world, state, state["edits"][0])
        == "The target picture is no longer in the Library."
    )


@pytest.mark.parametrize("legacy", [False, True])
def test_undo_replaces_a_character_sync_brought_back_only_while_untouched(world, legacy):
    world.report["clusters"] = [
        {"id": "a", "track_ids": ["t1"]},
        {"id": "b", "track_ids": ["t2", "t3"]},
        {"id": "c", "track_ids": ["t4"]},
    ]
    state = world.create()
    a, b, c = [ch["id"] for ch in state["characters"]]
    if legacy:
        # A cluster character from before deterministic ids: `sync` brings its cluster back under another id, and
        # the store frees the cluster (a unique key) before the undo's row takes it again.
        state = edit(world, state, recast.patch_shot, "s0001", priority=1)
        with world.db.transaction() as session:
            aggregate = p.store.load(session, state["id"])
            next(ch for ch in aggregate["characters"] if ch["id"] == b)["id"] = "legacy-b"
            p.store.save(session, aggregate)
        b = "legacy-b"
    state = edit(world, state, recast.move_tracks, track_ids=["t3"], to=c)
    state = edit(world, state, recast.merge_characters, into=a, sources=[b])
    merge = state["edits"][0]
    source = next(r for r in journal(world) if r["id"] == merge["id"])["before"]["characters"][b]
    # Only an override removed outside the commands lets `sync` bring the cluster's character back.
    with world.db.transaction() as session:
        aggregate = p.store.load(session, state["id"])
        aggregate["overrides"] = [o for o in aggregate["overrides"] if o["track_id"] != "t3"]
        p.store.save(session, aggregate)
    state = world.invoke(recast.recast_state, state["id"])
    [back] = [ch for ch in state["characters"] if ch["id"] not in {a, c}]
    assert back["label"] == "Character 4" and (back["id"] == b) is not legacy
    state = edit(world, state, recast.patch_character, back["id"], label="Nell")
    assert refused(world, state, merge) == LATER
    state = undo(world, state, state["edits"][0])
    state = undo(world, state, merge)
    restored = stored(world, state["id"])
    assert [ch["id"] for ch in restored["characters"]] == [a, b, c]
    assert restored["characters"][1] == source
    assert owners(world, restored)["t2"] == b
    assert [ch["label"] for ch in state["characters"]] == [
        "Character 1",
        "Character 2",
        "Character 3",
    ]


def test_undo_a_picture_rename_keeps_a_sheet_approved_since(world):
    """Astra R-B: a new label on the same picture is not new reference work; its undo keeps a later approval."""
    state = world.ready()
    cid = state["characters"][0]["id"]
    state, _, _ = waiting_reference(world, state)
    target = state["characters"][0]["target"]
    state = edit(
        world, state, recast.set_target, cid, asset_id=target["asset_id"], label="Arin.png"
    )
    rename = state["edits"][0]
    state = edit(world, state, recast.approve_reference, cid)
    approved = state["characters"][0]["target"]["reference"]
    assert approved["state"] == "approved"
    ops = operations(world)
    state = undo(world, state, rename)
    assert state["characters"][0]["target"]["label"] == target["label"]
    assert state["characters"][0]["target"]["reference"] == approved
    assert operations(world) == ops


def test_undo_refuses_to_drop_a_recreated_character_a_later_move_points_at(world):
    """Astra R-B: a pristine stand-in `sync` brought back under another id goes only while nothing points at it."""
    world.report["clusters"] = [
        {"id": "a", "track_ids": ["t1"]},
        {"id": "b", "track_ids": ["t2", "t3"]},
        {"id": "c", "track_ids": ["t4"]},
    ]
    state = world.create()
    a, b, c = [ch["id"] for ch in state["characters"]]
    state = edit(world, state, recast.patch_shot, "s0001", priority=1)
    with world.db.transaction() as session:
        aggregate = p.store.load(session, state["id"])
        next(ch for ch in aggregate["characters"] if ch["id"] == b)["id"] = "legacy-b"
        p.store.save(session, aggregate)
    state = edit(world, state, recast.move_tracks, track_ids=["t3"], to=c)
    state = edit(world, state, recast.merge_characters, into=a, sources=["legacy-b"])
    merge = state["edits"][0]
    with world.db.transaction() as session:
        aggregate = p.store.load(session, state["id"])
        aggregate["overrides"] = [o for o in aggregate["overrides"] if o["track_id"] != "t3"]
        p.store.save(session, aggregate)
    state = world.invoke(recast.recast_state, state["id"])
    [back] = [ch for ch in state["characters"] if ch["id"] not in {a, c}]
    assert back["id"] != "legacy-b"
    # A later move points a track at the stand-in; dropping it would leave that track's owner missing.
    state = edit(world, state, recast.move_tracks, track_ids=["t4"], to=back["id"])
    assert refused(world, state, merge) == LATER


def test_edits_migration_round_trip(world):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    state, (a, _b, _c) = three_clusters(world)
    state = edit(world, state, recast.patch_character, a, label="Nell")
    config = Config()
    config.set_main_option("script_location", MIGRATIONS.as_posix())
    config.set_main_option("sqlalchemy.url", "sqlite:///" + world.db.path.as_posix())
    command.downgrade(config, "0008_recast_not_same")
    assert "editing_recast_edits" not in inspect(world.db.engine).get_table_names()
    command.upgrade(config, "head")
    assert world.invoke(recast.recast_state, state["id"])["edits"] == []
    state = edit(world, state, recast.patch_character, a, label="Nell again")
    assert [e["summary"] for e in state["edits"]] == ["Renamed Nell to Nell again"]
