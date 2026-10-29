"""One claiming thread beside the OperationRunner.

The agent holds the lease only while running or parked. A parked model releases
within one poll of a job appearing; a running message is bounded by its turn
budget or cancellation. A session starting a job yields waiting_operation and
releases before waiting for that job (which may already be waiting at its lease).
Nothing waits on the agent while holding the lease. Maintenance also precedes
claims, so a stream of chat messages cannot starve an already queued render.
"""

import logging
import threading

from gaiavfx.application.harness.loop import OPERATION_TERMINAL
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.assistant import connection_for
from gaiavfx.shared.errors import ApplicationError

logger = logging.getLogger(__name__)


class AgentRunner:
    def __init__(
        self,
        db,
        harness,
        poll_seconds=1.0,
        stop=None,
        *,
        gpu_wanted=lambda: False,
        keep_warm_seconds=None,
    ):
        self.db, self.harness = db, harness
        self.poll_seconds, self.stop = poll_seconds, stop or threading.Event()
        self.harness.stopping = self.stop.is_set
        self.gpu_wanted = gpu_wanted
        self.keep_warm_seconds = keep_warm_seconds

    def release(self):
        self.harness.model.end()

    def maintain(self):
        wanted = bool(self.gpu_wanted()) if not self.stop.is_set() else False
        seconds = self.keep_warm_seconds
        if seconds is None:
            seconds = self.harness.settings().get("agent_keep_warm_seconds", 180)
        parked_session = getattr(self.harness.model, "session_id", None)
        if parked_session and self.harness.cancelled(parked_session):
            self.release()
        if hasattr(self.harness.model, "maintain"):
            self.harness.model.maintain(
                gpu_wanted=wanted, keep_warm_seconds=seconds, stopping=self.stop.is_set()
            )
        return wanted

    def recover(self):
        self.release()
        with self.db.transaction() as session:
            for row in self.harness.store.in_states(session, {"running"}):
                self.harness.store.update_session(
                    session, row["id"], row["revision"], state="queued", document=row["document"]
                )

    def run_once(self):
        wanted = self.maintain()
        if self.stop.is_set():
            self.release()
            return False
        with self.db.transaction() as session:
            waiting = self.harness.store.in_states(session, {"waiting_human", "idle", "stopped"})
        for row in waiting:
            plan = row["document"].get("plan")
            if (
                plan
                and plan["state"] == "executing"
                and any(s["status"] == "awaiting_user" for s in plan["steps"])
            ):
                try:
                    self.harness.resume_reviewed(row["id"])
                except ApplicationError as exc:
                    if exc.status != 409:
                        raise
        with self.db.transaction() as session:
            rows = self.harness.store.claimable(session)
        for row in rows:
            if self.stop.is_set():
                return False
            if (
                wanted
                and not row["document"].get("cancel_requested")
                and connection_for(row["document"]["settings"])["provider"] == "managed_local"
            ):
                continue
            with self.db.transaction() as session:
                if row["document"].get("waiting_on") and not row["document"].get(
                    "cancel_requested"
                ):
                    operation = OperationService().operation(session, row["document"]["waiting_on"])
                    if operation["state"] not in OPERATION_TERMINAL:
                        continue
                claimed = self.harness.store.claim_session(session, row["id"], {row["state"]})
            if claimed is None:
                continue

            def cancelled(session_id=row["id"]):
                return self.stop.is_set() or self.harness.cancelled(session_id)

            try:
                # Orphans and cancellations need no loaded model to fail closed.
                if not cancelled() and not any(
                    t["state"] == "requested" for t in self.harness.turns(row["id"])
                ):
                    if hasattr(self.harness.model, "session_id"):
                        self.harness.model.session_id = row["id"]
                    self.harness.model.begin(claimed["document"]["settings"], cancelled)
                self.harness.advance(row["id"])
            except Exception as exc:
                from gaiavfx.application.harness.session import AgentSession

                document = AgentSession.model_validate(self.harness.read(row["id"])["document"])
                document.stop_reason = "cancelled by the user" if cancelled() else str(exc)[:400]
                self.harness._save(row["id"], "stopped" if cancelled() else "failed", document)
                logger.exception("Agent session %s failed", row["id"])
            finally:
                state = self.harness.read(row["id"])["state"]
                if self.stop.is_set() or cancelled() or state == "waiting_operation":
                    self.release()
                elif hasattr(self.harness.model, "park"):
                    self.harness.model.park()
                    self.maintain()
                else:
                    self.release()
            return True
        return False

    def run_forever(self):
        try:
            self.recover()
            while not self.stop.is_set():
                if not self.run_once():
                    self.stop.wait(self.poll_seconds)
        finally:
            self.release()
