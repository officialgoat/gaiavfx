"""ADR 0020: independent stop decisions and the render allowance, without running engines."""

from copy import deepcopy

import pytest

from gaiavfx.application import set_fill, set_seed
from gaiavfx.integrations.minimax_h3 import probe as h3_probe


def job(name, rnd=None, **fields):
    return {
        "snapshot": {"stage": {"name": name, "round": rnd}},
        "state": "succeeded",
        "outputs": [],
        **fields,
    }


@pytest.fixture
def chain(monkeypatch):
    """Recorded stage summaries stand in for Library reads; the real advancer builds the next jobs."""
    monkeypatch.setattr(set_fill, "_summary", lambda session, library, row: row["summary"])
    return {
        "id": "bounded-fill",
        "request": {"name": "Room", **set_seed._frozen(True)},
        "jobs": [
            job("atlas", summary={"map_meta": {"voxel": 0.1, "room": 2}}),
            job("splat"),
        ],
    }


@pytest.mark.parametrize(
    "gain,closure,reason,words",
    [
        (0.099, 0.5, "gain", "the stage grew too little to go on"),
        (0.2, 0.9, "closure", "the place is closed"),
    ],
)
def test_each_stop_rule_keeps_its_checkpoint_and_explains_why(chain, gain, closure, reason, words):
    chain["jobs"] += [
        job("explore", 1, summary={"probes": []}),
        job(
            "consolidate",
            1,
            summary={
                "round": {
                    "accepted": ["p0"],
                    "gain": gain,
                    "coverage": {"closure": closure},
                }
            },
        ),
        job("extend", 1),
    ]
    result = set_fill.advance(None, None, chain)
    assert isinstance(result, set_fill.Done)
    assert (result.reason, result.rounds) == (reason, 1)
    assert result.words == f"stopped filling after round 1: {words}"


def test_gain_at_the_floor_and_closure_below_the_ceiling_continue(chain):
    chain["jobs"] += [
        job("explore", 1, summary={"probes": []}),
        job(
            "consolidate",
            1,
            summary={
                "round": {
                    "accepted": ["p0"],
                    "gain": 0.1,
                    "coverage": {"closure": 0.899},
                }
            },
        ),
        job("extend", 1),
    ]
    result = set_fill.advance(None, None, chain)
    assert isinstance(result, set_fill.Next)
    assert result.jobs[0].snapshot["stage"] == {"name": "explore", "round": 2}


def test_no_probe_stops_before_depth_or_video_work(chain):
    chain["jobs"].append(job("explore", 1, summary={"probes": []}))
    result = set_fill.advance(None, None, chain)
    assert isinstance(result, set_fill.Done)
    assert (result.reason, result.rounds) == ("no_probe", 0)
    assert result.words == (
        "stopped filling in round 1: no probe was left that could see something new "
        "and keep enough of the stage in view"
    )


def artifact(key):
    return {"output_key": key, "artifact_id": key, "sha256": "a" * 64}


def planned_round(chain, spent):
    # Failed renders spend their reservation too. A three-probe plan can meet a smaller remaining allowance.
    chain["jobs"] += [job("probe", 1, state="failed") for _ in range(spent)]
    plan = {"probes": [{"kind": "turn", "turn": 15, "width": 64, "height": 64}] * 3}
    chain["jobs"].append(
        job(
            "explore",
            2,
            summary=plan,
            outputs=[
                artifact(f"{key}:{n}") for n in range(3) for key in ("first", "memory", "regen")
            ],
        )
    )
    return chain


@pytest.mark.parametrize("spent", [4, 5, 6])
def test_probe_submission_never_exceeds_the_six_reserved_renders(chain, spent):
    planned_round(chain, spent)
    if spent == 6:
        result = set_fill.advance(None, None, chain)
        assert isinstance(result, set_fill.Done)
        assert result.reason == "renders"
        assert result.words == "filled until its 6 renders were spent"
    else:
        chain["jobs"].append(job("paths", 2, outputs=[artifact(f"depth:{n}") for n in range(3)]))
        result = set_fill.advance(None, None, chain)
        assert isinstance(result, set_fill.Next)
        assert len(result.jobs) == 6 - spent
        assert all(j.kind == "video" and j.snapshot["intermediate"] for j in result.jobs)


def test_planning_is_limited_to_the_remaining_render_allowance(chain):
    chain["jobs"] += [job("probe", 1, state="failed") for _ in range(5)]
    frozen = deepcopy(chain["request"])
    result = set_fill.explore(None, None, chain, 2)
    assert result.jobs[0].snapshot["task_spec"]["rules"]["probes"] == 1
    assert chain["request"] == frozen


@pytest.mark.parametrize("stage", ["explore", "paths", "stills", "consolidate", "extend"])
def test_each_non_probe_failure_ends_at_the_previous_completed_round(chain, stage):
    chain["jobs"] += [job("consolidate", 1), job("extend", 1), job(stage, 2, state="failed")]
    result = set_fill.advance(None, None, chain)
    assert isinstance(result, set_fill.Done)
    assert (result.reason, result.rounds) == ("failed", 1)
    assert result.words == (
        f"stopped filling in round 2: its {set_fill.STAGE_WORDS[stage]} failed, "
        "so it holds what the rounds before found"
    )


def changed_probe_knowledge(monkeypatch):
    knowledge = deepcopy(h3_probe.probe_knowledge())
    knowledge.sampling["steps"] += 1
    knowledge.wording["opening"] = "Changed after this build was authorized."
    monkeypatch.setattr(h3_probe, "probe_knowledge", lambda: knowledge)


def test_probe_compile_changes_invalidate_the_build_fingerprint(monkeypatch):
    before = set_seed._fingerprint(set_seed._frozen(True))
    changed_probe_knowledge(monkeypatch)
    assert set_seed._fingerprint(set_seed._frozen(True)) != before


@pytest.mark.parametrize("node,field", [("sigmas", "steps"), ("conditioning", "prompt")])
def test_inflight_build_keeps_its_original_probe_compile(chain, monkeypatch, node, field):
    planned_round(chain, 5)
    chain["jobs"].append(job("paths", 2, outputs=[artifact(f"depth:{n}") for n in range(3)]))
    original = set_fill.advance(None, None, chain).jobs[0].graph
    changed_probe_knowledge(monkeypatch)
    later = set_fill.advance(None, None, chain).jobs[0].graph
    assert later[node]["inputs"][field] == original[node]["inputs"][field]
