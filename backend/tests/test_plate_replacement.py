"""Human plate replacement retains history and invalidates S5 input bindings."""

import harness_support
import pytest
from harness_eval_tasks import run_idle

from gaiavfx.application.direction_plans import plan_for_cut
from gaiavfx.application.prepare_references import (
    approve_plate,
    assemble_sheet,
    current_sheet,
    open_plate_review,
    sheet_tiles_for_place,
    start_removal,
)
from gaiavfx.application.sheet_recipes import sheet_reference
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.shared.errors import ApplicationError

world = harness_support.world
direction = DirectionService(sheet_reader=sheet_reference)


def binding_map(service, session, cut):
    return {b.kind: b for b in plan_for_cut(session, cut["id"]).bindings}


def replacement_candidate(work, setup):
    with work.db.transaction() as session:
        op = start_removal(
            session,
            place_id=setup["place_id"],
            angle_id=setup["angles"][1],
            source_asset=setup["plate"],
            seeds=[73],
            wording={"whom": "Arin", "object_pronoun": "him", "behind": "the courtyard"},
        )
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        return open_plate_review(session, op["id"])["members"][0]["candidate_id"]


def test_human_replacement_receipt_undo_and_s5_staleness(world):
    work, setup = world
    with work.db.transaction() as session:
        old = direction.read_place(session, setup["place_id"])
        cut = direction.read_cut(session, setup["cut_id"])
        view = direction.record_evidence(session, cut["to_angle_id"], "imported", setup["plate"])
        view = direction.approve_evidence(session, view["id"])
        pin = direction.record_keyframe(session, cut["id"], setup["plate"], view["id"])
        pin = direction.approve_keyframe(session, pin["id"])
        assemble_sheet(
            session,
            place_id=old["id"],
            tiles=sheet_tiles_for_place(session, old["id"]),
            origin="survey",
        )
    run_idle(work, setup["fake"])
    with work.db.transaction() as session:
        sheet = current_sheet(session, old["id"])
        assert sheet and not any(b.stale for b in binding_map(direction, session, cut).values())
    candidate = replacement_candidate(work, setup)
    with work.db.transaction() as session:
        changed = approve_plate(session, candidate_id=candidate)
        assert changed["plate_asset_id"] != old["plate_asset_id"]
        assert changed["plate_angle_id"] == setup["angles"][1]
        receipt = direction.receipts(session, setup["movie_id"])[0]
        assert receipt["command"] == "replace_plate"
        assert receipt["before"]["plate"]["asset_id"] == old["plate_asset_id"]
        assert receipt["before"]["plate"]["angle_id"] == old["plate_angle_id"]
        assert receipt["after"]["plate"]["asset_id"] == changed["plate_asset_id"]
        assert approve_plate(session, candidate_id=candidate) == changed
        assert direction.receipts(session, setup["movie_id"])[0] == receipt
        bindings = binding_map(direction, session, cut)
        assert {k for k, b in bindings.items() if b.stale} == {"view", "pin", "sheet"}
        assert current_sheet(session, old["id"]) is None
        assert direction.read_evidence(session, view["id"]) == view
        assert direction.keyframes(session, cut["id"])[0] == pin
        # Both original media and original approvals survive; no silent rebind.
        assert work.library.asset_info(session, old["plate_asset_id"])
    with work.db.transaction() as session:
        inverse = direction.undo(session, receipt["id"])
        restored = direction.read_place(session, old["id"])
        assert restored["plate_asset_id"] == old["plate_asset_id"]
        assert restored["plate_angle_id"] == old["plate_angle_id"]
        assert restored["revision"] > changed["revision"]
        assert direction.read_receipt(session, receipt["id"])["undone_at"]
        assert not any(b.stale for b in binding_map(direction, session, cut).values())
        assert current_sheet(session, old["id"]) == sheet
        assert direction.read_evidence(session, view["id"]) == view
        assert direction.keyframes(session, cut["id"])[0] == pin
        direction.undo(session, inverse["id"])
        assert (
            direction.read_place(session, old["id"])["plate_asset_id"] == changed["plate_asset_id"]
        )
        assert all(b.stale for b in binding_map(direction, session, cut).values())


def test_replacement_rolls_back_and_undo_refuses_later_place_edit(world):
    work, setup = world
    candidate = replacement_candidate(work, setup)
    with work.db.transaction() as session:
        old = direction.read_place(session, setup["place_id"])
        receipts = direction.receipts(session, setup["movie_id"])
    with pytest.raises(RuntimeError), work.db.transaction() as session:
        approve_plate(session, candidate_id=candidate)
        raise RuntimeError("rollback human approval")
    with work.db.transaction() as session:
        assert direction.read_place(session, old["id"]) == old
        assert direction.receipts(session, setup["movie_id"]) == receipts
        changed = approve_plate(session, candidate_id=candidate)
        receipt = direction.receipts(session, setup["movie_id"])[0]
        direction.update_place(session, old["id"], changed["revision"], description="Later edit")
        with pytest.raises(ApplicationError, match="changed"):
            direction.undo(session, receipt["id"])
        assert not direction.read_receipt(session, receipt["id"])["undone_at"]
        assert (
            direction.read_place(session, old["id"])["plate_asset_id"] == changed["plate_asset_id"]
        )


def test_same_pixels_with_new_plate_angle_stales_approval_and_undo_restores(world):
    work, setup = world
    with work.db.transaction() as session:
        old = direction.read_place(session, setup["place_id"])
        view = direction.record_evidence(session, setup["angles"][2], "imported", setup["plate"])
        view = direction.approve_evidence(session, view["id"])
        changed = direction.approve_plate_replacement(
            session,
            old["id"],
            old["revision"],
            asset_id=old["plate_asset_id"],
            angle_id=setup["angles"][1],
            approved_by="user",
        )
        assert direction.reference_binding(session, "view", view["id"]).stale
        assert direction.read_evidence(session, view["id"]) == view
        direction.undo(session, changed["receipt"]["id"])
        assert not direction.reference_binding(session, "view", view["id"]).stale


@pytest.mark.parametrize("case", ["authority", "revision", "angle", "initial"])
def test_replacement_guards_leave_plate_and_receipts_unchanged(world, case):
    work, setup = world
    with work.db.transaction() as session:
        old = direction.read_place(session, setup["place_id"])
        args = {"asset_id": "replacement", "angle_id": setup["angles"][1], "approved_by": "user"}
        revision = old["revision"]
        message = {
            "authority": "Only user",
            "revision": "stale",
            "angle": "belong",
            "initial": "initial plate",
        }[case]
        if case == "authority":
            args["approved_by"] = "provisional"
        elif case == "revision":
            revision += 1
        else:
            other = direction.create_place(session, "Other")
            angle = direction.add_angle(session, other["id"], "Other")
            args["angle_id"] = angle["id"]
            if case == "initial":
                old, revision = other, other["revision"]
        receipts = direction.receipts(session, setup["movie_id"])
        with pytest.raises(ApplicationError, match=message):
            direction.approve_plate_replacement(session, old["id"], revision, **args)
        assert direction.read_place(session, old["id"]) == old
        assert direction.receipts(session, setup["movie_id"]) == receipts
