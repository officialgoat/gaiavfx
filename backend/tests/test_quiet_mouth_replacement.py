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
from gaiavfx.modules.direction.replacement import Arm, ReplacementFacts, from_record

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
    from test_mouth_facts import trial
    from test_replacement_contract import POLICY, mug_facts

    series, presence = trial(shot, window)
    request = replacement.ReplacementRequest(
        "source", "analysis", (replacement.PerformerChoice("actor", "target"),)
    )
    people = {
        "actor": {"mouth": {"aperture": series[0].aperture, "face_height": series[0].face_height}}
    }
    tracks = mug_facts(len(presence[0])).tracks
    quiet, silent, actual, _, _ = replacement._mouth_facts(
        request, people, tracks, POLICY, has_audio=True
    )
    assert actual is speaking and silent == intervals
    assert quiet == (shot == "056")
    assert replacement._mouth_facts(request, people, tracks, POLICY, has_audio=False)[2] is False


@pytest.mark.parametrize("explicit", [True, False])
def test_explicit_speaking_wins_but_requires_audio_and_a_nonquiet_window(explicit):
    from dataclasses import replace

    from test_replacement_contract import POLICY, mug_facts

    request = replacement.ReplacementRequest(
        "source", "analysis", (replacement.PerformerChoice("actor", "target"),), speaking=explicit
    )
    people = {"actor": {"mouth": {"aperture": [0.02] * 73, "face_height": [300] * 73}}}
    tracks = mug_facts().tracks
    assert replacement._mouth_facts(request, people, tracks, POLICY, has_audio=True)[2] is explicit
    assert replacement._mouth_facts(request, people, tracks, POLICY, has_audio=False)[2] is False
    assert (
        replacement._mouth_facts(
            replace(request, quiet=True), people, tracks, POLICY, has_audio=True
        )[2]
        is False
    )


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
    from test_replacement_contract import POLICY, mug_facts

    request = replacement.ReplacementRequest(
        "source",
        "analysis",
        (replacement.PerformerChoice("actor", "target"),),
        speaking=True,
        **explicit,
    )
    assert replacement._mouth_facts(
        request,
        {"actor": {"mouth": {"aperture": "malformed"}}},
        mug_facts().tracks,
        POLICY,
        has_audio=True,
    ) == (request.quiet, request.silent_intervals, False, (), False)


@pytest.mark.parametrize("explicit", [None, True, False])
@pytest.mark.parametrize("mode", ["single", "never", "joint", "head"])
def test_reason_lines_distinguish_requests_and_actual_lock(monkeypatch, explicit, mode):
    from dataclasses import replace

    from test_head_plan import planned
    from test_replacement_contract import POLICY, joint_facts, mug_facts, plan

    request = replacement.ReplacementRequest(
        "source", "analysis", (replacement.PerformerChoice("actor", "target"),), speaking=explicit
    )
    people = {"actor": {"mouth": {"aperture": [0.02] * 73, "face_height": [300] * 73}}}
    _, _, speaking, reasons, _ = replacement._mouth_facts(
        request, people, mug_facts().tracks, POLICY, has_audio=True
    )
    facts = replace(
        joint_facts() if mode == "joint" else mug_facts(), quiet=False, speaking=speaking
    )
    policy = replace(POLICY, audio_lock="never") if mode == "never" else POLICY
    first = planned() if mode == "head" else plan(facts, policy=policy)
    monkeypatch.setattr(
        replacement,
        "_facts",
        lambda *args: (
            facts,
            {"contacts": {}, "concepts": ["person"]},
            reasons,
        ),
    )
    monkeypatch.setattr(replacement, "_plan", lambda *args, **kwargs: first)
    actual = replacement._initial(None, request, None)[3].reasons
    expected = (
        "Speaking: 73 open source-lip frames"
        if explicit is None
        else "Speaking (requested)"
        if explicit
        else "Not speaking (requested)"
    )
    expected += "; the render is locked to the window's speech." if first.arm.audio_lock else "."
    assert expected in actual
    if explicit is not None:
        assert not any(r.startswith("Speaking:") for r in actual)
    assert sum("the render is locked" in r for r in actual) == int(first.arm.audio_lock)


@pytest.mark.parametrize(
    "opened,minimum,expected", [(11, 12, False), (12, 12, True), (12, 13, False)]
)
def test_speaking_uses_the_configured_minimum(opened, minimum, expected):
    from dataclasses import replace

    from test_replacement_contract import POLICY, mug_facts

    request = replacement.ReplacementRequest(
        "source", "analysis", (replacement.PerformerChoice("actor", "target"),)
    )
    people = {
        "actor": {
            "mouth": {
                "aperture": [0.02] * opened + [0.001] * (73 - opened),
                "face_height": [300] * 73,
            }
        }
    }
    policy = replace(POLICY, speaking_min_open_frames=minimum)
    assert (
        replacement._mouth_facts(request, people, mug_facts().tracks, policy, has_audio=True)[2]
        is expected
    )
