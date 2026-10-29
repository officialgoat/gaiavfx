"""Versioned session document; the store does not interpret it."""

from typing import Annotated, Literal

from pydantic import Field, model_validator

from gaiavfx.application.harness.board import Whiteboard
from gaiavfx.shared.contracts import StrictModel

PLAN_JOBS_MAX = 50
STEP_JOBS_MAX = 8
StepKind = Literal["edit", "prepare", "render", "pick", "review", "answer", "build"]


class PlanStep(StrictModel):
    kind: StepKind
    title: str = Field(min_length=1, max_length=200)
    target: str = Field(default="", max_length=120)
    # STEP_JOBS_MAX per step, except a recast's render step, which may reserve up to the plan's limit (checked when
    # the plan is proposed, `tools/plan.py`).
    jobs: int = Field(default=0, ge=0, le=PLAN_JOBS_MAX, strict=True)
    status: Literal["pending", "done", "skipped", "awaiting_user"] = "pending"
    receipts: list[dict] = Field(default_factory=list)
    review_of: str = Field(default="", max_length=120)


class PlanGrant(StrictModel):
    budget_jobs: int = Field(ge=0, le=PLAN_JOBS_MAX)
    step_kinds: list[StepKind]
    approved_turn: int = Field(ge=0)
    reserved_at_approval: int = Field(ge=0)


class Plan(StrictModel):
    id: str
    revision: int = Field(ge=1)
    state: Literal[
        "proposed", "approved", "executing", "done", "failed", "revised", "rejected", "superseded"
    ] = "proposed"
    goal: str = Field(min_length=1, max_length=600)
    steps: list[PlanStep] = Field(min_length=1, max_length=12)
    budget_jobs: int = Field(ge=0, le=PLAN_JOBS_MAX)
    questions: list[Annotated[str, Field(max_length=200)]] = Field(
        default_factory=list, max_length=3
    )
    note: str = Field(default="", max_length=1000)
    proposed_turn: int = Field(ge=0)
    grant: PlanGrant | None = None
    revision_requested: bool = False


class AgentBudgets(StrictModel):
    """Turn counts are the meaningful guard; token budgets are a runaway backstop.

    A turn costs at most one context window, so the token ceilings are sized above
    ``max_session_turns`` full turns. Sizing them below that made a session stop mid-message
    with "input token budget reached" for reasons unrelated to the work being done.
    """

    max_turns_per_message: int = Field(default=24, ge=1, le=64)
    max_session_turns: int = Field(default=400, ge=1, le=2000)
    max_input_tokens: int = Field(default=4_000_000, ge=4096)
    max_output_tokens: int = Field(default=500_000, ge=512)
    request_turns: int = Field(default=0, ge=0)
    turns_spent: int = Field(default=0, ge=0)
    input_tokens_spent: int = Field(default=0, ge=0)
    output_tokens_spent: int = Field(default=0, ge=0)


class AgentSession(StrictModel):
    version: Literal[1, 2] = 2
    scope_kind: Literal["movie", "segment", "cut", "film"] = "movie"
    scope_id: str = Field(default="", max_length=120)
    board: Whiteboard = Field(default_factory=Whiteboard)
    budgets: AgentBudgets = Field(default_factory=AgentBudgets)
    settings: dict = Field(default_factory=dict)
    stop_reason: str = Field(default="", max_length=400)
    previous_signature: str = Field(default="", max_length=128)
    previous_discrepancy: str = Field(default="", max_length=2000)
    unchanged_turns: int = Field(default=0, ge=0)
    repeat_corrections: int = Field(default=0, ge=0)
    waiting_on: str = ""
    cancel_requested: bool = False
    plan_mode: bool = True
    plan: Plan | None = None
    plan_history: list[Plan] = Field(default_factory=list, max_length=8)
    summary: str = Field(default="", max_length=1200)
    model_retries: int = Field(default=0, ge=0, le=1)
    focus: dict | None = None

    @model_validator(mode="after")
    def _film_always_plans(self):
        # ADR 0017 amendment 11: a film's document cannot represent plan mode off, so every
        # authority check (grant, catalog, prompt) sees plan mode without a scope test.
        if self.scope_kind == "film":
            self.plan_mode = True
        return self
