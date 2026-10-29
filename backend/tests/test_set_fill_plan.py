"""Filling a take's unseen space (ADR 0020 §4): where the probes stand and look, on the synthetic box room whose
geometry is exact. The spike on real take geometry is calibration/worldbuilder-2026-10/fill-spike.md."""

import json
import math

import numpy as np
from synthetic_room import H, W, render, truck
from worldbuilder_splat import explore as ex
from worldbuilder_splat import fill
from worldbuilder_splat import frames as fr
from worldbuilder_splat import memory as mem

GATE = fr.Gate(stride=4, spacing=2)
RULES = fill.Rules(grid=(16, 9))


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


def seeded(tmp_path, shots, people=None, fill_on=True):
    frames = frames_of(shots, people)
    record, _ = fr.verdict(frames, GATE)
    layout = fr.layout(record, fill_on)
    atlas = fr.build_atlas(frames, tmp_path, "Box room", GATE, record, layout)
    (tmp_path / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")
    keys = mem.load_keyframes(tmp_path / "atlas.json")
    room = fr.frames_room(keys)
    return keys, room, fill.observed(keys, room, RULES.margin)


def test_a_turn_keeps_the_cameras_pitch_and_its_centre():
    _, _, view, _ = render([0.2, 0.1, -0.3], 10.0, pitch=-12.0)
    right = fill.turned(view, 30.0)
    assert math.isclose(fill.heading(right), 40.0, abs_tol=1e-6)
    pitch = lambda v: math.degrees(math.asin(v[2, 1]))
    assert math.isclose(pitch(right), pitch(view), abs_tol=1e-6)
    centre = lambda v: -v[:3, :3].T @ v[:3, 3]
    np.testing.assert_allclose(centre(right), centre(view), atol=1e-9)
    path = fill.move(view, 30.0, [0.5, 0.1, -0.3], 73)
    np.testing.assert_allclose(path[0], view, atol=1e-9)
    np.testing.assert_allclose(path[-1], fill.turned(view, 30.0, [0.5, 0.1, -0.3]), atol=1e-9)


def test_what_a_take_never_saw_is_unknown_not_open(tmp_path):
    keys, _, space = seeded(tmp_path, [render([0, 0, 0], 0.0) for _ in range(8)])
    (key,) = keys
    # The research map's grid stops near what was seen, where a ray counts as open sky; this one reaches beyond.
    research = ex.observed_space(
        keys, stride=1
    )  # carved as densely as `fill.observed` carves the 128 px room
    behind = fill.turned(key.viewmat, 180.0)
    assert fill.fractions(research, behind, key.k, W, H)[2] > 0.5
    assert fill.fractions(space, behind, key.k, W, H)[1] > 0.95
    # Its own view is mostly known, from its own centre (rays cast between the carved ones meet the voxel short of
    # each surface that no ray carved, so the small room never reads all of it).
    surface, unknown, _ = fill.fractions(space, key.viewmat, key.k, W, H)
    assert surface > 0.5 and unknown < 0.5


def test_a_still_take_turns_both_ways_before_it_steps(tmp_path):
    keys, room, space = seeded(tmp_path, [render([0, 0, 0], 0.0) for _ in range(8)])
    probes = fill.plan(space, keys, room, RULES)
    assert {p.kind for p in probes} == {"turn"} and len(probes) == 2
    assert sorted(np.sign(p.turn) for p in probes) == [-1, 1]
    for probe in probes:
        assert probe.known >= RULES.min_known and probe.unknown >= RULES.min_reveal
        assert probe.protected >= RULES.min_protected
        assert abs(probe.turn) <= max(RULES.turns)


def test_a_hold_reveals_what_a_person_hid(tmp_path):
    people = np.zeros((8, H, W), bool)
    people[:, 10:70, 40:90] = True  # someone in the middle of every frame
    keys, room, space = seeded(tmp_path, [render([0, 0, 0], 0.0) for _ in range(8)], people)
    probes = fill.plan(space, keys, room, RULES)
    hold = [p for p in probes if p.kind == "hold"]
    assert len(hold) == 1 and hold[0].turn == 0.0
    assert hold[0].unknown > 0.2  # the person's share of the view, never carved


def test_neighbouring_cameras_of_a_moving_take_do_not_repeat_one_turn(tmp_path):
    keys, room, space = seeded(tmp_path, truck(8))
    probes = fill.plan(space, keys, room, RULES)
    assert probes
    for a in probes:
        for b in probes:
            if a is b:
                continue
            apart = np.linalg.norm(np.subtract(a.position, b.position))
            turn = abs((a.heading - b.heading + 180) % 360 - 180)
            assert apart >= RULES.near * room or turn >= fr.hfov(keys[0].k, W) / 4


def test_a_round_gains_by_floor_or_by_the_horizon_whichever_grew_more():
    before = {"area": 1.0, "headings": 60.0}
    assert math.isclose(fill.gain(before, {"area": 1.02, "headings": 120.0}), 1.0)
    assert math.isclose(fill.gain(before, {"area": 1.5, "headings": 60.0}), 0.5)


def test_the_horizon_seen_is_the_union_of_the_keyframes_fields_of_view(tmp_path):
    keys, _, _ = seeded(tmp_path, [render([0, 0, 0], yaw) for yaw in np.linspace(-30, 30, 9)])
    seen = fill.seen_headings(keys)
    assert 60 + 70 - 25 <= seen <= 60 + 70 + 2  # a 60 degree pan with a 70 degree lens


def test_carving_keeps_its_density_whatever_the_takes_size(tmp_path):
    keys, room, space = seeded(tmp_path, [render([0, 0, 0], 0.0) for _ in range(8)])
    # The room's 128 px frames carve every pixel, as a 768 px take carves every fourth.
    assert (space.state == ex.FREE).sum() == (
        fill.observed(keys, room, RULES.margin, stride=1).state == ex.FREE
    ).sum()
