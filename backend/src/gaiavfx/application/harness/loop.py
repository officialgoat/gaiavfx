"""Durable one-action turns, with no transaction across model calls or waits."""

import hashlib
import json
import threading

from gaiavfx.application.harness import history
from gaiavfx.application.harness.actions import (
    ToolContext,
    ToolResult,
    action_schema,
    parse_action,
)
from gaiavfx.application.harness.commands import (
    ACTION_ALLOWANCE,  # noqa: F401 - compatibility for harness callers
    THINKING_ALLOWANCE,  # noqa: F401
    ConversationCommands,
    frozen_settings,  # noqa: F401
    jobs_reserved,
)
from gaiavfx.application.harness.context import (
    assemble,
    assemble_reading,
    call_with_window,
    turn_messages,
)
from gaiavfx.application.harness.knowledge import knowledge_head
from gaiavfx.application.harness.model import RETRYABLE_REPLY_PREFIXES
from gaiavfx.application.harness.plans import PlanCommands
from gaiavfx.application.harness.prompt import SYSTEM, system_prompt
from gaiavfx.application.harness.reading import (
    TEXT,
    load_parts,
    notes_schema,
    retain_notes,
    validate_notes,
)
from gaiavfx.application.harness.scope import (
    refresh_scope,
    scope_state,
    scope_view,
    state_label,
)
from gaiavfx.application.harness.session import AgentSession
from gaiavfx.application.harness.tools import DEFAULT_TOOLS, applicable_tools
from gaiavfx.application.harness.tools.common import operation_receipt
from gaiavfx.application.harness.tools.render import reported_reviews
from gaiavfx.application.harness.tools.replacement import completed_analysis
from gaiavfx.application.harness.tools.request_rules import turn_context
from gaiavfx.application.harness.world import FINISHED, LIST, operation_view, operations
from gaiavfx.infrastructure.database import new_id
from gaiavfx.infrastructure.requests import Requests
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.modules.library.media_application import MediaLibrary
from gaiavfx.shared.assistant import connection_for
from gaiavfx.shared.errors import ApplicationError

STALLED = 2
OPERATION_TERMINAL = {"succeeded", "partial", "failed", "cancelled", "needs_attention"}


def schema_billed(settings):
    try:
        connection = connection_for(settings, "agent")
    except ApplicationError:
        return False
    return (
        connection["provider"] != "managed_local"
        and connection.get("structured_output") == "json_schema"
    )


class Harness(ConversationCommands, PlanCommands):
    def __init__(
        self, db, store: AgentStore, model, settings, tools=DEFAULT_TOOLS, *, library=None
    ):
        self.db, self.store, self.model, self.settings = db, store, model, settings
        self.tools = tools
        self.library = library or MediaLibrary(db, None)
        self.requests = Requests(db)
        self._open_lock = threading.Lock()
        self.recast_checks = {}  # session id -> monotonic time of its last recast review check
        self.stopping = lambda: False

    def _adopt(self, session_id, document):
        latest = AgentSession.model_validate(self.read(session_id)["document"])
        if latest.board.revision > document.board.revision:
            document.board = latest.board
        document.cancel_requested = latest.cancel_requested

    def _save(self, session_id, state, document):
        with self.db.transaction() as session:
            row = self.store.read_session(session, session_id)
            latest = AgentSession.model_validate(row["document"])
            if latest.board.revision > document.board.revision:
                document.board = latest.board
            document.cancel_requested = latest.cancel_requested
            if state != "running":
                self._summarize(session, session_id, document)
            return self.store.update_session(
                session, session_id, row["revision"], state=state, document=document.model_dump()
            )

    def _turn(self, session_id, role, state, **kwargs):
        with self.db.transaction() as session:
            return self.store.create_turn(
                session,
                session_id,
                len(self.store.turns(session, session_id)),
                role,
                state,
                **kwargs,
            )

    def _update_turn(self, turn_id, state, *, session_id=None, document=None, **kwargs):
        with self.db.transaction() as session:
            turn = self.store.update_turn(session, turn_id, state, **kwargs)
            if document is not None:
                row = self.store.read_session(session, session_id)
                latest = AgentSession.model_validate(row["document"])
                if latest.board.revision > document.board.revision:
                    document.board = latest.board
                document.cancel_requested = latest.cancel_requested
                self.store.update_session(
                    session,
                    session_id,
                    row["revision"],
                    state="running",
                    document=document.model_dump(),
                )
            return turn

    def _reject_reply(self, session_id, turn_id, document, error):
        """Keep the rejection, retry marker and allowance atomic across recovery."""
        retry = document.model_retries == 0
        with self.db.transaction() as session:
            row = self.store.read_session(session, session_id)
            latest = AgentSession.model_validate(row["document"])
            if latest.board.revision > document.board.revision:
                document.board = latest.board
            document.cancel_requested = latest.cancel_requested
            self.store.update_turn(
                session, turn_id, "rejected", result={"ok": False, "discrepancy": str(error)[:2000]}
            )
            if retry:
                document.model_retries = 1
                self.store.create_turn(
                    session,
                    session_id,
                    len(self.store.turns(session, session_id)),
                    "system",
                    "accepted",
                    action={
                        "message": TEXT["retry"]
                        if document.focus
                        else "Your last reply was not one valid action. Answer with exactly one tool call."
                    },
                    provenance={"synthetic": True, "reason": "model reply retry"},
                )
            else:
                document.stop_reason = ("model call failed: " + str(error))[:400]
                self._summarize(session, session_id, document)
            return self.store.update_session(
                session,
                session_id,
                row["revision"],
                state="running" if retry else "failed",
                document=document.model_dump(),
            )

    def context(self, row, document, turns, turn_id=""):
        autonomy, limit = self._authority(document)
        reserved = jobs_reserved(turns)
        read = reported_reviews(turns)
        started = history.message_renders(turns)
        with self.db.transaction() as session:
            state = scope_state(
                session,
                row["movie_id"],
                row["scope_kind"],
                row["scope_id"],
                autonomy,
                library=self.library,
            )
            # Keep unread operations reachable even after their segment is removed.
            visible = {o["id"]: o for o in state.operations}
            owed = [
                visible[op_id]
                if op_id in visible
                else operation_view(session, operations.operation(session, op_id))
                for op_id in sorted(started - read)
            ]
            owed.sort(key=lambda o: o["state"] not in FINISHED)
            owed_ids = {o["id"] for o in owed}
            state.operations = [*owed, *(o for o in state.operations if o["id"] not in owed_ids)][
                :LIST
            ]
            rejections = history.plate_rejections(session, state)
            refresh_scope(session, state, history.attachments(turns), self.library, turns)
        state.reported_reviews = read
        return ToolContext(
            db=self.db,
            session_id=row["id"],
            movie_id=row["movie_id"],
            scope_kind=row["scope_kind"],
            scope_id=row["scope_id"],
            board=document.board,
            view=scope_view(state, autonomy, reserved, limit, document=document),
            turn=len(turns),
            turn_id=turn_id,
            state=state,
            settings={
                **document.settings,
                "agent_budget_jobs": self.settings().get("agent_budget_jobs", 6),
            },
            autonomy=autonomy,
            jobs_reserved=reserved,
            jobs_limit=limit,
            cancelled=lambda: self.cancelled(row["id"]),
            document=document,
            library=self.library,
            latest_user_message=history.latest_user_message(turns),
            **turn_context(turns, document),
            reference_notes=history.references_read(turns),
            recent_user_words=history.recent_user_words(turns),
            plate_rejections=rejections,
            report_turns=turns,
            unread_attachments=history.unread_attachments(turns),
            unread_renders=[o["id"] for o in owed if o["state"] in FINISHED],
            attachments=history.attachments(turns),
        )

    @staticmethod
    def _budget_stop(document):
        b = document.budgets
        for reached, reason in (
            (b.request_turns >= b.max_turns_per_message, "turn budget for this message reached"),
            (b.turns_spent >= b.max_session_turns, "session turn budget reached"),
            (b.input_tokens_spent >= b.max_input_tokens, "input token budget reached"),
            (b.output_tokens_spent >= b.max_output_tokens, "output token budget reached"),
        ):
            if reached:
                return reason
        return ""

    def _yield(self, session_id, document, reason, detail):
        self._turn(
            session_id,
            "system",
            "accepted",
            action={"message": f"{reason}: {detail} How should I proceed?"},
            provenance={"synthetic": True, "reason": reason},
        )
        document.stop_reason = reason
        return self._save(session_id, "waiting_human", document)

    def advance(self, session_id):
        row = self.read(session_id)
        if row["state"] != "running":
            raise ApplicationError("Claim the session before advancing it.", 409)
        document = AgentSession.model_validate(row["document"])
        turns = self.turns(session_id)
        orphans = [turn for turn in turns if turn["state"] == "requested"]
        if orphans:
            document.budgets.turns_spent = max(
                document.budgets.turns_spent, sum(t["role"] == "agent" for t in turns)
            )
            document.stop_reason = "interrupted before its turn was retained"
            with self.db.transaction() as session:
                for turn in orphans:
                    self.store.update_turn(
                        session,
                        turn["id"],
                        "failed",
                        result={
                            "ok": False,
                            "discrepancy": "Interrupted before the response was retained.",
                        },
                    )
                    operation = operations.for_request(session, f"agent:{turn['id']}")
                    if operation:
                        # Preserve committed spend without accepting an unknown response
                        # or running the interrupted producer again.
                        receipt = operation_receipt(operation)
                        self._link_receipts(document, [receipt])
                        self.store.create_turn(
                            session,
                            session_id,
                            len(self.store.turns(session, session_id)),
                            "system",
                            "accepted",
                            action={
                                "message": f"Recovered committed operation {operation['id']} from interrupted turn {turn['id']}."
                            },
                            receipts=[receipt],
                        )
                self._summarize(session, session_id, document)
                return self.store.update_session(
                    session,
                    session_id,
                    row["revision"],
                    state="failed",
                    document=document.model_dump(),
                )
        if not document.waiting_on:
            last = next((t for t in reversed(turns) if t["role"] in {"agent", "system"}), None)
            if last and last["state"] == "accepted":
                # Covers a crash after the accepted receipt commit but before the wait
                # state commit. The operation is already durable and must not be replayed.
                document.waiting_on = (last.get("result") or {}).get("waits_on", "")
        if document.waiting_on:
            with self.db.transaction() as session:
                operation = operations.operation(session, document.waiting_on)
                if operation["state"] in OPERATION_TERMINAL:
                    # Review remains a human boundary before any candidate reading.
                    self._link_review(document, operation, session)
                    # Resume marker and clearing waiting_on are atomic; restart cannot duplicate it.
                    ended = ended_turn(session, operation)
                    self.store.create_turn(
                        session,
                        session_id,
                        len(turns),
                        "system",
                        "accepted",
                        action=ended["action"],
                        receipts=ended.get("receipts", ()),
                        result=completed_analysis(session, operation, self.library),
                    )
                    document.waiting_on = ""
                    row = self.store.update_session(
                        session,
                        session_id,
                        row["revision"],
                        state="running",
                        document=document.model_dump(),
                    )
            if document.waiting_on and not self.cancelled(session_id):
                return self._save(session_id, "waiting_operation", document)
        while True:
            self._adopt(session_id, document)
            stop = (
                "cancelled by the user"
                if self.cancelled(session_id)
                else self._budget_stop(document)
            )
            if stop:
                document.stop_reason = stop
                return self._save(session_id, "stopped", document)
            turns = self.turns(session_id)
            turn_id = new_id()
            try:
                context = self.context(row, document, turns, turn_id)
                reading = document.focus
                if reading:
                    parts = load_parts(self.db.root, reading["parts"])
                    frames = sum(p["type"] == "image_url" for p in parts)
                    schema = notes_schema(reading["kind"], frames)
                    instruction = TEXT[reading["kind"]]
                    if document.model_retries:
                        instruction += " " + TEXT["retry"]
                    assembly = assemble_reading(
                        SYSTEM,
                        instruction,
                        reading["question"],
                        parts,
                        schema,
                        document.settings["assistant_context"],
                        document.settings["assistant_max_tokens"],
                        schema_billed=schema_billed(document.settings),
                        state=state_label(context, []).rsplit("/", 1)[0] + "/reading",
                    )
                    applicable = []
                else:
                    applicable = applicable_tools(context, self.tools)
                    schema = action_schema(applicable)
                    catalog = "\n".join(
                        f"{tool.name}: {tool.describe(context)}" for tool, _ in applicable
                    )
                    assembly = assemble(
                        system_prompt(context, applicable),
                        document.board.brief,
                        catalog,
                        schema,
                        document.board,
                        context.view,
                        turn_messages(turns),
                        document.settings["assistant_context"],
                        output=document.settings["assistant_max_tokens"],
                        schema_billed=schema_billed(document.settings),
                        state=state_label(context, applicable),
                        summary=document.summary,
                        knowledge=knowledge_head(context),
                    )
            except Exception as exc:  # noqa: BLE001 - retain assembly failures for recovery
                return self._context_failure(session_id, document, exc)
            # Persist both the requested turn and its charge before any external call.
            with self.db.transaction() as session:
                current = self.store.read_session(session, session_id)
                latest = AgentSession.model_validate(current["document"])
                if latest.board.revision > document.board.revision or latest.cancel_requested:
                    continue
                self.store.create_turn(
                    session,
                    session_id,
                    len(turns),
                    "agent",
                    "requested",
                    turn_id=turn_id,
                    provenance={
                        "context": assembly.report,
                        "tools": ["reference_notes"]
                        if reading
                        else [tool.name for tool, _ in applicable],
                        "jobs_limit": context.jobs_limit,
                        "jobs_reserved": context.jobs_reserved,
                    },
                )
                document.budgets.request_turns += 1
                document.budgets.turns_spent += 1
                self.store.update_session(
                    session,
                    session_id,
                    current["revision"],
                    state="running",
                    document=document.model_dump(),
                )
            try:
                log_path = self.db.root / "cache" / "assistant" / f"{session_id}-{len(turns)}.log"
                log_path.parent.mkdir(parents=True, exist_ok=True)
                reply = call_with_window(
                    self.model,
                    document.settings,
                    assembly,
                    schema,
                    context.cancelled,
                    log_path,
                    lambda report, turn_id=turn_id, applicable=applicable, context=context: (
                        self._update_turn(
                            turn_id,
                            "requested",
                            provenance={
                                "context": report,
                                "tools": [tool.name for tool, _ in applicable],
                                "jobs_limit": context.jobs_limit,
                                "jobs_reserved": context.jobs_reserved,
                            },
                        )
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - record provider failures, not process crashes
                if (
                    isinstance(exc, ApplicationError)
                    and str(exc).startswith(RETRYABLE_REPLY_PREFIXES)
                    and not context.cancelled()
                ):
                    rejected = self._reject_reply(session_id, turn_id, document, exc)
                    if rejected["state"] == "running":
                        continue
                    return rejected
                self._update_turn(
                    turn_id, "failed", result={"ok": False, "discrepancy": str(exc)[:2000]}
                )
                cancelled = context.cancelled()
                document.stop_reason = (
                    "cancelled by the user"
                    if cancelled
                    else ("model call failed: " + str(exc))[:400]
                )
                return self._save(session_id, "stopped" if cancelled else "failed", document)
            document.budgets.input_tokens_spent += reply.input_tokens
            document.budgets.output_tokens_spent += reply.output_tokens
            if reading:
                if context.cancelled():
                    self._update_turn(
                        turn_id,
                        "failed",
                        result={"ok": False, "discrepancy": "cancelled by the user"},
                    )
                    document.stop_reason = "cancelled by the user"
                    return self._save(session_id, "stopped", document)
                try:
                    notes = validate_notes(reply.raw, reading["kind"], frames)
                except ApplicationError as exc:
                    rejected = self._reject_reply(session_id, turn_id, document, exc)
                    if rejected["state"] == "running":
                        continue
                    return rejected
                self._adopt(session_id, document)
                retain_notes(document, notes, len(turns))
                document.unchanged_turns = 0
                self._update_turn(
                    turn_id,
                    "accepted",
                    session_id=session_id,
                    document=document,
                    action={"tool": "reference_notes", "input": notes},
                    result={"ok": True, "payload": notes},
                )
                continue
            tool, value = None, None
            try:
                self._adopt(session_id, document)
                context.board = document.board
                if context.cancelled():
                    raise ApplicationError("cancelled by the user", 409)
                context.autonomy, context.jobs_limit = self._authority(document)
                tools = applicable_tools(context, self.tools)
                tool, value = parse_action(reply.raw, tools, context)
                result = tool.run(context, value)
            except ApplicationError as exc:
                result = ToolResult(ok=False, discrepancy=str(exc)[:2000])
            raw = reply.raw if isinstance(reply.raw, dict) else {"malformed": str(reply.raw)[:1000]}
            signature = hashlib.sha256(
                json.dumps(
                    {key: raw.get(key) for key in ("tool", "input")}, sort_keys=True
                ).encode()
            ).hexdigest()
            repeated = signature == document.previous_signature
            document.previous_signature = signature
            novel = bool(result.discrepancy) and result.discrepancy != document.previous_discrepancy
            document.previous_discrepancy = result.discrepancy
            progressed = result.changed or novel or (result.progress and not repeated)
            document.unchanged_turns = 0 if progressed else document.unchanged_turns + 1
            if result.ok and tool:
                self._link_step(document, result, tool, value)
                if result.focus:
                    document.focus = result.focus
            # Retain plan progress with its turn, before either durable wait state.
            self._update_turn(
                turn_id,
                "accepted" if result.ok else "rejected",
                session_id=session_id,
                document=document,
                action=raw,
                result=result.model_dump(),
                receipts=result.receipts,
            )
            if result.ok and result.waits_on:
                document.waiting_on = result.waits_on
                return self._save(session_id, "waiting_operation", document)
            if result.ok and tool and tool.terminal:
                document.stop_reason = ""
                return self._save(
                    session_id,
                    "waiting_human" if tool.name in {"ask_human", "propose_plan"} else "idle",
                    document,
                )
            if repeated and not novel and tool is None:
                return self._yield(
                    session_id, document, "repeated invalid action", result.discrepancy
                )
            if repeated and not novel:
                if document.repeat_corrections >= 1:
                    return self._yield(
                        session_id,
                        document,
                        "repeated action",
                        result.discrepancy or "The result is already above.",
                    )
                document.repeat_corrections += 1
                self._turn(
                    session_id,
                    "system",
                    "accepted",
                    action={
                        "message": f"You already called {tool.name} with those arguments. Choose another action, say or ask_human."
                    },
                    provenance={"synthetic": True, "reason": "repeat correction"},
                )
            elif document.unchanged_turns >= STALLED:
                return self._yield(
                    session_id,
                    document,
                    "no state change",
                    result.discrepancy or "Two turns made no progress.",
                )
            self._save(session_id, "running", document)


def ended_turn(session, operation) -> dict:
    """The system turn when the operation the agent waited on ends: a stage build in the agent's own words, with the
    user's as the turn's notice and a receipt that keeps the stage in context (ADR 0020 §8)."""
    if operation["kind"] == "set_build":
        from gaiavfx.application.set_agent import build_outcome

        ended = build_outcome(session, operation)
        message = f"The stage build ended. {ended['action']['message']}"
        return {"action": ended["action"] | {"message": message}, "receipts": ended["receipts"]}
    message = f"Operation {operation['id']} ({operation['kind']}) ended {operation['state']}"
    return {"action": {"message": f"{message} with {len(operation['candidates'])} candidates."}}


def ended_words(session, operation) -> str:
    return ended_turn(session, operation)["action"]["message"]
