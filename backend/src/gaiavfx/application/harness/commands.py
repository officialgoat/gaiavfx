"""Conversation commands and payloads for the durable Harness session."""

from gaiavfx.application.harness.scope import scope_state
from gaiavfx.application.harness.session import PLAN_JOBS_MAX, AgentSession
from gaiavfx.application.harness.summary import conversation_summary
from gaiavfx.application.harness.world import operation_view, operations, plate_survey_notes
from gaiavfx.shared.assistant import connection_for
from gaiavfx.shared.errors import ApplicationError

ACTION_ALLOWANCE = 2048
THINKING_ALLOWANCE = {"low": 6144, "medium": 12288, "xhigh": 12288}


def jobs_reserved(turns):
    # Deduplicate defensively: attaching or reading an operation never spends again. A stopped build's settled
    # receipt replaces what it reserved (ADR 0020 §8).
    receipts = {}
    for turn in turns:
        if turn["state"] != "accepted":
            continue
        for r in turn["receipts"]:
            if r.get("kind") in {"operation", "recast_run"} and not receipts.get(r["id"], {}).get(
                "settled"
            ):
                receipts[r["id"]] = r
    return sum(r["budget_reserved"] for r in receipts.values())


def frozen_settings(settings):
    frozen = {
        key: value
        for key, value in settings.items()
        if key.startswith("assistant_") or key == "comfy_url"
    }
    frozen["assistant_max_tokens"] = max(
        int(frozen.get("assistant_max_tokens") or 2400), ACTION_ALLOWANCE
    )
    thinking = frozen.get("assistant_agent_thinking") or "inherit"
    if thinking != "inherit":
        frozen["assistant_thinking"] = thinking
        frozen["assistant_max_tokens"] = max(
            frozen["assistant_max_tokens"], THINKING_ALLOWANCE.get(thinking, 0)
        )
    frozen["assistant_context"] = int(frozen.get("assistant_agent_context") or 32768)
    return frozen


class ConversationCommands:
    def _context_failure(self, session_id, document, error):
        """Persist the diagnostic and recovery state without assembling context again."""
        reason = ("Context assembly failed: " + str(error))[:1800]
        with self.db.transaction() as session:
            row = self.store.read_session(session, session_id)
            latest = AgentSession.model_validate(row["document"])
            if latest.board.revision > document.board.revision:
                document.board = latest.board
            document.cancel_requested = latest.cancel_requested
            document.stop_reason = reason[:400]
            self.store.create_turn(
                session,
                session_id,
                len(self.store.turns(session, session_id)),
                "system",
                "accepted",
                action={"message": reason + ". Post a message to retry."},
                result={"ok": False, "discrepancy": reason},
                provenance={"synthetic": True, "reason": "context assembly failed"},
            )
            return self.store.update_session(
                session,
                session_id,
                row["revision"],
                state="waiting_human",
                document=document.model_dump(),
            )

    def _require_current(self, session, row):
        rows = (
            self.store.scope_sessions(session, "film", row["scope_id"])
            if row["scope_kind"] == "film"
            else self.store.sessions(session, row["movie_id"])
        )
        latest = next(
            r
            for r in rows
            if (r["scope_kind"], r["scope_id"]) == (row["scope_kind"], row["scope_id"])
        )
        if latest["id"] != row["id"]:
            raise ApplicationError(
                "Earlier conversations are read-only. Open the current conversation.", 409
            )

    def new_conversation(self, session_id, expected_revision):
        with self._open_lock, self.db.transaction() as session:
            row, document = self._command_row(session, session_id, expected_revision)
            self._require_current(session, row)
            # Revoking the plan prevents the review driver from resuming history.
            if document.plan:
                from gaiavfx.application.harness.tools.plan import archive

                archive(document, "superseded")
                document.plan = None
            document.cancel_requested = True
            document.waiting_on = ""
            self._command_save(
                session,
                row,
                document,
                "stopped",
                "system",
                {"message": "The user closed this conversation and started a new one."},
            )
            fresh = AgentSession(
                scope_kind=row["scope_kind"],
                scope_id=row["scope_id"],
                settings=frozen_settings(self.settings()),
                summary=f"Continues earlier conversation {session_id}.",
            )
            return self.store.create_session(
                session,
                movie_id=row["movie_id"],
                scope_kind=row["scope_kind"],
                scope_id=row["scope_id"],
                document=fresh.model_dump(),
            )

    def stop_plan(self, session_id, expected_revision):
        from gaiavfx.application.harness.tools.plan import archive

        with self.db.transaction() as session:
            row, document = self._command_row(session, session_id, expected_revision)
            self._require_current(session, row)
            if not document.plan or document.plan.state != "executing":
                raise ApplicationError("There is no executing plan to stop.", 409)
            revision = document.plan.revision
            archive(document, "superseded")
            document.plan = None
            self._reset(document)
            document.waiting_on = ""
            return self._command_save(
                session,
                row,
                document,
                "idle",
                "system",
                {
                    # No raw plan id in the user's transcript (F6, 22 Oct trial); the view names the plan.
                    "message": f"The user stopped plan revision {revision}; it is superseded. Already submitted jobs remain in Jobs."
                },
            )

    def open(self, movie_id, scope_kind="movie", scope_id=""):
        if scope_kind == "movie":
            scope_id = scope_id or movie_id
        elif scope_kind == "film":
            # A film's conversation belongs to its recast; opening it creates no movie records.
            movie_id = ""
        with self._open_lock, self.db.transaction() as session:
            scope_state(session, movie_id, scope_kind, scope_id, "off", library=self.library)
            row = self.store.find_session(session, movie_id, scope_kind, scope_id)
            if row:
                return {**row, "_created": False}
            document = AgentSession(
                scope_kind=scope_kind, scope_id=scope_id, settings=frozen_settings(self.settings())
            )
            row = self.store.create_session(
                session,
                movie_id=movie_id,
                scope_kind=scope_kind,
                scope_id=scope_id,
                document=document.model_dump(),
            )
            return {**row, "_created": True}

    def read(self, session_id):
        with self.db.transaction() as session:
            return self.store.read_session(session, session_id)

    def turns(self, session_id):
        with self.db.transaction() as session:
            return self.store.turns(session, session_id)

    def sessions(self, movie_id):
        with self.db.transaction() as session:
            return self.store.sessions(session, movie_id)

    def scope_sessions(self, scope_kind, scope_id):
        with self.db.transaction() as session:
            return self.store.scope_sessions(session, scope_kind, scope_id)

    def payload(self, row):
        doc = AgentSession.model_validate(row["document"])
        document = doc.model_dump()
        autonomy, limit = self._authority(doc)
        turns = self.turns(row["id"])
        build = self.build_progress(doc)
        return {
            **{
                key: row[key]
                for key in ("id", "movie_id", "scope_kind", "scope_id", "state", "revision")
            },
            **{key: document[key] for key in ("board", "budgets", "stop_reason", "waiting_on")},
            "jobs_reserved": jobs_reserved(turns),
            "jobs_limit": limit,
            "autonomy": autonomy,
            **{key: document[key] for key in ("plan_mode", "plan", "plan_history", "summary")},
            "plan_jobs_max": PLAN_JOBS_MAX,
            "activity": "building_stage"
            if build and build["note"] and build["state"] == "building"
            else self.activity(row, doc, turns),
            "build": build,
        }

    def build_progress(self, document):
        """The plan's stage build in the user's words, for the chat (ADR 0020 §8): its build step, the newest build
        of that step, and, while the conversation waits on it, why the agent is quiet."""
        from gaiavfx.application.set_agent import build_state

        plan = document.plan
        steps = list(enumerate(plan.steps)) if plan else []
        for index, step in reversed(steps):
            ids = [
                r["id"]
                for r in step.receipts
                if r.get("kind") == "operation" and r.get("operation_kind") == "set_build"
            ]
            if step.kind != "build" or not ids:
                continue
            with self.db.transaction() as session:
                seed = build_state(session, operations.operation(session, ids[-1]))
            local = connection_for(document.settings)["provider"] == "managed_local"
            note = (
                "Gaia answers when the build ends"
                + (": Gaia's model and the build share the GPU." if local else ".")
                if document.waiting_on == ids[-1]
                else ""
            )
            return {**seed, "step": index, "note": note}
        return None

    def model_status(self):
        status = (
            self.model.status()
            if hasattr(self.model, "status")
            else {
                "provider": "",
                "connection_id": "",
                "model": "",
                "state": "unloaded",
                "since": "",
                "session_id": None,
            }
        )
        if status["state"] == "unloaded":
            connection = connection_for(self.settings())
            status.update(
                provider=connection["provider"],
                connection_id=connection["id"],
                model=connection["model"],
            )
        return {**status, "keep_warm_seconds": self.settings().get("agent_keep_warm_seconds", 180)}

    def activity(self, row, document, turns):
        if row["state"] == "running":
            status = self.model_status()
            if status["session_id"] == row.get("id") and status["state"] in {
                "waiting_gpu",
                "loading",
            }:
                return "waiting_gpu" if status["state"] == "waiting_gpu" else "loading_model"
            newest = next((t for t in reversed(turns) if t["role"] == "agent"), None)
            return "thinking" if newest and newest["state"] == "requested" else "running_tool"
        if row["state"] == "waiting_human" and document.plan and document.plan.state == "proposed":
            return "awaiting_approval"
        return row["state"]

    def _authority(self, document):
        if document.plan_mode:
            plan = document.plan
            grant = plan.grant if plan and plan.state == "executing" else None
            return "off", grant.reserved_at_approval + grant.budget_jobs if grant else 0
        settings = self.settings()
        return settings.get("agent_autonomy", "off"), settings.get("agent_budget_jobs", 6)

    @staticmethod
    def _reset(document):
        document.budgets.request_turns = 0
        document.previous_signature = document.previous_discrepancy = document.stop_reason = ""
        document.unchanged_turns = document.repeat_corrections = document.model_retries = 0
        document.cancel_requested = False

    def _summarize(self, session, session_id, document):
        turns = self.store.turns(session, session_id)
        ids = dict.fromkeys(
            r["id"]
            for t in turns
            if t["state"] == "accepted"
            for r in t["receipts"]
            if r.get("kind") == "operation"
        )
        row = self.store.read_session(session, session_id)
        state = scope_state(
            session, row["movie_id"], row["scope_kind"], row["scope_id"], "off", self.library
        )
        document.summary = conversation_summary(
            document,
            turns,
            [
                operation_view(session, operations.operation(session, operation_id))
                for operation_id in ids
            ],
            plate_survey_notes(state),
        )

    def state(self, session_id, after=-1):
        return {
            "session": self.payload(self.read(session_id)),
            "turns": [turn for turn in self.turns(session_id) if turn["ordinal"] > after],
        }

    def post_message(self, session_id, text, request_key, attachments=()):
        if not isinstance(text, str) or not text.strip():
            raise ApplicationError("Write a message for the agent.", 422)
        if (
            not isinstance(attachments, (list, tuple))
            or len(attachments) > 6
            or any(not isinstance(a, str) or not a or len(a) > 120 for a in attachments)
        ):
            raise ApplicationError(
                "Attach at most 6 library asset ids, each at most 120 characters.", 422
            )
        with (
            self.requests.reserve(
                request_key,
                "harness-message",
                {"session_id": session_id, "text": text, "attachments": list(attachments)},
            ) as turn_id,
            self.db.transaction() as session,
        ):
            row = self.store.read_session(session, session_id)
            turns = self.store.turns(session, session_id)
            self._require_current(session, row)
            if any(turn["id"] == turn_id for turn in turns):
                return row
            if row["state"] == "running":
                raise ApplicationError(
                    "The agent is still working. Wait for its turn to finish.", 409
                )
            document = AgentSession.model_validate(row["document"])
            attached = []
            for asset_id in attachments:
                try:
                    asset = self.library.asset_info(session, asset_id)
                except ApplicationError as exc:
                    if exc.status != 404:
                        raise
                    raise ApplicationError(
                        f"Attachment {asset_id} is not in the library", 422
                    ) from exc
                if asset["kind"] not in {"image", "video"}:
                    raise ApplicationError(f"Attachment {asset_id} must be an image or video", 422)
                attached.append(
                    {
                        "asset_id": asset_id,
                        "kind": asset["kind"],
                        "name": self.library.get(session, asset_id)["name"][:120],
                    }
                )
            self._reset(document)
            self._complete_reviews(session, row, document)
            turns = self.store.turns(session, session_id)
            action = {"message": text.strip(), "attachments": attached}
            if document.plan and document.plan.state == "proposed":
                self._revision_note(document, text.strip())
                action["plan_revision_of"] = document.plan.id
            self.store.create_turn(
                session,
                session_id,
                len(turns),
                "human",
                "accepted",
                action=action,
                turn_id=turn_id,
            )
            state = scope_state(
                session, row["movie_id"], row["scope_kind"], row["scope_id"], "off", self.library
            )
            notes = plate_survey_notes(state)
            if notes:
                self.store.create_turn(
                    session,
                    session_id,
                    len(self.store.turns(session, session_id)),
                    "system",
                    "accepted",
                    action={"message": " ".join(notes)},
                )
            self._summarize(session, session_id, document)
            return self.store.update_session(
                session,
                session_id,
                row["revision"],
                state="queued",
                document=document.model_dump(),
            )

    def edit_board(self, session_id, expected_revision, board):
        with self.db.transaction() as session:
            row = self.store.read_session(session, session_id)
            self._require_current(session, row)
            document = AgentSession.model_validate(row["document"])
            board = board.model_copy(deep=True)
            board.revision = document.board.revision + 1
            document.board = board
            return self.store.update_session(
                session,
                session_id,
                expected_revision,
                state=row["state"],
                document=document.model_dump(),
            )

    def request_cancel(self, session_id):
        with self.db.transaction() as session:
            row = self.store.read_session(session, session_id)
            document = AgentSession.model_validate(row["document"])
            document.cancel_requested = True
            return self.store.update_session(
                session,
                session_id,
                row["revision"],
                state=row["state"],
                document=document.model_dump(),
            )

    def cancelled(self, session_id):
        return self.stopping() or self.read(session_id)["document"]["cancel_requested"]
