"""Authenticated agent sessions; production approvals remain on the production API."""

from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Response
from pydantic import Field, model_validator

from gaiavfx.application.harness.board import Whiteboard
from gaiavfx.application.harness.session import AgentBudgets, Plan, PlanGrant, PlanStep
from gaiavfx.shared.contracts import StrictModel
from gaiavfx.shared.errors import ApplicationError


class SessionInput(StrictModel):
    movie_id: str = Field(default="", max_length=120)
    scope_kind: Literal["movie", "segment", "cut", "film"] = "movie"
    scope_id: str = Field(default="", max_length=120)

    @model_validator(mode="after")
    def _scope(self):
        # A film's conversation is keyed to its recast and names no movie (ADR 0017 amendment 11).
        if self.scope_kind == "film":
            if not self.scope_id or self.movie_id:
                raise ValueError("A film conversation names its recast in scope_id and no movie.")
        elif not self.movie_id:
            raise ValueError("A movie, segment or cut conversation names its movie.")
        return self


class MessageInput(StrictModel):
    text: str = Field(min_length=1, max_length=8000)
    request_key: str = Field(min_length=1, max_length=120)
    attachments: list[Annotated[str, Field(min_length=1, max_length=120)]] = Field(
        default_factory=list, max_length=6
    )


class AgentPlanDecisionInput(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    note: str = Field(default="", max_length=1000)


class AgentConversationRevisionInput(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)


class AgentPlanReviseInput(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    note: str = Field(min_length=1, max_length=1000)


class AgentPlanModeInput(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    enabled: bool = Field(strict=True)


class AgentPlanStepDTO(PlanStep):
    pass


class AgentPlanGrantDTO(PlanGrant):
    pass


class AgentPlanDTO(Plan):
    steps: list[AgentPlanStepDTO] = Field(min_length=1, max_length=12)
    grant: AgentPlanGrantDTO | None = None


class BoardInput(StrictModel):
    expected_revision: int = Field(ge=1, strict=True)
    board: Whiteboard


class AgentBuildDTO(StrictModel):
    """The plan's stage build in Python's words, for the chat (ADR 0020 §8)."""

    state: Literal["building", "refused", "failed", "done"]
    name: str  # the stage it builds
    label: str  # a few words while it runs
    message: str  # a sentence
    operation_id: str
    set_id: str | None
    shot: int | None  # the take's shot it builds from; None when it fills more of a stage
    waiting: bool  # done, and its version waits for the user's decision
    can_retry: bool
    step: int  # the plan step it belongs to
    note: str  # why the agent is quiet while the conversation waits on it; empty otherwise


class HarnessSessionDTO(StrictModel):
    id: str
    movie_id: str
    scope_kind: str
    scope_id: str
    state: str
    revision: int
    board: Whiteboard
    budgets: AgentBudgets
    stop_reason: str
    waiting_on: str
    jobs_reserved: int
    jobs_limit: int
    autonomy: Literal["off", "prepare", "render"]
    plan_mode: bool
    plan: AgentPlanDTO | None
    plan_history: list[AgentPlanDTO]
    activity: str
    summary: str
    plan_jobs_max: int
    build: AgentBuildDTO | None = None


class TurnDTO(StrictModel):
    id: str
    ordinal: int
    role: str
    state: str
    action: dict | None
    result: dict | None
    receipts: list[dict]
    provenance: dict | None
    created_at: str
    updated_at: str


class SessionStateDTO(StrictModel):
    session: HarnessSessionDTO
    turns: list[TurnDTO]


class AgentModelStatusDTO(StrictModel):
    provider: str
    connection_id: str
    model: str
    state: Literal["unloaded", "waiting_gpu", "loading", "ready", "thinking"]
    since: str
    session_id: str | None
    keep_warm_seconds: int


def agent_router(harness, authenticate):
    router = APIRouter(prefix="/api/agent", dependencies=[Depends(authenticate)])

    @router.get("/model", response_model=AgentModelStatusDTO)
    def model_status():
        return harness.model_status()

    def state(session_id, after=-1):
        result = harness.state(session_id, after)
        result["turns"] = [
            {key: value for key, value in turn.items() if key != "session_id"}
            for turn in result["turns"]
        ]
        return result

    @router.post(
        "/sessions",
        response_model=HarnessSessionDTO,
        status_code=201,
        responses={200: {"model": HarnessSessionDTO, "description": "Existing session"}},
    )
    def open_session(body: SessionInput, response: Response):
        row = harness.open(**body.model_dump())
        response.status_code = 201 if row["_created"] else 200
        return harness.payload(row)

    @router.get("/sessions", response_model=list[HarnessSessionDTO])
    def sessions(movie_id: str | None = None, recast_id: str | None = None):
        # Exactly one key; an empty movie id would otherwise match every film's conversation.
        if bool(movie_id) == bool(recast_id):
            raise ApplicationError("List conversations by movie_id or by recast_id.", 422)
        rows = harness.sessions(movie_id) if movie_id else harness.scope_sessions("film", recast_id)
        return [harness.payload(row) for row in rows]

    @router.post("/sessions/{session_id}/new", response_model=HarnessSessionDTO, status_code=201)
    def new_conversation(session_id: str, body: AgentConversationRevisionInput):
        return harness.payload(harness.new_conversation(session_id, body.expected_revision))

    @router.post("/sessions/{session_id}/plan/stop", response_model=SessionStateDTO)
    def stop_plan(session_id: str, body: AgentConversationRevisionInput):
        harness.stop_plan(session_id, body.expected_revision)
        return state(session_id)

    @router.get("/sessions/{session_id}", response_model=SessionStateDTO)
    def get_session(session_id: str, after: int = -1):
        return state(session_id, after)

    @router.post("/sessions/{session_id}/messages", response_model=SessionStateDTO, status_code=202)
    def message(session_id: str, body: MessageInput):
        harness.post_message(session_id, body.text, body.request_key, body.attachments)
        return state(session_id)

    @router.put("/sessions/{session_id}/board", response_model=HarnessSessionDTO)
    def board(session_id: str, body: BoardInput):
        return harness.payload(harness.edit_board(session_id, body.expected_revision, body.board))

    @router.post("/sessions/{session_id}/cancel", response_model=HarnessSessionDTO)
    def cancel(session_id: str):
        return harness.payload(harness.request_cancel(session_id))

    @router.post(
        "/sessions/{session_id}/plan/approve", response_model=SessionStateDTO, status_code=202
    )
    def approve_plan(session_id: str, body: AgentPlanDecisionInput):
        harness.approve_plan(session_id, body.expected_revision)
        return state(session_id)

    @router.post(
        "/sessions/{session_id}/plan/revise", response_model=SessionStateDTO, status_code=202
    )
    def revise_plan(session_id: str, body: AgentPlanReviseInput):
        harness.revise_plan(session_id, body.expected_revision, body.note)
        return state(session_id)

    @router.post("/sessions/{session_id}/plan/reject", response_model=SessionStateDTO)
    def reject_plan(session_id: str, body: AgentPlanDecisionInput):
        harness.reject_plan(session_id, body.expected_revision, body.note)
        return state(session_id)

    @router.put("/sessions/{session_id}/plan-mode", response_model=HarnessSessionDTO)
    def plan_mode(session_id: str, body: AgentPlanModeInput):
        return harness.payload(
            harness.set_plan_mode(session_id, body.expected_revision, body.enabled)
        )

    return router
