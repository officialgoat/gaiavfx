"""A stage from a panorama in the stage engine: the panorama's sky, glass and surface judged as the research did, the
atlas it lays out, and probes that start from its crops. On the synthetic box room, whose geometry is exact."""

import io
import json

import numpy as np
from PIL import Image
from synthetic_room import panorama, panorama_npz
from worldbuilder_splat import erp, fill
from worldbuilder_splat import frames as fr
from worldbuilder_splat import geometry as geo
from worldbuilder_splat import memory as mem
from worldbuilder_splat import panorama as pano

RULES = {"sky_factor": 3.0, "glass_max": 0.5, "min_surface": 0.25}


def arrays(distance, sky=None, glass=None):
    with np.load(io.BytesIO(panorama_npz(distance, sky, glass))) as data:
        return {name: data[name] for name in data.files}


def test_sky_must_be_found_and_far():
    _, distance = panorama()
    found = np.zeros(distance.shape, bool)
    found[:20] = True  # SAM calls the ceiling sky
    near = pano.judge(arrays(distance, sky=found), RULES)["record"]
    assert near["sky_found"] > 0.1 and near["sky"] == 0.0
    distance[:20] *= 10  # an open roof: what SAM found is also far away
    far = pano.judge(arrays(distance, sky=found), RULES)
    assert far["record"]["sky"] > 0.1 and not far["mask"][:20].any()


def test_a_whole_frame_of_glass_is_none_and_too_little_surface_is_refused():
    _, distance = panorama()
    judged = pano.judge(arrays(distance, glass=np.ones(distance.shape, bool)), RULES)
    assert judged["record"]["glass_ignored"] and judged["record"]["glass"] == 0.0
    open_sea = np.ones(distance.shape, bool)
    open_sea[100:] = False
    distance[:100] *= 50
    refused = pano.judge(arrays(distance, sky=open_sea), RULES)["record"]
    assert not refused["passed"] and refused["surface"] < RULES["min_surface"]


def test_the_panorama_becomes_the_atlas_k0(tmp_path):
    image, distance = panorama()
    Image.fromarray(image).save(tmp_path / "panorama.png")
    (tmp_path / "geometry.npz").write_bytes(panorama_npz(distance))
    atlas, gate = pano.build_atlas(
        tmp_path / "panorama.png", tmp_path / "geometry.npz", tmp_path / "atlas", "Box room", RULES
    )
    assert gate["passed"] and atlas["place"] == "Box room" and atlas["version"] == 1
    (record,) = atlas["keyframes"]
    assert record["id"] == "K0" and record["kind"] == "erp" and record["role"] == "observed"
    stored = json.loads((tmp_path / "atlas" / "atlas.json").read_text(encoding="utf-8"))
    (key,) = mem.load_keyframes(tmp_path / "atlas" / "atlas.json")
    assert stored == atlas and key.valid.all()
    np.testing.assert_allclose(key.depth, distance, rtol=1e-5)


def test_probes_may_start_from_a_panoramas_crops(tmp_path):
    image, distance = panorama(512, 256)
    key = mem.pano_keyframe("K0", image, distance, np.ones(distance.shape, bool))
    starts = fill.start_keyframes([key])
    assert [s.id for s in starts] == [f"K0@{yaw:03d}" for yaw in range(0, 360, 45)]
    behind = starts[4]
    assert behind.kind == "pinhole" and behind.depth.shape == (576, 1024)
    assert round(fill.heading(behind.viewmat)) == 180
    # A crop's z-depth agrees with the room: straight ahead from the centre, the back wall is 2 m away.
    assert abs(float(behind.depth[288, 512]) - 2.0) < 0.05
    assert behind.valid.mean() > 0.99


def test_a_panorama_stage_is_filled_by_r5s_planner_from_its_crops():
    """A pillar behind the viewpoint hides part of the room: the round steps out from the crops to look past it."""
    width, height = 512, 256
    image, distance = panorama(width, height, room=(3.0, 1.2, 0.8, 3.0))
    lon = (np.arange(width) + 0.5) / width * 360 - 180
    lat = 90 - (np.arange(height) + 0.5) / height * 180
    pillar = (np.abs(np.abs(lon) - 180) < 30)[None, :] & (lat < 40)[:, None]
    rays = erp.directions(width, height) @ geo.F
    distance = np.where(
        pillar, np.minimum(distance, 1.0 / np.maximum(-rays[..., 2], 1e-6)), distance
    )
    key = mem.pano_keyframe("K0", image, distance.astype(np.float32), np.ones(distance.shape, bool))
    room = fr.frames_room([key])
    rules = fill.Rules(grid=(16, 9))
    probes = fill.plan(fill.observed([key], room, rules.margin), [key], room, rules)
    assert len(probes) == rules.probes
    starts = {s.id: s for s in fill.start_keyframes([key])}
    for probe in probes:
        assert (
            probe.kind == "step" and probe.start in starts and abs(probe.turn) <= max(rules.turns)
        )
        assert probe.unknown >= rules.min_reveal and probe.step > 0
