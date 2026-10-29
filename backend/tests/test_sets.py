"""Sets (ADR 0018): bundle checks and import, version authority, issues and fixes, hidden regions, saved views, the
map payload and the signed splat route."""

import base64
import hashlib
import io
import json
import struct
from pathlib import Path

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gaiavfx.api.app import create_app
from gaiavfx.application import set_bundles
from gaiavfx.infrastructure.database import Database
from gaiavfx.modules.sets.service import SetStore
from gaiavfx.shared.errors import ApplicationError

MIGRATIONS = Path(__file__).parents[1] / "migrations"
PROPERTIES = [
    "x",
    "y",
    "z",
    "f_dc_0",
    "f_dc_1",
    "f_dc_2",
    "f_rest_0",
    "f_rest_1",
    "f_rest_2",
    "f_rest_3",
    "f_rest_4",
    "f_rest_5",
    "f_rest_6",
    "f_rest_7",
    "f_rest_8",
    "opacity",
    "scale_0",
    "scale_1",
    "scale_2",
    "rot_0",
    "rot_1",
    "rot_2",
    "rot_3",
]


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def entry(bundle: Path, relative: str) -> dict:
    return {"path": relative, "sha256": sha(bundle / relative)}


def write_ply(path: Path, count=5, properties=PROPERTIES):
    header = (
        "ply\nformat binary_little_endian 1.0\n"
        f"element vertex {count}\n"
        + "".join(f"property float {name}\n" for name in properties)
        + "end_header\n"
    ).encode()
    path.write_bytes(
        header + struct.pack(f"<{count * len(properties)}f", *([0.5] * (count * len(properties))))
    )


def keyframe(
    bundle: Path, name: str, kind: str, width: int, height: int, viewmat, colour, label, role
):
    Image.new("RGB", (width, height), colour).save(bundle / f"keyframes/{name}.png")
    depth = np.full((height, width), 2.0, np.float32)
    valid = np.ones((height, width), bool)
    np.savez_compressed(
        bundle / f"keyframes/{name}.npz", depth=depth, valid=valid, trust=valid, solid=valid
    )
    return {
        "key": name,
        "label": label,
        "kind": kind,
        "role": role,
        "viewmat": viewmat,
        "intrinsics": None if kind == "erp" else [[20.0, 0, 16], [0, 20.0, 9], [0, 0, 1]],
        "width": width,
        "height": height,
        "heading": None if kind == "erp" else 90.0,
        "image": entry(bundle, f"keyframes/{name}.png"),
        "depth": entry(bundle, f"keyframes/{name}.npz"),
        "info": {},
    }


def make_bundle(root: Path, name="Tiny shack") -> Path:
    bundle = root / "bundle"
    (bundle / "keyframes").mkdir(parents=True)
    eye = np.eye(4).tolist()
    turn = [[0.0, 0.0, -1.0, 0.0], [0.0, 1.0, 0.0, 0.0], [1.0, 0.0, 0.0, 0.0], [0, 0, 0, 1.0]]
    keys = [
        keyframe(bundle, "pano", "erp", 64, 32, eye, "navy", "K0", "observed"),
        keyframe(bundle, "r0p0", "pinhole", 32, 18, turn, "olive", "K1", "generated"),
        keyframe(bundle, "r0p0_t24", "pinhole", 32, 18, turn, "teal", "A1", "trajectory"),
    ]
    plane = np.zeros((6, 5), np.int8)
    plane[1:5, 1:4] = 1
    plane[0] = 2
    frontier = np.zeros_like(plane, bool)
    frontier[4, 1:4] = True
    np.savez_compressed(
        bundle / "map.npz", plane=plane, frontier=frontier, lower=np.zeros(3), voxel=np.float32(0.1)
    )
    write_ply(bundle / "splat.ply")
    (bundle / "atlas.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "version": 3,
                "keyframes": [
                    {
                        "id": k["key"],
                        "kind": k["kind"],
                        "image": k["image"]["path"],
                        "data": k["depth"]["path"],
                    }
                    for k in keys
                ],
            }
        ),
        encoding="utf-8",
    )
    document = {
        "schema": 1,
        "name": name,
        "place_text": "A tiny wooden fishing shack.",
        "seed": 7101,
        "provenance": {"source": "test"},
        "version": {
            "origin": "import",
            "note": "test bundle",
            "atlas": entry(bundle, "atlas.json"),
            "map": entry(bundle, "map.npz"),
            "splat": entry(bundle, "splat.ply"),
            "map_meta": {
                "voxel": 0.1,
                "lower": [0, 0, 0],
                "shape": [6, 5],
                "cameras": [
                    {"label": "K0", "id": "pano", "position": [0, 0, 0], "heading": None},
                    {"label": "K1", "id": "r0p0", "position": [0, 0, 0], "heading": 90.0},
                ],
            },
            "metrics": {"uncalibrated": True},
            "keyframes": keys,
        },
        "issues": [
            {
                "kind": "floaters",
                "why": "A dark smear hangs in front of the window.",
                "region": {"time": 4.0, "viewmat": eye},
                "note": "R6 splat check",
            },
            {"kind": "made-up", "why": "Unknown kinds become other.", "cells": ["B2"]},
        ],
    }
    (bundle / "set.json").write_text(json.dumps(document), encoding="utf-8")
    return bundle


def rewrite(bundle: Path, change):
    document = json.loads((bundle / "set.json").read_text(encoding="utf-8"))
    change(document)
    (bundle / "set.json").write_text(json.dumps(document), encoding="utf-8")


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path / "library", MIGRATIONS, "token", enable_runner=False)
    with TestClient(
        app, base_url="http://localhost", headers={"Authorization": "Bearer token"}
    ) as client:
        assert client.post("/api/v1/library", json={"name": "Sets"}).status_code == 201
        yield client


# ---------------------------------------------------------------- bundle checks


def test_a_bundle_is_checked_whole_before_anything_is_registered(tmp_path):
    bundle = make_bundle(tmp_path)
    checked = set_bundles.read_bundle(bundle)
    assert [k["label"] for k in checked["keyframes"]] == ["K0", "K1", "A1"]
    assert checked["files"]["splat_header"] == {"gaussians": 5, "sh_degree": 1, "stride": 92}


@pytest.mark.parametrize(
    "breakage, message",
    [
        (lambda b: (b / "keyframes/pano.png").write_bytes(b"changed"), "does not match its sha256"),
        (
            lambda b: rewrite(
                b, lambda d: d["version"]["keyframes"][1]["image"].update(path="../escape.png")
            ),
            "not a file inside the bundle",
        ),
        (
            lambda b: rewrite(
                b,
                lambda d: d["version"]["keyframes"][1].update(
                    viewmat=[[2, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]]
                ),
            ),
            "not a rigid camera pose",
        ),
        (
            lambda b: rewrite(b, lambda d: d["version"]["keyframes"].reverse()),
            "must be the panorama",
        ),
        (
            lambda b: rewrite(b, lambda d: d["version"]["keyframes"][2].update(label="K1")),
            "labels must be unique",
        ),
    ],
)
def test_broken_bundles_are_refused(tmp_path, breakage, message):
    bundle = make_bundle(tmp_path)
    breakage(bundle)
    with pytest.raises(ApplicationError, match=message):
        set_bundles.read_bundle(bundle)


def test_depth_npz_with_pickled_objects_is_refused(tmp_path):
    bundle = make_bundle(tmp_path)
    np.savez(bundle / "keyframes/r0p0.npz", depth=np.array([object()], dtype=object))
    rewrite(
        bundle,
        lambda d: d["version"]["keyframes"][1]["depth"].update(
            sha256=sha(bundle / "keyframes/r0p0.npz")
        ),
    )
    with pytest.raises(ApplicationError, match="not a readable npz"):
        set_bundles.read_bundle(bundle)


def test_a_splat_without_3dgs_properties_is_refused(tmp_path):
    bundle = make_bundle(tmp_path)
    write_ply(bundle / "splat.ply", properties=("x", "y", "z"))
    rewrite(bundle, lambda d: d["version"]["splat"].update(sha256=sha(bundle / "splat.ply")))
    with pytest.raises(ApplicationError, match="lacks gaussians or a 3DGS property"):
        set_bundles.read_bundle(bundle)


# ---------------------------------------------------------------- import and authority


def test_import_registers_a_proposed_version_that_only_the_human_makes_current(api, tmp_path):
    response = api.post("/api/sets/import", json={"folder": str(make_bundle(tmp_path))})
    assert response.status_code == 201, response.text
    state = response.json()
    version = state["version"]
    assert state["current_version_id"] is None
    assert (version["state"], version["origin"]) == ("proposed", "import")
    assert [k["label"] for k in version["keyframes"]] == ["K0", "K1", "A1"]
    assert version["splat"]["gaussians"] == 5
    assert [(i["author"], i["kind"], i["status"]) for i in state["issues"]] == [
        ("judge", "floaters", "open"),
        ("judge", "other", "open"),
    ]
    listed = api.get("/api/sets").json()
    assert listed[0]["open_issues"] == 2 and listed[0]["poster"]["thumbnail"].startswith(
        "/api/v1/media/"
    )
    poster = api.get(listed[0]["poster"]["thumbnail"])
    assert poster.status_code == 200

    accepted = api.post(
        f"/api/sets/{state['id']}/versions/{version['id']}/accept",
        json={"expected_current_id": None},
    ).json()
    assert accepted["current_version_id"] == version["id"]
    assert accepted["version"]["state"] == "accepted"
    again = api.post(
        f"/api/sets/{state['id']}/versions/{version['id']}/accept",
        json={"expected_current_id": None},
    )
    assert again.status_code == 409


def test_acceptance_never_replaces_a_newer_current_version(api, tmp_path):
    state = api.post("/api/sets/import", json={"folder": str(make_bundle(tmp_path))}).json()
    store, db = SetStore(), Database(tmp_path / "library")
    try:
        with db.transaction() as session:
            keyframes = store.keyframes(session, state["version"]["id"])
            second = store.add_version(
                session,
                state["id"],
                origin="repair",
                atlas_artifact_id="atlas",
                keyframe_ids=[k["id"] for k in keyframes],
                labels=[k["label"] for k in keyframes],
            )
            third = store.add_version(
                session,
                state["id"],
                origin="repair",
                atlas_artifact_id="atlas",
                keyframe_ids=[keyframes[0]["id"]],
            )
    finally:
        db.close()
    url = f"/api/sets/{state['id']}/versions"
    assert api.post(f"{url}/{second}/accept", json={"expected_current_id": None}).status_code == 200
    stale = api.post(f"{url}/{third}/accept", json={"expected_current_id": None})
    assert stale.status_code == 409 and "Compare against it first" in stale.json()["detail"]
    discarded = api.post(f"{url}/{third}/discard").json()
    assert discarded["current_version_id"] == second
    assert [v["state"] for v in discarded["versions"]] == ["proposed", "accepted", "discarded"]


# ---------------------------------------------------------------- issues, fixes, regions, views


def test_human_tools_queue_fixes_hide_regions_and_snapshot_views(api, tmp_path):
    state = api.post("/api/sets/import", json={"folder": str(make_bundle(tmp_path))}).json()
    set_id, version_id = state["id"], state["version"]["id"]
    marked = api.post(
        f"/api/sets/{set_id}/issues",
        json={
            "version_id": version_id,
            "kind": "hole",
            "why": "Black hole by the door.",
            "region": {"point": [0.1, 0.0, 0.4]},
        },
    ).json()
    human = next(i for i in marked["issues"] if i["author"] == "human")
    assert human["region"] == {"point": [0.1, 0.0, 0.4]}

    queued = api.post(
        f"/api/sets/{set_id}/fixes",
        json={
            "version_id": version_id,
            "action": "reshoot",
            "target": {"viewmat": np.eye(4).tolist()},
            "shows": "The door wall continues, plain planks.",
            "issue_id": human["id"],
        },
    ).json()
    fix = queued["fixes"][0]
    assert fix["state"] == "queued" and fix["author"] == "human"
    assert next(i for i in queued["issues"] if i["id"] == human["id"])["status"] == "queued"
    dismiss = api.patch(
        f"/api/sets/{set_id}/issues/{human['id']}",
        json={"status": "dismissed", "expected_revision": queued["revision"]},
    )
    assert dismiss.status_code == 409  # a queued issue is cancelled first
    cancelled = api.delete(f"/api/sets/{set_id}/fixes/{fix['id']}").json()
    assert next(i for i in cancelled["issues"] if i["id"] == human["id"])["status"] == "open"
    stale = api.patch(
        f"/api/sets/{set_id}/issues/{human['id']}",
        json={"status": "dismissed", "expected_revision": queued["revision"]},
    )
    assert stale.status_code == 409  # the set changed since that revision
    dismissed = api.patch(
        f"/api/sets/{set_id}/issues/{human['id']}",
        json={"status": "dismissed", "expected_revision": cancelled["revision"]},
    ).json()
    assert next(i for i in dismissed["issues"] if i["id"] == human["id"])["status"] == "dismissed"

    hidden = api.post(
        f"/api/sets/{set_id}/hide-regions",
        json={
            "version_id": version_id,
            "shape": "sphere",
            "params": {"centre": [0, 0, 1], "radius": 0.2},
        },
    ).json()
    region = hidden["hide_regions"][0]
    viewed = api.post(
        f"/api/sets/{set_id}/views",
        json={
            "version_id": version_id,
            "name": "Door",
            "viewmat": np.eye(4).tolist(),
            "fov": 60,
            "width": 1024,
            "height": 576,
        },
    ).json()
    assert viewed["views"][0]["hide_region_ids"] == [region["id"]]
    removed = api.patch(
        f"/api/sets/{set_id}/hide-regions/{region['id']}", json={"removed": True}
    ).json()
    assert removed["hide_regions"] == []
    restored = api.patch(
        f"/api/sets/{set_id}/hide-regions/{region['id']}", json={"removed": False}
    ).json()
    assert [r["id"] for r in restored["hide_regions"]] == [region["id"]]
    bad_view = api.post(
        f"/api/sets/{set_id}/views",
        json={
            "version_id": version_id,
            "viewmat": [[2, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]],
            "fov": 60,
            "width": 64,
            "height": 64,
        },
    )
    assert bad_view.status_code == 422


# ---------------------------------------------------------------- map and splat delivery


def test_map_payload_and_signed_splat_with_ranges(api, tmp_path):
    state = api.post("/api/sets/import", json={"folder": str(make_bundle(tmp_path))}).json()
    version = state["version"]
    payload = api.get(f"/api/sets/{state['id']}/versions/{version['id']}/map").json()
    plane = np.frombuffer(base64.b64decode(payload["plane"]), np.int8).reshape(payload["shape"])
    frontier = np.unpackbits(np.frombuffer(base64.b64decode(payload["frontier"]), np.uint8))
    assert payload["shape"] == [6, 5] and plane[0].tolist() == [2] * 5
    assert frontier[: 6 * 5].reshape(6, 5)[4, 1:4].all()

    url = version["splat"]["url"]
    whole = api.get(url, headers={"Authorization": ""})
    assert whole.status_code == 200
    assert "immutable" in whole.headers["cache-control"]
    assert whole.headers["content-type"] == "application/octet-stream"
    assert whole.content.startswith(b"ply\n")
    part = api.get(url, headers={"Range": "bytes=0-2"})
    assert part.status_code == 206 and part.content == b"ply"
    forged = api.get(url.replace("signature=", "signature=0"))
    assert forged.status_code == 401
    elsewhere = api.get(f"/api/sets/{state['id']}/versions/missing/map")
    assert elsewhere.status_code == 404


def png_bytes(colour, size=(128, 72)):
    out = io.BytesIO()
    Image.new("RGB", size, colour).save(out, "PNG")
    return out.getvalue()


def test_a_saved_view_keeps_one_picture_with_its_provenance(api, tmp_path):
    state = api.post("/api/sets/import", json={"folder": str(make_bundle(tmp_path))}).json()
    set_id, version_id = state["id"], state["version"]["id"]
    saved = api.post(
        f"/api/sets/{set_id}/views",
        json={
            "version_id": version_id,
            "name": "Door",
            "viewmat": np.eye(4).tolist(),
            "fov": 60,
            "width": 1024,
            "height": 576,
        },
    ).json()
    view = saved["views"][0]
    assert view["still"] is None
    url = f"/api/sets/{set_id}/views/{view['id']}/still"
    stilled = api.post(url, files={"file": ("view.png", png_bytes("navy"), "image/png")})
    assert stilled.status_code == 200, stilled.text
    view = stilled.json()["views"][0]
    assert view["still"]["original"].startswith("/api/v1/media/")
    asset = api.get(f"/api/v1/assets/{view['asset_id']}").json()
    entry = next(p for p in asset["provenance"] if p["type"] == "set_view")
    assert entry["view_id"] == view["id"] and entry["version_id"] == version_id
    again = api.post(url, files={"file": ("view.png", png_bytes("olive"), "image/png")})
    assert again.status_code == 409
    junk = api.post(url, files={"file": ("view.png", b"not a png", "image/png")})
    assert junk.status_code == 422


def test_the_human_can_call_an_issue_fixed_and_reopen_it(api, tmp_path):
    state = api.post("/api/sets/import", json={"folder": str(make_bundle(tmp_path))}).json()
    issue = state["issues"][0]
    fixed = api.patch(
        f"/api/sets/{state['id']}/issues/{issue['id']}",
        json={"status": "fixed", "expected_revision": state["revision"]},
    ).json()
    assert next(i for i in fixed["issues"] if i["id"] == issue["id"])["status"] == "fixed"
    reopened = api.patch(
        f"/api/sets/{state['id']}/issues/{issue['id']}",
        json={"status": "open", "expected_revision": fixed["revision"]},
    ).json()
    assert next(i for i in reopened["issues"] if i["id"] == issue["id"])["status"] == "open"


def test_a_saved_view_is_the_camera_of_one_angle_of_a_place_built_as_the_set(api, tmp_path):
    """ADR 0019 §2: aiming approves nothing; it says which camera an angle has in the set."""
    state = api.post("/api/sets/import", json={"folder": str(make_bundle(tmp_path))}).json()
    set_id, version_id = state["id"], state["version"]["id"]
    place = api.post("/api/direction/places", json={"name": "Shack"}).json()
    angle = api.post(f"/api/direction/places/{place['id']}/angles", json={"name": "Window"}).json()

    def save(name):
        return api.post(
            f"/api/sets/{set_id}/views",
            json={
                "version_id": version_id,
                "name": name,
                "viewmat": np.eye(4).tolist(),
                "fov": 60,
                "width": 1024,
                "height": 576,
            },
        ).json()["views"][-1]

    first, second = save("Window"), save("Window, wider")

    def aim(view, angle_id):
        return api.put(
            f"/api/sets/{set_id}/views/{view['id']}/angle",
            json={"version_id": version_id, "angle_id": angle_id},
        )

    refused = aim(first, angle["id"])
    assert refused.status_code == 409 and "not built as this stage" in refused.text
    linked = api.put(
        f"/api/direction/places/{place['id']}/set",
        json={"expected_revision": place["revision"], "set_id": set_id},
    )
    assert linked.status_code == 200, linked.text
    views = aim(first, angle["id"]).json()["views"]
    assert views[0]["angle"] == {"id": angle["id"], "name": "Window", "place_name": "Shack"}
    views = aim(second, angle["id"]).json()["views"]
    assert [v["angle"] and v["angle"]["id"] for v in views] == [None, angle["id"]]
    assert aim(second, None).json()["views"][1]["angle"] is None
    assert api.get(f"/api/direction/angles/{angle['id']}/evidence").json() == []
