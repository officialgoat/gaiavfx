"""One fill round through the worldbuilder worker's real tasks (ADR 0020 §4), on the synthetic box room: a still take
seeded at one centre, the round planned and its memory videos written (`fill_explore`), each probe's still placed
(`fill_stills`) and the stills consolidated into the next atlas (`fill_consolidate`). The room stands in for H3 (the
frames along each probe's move, exactly) and for MoGe (the true depth at each still, at another scale). The probes'
depth (`fill_paths`) is a splat render and needs the GPU; nothing here is inference."""

import importlib.util
import itertools
import json
import shutil
from pathlib import Path

import numpy as np
import pytest
from synthetic_room import H, W, render, seen
from worldbuilder_splat import frames as fr
from worldbuilder_splat import memory as mem
from worldbuilder_splat.splat2 import _ffmpeg

WORKERS = Path(__file__).parents[2] / "engine_workers"
GATE = fr.Gate(stride=4, spacing=2)
FFMPEG = shutil.which(_ffmpeg())
RULES = {
    "grid": [16, 9],
    "residual_max": 0.05,
    "contradiction_max": 0.1,
    "anchor_psnr_min": 22.0,
    "trajectory": [24, 48],
    "agree": 0.08,
}

pytestmark = pytest.mark.skipif(FFMPEG is None, reason="the fill round writes videos with ffmpeg")


@pytest.fixture
def worker(monkeypatch):
    monkeypatch.syspath_prepend(str(WORKERS))
    spec = importlib.util.spec_from_file_location(
        "worldbuilder_fill_worker", WORKERS / "worldbuilder.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def run(worker, tmp_path, task, params, inputs=None):
    work = tmp_path / "work" / f"{task}-{next(COUNTER)}"
    work.mkdir(parents=True)
    (work / "job.json").write_text(
        json.dumps(
            {
                "schema": 1,
                "job_id": "j",
                "step": f"worldbuilder.{task}",
                "task": task,
                "params": params,
                "inputs": {k: str(v) for k, v in (inputs or {}).items()},
                "scratch": str(tmp_path / "scratch"),
                "engine": {"programs": {"ffmpeg": FFMPEG}},
            }
        ),
        encoding="utf-8",
    )
    assert worker.run(worker.TASKS, argv=["--job", str(work / "job.json")]) == 0
    result = json.loads((work / "result.json").read_text(encoding="utf-8"))
    outputs = {o["key"]: work / o["path"] for o in result["outputs"]}
    return outputs, json.loads(outputs["summary"].read_text(encoding="utf-8"))


COUNTER = itertools.count()


def seed_still_take(tmp_path):
    """A still camera's take laid out at one centre, as the seed's atlas task leaves it in the operation's scratch."""
    shots = [render([0, 0, 0], 0.0) for _ in range(8)]
    frames = fr.Frames(
        image=np.stack([s[0] for s in shots]),
        depth=np.stack([s[1] for s in shots]),
        confidence=np.ones((8, H, W), np.float32),
        viewmat=np.stack([s[2] for s in shots]),
        k=np.stack([s[3] for s in shots]),
        people=np.zeros((8, H, W), bool),
    )
    record, _ = fr.verdict(frames, GATE)
    seed = tmp_path / "scratch" / "atlas" / "seed"
    atlas = fr.build_atlas(frames, seed, "Box room", GATE, record, fr.layout(record, True))
    (seed / "atlas.json").write_text(json.dumps(atlas), encoding="utf-8")


def test_a_still_take_fills_its_unseen_space_in_one_round(worker, tmp_path):
    from worldbuilder_splat import splat2

    seed_still_take(tmp_path)

    outputs, plan = run(
        worker,
        tmp_path,
        "fill_explore",
        {"name": "r1", "atlas_state": "seed", "rules": RULES, "frames": 49, "workers": 1},
    )
    probes = plan["probes"]
    assert len(probes) == 2 and {p["kind"] for p in probes} == {"turn"}
    assert {f"{key}:{n}" for n in range(2) for key in ("first", "memory", "regen")} <= set(outputs)
    assert plan["coverage"]["headings"] == pytest.approx(70, abs=2)  # the room's lens
    assert {round(p["hfov"]) for p in probes} == {70}  # MoGe runs at the probe's own lens

    # H3's stand-in: the room seen along each probe's move, exactly.
    videos = {}
    for n, probe in enumerate(probes):
        poses = json.loads(
            (tmp_path / "scratch" / "fill" / "r1" / probe["folder"] / "poses.json").read_text(
                "utf-8"
            )
        )
        assert len(poses) == 49
        shots = [seen(p["viewmat"], p["K"], probe["width"], probe["height"])[0] for p in poses]
        videos[f"video:{n}"] = tmp_path / f"probe{n}.mp4"
        splat2.encode_video(videos[f"video:{n}"], shots, FFMPEG)

    # The app keys an asset input by its id and names each probe's in `videos`.
    named = {str(n): f"video:{n}" for n in range(len(probes))}
    outputs, stills = run(
        worker,
        tmp_path,
        "fill_stills",
        {"name": "r1", "atlas_state": "seed", "videos": named},
        videos,
    )
    assert [s["still"] for s in stills["stills"]] == [True, True]
    assert {"still:0", "still:1"} <= set(outputs)

    # MoGe's stand-in: the true depth where each still was really seen from, at another scale.
    geometry = {}
    for n, probe in enumerate(probes):
        view = json.loads(
            (tmp_path / "scratch" / "fill" / "r1" / probe["folder"] / "view.json").read_text(
                "utf-8"
            )
        )
        _, depth = seen(view["viewmat"], view["K"], view["width"], view["height"])
        geometry[f"geometry:{n}"] = tmp_path / f"still{n}.npz"
        np.savez(
            geometry[f"geometry:{n}"],
            depth=0.7 * depth[None],
            mask=np.ones((1, *depth.shape), np.float32),
        )

    outputs, summary = run(
        worker,
        tmp_path,
        "fill_consolidate",
        {
            "name": "r1",
            "atlas_state": "seed",
            "atlas": "r1",
            "rules": RULES,
            "voxel": plan["voxel"],
            "videos": named,
        },
        geometry | videos,
    )
    round_ = summary["round"]
    assert sorted(round_["accepted"]) == ["r1p0", "r1p1"], round_["stills"]
    assert round_["coverage"]["headings"] > plan["coverage"]["headings"] + 20
    assert round_["gain"] > 0.3
    filled = json.loads((tmp_path / "scratch" / "atlas" / "r1" / "atlas.json").read_text("utf-8"))
    current = mem.current(filled)
    assert [r["move"] for r in current if r.get("role") == "generated" and "move" in r] == [
        "turn",
        "turn",
    ]
    trajectory = [r for r in current if r.get("role") == mem.TRAJECTORY]
    assert trajectory and [r.get("heldout", False) for r in trajectory][:2] == [False, True]
    assert {"atlas", "map"} <= set(outputs) and all(f"still:{r['id']}" in outputs for r in current)


def test_a_probe_whose_render_failed_is_a_rejected_probe(worker, tmp_path):
    seed_still_take(tmp_path)
    run(
        worker,
        tmp_path,
        "fill_explore",
        {"name": "r1", "atlas_state": "seed", "rules": RULES, "frames": 17, "workers": 1},
    )
    outputs, stills = run(worker, tmp_path, "fill_stills", {"name": "r1", "atlas_state": "seed"})
    assert stills["stills"] == [
        {"probe": 0, "still": False, "reason": "no render"},
        {"probe": 1, "still": False, "reason": "no render"},
    ]
    assert not any(key.startswith("still:") for key in outputs)
    _, summary = run(
        worker,
        tmp_path,
        "fill_consolidate",
        {"name": "r1", "atlas_state": "seed", "atlas": "r1", "rules": RULES},
    )
    assert summary["round"]["accepted"] == [] and summary["round"]["planned"] == 0
    assert summary["round"]["gain"] == 0.0
