"""ADR 0020 §8: stale or lost evidence must not turn a Keep into an unsafe rebuild."""

import pytest
from test_harness_build import operation_count
from test_set_seed import operation, transact
from test_set_seed import scene as scene  # noqa: PLC0414
from test_set_seed_fill import drain, start

from gaiavfx.application import set_build, sets
from gaiavfx.modules.library.assets import ArtifactRecord
from gaiavfx.shared.errors import ApplicationError


@pytest.fixture
def rejected(scene):
    s = scene
    s.comfy.fill.bent = {"r1p0"}
    seeded = start(s)
    drain(s.run)
    set_id = operation(s.workspace, seeded["id"])["review"][-1]["set_id"]
    state = transact(s.workspace, sets.set_state, s.workspace.library, set_id)
    aside = next(k for k in state["version"]["keyframes"] if k["key"] == "r1p0")
    assert aside["can_keep"]
    return s, state, aside


def queue(s, state, keyframe, action="keep"):
    return transact(
        s.workspace,
        sets.queue_fix,
        s.workspace.library,
        state["id"],
        version_id=state["version"]["id"],
        action=action,
        target={"keyframe": keyframe["id"], "label": keyframe["label"]},
        shows="",
        issue_id=None,
    )


def run(s, state):
    return transact(
        s.workspace,
        set_build.run_queued_fixes,
        s.workspace.library,
        state["id"],
        version_id=state["version"]["id"],
    )


@pytest.mark.parametrize("already_queued", [False, True])
def test_missing_geometry_refuses_keep_at_queue_and_again_at_build_start(rejected, already_queued):
    s, state, aside = rejected
    if already_queued:
        queue(s, state, aside)
    with s.workspace.db.transaction() as session:
        keyframe = next(
            k
            for k in sets.STORE.keyframes(session, state["version"]["id"])
            if k["id"] == aside["id"]
        )
        pinned = sets.judged_geometry(session, s.workspace.library, state["id"], keyframe)
        session.delete(session.get(ArtifactRecord, pinned["artifact_id"]))
    before = operation_count(s)
    with pytest.raises(
        ApplicationError, match="geometry filling measured.*no longer in the Library"
    ):
        run(s, state) if already_queued else queue(s, state, aside)
    assert operation_count(s) == before
    refreshed = transact(s.workspace, sets.set_state, s.workspace.library, state["id"])
    assert not next(k for k in refreshed["version"]["keyframes"] if k["id"] == aside["id"])[
        "can_keep"
    ]
    assert [f["state"] for f in refreshed["fixes"]] == (["queued"] if already_queued else [])


def test_a_queued_keep_cannot_run_on_a_version_without_that_still(rejected):
    s, state, aside = rejected
    queue(s, state, aside)
    # A later version has replaced the still. Queue membership must be rechecked against the shown version.
    with s.workspace.db.transaction() as session:
        base = sets.STORE.get(session, state["id"])["versions"][0]
        new_id = sets.STORE.add_version(
            session,
            state["id"],
            origin="human",
            parent_id=state["version"]["id"],
            atlas_artifact_id=base["atlas_artifact_id"],
            keyframe_ids=[k["id"] for k in state["version"]["keyframes"] if k["id"] != aside["id"]],
        )
    shown = transact(s.workspace, sets.set_state, s.workspace.library, state["id"], new_id)
    before = operation_count(s)
    with pytest.raises(ApplicationError, match="Keep a still of the version you are looking at"):
        run(s, shown)
    assert operation_count(s) == before
    with s.workspace.db.transaction() as session:
        assert [f["state"] for f in sets.STORE.fixes(session, state["id"])] == ["queued"]


def test_excluding_a_kept_still_records_the_humans_exclusion(rejected):
    s, state, aside = rejected
    queue(s, state, aside)
    run(s, state)
    drain(s.run)
    kept_state = transact(s.workspace, sets.set_state, s.workspace.library, state["id"])
    kept = next(k for k in kept_state["version"]["keyframes"] if k["key"] == "r1p0~1")
    assert kept["role"] == "generated" and not kept["can_keep"]
    queue(s, kept_state, kept, "retract")
    run(s, kept_state)
    drain(s.run)
    excluded_state = transact(s.workspace, sets.set_state, s.workspace.library, state["id"])
    excluded = next(k for k in excluded_state["version"]["keyframes"] if k["key"] == "r1p0~2")
    assert excluded["role"] == "appearance-only"
    assert excluded_state["version"]["note"] == f"v2 rebuilt without {kept['label']}."
    assert excluded["dropped"] is None and not excluded["can_keep"]
    with pytest.raises(ApplicationError, match="not a still that filling set aside"):
        queue(s, excluded_state, excluded)


def test_keep_requires_the_geometry_bytes_as_well_as_its_catalog_record(rejected):
    s, state, aside = rejected
    with s.workspace.db.transaction() as session:
        keyframe = next(
            k
            for k in sets.STORE.keyframes(session, state["version"]["id"])
            if k["id"] == aside["id"]
        )
        pinned = sets.judged_geometry(session, s.workspace.library, state["id"], keyframe)
        path = s.workspace.library.artifact_path(session, pinned["artifact_id"])
    # Simulate an interrupted restore: metadata survives, but the measured glass mask does not.
    path.unlink()
    with pytest.raises(ApplicationError, match="no longer in the Library"):
        queue(s, state, aside)
