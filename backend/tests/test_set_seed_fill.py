"""A seed that fills its stage's unseen space (ADR 0020), through the durable runner with test_set_seed's fakes: the
synthetic room's still camera (the scene's second shot), the real worldbuilder worker for the atlas, the plan, the
memory, the stills and their consolidation, a fake ComfyUI for the take's geometry, the probes and the stills'
geometry, and fake splat fits. No model, no GPU."""

import sys

from set_scene import FAKE
from test_set_seed import OPERATIONS, operation, transact
from test_set_seed import scene as scene  # noqa: PLC0414 - pytest fixture export

from gaiavfx.application import set_fill, set_seed, sets
from gaiavfx.application.engine_jobs import EngineCommand


def drain(run, rounds=400):
    for _ in range(rounds):
        if not run.run_once():
            break


def start(s, shot=1, **changes):
    return transact(
        s.workspace,
        set_seed.start_seed,
        s.workspace.library,
        segment_id=s.segment["id"],
        shot=shot,
        name="Box room filled",
        use_for_place=False,
        **changes,
    )


def stages(op, name):
    return [job for job in op["jobs"] if set_fill.stage(job).get("name") == name]


def test_the_preview_says_what_filling_adds_and_costs(scene):
    s = scene
    preview = transact(
        s.workspace, set_seed.seed_preview, s.workspace.library, segment_id=s.segment["id"], shot=1
    )
    assert (preview["fill_unseen"], preview["renders"], preview["refusal"]) == (True, 6, None)
    assert preview["estimate"].startswith("Up to 6 renders, about 15 to 25 minutes of GPU work")
    assert (
        "then up to 2 rounds of probes fill in what the take never saw around this shot"
        in preview["estimate"]
    )
    assert "stands still or only turns builds this way too" in preview["what"]
    plain = transact(
        s.workspace,
        set_seed.seed_preview,
        s.workspace.library,
        segment_id=s.segment["id"],
        shot=1,
        fill_unseen=False,
    )
    assert (plain["renders"], plain["estimate"][:15]) == (0, "About 3 minutes")
    assert "is refused" in plain["what"]


def test_a_still_take_fills_its_unseen_space_and_becomes_a_stage(scene):
    s = scene
    started = start(s)
    assert started["budget_reserved"] == 6 and started["request"]["fill"]["rounds"]["renders"] == 6
    drain(s.run)
    op = operation(s.workspace, started["id"])
    state = transact(s.workspace, set_seed.seed_state, op)
    assert state["state"] == "done", (op["state"], op["review"][-1])
    assert state["message"] == (
        "Box room filled is built and filled its unseen space in 2 rounds; its v1 waits for you in the "
        "stage workbench."
    )
    # Two rounds of probes, all within the reservation, kept as hidden material: no take candidates.
    probes = stages(op, "probe")
    assert 2 <= len(probes) <= 6 and {set_fill.stage(j)["round"] for j in probes} == {1, 2}
    with s.workspace.db.transaction() as session:
        assert OPERATIONS.candidates(session, op["id"]) == []
    # Every probe's inputs are earlier results pinned by identity, and it says what it is.
    for job in probes:
        assert all(v["source"] == "artifact" and v["sha256"] for v in job["inputs"].values())
        prompt = next(
            n for n in job["graph"].values() if n["class_type"] == "MiniMaxH3ImageToVideo"
        )
        assert "The place: A box room." in prompt["inputs"]["prompt"]
        assert job["snapshot"]["intermediate"] is True
    # The version is the last round's: the take's frame and the accepted stills.
    set_id = op["review"][-1]["set_id"]
    with s.workspace.db.transaction() as session:
        stage = sets.STORE.get(session, set_id)
    version = stage["versions"][0]
    assert version["state"] == "proposed"
    assert stage["provenance"]["layout"] == "centre"
    assert stage["provenance"]["fill"] == {"rounds": 2, "renders": len(probes), "ended": "rounds"}
    with s.workspace.db.transaction() as session:
        keyframes = sets.STORE.keyframes(session, version["id"])
    # The take's one frame (a still camera) and what the rounds added: accepted stills and their trajectory frames.
    assert len(keyframes) > 1
    assert "filled its unseen space in 2 rounds" in version["note"]


def test_a_still_shot_refused_without_filling_builds_with_it(scene):
    s = scene
    refused = start(s, fill_unseen=False)
    drain(s.run)
    assert (
        transact(s.workspace, set_seed.seed_state, operation(s.workspace, refused["id"]))["state"]
        == "refused"
    )
    # The refusal holds for its own settings only: filling is another build.
    preview = transact(
        s.workspace, set_seed.seed_preview, s.workspace.library, segment_id=s.segment["id"], shot=1
    )
    assert preview["refusal"] is None
    # The last build's refusal stays in view, saying that this choice lifts it; without filling it holds.
    assert preview["seed"]["message"].endswith("With filling on, this shot can be built now.")
    plain = transact(
        s.workspace,
        set_seed.seed_preview,
        s.workspace.library,
        segment_id=s.segment["id"],
        shot=1,
        fill_unseen=False,
    )
    assert plain["refusal"] and "With filling on" not in plain["seed"]["message"]
    filled = start(s)
    assert filled["id"] != refused["id"]


def test_probes_whose_renders_fail_are_rejected_and_the_stage_keeps_the_take(scene):
    s = scene
    s.comfy.fail_graph = lambda graph: any(
        node["class_type"] == "MiniMaxH3FunControlNetApply" for node in graph.values()
    )
    started = start(s)
    drain(s.run)
    op = operation(s.workspace, started["id"])
    assert op["review"][-1]["stage"] == "done" and op["review"][-1]["ended"] == "acceptance"
    assert all(job["state"] == "failed" for job in stages(op, "probe"))
    assert not stages(op, "extend")
    state = transact(s.workspace, set_seed.seed_state, op)
    assert (
        "stopped filling after round 2: no probe's still fitted the stage for 2 rounds"
        in state["message"]
    )
    with s.workspace.db.transaction() as session:
        stage = sets.STORE.get(session, op["review"][-1]["set_id"])
    assert stage["provenance"]["fill"]["rounds"] == 0  # the seed's own atlas and splat


def test_a_stage_that_fails_ends_the_filling_at_the_last_checkpoint(scene):
    s = scene
    s.run.engines[set_fill.CONSOLIDATE] = EngineCommand(
        (sys.executable, str(FAKE), "--crash", "fill_consolidate")
    )
    started = start(s)
    drain(s.run)
    op = operation(s.workspace, started["id"])
    assert op["review"][-1]["ended"] == "failed"
    state = transact(s.workspace, set_seed.seed_state, op)
    assert state["state"] == "done"
    assert (
        "stopped filling in round 1: its consolidation failed, so it holds what the rounds before found"
        in (state["message"])
    )


def test_a_stopped_fill_is_retried_with_its_frozen_settings(scene):
    s = scene
    started = start(s)
    for _ in range(400):
        if stages(operation(s.workspace, started["id"]), "probe"):
            break
        if not s.run.run_once():
            break
    with s.workspace.db.transaction() as session:
        OPERATIONS.request_cancel(session, started["id"])
    drain(s.run)
    stopped = operation(s.workspace, started["id"])
    state = transact(s.workspace, set_seed.seed_state, stopped)
    assert (state["state"], state["can_retry"]) == ("failed", True)
    again = transact(s.workspace, set_seed.retry_seed, s.workspace.library, started["id"])
    assert again["id"] != started["id"]
    assert again["request"]["fill"] == stopped["request"]["fill"]
    assert again["budget_reserved"] == 6


def test_fill_more_fills_a_stage_into_a_proposed_version(scene):
    # Fill more unseen space (ADR 0020 §4): an extension Run queued fixes runs from the version the user looks at.
    import pytest

    from gaiavfx.application import set_build
    from gaiavfx.shared.errors import ApplicationError

    s = scene
    seeded = start(s, shot=0, fill_unseen=False)
    drain(s.run)
    set_id = operation(s.workspace, seeded["id"])["review"][-1]["set_id"]
    library = s.workspace.library
    base = transact(s.workspace, sets.set_state, library, set_id)["version"]
    queued = transact(s.workspace, sets.fill_more, library, set_id, version_id=base["id"])
    assert [(f["action"], f["state"]) for f in queued["fixes"]] == [("extend", "queued")]
    with pytest.raises(ApplicationError, match="queued already"):
        transact(s.workspace, sets.fill_more, library, set_id, version_id=base["id"])
    started = transact(
        s.workspace, set_build.run_queued_fixes, library, set_id, version_id=base["id"]
    )
    build = operation(s.workspace, started["build"]["operation_id"])
    assert build["budget_reserved"] == 6 and build["request"]["fill_base"]["atlas"].startswith(
        "v1-repair"
    )
    drain(s.run)
    done = transact(s.workspace, sets.set_state, library, set_id)
    version = done["versions"][-1]
    assert (version["ordinal"], version["state"], version["origin"]) == (2, "proposed", "repair")
    assert version["parent_id"] == base["id"]
    assert version["note"] == "v1 rebuilt; filled its unseen space in 2 rounds."
    with s.workspace.db.transaction() as session:
        before = len(sets.STORE.keyframes(session, base["id"]))
        after = len(sets.STORE.keyframes(session, version["id"]))
    assert after > before
    assert [f["state"] for f in done["fixes"]] == ["done"]


def test_fill_more_on_a_filled_stage_names_its_stills_after_the_rounds_it_holds(scene):
    # The GPU smoke (28 October): Fill more on a stage the seed had filled named its first still r1p0 again, and
    # consolidation refused it as a keyframe already in the atlas.
    import re

    from gaiavfx.application import set_build

    s = scene
    seeded = start(s)
    drain(s.run)
    set_id = operation(s.workspace, seeded["id"])["review"][-1]["set_id"]
    library = s.workspace.library
    base = transact(s.workspace, sets.set_state, library, set_id)["version"]
    transact(s.workspace, sets.fill_more, library, set_id, version_id=base["id"])
    started = transact(
        s.workspace, set_build.run_queued_fixes, library, set_id, version_id=base["id"]
    )
    drain(s.run)
    build = operation(s.workspace, started["build"]["operation_id"])
    done = transact(s.workspace, sets.set_state, library, set_id)
    version = done["versions"][-1]
    assert (version["ordinal"], version["state"]) == (2, "proposed"), build["review"][-1]
    with s.workspace.db.transaction() as session:
        held = {k["key"] for k in sets.STORE.keyframes(session, base["id"])}
        added = {k["key"] for k in sets.STORE.keyframes(session, version["id"])} - held
    assert {re.match(r"r(\d+)p", k).group(1) for k in held if k.startswith("r")} == {"1", "2"}
    assert added and all(re.match(r"r[34]p\d", k) for k in added), added
    assert version["note"] == "v1 rebuilt; filled its unseen space in 2 rounds."


def test_a_still_filling_set_aside_can_be_kept_into_a_proposed_version(scene):
    # Keep this still (ADR 0020 §8): the user's ranks put a still consolidation rejected first. Kept, it shapes the
    # stage from the next build, with the trusted pixels its own geometry gives it.
    import pytest

    from gaiavfx.application import set_build
    from gaiavfx.shared.errors import ApplicationError

    s = scene
    s.comfy.fill.bent = {"r1p0"}
    seeded = start(s)
    drain(s.run)
    set_id = operation(s.workspace, seeded["id"])["review"][-1]["set_id"]
    library = s.workspace.library
    state = transact(s.workspace, sets.set_state, library, set_id)
    keyframes = {k["key"]: k for k in state["version"]["keyframes"]}
    aside = keyframes["r1p0"]
    assert (aside["role"], aside["can_keep"]) == ("appearance-only", True)
    assert aside["dropped"].startswith(
        "Filling set this still aside: its depth disagreed with the stage by"
    )
    assert all(k["dropped"] is None for key, k in keyframes.items() if key != "r1p0")

    def keep(keyframe):
        return transact(
            s.workspace,
            sets.queue_fix,
            library,
            set_id,
            version_id=state["version"]["id"],
            action="keep",
            target={"keyframe": keyframe["id"], "label": keyframe["label"]},
            shows="",
            issue_id=None,
        )

    queued = keep(aside)
    assert (
        f"Rebuilds v1 keeping {aside['label']}, about 4 minutes on the GPU"
        in queued["run"]["words"]
    )
    with pytest.raises(ApplicationError, match="queued already"):
        keep(aside)
    with pytest.raises(ApplicationError, match="not a still that filling set aside"):
        keep(keyframes["k01"])
    transact(
        s.workspace, set_build.run_queued_fixes, library, set_id, version_id=state["version"]["id"]
    )
    drain(s.run)
    done = transact(s.workspace, sets.set_state, library, set_id)
    version = done["versions"][-1]
    assert (version["ordinal"], version["state"]) == (2, "proposed")
    assert version["note"] == f"v1 rebuilt keeping {aside['label']}."
    kept = {k["key"]: k for k in done["version"]["keyframes"]}["r1p0~1"]
    assert (kept["role"], kept["dropped"], kept["can_keep"]) == ("generated", None, False)
    with s.workspace.db.transaction() as session:
        record = next(
            k for k in sets.STORE.keyframes(session, version["id"]) if k["key"] == "r1p0~1"
        )
        before = next(
            k for k in sets.STORE.keyframes(session, state["version"]["id"]) if k["key"] == "r1p0"
        )
    # The same picture; its depth file is new, with the trusted pixels restored.
    assert record["image_asset_id"] == before["image_asset_id"]
    assert record["depth_artifact_id"] != before["depth_artifact_id"]
    assert record["info"]["kept"]["reason"].startswith("Kept in the workbench")

    # Accepted, the kept stage fills more: the live trial's Fill more after a kept still stopped at consolidation,
    # which read the superseded record's content names as paths.
    transact(
        s.workspace,
        sets.accept_version,
        library,
        set_id,
        version["id"],
        expected_current_id=None,
    )
    transact(s.workspace, sets.fill_more, library, set_id, version_id=version["id"])
    started = transact(
        s.workspace, set_build.run_queued_fixes, library, set_id, version_id=version["id"]
    )
    drain(s.run)
    build = operation(s.workspace, started["build"]["operation_id"])
    filled = transact(s.workspace, sets.set_state, library, set_id)["versions"][-1]
    assert (filled["ordinal"], filled["state"]) == (3, "proposed"), build["review"][-1]
    assert filled["note"] == "v2 rebuilt; filled its unseen space in 2 rounds."
