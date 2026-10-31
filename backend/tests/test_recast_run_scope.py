"""Run scope: the feed's start rule, the estimates and the start gate read one rule over shot states."""

import pytest

from gaiavfx.application import recast_projection as p
from gaiavfx.application import recast_run

RANGE = {"start": 0, "end": 500}


def plan(**changes):
    return {
        "performers": ["c1"],
        "gaps": [],
        "_ready": True,
        "windows": [{"start": 0, "end": 73, "length": 73}, {"start": 51, "end": 124, "length": 73}],
        "_handoff": {"kind": "bridge", "joins": [{"join": 1, "kind": "bridge"}]},
        "renders_per_window": 0,
        **changes,
    }


# (name, status, included, plan, start): covered by the run, rendered next, still analysing
SHOTS = [
    ("planned", "planned", True, plan(), 0, (True, True, False)),
    ("left out", "planned", False, plan(), 100, (False, False, False)),
    ("outside the range", "planned", True, plan(), 600, (False, False, False)),
    ("in review", "needs_review", True, plan(), 200, (True, False, False)),
    ("a gap", "planned", True, plan(gaps=[{"frame": 3}]), 300, (False, False, False)),
    ("nobody to replace", "planned", True, plan(performers=[]), 350, (False, False, False)),
    ("analysing", "analysing", True, plan(_ready=False, windows=[]), 400, (False, False, True)),
    ("unplanned", "unplanned", True, None, 450, (False, False, False)),
]


def shots():
    return [
        {
            "id": name,
            "status": status,
            "included": included,
            "plan": shot_plan,
            "start": start,
            "end": start + 40,
        }
        for name, status, included, shot_plan, start, _ in SHOTS
    ]


@pytest.mark.parametrize("mode", ["draft", "final"])
def test_one_rule_says_which_shots_a_run_covers_renders_next_and_waits_for(mode):
    views = shots()
    state = {"film": {"frame_count": 1000}, "shots": views, "run": {}}
    rendered, estimate = recast_run.estimate_run(state, mode=mode, frame_range=RANGE)
    expected = {name: flags for name, *_, flags in SHOTS}
    assert [s["id"] for s in rendered] == [n for n, f in expected.items() if f[1]]
    assert {s["id"] for s in views if recast_run.covered(s, RANGE)} == {
        n for n, f in expected.items() if f[0]
    }
    # The feed starts only a renderable plan: every shot the estimate counts is one.
    assert all(recast_run.renderable(s["plan"]) for s in rendered)
    assert recast_run.analysing(views, RANGE) is any(f[2] for f in expected.values())
    assert recast_run.analysing([s for s in views if s["status"] != "analysing"], RANGE) is False
    # Each window at the mode's renders, plus the bridge its handoff joins with.
    renders = p.window_renders(mode)
    bridge = 1 if mode == "draft" else renders
    assert estimate["renders"] == 2 * renders + bridge == rendered[0]["renders"]
    assert estimate["basis"] == "seed"


@pytest.mark.parametrize("mode", ["draft", "final"])
def test_the_runs_own_estimate_and_a_new_runs_agree_before_anything_renders(mode):
    """One estimator: the recast's estimate over its range and /estimate count the same renders, GPU time and storage
    while nothing is rendered or analysing, and both take the measured timing once there is one."""
    renders = p.window_renders(mode)
    views = [s for s in shots() if s["status"] in ("planned", "unplanned")]
    for view in views:
        if view["plan"]:
            view["plan"] = view["plan"] | {"renders_per_window": renders}
    recast = {"run_mode": mode, "run_range_start": RANGE["start"], "run_range_end": RANGE["end"]}
    planned = [s for s in views if recast_run.covered(s, RANGE)]
    for seconds in (None, 120.0):
        own = recast_run.run_estimate(recast, planned, {}, {}, seconds)
        state = {"film": {"frame_count": 1000}, "shots": views, "run": {"render_seconds": seconds}}
        _, fresh = recast_run.estimate_run(state, mode=mode, frame_range=RANGE)
        assert {k: own[k] for k in fresh} == fresh
        assert fresh["basis"] == ("measured" if seconds else "seed")


@pytest.mark.parametrize(
    ("change", "plan", "start", "pause"),
    [
        ({}, None, None, "No run is running."),
        (
            {"analysis": "running"},
            "Wait for analysis to finish",
            "Wait for analysis to finish.",
            "No run is running.",
        ),
        (
            {"mapped": False},
            "Map a character first",
            "Confirm a character mapping first.",
            "No run is running.",
        ),
        ({"analysing": True}, None, "Wait for window analyses to finish.", "No run is running."),
        (
            {"mapped": False, "analysing": True},
            "Map a character first",
            "Wait for window analyses to finish.",
            "No run is running.",
        ),
        ({"state": "running"}, "Pause the run before planning", "The run is already active.", None),
        (
            {"fps": "unsupported"},
            None,
            "This film's frame rate is unsupported.",
            "No run is running.",
        ),
    ],
)
def test_run_scope_says_why_plan_start_and_pause_may_not_run(change, plan, start, pause):
    view = {
        "analysis": {"state": change.get("analysis", "succeeded")},
        "characters": [{"target": {"state": "confirmed"} if change.get("mapped", True) else None}],
        "film": {"fps_status": change.get("fps", "supported")},
        "run": {"state": change.get("state", "idle"), "analysing": change.get("analysing", False)},
    }
    assert recast_run.refusals(view) == {
        "plan_refusal": plan,
        "start_refusal": start,
        "pause_refusal": pause,
    }
