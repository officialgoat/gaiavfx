"""The direction kernel (ADR 0009, ADR 0010): coverage, the technique policy, capabilities and plan_cut.

The cases mirror the user's blind ranks of 29 Sep 2026: text for a cut that continues what the render receives,
a views-only sheet with a pinned frame for new geography, nothing automatic when the relation is unknown.
"""

from dataclasses import replace
from fractions import Fraction

import pytest

from gaiavfx.integrations import minimax_h3
from gaiavfx.modules.direction.application import default_policy, policy_text
from gaiavfx.modules.direction.capabilities import (
    Capabilities,
    KeyframeAnchor,
    KnowledgePack,
    parse_pack,
)
from gaiavfx.modules.direction.coverage import (
    AngleRelation,
    Coverage,
    CutContext,
    Evidence,
    Relation,
    assess_cut,
    relation_from_yaw,
)
from gaiavfx.modules.direction.planning import Action, Available, plan_cut
from gaiavfx.modules.direction.techniques import Block, Status, parse_policy
from gaiavfx.modules.direction.timebase import FrameGrid

H3 = minimax_h3.capabilities()
PACK = minimax_h3.knowledge_pack()
POLICY = default_policy()


def fake_capabilities(**changes):
    """A second, made-up integration: no keyframes after a cut, one picture slot."""
    base = Capabilities(
        integration="fake",
        picture_slots=1,
        keyframes=frozenset({KeyframeAnchor.START}),
        motion_context=False,
        media_guides=frozenset(),
        frame_grid=FrameGrid(
            fps=25, length_base=1, length_step=1, max_window=250, context_default=0
        ),
    )
    return replace(base, **changes)


FAKE_PACK = KnowledgePack(integration="fake", pin_offset_frames=None)


def cut(
    relation=None,
    crosses=None,
    *,
    same=False,
    receives=True,
    evidence=(),
    side_facts=False,
    cut_id="c1",
):
    source = "authored" if relation else "unknown"
    return CutContext(
        cut_id=cut_id,
        from_angle="a1",
        to_angle="a1" if same else "a2",
        relation=AngleRelation(relation, crosses, source),
        receives_predecessor=receives,
        evidence=evidence,
        side_facts=side_facts,
    )


def view(angle="a2", approved=True, evidence_id="v1"):
    return Evidence(evidence_id, angle, "survey_view", "asset-1", approved)


# ---- timebase ----------------------------------------------------------------------------------------------


def test_h3_frame_grid_holds_the_research_window():
    grid = H3.frame_grid
    assert grid.frames(Fraction(17, 4)) == 102  # 4.25 s delivered
    assert grid.window_for(delivered=102, context=22) == 124  # the research window, 5 + 7 * 17
    assert grid.valid_window(124) and not grid.valid_window(125)
    assert grid.window_frame(delivered=36 + 12, context=22) == 70  # the arcade pin: cut 58 + 12
    assert grid.max_window is None and grid.recommended_max_window == 396
    assert grid.window_for(delivered=480, context=22) == 515  # 20 s delivered, beyond guidance
    assert grid.valid_window(515)
    with pytest.raises(ValueError):
        replace(grid, max_window=396).window_for(delivered=480, context=22)


# ---- relations and coverage --------------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("delta", "expected"),
    [
        (0, Relation.SAME_DIRECTION),
        (40, Relation.SAME_DIRECTION),
        (90, Relation.SIDE),
        (-90, Relation.SIDE),
        (180, Relation.REVERSE),
        (-170, Relation.REVERSE),
        (360 + 30, Relation.SAME_DIRECTION),
    ],
)
def test_relation_from_yaw_is_a_turn_not_a_heading(delta, expected):
    assert (
        relation_from_yaw(delta, POLICY.same_direction_max_degrees, POLICY.reverse_min_degrees)
        is expected
    )


def test_rotating_both_cameras_does_not_change_the_relation():
    for rotation in (0, 73, 180, 271):
        delta = (200 + rotation) - (20 + rotation)
        assert relation_from_yaw(delta, 45, 135) is Relation.REVERSE


def test_a_known_relation_says_whether_it_crosses_a_barrier():
    with pytest.raises(ValueError):
        AngleRelation(Relation.SIDE, None, "authored")
    with pytest.raises(ValueError):
        AngleRelation(None, False, "unknown")


def test_same_direction_on_the_same_side_continues():
    assert assess_cut(cut(Relation.SAME_DIRECTION, False)).coverage is Coverage.CONTINUING
    assert assess_cut(cut(same=True)).coverage is Coverage.CONTINUING


def test_crossing_a_barrier_or_losing_the_predecessor_is_new():
    assert assess_cut(cut(Relation.SAME_DIRECTION, True)).coverage is Coverage.NEW
    assert assess_cut(cut(Relation.SAME_DIRECTION, False, receives=False)).coverage is Coverage.NEW


def test_side_and_reverse_views_need_evidence():
    for relation in (Relation.SIDE, Relation.REVERSE):
        assert assess_cut(cut(relation, False)).coverage is Coverage.NEW
        assessment = assess_cut(cut(relation, False, evidence=(view(),)))
        assert assessment.coverage is Coverage.ESTABLISHED and assessment.evidence_ids == ("v1",)


def test_unreviewed_evidence_is_not_evidence_yet():
    assessment = assess_cut(cut(Relation.REVERSE, True, evidence=(view(approved=False),)))
    assert assessment.coverage is Coverage.NEW
    assert any("waiting for review" in reason for reason in assessment.reasons)


def test_evidence_of_another_angle_does_not_count():
    assert (
        assess_cut(cut(Relation.REVERSE, True, evidence=(view(angle="a7"),))).coverage
        is Coverage.NEW
    )


def test_an_unknown_relation_is_unknown_unless_reviewed_evidence_exists():
    assert assess_cut(cut()).coverage is Coverage.UNKNOWN
    assert assess_cut(cut(evidence=(view(),))).coverage is Coverage.ESTABLISHED


# ---- the policy --------------------------------------------------------------------------------------------


def test_the_shipped_policy_carries_the_verdict():
    ids = [t.id for t in POLICY.techniques]
    assert ids == [
        "text",
        "text_and_views",
        "text_with_sides",
        "views_and_pin",
        "opening_text",
        "opening_views",
    ]
    assert POLICY.technique("views_and_pin").status is Status.DEFAULT
    # ADR 0019: a views-only sheet for an opening stays an option until the film-cut comparison is ranked
    assert POLICY.technique("opening_views").status is Status.OPTION
    assert POLICY.technique("text_with_sides").status is Status.EXPERIMENTAL
    assert all(Block.DIRECTION_TEXT in t.blocks for t in POLICY.techniques)


@pytest.mark.parametrize(
    ("change", "message"),
    [
        (lambda t: t.replace('status = "default"', 'status = "maybe"', 1), "status"),
        (lambda t: t.replace('coverage = ["continuing"]', 'coverage = ["seen"]', 1), "coverage"),
        (
            lambda t: t.replace('blocks = ["direction_text"]', 'blocks = ["depth_control"]', 1),
            "blocks",
        ),
        (
            lambda t: t.replace('blocks = ["direction_text"]', 'blocks = ["place_views"]', 1),
            "direction_text",
        ),
        (lambda t: t.replace('id = "text_and_views"', 'id = "text"', 1), "unique id"),
        (
            lambda t: t.replace(
                'title = "Direction text"\n', 'title = "Direction text"\nwhen = "always"\n', 1
            ),
            "unknown keys",
        ),
        (lambda t: t.replace("schema = 1", "schema = 2", 1), "schema"),
    ],
)
def test_the_policy_refuses_what_it_does_not_know(change, message):
    with pytest.raises(ValueError, match=message):
        parse_policy(change(policy_text()))


def test_a_pack_may_set_status_and_evidence_but_nothing_else():
    pack = parse_pack('schema = 1\nintegration = "x"\n[technique.text]\nstatus = "retired"\n')
    assert pack.status == {"text": Status.RETIRED}
    with pytest.raises(ValueError):
        parse_pack('schema = 1\nintegration = "x"\n[technique.text]\ncoverage = ["new"]\n')
    with pytest.raises(ValueError):
        parse_pack('schema = 1\nintegration = "x"\n[presets]\nwording = "x"\n')


def test_the_h3_pack_pins_twelve_frames_after_the_cut():
    assert PACK.integration == H3.integration == "minimax_h3"
    assert PACK.pin_offset_frames == 12


# ---- plan_cut ----------------------------------------------------------------------------------------------


def test_a_continuing_cut_gets_text_and_is_ready():
    plan = plan_cut(cut(Relation.SAME_DIRECTION, False), POLICY, H3, PACK)
    assert plan.assessment.coverage is Coverage.CONTINUING
    assert plan.recommended.technique_id == "text" and plan.recommended.ready
    assert plan.alternatives == () and plan.gaps == ()


def test_the_fidelity_option_offers_views_without_replacing_text():
    plan = plan_cut(cut(Relation.SAME_DIRECTION, False), POLICY, H3, PACK, fidelity=True)
    assert plan.recommended.technique_id == "text"
    [views] = plan.alternatives
    assert views.technique_id == "text_and_views" and not views.ready
    assert [a.action for a in views.actions] == [Action.VIEW_PLACE]
    ready = plan_cut(
        cut(Relation.SAME_DIRECTION, False),
        POLICY,
        H3,
        PACK,
        Available(place_views=5),
        fidelity=True,
    )
    assert ready.alternatives[0].ready


def test_a_reverse_without_evidence_asks_for_a_survey_then_a_pin():
    plan = plan_cut(cut(Relation.REVERSE, True), POLICY, H3, PACK)
    assert plan.assessment.coverage is Coverage.NEW
    choice = plan.recommended
    assert choice.technique_id == "views_and_pin" and not choice.ready
    assert [a.action for a in choice.actions] == [
        Action.VIEW_ANGLE,
        Action.INSERT_SUBJECT,
        Action.VIEW_PLACE,
    ]
    assert choice.keyframe_offset_frames == 12
    assert any("blockout" in limitation for limitation in choice.limitations)


def test_pending_reviews_show_up_as_review_actions():
    available = Available(
        pending_view_angles=frozenset({"a2"}), pending_keyframe_cuts=frozenset({"c1"})
    )
    plan = plan_cut(cut(Relation.REVERSE, True), POLICY, H3, PACK, available)
    assert [a.action for a in plan.recommended.actions][:2] == [
        Action.REVIEW_VIEW,
        Action.REVIEW_KEYFRAME,
    ]


def test_an_established_reverse_with_reviewed_view_and_pin_is_ready():
    available = Available(
        approved_view_angles=frozenset({"a2"}),
        approved_keyframe_cuts=frozenset({"c1"}),
        place_views=5,
    )
    plan = plan_cut(cut(Relation.REVERSE, True, evidence=(view(),)), POLICY, H3, PACK, available)
    assert plan.assessment.coverage is Coverage.ESTABLISHED
    assert plan.recommended.technique_id == "views_and_pin" and plan.recommended.ready


def test_a_side_view_with_known_sides_offers_the_experimental_text():
    plan = plan_cut(cut(Relation.SIDE, False, side_facts=True), POLICY, H3, PACK)
    assert plan.recommended.technique_id == "views_and_pin"
    [sides] = plan.alternatives
    assert (
        sides.technique_id == "text_with_sides"
        and sides.status is Status.EXPERIMENTAL
        and sides.ready
    )


def test_an_unknown_relation_recommends_nothing_and_asks_for_it():
    plan = plan_cut(cut(), POLICY, H3, PACK)
    assert plan.recommended is None
    [gap] = plan.gaps
    assert gap.code == "coverage_unknown" and gap.actions[0].action is Action.DECLARE_RELATION


def test_a_model_that_cannot_pin_gets_no_pin_technique():
    fake = fake_capabilities()
    plan = plan_cut(cut(Relation.REVERSE, True), POLICY, fake, FAKE_PACK)
    assert plan.recommended is None
    assert [tid for tid, _ in plan.unavailable] == ["views_and_pin"]
    assert plan.gaps[0].code == "technique_unavailable"
    continuing = plan_cut(cut(Relation.SAME_DIRECTION, False), POLICY, fake, FAKE_PACK)
    assert continuing.recommended.technique_id == "text"


def test_a_pack_can_retire_a_technique_for_its_model():
    retired = replace(PACK, status={"views_and_pin": Status.RETIRED})
    plan = plan_cut(cut(Relation.REVERSE, True), POLICY, H3, retired)
    assert plan.recommended is None and plan.gaps[0].code == "no_default_technique"


def test_an_override_is_honoured_and_marked():
    plan = plan_cut(cut(Relation.REVERSE, True), POLICY, H3, PACK, override="text")
    assert plan.override and plan.recommended.technique_id == "text"
    with pytest.raises(ValueError):
        plan_cut(
            cut(Relation.REVERSE, True),
            POLICY,
            fake_capabilities(),
            FAKE_PACK,
            override="views_and_pin",
        )


def test_a_pack_for_another_model_is_refused():
    with pytest.raises(ValueError):
        plan_cut(cut(Relation.REVERSE, True), POLICY, H3, FAKE_PACK)


def test_names_never_change_a_plan():
    """Renaming the cut and the angles changes nothing but the names (no per-place or per-shot branches)."""
    a = plan_cut(
        cut(Relation.REVERSE, True, evidence=(view(),)),
        POLICY,
        H3,
        PACK,
        Available(approved_view_angles=frozenset({"a2"})),
    )
    renamed = CutContext(
        cut_id="the-harbour-reverse",
        from_angle="pier-side",
        to_angle="water-side",
        relation=AngleRelation(Relation.REVERSE, True, "authored"),
        receives_predecessor=True,
        evidence=(view(angle="water-side"),),
    )
    b = plan_cut(
        renamed, POLICY, H3, PACK, Available(approved_view_angles=frozenset({"water-side"}))
    )
    assert a.assessment == b.assessment
    assert [
        (c.technique_id, [x.action for x in c.actions]) for c in (a.recommended, *a.alternatives)
    ] == [(c.technique_id, [x.action for x in c.actions]) for c in (b.recommended, *b.alternatives)]
