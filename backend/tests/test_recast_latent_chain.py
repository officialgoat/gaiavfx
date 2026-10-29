"""Bounded close-up chains, without GPU inference."""

import re
from types import SimpleNamespace

import pytest
from test_recast_batch7 import crop_planner, stored_shot
from test_recast_feed import long_shots, run, settle_all
from test_recast_service import recast_template as recast_template  # noqa: PLC0414
from test_recast_service import world as world  # noqa: PLC0414

from gaiavfx.application import recast, recast_feed, recast_planning, shot_assembly
from gaiavfx.application import recast_projection as p
from gaiavfx.application import replace_character as app
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.direction.replacement import LatentChainPolicy, parse_replacement_policy
from gaiavfx.modules.direction.windows import latent_chain_allowed, shot_windows

TEXT = replacement_policy_text()
OLD = re.sub(r"(?ms)^\[latent_chain\]\n.*?(?=^\[|\Z)", "", TEXT)


def close_up_preview(monkeypatch, flags=(True, True, True)):
    preview = app.replacement_plan

    def wrapped(session, request, **kwargs):
        result = preview(session, request, **kwargs)
        index = close_up_preview.index % len(flags)
        close_up_preview.index += 1
        return {**result, "facts": {"close_up": flags[index]}}

    close_up_preview.index = 0
    monkeypatch.setattr(app, "replacement_plan", wrapped)


def test_latent_chain_policy_optional_closed_and_evidenced():
    policy = parse_replacement_policy(TEXT).latent_chain
    assert isinstance(policy, LatentChainPolicy) and policy.max_windows == 4
    assert policy.evidence == "calibration/RECAST_BATCH7_2026-10-13.md"
    assert parse_replacement_policy(OLD).latent_chain is None
    for table in (
        "max_windows = 2",
        "max_windows = true",
        "max_windows = 4.0",
        'max_windows = 4\nevidence = "../x"\nnote = "test"',
        'max_windows = 4\nevidence = "docs/x"\nnote = "test"\nextra = 1',
    ):
        with pytest.raises(ValueError):
            parse_replacement_policy(OLD + "\n[latent_chain]\n" + table)


@pytest.mark.parametrize(
    "count,flags,performers,allowed",
    [
        (2, [True] * 2, 1, False),
        (3, [True] * 3, 1, True),
        (4, [True] * 4, 1, True),
        (5, [True] * 5, 1, False),
        (3, [True, False, True], 1, False),
        (3, [True] * 3, 2, False),
        (3, [True] * 2, 1, False),
    ],
)
def test_latent_chain_gate(count, flags, performers, allowed):
    assert latent_chain_allowed(count, flags, performers, 4) is allowed


@pytest.mark.parametrize(
    "count,flags,scale,old,kind",
    [
        (3, (True,), 0.45, False, "latent"),
        (4, (True,), 0.45, False, "latent"),
        (3, (False,), 0.45, False, "bridge"),
        (5, (True,), 0.45, False, "bridge"),
        (3, (True, False, True), 0.45, False, "bridge"),
        (3, (True,), 0.39, False, "bridge"),
        (3, (True,), None, False, "bridge"),
        (3, (True,), 0.45, True, "bridge"),
    ],
)
def test_chain_planning(world, monkeypatch, count, flags, scale, old, kind):
    crop_planner(world, monkeypatch, scale)
    close_up_preview(monkeypatch, flags)
    if old:
        monkeypatch.setattr(recast_planning, "replacement_policy_text", lambda: OLD)
    state = long_shots(world, length=73 + (count - 1) * 51)
    plan = stored_shot(world, state)["plan"]
    assert plan["_handoff"]["kind"] == kind
    if kind == "latent":
        assert plan["_handoff"]["reason"] == "close_up_chain"
        assert "close-up chain" in plan["_handoff"]["reasons"][0]
        assert plan["_handoff"]["crop"]["scale"] == scale
        assert plan["_handoff"]["joins"] == []
        assert recast_feed.bridge_reservation(plan, 2, mode="draft") == 0
    else:
        assert len(plan["_handoff"]["joins"]) == count - 1


@pytest.mark.parametrize("middle_fails", [False, True])
def test_three_window_feed_waits_for_each_predecessor(world, monkeypatch, middle_fails):
    close_up_preview(monkeypatch)
    state = run(world, long_shots(world), budget=6)
    assert state["estimate"]["renders"] == 6
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 1
    with world.db.transaction() as session:
        assert recast_feed.used(session, p.store.load(session, state["id"])) == 6
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 1
    settle_all(world, "character_replacement")
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 2
    world.invoke(recast.feed)
    assert len(world.calls["replacement"]) == 2
    assert not world.calls["assembly"]
    with world.db.transaction() as session:
        world.settle(
            session, world.calls["replacement"][-1], "failed" if middle_fails else "succeeded"
        )
    world.invoke(recast.feed)
    if middle_fails:
        assert len(world.calls["replacement"]) == 2
        with world.db.transaction() as session:
            assembly = world.ops.operation(session, world.calls["assembly"][-1])
        assert assembly["request"]["stopped"] == {"window": 1, "reason": "no_usable_take"}
    else:
        assert len(world.calls["replacement"]) == 3
        with world.db.transaction() as session:
            last = world.ops.operation(session, world.calls["replacement"][-1])
        assert last["request"]["handoff"]["predecessor_operation"] == world.calls["replacement"][1]
        settle_all(world, "character_replacement")
        world.invoke(recast.feed)
        assert len(world.calls["assembly"]) == 1
    assert not world.calls.get("bridge")


def test_chain_assembly_pins_both_predecessor_takes(monkeypatch):
    ops, takes = {}, {}
    for i, (start, end, _) in enumerate(shot_windows(0, 175)):
        key = f"op{i}"
        ops[key] = {
            "id": key,
            "kind": "character_replacement",
            "state": "succeeded",
            "request": {
                "facts": {"source_asset": "film", "shot_start": start, "shot_end": end},
                **(
                    {
                        "handoff": {
                            "predecessor_operation": f"op{i - 1}",
                            "candidate_id": f"c{i - 1}-1",
                        }
                    }
                    if i
                    else {}
                ),
            },
        }
        takes[key] = {
            t: {
                "asset": f"a{i}-{t}",
                "checks": [],
                "window": {"operation_id": key, "candidate_id": f"c{i}-{t}"},
            }
            for t in (0, 1)
        }
    monkeypatch.setattr(shot_assembly.operations, "operation", lambda s, key: ops[key])
    monkeypatch.setattr(shot_assembly.operations, "create", lambda s, **kw: kw)
    monkeypatch.setattr(shot_assembly, "window_takes", lambda s, lib, op: takes[op["id"]])
    library = SimpleNamespace(
        asset_info=lambda *args: {"sha256": "hash", "info": {"frame_rate": "24"}}
    )
    assembly = shot_assembly.start_shot_assembly(
        None,
        library,
        source_asset="film",
        shot_start=0,
        shot_end=175,
        window_operations=list(ops),
        request_key="chain",
        handoff={"kind": "latent", "joins": []},
    )
    versions = assembly["request"]["versions"]
    assert len(versions) == 2
    for version in versions:
        assert [w["candidate_id"] for w in version["windows"][:2]] == ["c0-1", "c1-1"]
        assert [j["handoff"] for j in version["joins"]] == ["latent", "latent"]


def test_close_up_chain_without_shot_crop_table_still_requires_shared_crop(world, monkeypatch):
    calls = crop_planner(world, monkeypatch, 0.1)
    close_up_preview(monkeypatch)
    text = re.sub(r"(?ms)^\[shot_crop\]\n.*?(?=^\[|\Z)", "", TEXT)
    monkeypatch.setattr(recast_planning, "replacement_policy_text", lambda: text)
    state = long_shots(world)
    plan = stored_shot(world, state)["plan"]
    assert plan["_handoff"]["kind"] == "latent"
    assert "shot_crop" not in plan["_handoff"]
    assert plan["canvas_scale"] == 0.1
    assert len(calls) == 1
    assert "close-up chain" in state["shots"][0]["plan"]["reasons"][0]


def test_continuation_checks_last_saved_latent_and_pins_first_snapshot(monkeypatch):
    from test_replacement_contract import mug_facts, plan

    from gaiavfx.application import shot_replacement as shot
    from gaiavfx.modules.direction.replacement import to_record
    from gaiavfx.shared.errors import ApplicationError

    crop = to_record(plan(mug_facts()).arm.crop)
    first = {
        "id": "w0",
        "request": {
            "save_latent": True,
            "crop_override": crop,
            "policy": "first-policy",
            "first_plan": {"arm": {"treatment": "rgb"}},
            "faceswap_override": True,
            "facts": {"close_up": True},
        },
    }
    middle = {"id": "w1", "request": {"save_latent": False}}
    monkeypatch.setattr(
        shot.operations, "operation", lambda s, key: {"w0": first, "w1": middle}[key]
    )
    monkeypatch.setattr(
        shot, "chain_take", lambda *args: {"window": {"candidate_id": "middle-take"}}
    )
    monkeypatch.setattr(
        shot, "start_replacement", lambda s, request, *args, **kwargs: (request, kwargs)
    )
    kwargs = {
        "source_asset": "film",
        "window_analyses": ["a0", "a1", "a2", "a3"],
        "window_operations": ["w0", "w1"],
        "performers": [{"tracks": {"a2": "p2"}, "target_asset": "target"}],
        "mode": "draft",
        "seed": 3,
        "request_key": "chain",
    }
    with pytest.raises(ApplicationError, match="Predecessor must save"):
        shot.continue_shot_replacement(None, None, **kwargs)
    middle["request"]["save_latent"] = True
    request, settings = shot.continue_shot_replacement(None, None, **kwargs)
    assert request.handoff == shot.HandoffChoice("w1", "middle-take")
    assert request.save_latent and request.faceswap_override and request.close_up
    assert to_record(request.crop_override) == crop and request.treatment_override == "rgb"
    assert settings["continuation_snapshot"] is first["request"]
    kwargs["window_analyses"] = ["a0", "a1", "a2"]
    request, _ = shot.continue_shot_replacement(None, None, **kwargs)
    assert not request.save_latent
