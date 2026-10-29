"""Real SQLite aggregate with fixture A/W boundaries; never runs inference."""

import io
import json
import shutil
import sys
from copy import deepcopy
from pathlib import Path
from types import ModuleType, SimpleNamespace

import numpy as np
import pytest
from PIL import Image

from gaiavfx.api.recast import RecastDTO
from gaiavfx.application import recast, replace_character
from gaiavfx.application import recast_projection as p
from gaiavfx.infrastructure.database import Database
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.modules.library.application import LibraryService
from gaiavfx.modules.library.assets import AssetRecord
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.errors import ApplicationError

MIGRATIONS = Path(__file__).parents[1] / "migrations"


@pytest.fixture(scope="session")
def recast_template(tmp_path_factory):
    root = tmp_path_factory.mktemp("recast-template") / "library"
    LibraryService(root, MIGRATIONS).create("Recast tests")
    return root / "library.sqlite3"


@pytest.fixture
def world(tmp_path, monkeypatch, recast_template):
    root = tmp_path / "library"
    root.mkdir()
    shutil.copy2(recast_template, root / "library.sqlite3")
    db = Database(root)
    library = MediaLibrary(db, None)
    ops = OperationService()
    blobs = {}
    report = {
        "schema": 1,
        "source_asset": "film",
        "fps": [24, 1],
        "frame_count": 292,
        "width": 1920,
        "height": 1080,
        "letterbox": None,
        "shots": [
            {"index": i + 1, "start": i * 73, "end": (i + 1) * 73, "poster_artifact": None}
            for i in range(4)
        ],
        "tracks": [
            {
                "id": f"t{i + 1}",
                "shot": i + 1,
                "frames": [i * 73 + f for f in range(0, 73, 5)],
                "faces": list(range(15 * i, 15 * i + 15)),
                "boxes": [[0, 0, 20, 20]] * 15,
                "face_count": 15,
                "quality": 0.9,
                "crop_artifact": None,
            }
            for i in range(4)
        ],
        "clusters": [
            {
                "id": "c01",
                "track_ids": ["t1", "t2", "t3", "t4"],
                "screen_time_frames": 292,
                "shot_count": 4,
                "presence": [[0, 292]],
                "face_artifact": None,
            }
        ],
        "unknown_track_ids": [],
        "params": {"stride": 5},
    }
    with db.transaction() as session:
        for asset_id, kind in (
            ("film", "video"),
            ("picture", "image"),
            ("picture2", "image"),
            ("audio", "audio"),
        ):
            session.add(
                AssetRecord(
                    id=asset_id,
                    sha256=asset_id,
                    name=asset_id,
                    kind=kind,
                    path=asset_id,
                    mime="application/octet-stream",
                    info={"width": 1920, "height": 1080, "frame_rate": "24/1", "has_audio": True},
                )
            )
    monkeypatch.setattr(
        library,
        "read_blob",
        lambda session, artifact: (
            blobs[artifact] if isinstance(blobs[artifact], bytes) else json.dumps(blobs[artifact])
        ),
    )

    def create_op(session, kind, key, request, budget=0, steps=None):
        return ops.create(
            session,
            kind=kind,
            request=request,
            request_key=key,
            jobs=(
                [
                    JobSpec(
                        "video",
                        "comfy",
                        "fixture",
                        seed=i,
                        graph={"render": {"class_type": "Fixture", "inputs": {}}},
                        snapshot={"knowledge": {"fixture": "no inference"}},
                    )
                    for i in range(1 if request.get("mode") == "draft" else budget)
                ]
                if budget
                else [
                    JobSpec("analysis", "local", "fixture", step=step) for step in (steps or [kind])
                ]
            ),
            budget_limit=None,
            budget=budget,
        )

    def settle(session, operation_id, state="succeeded"):
        for job in ops.operation(session, operation_id)["jobs"]:
            if job["state"] != "queued":
                continue
            ops.transition(session, job["id"], "claimed")
            ops.transition(session, job["id"], "running")
            if state == "succeeded":
                ops.transition(session, job["id"], "collecting")
            ops.transition(session, job["id"], state)

    def settle_target(asset_id="picture", scores=(0.881,), boxes=None, state="succeeded", raw=None):
        from gaiavfx.application.target_faces import start_target_report

        with db.transaction() as session:
            operation = start_target_report(session, library, asset_id)
            if operation["state"] == "succeeded":
                return
            if state != "succeeded":
                settle(session, operation["id"], state)
                return
            png = io.BytesIO()
            Image.new("RGB", (768, 1024)).save(png, format="PNG")
            data = io.BytesIO()
            np.savez(
                data,
                frame=np.zeros(len(scores), dtype=int),
                score=np.array(scores),
                box=np.array(
                    boxes if boxes is not None else [[0, 0, 97, 97]] * len(scores)
                ).reshape(-1, 4),
            )
            for job, key, blob in zip(
                operation["jobs"],
                ("normalized", "faces:reports:0"),
                (png.getvalue(), data.getvalue() if raw is None else raw),
            ):
                blobs[job["id"]] = blob
                for transition in ("claimed", "running", "collecting"):
                    ops.transition(session, job["id"], transition)
                ops.record_outputs(
                    session, job["id"], [{"output_key": key, "artifact_id": job["id"]}]
                )
                ops.transition(session, job["id"], "succeeded")

    analysis_module = ModuleType("gaiavfx.application.film_analysis")

    def start_film_analysis(session, library, asset_id, *, request_key):
        return create_op(
            session,
            "film_analysis",
            request_key,
            {"source_asset": asset_id},
            steps=["film_shots", "faces", "film_cast"],
        )

    def film_analysis_report(session, library, operation_id):
        return (
            deepcopy(report)
            if ops.operation(session, operation_id)["state"] == "succeeded"
            else None
        )

    face_embeddings = {}  # film track id -> embedding; every other face points one way

    def film_faces(session, library, operation_id):
        count = 1 + max(row for t in report["tracks"] for row in t["faces"])
        frame, embedding = np.full(count, -1), np.zeros((count, 4), np.float32)
        for t in report["tracks"]:
            for row, value in zip(t["faces"], t["frames"]):
                frame[row] = value
            embedding[t["faces"]] = face_embeddings.get(t["id"], (1, 0, 0, 0))
        return {"frame": frame, "embedding": embedding}

    analysis_module.start_film_analysis = start_film_analysis
    analysis_module.film_analysis_report = film_analysis_report
    analysis_module.film_faces = film_faces
    monkeypatch.setitem(sys.modules, analysis_module.__name__, analysis_module)

    calls = {"analysis": [], "replacement": [], "assembly": [], "plan": []}
    window_reports = {}
    version_map = {}
    window_module = ModuleType("gaiavfx.application.shot_replacement")

    def shot_windows(start, end, *, length=73, overlap=22):
        result = []
        while start < end:
            stop = min(start + length, end)
            result.append((start, stop, 5 + 17 * max(0, (stop - start - 5 + 16) // 17)))
            if stop == end:
                break
            start += length - overlap
        return result

    def start_window_analyses(
        session,
        library,
        *,
        source_asset,
        shot_start,
        shot_end,
        faces_operation,
        concepts=("person",),
        request_key,
    ):
        result = []
        for i, (start, end, _) in enumerate(shot_windows(shot_start, shot_end)):
            op = create_op(
                session,
                "person_analysis",
                request_key + str(i),
                {"source_asset": source_asset, "shot_start": start, "shot_end": end},
                steps=["people_report"],
            )
            if op["_created"]:
                calls["analysis"].append(op["id"])
                shot = next(s for s in report["shots"] if s["start"] <= start < s["end"])
                tracks = [t for t in report["tracks"] if t["shot"] == shot["index"]]
                blob = {
                    "source_asset": source_asset,
                    "shot_start": start,
                    "shot_end": end,
                    "native": [1920, 1080],
                    "fps": 24,
                    "letterbox": None,
                    "concepts": ["person"],
                    "contacts": {},
                    "tracks": [
                        {
                            "id": f"person{j}",
                            "concept": "person",
                            "box": {"x0": 700, "y0": 100, "x1": 1000, "y1": 950},
                            "identity_margin": 0.5,
                            "presence": ["present"] * (end - start),
                            "identity": {
                                "cluster": "c01",
                                "film_tracks": {t["id"]: 3},
                                "votes": 3,
                                "margin": 0.5,
                            },
                        }
                        for j, t in enumerate(tracks)
                    ],
                }
                blobs[op["id"]] = blob
                window_reports[op["id"]] = blob
                job_id = op["jobs"][0]["id"]
                for state in ("claimed", "running", "collecting"):
                    ops.transition(session, job_id, state)
                ops.record_outputs(
                    session,
                    op["jobs"][0]["id"],
                    [{"output_key": "people", "artifact_id": op["id"]}],
                )
                ops.transition(session, job_id, "succeeded")
            result.append(ops.operation(session, op["id"]))
        return result

    def start_shot_replacement(
        session,
        library,
        *,
        source_asset,
        window_analyses,
        performers,
        mode,
        seed,
        request_key,
        budget_limit=None,
        handoff=None,
        faceswap_allowed=True,
    ):
        result = []
        for i, analysis in enumerate(
            window_analyses[:1] if handoff and handoff["kind"] == "latent" else window_analyses
        ):
            budget = p.window_renders(mode)
            op = create_op(
                session,
                "character_replacement",
                request_key + str(i),
                {
                    "source_asset": source_asset,
                    "analysis": analysis,
                    "analysis_operation": analysis,
                    "facts": {
                        "performers": [
                            {"track_id": p["tracks"][analysis], "target_asset": p["target_asset"]}
                            for p in performers
                        ]
                    },
                    "performers": performers,
                    "mode": mode,
                    "seed": seed,
                    "save_latent": bool(handoff and handoff["kind"] == "latent"),
                    "crop_override": (handoff or {}).get("crop"),
                    "faceswap_allowed": faceswap_allowed,
                },
                budget,
            )
            if op["_created"]:
                calls["replacement"].append(op["id"])
            result.append(op)
        return result

    def chain_take(session, library, operation):
        return (
            {
                "window": {
                    "candidate_id": operation["id"] + "-candidate",
                    "operation_id": operation["id"],
                }
            }
            if operation["state"] == "succeeded"
            else None
        )

    def continue_shot_replacement(
        session,
        library,
        *,
        source_asset,
        window_analyses,
        window_operations,
        performers,
        mode,
        seed,
        request_key,
        budget_limit=None,
        handoff=None,
        predecessor_take=None,
    ):
        index = len(window_operations)
        analysis = window_analyses[index]
        op = create_op(
            session,
            "character_replacement",
            request_key + str(index),
            {
                "source_asset": source_asset,
                "analysis": analysis,
                "analysis_operation": analysis,
                "facts": {
                    "performers": [
                        {"track_id": p["tracks"][analysis], "target_asset": p["target_asset"]}
                        for p in performers
                    ]
                },
                "performers": performers,
                "mode": mode,
                "seed": seed,
                "handoff": {
                    "predecessor_operation": window_operations[-1],
                    "candidate_id": predecessor_take["window"]["candidate_id"],
                },
            },
            p.window_renders(mode),
        )
        if op["_created"]:
            calls["replacement"].append(op["id"])
        return op

    def start_shot_assembly(
        session,
        library,
        *,
        source_asset,
        shot_start,
        shot_end,
        window_operations,
        request_key,
        layer=None,
        parent=None,
        stopped=None,
        bridge_operations=None,
        handoff=None,
    ):
        op = create_op(
            session,
            "shot_assembly",
            request_key,
            {
                "source_asset": source_asset,
                "window_operations": window_operations,
                "bridge_operations": bridge_operations or [],
                "handoff": handoff,
                **({"stopped": stopped} if stopped else {}),
                "layer": layer,
                "parent": parent,
            },
        )
        if op["_created"]:
            calls["assembly"].append(op["id"])
            available = stopped is None and all(
                ops.operation(session, x)["state"] == "succeeded" for x in window_operations
            )
            version_map[op["id"]] = (
                [
                    {
                        "id": op["id"],
                        "take": 0,
                        "layer": layer,
                        "parent": parent,
                        "preview_asset": "preview",
                        "patch_artifact": "patch",
                        "checks": [
                            {
                                "check": "background_geometry",
                                "verdict": "pass",
                                "calibrated": True,
                                "measured": 0.01,
                                "reason": "Geometry is within tolerance.",
                            }
                        ],
                        "bridges": [
                            {
                                "operation_id": key,
                                "candidate_id": key + "-candidate",
                                "join": ops.operation(session, key)["request"]["bridge"]["join"],
                            }
                            for key in (bridge_operations or [])
                            if ops.operation(session, key)["state"] == "succeeded"
                        ],
                        "joins": [
                            {
                                "join": j["join"],
                                "handoff": "bridge" if j["kind"] == "bridge" else "crossfade",
                            }
                            for j in (handoff or {}).get("joins", [])
                        ],
                        "windows": [
                            {
                                "operation_id": x,
                                "candidate_id": x + "-candidate",
                                "handoff": "latent"
                                if ops.operation(session, x)["request"].get("handoff")
                                else None,
                                "seed": 123,
                                "treatment": "inverted",
                                "framing": "crop",
                                "round": 0,
                            }
                            for x in window_operations
                        ],
                    }
                ]
                if available
                else []
            )
        return op

    def start_shot_head_pass(
        session,
        library,
        *,
        source_asset,
        window_analyses,
        performer,
        parent_version,
        seed,
        request_key,
    ):
        result = []
        for i, analysis in enumerate(window_analyses):
            op = create_op(
                session,
                "character_replacement",
                request_key + str(i),
                {
                    "source_asset": source_asset,
                    "analysis_operation": analysis,
                    "head": {"parent_asset": "parent"},
                    "seed": seed,
                    "facts": {
                        "performers": [
                            {
                                "track_id": performer["tracks"][analysis],
                                "target_asset": performer["targets"][analysis]
                                if "targets" in performer
                                else performer["target_asset"],
                            }
                        ]
                    },
                },
                budget=1,
            )
            calls["replacement"].append(op["id"])
            result.append(op)
        for bridge in parent_version.get("bridges", []):
            original = ops.operation(session, bridge["operation_id"])["request"]["bridge"]
            op = create_op(
                session,
                "character_replacement",
                request_key + "bridge" + str(bridge["join"]),
                {
                    "source_asset": source_asset,
                    "head": {"parent_asset": bridge["candidate_id"]},
                    "bridge": original,
                    "facts": {"performers": [{"track_id": "person0", "target_asset": "picture"}]},
                    "seed": seed,
                },
                budget=1,
            )
            calls["replacement"].append(op["id"])
            result.append(op)
        return result

    def shot_versions(session, library, assembly_operation_id):
        return (
            deepcopy(version_map[assembly_operation_id])
            if ops.operation(session, assembly_operation_id)["state"] == "succeeded"
            else []
        )

    def shared_crop_for_windows(session, library, *, requests):
        return {"fixture": "shared_crop", "scale": 0.5}

    def window_takes(session, library, operation):
        take = chain_take(session, library, operation)
        return {0: take} if take else {}

    def start_shot_bridge(
        session,
        library,
        *,
        source_asset,
        predecessor,
        successor,
        join,
        crop,
        seed,
        request_key,
        budget_limit=None,
    ):
        op = create_op(
            session,
            "character_replacement",
            request_key,
            {
                "source_asset": source_asset,
                "seed": seed,
                "bridge": {"join": join, "windows": [predecessor["window"], successor["window"]]},
            },
            1,
        )
        if op["_created"]:
            calls.setdefault("bridge", []).append(op["id"])
        return op

    for function in (
        shot_windows,
        shared_crop_for_windows,
        window_takes,
        start_shot_bridge,
        start_window_analyses,
        start_shot_replacement,
        continue_shot_replacement,
        chain_take,
        start_shot_head_pass,
        start_shot_assembly,
        shot_versions,
    ):
        setattr(window_module, function.__name__, function)
    monkeypatch.setitem(sys.modules, window_module.__name__, window_module)

    def replacement_plan(session, request, *, library, renders=None, draft=False):
        calls["plan"].append(deepcopy(request))
        blob = window_reports[request["analysis_operation"]]
        return {"gaps": blob.get("gaps", []), "arm": {}, "crop_preview": {"scale": 0.5}}

    real_planner = replace_character.replacement_plan
    monkeypatch.setattr(replace_character, "replacement_plan", replacement_plan)

    def invoke(function, *args, **kwargs):
        with db.transaction() as session:
            return function(session, library, *args, **kwargs)

    def touch_window(operation_id):
        # Updated evidence is a new immutable artifact, never in-place pixels.
        from gaiavfx.modules.generation.operations.persistence import JobRecord

        with db.transaction() as session:
            job = session.get(JobRecord, ops.operation(session, operation_id)["jobs"][0]["id"])
            artifact = job.outputs[0]["artifact_id"] + "-v2"
            blobs[artifact] = window_reports[operation_id]
            job.outputs = [{"output_key": "people", "artifact_id": artifact}]

    def create(request_key="create"):
        state = invoke(
            recast.create_recast, source_asset_id="film", title=None, request_key=request_key
        )
        with db.transaction() as session:
            settle(session, state["analysis"]["operation_id"])
        return invoke(recast.recast_state, state["id"])

    def ready(request_key="create"):
        state = create(request_key)
        state = invoke(
            recast.set_target,
            state["id"],
            state["characters"][0]["id"],
            expected_revision=state["revision"],
            asset_id="picture",
            label="Target",
        )
        settle_target()
        return invoke(
            recast.plan_recast, state["id"], expected_revision=state["revision"], range=None
        )

    fixture = SimpleNamespace(
        db=db,
        library=library,
        ops=ops,
        report=report,
        settle=settle,
        settle_target=settle_target,
        blobs=blobs,
        invoke=invoke,
        create=create,
        ready=ready,
        calls=calls,
        window_reports=window_reports,
        versions=version_map,
        window_api=window_module,
        film_api=analysis_module,
        face_embeddings=face_embeddings,
        real_planner=real_planner,
        touch_window=touch_window,
        create_op=create_op,
    )
    yield fixture
    db.close()


def edit(world, state, function, *args, **kwargs):
    result = world.invoke(
        function, state["id"], *args, expected_revision=state["revision"], **kwargs
    )
    RecastDTO.model_validate(result)
    return result


def test_create_analysis_replay_and_revision(world):
    state = world.invoke(
        recast.create_recast, source_asset_id="film", title="Film", request_key="one"
    )
    RecastDTO.model_validate(state)
    assert state["analysis"] == {
        "operation_id": state["analysis"]["operation_id"],
        "state": "running",
        "stage": "shots",
        "done": 0,
        "total": 3,
        "error": None,
    }
    replay = world.invoke(
        recast.create_recast, source_asset_id="film", title="Film", request_key="one"
    )
    assert replay["_created"] is False and replay["id"] == state["id"]
    with world.db.transaction() as session:
        world.settle(session, state["analysis"]["operation_id"])
    state = world.invoke(recast.recast_state, state["id"])
    assert state["analysis"]["stage"] == "done"
    assert state["film"]["frame_count"] == 292
    character = state["characters"][0]["id"]
    state = edit(world, state, recast.patch_character, character, label="Lead", hidden=True)
    with pytest.raises(ApplicationError) as error:
        world.invoke(
            recast.patch_character,
            state["id"],
            character,
            expected_revision=0,
            label="Stale",
            hidden=False,
        )
    assert error.value.status == 409
    assert world.invoke(recast.recast_state, state["id"])["characters"][0]["label"] == "Lead"


def test_overrides_new_unknown_merge_survive_new_report(world):
    state = world.create()
    original = state["characters"][0]["id"]
    state = edit(world, state, recast.move_tracks, track_ids=["t1"], to="new")
    created = state["characters"][1]["id"]
    assert state["characters"][1]["presence"] == [[0, 73]]
    state = edit(world, state, recast.move_tracks, track_ids=["t2"], to="unknown")
    assert state["characters"][0]["shot_count"] == 2
    state = edit(world, state, recast.merge_characters, into=created, sources=[original])
    # A fresh machine cluster cannot reclaim overridden tracks.
    world.report["clusters"][0]["id"] = "reclustered"
    state = world.invoke(recast.recast_state, state["id"])
    assert len(state["characters"]) == 1 and state["characters"][0]["id"] == created
    assert state["characters"][0]["track_count"] == 3
    assert state["characters"][0]["screen_time_frames"] == 219
    assert state["shots"][1]["appearances"][0]["character_id"] is None


def test_character_tracks_follow_overrides_and_keep_crop_identity(world):
    world.report["tracks"][0].update(crop_artifact="track-crop-128", face_count=7)
    state = world.create()
    original = state["characters"][0]["id"]
    tracks = state["characters"][0]["tracks"]
    assert [t["id"] for t in tracks] == ["t1", "t2", "t3", "t4"]
    assert tracks[0] == {
        "id": "t1",
        "shot_id": "s0001",
        "frames_with_face": 7,
        "face": {
            "artifact_id": "track-crop-128",
            "url": f"/api/recasts/{state['id']}/faces/track-crop-128",
            "frame": 0,
            "track_id": "t1",
        },
    }
    assert tracks[1]["face"] is None
    state = edit(world, state, recast.move_tracks, track_ids=["t1"], to="new")
    created = state["characters"][1]["id"]
    assert state["characters"][1]["tracks"] == tracks[:1]
    assert state["characters"][0]["tracks"] == tracks[1:]
    state = edit(world, state, recast.merge_characters, into=created, sources=[original])
    state = world.invoke(recast.recast_state, state["id"])
    assert state["characters"][0]["tracks"] == tracks
    assert state["characters"][0]["track_count"] == len(tracks)


def test_unknown_face_track_id_can_move_the_tray_face(world):
    world.report["tracks"][0]["crop_artifact"] = "unknown-crop-128"
    world.report["clusters"][0]["track_ids"].remove("t1")
    world.report["unknown_track_ids"] = ["t1"]
    state = world.create()
    tray_face = state["unknown_faces"][0]
    assert tray_face["track_id"] == "t1"
    assert tray_face["artifact_id"] == "unknown-crop-128"
    character = state["characters"][0]["id"]
    state = edit(world, state, recast.move_tracks, track_ids=[tray_face["track_id"]], to=character)
    assert state["unknown_faces"] == []
    assert state["characters"][0]["tracks"][0]["face"] == tray_face
    state = edit(world, state, recast.move_tracks, track_ids=["t1"], to="unknown")
    assert state["unknown_faces"] == [tray_face]
    assert "t1" not in {t["id"] for t in state["characters"][0]["tracks"]}


def test_targets_human_agent_and_non_images(world):
    state = world.create()
    cid = state["characters"][0]["id"]
    state = edit(world, state, recast.propose_target, cid, asset_id="picture", label="Proposal")
    assert state["characters"][0]["target"]["state"] == "proposed"
    assert all(s["status"] == "absent" for s in state["shots"])
    state = edit(world, state, recast.confirm_target, cid)
    assert state["characters"][0]["target"] == {
        "asset_id": "picture",
        "label": "Proposal",
        "state": "confirmed",
        "proposed_by": "agent",
        "face": "checking",
        "reference": None,
    }
    state = edit(world, state, recast.clear_target, cid)
    for asset in ("film", "audio", "missing"):
        with pytest.raises(ApplicationError) as error:
            edit(world, state, recast.set_target, cid, asset_id=asset, label="No")
        assert error.value.status == 422
    state = edit(world, state, recast.set_target, cid, asset_id="picture", label="Human")
    assert state["characters"][0]["target"]["state"] == "confirmed"
    assert state["characters"][0]["target"]["proposed_by"] == "user"


def test_choice_history_and_rollback(world):
    state = world.ready()
    state = edit(world, state, recast.choose_version, "s0001", version="original")
    state = edit(world, state, recast.choose_version, "s0001", version="original")
    assert state["shots"][0]["status"] == "kept_original"
    with pytest.raises(ApplicationError):
        edit(world, state, recast.choose_version, "s0001", version="missing")
    with world.db.transaction() as session:
        row = p.store.load(session, state["id"])
    assert len(row["choices"]) == 2
    assert {c["authority"] for c in row["choices"]} == {"user"}
    assert [c["recast_revision"] for c in row["choices"]] == [3, 4]


def test_identity_votes_resolve_after_split_and_conflict(world):
    world.report["tracks"][1]["shot"] = 1
    world.report["tracks"][1]["frames"] = world.report["tracks"][0]["frames"][:]
    state = world.ready()
    state = edit(world, state, recast.move_tracks, track_ids=["t2"], to="new")
    second = state["characters"][1]["id"]
    state = edit(world, state, recast.set_target, second, asset_id="picture2", label="Second")
    analysis = world.calls["analysis"][0]
    track = world.window_reports[analysis]["tracks"][0]
    track["identity"]["film_tracks"] = {"t1": 2, "t2": 1}
    world.touch_window(analysis)
    state = world.invoke(recast.recast_state, state["id"])
    assert not state["shots"][0]["plan"]["gaps"]
    track["identity"]["film_tracks"] = {"t1": 1, "t2": 1}
    world.touch_window(analysis)
    state = world.invoke(recast.recast_state, state["id"])
    assert "identity_conflict" in {g["code"] for g in state["shots"][0]["plan"]["gaps"]}
    # Unknown faces abstain (Tears of Steel shot 46: the actor seen through a hologram) ...
    track["identity"]["film_tracks"] = {"t1": 3, "unknown-track": 3}
    world.touch_window(analysis)
    state = world.invoke(recast.recast_state, state["id"])
    assert not state["shots"][0]["plan"]["gaps"]
    # ... unless they outnumber the known winner more than two to one.
    track["identity"]["film_tracks"] = {"t1": 1, "unknown-track": 3}
    world.touch_window(analysis)
    state = world.invoke(recast.recast_state, state["id"])
    assert "unbound_performer" in {g["code"] for g in state["shots"][0]["plan"]["gaps"]}
    track["identity"]["film_tracks"] = {}
    world.touch_window(analysis)
    state = world.invoke(recast.recast_state, state["id"])
    assert "unbound_performer" in {g["code"] for g in state["shots"][0]["plan"]["gaps"]}


def test_planning_long_shot_is_idempotent_and_presence_gaps_are_visible(world):
    world.report["shots"] = [{"index": 1, "start": 0, "end": 124, "poster_artifact": None}]
    world.report["frame_count"] = 124
    world.report["tracks"] = [world.report["tracks"][0]]
    world.report["tracks"][0]["frames"] = list(range(0, 124, 5))
    world.report["clusters"][0]["track_ids"] = ["t1"]
    state = world.ready()
    plan = state["shots"][0]["plan"]
    assert [(w["start"], w["end"], w["length"]) for w in plan["windows"]] == [
        (0, 73, 73),
        (51, 124, 73),
    ]
    assert state["estimate"] == {
        "windows_pending": 0,
        "shots": 1,
        "windows": 2,
        "renders": 4,
        "gpu_seconds_low": int(2 * (280 + 280) * 0.7),
        "gpu_seconds_high": int(2 * (280 + 280) * 1.3),
        "storage_bytes": 2 * 146 * 2_500_000,
        "basis": "seed",
    }
    count = len(world.calls["analysis"])
    state = edit(world, state, recast.plan_recast, range=None)
    assert len(world.calls["analysis"]) == count
    world.window_reports[world.calls["analysis"][1]]["gaps"] = [
        {"code": "presence_gap", "message": "Performer leaves the window."}
    ]
    world.touch_window(world.calls["analysis"][1])
    state = world.invoke(recast.recast_state, state["id"])
    assert state["shots"][0]["status"] == "gap"
    assert state["shots"][0]["plan"]["gaps"][0]["code"] == "presence_gap"


def test_preview_uses_the_run_selection_in_every_window(world):
    # Regression (8 Oct trial, shot 46): a merged profile track made the run user-confirmed in
    # every window, while the preview re-derived it per window and asked for a margin.
    world.report["shots"] = [{"index": 1, "start": 0, "end": 124, "poster_artifact": None}]
    world.report["frame_count"] = 124
    world.report["tracks"] = world.report["tracks"][:2]
    for track, frames in zip(world.report["tracks"], (range(0, 60, 5), range(60, 124, 5))):
        track.update(shot=1, frames=list(frames))
    world.report["clusters"] = [
        {**world.report["clusters"][0], "id": cluster, "track_ids": [track]}
        for cluster, track in (("c01", "t1"), ("c02", "t2"))
    ]
    state = world.create()
    main, profile = (c["id"] for c in state["characters"])
    state = edit(world, state, recast.merge_characters, into=main, sources=[profile])
    state = edit(world, state, recast.set_target, main, asset_id="picture", label="Target")
    state = edit(world, state, recast.plan_recast, range=None)
    for analysis, votes in zip(world.calls["analysis"], ({"t2": 3}, {"t1": 3}), strict=True):
        person = world.window_reports[analysis]["tracks"][0]
        person["identity"]["film_tracks"] = votes
        person["identity_margin"] = person["identity"]["margin"] = 0.05
        world.window_reports[analysis]["tracks"] = [person]
        world.touch_window(analysis)
    world.calls["plan"].clear()
    state = world.invoke(recast.recast_state, state["id"])
    assert state["shots"][0]["plan"]["gaps"] == []
    assert [r["performers"][0]["selected_by"] for r in world.calls["plan"]] == ["user", "user"]


def test_identity_margin_is_measured_over_characters_after_a_merge(world):
    # Regression (8 Oct trial, shot 56): the actor's frontal faces sat closer to his own
    # looking-down cluster than to their cluster, so the cluster margin was negative.
    world.report["tracks"] = world.report["tracks"][:3]
    world.report["clusters"] = [
        {**world.report["clusters"][0], "id": f"c0{i + 1}", "track_ids": [f"t{i + 1}"]}
        for i in range(3)
    ]
    world.face_embeddings.update(t2=(0.995, 0.0998, 0, 0), t3=(0, 0, 1, 0))
    state = world.create()
    main, profile, _other = (c["id"] for c in state["characters"])
    state = edit(world, state, recast.set_target, main, asset_id="picture", label="Target")
    state = edit(world, state, recast.plan_recast, range={"start": 0, "end": 73})
    before = world.calls["plan"][-1]["performers"][0]
    assert before["selected_by"] == "identity" and before["identity_margin"] < 0.01
    state = edit(world, state, recast.merge_characters, into=main, sources=[profile])
    after = world.calls["plan"][-1]["performers"][0]
    assert after["selected_by"] == "identity" and after["identity_margin"] > 0.9


def test_three_mapped_characters_are_a_gap(world):
    for index in (1, 2):
        world.report["tracks"][index]["shot"] = 1
        world.report["tracks"][index]["frames"] = [0, 5, 10]
    state = world.create()
    state = edit(world, state, recast.move_tracks, track_ids=["t2"], to="new")
    state = edit(world, state, recast.move_tracks, track_ids=["t3"], to="new")
    for character in state["characters"][:]:
        state = edit(
            world, state, recast.set_target, character["id"], asset_id="picture", label="Target"
        )
    state = edit(world, state, recast.plan_recast, range=None)
    assert state["shots"][0]["status"] == "gap"
    assert state["shots"][0]["plan"]["gaps"][0]["code"] == "too_many_performers"


def test_failed_analysis_can_be_retried_and_running_cannot(world):
    # Regression (8 Oct real-film trial): a failed film analysis left no way forward.
    state = world.invoke(
        recast.create_recast, source_asset_id="film", title=None, request_key="retry"
    )
    with pytest.raises(ApplicationError) as running:
        world.invoke(recast.retry_analysis, state["id"], expected_revision=state["revision"])
    assert running.value.status == 409
    first = state["analysis"]["operation_id"]
    with world.db.transaction() as session:
        world.settle(session, first, state="failed")
    state = world.invoke(recast.recast_state, state["id"])
    assert state["analysis"]["state"] == "failed"
    state = edit(world, state, recast.retry_analysis)
    assert state["analysis"]["operation_id"] != first
    assert state["analysis"]["state"] == "running"
    with world.db.transaction() as session:
        world.settle(session, state["analysis"]["operation_id"])
    assert world.invoke(recast.recast_state, state["id"])["analysis"]["state"] == "succeeded"


def test_shot_planning_status_and_pending_estimate(world, monkeypatch):
    state = world.create()
    character = state["characters"][0]["id"]
    state = edit(world, state, recast.set_target, character, asset_id="picture", label="Lead")
    assert all(s["status"] == "unplanned" and s["plan"] is None for s in state["shots"])
    assert state["estimate"] is None
    state = edit(world, state, recast.plan_recast, range=None)
    assert all(s["status"] == "planned" and s["plan"]["windows"] for s in state["shots"])
    assert state["estimate"]["windows_pending"] == 0

    operation = p.operations.operation
    pending = set(world.calls["analysis"])

    def with_pending(session, operation_id):
        result = operation(session, operation_id)
        return {**result, "state": "running"} if operation_id in pending else result

    monkeypatch.setattr(p.operations, "operation", with_pending)
    state = world.invoke(recast.recast_state, state["id"])
    assert all(s["status"] == "analysing" and not s["plan"]["windows"] for s in state["shots"])
    assert state["estimate"]["windows"] == state["estimate"]["windows_pending"] == 4
    pending.pop()
    state = world.invoke(recast.recast_state, state["id"])
    assert state["estimate"]["windows"] == 4
    assert state["estimate"]["windows_pending"] == 3
    assert sum(s["status"] == "planned" for s in state["shots"]) == 1
    pending.clear()
    state = world.invoke(recast.recast_state, state["id"])
    assert all(s["status"] == "planned" for s in state["shots"])
    assert state["estimate"]["renders"] == 8
    RecastDTO.model_validate(state)


def test_similar_characters_follow_owners_merge_and_visibility(world):
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
    a, b, c = [v["id"] for v in state["characters"]]
    assert state["characters"][0]["similar"] == [
        {"character_id": b, "similarity": 0.8},
        {"character_id": c, "similarity": 0.7},
    ]
    state = edit(world, state, recast.move_tracks, track_ids=["t1"], to="new")
    moved = state["characters"][-1]
    assert moved["similar"] == state["characters"][0]["similar"]
    state = edit(world, state, recast.merge_characters, into=b, sources=[a, moved["id"]])
    assert len(state["characters"]) == 2
    assert state["characters"][0]["similar"] == [{"character_id": c, "similarity": 0.75}]
    assert state["characters"][1]["similar"] == [{"character_id": b, "similarity": 0.75}]
    state = edit(world, state, recast.patch_character, c, hidden=True)
    assert all(not character["similar"] for character in state["characters"])
    state = edit(world, state, recast.patch_character, c, hidden=False)
    state = edit(world, state, recast.move_tracks, track_ids=["t4"], to="unknown")
    assert all(not character["similar"] for character in state["characters"])


def test_character_reference_migration_and_round_trip(world):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    state = world.ready()
    config = Config()
    config.set_main_option("script_location", MIGRATIONS.as_posix())
    config.set_main_option("sqlalchemy.url", "sqlite:///" + world.db.path.as_posix())
    command.downgrade(config, "0004_recast_shot_layers")
    assert "reference_state" not in {
        c["name"] for c in inspect(world.db.engine).get_columns("editing_recast_characters")
    }
    command.upgrade(config, "head")
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        assert stored["characters"][0]["reference_state"] is None
        assert stored["characters"][0]["reference_asset_id"] is None
        stored["characters"][0].update(reference_state="waiting", reference_asset_id="picture2")
        p.store.save(session, stored)
    with world.db.transaction() as session:
        character = p.store.load(session, state["id"])["characters"][0]
        assert (
            character["reference_state"] == "waiting"
            and character["reference_asset_id"] == "picture2"
        )


def test_bridge_migration_and_round_trip(world):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect

    state = world.ready()
    config = Config()
    config.set_main_option("script_location", MIGRATIONS.as_posix())
    config.set_main_option("sqlalchemy.url", "sqlite:///" + world.db.path.as_posix())
    command.downgrade(config, "0005_recast_character_reference")
    assert "bridge_ops" not in {
        c["name"] for c in inspect(world.db.engine).get_columns("editing_recast_shots")
    }
    command.upgrade(config, "head")
    with world.db.transaction() as session:
        stored = p.store.load(session, state["id"])
        assert stored["shots"][0]["bridge_ops"] == []
        stored["shots"][0]["bridge_ops"] = ["bridge-1", "bridge-2"]
        p.store.save(session, stored)
    with world.db.transaction() as session:
        assert p.store.load(session, state["id"])["shots"][0]["bridge_ops"] == [
            "bridge-1",
            "bridge-2",
        ]


def test_appearance_link_migration_constraints_round_trip_and_rollback(world):
    from alembic import command
    from alembic.config import Config
    from sqlalchemy import inspect
    from sqlalchemy.exc import IntegrityError

    from gaiavfx.modules.editing.recast.persistence import AppearanceLinkRecord

    state = world.ready()
    config = Config()
    config.set_main_option("script_location", MIGRATIONS.as_posix())
    config.set_main_option("sqlalchemy.url", "sqlite:///" + world.db.path.as_posix())
    command.downgrade(config, "0006_recast_shot_bridges")
    assert "editing_recast_appearance_links" not in inspect(world.db.engine).get_table_names()
    command.upgrade(config, "head")
    with world.db.transaction() as session:
        assert p.store.load(session, state["id"])["appearance_links"] == []
    row = {
        "recast_id": state["id"],
        "shot_id": "s0001",
        "analysis_operation_id": world.calls["analysis"][0],
        "track_id": "person0",
        "character_id": state["characters"][0]["id"],
        "state": "proposed",
        "proposed_by": "analysis",
        "evidence": {"scores": [0.5]},
        "created_at": "now",
        "decided_at": None,
    }
    for invalid in (
        {"state": "accepted"},
        {"proposed_by": "model"},
        {"state": "confirmed"},
        {"decided_at": "now"},
    ):
        with pytest.raises(IntegrityError), world.db.transaction() as session:
            session.add(AppearanceLinkRecord(**{**row, **invalid}))
            session.flush()
    with pytest.raises(RuntimeError, match="rollback"), world.db.transaction() as session:
        aggregate = p.store.load(session, state["id"])
        aggregate["appearance_links"].append(row)
        p.store.save(session, aggregate)
        raise RuntimeError("rollback")
    with world.db.transaction() as session:
        aggregate = p.store.load(session, state["id"])
        assert aggregate["appearance_links"] == []
        aggregate["appearance_links"].append(row)
        p.store.save(session, aggregate)
    with world.db.transaction() as session:
        aggregate = p.store.load(session, state["id"])
        assert aggregate["appearance_links"] == [row]
        aggregate["appearance_links"][0].update(state="confirmed", decided_at="later")
        p.store.save(session, aggregate)
    with world.db.transaction() as session:
        aggregate = p.store.load(session, state["id"])
        assert aggregate["appearance_links"][0]["decided_at"] == "later"
        aggregate["appearance_links"] = []
        p.store.save(session, aggregate)
    with world.db.transaction() as session:
        assert p.store.load(session, state["id"])["appearance_links"] == []
    command.downgrade(config, "0006_recast_shot_bridges")
    command.upgrade(config, "head")
