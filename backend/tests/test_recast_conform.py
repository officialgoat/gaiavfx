"""Conform application transactions with fake pixel work and real operation state."""

import re
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_recast_feed import drain, run, settle_all
from test_recast_service import edit
from test_recast_service import recast_template as recast_template  # noqa: PLC0414
from test_recast_service import world as world  # noqa: PLC0414

from gaiavfx.api.recast import RecastDTO
from gaiavfx.application import recast
from gaiavfx.application.operations import OperationRunner
from gaiavfx.shared.errors import ApplicationError


def submit(world, state, **overrides):
    return world.invoke(
        recast.start_conform,
        state["id"],
        **{"mode": "chosen", "range": None, "codec": "h264", "request_key": "conform", **overrides},
    )


@pytest.fixture
def reviewed(world, monkeypatch):
    state = drain(world, run(world, world.ready())["id"])
    state = edit(
        world, state, recast.choose_version, "s0001", version=state["shots"][0]["versions"][0]["id"]
    )
    state = edit(world, state, recast.choose_version, "s0002", version="original")
    monkeypatch.setattr(
        world.library,
        "artifact",
        lambda session, id: {"id": id, "kind": "replacement_patch", "sha256": "patch-sha"},
    )
    return state


def test_frozen_choices_preview_range_provenance_and_dto(world, reviewed):
    chosen = submit(world, reviewed)
    assert chosen["budget_reserved"] == 0
    assert len(chosen["request"]["choices"]) == 1
    assert chosen["request"]["choices"][0]["shot_id"] == "s0001"
    assert chosen["request"]["recast_revision"] == reviewed["revision"]
    assert chosen["request"]["audio"] == "copied"
    preview = submit(
        world,
        reviewed,
        mode="preview_unreviewed",
        range={"start": 73, "end": 292},
        request_key="preview",
    )
    assert [c["shot_id"] for c in preview["request"]["choices"]] == ["s0003", "s0004"]
    assert preview["request"]["audio"] == "pcm_trimmed"
    assert "unreviewed" in preview["jobs"][0]["snapshot"]["title"]
    # An excerpt is named by its times, so conforms of different ranges differ in the Library.
    assert re.search(
        r" - \d\d\.\d\d\.\d\d-\d\d\.\d\d\.\d\d$", preview["jobs"][0]["snapshot"]["title"]
    )
    state = world.invoke(recast.recast_state, reviewed["id"])
    RecastDTO.model_validate(state)
    assert len(state["conforms"]) == 2
    assert [(c["codec"], c["error"]) for c in state["conforms"]] == [("h264", None)] * 2
    assert [s["choice"] for s in state["shots"]] == [s["choice"] for s in reviewed["shots"]]
    edit(world, state, recast.choose_version, "s0001", version="original")
    replay = submit(world, reviewed)
    assert replay["id"] == chosen["id"] and not replay["_created"]
    assert replay["request"] == chosen["request"]
    with pytest.raises(ApplicationError, match="different"):
        submit(world, reviewed, codec="prores")


def test_preview_uses_latest_version_without_overriding_an_explicit_choice(world, reviewed):
    for versions in world.versions.values():
        versions.append({**versions[0], "id": versions[0]["id"] + "-new", "take": 1})
    operation = submit(world, reviewed, mode="preview_unreviewed")
    frozen = operation["request"]["choices"]
    assert frozen[0]["version"] == reviewed["shots"][0]["choice"]["version"]
    assert all(c["version"].endswith("-new") for c in frozen[1:])
    assert "s0002" not in {c["shot_id"] for c in frozen}


def test_nothing_to_conform_and_preview_skips_excluded_shots(world, reviewed):
    state = edit(world, reviewed, recast.choose_version, "s0001", version="unchosen")
    with pytest.raises(ApplicationError, match="Nothing to conform") as error:
        submit(world, state)
    assert error.value.status == 409
    # Usability round 2 (C12): the refusal says why and where to go next.
    assert str(error.value) == (
        "Nothing to conform: no shot has a chosen version, so the film would be the original. "
        "Choose versions in Review, or conform a preview with unreviewed drafts."
    )
    with pytest.raises(ApplicationError, match="no shot in this range has a chosen version"):
        submit(world, state, range={"start": 73, "end": 292}, request_key="range")
    state = edit(world, state, recast.patch_shot, "s0003", included=False)
    preview = submit(world, state, mode="preview_unreviewed", request_key="preview")
    assert "s0003" not in {c["shot_id"] for c in preview["request"]["choices"]}
    assert preview["request"]["video"] == "reencoded_h264_crf14"
    character = state["characters"][0]["id"]
    state = edit(world, state, recast.clear_target, character)
    with pytest.raises(ApplicationError, match="Nothing to preview"):
        submit(world, state, mode="preview_unreviewed", request_key="unmapped")


def test_completion_progress_of_one_does_not_fail_the_job(world, reviewed, monkeypatch):
    # Regression (8 Oct review): the media step reports progress(1) after writing the film,
    # and the service accepts running progress below 1 only.
    submit(world, reviewed)
    runner, _, registered = fake_runner(world, monkeypatch)

    def conform(source, patches, output, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"complete master")
        kwargs["progress"](1)

    world.library.media = SimpleNamespace(conform_patches=conform)
    assert runner.run_once()
    registered.assert_called_once()
    assert world.invoke(recast.recast_state, reviewed["id"])["conforms"][0]["state"] == "succeeded"


def fake_runner(world, monkeypatch):
    # These single-job runner checks isolate conform from background portrait inference.
    with world.db.transaction() as session:
        for operation in world.ops.recent(session, kind="face_portraits"):
            world.ops.request_cancel(session, operation["id"])
    runner = OperationRunner(world.db, None, world.library, "unused")
    monkeypatch.setattr(
        runner,
        "_sources",
        lambda job: {k: (world.db.root / k, v["sha256"], k) for k, v in job["inputs"].items()},
    )
    prepared, registered = Mock(return_value="prepared"), Mock(return_value={"id": "output-asset"})
    monkeypatch.setattr(world.library, "prepare", prepared)
    monkeypatch.setattr(world.library, "register", registered)
    return runner, prepared, registered


def test_local_restart_progress_and_atomic_registration(world, reviewed, monkeypatch):
    operation = submit(world, reviewed)
    runner, prepared, registered = fake_runner(world, monkeypatch)
    calls = []

    def conform(source, patches, output, **kwargs):
        calls.append((source, patches, kwargs))
        output.parent.mkdir(parents=True, exist_ok=True)
        kwargs["progress"](0.5)
        if len(calls) == 1:
            runner.stop.set()
            kwargs["cancelled"]()
        output.write_bytes(b"complete master")

    world.library.media = SimpleNamespace(conform_patches=conform)
    assert not runner.run_once()
    pending = world.invoke(recast.recast_state, reviewed["id"])["conforms"][0]
    assert pending["state"] == "running" and pending["progress"] == 0.5
    registered.assert_not_called()
    runner.stop.clear()
    runner.recover()
    assert runner.run_once()
    state = world.invoke(recast.recast_state, reviewed["id"])
    assert state["conforms"][0]["state"] == "succeeded"
    assert state["conforms"][0]["asset_id"] == "output-asset"
    assert calls[0][1] == calls[1][1]
    assert registered.call_args.args[2] == {
        **operation["jobs"][0]["snapshot"]["provenance"],
        "operation_id": operation["id"],
    }
    assert state["shots"][0]["choice"] == reviewed["shots"][0]["choice"]
    prepared.assert_called_once()


@pytest.mark.parametrize("when", ["queued", "encoding", "publication"])
def test_cancel_cannot_publish(world, reviewed, monkeypatch, when):
    operation = submit(world, reviewed)
    runner, _, registered = fake_runner(world, monkeypatch)

    def cancel():
        with world.db.transaction() as session:
            world.ops.request_cancel(session, operation["id"])

    def conform(source, patches, output, **kwargs):
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"complete master")
        if when == "encoding":
            cancel()

    world.library.media = SimpleNamespace(conform_patches=conform)
    if when == "queued":
        cancel()
    if when == "publication":
        monkeypatch.setattr(world.library, "prepare", lambda *a: cancel())
    runner.run_once()
    registered.assert_not_called()
    with world.db.transaction() as session:
        assert world.ops.operation(session, operation["id"])["state"] == "cancelled"


def test_new_request_after_cancel_reuses_completed_master(world, reviewed, monkeypatch):
    operation = submit(world, reviewed)
    runner, _, registered = fake_runner(world, monkeypatch)
    encoded = []

    def conform(source, patches, output, **kwargs):
        encoded.append(output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(b"complete master")
        with world.db.transaction() as session:
            world.ops.request_cancel(session, operation["id"])

    world.library.media = SimpleNamespace(conform_patches=conform)
    runner.run_once()
    assert len(encoded) == 1 and encoded[0].exists()
    assert submit(world, reviewed)["state"] == "cancelled"  # replay does not restart
    next_operation = submit(world, reviewed, request_key="resume-conform")
    assert next_operation["id"] != operation["id"]
    runner.run_once()
    registered.assert_called_once()
    assert len(encoded) == 1 and not encoded[0].exists()  # success cleans staging
    with world.db.transaction() as session:
        assert world.ops.operation(session, next_operation["id"])["state"] == "succeeded"


def test_retake_after_head_pass_keeps_letters_and_preview_uses_newest_assembly(world, reviewed):
    state = edit(world, reviewed, recast.choose_version, "s0001", version="unchosen")
    shot = state["shots"][0]
    state = edit(
        world,
        state,
        recast.start_head_pass,
        "s0001",
        version=shot["versions"][0]["id"],
        character_id=shot["plan"]["performers"][0],
    )
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    settle_all(world, "shot_assembly")
    state = world.invoke(recast.recast_state, state["id"])
    old_versions = state["shots"][0]["versions"]
    assert [v["letter"] for v in old_versions] == ["A", "B"]
    state = edit(world, state, recast.retake_shot, "s0001", mode="draft")
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    settle_all(world, "shot_assembly")
    state = world.invoke(recast.recast_state, state["id"])
    versions = state["shots"][0]["versions"]
    assert versions[:2] == old_versions
    assert versions[2]["letter"] == "C"
    preview = submit(world, state, mode="preview_unreviewed", range={"start": 0, "end": 73})
    assert preview["request"]["choices"][0]["version"] == versions[2]["id"]
    revealed = world.invoke(recast.reveal_shot, state["id"], "s0001")["versions"]
    assert revealed[1]["layer"] == "head" and revealed[2]["layer"] is None
