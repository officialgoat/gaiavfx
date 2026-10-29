"""The set build (ADR 0018 §4): Run queued fixes, mode repair, exclusions only. Through the durable runner, with the
real worldbuilder worker and its real map code on a synthetic box room and a fake splat fit
(`fixtures/worldbuilder_fake_worker.py`). No model, no GPU."""

import json
import sys
from pathlib import Path

import numpy as np
import pytest
from fake_comfy import FakeComfy
from PIL import Image

from gaiavfx.application import set_agent, set_build, set_bundles, sets
from gaiavfx.application.engine_jobs import EngineCommand
from gaiavfx.application.operations import OperationRunner
from gaiavfx.application.workspace import Workspace
from gaiavfx.infrastructure import engines
from gaiavfx.modules.library.application import LibraryService
from gaiavfx.shared.errors import ApplicationError

MIGRATIONS = Path(__file__).parents[1] / "migrations"
WORKERS = Path(__file__).parents[2] / "engine_workers"
FAKE = Path(__file__).parent / "fixtures" / "worldbuilder_fake_worker.py"
HALF = np.array([2.0, 1.2, 3.0])  # the box room's half extents around the panorama, y down


def _exit(origin, direction):
    """Distance along each ray (rows of `direction`) from `origin` to the box room's wall."""
    with np.errstate(divide="ignore", invalid="ignore"):
        t = (np.sign(direction) * HALF - origin) / direction
    return np.nanmin(np.where(np.isfinite(t) & (t > 0), t, np.inf), axis=-1)


def _camera(position, heading):
    h = np.radians(heading)
    forward = np.array([np.sin(h), 0.0, np.cos(h)])
    down = np.array([0.0, 1.0, 0.0])
    rotation = np.stack([np.cross(down, forward), down, forward])
    viewmat = np.eye(4)
    viewmat[:3, :3], viewmat[:3, 3] = rotation, -rotation @ np.asarray(position, float)
    return viewmat


def _still(folder, name, colour, depth):
    height, width = depth.shape
    Image.new("RGB", (width, height), colour).save(folder / f"{name}.png")
    valid = np.ones(depth.shape, bool)
    np.savez_compressed(
        folder / f"{name}.npz", depth=depth.astype(np.float32), valid=valid, trust=valid
    )


def room_bundle(root: Path) -> Path:
    """A set bundle of a box room: the panorama at its centre, two explored views (K1, K2) and one in-between frame
    (A1), its map carved by the worker's own map code as an import's would be."""
    sys.path.insert(0, str(WORKERS))
    from worldbuilder_splat import explore as ex
    from worldbuilder_splat import plausibility as pl
    from worldbuilder_splat.erp import directions

    bundle = root / "room"
    (bundle / "keyframes").mkdir(parents=True)
    pano = directions(256, 128).reshape(-1, 3)
    _still(bundle / "keyframes", "pano", "navy", _exit(np.zeros(3), pano).reshape(128, 256))
    k = np.array([[40.0, 0, 32], [0, 40.0, 18], [0, 0, 1]])
    u, v = np.meshgrid(np.arange(64) + 0.5, np.arange(36) + 0.5)
    rays = np.stack([(u - 32) / 40, (v - 18) / 40, np.ones_like(u)], -1).reshape(-1, 3)
    views = {
        "v1": ([0.6, 0.0, 0.8], 70.0, "olive", "generated"),
        "v2": ([-0.5, 0.0, -0.7], 210.0, "maroon", "generated"),
        "v1_t24": ([0.3, 0.0, 0.4], 40.0, "teal", "trajectory"),
    }
    records = [
        {
            "id": "pano",
            "kind": "erp",
            "role": "observed",
            "image": "keyframes/pano.png",
            "data": "keyframes/pano.npz",
            "viewmat": np.eye(4).tolist(),
            "position": [0.0, 0.0, 0.0],
        }
    ]
    for name, (position, heading, colour, role) in views.items():
        viewmat = _camera(position, heading)
        world = rays @ viewmat[:3, :3]  # camera rays (z = 1) turned into the world
        _still(
            bundle / "keyframes", name, colour, _exit(np.asarray(position), world).reshape(36, 64)
        )
        records.append(
            {
                "id": name,
                "kind": "pinhole",
                "role": role,
                "image": f"keyframes/{name}.png",
                "data": f"keyframes/{name}.npz",
                "viewmat": viewmat.tolist(),
                "K": k.tolist(),
                "position": position,
            }
        )
    atlas = {"version": 4, "keyframes": records, "decisions": []}
    (bundle / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
    m = pl.place_map(bundle / "atlas.json")
    space, plane = m["space"], m["plane"]
    grid = pl.grid_for(plane, space.voxel, space.lower)
    cameras = pl.cameras_of(atlas)
    np.savez_compressed(bundle / "map.npz", plane=plane, frontier=m["frontier"])
    (bundle / "splat.ply").write_bytes(b"")
    labels = {"pano": "K0", "v1": "K1", "v2": "K2", "v1_t24": "A1"}

    def entry(relative):
        from test_sets import entry as named

        return named(bundle, relative)

    from test_sets import write_ply

    write_ply(bundle / "splat.ply")
    document = {
        "schema": 1,
        "name": "Box room",
        "place_text": "A plain box room.",
        "provenance": {"source": "test"},
        "version": {
            "origin": "import",
            "atlas": entry("atlas.json"),
            "map": entry("map.npz"),
            "splat": entry("splat.ply"),
            "map_meta": {
                "voxel": float(space.voxel),
                "lower": [float(x) for x in space.lower],
                "shape": list(plane.shape),
                "room": round(float(m["room"]), 4),
                "height": round(float(m["height"]), 5),
                "grid": grid.record(),
                "coverage": ex.coverage(space, plane, m["room"]),
                "cameras": [{**c, "cell": grid.name_of(c["position"])} for c in cameras],
            },
            "metrics": {"uncalibrated": True},
            "keyframes": [
                {
                    "key": r["id"],
                    "label": labels[r["id"]],
                    "kind": r["kind"],
                    "role": r["role"],
                    "viewmat": r["viewmat"],
                    "intrinsics": r.get("K"),
                    "width": 256 if r["kind"] == "erp" else 64,
                    "height": 128 if r["kind"] == "erp" else 36,
                    "heading": None,
                    "image": entry(r["image"]),
                    "depth": entry(r["data"]),
                    "info": {},
                }
                for r in records
            ],
        },
        "issues": [{"kind": "floaters", "why": "Smear in front of K2's wall.", "cameras": ["K2"]}],
    }
    (bundle / "set.json").write_text(json.dumps(document), encoding="utf-8")
    return bundle


@pytest.fixture
def room(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIAVFX_GPU_LOCK", str(tmp_path / "gpu.lock"))
    monkeypatch.setattr(engines, "free_vram_gb", lambda: None)
    root = tmp_path / "library"
    LibraryService(root, MIGRATIONS).create("Sets")
    workspace = Workspace(root)
    set_id = set_bundles.import_bundle(workspace.db, workspace.library, room_bundle(tmp_path))
    yield workspace, set_id
    workspace.db.close()


def runner(workspace, *flags):
    commands = {
        step: EngineCommand((sys.executable, str(FAKE), *flags)) for step in set_build.STEPS
    }
    return OperationRunner(
        workspace.db,
        FakeComfy(),
        workspace.library,
        "http://fake",
        poll_seconds=0,
        sweep_seconds=0,
        engines=commands,
    )


def drain(run):
    for _ in range(40):
        if not run.run_once():
            break


def call(workspace, function, *args, **kwargs):
    with workspace.db.transaction() as session:
        return function(session, workspace.library, *args, **kwargs)


def keyframe(state, label):
    return next(k for k in state["version"]["keyframes"] if k["label"] == label)


def exclude(workspace, set_id, state, label, issue_id=None):
    return call(
        workspace,
        sets.queue_fix,
        set_id,
        version_id=state["version"]["id"],
        action="retract",
        target={"keyframe": keyframe(state, label)["id"], "label": label},
        shows="",
        issue_id=issue_id,
    )


def test_an_exclusion_names_one_keyframe_of_the_version_once(room):
    workspace, set_id = room
    state = call(workspace, sets.set_state, set_id)
    version_id = state["version"]["id"]
    for target, message in (
        ({"keyframe": keyframe(state, "K0")["id"]}, "ground truth"),
        ({"keyframe": "elsewhere"}, "version you are looking at"),
    ):
        with pytest.raises(ApplicationError, match=message):
            call(
                workspace,
                sets.queue_fix,
                set_id,
                version_id=version_id,
                action="retract",
                target=target,
                shows="",
                issue_id=None,
            )
    exclude(workspace, set_id, state, "K2")
    with pytest.raises(ApplicationError, match="queued already"):
        exclude(workspace, set_id, state, "K2")


def reshoot(workspace, set_id, state):
    return call(
        workspace,
        sets.queue_fix,
        set_id,
        version_id=state["version"]["id"],
        action="reshoot",
        target={"viewmat": state["version"]["keyframes"][1]["viewmat"]},
        shows="the window wall",
        issue_id=None,
    )


def test_reshoots_wait_for_a_later_step(room):
    workspace, set_id = room
    state = reshoot(workspace, set_id, call(workspace, sets.set_state, set_id))
    with pytest.raises(ApplicationError, match="Reshoots wait for a later step"):
        call(workspace, set_build.run_queued_fixes, set_id, version_id=state["version"]["id"])


def test_run_queued_fixes_builds_a_proposed_version_on_the_base_grid(room):
    workspace, set_id = room
    state = call(workspace, sets.set_state, set_id)
    base = state["version"]
    issue = state["issues"][0]
    exclude(workspace, set_id, state, "K2", issue_id=issue["id"])
    reshoot(workspace, set_id, state)

    started = call(workspace, set_build.run_queued_fixes, set_id, version_id=base["id"])
    running = [f for f in started["fixes"] if f["state"] == "running"]
    assert [f["action"] for f in running] == ["retract"]
    assert [f["state"] for f in started["fixes"] if f["action"] == "reshoot"] == ["queued"]
    assert started["build"]["state"] == "queued" and started["build"]["stage"] == "prepare"
    with pytest.raises(ApplicationError, match="already running"):
        call(workspace, set_build.run_queued_fixes, set_id, version_id=base["id"])

    assert "running (preparing)" in call(workspace, set_agent.agent_read_set, set_id)["build"]
    drain(runner(workspace))
    done = call(workspace, sets.set_state, set_id)
    assert done["build"]["state"] == "succeeded" and done["build"]["progress"] == 1.0
    assert (
        "v2 was built and waits for the user's review"
        in (call(workspace, set_agent.agent_read_set, set_id)["build"])
    )
    assert [(v["ordinal"], v["state"], v["origin"]) for v in done["versions"]] == [
        (1, "proposed", "import"),
        (2, "proposed", "repair"),
    ]
    built = call(workspace, sets.set_state, set_id, done["build"]["version_id"])
    version = built["version"]
    # K2 is colour only now, under an A label, with its still and depth; the others are the base's own records.
    assert [k["label"] for k in version["keyframes"]] == ["K0", "K1", "A1", "A2"]
    excluded = keyframe(built, "A2")
    assert (excluded["role"], excluded["was"], excluded["key"]) == ("appearance-only", "K2", "v2~1")
    assert excluded["image_asset_id"] == keyframe(state, "K2")["image_asset_id"]
    assert keyframe(built, "K1")["id"] == keyframe(state, "K1")["id"]
    # One grid: B3 names one place in both versions; the map lost K2's camera.
    meta = version["map_meta"]
    assert meta["grid"] == base["map_meta"]["grid"]
    # The map's voxels lie on the base map's lattice, so no cell boundary moves by a fraction of a voxel.
    offset = (np.array(meta["lower"]) - np.array(base["map_meta"]["lower"])) / meta["voxel"]
    assert np.allclose(offset, np.round(offset), atol=1e-6)
    assert [c["label"] for c in meta["cameras"]] == ["K0", "K1"]
    assert version["splat"]["gaussians"] == 4 and version["splat"]["sh_degree"] == 1
    assert version["metrics"]["heldout_psnr"] == 21.25 and version["metrics"]["uncalibrated"]
    assert version["note"] == "v1 rebuilt without K2."
    assert {f["action"]: f["state"] for f in built["fixes"]} == {
        "retract": "done",
        "reshoot": "queued",
    }
    # The issue waits for the human: only what they saw fixed in the review is fixed in the version.
    assert next(i for i in built["issues"] if i["id"] == issue["id"])["status"] == "queued"
    assert (version["parent_id"], version["operation_id"]) == (
        base["id"],
        done["build"]["operation_id"],
    )
    with pytest.raises(ApplicationError, match="This issue is not part of the stage"):
        call(
            workspace,
            sets.accept_version,
            set_id,
            version["id"],
            expected_current_id=None,
            looks_fixed=["another issue"],
        )
    accepted = call(
        workspace,
        sets.accept_version,
        set_id,
        version["id"],
        expected_current_id=None,
        looks_fixed=[issue["id"]],
    )
    settled = next(i for i in accepted["issues"] if i["id"] == issue["id"])
    assert (settled["status"], settled["resolved_in_id"]) == ("fixed", version["id"])
    # A build of the built version starts from its atlas: the excluded keyframe stays excluded.
    with pytest.raises(ApplicationError, match="colour only already"):
        exclude(workspace, set_id, accepted, "A2")


@pytest.mark.parametrize("decision", ["discard", "accept unticked"])
def test_a_built_version_reopens_the_issues_nobody_saw_fixed(room, decision):
    workspace, set_id = room
    state = call(workspace, sets.set_state, set_id)
    issue = state["issues"][0]
    exclude(workspace, set_id, state, "K2", issue_id=issue["id"])
    call(workspace, set_build.run_queued_fixes, set_id, version_id=state["version"]["id"])
    drain(runner(workspace))
    built_id = call(workspace, sets.set_state, set_id)["build"]["version_id"]
    if decision == "discard":
        after = call(workspace, sets.discard_version, set_id, built_id)
    else:
        after = call(workspace, sets.accept_version, set_id, built_id, expected_current_id=None)
    assert next(i for i in after["issues"] if i["id"] == issue["id"])["status"] == "open"
    # The exclusion ran: its fix stays done, so the button does not offer it again.
    assert [f["state"] for f in after["fixes"]] == ["done"]


def test_the_review_can_call_open_issues_fixed_but_no_others(room):
    workspace, set_id = room
    state = call(workspace, sets.set_state, set_id)
    judge = state["issues"][0]
    mine = call(
        workspace,
        sets.mark_issue,
        set_id,
        version_id=state["version"]["id"],
        kind="hole",
        why="A gap in the floor.",
        cells=[],
        cameras=[],
        region=None,
        note="",
    )["issues"][-1]
    call(
        workspace,
        sets.set_issue_status,
        set_id,
        mine["id"],
        status="dismissed",
        expected_revision=None,
        version_id=state["version"]["id"],
    )
    exclude(workspace, set_id, state, "K2")  # no issue named: the review lists the open ones
    call(workspace, set_build.run_queued_fixes, set_id, version_id=state["version"]["id"])
    drain(runner(workspace))
    built_id = call(workspace, sets.set_state, set_id)["build"]["version_id"]
    with pytest.raises(ApplicationError, match="Only open issues"):
        call(
            workspace,
            sets.accept_version,
            set_id,
            built_id,
            expected_current_id=None,
            looks_fixed=[mine["id"]],
        )
    accepted = call(
        workspace,
        sets.accept_version,
        set_id,
        built_id,
        expected_current_id=None,
        looks_fixed=[judge["id"]],
    )
    settled = next(i for i in accepted["issues"] if i["id"] == judge["id"])
    assert (settled["status"], settled["resolved_in_id"]) == ("fixed", built_id)


@pytest.mark.parametrize("crash", ["atlas_prepare", "splat_init_atlas"])
def test_a_stopped_build_fails_its_fixes_and_retry_queues_them_again(room, crash):
    workspace, set_id = room
    state = call(workspace, sets.set_state, set_id)
    issue = state["issues"][0]
    exclude(workspace, set_id, state, "K2", issue_id=issue["id"])
    call(workspace, set_build.run_queued_fixes, set_id, version_id=state["version"]["id"])
    drain(runner(workspace, "--crash", crash))
    stopped = call(workspace, sets.set_state, set_id)
    fix = stopped["fixes"][0]
    assert fix["state"] == "failed" and fix["operation_id"] == stopped["build"]["operation_id"]
    assert stopped["build"]["state"] in ("failed", "partial")
    assert "crashed on purpose" in stopped["build"]["error"]
    assert [v["origin"] for v in stopped["versions"]] == ["import"]
    assert next(i for i in stopped["issues"] if i["id"] == issue["id"])["status"] == "open"

    if crash == "atlas_prepare":  # Queue again on the fix's card, then Run queued fixes
        again = call(
            workspace, sets.retry_fix, set_id, fix["id"], version_id=state["version"]["id"]
        )
        assert again["fixes"][0]["state"] == "queued" and again["build"] is None
        assert next(i for i in again["issues"] if i["id"] == issue["id"])["status"] == "queued"
        restarted = call(
            workspace, set_build.run_queued_fixes, set_id, version_id=state["version"]["id"]
        )
    else:  # Retry build: one click
        restarted = call(
            workspace,
            set_build.run_queued_fixes,
            set_id,
            version_id=state["version"]["id"],
            retry=True,
        )
    assert restarted["fixes"][0]["state"] == "running"
    assert restarted["build"]["operation_id"] != stopped["build"]["operation_id"]
    drain(runner(workspace))
    assert [v["origin"] for v in call(workspace, sets.set_state, set_id)["versions"]] == [
        "import",
        "repair",
    ]


def test_the_build_routes_start_a_build_and_refuse_to_retry_a_running_fix(tmp_path):
    from fastapi.testclient import TestClient

    from gaiavfx.api.app import create_app

    app = create_app(tmp_path / "library", MIGRATIONS, "token", enable_runner=False)
    with TestClient(
        app, base_url="http://localhost", headers={"Authorization": "Bearer token"}
    ) as api:
        assert api.post("/api/v1/library", json={"name": "Sets"}).status_code == 201
        state = api.post("/api/sets/import", json={"folder": str(room_bundle(tmp_path))}).json()
        set_id, version_id = state["id"], state["version"]["id"]
        assert state["build"] is None
        queued = api.post(
            f"/api/sets/{set_id}/fixes",
            json={
                "version_id": version_id,
                "action": "retract",
                "target": {"keyframe": keyframe(state, "K1")["id"], "label": "K1"},
            },
        )
        assert queued.status_code == 200, queued.text
        started = api.post(f"/api/sets/{set_id}/build", json={"version_id": version_id})
        assert started.status_code == 200, started.text
        build = started.json()["build"]
        assert (build["state"], build["stage"], build["progress"]) == ("queued", "prepare", 0.0)
        fix_id = started.json()["fixes"][0]["id"]
        retry = api.post(
            f"/api/sets/{set_id}/fixes/{fix_id}/retry", json={"version_id": version_id}
        )
        assert retry.status_code == 409 and "failed fix" in retry.json()["detail"]
