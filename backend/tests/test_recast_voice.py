"""ADR 0016 §1, §4, §8 (P5b, P5d): a recast character's voice recordings, the build they ask for, the feed that
follows it and starts the audition, and the audition's approval, on the real Editing store (A8-A16, A29, A32-A34).

The `builds` fixture installs a fake `voice_build` module through `sys.modules` (as `test_recast_lines.voice` fakes the
dialogue analysis), with exactly the interface the real module implements: the shared build is a real Generation
operation found by its content key, created through the world's operation service and settled by the tests. The
`heard` fixture adds the fake dialogue analysis with its stem, so the feed can start an audition.
"""

import json
import sys
from hashlib import sha256
from importlib import import_module
from types import ModuleType, SimpleNamespace

import pytest
from test_boundaries import SOURCE, imported_names
from test_recast_lines import KEYS, analysed
from test_recast_lines import voice as voice  # noqa: PLC0414 - shared fixture
from test_recast_service import edit
from test_recast_service import recast_template as recast_template  # noqa: PLC0414 - shared fixture
from test_recast_service import world as world  # noqa: PLC0414 - shared fixture

from gaiavfx.api.recast import RecastDTO, VoiceDTO
from gaiavfx.application import recast, recast_edits, recast_lines, recast_voice
from gaiavfx.application import recast_projection as p
from gaiavfx.modules.generation.operations.lifecycle import OPERATION_TERMINAL, JobSpec
from gaiavfx.modules.library.assets import AssetRecord
from gaiavfx.shared.errors import ApplicationError

# Library audio recordings of the target (seconds); `picture` is the world's image.
RECORDINGS = {
    "rec_a": 12.0,
    "rec_b": 18.0,
    "rec_c": 20.0,
    "rec_d": 25.0,
    "short": 10.0,
    "long": 70.0,
}
STOPPED = frozenset({"failed", "cancelled", "needs_attention"})
TRAINING = "The voice build failed while training the voice. Retry; if it fails again, the app log has the details."


@pytest.fixture
def builds(world, monkeypatch):
    with world.db.transaction() as session:
        for asset_id, seconds in RECORDINGS.items():
            session.add(
                AssetRecord(
                    id=asset_id,
                    sha256="sha-" + asset_id,
                    name=f"{asset_id}.wav",
                    kind="audio",
                    path=asset_id,
                    mime="audio/wav",
                    info={"duration": seconds, "has_audio": True},
                )
            )
        session.add(
            AssetRecord(
                id="reel",
                sha256="sha-reel",
                name="audition.wav",
                kind="audio",
                path="reel",
                mime="audio/wav",
                info={"duration": 9.0, "has_audio": True},
                provenance=[{"type": "generation_candidate", "operation_id": "audition"}],
            )
        )
    fake = ModuleType("gaiavfx.application.voice_build")
    calls = {"start": [], "cancel": [], "retry": [], "audition": []}
    reels = {}  # audition operation id -> reel asset id

    def key(shas, attempt):
        return sha256(json.dumps(["voice_build", list(shas), 1, attempt]).encode()).hexdigest()[:12]

    def latest(session, shas):
        operation, attempt = None, 1
        while (found := world.ops.for_request(session, key(shas, attempt))) is not None:
            operation, attempt = found, attempt + 1
        return (operation, attempt - 1) if operation else (None, 1)

    def start_voice_build(session, library, sample_asset_ids, *, library_scan, attempt):
        calls["start"].append(
            {"ids": list(sample_asset_ids), "attempt": attempt, "digest": library_scan["digest"]}
        )
        shas = [library.get(session, asset_id)["sha256"] for asset_id in sample_asset_ids]
        request = {
            "samples": list(sample_asset_ids),
            "version": 1,
            "attempt": attempt,
            "library": {
                "folder": library_scan["folder"],
                "digest": library_scan["digest"],
                "clips": len(library_scan["clips"]),
                "categories": library_scan["categories"],
            },
        }
        return world.ops.create(
            session,
            kind="voice_build",
            request=request,
            request_key=key(shas, attempt),
            jobs=[
                JobSpec("analysis", "local", "fixture", step="voice_samples"),
                JobSpec("analysis", "local", "fixture", step="analysis.transcribe"),
            ],
            budget=0,
            budget_limit=None,
            awaits_review=True,
        )

    def cancel_build(session, shas):
        calls["cancel"].append(list(shas))
        operation, _ = latest(session, shas)
        if operation and operation["state"] not in OPERATION_TERMINAL:
            world.ops.request_cancel(session, operation["id"])

    def stopped_jobs(operation):
        if operation["state"] != "awaiting_review":
            return []
        return [j for j in operation["jobs"] if j["state"] in STOPPED]

    def retry_stopped(session, operation):
        calls["retry"].append(operation["id"])
        return operation

    def build_view(operation):
        if operation["state"] == "succeeded":
            state = "succeeded"
        elif operation["state"] in OPERATION_TERMINAL or stopped_jobs(operation):
            state = "failed"
        else:
            state = "running"
        return {
            "state": state,
            "stage": {"running": "material", "failed": "train"}.get(state),
            "done": sum(j["state"] == "succeeded" for j in operation["jobs"]),
            "total": 27,
            "error": TRAINING if state == "failed" else None,
        }

    def audition_view(operation):
        view = build_view(operation)
        return view | {
            "stage": "audition",
            "error": "The voice build failed while converting the audition. Retry; if it fails again, the app "
            "log has the details."
            if view["state"] == "failed"
            else None,
        }

    def manifest_artifact(operation):
        if operation["state"] != "succeeded":
            return None
        return {"artifact_id": "hidden-artifact-" + operation["id"], "sha256": "manifest-sha"}

    def voice_manifest(session, library, operation_id):
        return {
            "target_median_hz": 190.0,
            "operation_id": operation_id,
            "weights": {"artifact_id": "hidden-weights", "sha256": "weights-sha"},
            "index": {"artifact_id": "hidden-index", "sha256": "index-sha"},
        }

    def audition_reel(operation):
        if operation["state"] != "succeeded" or operation["id"] not in reels:
            return None
        return {"asset_id": reels[operation["id"]], "output_key": "reel", "kind": "audio"}

    def start_voice_audition(session, library, **kwargs):
        calls["audition"].append(kwargs)
        return world.create_op(
            session,
            "voice_audition",
            kwargs["request_key"],
            {
                "lines": [line["key"] for line in kwargs["lines"]],
                "source_median_hz": kwargs["source_median_hz"],
                "target_median_hz": kwargs["target_median_hz"],
                "voice_model": kwargs.get("voice_model"),
                "attempt": kwargs.get("attempt", 1),
            },
            steps=["voice_audition_cut", "rvc.convert", "voice_reel"],
        )

    for name, value in {
        "NAME": "voice_build",
        "AUDITION": "voice_audition",
        "BUILD_VERSION": 1,
        "STOPPED": STOPPED,
        "key": key,
        "latest": latest,
        "start_voice_build": start_voice_build,
        "cancel_build": cancel_build,
        "stopped_jobs": stopped_jobs,
        "retry_stopped": retry_stopped,
        "build_view": build_view,
        "audition_view": audition_view,
        "manifest_artifact": manifest_artifact,
        "voice_manifest": voice_manifest,
        "audition_reel": audition_reel,
        "start_voice_audition": start_voice_audition,
    }.items():
        setattr(fake, name, value)
    monkeypatch.setitem(sys.modules, fake.__name__, fake)
    return SimpleNamespace(fake=fake, calls=calls, reels=reels, key=key)


# --- helpers ----------------------------------------------------------------------------------------------------------


def checked(state):
    RecastDTO.model_validate(state)
    return state


def invoke(world, function, state, *args, **kwargs):
    return checked(
        world.invoke(function, state["id"], *args, expected_revision=state["revision"], **kwargs)
    )


def refused(world, state, function, *args, **kwargs):
    with pytest.raises(ApplicationError) as error:
        world.invoke(function, state["id"], *args, expected_revision=state["revision"], **kwargs)
    return error.value


def read(world, state, **kwargs):
    return checked(world.invoke(recast.recast_state, state["id"], **kwargs))


def character(state, character_id):
    return next(c for c in state["characters"] if c["id"] == character_id)


def stored(world, state, character_id):
    with world.db.transaction() as session:
        row = p.store.load(session, state["id"])
    return next(c for c in row["characters"] if c["id"] == character_id)


def columns(row):
    return {k: row[k] for k in recast_edits.VOICE}


def mara(world):
    """A recast whose only character is Mara."""
    state = world.create()
    cid = state["characters"][0]["id"]
    return edit(world, state, recast.patch_character, cid, label="Mara"), cid


def build_operation(world, ids, attempt=None):
    shas = ["sha-" + asset_id for asset_id in ids]
    with world.db.transaction() as session:
        operation, found = sys.modules["gaiavfx.application.voice_build"].latest(session, shas)
    assert attempt is None or found == attempt
    return operation


def drive(world, operation_id, states):
    """Move the operation's queued jobs, in order, to these end states."""
    with world.db.transaction() as session:
        for job, state in zip(world.ops.operation(session, operation_id)["jobs"], states):
            if job["state"] != "queued":
                continue
            world.ops.transition(session, job["id"], "claimed")
            world.ops.transition(session, job["id"], "running")
            if state == "succeeded":
                world.ops.transition(session, job["id"], "collecting")
            failure = {} if state == "succeeded" else {"error": "worker said X:/secret/x.wav"}
            world.ops.transition(session, job["id"], state, **failure)


def succeed(world, operation_id):
    drive(world, operation_id, ["succeeded", "succeeded"])
    with world.db.transaction() as session:
        world.ops.resume(session, operation_id, {"stage": "done"}, jobs=[])
        assert world.ops.operation(session, operation_id)["state"] == "succeeded"


def feed_writes(world, state, character_id, **values):
    """What the voice feed (P5d) writes: feed-owned columns, without a revision."""
    with world.db.transaction() as session:
        p.store.lock(session, state["id"])
        row = p.store.load(session, state["id"])
        next(c for c in row["characters"] if c["id"] == character_id).update(values)
        p.store.save(session, row)


def audition(world, state, character_id, build, *, outcome="succeeded", attempt=1, builds=None):
    """An audition the feed started (P5d), settled; a succeeded one has the reel candidate."""
    with world.db.transaction() as session:
        row = p.store.load(session, state["id"])
        stand = next(c for c in row["characters"] if c["id"] == character_id)
        key = recast_voice._audition_key(row, stand, "hidden-artifact-" + build["id"], attempt)
        operation = world.create_op(
            session,
            "voice_audition",
            key,
            {"lines": ["00000-200"], "source_median_hz": 120.0, "target_median_hz": 190.0},
            steps=["voice_audition_cut"],
        )
        world.settle(session, operation["id"], outcome)
    if builds is not None and outcome == "succeeded":
        builds.reels[operation["id"]] = "reel"
    return operation


def waiting(world, builds, state, cid, ids=("rec_a", "rec_b")):
    """A built voice whose audition succeeded and waits for the user."""
    state = invoke(world, recast_voice.build_voice, state, cid, expressive_library="")
    build = build_operation(world, list(ids))
    succeed(world, build["id"])
    audition(world, state, cid, build, builds=builds)
    feed_writes(
        world,
        state,
        cid,
        voice_model_state="waiting",
        voice_model_artifact_id="hidden-artifact-" + build["id"],
    )
    return read(world, state), build


def edits(world, state):
    with world.db.transaction() as session:
        return p.store.edits(session, state["id"], 50)


# --- A8 set_voice ------------------------------------------------------------------------------------------------------


def test_set_voice_confirms_the_users_recordings_at_once(world, builds):
    state, cid = mara(world)
    assert character(state, cid)["voice"] is None
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=["rec_a", "rec_b"])
    assert columns(stored(world, state, cid)) == {
        "voice_asset_ids": ["rec_a", "rec_b"],
        "voice_state": "confirmed",
        "voice_proposed_by": "user",
        "voice_model_state": None,
        "voice_model_artifact_id": None,
    }
    assert state["edits"][0]["summary"] == "Set Mara's voice from 2 recordings (30 s)"
    assert state["edits"][0]["command"] == "set_voice"
    assert len(edits(world, state)) == 2  # the rename and this
    view = character(state, cid)["voice"]
    assert view == {
        "samples": [
            {"asset_id": "rec_a", "name": "rec_a.wav", "seconds": 12.0},
            {"asset_id": "rec_b", "name": "rec_b.wav", "seconds": 18.0},
        ],
        "seconds": 30.0,
        "state": "confirmed",
        "proposed_by": "user",
        "model_state": None,
        "stage": None,
        "done": 0,
        "total": 0,
        "error": None,
        "reason": "Build the voice to hear an audition.",
        "audition_asset_id": None,
        "library_changed": False,
        "blocked": False,
    }
    assert builds.calls["start"] == []  # recording the samples never starts a build


@pytest.mark.parametrize(
    ("asset_ids", "status", "message"),
    [
        (["picture"], 422, "Choose Library audio recordings for the voice."),
        (["rec_a", "nothing"], 422, "Choose Library audio recordings for the voice."),
        (["short"], 422, "Choose between 15 and 60 seconds of recordings; these are 10 s."),
        (["long"], 422, "Choose between 15 and 60 seconds of recordings; these are 70 s."),
        (["rec_a", "rec_a"], 422, "Choose each recording once."),
    ],
)
def test_set_voice_refuses_before_any_write(world, builds, asset_ids, status, message):
    state, cid = mara(world)
    error = refused(world, state, recast_voice.set_voice, cid, asset_ids=asset_ids)
    assert (error.status, str(error)) == (status, message)
    after = read(world, state)
    assert after["revision"] == state["revision"] and character(after, cid)["voice"] is None


def test_reads_scan_the_expressive_folder_at_most_once_a_minute(monkeypatch, tmp_path):
    # Astra's P5 review: the film's 2 s poll scanned the folder on every read.
    scans, clock = [], [1000.0]
    monkeypatch.setattr(recast_voice, "_DIGESTS", {})
    monkeypatch.setattr(recast_voice, "_clock", lambda: clock[0])
    monkeypatch.setattr(
        recast_voice.expressive,
        "scan",
        lambda folder: scans.append(folder) or {"digest": f"d{len(scans)}"},
    )
    folder = str(tmp_path)
    assert [recast_voice.folder_digest(folder) for _ in range(3)] == ["d1", "d1", "d1"]
    clock[0] += recast_voice.DIGEST_SECONDS
    assert recast_voice.folder_digest(folder) == "d2" and len(scans) == 2
    recast_voice._remember(folder, "fresh")  # build_voice's own scan refreshes what reads see
    assert recast_voice.folder_digest(folder) == "fresh" and len(scans) == 2


@pytest.mark.parametrize(("seconds", "said"), [(30.0, "30 s"), (58.53, "58.5 s"), (15.04, "15 s")])
def test_the_journal_says_the_seconds_as_the_card_does(seconds, said):
    # The P5f trial: 58.5 s of recordings read "59 s" in the notice while the card said 58.5 s.
    journal = SimpleNamespace(before={"characters": {"c": {"label": "Mara"}}})
    summary = recast_edits.summary_for(
        "set_voice", journal, {"characters": []}, character_id="c", count=7, seconds=seconds
    )
    assert summary == f"Set Mara's voice from 7 recordings ({said})"


def test_set_voice_refuses_hidden_characters_and_stale_revisions(world, builds):
    state, cid = mara(world)
    stale = state
    hidden = edit(world, state, recast.patch_character, cid, hidden=True)
    error = refused(world, hidden, recast_voice.set_voice, cid, asset_ids=["rec_c"])
    assert error.status == 422
    error = refused(world, stale, recast_voice.set_voice, cid, asset_ids=["rec_c"])
    assert error.status == 409
    missing = refused(world, hidden, recast_voice.set_voice, "nobody", asset_ids=["rec_c"])
    assert missing.status == 404


# --- A9 clear, confirm, propose ----------------------------------------------------------------------------------------


def test_clear_confirm_and_propose(world, builds):
    state, cid = mara(world)
    assert refused(world, state, recast_voice.clear_voice, cid).status == 409
    assert refused(world, state, recast_voice.confirm_voice, cid).status == 409
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=["rec_c"])
    assert refused(world, state, recast_voice.confirm_voice, cid).status == 409  # already confirmed
    over = refused(
        world,
        state,
        recast_voice.propose_voice,
        cid,
        asset_ids=["rec_d"],
        user_message="Give Mara this voice",
    )
    assert over.status == 409
    state = invoke(world, recast_voice.clear_voice, state, cid)
    assert state["edits"][0]["summary"] == "Cleared Mara's voice recordings"
    assert columns(stored(world, state, cid)) == dict.fromkeys(recast_edits.VOICE)
    assert character(state, cid)["voice"] is None
    # An agent proposal needs the user's naming; it arrives proposed.
    unnamed = refused(
        world, state, recast_voice.propose_voice, cid, asset_ids=["rec_d"], user_message="a voice"
    )
    assert unnamed.status == 422
    state = invoke(
        world,
        recast_voice.propose_voice,
        state,
        cid,
        asset_ids=["rec_d"],
        evidence={"why": "named by the user"},
        user_message="Give Mara this voice",
    )
    assert state["edits"][0]["summary"] == "Proposed a voice for Mara (1 recording)"
    view = character(state, cid)["voice"]
    assert (view["state"], view["proposed_by"], view["reason"]) == (
        "proposed",
        "agent",
        "Confirm the proposed recordings first.",
    )
    assert refused(world, state, recast_voice.build_voice, cid, expressive_library="").status == 409
    state = invoke(world, recast_voice.confirm_voice, state, cid)
    assert state["edits"][0]["summary"] == "Confirmed Mara's voice recordings"
    assert columns(stored(world, state, cid))["voice_state"] == "confirmed"
    assert (
        columns(stored(world, state, cid))["voice_proposed_by"] == "agent"
    )  # the proposer is kept
    state = invoke(world, recast_voice.clear_voice, state, cid)
    state = invoke(
        world,
        recast_voice.propose_voice,
        state,
        cid,
        asset_ids=["rec_a", "rec_b"],
        user_message="Mara",
    )
    state = invoke(world, recast_voice.clear_voice, state, cid)
    assert state["edits"][0]["summary"] == "Rejected the proposed voice for Mara"


# --- A10 journal --------------------------------------------------------------------------------------------------------


def test_sample_edits_undo_and_redo_with_feed_owned_progress(world, builds):
    state, cid = mara(world)
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=["rec_a", "rec_b"])
    entry = state["edits"][0]
    feed_writes(
        world, state, cid, voice_model_state="making"
    )  # the feed's progress never blocks undo
    state = invoke(world, recast_edits.undo_edit, state, entry["id"])
    assert (state["edits"][0]["kind"], state["edits"][0]["summary"]) == (
        "undo",
        "Undo: Set Mara's voice from 2 recordings (30 s)",
    )
    assert character(state, cid)["voice"] is None
    assert stored(world, state, cid)["voice_asset_ids"] is None
    state = invoke(world, recast_edits.undo_edit, state, state["edits"][0]["id"])
    assert state["edits"][0]["summary"] == "Redo: Set Mara's voice from 2 recordings (30 s)"
    assert stored(world, state, cid)["voice_asset_ids"] == ["rec_a", "rec_b"]
    assert character(state, cid)["voice"]["state"] == "confirmed"
    assert builds.calls["cancel"] == []
    fresh = p.new_character({"id": "r", "characters": []})
    assert recast_edits._pristine(fresh)
    assert not recast_edits._pristine({**fresh, "voice_asset_ids": ["rec_a"]})


# --- A11 build_voice ----------------------------------------------------------------------------------------------------


def test_build_voice_starts_the_shared_build_only_when_asked(world, builds, tmp_path):
    state, cid = mara(world)
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=["rec_b", "rec_a"])
    # Reading, the feed and planning never start a build.
    for _ in range(10):
        read(world, state)
    world.invoke(recast.feed)
    world.invoke(recast.list_recasts)
    state = invoke(world, recast.plan_recast, read(world, state), range=None)
    assert builds.calls["start"] == []
    state = invoke(world, recast_voice.build_voice, state, cid, expressive_library="")
    assert builds.calls["start"] == [{"ids": ["rec_b", "rec_a"], "attempt": 1, "digest": ""}]
    view = character(state, cid)["voice"]
    assert (view["model_state"], view["stage"], view["done"], view["total"], view["reason"]) == (
        "making",
        "material",
        0,
        27,
        None,
    )
    running = refused(world, state, recast_voice.build_voice, cid, expressive_library="")
    assert (running.status, str(running)) == (409, "This voice is already being built.")
    build = build_operation(world, ["rec_b", "rec_a"], attempt=1)
    drive(world, build["id"], ["succeeded", "failed"])
    stopped = refused(world, state, recast_voice.build_voice, cid, expressive_library="")
    assert (stopped.status, str(stopped)) == (409, "The last build stopped; retry it.")
    view = character(read(world, state), cid)["voice"]
    assert (view["stage"], view["error"]) == ("train", TRAINING)
    state = invoke(world, recast_voice.retry_voice, state, cid, expressive_library="")
    assert builds.calls["retry"] == [build["id"]]
    with world.db.transaction() as session:
        # The stopped job's replacement (what retry_stopped appends) succeeds; the build finishes.
        world.ops.resume(session, build["id"], {"stage": "done"}, jobs=[])
        assert world.ops.operation(session, build["id"])["state"] == "partial"
    # A partial build settled without success: the next attempt.
    state = invoke(world, recast_voice.build_voice, state, cid, expressive_library="")
    assert builds.calls["start"][-1]["attempt"] == 2
    second = build_operation(world, ["rec_b", "rec_a"], attempt=2)
    succeed(world, second["id"])
    built = refused(world, state, recast_voice.build_voice, cid, expressive_library="")
    assert (built.status, str(built)) == (409, "This voice is already built.")
    folder = tmp_path / "expressive"
    (folder / "screams").mkdir(parents=True)
    (folder / "screams" / "a.wav").write_bytes(b"scream")
    changed = refused(world, state, recast_voice.build_voice, cid, expressive_library=str(folder))
    assert (changed.status, str(changed)) == (
        409,
        "The expressive recordings changed since this voice was built. Build again to use them.",
    )
    assert character(read(world, state, expressive_library=str(folder)), cid)["voice"][
        "library_changed"
    ]
    assert not character(read(world, state, expressive_library=""), cid)["voice"]["library_changed"]
    assert not character(read(world, state), cid)["voice"]["library_changed"]
    state = invoke(
        world, recast_voice.build_voice, state, cid, expressive_library=str(folder), rebuild=True
    )
    assert builds.calls["start"][-1]["attempt"] == 3
    assert builds.calls["start"][-1]["digest"] != ""
    assert character(state, cid)["voice"]["model_state"] == "making"


def test_a_failed_build_gets_the_next_attempt_and_refusals_come_first(world, builds):
    state, cid = mara(world)
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=["rec_c"])
    state = invoke(world, recast_voice.build_voice, state, cid, expressive_library="")
    build = build_operation(world, ["rec_c"], attempt=1)
    drive(world, build["id"], ["failed", "failed"])
    view = character(read(world, state), cid)["voice"]
    assert view["error"] == TRAINING and "secret" not in json.dumps(view)
    state = invoke(world, recast_voice.build_voice, state, cid, expressive_library="")
    assert [c["attempt"] for c in builds.calls["start"]] == [1, 2]
    assert build_operation(world, ["rec_c"], attempt=2)["id"] != build["id"]
    hidden = edit(world, state, recast.patch_character, cid, hidden=True)
    error = refused(world, hidden, recast_voice.build_voice, cid, expressive_library="")
    assert (error.status, str(error)) == (422, "A hidden character has no voice built.")
    assert character(hidden, cid)["voice"]["reason"] == "A hidden character has no voice built."
    # Hiding leaves the voice columns alone (the feed skips hidden characters).
    assert stored(world, hidden, cid)["voice_asset_ids"] == ["rec_c"]
    assert len(builds.calls["start"]) == 2


def test_the_audition_waits_for_three_confirmed_lines(world, builds, voice):
    state, a, _b = analysed(world)
    state = invoke(world, recast_voice.set_voice, state, a, asset_ids=["rec_a", "rec_b"])
    state = invoke(world, recast_voice.build_voice, state, a, expressive_library="")
    succeed(world, build_operation(world, ["rec_a", "rec_b"])["id"])
    view = character(read(world, state), a)["voice"]
    assert view["reason"] == "Confirm at least 3 of Mara's lines to hear an audition."
    state = invoke(world, recast_lines.confirm_lines, state, character_id=a)
    assert character(state, a)["lines_confirmed"] == 4
    assert character(state, a)["voice"]["reason"] is None


# --- A12 cancel on change -----------------------------------------------------------------------------------------------


def test_changing_samples_cancels_only_an_unshared_running_build(world, builds):
    state, cid = mara(world)
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=["rec_a", "rec_b"])
    state = invoke(world, recast_voice.build_voice, state, cid, expressive_library="")
    feed_writes(world, state, cid, voice_model_state="making", voice_model_artifact_id="x")
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=["rec_c"])
    assert builds.calls["cancel"] == [["sha-rec_a", "sha-rec_b"]]
    assert build_operation(world, ["rec_a", "rec_b"])["cancel_requested"]
    assert columns(stored(world, state, cid))["voice_model_state"] is None  # the samples changed
    assert columns(stored(world, state, cid))["voice_model_artifact_id"] is None
    # The same recordings again change nothing and cancel nothing.
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=["rec_c"])
    assert len(builds.calls["cancel"]) == 1
    # A second recast of the film with a confirmed character on the same recordings keeps the build alive.
    other = world.create("second")
    other_cid = other["characters"][0]["id"]
    other = invoke(world, recast_voice.set_voice, other, other_cid, asset_ids=["rec_c"])
    state = invoke(world, recast_voice.build_voice, read(world, state), cid, expressive_library="")
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=["rec_d"])
    state = invoke(world, recast_voice.clear_voice, state, cid)
    assert builds.calls["cancel"] == [["sha-rec_a", "sha-rec_b"], ["sha-rec_d"]]
    assert not build_operation(world, ["rec_c"])["cancel_requested"]
    # Clearing the second recast's voice, now unshared, cancels it; undo never cancels anything.
    other = invoke(world, recast_voice.clear_voice, other, other_cid)
    assert builds.calls["cancel"][-1] == ["sha-rec_c"]
    count = len(builds.calls["cancel"])
    other = invoke(world, recast_edits.undo_edit, other, other["edits"][0]["id"])
    state = invoke(world, recast_edits.undo_edit, state, state["edits"][0]["id"])
    state = invoke(world, recast_edits.undo_edit, state, state["edits"][2]["id"])  # set rec_d
    assert stored(world, state, cid)["voice_asset_ids"] == ["rec_c"]
    assert len(builds.calls["cancel"]) == count


# --- A13 merge ----------------------------------------------------------------------------------------------------------


def test_a_merge_carries_a_voice_to_a_character_without_one(world, builds):
    state = world.create()
    state = edit(world, state, recast.move_tracks, track_ids=["t3", "t4"], to="new")
    a, b = (c["id"] for c in state["characters"])
    state = invoke(world, recast_voice.set_voice, state, a, asset_ids=["rec_a", "rec_b"])
    feed_writes(world, state, a, voice_model_state="waiting", voice_model_artifact_id="m")
    state = read(world, state)
    a_row, b_row = stored(world, state, a), stored(world, state, b)
    merged = invoke(world, recast.merge_characters, state, into=b, sources=[a])
    assert columns(stored(world, merged, b)) == columns(a_row)
    restored = invoke(world, recast_edits.undo_edit, merged, merged["edits"][0]["id"])
    assert columns(stored(world, restored, a)) == columns(a_row)
    assert stored(world, restored, b)["voice_asset_ids"] is None
    assert stored(world, restored, b)["voice_state"] is None
    assert b_row["voice_asset_ids"] is None
    # Into a character with its own voice: it keeps it, the source's goes with the source.
    state = invoke(world, recast_voice.set_voice, restored, b, asset_ids=["rec_c"])
    merged = invoke(world, recast.merge_characters, state, into=b, sources=[a])
    assert stored(world, merged, b)["voice_asset_ids"] == ["rec_c"]
    restored = invoke(world, recast_edits.undo_edit, merged, merged["edits"][0]["id"])
    assert stored(world, restored, a)["voice_asset_ids"] == ["rec_a", "rec_b"]
    assert stored(world, restored, b)["voice_asset_ids"] == ["rec_c"]
    assert builds.calls["cancel"] == []


# --- A14 approval (the routes: test_recast_api.test_voice_routes) ----------------------------------------------------------------------------------------


def test_approve_and_reject_need_a_waiting_audition(world, builds):
    state, cid = mara(world)
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=["rec_a", "rec_b"])
    for command in (recast_voice.approve_voice, recast_voice.reject_voice):
        assert refused(world, state, command, cid).status == 409
    state, build = waiting(world, builds, state, cid)
    view = character(state, cid)["voice"]
    assert (view["model_state"], view["audition_asset_id"], view["stage"]) == (
        "waiting",
        "reel",
        "audition",
    )
    builds.reels.clear()  # the reel candidate is gone
    missing = refused(world, state, recast_voice.approve_voice, cid)
    assert (missing.status, str(missing)) == (409, "Audition provenance is missing.")
    state = read(world, state)
    with world.db.transaction() as session:
        row = p.store.load(session, state["id"])
        stand = next(c for c in row["characters"] if c["id"] == cid)
        audition_op = world.ops.for_request(
            session, recast_voice._audition_key(row, stand, "hidden-artifact-" + build["id"], 1)
        )
    builds.reels[audition_op["id"]] = "reel"
    state = invoke(world, recast_voice.approve_voice, state, cid)
    assert character(state, cid)["voice"]["model_state"] == "approved"
    with world.db.transaction() as session:
        provenance = world.library.get(session, "reel")["provenance"]
    assert provenance[0]["type"] == "generation_candidate"
    approval = provenance[-1]
    assert {k: approval[k] for k in approval if k != "approved_at"} == {
        "type": "generated_voice",
        "subject": "recast_voice",
        "recast_id": state["id"],
        "character_id": cid,
        "sample_assets": ["rec_a", "rec_b"],
        "voice_model_artifact_id": "hidden-artifact-" + build["id"],
        "build_operation": build["id"],
        "audition_operation": audition_op["id"],
        "source_median_hz": 120.0,
        "target_median_hz": 190.0,
        "lines": ["00000-200"],
        "approved_by": "user",
        "recast_revision": state["revision"],
    }
    assert refused(world, state, recast_voice.approve_voice, cid).status == 409  # approved now
    feed_writes(world, state, cid, voice_model_state="waiting")
    state = invoke(world, recast_voice.reject_voice, read(world, state), cid)
    assert character(state, cid)["voice"]["model_state"] == "rejected"
    # Approval and the build are the feed's, never journaled.
    assert state["edits"][0]["command"] == "set_voice"


# --- A15 blind-safe -----------------------------------------------------------------------------------------------------


def test_the_voice_view_is_blind_safe(world, builds, tmp_path):
    state, cid = mara(world)
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=["rec_a", "rec_b"])
    folder = tmp_path / "expressive-folder"
    (folder / "screams").mkdir(parents=True)
    (folder / "screams" / "a.wav").write_bytes(b"scream")
    state = invoke(world, recast_voice.build_voice, state, cid, expressive_library=str(folder))
    build = build_operation(world, ["rec_a", "rec_b"])
    assert build["request"]["library"]["folder"] == str(folder)  # the fake's request holds it
    for outcome in ("running", "stopped", "succeeded"):
        if outcome == "stopped":
            drive(world, build["id"], ["succeeded", "failed"])
        if outcome == "succeeded":
            with world.db.transaction() as session:
                world.ops.resume(session, build["id"], {"stage": "done"}, jobs=[])
            state = invoke(world, recast_voice.build_voice, state, cid, expressive_library="")
            build = build_operation(world, ["rec_a", "rec_b"], attempt=2)
            succeed(world, build["id"])
            audition_op = audition(world, state, cid, build, builds=builds)
            feed_writes(world, state, cid, voice_model_state="waiting")
        page = read(world, state, expressive_library=str(folder))
        text = json.dumps(page)
        view = character(page, cid)["voice"]
        assert build["id"] not in text and "hidden-artifact" not in text, outcome
        assert str(folder) not in text and json.dumps(str(folder))[1:-1] not in text, outcome
        assert "secret" not in text and "worker said" not in text, outcome
        ids = {s["asset_id"] for s in view["samples"]} | {view["audition_asset_id"]}
        assert ids <= {"rec_a", "rec_b", "reel", None}
    assert view["audition_asset_id"] == "reel" and audition_op["id"] not in text
    assert set(view) == set(VoiceDTO.model_fields)


# --- A16 boundaries -----------------------------------------------------------------------------------------------------


def test_recast_voice_keeps_its_boundaries():
    names = set(imported_names(SOURCE / "application" / "recast_voice.py"))
    assert not any(
        name.startswith(("sqlalchemy", "gaiavfx.api")) or ".persistence" in name for name in names
    ), names
    # The build and dialogue analysis modules are reached at call time, so the test world can replace them.
    assert "gaiavfx.application.voice_build" not in names
    assert "gaiavfx.application.dialogue_analysis" not in names
    source = (SOURCE / "application" / "recast_voice.py").read_text("utf-8")
    assert "start_voice_build(" in source
    for file in (SOURCE / "application").glob("recast*.py"):
        if file.name != "recast_voice.py":
            assert "start_voice_build(" not in file.read_text("utf-8"), file.name
    # The feed reaches the build only through recast_voice, and starts an audition only through start_audition.
    feed_source = (SOURCE / "application" / "recast_feed.py").read_text("utf-8")
    assert "start_audition(" in feed_source and "start_voice_audition(" not in feed_source
    assert not any(
        "voice_build" in name for name in imported_names(SOURCE / "application" / "recast_feed.py")
    )
    # The shared dialogue analysis stays recast-free with its new stem helper (A34).
    assert not any(
        name.startswith(("gaiavfx.application.recast", "gaiavfx.modules.editing"))
        for name in imported_names(SOURCE / "application" / "dialogue_analysis.py")
    )


def test_the_dialogue_stem_is_the_succeeded_analysis_separated_dialogue():
    analysis = import_module("gaiavfx.application.dialogue_analysis")
    separate = {
        "step": "analysis.separate",
        "outputs": [
            {"output_key": "dialogue", "artifact_id": "stem", "sha256": "stem-sha", "info": {}},
            {"output_key": "remainder", "artifact_id": "rest", "sha256": "rest-sha"},
        ],
    }
    operation = {
        "kind": "dialogue_analysis",
        "state": "succeeded",
        "jobs": [{"step": "dialogue_audio", "outputs": []}, separate],
    }
    assert analysis.stem_artifact(operation) == {"artifact_id": "stem", "sha256": "stem-sha"}
    for unfinished in (
        None,
        operation | {"state": "awaiting_review"},
        operation | {"state": "failed"},
        operation | {"kind": "voice_build"},
        operation | {"jobs": [separate, separate]},
    ):
        with pytest.raises(ApplicationError) as error:
            analysis.stem_artifact(unfinished)
        assert error.value.status == 409


# --- P5d: the voice feed, the audition and approval read from the reel -------------------------------------------------

STEM = {"artifact_id": "hidden-stem", "sha256": "stem-sha"}
WAITING_FOR_LINES = "Confirm at least 3 of Mara's lines to hear an audition."
AUDITION_STOPPED = "The voice build failed while converting the audition. Retry; if it fails again, the app log has the details."
HIDDEN_IDS = ("hidden-artifact", "hidden-stem", "hidden-weights", "hidden-index")


@pytest.fixture
def heard(builds, voice):
    """The voice world with the film's dialogue analysis, whose fake also names its stem (the audition cuts from it)."""
    voice.fake.stem_artifact = lambda operation: dict(STEM)
    return builds


def feed(world):
    return world.invoke(recast.feed)


def auditions(world, builds):
    """The audition operations the feed or a retry started, in order."""
    with world.db.transaction() as session:
        return [
            world.ops.for_request(session, call["request_key"]) for call in builds.calls["audition"]
        ]


def settle_audition(world, builds, operation, outcome="succeeded"):
    with world.db.transaction() as session:
        world.settle(session, operation["id"], outcome)
    if outcome == "succeeded":
        builds.reels[operation["id"]] = "reel"


def confirm(world, state, cid, keys):
    return invoke(
        world, recast_lines.confirm_lines, state, character_id=cid, segment_keys=list(keys)
    )


def built(world, state, cid, ids=("rec_a", "rec_b")):
    """The character's recordings set and their shared build asked for and succeeded."""
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=list(ids))
    state = invoke(world, recast_voice.build_voice, state, cid, expressive_library="")
    build = build_operation(world, list(ids))
    succeed(world, build["id"])
    return read(world, state), build


def model(world, state, cid):
    row = stored(world, state, cid)
    return row["voice_model_state"], row["voice_model_artifact_id"]


def test_the_feed_follows_the_build_and_starts_the_audition_once(world, heard, voice):
    """A29, and the audition's plain-data request (A31's recast side)."""
    builds = heard
    voice.report["whisper_spans"] = [
        [3.5, 3.9],
        [9.2, 10.4],
    ]  # a whispered stretch inside Mara's third line
    state, a, _b = analysed(world)
    state = invoke(world, recast_voice.set_voice, state, a, asset_ids=["rec_a", "rec_b"])
    feed(world)
    assert model(world, state, a) == (None, None)  # no build: the feed leaves the columns
    state = invoke(world, recast_voice.build_voice, state, a, expressive_library="")
    build = build_operation(world, ["rec_a", "rec_b"], attempt=1)
    feed(world)
    assert model(world, state, a) == ("making", None)
    drive(world, build["id"], ["succeeded", "failed"])  # a stopped stage awaits its retry
    feed(world)
    assert model(world, state, a) == ("failed", None)
    assert character(read(world, state), a)["voice"]["error"] == TRAINING
    with world.db.transaction() as session:
        world.ops.resume(session, build["id"], {"stage": "done"}, jobs=[])  # settles partial
    feed(world)
    assert model(world, state, a) == ("failed", None)
    state = invoke(world, recast_voice.build_voice, read(world, state), a, expressive_library="")
    second = build_operation(world, ["rec_a", "rec_b"], attempt=2)
    feed(world)
    assert model(world, state, a) == ("making", None)
    succeed(world, second["id"])
    manifest = "hidden-artifact-" + second["id"]
    # Two confirmed lines: the manifest is recorded, nothing starts, the view says what the audition waits for.
    state = confirm(world, state, a, KEYS[:2])
    assert feed(world) is False
    assert model(world, state, a) == ("making", manifest)
    assert builds.calls["audition"] == []
    view = character(read(world, state), a)["voice"]
    assert (view["model_state"], view["reason"], view["blocked"]) == (
        "making",
        WAITING_FOR_LINES,
        False,
    )
    # A third confirmed line: the feed starts the audition, once.
    state = confirm(world, state, a, KEYS[2:3])
    assert feed(world) is True
    [call] = builds.calls["audition"]
    [audition_op] = auditions(world, builds)
    assert audition_op["kind"] == "voice_audition"
    assert model(world, state, a) == ("making", manifest)
    view = character(read(world, state), a)["voice"]
    assert (view["stage"], view["reason"]) == ("audition", None)
    assert feed(world) is False  # a second pass finds it and starts nothing
    assert len(builds.calls["audition"]) == 1
    assert [c["attempt"] for c in builds.calls["start"]] == [
        1,
        2,
    ]  # only build_voice started builds
    # The audition gets plain data: the rule's picks (highest median, then the loudest, then the nearest the
    # median), their integer spans as the lines show them, whispered stretches relative to each clip, both medians.
    with world.db.transaction() as session:
        row = p.store.load(session, state["id"])
        stand = next(c for c in row["characters"] if c["id"] == a)
        assert call["request_key"] == recast_voice._audition_key(row, stand, manifest, 1)
    assert [line["key"] for line in call["lines"]] == [KEYS[2], KEYS[0], KEYS[1]]
    shown = {
        line["segment_key"]: line
        for line in world.invoke(recast_lines.lines_state, state["id"])["lines"]
    }
    for line in call["lines"]:
        assert (line["start_sample"], line["end_sample"]) == (
            shown[line["key"]]["start_sample"],
            shown[line["key"]]["end_sample"],
        )
        assert type(line["start_sample"]) is int and type(line["end_sample"]) is int
    third = call["lines"][0]
    begin, end = third["start_sample"] / 48000, third["end_sample"] / 48000
    assert third["whisper_spans"] == [
        [pytest.approx(3.5 - begin, abs=1e-6), pytest.approx(min(3.9, end) - begin, abs=1e-6)]
    ]
    assert [line["whisper_spans"] for line in call["lines"][1:]] == [[], []]
    assert {
        k: call[k]
        for k in ("source_median_hz", "target_median_hz", "sample_rate", "voice_model", "attempt")
    } == {
        "source_median_hz": 101.0,  # the median of the confirmed lines' 100, 101 and 102 Hz
        "target_median_hz": 190.0,  # the build's manifest
        "sample_rate": 48000,
        "voice_model": manifest,
        "attempt": 1,
    }
    assert call["stem"] == STEM
    assert call["weights"] == {"artifact_id": "hidden-weights", "sha256": "weights-sha"}
    assert call["index"] == {"artifact_id": "hidden-index", "sha256": "index-sha"}


def test_hidden_or_proposed_voices_never_advance(world, heard):
    builds = heard
    state, a, b = analysed(world)
    state = confirm(world, state, a, KEYS[:4])
    state = invoke(world, recast_voice.set_voice, state, b, asset_ids=["rec_c"])
    state = invoke(world, recast_voice.build_voice, state, b, expressive_library="")
    feed(world)
    assert model(world, state, b) == ("making", None)
    state = edit(world, read(world, state), recast.patch_character, b, hidden=True)
    succeed(world, build_operation(world, ["rec_c"])["id"])
    # The same recordings proposed for Mara: a proposal is not the user's voice yet.
    state = invoke(
        world,
        recast_voice.propose_voice,
        state,
        a,
        asset_ids=["rec_c"],
        user_message="Give Mara this voice",
    )
    for _ in range(2):
        feed(world)
    assert model(world, state, b) == ("making", None)  # hidden: left as it was
    assert model(world, state, a) == (None, None)
    assert builds.calls["audition"] == []
    page = read(world, state)
    assert character(page, a)["voice"]["blocked"] and character(page, b)["voice"]["blocked"]


def test_approval_is_read_from_the_reel_and_survives_the_feed_an_undo_but_not_a_rebuild(
    world, heard, tmp_path
):
    """A32's feed parts, approval read from evidence and the blind page (A34)."""
    builds = heard
    state, a, _b = analysed(world)
    state = confirm(world, state, a, KEYS[:4])
    state, build = built(world, state, a)
    manifest = "hidden-artifact-" + build["id"]
    feed(world)
    [first] = auditions(world, builds)
    settle_audition(world, builds, first)
    feed(world)
    page = read(world, state)
    view = character(page, a)["voice"]
    assert (view["model_state"], view["audition_asset_id"], view["error"]) == (
        "waiting",
        "reel",
        None,
    )
    state = invoke(world, recast_voice.approve_voice, page, a)
    with world.db.transaction() as session:
        approval = world.library.get(session, "reel")["provenance"][-1]
    assert {k: approval[k] for k in ("type", "audition_operation", "voice_model_artifact_id")} == {
        "type": "generated_voice",
        "audition_operation": first["id"],
        "voice_model_artifact_id": manifest,
    }
    assert sorted(approval["lines"]) == sorted(KEYS[:4])
    assert (approval["source_median_hz"], approval["target_median_hz"]) == (101.5, 190.0)
    feed(world)
    assert model(world, state, a) == ("approved", manifest)  # the approval survives a feed pass
    # Other recordings reset the model; undoing that brings the recordings back, and the approval with them, although
    # undo leaves the feed's columns alone.
    state = invoke(
        world, recast_voice.set_voice, read(world, state), a, asset_ids=["rec_c", "rec_d"]
    )
    feed(world)
    assert model(world, state, a) == (None, None)
    state = invoke(world, recast_edits.undo_edit, state, state["edits"][0]["id"])
    assert model(world, state, a) == (None, None)
    feed(world)
    assert model(world, state, a) == ("approved", manifest)
    assert len(builds.calls["audition"]) == 1  # nothing ran again
    page = read(world, state)
    assert character(page, a)["voice"]["model_state"] == "approved"
    text = json.dumps(page)
    assert not any(name in text for name in HIDDEN_IDS), text
    assert first["id"] not in text and build["id"] not in text
    # A rebuild is a new voice model: its audition starts and waits, the old approval names the old model.
    folder = tmp_path / "expressive"
    (folder / "screams").mkdir(parents=True)
    (folder / "screams" / "a.wav").write_bytes(b"scream")
    state = invoke(
        world, recast_voice.build_voice, page, a, expressive_library=str(folder), rebuild=True
    )
    rebuilt = build_operation(world, ["rec_a", "rec_b"], attempt=2)
    feed(world)
    assert model(world, state, a) == ("making", None)
    succeed(world, rebuilt["id"])
    feed(world)
    assert model(world, state, a) == ("making", "hidden-artifact-" + rebuilt["id"])
    second = auditions(world, builds)[-1]
    assert second["id"] != first["id"]
    assert builds.calls["audition"][-1]["voice_model"] == "hidden-artifact-" + rebuilt["id"]
    settle_audition(world, builds, second)
    feed(world)
    assert model(world, state, a) == ("waiting", "hidden-artifact-" + rebuilt["id"])


def test_a_run_error_does_not_hold_back_the_voice(world, heard):
    """The P5f trial: the trial film's run had been paused with a run error since 8 October, so the feed skipped the
    recast and the built voice's audition never started (the card read "making" with no reason)."""
    builds = heard
    state, a, _b = analysed(world)
    state = confirm(world, state, a, KEYS[:3])
    state, build = built(world, state, a)
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        stored.update(run_state="paused", run_reason="Run error: an old failure")
        p.store.save(session, stored)
    feed(world)
    assert len(builds.calls["audition"]) == 1
    assert model(world, state, a) == ("making", "hidden-artifact-" + build["id"])
    run = read(world, state)["run"]
    assert (run["state"], run["reason"]) == ("paused", "Run error: an old failure")
    feed(world)  # nothing changed: no second audition
    assert len(builds.calls["audition"]) == 1


def test_undo_to_recordings_without_a_build_drops_another_voices_approval(world, heard):
    """Astra's P5 review: recordings A (never built), replaced by B, B built and approved, then the replacement
    undone. Undo keeps the feed-owned columns, so A read approved under B's model until the feed cleared them."""
    builds = heard
    state, a, _b = analysed(world)
    state = confirm(world, state, a, KEYS[:3])
    state = invoke(world, recast_voice.set_voice, state, a, asset_ids=["rec_c", "rec_d"])
    state, build = built(world, state, a)
    feed(world)
    [first] = auditions(world, builds)
    settle_audition(world, builds, first)
    feed(world)
    state = invoke(world, recast_voice.approve_voice, read(world, state), a)
    assert model(world, state, a) == ("approved", "hidden-artifact-" + build["id"])
    replacement = state["edits"][0]
    assert replacement["summary"] == "Set Mara's voice from 2 recordings (30 s)"
    state = invoke(world, recast_edits.undo_edit, state, replacement["id"])
    assert model(world, state, a) == (
        "approved",
        "hidden-artifact-" + build["id"],
    )  # undo leaves them
    feed(world)
    assert model(world, state, a) == (None, None)
    view = character(read(world, state), a)["voice"]
    assert [s["asset_id"] for s in view["samples"]] == ["rec_c", "rec_d"]
    assert (view["model_state"], view["blocked"], view["audition_asset_id"]) == (None, False, None)


def test_a_rejection_persists_until_the_retry_auditions_the_lines_confirmed_now(world, heard):
    builds = heard
    state, a, _b = analysed(world)
    state = confirm(world, state, a, KEYS[:3])
    state, build = built(world, state, a)
    feed(world)
    [first] = auditions(world, builds)
    settle_audition(world, builds, first)
    feed(world)
    state = invoke(world, recast_voice.reject_voice, read(world, state), a)
    feed(world)
    assert (
        model(world, state, a)[0] == "rejected"
    )  # the human's decision on this voice model persists
    state = confirm(world, state, a, KEYS[3:4])
    state = invoke(world, recast_voice.retry_voice, state, a, expressive_library="")
    assert character(state, a)["voice"]["model_state"] == "making"
    call = builds.calls["audition"][-1]
    assert call["attempt"] == 2 and sorted(line["key"] for line in call["lines"]) == sorted(
        KEYS[:4]
    )
    assert call["source_median_hz"] == 101.5  # the lines confirmed now
    feed(world)
    second = auditions(world, builds)[-1]
    assert second["id"] != first["id"] and model(world, state, a)[0] == "making"
    settle_audition(world, builds, second)
    feed(world)
    assert model(world, state, a)[0] == "waiting"
    # A rejection leaves no evidence: undoing a sample change brings the voice back waiting, not rejected.
    state = invoke(world, recast_voice.reject_voice, read(world, state), a)
    state = invoke(world, recast_voice.set_voice, state, a, asset_ids=["rec_c", "rec_d"])
    state = invoke(world, recast_edits.undo_edit, state, state["edits"][0]["id"])
    feed(world)
    assert model(world, state, a) == ("waiting", "hidden-artifact-" + build["id"])


def test_retry_voice_resumes_rebuilds_or_auditions_again_and_refuses_while_running(
    world, heard, voice, caplog
):
    """A33, and an audition the feed could not start (no confirmed line has a measured pitch)."""
    builds = heard
    for item in voice.report["speakers"].values():
        item["median_hz"] = None
    state, a, _b = analysed(world)
    state = confirm(world, state, a, KEYS[:3])
    state = invoke(world, recast_voice.set_voice, state, a, asset_ids=["rec_a", "rec_b"])
    state = invoke(world, recast_voice.build_voice, state, a, expressive_library="")
    build = build_operation(world, ["rec_a", "rec_b"])
    running = refused(world, state, recast_voice.retry_voice, a, expressive_library="")
    assert (running.status, str(running)) == (409, "Nothing to retry.")
    drive(world, build["id"], ["succeeded", "failed"])
    state = invoke(world, recast_voice.retry_voice, state, a, expressive_library="")
    assert builds.calls["retry"] == [build["id"]]  # a stopped build resumes in place
    with world.db.transaction() as session:
        world.ops.resume(session, build["id"], {"stage": "done"}, jobs=[])  # settles partial
    state = invoke(world, recast_voice.retry_voice, state, a, expressive_library="")
    assert [c["attempt"] for c in builds.calls["start"]] == [
        1,
        2,
    ]  # a terminal build: the next attempt
    second = build_operation(world, ["rec_a", "rec_b"], attempt=2)
    succeed(world, second["id"])
    # The feed cannot start the audition: it reads failed, logs once and does not try again on every pass.
    with caplog.at_level("ERROR"):
        feed(world)
        feed(world)
    assert sum("Voice feed failed" in r.getMessage() for r in caplog.records) == 1
    assert model(world, state, a) == ("failed", "hidden-artifact-" + second["id"])
    assert builds.calls["audition"] == []
    view = character(read(world, state), a)["voice"]
    assert (view["stage"], view["error"]) == ("audition", recast_voice.AUDITION_FAILED)
    # The retry names the cause; once a line has a pitch, it starts the audition.
    nothing = refused(world, state, recast_voice.retry_voice, a, expressive_library="")
    assert (nothing.status, str(nothing)) == (
        409,
        "None of Mara's confirmed lines has a measured pitch. Confirm a spoken line, then retry.",
    )
    for key, item in voice.report["speakers"].items():
        item["median_hz"] = 100.0 + KEYS.index(key)
    state = invoke(world, recast_voice.retry_voice, state, a, expressive_library="")
    assert character(state, a)["voice"]["model_state"] == "making"
    [first] = auditions(world, builds)
    assert builds.calls["audition"][0]["attempt"] == 1
    feed(world)
    assert model(world, state, a)[0] == "making"
    running = refused(world, state, recast_voice.retry_voice, a, expressive_library="")
    assert (running.status, str(running)) == (409, "Nothing to retry.")  # the audition runs
    settle_audition(world, builds, first, "failed")
    feed(world)
    view = character(read(world, state), a)["voice"]
    assert (view["model_state"], view["error"]) == ("failed", AUDITION_STOPPED)
    state = invoke(world, recast_voice.retry_voice, state, a, expressive_library="")
    assert builds.calls["audition"][-1]["attempt"] == 2
    assert character(state, a)["voice"]["model_state"] == "making"


def test_blocked_names_what_build_voice_refuses(world, builds):
    state, cid = mara(world)
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=["rec_a", "rec_b"])
    view = character(state, cid)["voice"]
    assert (view["blocked"], view["reason"]) == (False, recast_voice.NOT_BUILT)  # the next step
    state = invoke(world, recast_voice.build_voice, state, cid, expressive_library="")
    succeed(world, build_operation(world, ["rec_a", "rec_b"])["id"])
    view = character(read(world, state), cid)["voice"]
    assert (view["blocked"], view["reason"]) == (False, WAITING_FOR_LINES)  # waiting, not blocked
    # Recordings outside the bounds (as an older store could hold them), hidden, proposed: blocked.
    feed_writes(world, state, cid, voice_asset_ids=["short"])
    view = character(read(world, state), cid)["voice"]
    assert view["blocked"] and view["reason"] == (
        "Choose between 15 and 60 seconds of recordings; these are 10 s."
    )
    assert refused(world, state, recast_voice.build_voice, cid, expressive_library="").status == 422
    state = invoke(world, recast_voice.set_voice, state, cid, asset_ids=["rec_c"])
    hidden = edit(world, state, recast.patch_character, cid, hidden=True)
    assert character(hidden, cid)["voice"]["blocked"]
    state = edit(world, hidden, recast.patch_character, cid, hidden=False)
    assert not character(state, cid)["voice"]["blocked"]
    state = invoke(world, recast_voice.clear_voice, state, cid)
    state = invoke(
        world,
        recast_voice.propose_voice,
        state,
        cid,
        asset_ids=["rec_d"],
        user_message="Give Mara this voice",
    )
    assert character(state, cid)["voice"]["blocked"]
    state = invoke(world, recast_voice.confirm_voice, state, cid)
    assert not character(state, cid)["voice"]["blocked"]
