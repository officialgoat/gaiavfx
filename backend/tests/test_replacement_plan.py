"""The Replacement plan through its interface: what a request may ask, what a window's reading makes of it, its
first round with the reasons a person reads, and the pins a shot's later windows keep."""

import re
from dataclasses import replace
from pathlib import Path

import pytest
from replacement_plan_support import (
    CAP,
    HEAD_BOXES,
    TEXT,
    attempt,
    edited,
    first_round,
    head_facts,
    joint_facts,
    lips,
    mug_facts,
    reading,
    request,
    result,
    round_plan,
    trial,
)

from gaiavfx.modules.recast.mouth import quiet_facts
from gaiavfx.modules.recast.replacement import (
    Box,
    Performer,
    ReplacementFacts,
    Track,
    parse_replacement_policy,
    plan_bridge,
    plan_replacement,
    to_record,
)
from gaiavfx.modules.recast.replacement_plan import (
    BridgeReading,
    HeadPassChoice,
    PerformerChoice,
    Refused,
    ShotPins,
    effective_policy,
    parse_request,
    plan_window,
    shot_crop,
)

OLD_TEXT = (Path(__file__).parent / "fixtures/replacement/policy-before-batch5.toml").read_text()
POLICY = parse_replacement_policy(TEXT)


def without_table(text, name):
    return re.sub(rf"(?m)^\[{name}\]\n[^\[]*", "", text)


def plan(facts, text=TEXT, prior=(), support=CAP.replacement):
    return plan_replacement(facts, support, parse_replacement_policy(text), CAP.frame_grid, prior)


# --- what a request may ask ---------------------------------------------------------------------------------------

BASE = {
    "source_asset": "source",
    "analysis_operation": "analysis",
    "performers": [{"track_id": "actor", "target_asset": "target"}],
}
CROP = to_record(plan(mug_facts()).arm.crop)


@pytest.mark.parametrize(
    "changes,refusal",
    [
        (
            {"colour": "warm"},
            "Invalid replacement request: ReplacementRequest.__init__() got an unexpected keyword argument 'colour'",
        ),
        (
            {
                "handoff": {"predecessor_operation": "w0", "candidate_id": "c0"},
                "crop_override": CROP,
                "head": {"parent_asset": "p"},
            },
            "A latent handoff needs a predecessor and candidate, without a head pass.",
        ),
        (
            {"bridge": {"join": 1, "predecessor": ["w0", "c0"], "successor": ["w1", "c1"]}},
            "A bridge needs a crop and two frozen neighbour candidates.",
        ),
        ({"save_latent": 1, "crop_override": CROP}, "save_latent must be boolean."),
        ({"save_latent": True}, "Latent windows need a pinned crop and cannot be head passes."),
        (
            {"head": {"parent_asset": "p"}, "treatment_override": "inverted"},
            "A head pass needs one performer and an RGB parent asset.",
        ),
        ({"treatment_override": "sepia"}, "Invalid treatment override."),
        ({"faceswap_override": 1}, "FaceSwap override must be boolean."),
        (
            {
                "close_up": True,
                "performers": [*BASE["performers"], {"track_id": "woman", "target_asset": "t"}],
            },
            "Close-up applies to a single performer",
        ),
        ({"close_up": 1}, "Close-up must be boolean."),
        ({"speaking": "yes"}, "Speaking must be boolean."),
        ({"seed": -1}, "Replacement seed must leave room for four signed 64-bit library seeds."),
        (
            {"performers": [{"track_id": str(i), "target_asset": "t"} for i in range(5)]},
            "performers is limited to 4 entries.",
        ),
    ],
)
def test_a_request_is_refused_in_the_persons_words(changes, refusal):
    with pytest.raises(Refused) as refused:
        parse_request({**BASE, **changes})
    assert str(refused.value) == refusal
    # Not a ValueError: the shared crop's fallback catches those, and must never swallow a refusal.
    assert not isinstance(refused.value, ValueError)


def test_an_invalid_shared_crop_is_refused():
    with pytest.raises(Refused, match="^Invalid shared crop.$"):
        parse_request(replace(request(), crop_override=CROP))


@pytest.mark.parametrize(
    "ask,refusal",
    [
        (
            lambda: first_round(source_asset="elsewhere"),
            "Analysis belongs to a different source asset.",
        ),
        (
            lambda: first_round(performers=(PerformerChoice("ghost", "target"),)),
            "Select distinct people from this shot's analysis.",
        ),
        (
            lambda: first_round(reading_args={"target_kind": "video"}),
            "Replacement targets must be Library images.",
        ),
        (lambda: first_round(renders=9), "renders must be an integer from 1 to 4."),
        (lambda: first_round(head=HeadPassChoice("parent")), "A head pass requires renders = 1."),
    ],
)
def test_a_window_is_refused_in_the_persons_words(ask, refusal):
    with pytest.raises(Refused) as refused:
        ask()
    assert str(refused.value) == refusal


def test_a_bridge_is_refused_when_it_does_not_follow_its_successor():
    facts = replace(mug_facts(), handoff="bridge")
    arm = to_record(plan(mug_facts()).arm)
    windows = ({"arm": arm}, {"arm": arm})
    bridge = BridgeReading(facts, windows, {"analysis_operation": "other", "tracks": []})
    with pytest.raises(Refused, match="^Bridge analysis must be its successor's.$"):
        plan_window(request(), bridge, TEXT, CAP.replacement, CAP.frame_grid, renders=1)
    follows = BridgeReading(facts, windows, {"analysis_operation": "analysis", "tracks": []})
    with pytest.raises(
        Refused, match="^A bridge requires one render, its successor arm and a crop.$"
    ):
        plan_window(request(), follows, TEXT, CAP.replacement, CAP.frame_grid, renders=1)


def test_the_effective_policy_cuts_rounds_to_the_renders_asked_and_drafts_one_retry():
    policy = effective_policy(TEXT, 3)
    assert (policy.renders, policy.renders_per_round, policy.max_rounds) == (3, (2, 1), 2)
    draft = effective_policy(TEXT, draft=True)
    assert draft.renders == 2 and {row.on for row in draft.escalation} <= set(draft.draft.retry_on)
    assert effective_policy(OLD_TEXT, draft=True).renders == 1


def test_a_shot_crop_is_a_value_error_when_none_fits_and_a_refusal_when_a_window_is():
    windows = [
        (request(), reading()),
        (request(), reading(replace(mug_facts(), shot_start=51, shot_end=124))),
    ]
    assert shot_crop(windows, TEXT, CAP.replacement).canvas
    avoided = (mug_facts().tracks[0].box,)  # avoid where the performer stands
    with pytest.raises(ValueError):
        shot_crop([(request(avoided_regions=avoided), reading())], TEXT, CAP.replacement)
    with pytest.raises(Refused):
        shot_crop([(request(source_asset="elsewhere"), reading())], TEXT, CAP.replacement)


# --- what a window's reading makes of it: close-ups ---------------------------------------------------------------

# Close-up facts are read without the mouth table, so a malformed face height cannot fail the lip reading first.
CLOSE_UP = without_table(TEXT, "mouth")


def box(native, index):
    """A performer standing in the middle of the canvas; a second one beside the first."""
    w, h = native
    return (0.4 * w + 40 * index, 0.1 * h, 0.6 * w + 40 * index, 0.9 * h)


def close_up(heights, presence=None, *, native=(1920, 1000), text=CLOSE_UP, joint=False, **changes):
    """Whether the Replacement plan calls a window a close-up from its performer's measured face heights (None for
    no mouth series), and the close-up lines among its reasons."""
    presence = presence or ["present"] * (len(heights) if heights is not None else 73)
    names = ("actor", "two") if joint else ("actor",)
    facts = ReplacementFacts(
        "source",
        0,
        len(presence),
        native,
        24,
        None,
        tuple(
            Performer(n, "target", None, None, "performer", "at the centre", None) for n in names
        ),
        (),
        (),
        (),
        False,
        (),
        tuple(
            Track(n, Box(*box(native, i)), tuple(presence), None, "user")
            for i, n in enumerate(names)
        ),
    )
    mouth = (
        {"actor": {"face_height": list(heights), "aperture": [0.0] * len(heights)}}
        if heights is not None
        else None
    )
    planned = first_round(facts, text=text, reading_args={"mouth": mouth}, **changes)
    reasons = tuple(
        r
        for r in planned.plan.reasons
        if r == "Close-up unmeasured" or r.startswith("Close-up: median") or "(requested)" in r
    )
    return planned.facts.close_up, reasons


@pytest.mark.parametrize(
    "heights,expected",
    [
        ([None, 699, 699, 999], False),
        ([650, None, 700, 750], True),
        ([None, None], False),
        ([0, -1, float("nan")], False),
    ],
)
def test_median_measured_native_face_height(heights, expected):
    actual, reasons = close_up(heights)
    assert actual is expected
    assert bool(reasons) is expected or reasons == ("Close-up unmeasured",)


def test_close_up_derivation_is_optional_single_and_overridable():
    tall = [740] * 73
    assert close_up(tall) == (True, ("Close-up: median face 74% of the frame height.",))
    assert not close_up(None)[0]
    assert not close_up(tall, text=without_table(CLOSE_UP, "faceswap"))[0]
    assert close_up(tall, joint=True) == (False, ())
    for explicit in (True, False):
        assert close_up(None, close_up=explicit)[0] is explicit
    assert not close_up(tall, close_up=False)[0]
    threshold = edited(
        CLOSE_UP, "close_up_min_face_fraction = 0.7", "close_up_min_face_fraction = 0.8"
    )
    assert not close_up(tall, text=threshold)[0]


def test_close_up_derivation_matches_native_trial_series():
    from replacement_plan_support import EVIDENCE, trial

    assert POLICY.faceswap.close_up_min_face_fraction == 0.7
    seen = set()
    for window in EVIDENCE["windows"]:
        shot = window["app_shot"]
        if shot not in {"058", "061", "056", "059", "006"}:
            continue
        series, presence = trial(shot, window["window"], side="native")
        measured, _ = close_up(series[0].face_height, list(presence[0]), native=(1920, 800))
        assert measured is (shot in {"058", "061"}), (shot, window["window"])
        seen.add(shot)
    assert seen == {"058", "061", "056", "059", "006"}


@pytest.mark.parametrize(
    "measured,present,expected", [(1, 8, False), (2, 8, True), (2, 9, False), (0, 0, False)]
)
def test_close_up_requires_measurements_on_a_quarter_of_present_frames(measured, present, expected):
    heights = [740] * measured + [None] * (present - measured) + [999] * 12
    presence = ["present"] * present + ["absent"] * 12
    actual, reasons = close_up(heights, presence)
    assert actual is expected
    assert reasons == (
        ("Close-up: median face 74% of the frame height.",)
        if expected
        else ("Close-up unmeasured",)
    )
    if present:
        stricter = edited(
            CLOSE_UP, "min_face_frames_fraction = 0.25", "min_face_frames_fraction = 0.5"
        )
        assert close_up(heights, presence, text=stricter) == (False, ("Close-up unmeasured",))


# --- what a window's reading makes of it: speech and the audio lock --------------------------------------------


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
        TEXT,
        edited,
        first_round,
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


# --- what a window's reading makes of it: joint lips ---------------------------------------------------------


@pytest.mark.parametrize("window", [0, 5])
@pytest.mark.parametrize("silent_intervals", [(), ((0, 10),)])
def test_006_joint_unmeasured_lips_lock_and_bridge_inherits(window, silent_intervals):
    series, presence = trial("006", window)
    derived = quiet_facts(series, presence, POLICY.mouth)
    assert derived.measured_frames == derived.open_frames == 0
    facts = replace(joint_facts(), quiet=False)
    facts = replace(
        facts, tracks=tuple(replace(t, presence=p) for t, p in zip(facts.tracks, presence))
    )
    mouth = {t.id: lips(m.aperture, m.face_height) for t, m in zip(facts.tracks, series)}

    def window(has_audio=True, **changes):
        reading_args = {"has_audio": has_audio, "mouth": mouth}
        changes = {"silent_intervals": silent_intervals, **changes}
        return first_round(facts, reading_args=reading_args, **changes)

    read = window()
    assert not read.facts.quiet and not read.facts.speaking and read.facts.lips_unmeasured
    first = read.plan
    assert first.arm.audio_lock and any("joint; lips unmeasured" in r for r in first.reasons)
    bridge = plan_bridge(
        replace(read.facts, handoff="bridge"),
        first.arm,
        first.arm.crop,
        CAP.replacement,
        POLICY,
        CAP.frame_grid,
    )
    assert bridge.arm.audio_lock and bridge.arm.seed_count == 1
    assert not plan(joint_facts()).arm.audio_lock
    # A quiet window has no silent intervals of its own.
    for changes in (
        {"speaking": False},
        {"speaking": True},
        {"quiet": True, "silent_intervals": ()},
    ):
        assert not window(**changes).facts.lips_unmeasured
    assert not window(has_audio=False).facts.lips_unmeasured


@pytest.mark.parametrize(
    "scan,has_series", [(None, False), ("unavailable", False), ("unavailable", True)]
)
@pytest.mark.parametrize("silent_intervals", [(), ((0, 10),)])
def test_joint_windows_without_a_lip_scan_do_not_derive_unmeasured_lock(
    scan, has_series, silent_intervals
):
    facts = replace(joint_facts(), quiet=False)
    unmeasured = lips([None] * 73, [None] * 73)
    mouth = {t.id: unmeasured for t in facts.tracks} if has_series else None
    read = first_round(
        facts,
        reading_args={"has_audio": True, "mouth": mouth, "mouth_scan": scan},
        silent_intervals=silent_intervals,
    )
    assert read.facts.silent_intervals == silent_intervals
    assert not read.facts.quiet and not read.facts.speaking and not read.facts.lips_unmeasured
    assert not read.plan.arm.audio_lock


# --- what a shot's later windows keep ------------------------------------------------------------------------


@pytest.mark.parametrize("close_up", [False, True])
@pytest.mark.parametrize("first_arm_faceswap", ["close_up", "never"])
def test_window_one_pins_its_treatment_faceswap_and_close_up_for_the_shot(
    close_up, first_arm_faceswap
):
    text = TEXT.replace('faceswap = "close_up"', f'faceswap = "{first_arm_faceswap}"')
    expected = close_up and first_arm_faceswap == "close_up"
    planned = first_round(text=text, close_up=close_up)
    assert planned.pins == ShotPins("rgb", expected, close_up)
    later = replace(mug_facts(), close_up=close_up)
    args = {"treatment_override": "rgb", "faceswap_override": expected}
    first_arm = round_plan(later, text=text, **args).arm
    next_arm = round_plan(
        later, (attempt(first_arm, (result("arcface_target"),)),), text=text, **args
    ).arm
    assert first_arm.faceswap is next_arm.faceswap is expected
    # A policy that names no FaceSwap first arm pins only the treatment, unless the request pins FaceSwap.
    assert first_round(text=OLD_TEXT, close_up=close_up).pins.faceswap_override is None
    assert first_round(text=OLD_TEXT, close_up=close_up, faceswap_override=False).pins == ShotPins(
        "inverted", False, close_up
    )
