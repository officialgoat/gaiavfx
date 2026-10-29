"""Render several candidates, durably yielding without selecting any take."""

import hashlib

from pydantic import Field, model_validator

from gaiavfx.application.harness.actions import ToolResult, pin_enum
from gaiavfx.application.harness.tools.common import (
    TransactionTool,
    operation_receipt,
    permitted,
    scoped,
)
from gaiavfx.application.harness.tools.plan_costs import afford_step, step_jobs_left
from gaiavfx.application.harness.tools.step_targets import target_ids, target_problem
from gaiavfx.application.harness.world import FINISHED, IN_FLIGHT, operations
from gaiavfx.application.render_cut import start_render
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


def reported_reviews(turns):
    return {
        r["id"]
        for t in turns
        if t["state"] == "accepted"
        for r in t["receipts"]
        if r.get("kind") == "review_set"
    }


def render_blocked(ops, read):
    return any(
        o["state"] in IN_FLIGHT or (o["state"] in FINISHED and o["id"] not in read) for o in ops
    )


class RenderCandidates(TransactionTool):
    name = "render_candidates"
    description = (
        "Render count takes (default 2) of required segment_id within budget. Never selects."
    )

    class Input(StrictModel):
        segment_id: str | None = None
        cut_id: str | None = None
        count: int = Field(default=2, ge=1, le=4, strict=True)
        seeds: list[int] | None = Field(default=None, min_length=1, max_length=4)

        @model_validator(mode="after")
        def one_target(self):
            if (self.segment_id is None) == (self.cut_id is None):
                raise ValueError("Exactly one of segment_id or cut_id is required.")
            return self

    def narrow(self, context):
        left = context.jobs_limit - context.jobs_reserved
        if not permitted(context, "render") or left <= 0:
            return None
        # Keep the established continuation-recovery path callable: it returns
        # the instruction to read and pick a predecessor. Every other refusal
        # removes the whole window, even when the selected cut alone is ready.
        ids = [
            segment["id"]
            for segment in context.state.segments
            if all(
                r["code"] == "continuation_source"
                for r in context.state.segment_plans[segment["id"]]["refusals"]
            )
            and not render_blocked(
                [
                    o
                    for o in context.state.operations
                    if o["kind"] == "render" and o["segment_id"] == segment["id"]
                ],
                context.state.reported_reviews,
            )
        ]
        targets = target_ids(context, self.name)
        if targets is not None:
            ids = targets
        if not ids:
            return None
        schema = self.Input.model_json_schema()
        schema = pin_enum(schema, "segment_id", ids)
        # The model grammar has one required target; Input retains the legacy
        # cut alias for direct callers, whose validator still enforces exclusivity.
        schema["properties"].pop("cut_id")
        # Seeds remain a direct Input/API feature; the agent requests a count.
        # Dispatch's closed-schema check refuses even valid explicit seed lists.
        schema["properties"].pop("seeds")
        schema.setdefault("required", []).append("segment_id")
        schema["properties"]["count"].update(maximum=min(4, left), default=min(2, left))
        # The input validator enforces exactly one target on every execution path.
        return schema

    def execute(self, session, context, value):
        if context.cancelled():
            raise ApplicationError("cancelled by the user", 409)
        if not permitted(context, "render"):
            raise ApplicationError("Rendering needs render autonomy.", 422)
        segment_id = value.segment_id
        if value.cut_id is not None:
            segment_id = scoped(context.state.cuts, value.cut_id, "Cut")["segment_id"]
        scoped(context.state.segments, segment_id, "Segment")
        if problem := target_problem(context, self.name):
            raise ApplicationError(problem, 422)
        seeds = render_seeds(context, value)
        afford_step(context, len(seeds))
        if context.jobs_reserved + len(seeds) > context.jobs_limit:
            raise ApplicationError(
                f"budget_exhausted: {context.jobs_reserved} reserved, limit {context.jobs_limit}, needed {len(seeds)} jobs.",
                422,
            )
        if len(set(seeds)) != len(seeds):
            raise ApplicationError("Render seeds must be distinct.", 422)
        recent = operations.recent(session, kind="render", scope={"segment_id": [segment_id]})
        read = reported_reviews(AgentStore().turns(session, context.session_id))
        if render_blocked(recent, read):
            raise ApplicationError(
                "A render is in flight or unread; use read_candidates after it finishes.", 409
            )
        try:
            operation = start_render(
                session,
                segment_id=segment_id,
                seeds=seeds,
                context_artifact=None,
                budget_limit=None,
                request_key=f"agent:{context.turn_id}",
            )
        except ApplicationError as exc:
            if "Select a take of segment" in str(exc):
                raise ApplicationError(
                    f"{exc} Read its candidates and use pick_provisional to continue.", exc.status
                ) from exc
            raise
        return ToolResult(
            payload={"operation_id": operation["id"], "count": len(seeds), "seeds": seeds},
            receipts=[operation_receipt(operation)],
            waits_on=operation["id"],
            changed=True,
        )


def render_seeds(context, value):
    """Stable per turn, distinct, and never coupled to blind review letters."""
    if value.seeds is not None:
        if "count" in value.model_fields_set and value.count != len(value.seeds):
            raise ApplicationError("count must match the number of explicit seeds.", 422)
        return value.seeds
    left = max(0, context.jobs_limit - context.jobs_reserved)
    step_left = step_jobs_left(context)
    if step_left is not None:
        left = min(left, step_left)
    count = value.count if "count" in value.model_fields_set else min(2, left)
    if not count:
        raise ApplicationError("budget_exhausted: No render jobs left.", 422)
    base = int(hashlib.sha256(f"{context.session_id}:{context.turn}".encode()).hexdigest()[:8], 16)
    return [(base + index) % (2**32) for index in range(count)]
