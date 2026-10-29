"""Replacement contract, true track geometry, policy and bounded planner."""

import json
from dataclasses import FrozenInstanceError, fields, replace
from pathlib import Path

import pytest
from test_direction_kernel import fake_capabilities

from gaiavfx.integrations.minimax_h3 import capabilities
from gaiavfx.modules.direction.application import (
    default_replacement_policy,
)
from gaiavfx.modules.direction.application import (
    replacement_policy_text as shipped_policy_text,
)
from gaiavfx.modules.direction.person_crop import to_native
from gaiavfx.modules.direction.replacement import (
    CHECKS,
    Arm,
    Attempt,
    Box,
    Bystander,
    CheckResult,
    Gap,
    Letterbox,
    Occluder,
    Performer,
    ReplacementFacts,
    Track,
    parse_replacement_policy,
    plan_replacement,
    to_record,
)

FIXTURES = Path(__file__).parent / "fixtures" / "parity"
CAP = capabilities()
# Preserve the A1 RGB research/golden cases and their shorter escalation ladder.
POLICY = replace(
    default_replacement_policy(),
    treatment="rgb",
    escalation=tuple(
        r
        for r in default_replacement_policy().escalation
        if not (r.on == "likeness" and r.to in {"seed", "faceswap"})
    ),
)


def replacement_policy_text():
    return (
        shipped_policy_text()
        .replace(
            '\n[[escalation]]\non = "likeness"\nchecks = ["arcface_target", "residual_source", "inverted_copy"]\nfires_on = ["fail"]\nto = "faceswap"\n',
            "",
        )
        .replace(
            '\n[[escalation]]\non = "likeness"\nchecks = ["arcface_target", "residual_source", "inverted_copy"]\nfires_on = ["fail"]\nto = "seed"\n',
            "",
        )
    )


def attempt(arm, checks):
    return Attempt(arm, (checks,) * arm.seed_count)


def geometry_fixture():
    return json.loads((FIXTURES / "REPL-MUG-GEOMETRY.json").read_text(encoding="utf-8"))


def mug_facts(frames=73):
    """True actor/woman bounds; props and chair context are distinct regions."""
    geometry = geometry_fixture()
    lb = geometry["letterbox"]
    native = tuple(geometry["frame"])
    actor = Box(*to_native((588, 4, 863, 508), lb["fit"], lb["offset"], native))
    woman = Box(*to_native((50, 173, 471, 508), lb["fit"], lb["offset"], native))
    guide = json.loads((FIXTURES / "REPL-MUG-GUIDE.json").read_text(encoding="utf-8"))
    prompt = guide["graph"]["conditioning"]["inputs"]["prompt"]
    target = prompt.split(", with ", 1)[1].split(".\n", 1)[0]
    action = prompt.split(
        "[Shot 1] <Subject 1> appears as the standing performer at screen right. "
    )[1].split(" Render natural scene colors.")[0]
    return ReplacementFacts(
        source_asset="source",
        shot_start=0,
        shot_end=frames,
        native=native,
        fps=24,
        letterbox=Letterbox(tuple(lb["fit"]), tuple(lb["offset"]), tuple(lb["canvas"])),
        performers=(
            Performer(
                "actor",
                "target",
                "subject",
                target,
                "standing performer",
                "at screen right",
                action,
            ),
        ),
        bystanders=(Bystander("woman", "seated woman", "at screen left", ("her blue cup",)),),
        tracks=(
            Track("actor", actor, ("present",) * frames, None, "user"),
            Track("woman", woman, ("present",) * frames, None, "user"),
        ),
        occluders=(),
        scene_items=("the yellow mug, tableware and furniture",),
        quiet=False,
        silent_intervals=(),
        required_context=tuple(
            Box(*geometry["required"]["parts"][name]) for name in ("object-2 (yellow mug)", "chair")
        ),
        avoided_regions=(Box(*geometry["avoid"]["cup"]),),
    )


def joint_facts():
    f = mug_facts()
    first = replace(
        f.performers[0],
        action="The first replacement holds a yellow mug at chest height, looks toward the seated person, releases one hand from the mug, and lowers that hand toward the chair.",
    )
    second = Performer(
        "woman",
        "target-two",
        None,
        "short curly dark hair, a cream striped long-sleeve top and lilac overalls",
        "seated performer",
        "at screen left",
        "The second replacement stays seated at the table, leaning forward with her chin on her hand and holding the blue cup.",
    )
    return replace(
        f,
        performers=(first, second),
        bystanders=(),
        avoided_regions=(),
        quiet=True,
        scene_items=("the yellow mug", "the blue cup", "tableware and furniture"),
    )


def plan(facts=None, prior=(), policy=POLICY, support=CAP.replacement):
    return plan_replacement(facts or mug_facts(), support, policy, CAP.frame_grid, prior)


def test_audio_lock_is_a_capability_gated_first_arm_decision():
    speaking = replace(mug_facts(), speaking=True)
    assert plan(speaking).arm.audio_lock is True
    assert plan().arm.audio_lock is False
    assert plan(replace(joint_facts(), quiet=False, speaking=True)).arm.audio_lock is True
    assert (
        plan(speaking, support=replace(CAP.replacement, audio_lock=False)).arm.audio_lock is False
    )
    for text in (
        replacement_policy_text().replace('audio_lock = "speaking"', 'audio_lock = "never"'),
        replacement_policy_text().replace('audio_lock = "speaking"\n', ""),
        replacement_policy_text()
        .replace('audio_lock = "speaking"\n', "")
        .replace("speaking_min_open_frames = 12\n", ""),
    ):
        assert plan(speaking, policy=parse_replacement_policy(text)).arm.audio_lock is False


@pytest.mark.parametrize("locked", [False, True])
def test_every_escalation_preserves_the_frozen_audio_lock(locked):
    facts = replace(mug_facts(), speaking=locked)
    first = plan(facts).arm
    prior = (attempt(first, (result("arcface_target"),)),)
    second = plan(facts, prior=prior).arm
    prior += (attempt(second, (result("background_geometry"),)),)
    third = plan(facts, prior=prior).arm
    assert second.treatment == "inverted" and third.framing == "active"
    assert first.audio_lock is second.audio_lock is third.audio_lock is locked
    changed = plan(
        replace(facts, speaking=not locked),
        prior=prior,
        policy=replace(POLICY, audio_lock="never" if locked else "speaking"),
    ).arm
    assert changed.audio_lock is locked
    seed_policy = default_replacement_policy()
    first = plan(facts, policy=seed_policy).arm
    second = plan(
        facts, prior=(attempt(first, (result("residual_source"),)),), policy=seed_policy
    ).arm
    assert second.seed_offset == 2 and second.audio_lock is locked


@pytest.mark.parametrize(
    "key,value",
    [
        ("audio_lock", '"always"'),
        ("audio_lock", "true"),
        ("speaking_min_open_frames", "0"),
        ("speaking_min_open_frames", "1.5"),
        ("speaking_min_open_frames", "true"),
    ],
)
def test_audio_lock_policy_values_are_validated(key, value):
    old = '"speaking"' if key == "audio_lock" else "12"
    with pytest.raises((ValueError, TypeError)):
        parse_replacement_policy(
            replacement_policy_text().replace(f"{key} = {old}", f"{key} = {value}")
        )


def test_speaking_head_pass_never_locks():
    from test_head_plan import facts, planned

    assert planned(f=replace(facts(), speaking=True)).arm.audio_lock is False


def test_pilot_crop_matches_frozen_step0b_without_enlarging_tracks():
    f, expected = mug_facts(), geometry_fixture()
    result = plan(f)
    arm = result.arm
    assert (arm.treatment, arm.grouping, arm.framing, arm.round, arm.seed_count) == (
        "rgb",
        "single",
        "crop",
        0,
        2,
    )
    assert not result.stop and not result.gaps
    assert arm.crop.canvas == tuple(expected["canvas"]) == (608, 736)
    assert tuple(arm.crop.box) == pytest.approx(expected["box"], abs=1e-6)
    assert arm.crop.scale == pytest.approx(expected["scale"], abs=1e-6)
    assert arm.crop.interior_edges == tuple(k for k, v in expected["interior_edges"].items() if v)
    for actual, row in zip(arm.crop.matrix, expected["matrix_native_to_canvas"]):
        assert actual == pytest.approx(row, abs=1e-6)
    assert f.tracks[0].box.x0 == 840
    assert f.required_context[1].x0 < f.tracks[0].box.x0
    assert plan(replace(f, required_context=(), avoided_regions=())).arm.crop.canvas == (544, 832)


def test_joint_pair_and_three_performer_refusal():
    f = joint_facts()
    arm = plan(f).arm
    assert (arm.treatment, arm.grouping, arm.framing) == ("rgb", "joint", "pair")
    third = replace(f.performers[0], track_id="third", target_asset="third-target")
    f = replace(
        f, performers=(*f.performers, third), tracks=(*f.tracks, replace(f.tracks[0], id="third"))
    )
    result = plan(f)
    assert result.stop and result.arm is None
    assert [g.code for g in result.gaps] == ["too_many_performers"]
    # The current contract remains one or two performers even if a manifest grows.
    expanded = replace(CAP.replacement, max_performers=3, pictures=3)
    assert plan(f, support=expanded).gaps[0].code == "too_many_performers"


def test_scope_gaps_are_typed_and_missing_faces_are_not_absence():
    assert plan(mug_facts(90)).gaps[0].code == "untested_length"
    f = mug_facts()
    for margin in (None, 0.01):
        tracks = (replace(f.tracks[0], selected_by="identity", identity_margin=margin), f.tracks[1])
        assert plan(replace(f, tracks=tracks)).gaps[0].code == "ambiguous_identity"
    for state, blocked in (("unresolved", True), ("absent", True)):
        tracks = (replace(f.tracks[0], presence=(state,) * 73), f.tracks[1])
        result = plan(replace(f, tracks=tracks))
        assert result.stop is blocked
        if blocked:
            assert result.gaps[0].code == (
                "presence_unresolved" if state == "unresolved" else "performer_absent"
            )
    assert not plan(replace(f, fps=25)).stop
    assert plan(replace(f, fps=48)).gaps[0].code == "unsupported_fps"


def test_impossible_crop_falls_back_to_active_with_a_gap():
    f = mug_facts()
    result = plan(replace(f, required_context=(Box(0, 0, 1280, 720),)))
    assert not result.stop and result.arm.framing == "active" and result.arm.crop is None
    assert [g.code for g in result.gaps] == ["no_crop_fits"]
    assert any("falling back to active" in reason for reason in result.reasons)


def result(check, verdict="fail", advice="none"):
    return CheckResult(check, verdict, advice, None)


@pytest.mark.parametrize(
    "check,treatment,framing",
    [
        ("arcface_target", "inverted", "crop"),
        ("residual_source", "inverted", "crop"),
        ("background_geometry", "rgb", "active"),
        ("letterbox_difference", "rgb", "active"),
        ("mouth_aperture", "rgb", "crop"),
    ],
)
def test_escalation(check, treatment, framing):
    first = plan().arm
    next_plan = plan(prior=(attempt(first, (result(check),)),))
    assert not next_plan.stop
    assert (
        next_plan.arm.treatment,
        next_plan.arm.framing,
        next_plan.arm.round,
        next_plan.arm.seed_count,
    ) == (treatment, framing, 1, 1)


def test_all_stop_conditions_and_seed_budget():
    arm = plan().arm
    unsupported = tuple(result(c, "unsupported") for c in CHECKS)
    assert plan(prior=(attempt(arm, unsupported),)).reasons == ("no calibrated signal",)
    passed = tuple(result(c, "pass") for c in CHECKS)
    assert plan(prior=(attempt(arm, passed),)).reasons == ("all calibrated checks passed",)
    assert plan(prior=(attempt(arm, (result("seam"),)),)).reasons == ("no escalation trigger",)
    prior = ()
    for i in range(3):
        current = plan(prior=prior)
        assert current.arm.round == i
        prior += (attempt(current.arm, (result("mouth_aperture"),)),)
    assert [a.arm.seed_count for a in prior] == [2, 1, 1]
    assert sum(a.arm.seed_count for a in prior) == POLICY.renders
    assert plan(prior=prior).reasons == ("maximum rounds reached",)
    limited = replace(POLICY, renders=1)
    first = plan(policy=limited).arm
    assert first.seed_count == 1
    assert plan(policy=limited, prior=(attempt(first, (result("mouth_aperture"),)),)).reasons == (
        "render budget exhausted",
    )
    first = plan().arm
    prior = (attempt(first, (result("arcface_target"),)),)
    inverted = plan(prior=prior).arm
    prior += (attempt(inverted, (result("arcface_target"),)),)
    assert plan(prior=prior).reasons == ("escalation would repeat an earlier arm",)


def test_policy_order_and_intended_geometry_advisory_path():
    first = plan().arm
    checks = (result("arcface_target"), result("background_geometry"))
    prior = (attempt(first, checks),)
    assert plan(prior=prior).arm.treatment == "inverted"
    text = replacement_policy_text()
    start = text.index("[[escalation]]")
    end = text.index("[[technique]]")
    rows = text[start:end].split("[[escalation]]")[1:]
    reordered = (
        text[:start]
        + "".join("[[escalation]]" + r for r in (rows[1], rows[0], rows[2]))
        + text[end:]
    )
    assert plan(prior=prior, policy=parse_replacement_policy(reordered)).arm.framing == "active"
    advisory = (attempt(first, (result("background_geometry", "unsupported", "retry"),)),)
    assert plan(prior=advisory).arm.framing == "active"
    no_retry = parse_replacement_policy(text.replace('["fail", "retry"]', '["fail"]'))
    assert plan(prior=advisory, policy=no_retry).stop
    # The mechanism remains available when a policy explicitly enables it.
    retry_policy = replace(
        POLICY,
        escalation=tuple(replace(row, fires_on=("fail", "retry")) for row in POLICY.escalation),
    )
    assert (
        plan(
            prior=(attempt(first, (result("mouth_aperture", "pass", "retry"),)),),
            policy=retry_policy,
        ).arm.round
        == 1
    )


def test_first_arm_similarity_is_optional_policy_data():
    f = replace(mug_facts(), source_target_similarity=0.8)
    assert plan(f).arm.treatment == "rgb"
    text = replacement_policy_text().replace(
        "[first_arm]", "[first_arm]\ninverted_when_similarity_at_least = 0.8"
    )
    calibrated = parse_replacement_policy(text)
    assert plan(f, policy=calibrated).arm.treatment == "inverted"
    assert plan(replace(f, source_target_similarity=0.79), policy=calibrated).arm.treatment == "rgb"
    assert plan(mug_facts(), policy=calibrated).arm.treatment == "rgb"


@pytest.mark.parametrize(
    "old,new",
    [
        ("schema = 1", "schema = 1\nunknown = true"),
        ("max_rounds = 3", "max_rounds = 3\nunknown = 1"),
        ("[loop]", "[missing_loop]"),
        ("[first_arm]", "[first_arm]\nunknown = 0.2"),
        ('on = "likeness"', 'on = "quality"'),
        ('status = "default"', 'status = "tested"'),
        ('to = "treatment"', 'to = "mask"'),
        ('"arcface_target"', '"invented_check"'),
        ('["fail", "retry"]', '["unsupported"]'),
        ("renders = 4", "renders = true"),
        ("renders_per_round = [2, 1, 1]", "renders_per_round = [2, 1]"),
        ("feather = 24", "feather = nan"),
    ],
)
def test_policy_refuses_unknown_keys_values_and_missing_sections(old, new):
    with pytest.raises((ValueError, TypeError)):
        parse_replacement_policy(replacement_policy_text().replace(old, new))


def test_default_policy_and_capability_manifest():
    assert default_replacement_policy() == parse_replacement_policy(shipped_policy_text())
    assert (POLICY.max_rounds, POLICY.renders, POLICY.renders_per_round) == (3, 4, (2, 1, 1))
    support = CAP.replacement
    assert support.guide_video and support.pictures == support.max_performers == 2
    assert support.treatments == {"rgb", "inverted"}
    assert support.groupings == {"single", "joint"}
    assert support.framings == {"crop", "pair", "active", "full", "head"}
    assert (support.canvas_budget_pixels, support.canvas_step, support.tested_frames) == (
        458752,
        32,
        {56, 73},
    )
    assert not CAP.media_guides
    fake = fake_capabilities()
    assert fake.replacement is None
    assert plan(support=fake.replacement).reasons == ("replacement unsupported",)
    gap = plan(support=fake.replacement).gaps[0]
    assert isinstance(gap, Gap) and gap.code == "replacement_unsupported"


@pytest.mark.parametrize(
    "make",
    [
        lambda: Box(0, 0, 0, 1),
        lambda: Box(0, 0, float("nan"), 1),
        lambda: Letterbox((10, 10), (1, 0), (10, 10)),
        lambda: replace(mug_facts().performers[0], role="standing jacket wearer"),
        lambda: replace(mug_facts().performers[0], position="in the blond hair"),
        lambda: replace(mug_facts().performers[0], target_text="<Picture 2>"),
        lambda: replace(mug_facts(), performers=()),
        lambda: replace(mug_facts(), shot_end=0),
        lambda: replace(mug_facts(), quiet=True, silent_intervals=((0, 24),)),
        lambda: replace(mug_facts(), silent_intervals=((24, 48), (36, 60))),
        lambda: replace(mug_facts(), silent_intervals=((0, 96),)),
        lambda: replace(mug_facts(), required_context=(Box(-1, 0, 1, 1),)),
        lambda: replace(mug_facts(), avoided_regions=((0, 0, 1, 1),)),
        lambda: replace(mug_facts(), source_target_similarity=float("nan")),
        lambda: replace(mug_facts(), source_target_similarity=1.1),
        lambda: replace(mug_facts(), tracks=(mug_facts().tracks[0],)),
        lambda: replace(mug_facts().tracks[0], presence=("unknown",)),
        lambda: replace(
            mug_facts(),
            tracks=(replace(mug_facts().tracks[0], presence=("present",)), mug_facts().tracks[1]),
        ),
        lambda: Arm("rgb", "single", "crop", None, 2, 0),
        lambda: replace(plan().arm, framing="full"),
        lambda: replace(plan().arm, seed_count=0),
        lambda: CheckResult("seam", "unknown", "none", None),
        lambda: CheckResult("seam", "pass", "none", float("inf")),
        lambda: Occluder("hand", "unknown", "lower face"),
    ],
)
def test_invalid_contracts_are_refused(make):
    with pytest.raises((ValueError, TypeError)):
        make()


def test_frozen_contract_authority_and_fraction_intervals():
    f = replace(mug_facts(), silent_intervals=((36, 60),))
    with pytest.raises(FrozenInstanceError):
        f.quiet = True
    assert not any(
        "take" in field.name or "select" in field.name or "approv" in field.name
        for field in fields(type(plan()))
    )
    assert f.delivered_frames == 73
    assert replace(f, required_context=(), avoided_regions=()).required_context == ()


# Cross-family review probes: per-take policy, deterministic retries and records.
def test_matching_repeat_does_not_mask_a_later_geometry_escalation():
    first = plan().arm
    prior = (attempt(first, (result("arcface_target"),)),)
    second = plan(prior=prior).arm
    assert (second.treatment, second.framing, second.seed_offset) == ("inverted", "crop", 2)
    prior += (attempt(second, (result("arcface_target"), result("background_geometry"))),)
    third = plan(prior=prior)
    assert not third.stop
    assert (third.arm.treatment, third.arm.framing, third.arm.seed_offset) == (
        "inverted",
        "active",
        3,
    )
    assert third.reasons == ("geometry: framing",)


@pytest.mark.parametrize("check", ["arcface_target", "residual_source", "mouth_aperture"])
def test_shipped_uncalibrated_face_and_mouth_advice_does_not_spend_renders(check):
    first = plan().arm
    assert plan(prior=(attempt(first, (result(check, "unsupported", "retry"),)),)).stop
    enabled = parse_replacement_policy(
        replacement_policy_text().replace('fires_on = ["fail"]', 'fires_on = ["fail", "retry"]')
    )
    assert not plan(
        prior=(attempt(first, (result(check, "unsupported", "retry"),)),), policy=enabled
    ).stop
    assert plan(
        prior=(attempt(first, (result(check, "unsupported", "none"),)),), policy=enabled
    ).stop


def test_per_take_all_and_any_rules_are_policy_data():
    first = plan().arm
    mixed = Attempt(first, ((result("arcface_target"),), (result("arcface_target", "pass"),)))
    assert POLICY.take_rule == "all"
    assert plan(prior=(mixed,)).stop
    any_policy = parse_replacement_policy(
        replacement_policy_text().replace('take_rule = "all"', 'take_rule = "any"')
    )
    assert plan(prior=(mixed,), policy=any_policy).arm.treatment == "inverted"
    # Each take may trigger a different check belonging to the same escalation row.
    failures = Attempt(first, ((result("arcface_target"),), (result("residual_source"),)))
    assert plan(prior=(failures,)).arm.treatment == "inverted"
    unknown = Attempt(
        first, ((result("arcface_target"),), (result("arcface_target", "unsupported"),))
    )
    assert plan(prior=(unknown,)).stop
    assert not plan(prior=(unknown,), policy=any_policy).stop
    survivor = Attempt(first, ((result("arcface_target"),),))
    assert plan(prior=(survivor,)).arm.seed_offset == first.seed_count
    with pytest.raises(ValueError, match="successful rendered takes"):
        Attempt(first, ())
    with pytest.raises(ValueError, match="duplicate"):
        Attempt(first, ((result("arcface_target"), result("arcface_target")), ()))


@pytest.mark.parametrize(
    "replacement", ['take_rule = "majority"', "take_rule = true", 'take_rule = ["all"]', ""]
)
def test_take_rule_is_required_and_strict(replacement):
    with pytest.raises((ValueError, TypeError)):
        parse_replacement_policy(
            replacement_policy_text().replace('take_rule = "all"', replacement)
        )


def test_seed_offsets_cover_distinct_deterministic_ranges():
    prior = ()
    seeds = []
    for offset, count in ((0, 2), (2, 1), (3, 1)):
        arm = plan(prior=prior).arm
        assert (arm.seed_offset, arm.seed_count) == (offset, count)
        seeds.extend(271828 + arm.seed_offset + i for i in range(arm.seed_count))
        prior += (attempt(arm, (result("mouth_aperture"),)),)
    assert seeds == [271828, 271829, 271830, 271831]
    assert plan(prior=prior).stop
    with pytest.raises(ValueError, match="seed offsets"):
        plan(prior=(attempt(replace(prior[0].arm, seed_offset=1), (result("mouth_aperture"),)),))
    with pytest.raises(ValueError, match="seed_offset"):
        replace(prior[0].arm, seed_offset=-1)


def test_success_reason_precedes_round_and_budget_bounds():
    prior = ()
    for i in range(3):
        arm = plan(prior=prior).arm
        checks = tuple(result(c, "pass") for c in CHECKS) if i == 2 else (result("mouth_aperture"),)
        prior += (attempt(arm, checks),)
    assert plan(prior=prior).reasons == ("all calibrated checks passed",)
    one = replace(POLICY, renders=1)
    arm = plan(policy=one).arm
    assert plan(prior=(attempt(arm, (result("arcface_target", "pass"),)),), policy=one).reasons == (
        "all calibrated checks passed",
    )


def test_techniques_match_the_arm_and_policy_applicability():
    first = plan()
    assert set(first.technique_ids) == {"replacement_reference", "replacement_crop"}
    prior = (attempt(first.arm, (result("arcface_target"),)),)
    second = plan(prior=prior)
    assert set(second.technique_ids) == {
        "replacement_reference",
        "replacement_crop",
        "replacement_inversion",
    }
    prior += (attempt(second.arm, (result("background_geometry"),)),)
    third = plan(prior=prior)
    assert set(third.technique_ids) == {"replacement_reference", "replacement_inversion"}
    assert set(plan(joint_facts()).technique_ids) == {
        "replacement_reference",
        "replacement_crop",
        "replacement_joint",
    }
    changed = parse_replacement_policy(
        replacement_policy_text().replace('treatment = ["inverted"]', 'treatment = ["rgb"]')
    )
    assert "replacement_inversion" in plan(policy=changed).technique_ids
    assert not plan(support=None).technique_ids


@pytest.mark.parametrize(
    "old,new",
    [
        ("applies_to = { treatment", "applies_to = { unknown"),
        ('treatment = ["rgb", "inverted"]', 'treatment = ["gray"]'),
        ('grouping = ["single", "joint"]', 'grouping = "single"'),
        ('framing = ["crop", "pair"]', "framing = []"),
    ],
)
def test_technique_applicability_is_strict(old, new):
    with pytest.raises((ValueError, TypeError)):
        parse_replacement_policy(replacement_policy_text().replace(old, new))


def test_canvas_search_parameters_come_only_from_capabilities():
    support = CAP.replacement
    assert (support.canvas_low, support.canvas_high, support.canvas_aspect) == (
        256,
        1280,
        (0.5, 2.0),
    )
    restricted = replace(support, canvas_low=512, canvas_high=512, canvas_aspect=(1.0, 1.0))
    assert plan(support=restricted).arm.framing == "active"
    from gaiavfx.modules.direction.person_crop import canvas_candidates

    assert canvas_candidates(90000, 16, 300, 320, (0.9, 1.1)) == []
    assert canvas_candidates(100000, 16, 300, 320, (0.9, 1.1)) == [
        (304, 304),
        (304, 320),
        (320, 304),
    ]
    for changes in (
        {"canvas_low": 0},
        {"canvas_high": 128},
        {"canvas_aspect": (0, 2)},
        {"canvas_aspect": (1, float("inf"))},
    ):
        with pytest.raises(ValueError):
            replace(support, **changes)


def test_bystander_dilation_matches_step0b_without_mutating_track(monkeypatch):
    from gaiavfx.modules.direction import person_crop

    facts = mug_facts()
    original = facts.tracks[1].box
    expected = geometry_fixture()["avoid"]["object-3 (seated woman, dilated SAM)"]
    limits = person_crop.limits
    seen = []

    def capture(required, avoid, frame):
        seen.extend(avoid)
        return limits(required, avoid, frame)

    monkeypatch.setattr(person_crop, "limits", capture)
    assert plan(facts).arm.crop.canvas == (608, 736)
    assert tuple(dict(seen)["woman"]) == pytest.approx(expected)
    assert facts.tracks[1].box == original
    assert dict(seen)["avoided region 0"] == facts.avoided_regions[0]


def test_gap_plans_are_equal_and_dataclass_records_are_safe():
    from dataclasses import asdict

    from gaiavfx.modules.direction.capabilities import CapabilityGap
    from gaiavfx.modules.direction.replacement import ReplacementPlan, from_record, to_record

    for factory in (
        lambda: plan(support=None),
        lambda: plan(mug_facts(90)),
        lambda: plan(replace(mug_facts(), fps=25)),
    ):
        value = factory()
        assert value == factory()
        assert all(type(g) is Gap for g in value.gaps)
        json.dumps(asdict(value), allow_nan=False)
        assert (
            from_record(ReplacementPlan, json.loads(json.dumps(to_record(value), allow_nan=False)))
            == value
        )
    assert Gap.from_capability_gap(CapabilityGap("unsupported", "message")) == Gap(
        "unsupported", "message"
    )
    with pytest.raises(ValueError):
        replace(plan(support=None), gaps=(CapabilityGap("unsupported", "message"),))


def test_records_round_trip_typed_values_and_finite_clearance():
    from dataclasses import asdict

    from gaiavfx.modules.direction.person_crop import fit
    from gaiavfx.modules.direction.replacement import CropPlan, from_record, to_record

    arm = plan().arm
    f = replace(mug_facts(), silent_intervals=((36, 60),))
    whole = CropPlan.from_geometry(
        fit((0, 0, 100, 100), (100, 100), (100, 100), 0, (0, 0, 100, 100)), 0
    )
    assert whole.interior_edges == () and whole.clearance is None
    for value in (
        arm,
        arm.crop,
        whole,
        result("seam", "unsupported", "retry"),
        attempt(arm, (result("seam"),)),
        Gap("gap", "reason"),
        plan(),
        plan(support=None),
    ):
        record = to_record(value)
        loaded = json.loads(json.dumps(record, allow_nan=False))
        assert from_record(type(value), loaded) == value
        json.dumps(asdict(value), allow_nan=False)
    record = to_record(f)
    assert all(type(v) is int for row in record["silent_intervals"] for v in row)
    restored = from_record(ReplacementFacts, json.loads(json.dumps(record, allow_nan=False)))
    assert restored.silent_intervals == ((36, 60),)
    assert to_record(restored) == record
    assert restored.required_context == f.required_context
    assert restored.avoided_regions == f.avoided_regions
    for interval in ([[2, 1]], [[0, 74]], [[0, float("nan")]], [[True, 1]]):
        with pytest.raises((ValueError, TypeError)):
            from_record(ReplacementFacts, {**record, "silent_intervals": interval})
    for broken in ({**to_record(arm), "seed_offset": -1}, {**to_record(arm), "unknown": 0}):
        with pytest.raises(ValueError):
            from_record(Arm, broken)
    with pytest.raises(ValueError):
        from_record(CropPlan, {**to_record(whole), "clearance": float("inf")})
    with pytest.raises(ValueError):
        from_record(CropPlan, {**to_record(arm.crop), "clearance": float("inf")})


def test_motion_context_contract_and_old_facts():
    from gaiavfx.modules.direction.replacement import from_record, to_record

    facts = replace(mug_facts(), context_frames=22)
    assert plan(facts).arm is not None
    assert {
        g.code for g in plan(facts, support=replace(CAP.replacement, motion_context=False)).gaps
    } == {"chain_unsupported"}
    assert {g.code for g in plan(replace(facts, context_frames=17)).gaps} == {"untested_context"}
    old = to_record(facts)
    del old["context_frames"]
    assert from_record(ReplacementFacts, old).context_frames == 0
    for value in (-1, 73, True, 22.0):
        with pytest.raises(ValueError):
            replace(facts, context_frames=value)


def test_handoff_request_validation():
    from gaiavfx.application.replace_character import _request
    from gaiavfx.shared.errors import ApplicationError

    request = {
        "source_asset": "s",
        "analysis_operation": "a",
        "performers": [{"track_id": "p", "target_asset": "t"}],
        "handoff": {"predecessor_operation": "previous", "candidate_id": "candidate"},
        "crop_override": to_record(plan().arm.crop),
    }
    assert _request(request).handoff.candidate_id == "candidate"
    with pytest.raises(ApplicationError) as exc:
        _request({**request, "head": {"parent_asset": "parent"}})
    assert exc.value.status == 422


def test_shared_crop_covers_union_and_refuses_avoided_union():
    from gaiavfx.modules.direction.replacement import union_crop

    first = mug_facts()
    second = replace(first, required_context=(*first.required_context, Box(950, 50, 980, 500)))
    crop = union_crop([first, second], POLICY, CAP.replacement)
    assert crop.box.x0 <= 950 and 980 <= crop.box.x1
    assert crop.box.y0 <= 50 and 500 <= crop.box.y1
    with pytest.raises(ValueError):
        union_crop(
            [first, replace(second, avoided_regions=(Box(950, 50, 980, 500),))],
            POLICY,
            CAP.replacement,
        )
    initial = plan_replacement(first, CAP.replacement, POLICY, CAP.frame_grid, shared_crop=crop)
    assert initial.arm.crop == crop
    prior = (
        attempt(
            initial.arm,
            (
                CheckResult("background_geometry", "fail", "retry", 2.0),
                CheckResult("mouth_aperture", "fail", "retry", 2.0),
            ),
        ),
    )
    next_plan = plan_replacement(
        first,
        CAP.replacement,
        POLICY,
        CAP.frame_grid,
        prior,
        shared_crop=crop,
    )
    assert next_plan.arm is not None
    assert next_plan.arm.crop == crop and next_plan.arm.framing == "crop"
    assert next_plan.arm.round == 1 and next_plan.arm.seed_offset == initial.arm.seed_count
    assert "geometry: framing" not in next_plan.reasons
    unpinned = plan_replacement(first, CAP.replacement, POLICY, CAP.frame_grid, prior)
    assert unpinned.arm is not None and unpinned.arm.framing == "active"
    assert unpinned.arm.crop != crop and "geometry: framing" in unpinned.reasons


def test_handoff_capability_and_old_facts_backfill():
    from gaiavfx.modules.direction.replacement import from_record, to_record

    facts = replace(mug_facts(), handoff="latent")
    assert CAP.replacement.masked_span
    assert {
        g.code for g in plan(facts, support=replace(CAP.replacement, masked_span=False)).gaps
    } == {"handoff_unsupported"}
    record = to_record(facts)
    del record["handoff"]
    assert from_record(ReplacementFacts, record).handoff == "none"
    with pytest.raises(ValueError):
        replace(facts, context_frames=22)
