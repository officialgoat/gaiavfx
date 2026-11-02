"""Quiet-mouth facts derived from the source lips, end to end through the fake runtime."""

import json

import pytest
from test_replacement_checks import _rgb_policy, report
from test_replacement_runner import analysed, drain, pcm, read
from test_replacement_runner import work as work  # noqa: PLC0414 - pytest fixture export

import gaiavfx.application.replace_character as replacement
from gaiavfx.application import recast_planning
from gaiavfx.application.replacement_steps import _pipe, decode_rgb
from gaiavfx.integrations.minimax_h3.replacement import compile_replacement
from gaiavfx.modules.recast.replacement import Arm, ReplacementFacts, from_record

FACE = (100, 110, 136, 250)  # overlaps the fake performer 0 mask; 140 px tall
QUIET = "keeps lips gently closed and jaw at rest throughout this quiet shot"


def preview(work, request):
    db, library = work[0], work[1]
    with db.transaction() as session:
        plan = replacement.replacement_plan(session, request, library=library)
    facts = from_record(ReplacementFacts, plan["facts"])
    return plan, compile_replacement(facts, from_record(Arm, plan["arm"])).text


def test_closed_source_lips_make_a_quiet_window(work):
    work[2].comfy.window_face = (FACE, 0.0)
    _, _, request = analysed(work)
    plan, prompt = preview(work, request)
    assert plan["facts"]["quiet"] is True and plan["facts"]["silent_intervals"] == []
    assert "Quiet mouth from the source lips: closed in 51/51 frames." in plan["reasons"]
    assert QUIET in prompt
    assert plan["facts"]["speaking"] is False and plan["arm"]["audio_lock"] is False


def test_closed_runs_become_silent_intervals_on_the_24fps_grid(work):
    work[2].comfy.window_face = (FACE, [0.0] * 24 + [7.0] * 27)
    _, _, request = analysed(work)
    plan, prompt = preview(work, request)
    assert plan["facts"]["quiet"] is False
    assert plan["facts"]["silent_intervals"] == [[0, 24]]
    assert "Closed source lips at frames 0-23." in plan["reasons"]
    assert "From 0 to 1 seconds the performer's lips stay gently closed" in prompt
    assert plan["facts"]["speaking"] is True and plan["arm"]["audio_lock"] is True


def test_open_and_unmeasured_lips_keep_the_mouth_following_the_source(work):
    work[2].comfy.window_face = (FACE, 7.0)
    _, _, request = analysed(work)
    plan, prompt = preview(work, request)
    assert (plan["facts"]["quiet"], plan["facts"]["silent_intervals"]) == (False, [])
    assert "Source lips move; the mouth follows the source." in plan["reasons"]
    assert "lips gently closed" not in prompt
    assert plan["facts"]["speaking"] is True and plan["arm"]["audio_lock"] is True
    assert (
        "Speaking: 51 open source-lip frames; the render is locked to the window's speech."
        in plan["reasons"]
    )


def test_explicit_request_facts_override_the_derivation(work):
    work[2].comfy.window_face = (FACE, 0.0)
    _, _, request = analysed(work)
    request["silent_intervals"] = [[30, 45]]
    plan, _ = preview(work, request)
    assert (plan["facts"]["quiet"], plan["facts"]["silent_intervals"]) == (False, [[30, 45]])
    assert not any("source lips" in r for r in plan["reasons"])


def test_analysis_without_mouth_series_derives_nothing_and_says_so(work):
    work[2].comfy.window_face = None
    _, _, request = analysed(work)
    plan, _ = preview(work, request)
    assert (plan["facts"]["quiet"], plan["facts"]["silent_intervals"]) == (False, [])
    assert plan["facts"]["speaking"] is False and plan["arm"]["audio_lock"] is False
    assert any(
        r.startswith("The window face scan returned no lip landmarks") for r in plan["reasons"]
    )


def test_every_round_compiles_the_frozen_quiet_wording(work, monkeypatch):
    db, library, runner, _, _ = work
    runner.comfy.window_face = (FACE, 0.0)
    _, _, request = analysed(work)
    _rgb_policy(monkeypatch)
    runner.comfy.face_reports = {"performer_0": report(0.55)}
    with db.transaction() as session:
        op = replacement.start_replacement(session, request, 4, library=library)
    assert op["request"]["facts"]["quiet"] is True
    drain(runner)
    finished = read(db, op["id"])
    renders = [j for j in finished["jobs"] if j["kind"] == "video" and j["executor"] == "comfy"]
    assert len(renders) >= 3  # the failing first round escalated to a second
    assert all(QUIET in json.dumps(j["graph"]) for j in renders)


def test_planner_version_replans_stored_recast_plans():
    assert recast_planning.PLANNER_VERSION == 10


def speaking_window(facts=None, aperture=0.02, face_height=300, *, has_audio=True, **changes):
    """The facts the Replacement plan reads from the actor's lips in a window of these facts."""
    from replacement_plan_support import first_round, lips, mug_facts

    facts = facts or mug_facts()
    frames = facts.shot_end - facts.shot_start
    mouth = {"actor": lips(aperture, face_height, frames)}
    return first_round(facts, reading_args={"has_audio": has_audio, "mouth": mouth}, **changes)


@pytest.mark.parametrize(
    "shot,window,speaking,intervals",
    [
        ("056", 0, False, ()),
        ("058", 1, True, ()),
        ("053", 0, False, ()),
        ("058", 0, True, ((29, 48),)),
    ],
)
def test_trial_speaking_facts_and_audio_requirement(shot, window, speaking, intervals):
    from replacement_plan_support import mug_facts, trial

    series, presence = trial(shot, window)
    facts = mug_facts(len(presence[0]))
    lips = (series[0].aperture, series[0].face_height)
    read = speaking_window(facts, *lips).facts
    assert read.speaking is speaking and read.silent_intervals == intervals
    assert read.quiet == (shot == "056")
    assert speaking_window(facts, *lips, has_audio=False).facts.speaking is False


@pytest.mark.parametrize("explicit", [True, False])
def test_explicit_speaking_wins_but_requires_audio_and_a_nonquiet_window(explicit):
    assert speaking_window(speaking=explicit).facts.speaking is explicit
    assert speaking_window(speaking=explicit, has_audio=False).facts.speaking is False
    assert speaking_window(speaking=explicit, quiet=True).facts.speaking is False


def test_speaking_request_freezes_the_lock_in_render_jobs(work):
    db, library, runner, assets, _ = work
    runner.comfy.window_face = (FACE, 7.0)
    _, _, request = analysed(work)
    with work[0].transaction() as session:
        op = replacement.start_replacement(session, request, 4, library=work[1])
    assert op["request"]["has_audio"] and op["request"]["facts"]["speaking"]
    renders = [j for j in op["jobs"] if j["kind"] == "video" and j["executor"] == "comfy"]
    assert len(renders) == 2
    assert all(j["graph"]["audio_lock"]["inputs"]["audio"] == ["components", 1] for j in renders)
    drain(runner)
    finished = read(db, op["id"])
    assert finished["state"] == "succeeded", finished
    prepare = next(j for j in finished["jobs"] if j["step"] == "replacement_prepare")
    spec = prepare["snapshot"]["step_spec"]
    assert spec["arm"]["audio_lock"] is True
    with db.transaction() as session:
        guide = library.path(session, prepare["outputs"][0]["asset_id"])
        source = library.path(session, assets[0])
    audio = _pipe(
        library.media,
        ["-i", str(guide), "-vn", "-f", "f32le", "-acodec", "pcm_f32le", "pipe:1"],
    )
    assert len(audio) / 4 / 48000 == pytest.approx(spec["window_frames"] / 24, abs=1 / 48000)
    facts = spec["facts"]
    delivered = facts["shot_end"] - facts["shot_start"]
    expected_audio = pcm(library.media, source, facts["shot_start"], facts["shot_end"])
    assert len(finished["candidates"]) == 2
    for candidate in finished["candidates"]:
        with db.transaction() as session:
            composite = library.path(session, candidate["asset_id"])
        assert len(decode_rgb(library.media, composite)) == delivered
        assert pcm(library.media, composite) == expected_audio


@pytest.mark.parametrize("explicit", [{"quiet": True}, {"silent_intervals": ((2, 20),)}])
def test_explicit_quiet_facts_skip_malformed_mouth_series(explicit):
    from replacement_plan_support import first_round

    planned = first_round(
        reading_args={"has_audio": True, "mouth": {"actor": {"aperture": "malformed"}}},
        speaking=True,
        **explicit,
    )
    facts = planned.facts
    assert facts.quiet == explicit.get("quiet", False)
    assert facts.silent_intervals == explicit.get("silent_intervals", ())
    assert not facts.speaking and not facts.lips_unmeasured
    assert not any("peaking" in r or "mouth" in r for r in planned.plan.reasons)


@pytest.mark.parametrize("explicit", [None, True, False])
@pytest.mark.parametrize("mode", ["single", "never", "joint", "head"])
def test_reason_lines_distinguish_requests_and_actual_lock(explicit, mode):
    from replacement_plan_support import (
        HEAD_BOXES,
        TEXT,
        edited,
        first_round,
        head_facts,
        joint_facts,
        lips,
    )

    from gaiavfx.modules.recast.replacement_plan import HeadPassChoice

    mouth = lips(0.02, 300)
    if mode == "head":
        boxes = {"box": [list(b) for b in HEAD_BOXES]}
        first = first_round(
            head_facts(),
            renders=1,
            reading_args={"has_audio": True, "mouth": {"one": {**mouth, **boxes}}},
            head=HeadPassChoice("parent"),
            speaking=explicit,
        ).plan
    else:
        facts = joint_facts() if mode == "joint" else None
        text = (
            edited(TEXT, 'audio_lock = "speaking"', 'audio_lock = "never"')
            if mode == "never"
            else TEXT
        )
        mouths = {"actor": mouth, "woman": mouth} if mode == "joint" else {"actor": mouth}
        first = first_round(
            facts,
            text=text,
            reading_args={"has_audio": True, "mouth": mouths},
            speaking=explicit,
        ).plan
    expected = (
        "Speaking: 73 open source-lip frames"
        if explicit is None
        else "Speaking (requested)"
        if explicit
        else "Not speaking (requested)"
    )
    expected += "; the render is locked to the window's speech." if first.arm.audio_lock else "."
    assert expected in first.reasons
    if explicit is not None:
        assert not any(r.startswith("Speaking:") for r in first.reasons)
    assert sum("the render is locked" in r for r in first.reasons) == int(first.arm.audio_lock)


@pytest.mark.parametrize(
    "opened,minimum,expected", [(11, 12, False), (12, 12, True), (12, 13, False)]
)
def test_speaking_uses_the_configured_minimum(opened, minimum, expected):
    from replacement_plan_support import TEXT, edited

    text = edited(TEXT, "speaking_min_open_frames = 12", f"speaking_min_open_frames = {minimum}")
    aperture = [0.02] * opened + [0.001] * (73 - opened)
    assert speaking_window(None, aperture, text=text).facts.speaking is expected
