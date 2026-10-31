"""The Grant's job budget: what Gaia has reserved and what it may still reserve.

A receipt of an operation or a Recast run reserves jobs; a stopped build's settled receipt replaces what it reserved
(ADR 0020 §8). The session may reserve up to its limit: the job budget setting, or, in plan mode, what was reserved
when the user approved the plan plus the jobs its Grant gives. An executing plan's current prepare, render or build
step has its own allowance, the jobs it was planned with. Every tool asks here; none works out what is left itself.
"""

from dataclasses import dataclass

from gaiavfx.shared.errors import ApplicationError

SPENDING = {"operation", "recast_run"}
STEP_KINDS = {"prepare", "render", "build"}


def spent(receipts) -> int:
    """The jobs these receipts reserve: per operation, a settled receipt's spend, else the most any receipt held."""
    held, settled = {}, {}
    for receipt in receipts:
        if receipt.get("kind") not in SPENDING:
            continue
        if receipt.get("settled"):
            settled[receipt["id"]] = receipt.get("budget_reserved", 0)
        else:
            held[receipt["id"]] = max(held.get(receipt["id"], 0), receipt.get("budget_reserved", 0))
    return sum((held | settled).values())


def reserved(turns) -> int:
    """The jobs the session's accepted turns reserved."""
    return spent(r for turn in turns if turn["state"] == "accepted" for r in turn["receipts"])


def limit_of(document, settings) -> int:
    if document.plan_mode:
        plan = document.plan
        grant = plan.grant if plan and plan.state == "executing" else None
        return grant.reserved_at_approval + grant.budget_jobs if grant else 0
    return settings.get("agent_budget_jobs", 6)


@dataclass(frozen=True)
class Budget:
    """The session's job budget as a turn starts: its limit and what it has reserved."""

    limit: int = 6
    spent: int = 0

    @classmethod
    def of(cls, document, turns, settings):
        return cls(limit_of(document, settings), reserved(turns))

    @property
    def left(self) -> int:
        return max(0, self.limit - self.spent)


def _current_step(context):
    return context.document.executing_step() if context.document else None


def step_left(context) -> int | None:
    """The current step's allowance still unspent, or None outside an executing prepare, render or build step."""
    step = _current_step(context)
    if step is None or step.kind not in STEP_KINDS:
        return None
    return max(0, step.jobs - spent(step.receipts))


def left(context) -> int:
    """The jobs Gaia may reserve now: the session's, within the current step's allowance."""
    step = step_left(context)
    return context.budget.left if step is None else min(context.budget.left, step)


def afford_step(context, needed):
    """Refuse `needed` jobs the current step's allowance cannot pay for."""
    remaining = step_left(context)
    if remaining is not None and needed > remaining:
        raise ApplicationError(
            f"step_budget_exhausted: producer needs {needed} jobs, but step "
            f"'{_current_step(context).title}' has {remaining} jobs remaining. "
            "Revise the plan's step budget for user approval.",
            422,
        )


def afford(context, needed):
    """Refuse `needed` jobs the current step or the session cannot pay for."""
    afford_step(context, needed)
    if needed > context.budget.left:
        raise ApplicationError(
            f"budget_exhausted: {context.budget.spent} reserved, limit {context.budget.limit}, "
            f"needed {needed} jobs.",
            422,
        )
