"""Start exactly one missing producer, commit its receipt, and yield to the driver."""

import hashlib

from gaiavfx.application.harness.actions import ToolResult
from gaiavfx.application.harness.preparation import PREPARATION, VIEW_ACTIONS
from gaiavfx.application.harness.shots import shot_plan, shots
from gaiavfx.application.harness.tools.common import (
    TransactionTool,
    available,
    current_step,
    direction_receipt,
    operation_receipt,
    permitted,
    revision_guidance,
    scoped,
)
from gaiavfx.application.harness.tools.input_hints import (
    identity_input,
    insertion_input,
)
from gaiavfx.application.harness.tools.insertion import InsertionInput, resolve_insertion
from gaiavfx.application.harness.tools.plan_costs import afford_step
from gaiavfx.application.harness.tools.producers import EmptyPlate, RemovalPlate, start_views
from gaiavfx.application.harness.tools.retries import (
    preparation_operations,
    producer_report,
    require_crossing,
    require_revised,
)
from gaiavfx.application.harness.world import (
    FINISHED,
    IN_FLIGHT,
    direction,
    operations_for_shot,
)
from gaiavfx.application.prepare_references import (
    assemble_sheet,
    current_sheet,
    sheet_tiles_for_place,
    start_insertion,
    view_producer,
)
from gaiavfx.modules.direction.planning import Binding, binding_problem
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


def preparation_actions(plan):
    """Interpret excluded bindings without changing the plan or granting an approval."""
    choice = plan["recommended"]
    actions = {a["action"] for a in choice["actions"]}
    kinds = {"view"} if "view_angle" in actions else set()
    if "place_views" in choice["blocks"]:
        kinds.update({"view", "sheet"})
    if "pinned_frame" in choice["blocks"]:
        kinds.add("pin")
    excluded = [
        Binding(**binding)
        for binding in plan["bindings"]
        if binding["kind"] in kinds and (binding["stale"] or binding["suitability"] == "unsuitable")
    ]
    review = [
        binding_problem(binding)
        for binding in excluded
        if binding.kind != "sheet" or binding.suitability == "unsuitable"
    ]
    if not review and any(binding.kind == "sheet" for binding in excluded):
        # The plan remains unready. Its missing place views are restored by a new
        # assembly, only after every other prerequisite and every tile is valid.
        actions.discard("view_place")
    return actions, review


def require_reference_review(reasons):
    if reasons:
        raise ApplicationError(
            "reference_review: "
            + "; ".join(reasons)
            + ". Use say to direct the user to the Review workspace to re-approve stale references or reconsider suitability.",
            422,
        )


def needed_jobs(state, shot):
    plan = shot_plan(state, shot)
    choice = plan["recommended"]
    if not choice:
        return None
    actions, review = preparation_actions(plan)
    if review or actions & {"review_view", "review_keyframe"}:
        return None
    place = scoped(state.places, shot["place_id"], "Place")
    if actions & VIEW_ACTIONS:
        if not view_producer(place).plate:
            return 0
        return 1 if place["plate"] else 2
    if "insert_subject" in actions:
        return 2
    if not actions and "place_views" in choice["blocks"] and not state.sheets.get(shot["place_id"]):
        return 0
    return None


def preparation_cost(context, shot):
    needed = needed_jobs(context.state, shot)
    if needed is not None:
        plan = shot_plan(context.state, shot)["recommended"]
        place = scoped(context.state.places, shot["place_id"], "Place")
        # An asserted-empty source costs zero; execution checks removal's cost.
        if (
            view_producer(place).plate
            and any(a["action"] in VIEW_ACTIONS for a in plan["actions"])
            and (not place["plate"] or place["plate"]["angle_id"] != shot["plate_angle_id"])
            and any(a["kind"] == "image" for a in [*context.attachments, *context.state.assets])
        ):
            return 0
    return needed


def preparable(context, shot):
    needed = preparation_cost(context, shot)
    return (
        needed is not None
        and needed <= context.jobs_limit - context.jobs_reserved
        and not any(
            o["state"] in IN_FLIGHT or o["review_pending"]
            for o in operations_for_shot(context.state, shot)
        )
        and not any(
            a["action"] in {"review_view", "review_keyframe"}
            for a in shot_plan(context.state, shot)["recommended"]["actions"]
        )
    )


class PrepareCut(TransactionTool):
    name = "prepare_cut"
    description = "Prepare references, including free sheet assembly; wait for human reviews."

    class Input(StrictModel):
        cut_id: str
        seed: int | None = None
        plate: EmptyPlate | RemovalPlate | None = None
        insertion: InsertionInput | None = None

    def narrow(self, context):
        from gaiavfx.application.harness.tools.preparation_schema import prepare_schema

        return prepare_schema(self.Input, context)

    def execute(self, session, context, value):
        if context.cancelled():
            raise ApplicationError("cancelled by the user", 409)
        if not permitted(context, "prepare"):
            raise ApplicationError("Preparation autonomy is off.", 422)
        # A cut, or a segment's first shot (its id the segment's).
        shot = scoped(shots(context.state), value.cut_id, "Cut")
        from gaiavfx.application.direction_plans import (
            plan_for_cut,
            plan_for_opening,
            plan_for_segment,
            plan_to_dict,
        )
        from gaiavfx.application.harness.world import operation_view

        if value.plate and value.plate.angle_id not in {None, shot["plate_angle_id"]}:
            raise ApplicationError(
                f"plate_angle_mismatch: Use source angle {shot['plate_angle']} "
                f"({shot['plate_angle_id']}) of the {'cut' if shot['kind'] == 'cut' else 'first shot'}'s place, "
                "or omit plate.angle_id.",
                422,
            )
        plan = plan_to_dict(
            plan_for_cut(session, shot["id"])
            if shot["kind"] == "cut"
            else plan_for_opening(session, shot["segment_id"])
        )
        choice = plan["recommended"]
        if choice is None:
            raise ApplicationError(
                "coverage_unknown: "
                + (
                    "Declare the relation with edit_cut first. "
                    if available(context, "edit_cut")
                    else revision_guidance(context)
                )
                + "; ".join(g["message"] for g in plan["gaps"]),
                422,
            )
        place_id = shot["place_id"]
        sheet_missing = "place_views" in choice["blocks"] and not current_sheet(session, place_id)
        step = current_step(context)
        if step and step.kind == "render":
            # A stale model view must never turn free recovery into a producer or review.
            actions, review = preparation_actions(plan)
            if not sheet_missing or actions or review or value.plate or value.insertion:
                raise ApplicationError(
                    "Render-step preparation permits only free sheet assembly.", 422
                )
            render_plan = plan_to_dict(plan_for_segment(session, shot["segment_id"]))
            if any(
                r["code"] != "cut_binding" or "A views-only sheet is required" not in r["message"]
                for r in render_plan["refusals"]
            ):
                raise ApplicationError(
                    "Resolve the render prerequisites before free sheet assembly.", 422
                )
        if choice["ready"] and not sheet_missing:
            render = plan_to_dict(plan_for_segment(session, shot["segment_id"]))
            message = (
                "Nothing to prepare; the segment is ready to render."
                if render["ready"]
                else "References prepared; segment not ready: "
                + "; ".join(r["message"] for r in render["refusals"])
            )
            raise ApplicationError(message, 422)
        # Refresh public operation readers within the producer transaction: cached view
        # data must not permit duplicate work or step across an intervening review.
        relevant = preparation_operations(session, shot, place_id)
        if (
            step
            and step.kind == "render"
            and any(
                op["state"] in IN_FLIGHT or operation_view(session, op)["review_pending"]
                for op in relevant
            )
        ):
            raise ApplicationError("Free sheet assembly must wait for the reference reviews.", 422)
        for operation in relevant:
            if operation["state"] in IN_FLIGHT:
                return ToolResult(
                    payload={"attached": operation["id"]}, waits_on=operation["id"], progress=True
                )
        for operation in relevant:
            if (
                operation["state"] not in FINISHED
                or not operation_view(session, operation)["review_pending"]
            ):
                continue
            opener = PREPARATION[operation["kind"]].review
            if opener:
                review = opener(session, operation["id"])
                return ToolResult(
                    payload={
                        "operation_id": operation["id"],
                        "review": {"kind": review["purpose"], "review_set_id": review["id"]},
                        "message": "Pending reference review. End with say naming the reference and the Review workspace; do not ask for ranks in chat.",
                    },
                    receipts=[{"kind": "review_set", "id": review["id"]}],
                    progress=True,
                )
        seed = (
            value.seed
            if value.seed is not None
            else int(
                hashlib.sha256(f"{context.session_id}:{context.turn}".encode()).hexdigest()[:8], 16
            )
        )
        key = f"agent:{context.turn_id}"
        receipts = []

        def afford(needed):
            afford_step(context, needed)
            zero_job_plan_step = needed == 0
            if (
                context.jobs_reserved >= context.jobs_limit and not zero_job_plan_step
            ) or context.jobs_reserved + needed > context.jobs_limit:
                raise ApplicationError(
                    f"budget_exhausted: {context.jobs_reserved} reserved, "
                    f"limit {context.jobs_limit}, needed {needed} jobs.",
                    422,
                )

        actions, review = preparation_actions(plan)
        if value.insertion and "insert_subject" not in actions:
            raise ApplicationError(
                "Supply insertion when the reviewed target view is ready for its pin.", 422
            )
        require_reference_review(review)
        if actions & {"review_view", "review_keyframe"}:
            raise ApplicationError(
                "review_keyframe or review_view: Use say to name the pending reference and the Review workspace; the user reviews there.",
                422,
            )
        if actions & VIEW_ACTIONS:
            operation = start_views(
                session, context, value, shot, place_id, relevant, afford, seed, key
            )
        elif "insert_subject" in actions:
            # A pin belongs to a cut: a first shot's plan never asks for one.
            cut = direction.read_cut(session, shot["id"])
            require_crossing(session, context, relevant, shot)
            if not value.insertion and not cut["insertion"]:
                raise ApplicationError(
                    "insertion_wording_missing: Supply insertion inline in prepare_cut; it is recorded with a receipt under the prepare grant."
                    + insertion_input(session, context, cut),
                    422,
                )
            segment = direction.read_segment(session, cut["segment_id"])
            subject_ids = segment["content"]["subjects"]
            subjects = [direction.read_subject(session, sid) for sid in subject_ids]
            if len(subjects) != 1 or not subjects[0]["identity_asset_id"]:
                count = sum(bool(s["identity_asset_id"]) for s in subjects)
                raise ApplicationError(
                    f"insertion_needs_one_subject: {len(subjects)} subjects, {count} with identity assets."
                    + identity_input(context, subjects, segment),
                    422,
                )
            evidence = sorted(
                [e for e in direction.evidence(session, cut["to_angle_id"]) if e["approved"]],
                key=lambda row: (row["approved_at"], row["id"]),
            )
            if not evidence:
                raise ApplicationError("review_view: Approve a target view first.", 422)
            wording = resolve_insertion(
                session,
                context,
                cut,
                value.insertion.model_dump(exclude_none=True)
                if value.insertion
                else cut["insertion"],
            )
            require_revised(session, context, relevant, shot, "insert", {"wording": wording})
            afford(2)
            if value.insertion:
                cut = direction.update_cut(session, cut["id"], cut["revision"], insertion=wording)
                receipts.append(direction_receipt(cut))
            operation = start_insertion(
                session,
                cut_id=cut["id"],
                evidence_id=evidence[-1]["id"],
                subject_asset=subjects[0]["identity_asset_id"],
                wording=wording,
                seeds=[seed, seed + 1],
                budget_limit=None,
                request_key=key,
            )
        elif sheet_missing and not actions:
            afford(0)
            tiles = sheet_tiles_for_place(session, place_id)
            # A sheet can be stale through a view of another angle, which is not
            # among this cut's view bindings. Reassembling cannot refresh that view.
            require_reference_review(
                [
                    reason
                    for tile in tiles
                    if isinstance(tile, str)
                    and (
                        reason := binding_problem(
                            direction.reference_binding(session, "view", tile)
                        )
                    )
                ]
            )
            operation = assemble_sheet(
                session,
                place_id=place_id,
                tiles=tiles,
                origin=view_producer(direction.read_place(session, place_id)).sheet_origin,
                request_key=key,
            )
        else:
            raise ApplicationError(
                "Remaining action: " + (next(iter(actions), "nothing to prepare")), 422
            )
        receipt = operation_receipt(operation)
        return ToolResult(
            payload={
                "operation_id": operation["id"],
                "kind": operation["kind"],
                "seed": seed,
                **producer_report(session, relevant, shot, operation),
            },
            receipts=[*receipts, receipt],
            waits_on=operation["id"],
            changed=True,
        )
