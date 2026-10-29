"""A set seeded from a take (ADR 0019 §4): the reconstruction gate and the atlas, on a synthetic textured room whose
geometry is exact. Calibration on real takes is in calibration/worldbuilder-2026-10/take-gate.md."""

import itertools
import json
import math

import numpy as np
import pytest
from synthetic_room import H, W, render, truck
from worldbuilder_splat import frames as fr
from worldbuilder_splat import memory as mem

GATE = fr.Gate(stride=4, spacing=2)


def frames_of(shots, people=None):
    images, depths, views, ks = zip(*shots)
    count = len(shots)
    return fr.Frames(
        image=np.stack(images),
        depth=np.stack(depths),
        confidence=np.ones((count, H, W), np.float32),
        viewmat=np.stack(views),
        k=np.stack(ks),
        people=np.zeros((count, H, W), bool) if people is None else people,
    )


def test_a_camera_moving_through_the_room_passes():
    record, _ = fr.verdict(frames_of(truck()), GATE)
    assert record["passed"], record
    assert record["kept"] == list(range(8))
    assert record["groups"] == [8]
    assert record["agreement"] > 0.9
    assert record["parallax"] > 0.5


def test_a_static_camera_or_a_pan_has_no_parallax():
    static = frames_of([render([0, 0, 0], 0.0) for _ in range(8)])
    record, _ = fr.verdict(static, GATE)
    assert (record["passed"], record["reason"]) == (False, "no_parallax")
    assert record["turn"] < 1 and record["travel"] < 0.01
    pan = frames_of([render([0, 0, 0], yaw) for yaw in np.linspace(-20, 20, 8)])
    record, _ = fr.verdict(pan, GATE)
    assert (record["passed"], record["reason"]) == (False, "no_parallax")
    assert record["turn"] > 30  # said as "turns", not "stands still"


def test_a_take_with_a_cut_does_not_join_into_one_place():
    first = truck(4)
    second = [
        render([0.3 * s, 0, 0.5], 140.0, room=(1.4, 1.0, 0.9, 1.6), phase=2.0)
        for s in np.linspace(0, 1, 4)
    ]
    record, _ = fr.verdict(frames_of(first + second), GATE)
    assert (record["passed"], record["reason"]) == (False, "inconsistent")
    assert record["groups"][:2] == [4, 4]


def test_people_are_excluded_even_where_their_depth_is_missing():
    shots = truck()
    people = np.zeros((8, H, W), bool)
    people[:, 20:50, 40:70] = True
    frames = frames_of(shots, people)
    frames.depth[0, 25:30, 45:50] = np.nan  # a person without depth
    valid, excluded, solid, trust = fr.flags(frames, people_margin=2, edge_ratio=0.08)
    assert excluded[0, 25:30, 45:50].all() and not valid[0, 25:30, 45:50].any()
    assert not (solid & excluded).any() and not (trust & excluded).any()
    assert (
        excluded[:, 18, 40:70].all() and not excluded[:, 10, 40:70].any()
    )  # grown by the margin, no further


def test_the_atlas_keeps_the_frames_in_a_levelled_world_with_people_retracted(tmp_path):
    shots = [render([-0.3 + 0.6 * s, 0.1 * s, 0], 0.0) for s in np.linspace(0, 1, 8)]
    # Tilt the whole world: the gate and the atlas must not care which way DA3's world happened to face.
    tilt = np.eye(4)
    a = math.radians(12)
    tilt[:3, :3] = [[1, 0, 0], [0, math.cos(a), -math.sin(a)], [0, math.sin(a), math.cos(a)]]
    shots = [(image, depth, view @ tilt, k) for image, depth, view, k in shots]
    people = np.zeros((8, H, W), bool)
    people[:, 30:40, 60:70] = True
    frames = frames_of(shots, people)
    record, _ = fr.verdict(frames, GATE)
    assert record["passed"], record
    atlas = fr.build_atlas(frames, tmp_path, "Box room", GATE, record)
    (tmp_path / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
    records = mem.current(atlas)
    assert [r["id"] for r in records] == [f"k{n:02d}" for n in range(1, 9)]
    assert {r["role"] for r in records} == {"generated"}
    assert [r["id"] for r in records if r.get("heldout")] == ["k04"]
    keys = mem.load_keyframes(tmp_path / "atlas.json")
    assert {r["people"] for r in records} == {round(22 * 22 / (H * W), 4)}  # 10 x 10, grown by 6 px
    for key in keys:
        assert key.retracted[30:40, 60:70].all()
        assert not key.solid[30:40, 60:70].any()
        # Level again: every camera's own up is world -y, as the shots were taken before the tilt.
        assert np.allclose(-key.rotation[1], [0, -1, 0], atol=0.02)
    assert np.allclose(fr.frames_centre(keys), [0, 0, 0], atol=0.05)
    assert (
        abs(math.degrees(math.atan2(keys[0].rotation[2, 0], keys[0].rotation[2, 2]))) < 0.5
    )  # K1 at yaw 0


def test_a_camera_looking_down_is_levelled_by_the_floor_not_by_itself():
    # A high-angle dolly: every camera pitched 25 degrees down, so the cameras' own up leans 25 degrees.
    shots = [render([-0.3 + 0.6 * s, -0.4, -0.6], 0.0, pitch=-25.0) for s in np.linspace(0, 1, 8)]
    frames = frames_of(shots)
    record, (_, _, _, trust) = fr.verdict(frames, GATE)
    assert record["passed"], record
    viewmat, info = fr.level(frames, trust, record["kept"])
    assert info["up"] == "floor" and info["tilt_from_cameras"] == pytest.approx(25, abs=1.5)
    # The world is level again: the true down is world +y, so each camera pitches 25 degrees down in it.
    for view in viewmat:
        assert math.degrees(math.asin(view[2, 1])) == pytest.approx(25, abs=1.5)


def test_a_takes_room_is_the_depth_of_what_it_saw(tmp_path):
    frames = frames_of(truck())
    record, _ = fr.verdict(frames, GATE)
    atlas = fr.build_atlas(frames, tmp_path, "Box room", GATE, record)
    (tmp_path / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
    keys = mem.load_keyframes(tmp_path / "atlas.json")
    room = fr.frames_room(keys)
    # The truck faces the far wall 2 rooms-units away; the side walls and floor bring the median in a little.
    assert 1.0 < room < 2.5


def test_withheld_keyframes_are_interior_and_never_the_ends():
    assert fr.heldout(12) == {3, 7}
    assert fr.heldout(8) == {3}
    assert fr.heldout(4) == set()
    for n in range(2, 40):
        assert not fr.heldout(n) & {0, n - 1}


def test_the_research_defaults_are_the_integrations_settings():
    # The app sends take.toml's [gate] with every seed; the dataclass defaults let research scripts measure alike.
    from dataclasses import replace

    from gaiavfx.integrations.worldbuilder.graph import settings

    assert fr.Gate.of(settings()["gate"]) == replace(fr.Gate(), coverage=0.6667)


@pytest.mark.parametrize("confidence", [0.0, float("nan"), 1e6])
def test_da3_confidence_does_not_decide_geometry_or_people(confidence):
    frames = frames_of(truck())
    frames.people[:, 20:35, 40:55] = True
    baseline, baseline_flags = fr.verdict(frames, GATE)
    frames.confidence.fill(confidence)
    actual, actual_flags = fr.verdict(frames, GATE)
    assert actual == baseline
    for actual_mask, baseline_mask in zip(actual_flags, baseline_flags, strict=True):
        np.testing.assert_array_equal(actual_mask, baseline_mask)


# ---------------------------------------------------------------- filling (ADR 0020 §3)


def test_the_layout_follows_the_gate_and_whether_the_unseen_space_will_be_filled():
    moving, _ = fr.verdict(frames_of(truck()), GATE)
    still, _ = fr.verdict(frames_of([render([0, 0, 0], 0.0) for _ in range(8)]), GATE)
    assert (fr.layout(moving, False), fr.layout(moving, True)) == ("moving", "moving")
    assert (fr.layout(still, False), fr.layout(still, True)) == (None, "centre")
    # Filling cannot make one place of a take that is two places, or of one without surface.
    for reason in ("inconsistent", "little_surface"):
        assert fr.layout({"passed": False, "reason": reason}, True) is None


def test_a_still_camera_keeps_one_frame_and_those_that_see_what_people_hid(tmp_path):
    frames = frames_of([render([0.002 * n, 0, 0], 0.0) for n in range(8)])
    record, _ = fr.verdict(frames, GATE)
    assert fr.layout(record, True) == "centre"
    atlas = fr.build_atlas(frames, tmp_path, "Box room", GATE, record, "centre")
    assert atlas["layout"] == "centre"
    assert [r["id"] for r in mem.current(atlas)] == ["k01"]
    assert not any(r.get("heldout") for r in mem.current(atlas))
    # Someone stands on the left for five frames and on the right for the last three.
    frames.people[:5, 15:60, 10:50] = True
    frames.people[5:, 15:60, 70:110] = True
    atlas = fr.build_atlas(frames, tmp_path / "people", "Box room", GATE, record, "centre")
    (tmp_path / "people" / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
    records = mem.current(atlas)
    assert len(records) == 2 and {r["frame"] < 5 for r in records} == {True, False}
    keys = mem.load_keyframes(tmp_path / "people" / "atlas.json")
    # Together they trust the whole picture but the margins; the poses are DA3's, close to one point.
    assert np.logical_or.reduce([k.trust for k in keys])[15:60, 10:110].mean() > 0.9
    assert np.allclose(fr.frames_centre(keys), [0, 0, 0], atol=0.02)


def test_a_turning_camera_keeps_one_keyframe_per_view_at_one_centre(tmp_path):
    frames = frames_of([render([0, 0, 0], yaw) for yaw in np.linspace(-30, 30, 13)])
    record, _ = fr.verdict(frames, GATE)
    assert fr.layout(record, True) == "centre"
    atlas = fr.build_atlas(frames, tmp_path, "Box room", GATE, record, "centre")
    (tmp_path / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
    keys = mem.load_keyframes(tmp_path / "atlas.json")
    spread = fr.hfov(frames.k[0], W) / 4  # 17.5 degrees for the room's 70 degree lens
    headings = sorted(fr.heading(k.viewmat) for k in keys)
    turns = [(b - a) % 360 for a, b in itertools.pairwise(headings)]
    assert 3 <= len(keys) <= 5 and min(turns) >= spread - 1e-6
    for key in keys:
        np.testing.assert_allclose(key.centre, [0, 0, 0], atol=1e-9)


def test_a_tilt_or_a_zoom_is_a_distinct_view_though_the_heading_stays():
    shots = [render([0, 0, 0], 0.0), render([0, 0, 0], 0.0, pitch=-25.0), render([0, 0, 0], 0.0)]
    frames = frames_of(shots)
    trust = np.ones((3, H, W), bool)
    spread = fr.hfov(frames.k[0], W) / 4
    assert fr.distinct_views(frames.viewmat, frames.k, [0, 1, 2], trust, spread) == [0, 1]
    zoomed = frames.k.copy()
    zoomed[2, :2, :2] *= 1.5
    assert fr.distinct_views(frames.viewmat, zoomed, [0, 1, 2], trust, spread) == [0, 1, 2]


def test_a_centre_takes_frames_are_fixed_anchors_and_a_moving_takes_train(tmp_path):
    from worldbuilder_splat import splat2

    for layout, shots in (
        ("centre", [render([0, 0, 0], yaw) for yaw in np.linspace(-30, 30, 9)]),
        ("moving", truck()),
    ):
        folder = tmp_path / layout
        frames = frames_of(shots)
        record, _ = fr.verdict(frames, GATE)
        atlas = fr.build_atlas(frames, folder, "Box room", GATE, record, layout)
        (folder / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
        scene, seed = fr.prepare_frames(folder / "state", folder / "atlas.json")
        splat2.validate_scene(scene)
        roles = {v["role"] for v in scene["views"]}
        # Each view says how its pose is treated: a centre take's frames are fixed anchors (no camera optimiser,
        # a guaranteed share of the steps); a moving take's frames train with their poses refined.
        assert scene["kind"] == "orbit"
        assert roles == ({"anchor"} if layout == "centre" else {"train", "heldout"})
        assert {v["source"] for v in scene["views"]} == {"take"}
        assert scene["layout"] == layout and len(seed[0]) > 0


def test_filled_stills_join_a_take_scene_with_fixed_poses(tmp_path):
    from worldbuilder_splat import explore as ex

    frames = frames_of(truck())
    record, _ = fr.verdict(frames, GATE)
    atlas = fr.build_atlas(frames, tmp_path, "Box room", GATE, record, "moving")
    (tmp_path / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
    scene, _ = fr.prepare_frames(tmp_path / "state", tmp_path / "atlas.json")
    take_views = len(scene["views"])
    # A fill round's result: one accepted still, one trajectory frame withheld, one still kept for its looks only.
    image, depth, view, k = render([0.1, 0, 0.3], 40.0)
    still = mem.view_keyframe("p1", image, view, k, depth)
    image, depth, view, k = render([0.05, 0, 0.15], 20.0)
    frame = mem.view_keyframe("p1_t24", image, view, k, depth)
    looks = mem.view_keyframe("p2", image, view, k, depth)
    for key, extra, decision in (
        (still, {"role": "generated"}, "accepted"),
        (frame, {"role": mem.TRAJECTORY, "heldout": True}, mem.TRAJECTORY),
        (looks, {"role": mem.APPEARANCE_ONLY}, mem.APPEARANCE_ONLY),
    ):
        record = mem.keyframe_record(key, tmp_path / "keyframes", tmp_path, **extra)
        atlas = ex.add_keyframe(atlas, record, decision)
    (tmp_path / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
    seed = fr.frame_views(scene, tmp_path / "state", tmp_path / "atlas.json")
    added = scene["views"][take_views:]
    assert [(v["name"], v["role"], v["fixed"], v["source"]) for v in added] == [
        ("kf_p1", "train", True, "fill"),
        ("kf_p1_t24", "heldout", True, "fill"),
    ]
    assert len(seed[0]) > 0  # from the accepted still only
    assert fr.frame_views(scene, tmp_path / "state", tmp_path / "atlas.json")[0].shape == (0, 3)


@pytest.mark.parametrize("layout", ["moving", "centre"])
def test_rebuilding_a_filled_atlas_keeps_its_stills_and_trajectory_frames_apart_from_the_take(
    tmp_path,
    layout,
):
    # A repair's first fit (Fill more, Run queued fixes) rebuilds a filled version: its accepted stills are fixed
    # fill views, not the take's own frames, and its trajectory frames stay (ADR 0020 §8).
    from worldbuilder_splat import explore as ex

    shots = truck() if layout == "moving" else [render([0, 0, 0], yaw) for yaw in (-30, 0, 30)]
    frames = frames_of(shots)
    record, _ = fr.verdict(frames, GATE)
    atlas = fr.build_atlas(frames, tmp_path, "Box room", GATE, record, layout)
    image, depth, view, k = render([0.1, 0, 0.3], 40.0)
    still = mem.view_keyframe("r1p0", image, view, k, depth)
    image, depth, view, k = render([0.05, 0, 0.15], 20.0)
    frame = mem.view_keyframe("r1p0_t24", image, view, k, depth)
    for key, extra, decision in (
        (still, {"role": "generated", "source": "r1p0", "consolidation": {}}, "accepted"),
        (frame, {"role": mem.TRAJECTORY, "source": "r1p0#24"}, mem.TRAJECTORY),
    ):
        atlas = ex.add_keyframe(
            atlas, mem.keyframe_record(key, tmp_path / "keyframes", tmp_path, **extra), decision
        )
    (tmp_path / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
    scene, seed = fr.prepare_frames(tmp_path / "state", tmp_path / "atlas.json")
    filled = [v for v in scene["views"] if v["source"] == "fill"]
    assert [(v["name"], v["role"], v["fixed"]) for v in filled] == [
        ("kf_r1p0", "train", True),
        ("kf_r1p0_t24", "train", True),
    ]
    take = [v for v in scene["views"] if v["source"] == "take"]
    assert len(take) == len(mem.current(atlas)) - 2
    assert {v["role"] for v in take} == ({"train", "heldout"} if layout == "moving" else {"anchor"})
    assert len(seed[0]) > 0


def test_a_centre_layout_preserves_da3_relative_poses_instead_of_snapping(tmp_path):
    # A small translation in a mostly turning reconstruction is intentional: DA3 trades rotation for motion.
    frames = frames_of(
        [render([0.003 * n, 0, 0], yaw) for n, yaw in enumerate(np.linspace(-30, 30, 9))]
    )
    record, _ = fr.verdict(frames, GATE)
    assert fr.layout(record, True) == "centre"
    atlas = fr.build_atlas(frames, tmp_path, "Box room", GATE, record, "centre")
    (tmp_path / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
    records = mem.current(atlas)
    keys = mem.load_keyframes(tmp_path / "atlas.json")
    assert len(keys) >= 3
    first_frame = frames.viewmat[records[0]["frame"]]
    for key, row in zip(keys[1:], records[1:], strict=True):
        # World levelling may change absolute poses; the camera-to-camera transform must stay identical.
        expected = frames.viewmat[row["frame"]] @ np.linalg.inv(first_frame)
        actual = key.viewmat @ np.linalg.inv(keys[0].viewmat)
        np.testing.assert_allclose(actual, expected, atol=1e-7)
    assert max(np.linalg.norm(key.centre - keys[0].centre) for key in keys) > 0.01


def test_centre_seed_reports_heldout_quality_as_unavailable(tmp_path, monkeypatch):
    from worldbuilder_splat import splat2

    frames = frames_of([render([0, 0, 0], 0.0) for _ in range(8)])
    record, _ = fr.verdict(frames, GATE)
    atlas = fr.build_atlas(frames, tmp_path, "Box room", GATE, record, "centre")
    (tmp_path / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
    out = tmp_path / "state"
    scene, _ = fr.prepare_frames(out, tmp_path / "atlas.json")
    scene.update(gaussians=4, steps=1, fit_seconds=0)
    monkeypatch.setattr(
        splat2,
        "render_numpy",
        lambda params, view, k, w, h: (
            np.zeros((h, w, 3), np.float32),
            None,
            None,
        ),
    )
    monkeypatch.setattr(
        splat2, "heldout_metrics", lambda *args: pytest.fail("no second viewpoint exists")
    )
    result = fr.frames_metrics(out, scene, None)
    assert result["heldout_psnr"] is None and result["heldout_psnr_refined"] is None
    saved = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    assert saved["heldout_psnr"] is None and saved["heldout_psnr_refined"] is None


def test_anchor_quality_leaves_out_the_people_nothing_trains(tmp_path, monkeypatch):
    # The GPU qualification (28 October): a filled stage drew the place where the actor stood, and the whole-frame
    # anchor PSNR fell 4.4 dB although the place's own pixels still rendered back at 38 dB.
    from PIL import Image
    from worldbuilder_splat import splat2

    people = np.zeros((8, H, W), bool)
    people[:, 20:50, 40:70] = True
    frames = frames_of([render([0, 0, 0], 0.0) for _ in range(8)], people)
    record, _ = fr.verdict(frames, GATE)
    atlas = fr.build_atlas(frames, tmp_path, "Box room", GATE, record, "centre")
    (tmp_path / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
    out = tmp_path / "state"
    scene, _ = fr.prepare_frames(out, tmp_path / "atlas.json")
    scene.update(gaussians=4, steps=1, fit_seconds=0)
    (view,) = scene["views"]
    # The splat renders the place exactly, and something else where the actor was.
    place = np.asarray(Image.open(out / view["image"]).convert("RGB"), np.float32) / 255
    drawn = place.copy()
    drawn[20:50, 40:70] = 1 - drawn[20:50, 40:70]
    monkeypatch.setattr(splat2, "render_numpy", lambda params, v, k, w, h: (drawn, None, None))
    assert fr.frames_metrics(out, scene, None)["anchor_psnr"] >= 100  # the exact-match ceiling
