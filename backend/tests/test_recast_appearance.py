"""Human appearance decisions, stale analysis, and non-authoritative suggestions."""

from copy import deepcopy

import numpy as np
import pytest
from test_recast_service import edit
from test_recast_service import recast_template as recast_template  # noqa: PLC0414
from test_recast_service import world as world  # noqa: PLC0414

from gaiavfx.application import recast, recast_appearance, recast_planning
from gaiavfx.application import recast_projection as p
from gaiavfx.shared.errors import ApplicationError


def faceless(world, *, second=True):
    state = world.ready()
    lead = state["characters"][0]["id"]
    if second:
        state = edit(world, state, recast.move_tracks, track_ids=["t2"], to="new")
        character = state["characters"][-1]["id"]
        state = edit(world, state, recast.set_target, character, asset_id="picture2", label="Celia")
    else:
        character = lead
    operation = world.calls["analysis"][0]
    window = world.window_reports[operation]
    track = deepcopy(window["tracks"][0])
    track.update(id="back", identity={"film_tracks": {}, "margin": None}, identity_margin=None)
    if second:
        window["tracks"].append(track)
    else:
        window["tracks"] = [track]
    world.touch_window(operation)
    state = world.invoke(recast.recast_state, state["id"])
    return state, lead, character, operation


def link_edit(world, state, command, character, operation, track="back", **kwargs):
    return edit(world, state, command, "s0001", operation, track, character, **kwargs)


def stored(world, state):
    with world.db.transaction() as session:
        return p.store.load(session, state["id"])


@pytest.mark.parametrize("second", [True, False], ids=["celia-beside-voted-man", "foreground-back"])
def test_confirmed_faceless_track_is_a_performer(world, second):
    state, lead, character, operation = faceless(world, second=second)
    before = deepcopy(state["shots"][0]["plan"])
    state = link_edit(
        world,
        state,
        recast.propose_appearance,
        character,
        operation,
        proposed_by="analysis",
        evidence={"continuity": 0.9},
    )
    assert state["shots"][0]["plan"] == before
    state = link_edit(world, state, recast.confirm_appearance, character, operation)
    plan = stored(world, state)["shots"][0]["plan"]
    assert not plan["gaps"]
    assert set(plan["performers"]) == ({lead, character} if second else {character})
    binding = next(b for b in plan["_bindings"] if b["character_id"] == character)
    assert binding["tracks"] == {operation: "back"}
    assert binding["selected_by"] == "user" and not binding.get("margins")
    preview = next(r for r in reversed(world.calls["plan"]) if r["analysis_operation"] == operation)
    assert (
        next(t for t in preview["performers"] if t["track_id"] == "back")["selected_by"] == "user"
    )
    from gaiavfx.application.replace_character import _facts, _request

    with world.db.transaction() as session:
        facts, _, _ = _facts(session, _request(preview), world.library, p.policy())
    assert not facts.bystanders and not facts.avoided_regions
    assert {performer.track_id for performer in facts.performers} == (
        {"person0", "back"} if second else {"back"}
    )
    assert next(a for a in state["shots"][0]["appearances"] if a["character_id"] == character)[
        "confirmed"
    ]
    if second:
        assert "joint" in plan["reasons"][0] and "FaceSwap" in plan["reasons"][0]
    link = state["shots"][0]["appearance_links"][0]
    assert link["state"] == "confirmed" and link["proposed_by"] == "analysis"
    assert link["evidence"] == {"continuity": 0.9} and link["decided_at"]


def test_rejection_removes_voted_owner_and_confirmed_wins_votes(world):
    state = world.ready()
    character = state["characters"][0]["id"]
    operation = world.calls["analysis"][0]
    state = link_edit(world, state, recast.reject_appearance, character, operation, "person0")
    assert "unbound_performer" in {g["code"] for g in state["shots"][0]["plan"]["gaps"]}
    state = link_edit(world, state, recast.confirm_appearance, character, operation, "person0")
    assert not state["shots"][0]["plan"]["gaps"]
    state = edit(world, state, recast.move_tracks, track_ids=["t1"], to="unknown")
    assert not state["shots"][0]["plan"]["gaps"]
    assert state["shots"][0]["plan"]["performers"] == [character]


def test_duplicate_linked_and_voted_tracks_stay_conflicted(world):
    state, lead, _, operation = faceless(world)
    state = link_edit(world, state, recast.confirm_appearance, lead, operation)
    assert "identity_conflict" in {g["code"] for g in state["shots"][0]["plan"]["gaps"]}


def test_decisions_are_inert_on_another_analysis_and_started_plans_are_frozen(world):
    state, _, character, operation = faceless(world, second=False)
    state = link_edit(world, state, recast.confirm_appearance, character, operation)
    with world.db.transaction() as session:
        row = p.store.load(session, state["id"])
        row["appearance_links"][0]["analysis_operation_id"] = "retired-analysis"
        p.store.save(session, row)
    state = world.invoke(recast.recast_state, state["id"])
    assert "unbound_performer" in {g["code"] for g in state["shots"][0]["plan"]["gaps"]}
    state = link_edit(world, state, recast.confirm_appearance, character, operation)
    state = edit(
        world, state, recast.start_run, mode="draft", range={"start": 0, "end": 73}, render_budget=2
    )
    world.invoke(recast.feed)
    before = deepcopy(stored(world, state)["shots"][0]["plan"])
    state = world.invoke(recast.recast_state, state["id"])
    state = link_edit(world, state, recast.reject_appearance, character, operation)
    assert stored(world, state)["shots"][0]["plan"] == before


def test_link_survives_merge_and_clear_restores_votes(world):
    state, lead, character, operation = faceless(world)
    state = link_edit(world, state, recast.confirm_appearance, character, operation)
    state = edit(world, state, recast.merge_characters, into=lead, sources=[character])
    assert state["shots"][0]["appearance_links"][0]["character_id"] == lead
    assert "identity_conflict" in {g["code"] for g in state["shots"][0]["plan"]["gaps"]}
    state = edit(world, state, recast.clear_appearance, "s0001", operation, "back")
    assert not state["shots"][0]["appearance_links"]
    assert not state["shots"][0]["plan"]["gaps"]


def test_a_shot_by_number_names_its_person_tracks_for_the_agent(world):
    """The film conversation's trial (22 Oct, F3): read_recast with a shot number gives the ids propose_appearance
    needs, with where each person stands and the current link; an unknown number is a 404."""
    state, _, character, operation = faceless(world)
    number = next(s for s in state["shots"] if s["id"] == "s0001")["index"] + 1
    shot = world.invoke(recast.shot_people, state, number)
    assert shot["id"] == "s0001" and shot["number"] == number
    [window] = [w for w in shot["windows"] if w["analysis_operation_id"] == operation]
    people = {person["track_id"]: person for person in window["people"]}
    assert window["finished"] and "back" in people
    assert people["back"]["position"] in {"left", "centre", "right"}
    assert 0 < people["back"]["present"] <= 1 and people["back"]["link"] is None
    state = link_edit(
        world,
        state,
        recast.propose_appearance,
        character,
        operation,
        proposed_by="agent",
        evidence={"note": "seen from behind"},
        user_message=character,
    )
    people = {
        person["track_id"]: person
        for w in world.invoke(recast.shot_people, state, number)["windows"]
        if w["analysis_operation_id"] == operation
        for person in w["people"]
    }
    assert people["back"]["link"] == {
        "character_id": character,
        "state": "proposed",
        "proposed_by": "agent",
    }
    with pytest.raises(ApplicationError) as missing:
        world.invoke(recast.shot_people, state, 999)
    assert missing.value.status == 404


@pytest.mark.parametrize(
    "command", [recast.confirm_appearance, recast.reject_appearance, recast.clear_appearance]
)
def test_appearance_decisions_are_human_only(world, command):
    state, _, character, operation = faceless(world, second=False)
    args = [] if command is recast.clear_appearance else [character]
    with pytest.raises(ApplicationError, match="Only a human"):
        edit(world, state, command, "s0001", operation, "back", *args, authority="agent")
    assert stored(world, state)["revision"] == state["revision"]


def test_proposal_guards_rejections_confirmations_and_revision(world):
    state, _, character, operation = faceless(world, second=False)
    with pytest.raises(ApplicationError, match="must name"):
        link_edit(
            world,
            state,
            recast.propose_appearance,
            character,
            operation,
            proposed_by="agent",
            evidence={"guess": character},
        )
    state = link_edit(
        world,
        state,
        recast.propose_appearance,
        character,
        operation,
        proposed_by="agent",
        evidence={"note": "seen from behind"},
        user_message=character,
    )
    with pytest.raises(ApplicationError, match="reload"):
        world.invoke(
            recast.confirm_appearance,
            state["id"],
            "s0001",
            operation,
            "back",
            character,
            expected_revision=state["revision"] - 1,
        )
    state = link_edit(world, state, recast.reject_appearance, character, operation)
    for proposed_by in ("agent", "analysis", "user"):
        with pytest.raises(ApplicationError, match="human appearance decision"):
            link_edit(
                world,
                state,
                recast.propose_appearance,
                character,
                operation,
                proposed_by=proposed_by,
                evidence={},
                user_message=character,
            )
    state = link_edit(world, state, recast.confirm_appearance, character, operation)
    with pytest.raises(ApplicationError, match="human appearance decision"):
        link_edit(
            world,
            state,
            recast.propose_appearance,
            character,
            operation,
            proposed_by="analysis",
            evidence={},
        )


@pytest.mark.parametrize("wrong", ["shot", "analysis", "track", "character", "object", "evidence"])
def test_link_validation_rolls_back(world, wrong):
    state, _, character, operation = faceless(world, second=False)
    if wrong == "object":
        world.window_reports[operation]["tracks"][0]["concept"] = "chair"
        world.touch_window(operation)
    with pytest.raises(ApplicationError):
        edit(
            world,
            state,
            recast.propose_appearance,
            "s0002" if wrong == "shot" else "s0001",
            "wrong" if wrong == "analysis" else operation,
            "wrong" if wrong == "track" else "back",
            "wrong" if wrong == "character" else character,
            proposed_by="analysis",
            evidence={"score": float("nan")} if wrong == "evidence" else {},
        )
    row = stored(world, state)
    assert row["revision"] == state["revision"] and not row["appearance_links"]


def suggestion_fixture():
    def track(name, character=None, hue=0):
        hist = np.zeros((12, 6))
        hist[hue, 5] = 1
        return {
            "id": name,
            "concept": "person",
            "mask_artifact": name,
            "presence": ["present"] * 73,
            "box": {"x0": 0, "y0": 0, "x1": 8, "y1": 8},
            "identity": {"film_tracks": {character: 3} if character else {}},
            "appearance": hist.tolist(),
        }

    rec = {
        "characters": [{"id": c, "cluster_id": c, "hidden": False} for c in ("a", "b")],
        "overrides": [],
        "appearance_links": [],
    }
    report = {
        "shots": [{"index": 1}],
        "clusters": [{"id": c, "track_ids": [c]} for c in ("a", "b")],
    }
    windows = {
        "s0001": [
            (
                "w0",
                {
                    "native": [8, 8],
                    "shot_start": 0,
                    "shot_end": 73,
                    "tracks": [track("voted-a", "a"), track("voted-b", "b", 4)],
                },
            ),
            (
                "w1",
                {"native": [8, 8], "shot_start": 51, "shot_end": 124, "tracks": [track("back")]},
            ),
        ]
    }
    masks = {name: np.zeros((73, 8, 8), bool) for name in ("voted-a", "voted-b", "back")}
    masks["voted-a"][:, :, :4] = masks["back"][:, :, :4] = True
    masks["voted-b"][:, :, 4:] = True
    return rec, report, windows, masks


@pytest.mark.parametrize("old", [False, True])
def test_suggestions_rank_overlap_and_colour_without_deciding(old):
    rec, report, windows, masks = suggestion_fixture()
    if old:
        for _, window in windows["s0001"]:
            for track in window["tracks"]:
                track.pop("appearance")
    proposals = recast_appearance.rank_proposals(
        rec, report, windows, "s0001", masks.__getitem__, p.policy()
    )
    assert len(proposals) == 1
    proposal = proposals[0]
    assert proposal["character_id"] == "a"
    candidates = proposal["evidence"]["candidates"]
    assert [c["character_id"] for c in candidates] == ["a", "b"]
    assert [c["continuity"] for c in candidates] == [1.0, 0.0]
    assert [c["appearance"] for c in candidates] == ([None, None] if old else [1.0, 0.0])
    assert not rec["appearance_links"]


def test_suggestions_skip_minor_decided_and_nonadjacent_tracks():
    rec, report, windows, masks = suggestion_fixture()
    back = windows["s0001"][1][1]["tracks"][0]
    back["box"]["x1"] = back["box"]["y1"] = 0.01
    assert not recast_appearance.rank_proposals(
        rec, report, windows, "s0001", masks.__getitem__, p.policy()
    )
    back["box"]["x1"] = back["box"]["y1"] = 8
    rec["appearance_links"] = [
        {
            "analysis_operation_id": "w1",
            "track_id": "back",
            "character_id": "a",
            "state": "rejected",
        }
    ]
    assert not recast_appearance.rank_proposals(
        rec, report, windows, "s0001", masks.__getitem__, p.policy()
    )
    rec["appearance_links"] = []
    windows["s0001"][1][1].update(shot_start=102, shot_end=175)
    proposals = recast_appearance.rank_proposals(
        rec, report, windows, "s0001", masks.__getitem__, p.policy()
    )
    assert proposals[0]["evidence"]["candidates"][0]["continuity"] is None


def test_suggestion_use_case_reads_masks_outside_writer_and_rechecks_revision(world, monkeypatch):
    from test_recast_feed import long_shots

    state = long_shots(world)
    operations = world.calls["analysis"]
    for i, operation in enumerate(operations):
        window = world.window_reports[operation]
        window["native"] = [8, 8]
        track = window["tracks"][0]
        track.update(mask_artifact=operation, box={"x0": 0, "y0": 0, "x1": 8, "y1": 8})
        if i == 1:
            track["identity"] = {"film_tracks": {}}
        world.touch_window(operation)
    locks, reads = [], []
    original = p.store.lock
    monkeypatch.setattr(p.store, "lock", lambda *a, **kw: locks.append(True) or original(*a, **kw))

    def masks(*args):
        assert not locks
        reads.append(True)
        return np.ones((73, 8, 8), bool)

    monkeypatch.setattr(recast_appearance, "_mask_data", masks)
    result = recast_appearance.suggest_appearances(
        world.db, world.library, state["id"], "s0001", expected_revision=state["revision"]
    )
    assert reads and locks
    link = result["shots"][0]["appearance_links"][0]
    assert link["state"] == "proposed" and link["proposed_by"] == "analysis"
    assert "unbound_performer" in {g["code"] for g in result["shots"][0]["plan"]["gaps"]}
    locks.clear()

    def stale_masks(*args):
        if not locks:
            with world.db.transaction() as session:
                current = p.store.load(session, state["id"])
                current["revision"] += 1
                p.store.save(session, current)
        return np.ones((73, 8, 8), bool)

    monkeypatch.setattr(recast_appearance, "_mask_data", stale_masks)
    with pytest.raises(ApplicationError, match="reload"):
        recast_appearance.suggest_appearances(
            world.db, world.library, state["id"], "s0001", expected_revision=result["revision"]
        )


def test_window_link_does_not_waive_identity_margin_in_other_windows(world):
    from test_recast_feed import long_shots

    state = long_shots(world, length=124)
    character = state["characters"][0]["id"]
    first, second = world.calls["analysis"]
    world.window_reports[first]["tracks"][0]["identity"] = {"film_tracks": {}}
    world.touch_window(first)
    state = link_edit(world, state, recast.confirm_appearance, character, first, "person0")
    binding = stored(world, state)["shots"][0]["plan"]["_bindings"][0]
    assert binding["selected_by"] == "identity"
    assert binding["selections"] == {first: "user"}
    assert set(binding["margins"]) == {second}
    previews = {r["analysis_operation"]: r for r in world.calls["plan"]}
    assert previews[first]["performers"][0]["selected_by"] == "user"
    assert previews[second]["performers"][0]["selected_by"] == "identity"


def test_confirmed_link_overrides_another_characters_face_votes(world):
    state, lead, character, operation = faceless(world)
    back = world.window_reports[operation]["tracks"][1]
    back["identity"] = {"film_tracks": {"t1": 50}, "margin": 0.9}
    world.touch_window(operation)
    state = link_edit(world, state, recast.confirm_appearance, character, operation)
    plan = stored(world, state)["shots"][0]["plan"]
    assert not plan["gaps"] and set(plan["performers"]) == {lead, character}


def test_linked_neighbour_is_continuity_evidence_but_proposed_neighbour_is_not():
    rec, report, windows, masks = suggestion_fixture()
    neighbour = windows["s0001"][0][1]["tracks"][0]
    neighbour["identity"] = {"film_tracks": {}}
    link = {
        "analysis_operation_id": "w0",
        "track_id": neighbour["id"],
        "character_id": "a",
        "state": "confirmed",
        "proposed_by": "analysis",
    }
    rec["appearance_links"] = [link]
    proposals = recast_appearance.rank_proposals(
        rec, report, windows, "s0001", masks.__getitem__, p.policy()
    )
    assert proposals[0]["evidence"]["candidates"][0]["continuity"] == 1
    link["state"] = "proposed"
    proposals = recast_appearance.rank_proposals(
        rec, report, windows, "s0001", masks.__getitem__, p.policy()
    )
    assert not any(row["track_id"] == "back" for row in proposals)


@pytest.mark.parametrize("linked", [False, True], ids=["voted", "confirmed-link"])
def test_suggestions_exclude_characters_already_owned_in_the_window(linked):
    rec, report, windows, masks = suggestion_fixture()
    previous, current = [window for _, window in windows["s0001"]]
    # Continuity favours A, but B has clothing evidence and remains eligible.
    current["tracks"][0]["appearance"] = deepcopy(previous["tracks"][1]["appearance"])
    owned = deepcopy(previous["tracks"][0])
    owned["id"] = "already-a"
    if linked:
        owned["identity"] = {"film_tracks": {}}
        rec["appearance_links"] = [
            {
                "analysis_operation_id": "w1",
                "track_id": owned["id"],
                "character_id": "a",
                "state": "confirmed",
                "proposed_by": "user",
            }
        ]
    current["tracks"].append(owned)
    proposals = recast_appearance.rank_proposals(
        rec, report, windows, "s0001", masks.__getitem__, p.policy()
    )
    assert len(proposals) == 1
    assert proposals[0]["track_id"] == "back" and proposals[0]["character_id"] == "b"
    assert {c["character_id"] for c in proposals[0]["evidence"]["candidates"]} == {"b"}


def test_rejected_majority_votes_remain_unknown_instead_of_promoting_stray_face():
    track = {"identity": {"film_tracks": {"YM": 10, "Celia": 1}}}
    owners = {"YM": "young-man", "Celia": "celia"}
    rejection = {"character_id": "young-man", "state": "rejected"}
    assert p.identity(track, owners) == ("young-man", None)
    assert p.identity(track, owners, rejection) == (None, "confirm_appearance")
    # The unknown bucket still observes the existing strict 2:1 rule.
    track["identity"]["film_tracks"]["YM"] = 2
    assert p.identity(track, owners, rejection) == ("celia", None)
    track["identity"]["film_tracks"]["unknown"] = 1
    assert p.identity(track, owners, rejection) == (None, "confirm_appearance")


@pytest.mark.parametrize("proposed_by", ["agent", "user", "analysis"])
def test_suggestions_preserve_pending_agent_and_user_proposals(proposed_by):
    rec, report, windows, masks = suggestion_fixture()
    rec["appearance_links"] = [
        {
            "analysis_operation_id": "w1",
            "track_id": "back",
            "character_id": "b",
            "state": "proposed",
            "proposed_by": proposed_by,
            "evidence": {"note": "Keep this proposal"},
        }
    ]
    before = deepcopy(rec)
    proposals = recast_appearance.rank_proposals(
        rec, report, windows, "s0001", masks.__getitem__, p.policy()
    )
    if proposed_by == "analysis":
        assert proposals[0]["character_id"] == "a"
    else:
        assert not proposals
    assert rec == before


@pytest.mark.parametrize("proposed_by", ["agent", "user"])
def test_analysis_proposal_endpoint_cannot_replace_pending_agent_or_user_link(world, proposed_by):
    state, _, character, operation = faceless(world, second=False)
    state = link_edit(
        world,
        state,
        recast.propose_appearance,
        character,
        operation,
        proposed_by=proposed_by,
        evidence={"note": "user-named"},
        user_message=character,
    )
    before = stored(world, state)
    with pytest.raises(ApplicationError, match="pending agent or user proposal"):
        link_edit(
            world,
            state,
            recast.propose_appearance,
            character,
            operation,
            proposed_by="analysis",
            evidence={"continuity": 1.0},
        )
    assert stored(world, state) == before


@pytest.mark.parametrize(
    "similarity,expected", [(None, False), (0, False), (0.6, False), (0.8, False), (0.81, True)]
)
def test_suggestions_require_positive_continuity_or_clothing_above_provisional_threshold(
    similarity, expected
):
    rec, report, windows, masks = suggestion_fixture()
    # All adjacent references are spatially disjoint from the unresolved person.
    for name, mask in masks.items():
        mask[:] = False
        if name == "back":
            mask[:, :, 6:8] = True
        else:
            mask[:, :, :2] = True
    back = windows["s0001"][1][1]["tracks"][0]
    if similarity is None:
        back.pop("appearance")
    else:
        hist = np.zeros((12, 6))
        hist[0, 5] = similarity**2
        hist[8, 5] = 1 - similarity**2
        back["appearance"] = hist.tolist()
    proposals = recast_appearance.rank_proposals(
        rec, report, windows, "s0001", masks.__getitem__, p.policy()
    )
    assert bool(proposals) is expected
    if expected:
        assert proposals[0]["character_id"] == "a"
        evidence = proposals[0]["evidence"]
        assert evidence["appearance_threshold"] == {"value": 0.8, "calibrated": False}
        assert evidence["candidates"][0]["continuity"] == 0
        assert evidence["candidates"][0]["appearance"] == pytest.approx(similarity)


@pytest.mark.parametrize("linked", [False, True], ids=["partly-voted", "partly-linked"])
def test_unbound_performer_advises_confirming_remaining_windows(world, linked):
    from test_recast_feed import long_shots

    state = long_shots(world, length=124)
    first, second = world.calls["analysis"]
    world.window_reports[second]["tracks"][0]["identity"] = {"film_tracks": {}}
    world.touch_window(second)
    if linked:
        world.window_reports[first]["tracks"][0]["identity"] = {"film_tracks": {}}
        world.touch_window(first)
        state = link_edit(
            world, state, recast.confirm_appearance, state["characters"][0]["id"], first, "person0"
        )
    else:
        state = world.invoke(recast.recast_state, state["id"])
    gap = next(g for g in state["shots"][0]["plan"]["gaps"] if g["code"] == "unbound_performer")
    assert "Confirm the character's appearance in the remaining windows" in gap["message"]
    assert "exclude this shot" in gap["message"]


def test_appearance_decisions_replan_only_their_own_shot(world, monkeypatch):
    state = world.ready()
    before = {s["shot_id"]: deepcopy(s["plan"]) for s in stored(world, state)["shots"]}
    computed = []
    compute = recast_planning._compute

    def record_compute(session, library, rec, report, shot, stored, policy):
        computed.append(stored["shot_id"])
        return compute(session, library, rec, report, shot, stored, policy)

    monkeypatch.setattr(recast_planning, "_compute", record_compute)
    for decision in (recast.confirm_appearance, recast.reject_appearance):
        computed.clear()
        state = link_edit(
            world,
            state,
            decision,
            state["characters"][0]["id"],
            world.calls["analysis"][0],
            "person0",
        )
        plans = {s["shot_id"]: s["plan"] for s in stored(world, state)["shots"]}
        assert computed == ["s0001"]
        assert plans["s0001"]["_fingerprint"] != before["s0001"]["_fingerprint"]
        assert {sid: plan for sid, plan in plans.items() if sid != "s0001"} == {
            sid: plan for sid, plan in before.items() if sid != "s0001"
        }
        before = deepcopy(plans)
