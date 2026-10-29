import json
from dataclasses import replace
from pathlib import Path

import pytest

from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.direction.mouth import MouthSeries, quiet_facts
from gaiavfx.modules.direction.replacement import MouthPolicy, parse_replacement_policy

ROOT = Path(__file__).parents[2]
POLICY = parse_replacement_policy(replacement_policy_text()).mouth
EVIDENCE = json.loads((ROOT / POLICY.evidence).read_text())


def trial(shot, window, side="native"):
    """The trial window's performers as (series, presence); every trial frame is present."""
    row = next(w for w in EVIDENCE["windows"] if w["app_shot"] == shot and w["window"] == window)
    series = tuple(
        MouthSeries(tuple(p[side]["aperture"]), tuple(p[side]["face_height_px"]))
        for p in row["performers"]
    )
    return series, tuple(("present",) * row["frames"] for _ in series)


def facts(series, presence, policy=POLICY):
    return quiet_facts(series, presence, policy)


def test_shipped_policy_reads_its_closure_evidence():
    assert EVIDENCE["schema"] == 1
    assert (POLICY.closed_aperture_at_most, POLICY.silent_min_frames) == (0.004, 12)


@pytest.mark.parametrize(
    ("shot", "window", "quiet", "intervals"),
    [
        ("056", 0, True, ()),
        ("059", 0, True, ()),
        ("058", 0, False, ((29, 48),)),
        ("061", 1, False, ((35, 54),)),
        ("058", 1, False, ()),
        ("061", 0, False, ()),
    ],
)
def test_trial_close_ups_give_the_visually_checked_facts(shot, window, quiet, intervals):
    for side in ("native", "evidence"):
        result = facts(*trial(shot, window, side))
        assert (result.quiet, result.silent_intervals) == (quiet, intervals)


def test_small_faces_abstain_instead_of_guessing():
    for shot, window in (("053", 0), ("006", 0), ("006", 5)):
        result = facts(*trial(shot, window))
        assert (result.quiet, result.silent_intervals) == (False, ())
        assert result.reason.startswith("Source lips unmeasured")
        assert result.open_frames == 0
        assert result.measured_frames == 0


@pytest.mark.parametrize("shot,window", [("056", 0), ("058", 0), ("058", 1), ("053", 0)])
def test_open_frames_count_measured_joint_lips(shot, window):
    from gaiavfx.modules.direction.mouth import frame_states

    series, presence = trial(shot, window)
    states = [frame_states(s, p, POLICY) for s, p in zip(series, presence)]
    result = facts(series, presence)
    assert result.open_frames == sum("open" in column for column in zip(*states))
    assert result.measured_frames == sum(
        "open" in column or all(s in {"closed", "absent"} for s in column) and "closed" in column
        for column in zip(*states)
    )
    assert (result.open_frames >= 12) == (shot == "058")


def test_joint_frames_close_only_when_every_present_performer_is_closed():
    # 006 window 0 with the face-size floor lowered: one performer speaks throughout.
    low = replace(POLICY, min_face_height_px=90)
    series, presence = trial("006", 0)
    assert facts((series[1],), presence[:1], low).silent_intervals == ((0, 39),)
    assert facts(series, presence, low).silent_intervals == ()
    closed = MouthSeries((0.001,) * 30, (300,) * 30)
    opened = MouthSeries((0.03,) * 30, (300,) * 30)
    # An absent performer does not hold the other's lips open.
    away = ("absent",) * 15 + ("present",) * 15
    assert facts((closed, opened), (("present",) * 30, away)).silent_intervals == ((0, 15),)


def test_quiet_needs_coverage_and_no_open_frame():
    frames = 40
    gaps = MouthSeries((0.001,) * 30 + (None,) * 10, (300,) * frames)
    assert facts((gaps,), (("present",) * frames,)).quiet is False
    assert facts((gaps,), (("present",) * frames,)).silent_intervals == ((0, 30),)
    one_open = MouthSeries((0.001,) * 39 + (0.02,), (300,) * frames)
    assert facts((one_open,), (("present",) * frames,)).silent_intervals == ((0, 39),)


def test_at_most_eight_longest_runs_in_order():
    pattern = []
    for length in range(12, 22):
        pattern += [0.001] * length + [0.03]
    series = MouthSeries(tuple(pattern), (300,) * len(pattern))
    result = facts((series,), (("present",) * len(pattern),))
    lengths = [b - a for a, b in result.silent_intervals]
    assert sorted(lengths) == list(range(14, 22)) and result.silent_intervals == tuple(
        sorted(result.silent_intervals)
    )


@pytest.mark.parametrize(
    "series",
    [
        lambda: MouthSeries((0.1,), ()),
        lambda: MouthSeries((), ()),
        lambda: MouthSeries((float("nan"),), (100,)),
        lambda: MouthSeries((True,), (100,)),
        lambda: MouthSeries((-0.1,), (100,)),
    ],
)
def test_series_are_validated(series):
    with pytest.raises(ValueError):
        series()


@pytest.mark.parametrize(
    "change",
    [
        {"closed_aperture_at_most": 0},
        {"closed_aperture_at_most": float("inf")},
        {"min_face_height_px": -1},
        {"quiet_min_coverage": 0},
        {"quiet_min_coverage": 1.5},
        {"silent_min_frames": 0},
        {"silent_min_frames": 1.5},
        {"evidence": "../outside.json"},
        {"evidence": ".cache/trial.json"},
        {"note": ""},
    ],
)
def test_mouth_policy_is_validated(change):
    with pytest.raises(ValueError):
        replace(POLICY, **change)


def test_mouth_table_keys_are_closed_and_optional():
    text = replacement_policy_text()
    with pytest.raises(ValueError, match="mouth keys"):
        parse_replacement_policy(
            text.replace("silent_min_frames = 12", "silent_min_frames = 12\nextra = 1")
        )
    with pytest.raises(ValueError, match="mouth keys"):
        parse_replacement_policy(text.replace("silent_min_frames = 12\n", ""))
    start, end = text.index("[mouth]"), text.index("[first_arm]")
    assert parse_replacement_policy(text[:start] + text[end:]).mouth is None
    assert isinstance(POLICY, MouthPolicy)


def test_unresolved_frames_count_open_lips_but_never_closed_ones():
    closed_then_open = MouthSeries((0.001,) * 40 + (0.03,) * 20, (300,) * 60)
    uncertain = ("present",) * 40 + ("unresolved",) * 20
    result = facts((closed_then_open,), (uncertain,))
    assert result.quiet is False and result.silent_intervals == ((0, 40),)
    shut = MouthSeries((0.001,) * 60, (300,) * 60)
    result = facts((shut,), (("unresolved",) * 30 + ("present",) * 30,))
    assert result.quiet is False and result.silent_intervals == ((30, 60),)
    # Joint: an uncertain but measurably open partner keeps the shared lips open.
    opened = MouthSeries((0.03,) * 60, (300,) * 60)
    joint = facts((shut, opened), (("present",) * 60, ("unresolved",) * 60))
    assert (joint.quiet, joint.silent_intervals) == (False, ())
    with pytest.raises(ValueError, match="presence"):
        facts((shut,), (("maybe",) * 60,))


def test_quiet_refuses_an_unmeasured_stretch_as_long_as_a_silent_interval():
    turned = MouthSeries((0.001,) * 59 + (None,) * 14, (300,) * 73)
    result = facts((turned,), (("present",) * 73,))
    assert result.quiet is False and result.silent_intervals == ((0, 59),)
    short = MouthSeries((0.001,) * 62 + (None,) * 11, (300,) * 73)
    assert facts((short,), (("present",) * 73,)).quiet is True
