"""Head geometry, contract, support constraints and the closed H3 head wording."""

import ast
import re
from dataclasses import replace
from pathlib import Path

import pytest

from gaiavfx.integrations.minimax_h3 import capabilities
from gaiavfx.integrations.minimax_h3.replacement import (
    RETIRED,
    build_replacement_graph,
    compile_replacement,
    head_wording,
    replacement_wording,
)
from gaiavfx.modules.direction import head
from gaiavfx.modules.direction.application import (
    default_replacement_policy,
    replacement_policy_text,
)
from gaiavfx.modules.direction.head import HeadGap, head_track
from gaiavfx.modules.direction.replacement import (
    Arm,
    Box,
    Bystander,
    Performer,
    ReplacementFacts,
    Track,
    from_record,
    parse_replacement_policy,
    plan_head_pass,
    plan_replacement,
    to_record,
)

POLICY = default_replacement_policy()
CAP = capabilities()
BOXES = [(800 + i * 60 / 72, 300, 875 + i * 60 / 72, 405) for i in range(73)]


def facts():
    return ReplacementFacts(
        "film",
        600,
        673,
        (1920, 800),
        24,
        None,
        (Performer("one", "target", None, None, "performer", "at screen left", None),),
        (Bystander("two", "person", "at screen right", ()),),
        (),
        (),
        False,
        (),
        (
            Track("one", Box(770, 270, 960, 750), ("present",) * 73, 0.5, "identity"),
            Track("two", Box(960, 300, 1030, 700), ("present",) * 73, None, "user"),
        ),
    )


def planned(f=None, boxes=BOXES, support=CAP.replacement, prior=()):
    return plan_head_pass(f or facts(), boxes, support, POLICY, CAP.frame_grid, prior)


def test_006_geometry_and_contract_round_trip():
    plan = planned()
    assert not plan.gaps and plan.technique_ids == ("head_pass",)
    crop = plan.arm.crop
    assert crop.canvas == (512, 512) and 1.3 < crop.scale < 2
    assert crop.box.x1 - crop.box.x0 == pytest.approx(crop.box.y1 - crop.box.y0)
    assert 0 < crop.box.x0 < crop.box.x1 < 1920
    assert 0 < crop.box.y0 < crop.box.y1 < 800
    assert set(crop.interior_edges) == {"left", "right", "top", "bottom"}
    assert from_record(Arm, to_record(plan.arm)) == plan.arm
    from gaiavfx.api.production import ReplacementArmDTO

    assert ReplacementArmDTO.model_validate(to_record(plan.arm)).framing == "head"
    assert Arm("rgb", "single", "head", crop, 1, 0) == plan.arm
    with pytest.raises(ValueError):
        Arm("rgb", "single", "head", None, 1, 0)
    with pytest.raises(ValueError):
        Arm("rgb", "joint", "head", crop, 1, 0)


@pytest.mark.parametrize(
    "boxes,code",
    [
        ([(800, 150, 1100, 550)] * 73, "head_too_large"),
        (BOXES[:30] + [None] * 43, "no_face_track"),
        ([(800, 300, 830, 340)] * 73, "face_too_small"),
        ([None] * 73, "no_face_track"),
    ],
)
def test_typed_geometry_gaps(boxes, code):
    result = planned(boxes=boxes)
    assert result.arm is None and result.gaps[0].code == code


def test_fill_smoothing_and_present_coverage():
    a, b = (100, 100, 175, 205), (160, 100, 235, 205)
    policy = replace(POLICY.head, smoothing_window=0)
    series = head_track([a, None, None, b], ("present",) * 4, policy)
    assert series[0] == series[1] and series[2] == series[3]
    smooth = head_track(
        [a] * 8 + [(1000, 500, 1300, 800)] + [a] * 8, ("present",) * 17, POLICY.head
    )
    assert len(set(smooth)) == 1
    assert head_track([a, None, None], ("present", "absent", "absent"), policy)
    with pytest.raises(HeadGap, match="No usable"):
        head_track([a, None, None], ("present",) * 3, policy)


def test_scope_gaps_capability_and_single_round():
    f = facts()
    f = replace(f, tracks=(replace(f.tracks[0], identity_margin=0.01), f.tracks[1]))
    assert planned(f).gaps == plan_replacement(f, CAP.replacement, POLICY, CAP.frame_grid).gaps
    assert (
        planned(support=replace(CAP.replacement, framings=frozenset({"crop"}))).gaps[0].code
        == "replacement_unsupported"
    )
    assert (
        planned(support=replace(CAP.replacement, canvas_budget_pixels=100)).gaps[0].code
        == "replacement_unsupported"
    )
    assert planned(prior=(object(),)).reasons == ("head pass runs one round",)


@pytest.mark.parametrize(
    "old,new",
    [
        ("canvas_sides = [512, 640]", "canvas_sides = [513]"),
        ("canvas_sides = [512, 640]", "canvas_sides = []"),
        ("min_scale = 1.0", "min_scale = -0.1"),
        ("up_shift = 0.1", "up_shift = 0.1\nunknown = 1"),
    ],
)
def test_closed_policy(old, new):
    with pytest.raises(ValueError):
        parse_replacement_policy(replacement_policy_text().replace(old, new))


def test_head_module_has_only_pure_imports():
    tree = ast.parse(Path(head.__file__).read_text())
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            assert {n.name for n in node.names} <= {"math"}
        if isinstance(node, ast.ImportFrom):
            assert node.module in {"dataclasses", "statistics", "gaiavfx.modules.direction"}
            if node.module == "gaiavfx.modules.direction":
                assert [n.name for n in node.names] == ["person_crop"]


def test_head_wording_closed_differences_golden_and_graph():
    body, words = replacement_wording(), head_wording()
    different = {
        (table, key)
        for table in ("sections", "lines", "words")
        for key, value in getattr(body, table).items()
        if getattr(words, table)[key] != value
    }
    assert words.sections["summary"].startswith("[video editing] ")
    assert body.sections["summary"].startswith("[video editing] ")
    assert different == {
        ("sections", "summary"),
        ("lines", "target_retention"),
        ("lines", "source_retention"),
    }
    compiled = compile_replacement(facts(), planned().arm)
    golden = Path(__file__).parent / "golden/minimax_h3/replacement/head.txt"
    assert compiled.text == golden.read_text("utf-8")
    assert len(compiled.text.split("\n\n")) == 6
    assert "body" not in compiled.prompt.summary and "clothing" not in compiled.prompt.summary
    assert not any(word in compiled.text.lower() for word in RETIRED)
    assert set(re.findall(r"<[^>]+>", compiled.text)) <= {
        "<Video 1>",
        "<Picture 1>",
        "<Subject 1>",
        "<Subject 2>",
        "<Subject 3>",
    }
    assert "fully_preserved - Preserve the person" in compiled.text
    graph = build_replacement_graph(
        compiled,
        guide_file="guide.mkv",
        picture_files={"target": "target.png"},
        seed=7,
        output_prefix="head-test",
    )
    assert any(n["inputs"].get("width") == n["inputs"].get("height") == 512 for n in graph.values())
    quiet = compile_replacement(replace(facts(), quiet=True), planned().arm)
    assert "lips gently closed" in quiet.text


def test_head_plan_cannot_offer_canvas_above_budget():
    large = replace(POLICY, head=replace(POLICY.head, canvas_sides=(768,)))
    assert (
        plan_head_pass(facts(), BOXES, CAP.replacement, large, CAP.frame_grid).gaps[0].code
        == "replacement_unsupported"
    )


def test_application_never_continues_a_head_arm():
    from gaiavfx.application.replace_character import _plan, _request
    from gaiavfx.modules.direction.replacement import Attempt, CheckResult
    from gaiavfx.shared.errors import ApplicationError

    prior = (Attempt(planned().arm, ((CheckResult("residual_source", "fail", "none", 0.5),),)),)
    with pytest.raises(ValueError, match="one round"):
        _plan(facts(), replacement_policy_text(), prior, renders=1)
    with pytest.raises(ApplicationError) as exc:
        _request(
            {
                "source_asset": "film",
                "analysis_operation": "analysis",
                "performers": [{"track_id": "one", "target_asset": "target"}],
                "head": {"parent_asset": ["invalid"]},
            }
        )
    assert exc.value.status == 422
