"""Amendment 6 crop decisions, frozen compatibility and geometry; no inference."""

import re
from contextlib import nullcontext
from copy import deepcopy
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_recast_feed import long_shots, run, settle_all
from test_recast_service import recast_template as recast_template  # noqa: PLC0414
from test_recast_service import world as world  # noqa: PLC0414
from test_replacement_contract import CAP, attempt, mug_facts, result

from gaiavfx.application import recast, recast_feed, recast_planning
from gaiavfx.application import recast_projection as p
from gaiavfx.application import replace_character as app
from gaiavfx.application import shot_replacement as shot
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.direction.replacement import (
    Box,
    ShotCropPolicy,
    parse_replacement_policy,
    to_record,
    union_crop,
)
from gaiavfx.modules.direction.windows import bridge_layout, shot_windows

TEXT = replacement_policy_text()
WITHOUT_SHOT_CROP = re.sub(r"(?ms)^\[shot_crop\]\n.*?(?=^\[|\Z)", "", TEXT)
OLD_TEXT = (Path(__file__).parent / "fixtures/replacement/policy-before-batch5.toml").read_text()


def test_shot_crop_policy_is_optional_and_validated():
    policy = parse_replacement_policy(TEXT)
    assert isinstance(policy.shot_crop, ShotCropPolicy)
    assert policy.shot_crop.max_scale_loss == 0.2
    assert policy.shot_crop.evidence == "calibration/data/recast-batch6-rankings-2026-10-13.json"
    for text in (OLD_TEXT, WITHOUT_SHOT_CROP):
        assert parse_replacement_policy(text).shot_crop is None
    with pytest.raises(ValueError, match="invalid shot_crop policy"):
        replace(policy, shot_crop={"max_scale_loss": 0.2})


@pytest.mark.parametrize("value", ["-0.1", "1.1", "true", '"0.2"', "nan", "inf"])
def test_shot_crop_policy_refuses_invalid_loss(value):
    with pytest.raises(ValueError):
        parse_replacement_policy(TEXT.replace("max_scale_loss = 0.2", f"max_scale_loss = {value}"))


@pytest.mark.parametrize(
    "table",
    [
        'max_scale_loss = 0.2\nevidence = "docs/evidence.md"',
        'max_scale_loss = 0.2\nnote = "Evidence"',
        'evidence = "docs/evidence.md"\nnote = "Evidence"',
        'max_scale_loss = 0.2\nevidence = "docs/evidence.md"\nnote = "Evidence"\nunknown = 1',
        'max_scale_loss = 0.2\nevidence = "../evidence.md"\nnote = "Evidence"',
        'max_scale_loss = 0.2\nevidence = "docs/evidence.md"\nnote = ""',
    ],
)
def test_shot_crop_policy_has_closed_keys_and_requires_evidence(table):
    with pytest.raises(ValueError):
        parse_replacement_policy(WITHOUT_SHOT_CROP + "\n[shot_crop]\n" + table)


def crop_planner(world, monkeypatch, scale, window_scales=(0.5, 0.75, 1.0)):
    calls = []

    def shared(session, library, *, requests):
        analyses = [r["analysis_operation"] for r in requests]
        calls.append(analyses)
        if len(requests) > 2:
            if scale is None:
                raise ValueError("avoided region overlaps union")
            return {"fixture": "whole-shot", "scale": scale}
        return {"fixture": analyses, "scale": 0.45}

    def preview(session, request, **kwargs):
        start = world.window_reports[request["analysis_operation"]]["shot_start"]
        scale = window_scales[start // 51 % len(window_scales)]
        return {"gaps": [], "crop_preview": None if scale is None else {"scale": scale}}

    monkeypatch.setattr(world.window_api, "shared_crop_for_windows", shared)
    monkeypatch.setattr(app, "replacement_plan", preview)
    return calls


def stored_shot(world, state):
    with world.db.transaction() as session:
        return p.store.load(session, state["id"])["shots"][0]


@pytest.mark.parametrize("count", [3, 4, 7])
@pytest.mark.parametrize("scale", [0.4, 0.45])
def test_bridged_plan_uses_one_crop_and_reports_its_scale(world, monkeypatch, count, scale):
    calls = crop_planner(world, monkeypatch, scale)
    state = long_shots(world, length=73 + 51 * (count - 1))
    stored = stored_shot(world, state)
    plan = stored["plan"]
    handoff = plan["_handoff"]
    assert handoff["kind"] == "bridge"
    assert calls == [stored["window_analysis_ops"]]
    assert handoff["shot_crop"]["used"] is True
    assert handoff["shot_crop"]["scale_loss"] == pytest.approx(1 - scale / 0.5)
    assert len(handoff["joins"]) == count - 1
    assert all(j["crop"] == handoff["crop"] for j in handoff["joins"])
    assert state["shots"][0]["plan"]["canvas_scale"] == scale
    assert "Shot-wide crop used" in handoff["reasons"][0]
    assert "scale loss" in handoff["reasons"][0]
    assert "skip framing escalation" in handoff["reasons"][0]
    assert recast_feed.bridge_reservation(plan, 2, mode="draft") == count - 1
    assert recast_feed.bridge_reservation(plan, 4, mode="final") == count + 2


@pytest.mark.parametrize("scale,reason", [(0.399999, "scale_loss"), (None, "no_shared_crop")])
def test_rejected_shot_crop_keeps_each_join_and_records_why(world, monkeypatch, scale, reason):
    calls = crop_planner(world, monkeypatch, scale)
    state = long_shots(world)
    stored = stored_shot(world, state)
    analyses = stored["window_analysis_ops"]
    handoff = stored["plan"]["_handoff"]
    assert "crop" not in handoff
    assert handoff["shot_crop"]["used"] is False
    assert handoff["shot_crop"]["reason"] == reason
    assert calls == [analyses, analyses[:2], analyses[1:]]
    assert [j["crop"]["fixture"] for j in handoff["joins"]] == [analyses[:2], analyses[1:]]
    assert stored["plan"]["canvas_scale"] == 0.5
    assert "Shot-wide crop not used" in handoff["reasons"][0]
    if scale is not None:
        assert handoff["shot_crop"]["scale_loss"] > 0.2
        assert "exceeds 20.0%" in handoff["reasons"][0]
    else:
        assert "no shared crop fits" in handoff["reasons"][0]


@pytest.mark.parametrize(
    "scales",
    [(None, None, None), (0.5, None, 1.0), (0.5, float("nan"), 1.0), (0.5, 0, 1.0)],
    ids=["no-previews", "missing-preview", "invalid-scale", "zero-scale"],
)
def test_unavailable_window_scales_fall_back_without_breaking_refresh(world, monkeypatch, scales):
    calls = crop_planner(world, monkeypatch, 0.4, window_scales=scales)
    state = long_shots(world)
    stored = stored_shot(world, state)
    analyses = stored["window_analysis_ops"]
    plan = stored["plan"]
    handoff = plan["_handoff"]
    assert not plan["gaps"]
    assert handoff["shot_crop"] == {"used": False, "reason": "scale_unavailable"}
    assert "crop" not in handoff
    assert "scale loss unavailable" in handoff["reasons"][0]
    assert calls == [analyses[:2], analyses[1:]]
    assert [j["crop"]["fixture"] for j in handoff["joins"]] == [analyses[:2], analyses[1:]]


def test_invalid_shared_scale_falls_back_without_breaking_refresh(world, monkeypatch):
    calls = crop_planner(world, monkeypatch, float("nan"))
    state = long_shots(world)
    stored = stored_shot(world, state)
    analyses = stored["window_analysis_ops"]
    plan = stored["plan"]
    handoff = plan["_handoff"]
    assert not plan["gaps"]
    assert handoff["shot_crop"] == {"used": False, "reason": "scale_unavailable"}
    assert "crop" not in handoff
    assert calls == [analyses, analyses[:2], analyses[1:]]
    assert [j["crop"]["fixture"] for j in handoff["joins"]] == [analyses[:2], analyses[1:]]


@pytest.mark.parametrize("scale", [0.8 * 0.7, 0.56])
def test_shot_crop_decision_uses_recorded_loss_at_float_boundary(world, monkeypatch, scale):
    # Multiplying 0.7 by 0.8 rounds down: the resulting loss is just over 0.2.
    crop_planner(world, monkeypatch, scale, window_scales=(0.7, 0.75, 1.0))
    state = long_shots(world)
    handoff = stored_shot(world, state)["plan"]["_handoff"]
    decision = handoff["shot_crop"]
    assert decision["scale_loss"] == 1 - scale / 0.7
    assert decision["used"] is (decision["scale_loss"] <= 0.2)
    assert ("crop" in handoff) is decision["used"]
    if not decision["used"]:
        assert decision["reason"] == "scale_loss"


@pytest.mark.parametrize("text", [OLD_TEXT, WITHOUT_SHOT_CROP], ids=["pre-batch5", "no-table"])
def test_frozen_policy_without_shot_crop_keeps_exact_per_join_plan(world, monkeypatch, text):
    calls = crop_planner(world, monkeypatch, 0.5)
    monkeypatch.setattr(recast_planning, "replacement_policy_text", lambda: text)
    state = long_shots(world)
    stored = stored_shot(world, state)
    plan = stored["plan"]
    analyses = stored["window_analysis_ops"]
    assert calls == [analyses[:2], analyses[1:]]
    assert plan["canvas_scale"] == 0.5
    assert plan["_handoff"] == {
        "kind": "bridge",
        "joins": [
            {
                **layout,
                "generated": list(layout["generated"]),
                "kind": "bridge",
                "crop": {"fixture": analyses[i : i + 2], "scale": 0.45},
            }
            for i, layout in enumerate(bridge_layout(plan["windows"], 175))
        ],
    }


def test_two_window_latent_crop_ignores_shot_scale_limit(world, monkeypatch):
    calls = []
    crop = {"fixture": "latent", "scale": 0.1}

    def shared(session, library, *, requests):
        calls.append(requests)
        return crop

    monkeypatch.setattr(world.window_api, "shared_crop_for_windows", shared)
    state = long_shots(world, length=124)
    plan = stored_shot(world, state)["plan"]
    assert len(calls) == 1 and len(calls[0]) == 2
    assert plan["_handoff"] == {"kind": "latent", "joins": [], "crop": crop}
    assert plan["canvas_scale"] == 0.5  # Existing latent estimate is unchanged.


def test_started_old_shot_is_never_replanned_when_crop_policy_changes(world, monkeypatch):
    monkeypatch.setattr(recast_planning, "replacement_policy_text", lambda: WITHOUT_SHOT_CROP)
    state = run(world, long_shots(world), budget=8)
    world.invoke(recast.feed)
    before = deepcopy(stored_shot(world, state)["plan"])
    assert "crop" not in before["_handoff"]
    monkeypatch.setattr(recast_planning, "replacement_policy_text", lambda: TEXT)
    state = world.invoke(recast.recast_state, state["id"])
    assert stored_shot(world, state)["plan"] == before
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    assert stored_shot(world, state)["plan"] == before


@pytest.mark.parametrize("pinned", [False, True])
def test_fresh_window_requests_and_bridge_inputs_keep_the_planned_crop(monkeypatch, pinned):
    facts = mug_facts()
    policy = parse_replacement_policy(TEXT)
    windows = shot_windows(0, 175)
    all_facts = [
        replace(
            facts,
            shot_start=start,
            shot_end=end,
            required_context=(*facts.required_context, Box(950, 50, 980 + i * 5, 500)),
        )
        for i, (start, end, _) in enumerate(windows)
    ]
    crop = union_crop(all_facts, policy, CAP.replacement)
    reports = {
        str(i): {"source_asset": "source", "shot_start": start, "shot_end": end}
        for i, (start, end, _) in enumerate(windows)
    }
    monkeypatch.setattr(shot.operations, "operation", lambda session, key: reports[key])
    monkeypatch.setattr(shot, "_report", lambda session, report, library: report)
    seen, frozen, assets, neighbours = [], {}, {}, {}

    def initial(session, request, library, **kwargs):
        assert request.crop_override == (crop if pinned else None)
        first = app._plan(all_facts[0], TEXT, crop_override=request.crop_override)
        return all_facts[0], {}, TEXT, first

    def start(session, request, *args, **kwargs):
        seen.append(app._request(request))
        return {"id": f"w{len(seen) - 1}"}

    monkeypatch.setattr(shot, "_initial", initial)
    monkeypatch.setattr(shot, "start_replacement", start)
    handoff = {"kind": "bridge", "joins": []}
    if pinned:
        handoff["crop"] = to_record(crop)
    shot.start_shot_replacement(
        SimpleNamespace(begin_nested=nullcontext),
        None,
        source_asset="source",
        window_analyses=list(reports),
        performers=[{"tracks": {k: "actor" for k in reports}, "target_asset": "target"}],
        mode="final",
        seed=7,
        request_key="test",
        handoff=handoff,
    )
    assert len(seen) == 3
    for i, request in enumerate(seen):
        assert request.crop_override == (crop if pinned else None)
        assert request.save_latent is False and request.handoff is None and request.bridge is None
        arm = app._plan(all_facts[i], TEXT, crop_override=request.crop_override).arm
        retry = app._plan(
            all_facts[i],
            TEXT,
            (attempt(arm, (result("background_geometry"),)),),
            crop_override=request.crop_override,
        )
        assert retry.stop is pinned  # Pinned geometry cannot escalate to active framing.
        key = f"w{i}"
        frozen[key] = {
            "facts": to_record(all_facts[i]),
            "seed": 7,
            "analysis_operation": str(i),
            "tracks": [],
        }
        spec = {
            "crop": to_record(arm.crop),
            "arm": to_record(arm),
            "selected_tracks": ["actor"],
            "window_frames": 73,
        }
        neighbours[key] = (
            {"id": key, "request": frozen[key]},
            {"seed": 7, "snapshot": {"step_spec": spec}},
            {"outputs": [{"asset_id": key, "sha256": key}]},
        )
        assets[key] = {
            "id": key,
            "sha256": key,
            "kind": "video",
            "info": {
                "width": arm.crop.canvas[0],
                "height": arm.crop.canvas[1],
                "duration": 73 / 24,
                "frame_rate": "24",
            },
        }
    if not pinned:
        return
    monkeypatch.setattr(shot.operations, "operation", lambda session, key: {"request": frozen[key]})
    monkeypatch.setattr(app, "_chosen_window", lambda session, key, *args: neighbours[key])
    monkeypatch.setattr(app.MediaLibrary, "asset_info", lambda session, key: assets[key])
    for join in (1, 2):
        shot.start_shot_bridge(
            None,
            None,
            source_asset="source",
            predecessor={"window": {"operation_id": f"w{join - 1}", "candidate_id": "c0"}},
            successor={"window": {"operation_id": f"w{join}", "candidate_id": "c1"}},
            join=join,
            crop=to_record(crop),
            seed=7,
            request_key=f"bridge-{join}",
        )
        request = seen[-1]
        assert request.crop_override == crop and not request.save_latent
        _, bridge, _ = app._bridge_data(None, request.bridge, "source")
        assert all(w["crop"] == to_record(crop) for w in bridge["windows"])
