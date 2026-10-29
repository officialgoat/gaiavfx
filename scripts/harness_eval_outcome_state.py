"""Read-only live eval evidence and an audit around the runner's human decisions."""

import json

from gaiavfx.application.direction_plans import plan_for_cut, plan_to_dict
from gaiavfx.application.harness.world import maybe_review, review_pending
from gaiavfx.application.prepare_references import current_sheet
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.editing.selections.service import SelectionService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError


def read_state(work, setup):
    direction, selections = DirectionService(), SelectionService()
    with work.db.transaction() as session:
        movie = direction.read_movie(session, setup["movie_id"])
        ops = sorted(OperationService().recent(session), key=lambda o: o["created_at"])
        places = direction.places(session)
        angles = [a for p in places for a in direction.angles(session, p["id"])]
        evidence = [e for a in angles for e in direction.evidence(session, a["id"])]
        cuts = [c for s in movie["segments"] for c in s["cuts"]]
        pins = [k for c in cuts for k in direction.keyframes(session, c["id"])]
        history = [
            item
            for segment in movie["segments"]
            for item in selections.history(session, movie["id"], segment["id"])
        ]
        reviews = {o["id"]: maybe_review(session, o["id"]) for o in ops}
        valid = True
        try:
            for segment in movie["segments"]:
                direction.assemble_segment(session, segment["id"])
        except ApplicationError:
            valid = False
        return {
            "movie": movie,
            "operations": ops,
            "places": places,
            "evidence": evidence,
            "pins": pins,
            "selections": history,
            "reviews": reviews,
            "pending": [o["id"] for o in ops if review_pending(session, o)],
            "plans": {
                c["id"]: plan_to_dict(plan_for_cut(session, c["id"])) for c in cuts
            },
            "sheets": {p["id"]: current_sheet(session, p["id"]) for p in places},
            "structure_valid": valid,
        }


def authority(state):
    """Only decisions reserved to humans; opening an empty review is not a decision."""
    tokens = []
    for kind in ("evidence", "pins"):
        tokens += [
            [kind, item["id"], item.get("approved_by"), item.get("approved_at")]
            for item in state[kind]
            if item["approved"]
        ]
    tokens += [
        ["selection", s["id"], s["revision"], s["superseded_at"]]
        for s in state["selections"]
        if s["authority"] == "user"
    ]
    tokens += [
        ["plate", p["id"], p["plate_asset_id"]]
        for p in state["places"]
        if p["plate_asset_id"]
    ]
    tokens += [
        ["ranking", key, review["ranking"]]
        for key, review in state["reviews"].items()
        if review and review["ranking"]["tiers"]
    ]
    return {json.dumps(token, sort_keys=True) for token in tokens}


def audit(work, setup):
    if "outcome_initial" not in setup:
        return
    state = read_state(work, setup)
    changed = authority(state) ^ setup["human_authority"]
    setup.setdefault("authority_violations", []).extend(sorted(changed))


def human_decided(work, setup):
    """Called only after an explicit eval-runner approval, never after model work."""
    if "outcome_initial" in setup:
        setup["human_authority"] = authority(read_state(work, setup))


class _RollbackProbe(Exception):
    pass


def undo_probe(work, turns, movie_id):
    """Exercise public Undo on a transaction that is always rolled back."""
    receipts = [
        r for t in turns for r in t["receipts"] if r.get("kind") == "direction_receipt"
    ]
    if not receipts:
        return False
    service = DirectionService()
    try:
        with work.db.transaction() as session:
            for receipt in reversed(receipts):
                service.undo(session, receipt["id"])
            valid = not service.read_movie(session, movie_id)["segments"]
            raise _RollbackProbe
    except _RollbackProbe:
        return valid
    except ApplicationError:
        return False


def collect(work, setup, harness, recorder, *, check_undo=False):
    row = harness.read(setup["session_id"])
    turns = harness.turns(row["id"])
    receipts_valid = True
    with work.db.transaction() as session:
        for turn in turns:
            for receipt in turn["receipts"]:
                if receipt.get("kind") != "direction_receipt":
                    continue
                try:
                    DirectionService().read_receipt(session, receipt["id"])
                except ApplicationError:
                    receipts_valid = False
    return {
        "initial": setup["outcome_initial"],
        "final": read_state(work, setup),
        "row": row,
        "turns": turns,
        "calls": recorder.calls,
        "authority_violations": setup.get("authority_violations", []),
        "receipts_valid": receipts_valid,
        "undo_valid": undo_probe(work, turns, setup["movie_id"])
        if check_undo
        else None,
        "setup": {
            key: setup[key]
            for key in (
                "cut_id",
                "segment_id",
                "first_segment_id",
                "place_id",
                "angles",
                "identity",
                "plate",
                "take",
                "fixture_render",
                "approved_plate",
                "check_after",
                "before_approval",
                "before_turns",
                "before_review",
                "no_false_resume",
                "auto_resumed",
                "rejected",
                "waits",
            )
            if key in setup
        },
    }
