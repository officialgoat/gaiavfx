"""Human plan commands, authoritative review resumption and receipt linkage.

These methods share Harness's compare-and-set session saves; tools never invoke
the human commands. No transaction spans a model call or a durable wait.
"""

import logging
import time
from collections import Counter

from gaiavfx.application.harness.commands import jobs_reserved
from gaiavfx.application.harness.history import (
    attachments,
    latest_user_message,
    plate_rejections,
    recent_user_words,
    references_read,
)
from gaiavfx.application.harness.preparation import REVIEWED
from gaiavfx.application.harness.scope import scope_state
from gaiavfx.application.harness.session import AgentSession, PlanGrant
from gaiavfx.application.harness.tools.plan import archive, finish_plan
from gaiavfx.application.harness.tools.plan_scope import prepare_step_finished
from gaiavfx.application.harness.tools.preparation_progress import completion_refusal
from gaiavfx.application.harness.world import (
    FINISHED,
    direction,
    maybe_review,
    operations,
    plate_approved,
    review_pending,
    selections,
)
from gaiavfx.application.recast import recast_state
from gaiavfx.application.set_agent import build_outcome, build_state, settled
from gaiavfx.shared.errors import ApplicationError

FILM_PLANS = "A film's conversation always plans before it renders."
RECAST_CHECK_SECONDS = 30
RECAST_REVIEWING = {"needs_review", "queued", "rendering"}


class PlanCommands:
    def _command_row(self, session, session_id, expected_revision):
        row = self.store.read_session(session, session_id)
        if row["revision"] != expected_revision:
            raise ApplicationError("Agent session changed.", 409)
        if row["state"] == "running":
            raise ApplicationError("The agent is still working. Wait for its turn to finish.", 409)
        return row, AgentSession.model_validate(row["document"])

    @staticmethod
    def _proposed(document):
        if not document.plan_mode or not document.plan or document.plan.state != "proposed":
            raise ApplicationError("There is no proposed plan to decide.", 409)
        return document.plan

    @staticmethod
    def _revision_note(document, note):
        if not isinstance(note, str) or not note.strip() or len(note) > 1000:
            raise ApplicationError("Write a revision note of 1 to 1000 characters.", 422)
        document.plan.note = note.strip()
        document.plan.revision_requested = True

    def _command_save(self, session, row, document, state, role, action):
        self.store.create_turn(
            session,
            row["id"],
            len(self.store.turns(session, row["id"])),
            role,
            "accepted",
            action=action,
        )
        self._summarize(session, row["id"], document)
        return self.store.update_session(
            session, row["id"], row["revision"], state=state, document=document.model_dump()
        )

    def approve_plan(self, session_id, expected_revision):
        with self.db.transaction() as session:
            row, document = self._command_row(session, session_id, expected_revision)
            plan = self._proposed(document)
            if row["state"] != "waiting_human" or plan.revision_requested:
                raise ApplicationError("Wait for the proposed plan before approving it.", 409)
            turns = self.store.turns(session, session_id)
            plan.grant = PlanGrant(
                budget_jobs=plan.budget_jobs,
                step_kinds=list(dict.fromkeys(step.kind for step in plan.steps)),
                approved_turn=len(turns),
                reserved_at_approval=jobs_reserved(turns),
            )
            plan.state = "executing"
            self._reset(document)
            return self._command_save(
                session,
                row,
                document,
                "queued",
                "system",
                {
                    # The chat shows this line; a raw plan id means nothing to the user (the trial, 22 Oct, F6).
                    "message": f"The user approved the plan (revision {plan.revision}): budget {plan.budget_jobs} jobs, steps: {', '.join(plan.grant.step_kinds)}."
                },
            )

    def revise_plan(self, session_id, expected_revision, note):
        with self.db.transaction() as session:
            row, document = self._command_row(session, session_id, expected_revision)
            plan = self._proposed(document)
            self._revision_note(document, note)
            self._reset(document)
            return self._command_save(
                session,
                row,
                document,
                "queued",
                "human",
                {"message": note.strip(), "plan_revision_of": plan.id},
            )

    def reject_plan(self, session_id, expected_revision, note=""):
        if not isinstance(note, str) or len(note) > 1000:
            raise ApplicationError("Keep the note within 1000 characters.", 422)
        with self.db.transaction() as session:
            row, document = self._command_row(session, session_id, expected_revision)
            plan = self._proposed(document)
            plan.note = note.strip()
            archive(document, "rejected")
            document.plan = None
            document.stop_reason = ""
            return self._command_save(
                session,
                row,
                document,
                "idle",
                "system",
                {
                    "message": f"The user rejected plan revision {plan.revision}."
                    + (" " + note.strip() if note.strip() else "")
                },
            )

    def set_plan_mode(self, session_id, expected_revision, enabled):
        with self.db.transaction() as session:
            # ADR 0017 amendment 11: refused before the document is read or changed.
            if not enabled and self.store.read_session(session, session_id)["scope_kind"] == "film":
                raise ApplicationError(FILM_PLANS, 409)
            row, document = self._command_row(session, session_id, expected_revision)
            document.plan_mode = enabled
            if (
                not enabled
                and document.plan
                and document.plan.state in {"proposed", "approved", "executing"}
            ):
                plan = document.plan
                archive(document, "superseded")
                document.plan = None
                return self._command_save(
                    session,
                    row,
                    document,
                    row["state"],
                    "system",
                    {
                        "message": f"The user turned plan mode off; plan revision {plan.revision} is superseded."
                    },
                )
            return self.store.update_session(
                session,
                session_id,
                expected_revision,
                state=row["state"],
                document=document.model_dump(),
            )

    def _recast_reviewed(self, session, row, recast_id, span, throttle):
        """The user's review outcome once the run settled and no shot in the range the step's run
        started with ("start-end", or "" for the whole film) awaits review."""
        if self.library is None:
            return ""
        checks = self.recast_checks
        now = time.monotonic()
        last = checks.get(row["id"])
        # The driver polls every second; the recast read is expensive. Process-local on purpose:
        # recording it in the document would bump the session revision on every poll.
        if throttle and last is not None and now - last < RECAST_CHECK_SECONDS:
            return ""
        checks[row["id"]] = now
        try:
            with session.begin_nested():
                state = recast_state(session, self.library, recast_id, private_plans=True)
        except Exception:  # a broken or deleted recast keeps the step waiting; Stop plan remains
            logging.getLogger(__name__).exception("Recast %s review check skipped", recast_id)
            return ""
        run = state["run"]
        start, _, end = span.partition("-")
        bounds = (int(start), int(end)) if span else None
        shots = [
            s
            for s in state["shots"]
            if not bounds or (s["start"] < bounds[1] and s["end"] > bounds[0])
        ]
        # A paused run (budget spent or paused by the user) keeps the review waiting.
        if run["state"] not in {"idle", "finished"} or any(
            s["status"] in RECAST_REVIEWING for s in shots
        ):
            return ""
        counts = Counter(s["status"] for s in shots)
        return (
            f"The user reviewed recast {recast_id}: {counts['approved']} approved, "
            f"{counts['kept_original']} kept original, {counts['failed']} failed; continuing the plan."
        )

    def _complete_reviews(self, session, row, document, *, throttle=False):
        plan = document.plan
        if not document.plan_mode or not plan or plan.state != "executing":
            return False
        changed = False
        for step in plan.steps:
            if (
                step.kind not in {"prepare", "review", "build"}
                or step.status != "awaiting_user"
                or not step.review_of
            ):
                continue
            if step.review_of.startswith(("seed:", "build:")):
                outcome = build_outcome(
                    session, operations.operation(session, step.review_of.partition(":")[2])
                )
                if outcome["decided"] is None:
                    continue
                # A discarded version leaves nothing for the plan to build on (ADR 0020 §8).
                step.status = "done" if outcome["decided"] == "accepted" else "skipped"
                self.store.create_turn(
                    session,
                    row["id"],
                    len(self.store.turns(session, row["id"])),
                    "system",
                    "accepted",
                    action=outcome["action"],
                    receipts=outcome["receipts"],
                )
                changed = True
                continue
            if step.review_of.startswith("recast:"):
                recast_id, _, span = step.review_of.removeprefix("recast:").partition("@")
                message = self._recast_reviewed(session, row, recast_id, span, throttle)
                if not message:
                    continue
                step.status = "done"
                self.store.create_turn(
                    session,
                    row["id"],
                    len(self.store.turns(session, row["id"])),
                    "system",
                    "accepted",
                    action={"message": message, "review_recast_id": recast_id},
                )
                changed = True
                continue
            operation = operations.operation(session, step.review_of)
            if operation["state"] not in FINISHED or not operation["candidates"]:
                continue
            complete = not review_pending(session, operation)
            if operation["kind"] == "render":
                selection = selections.current(
                    session, row["movie_id"], operation["request"]["segment_id"]
                )
                # A previous selection must not approve a newly generated set of takes.
                complete |= bool(
                    selection
                    and selection["authority"] == "user"
                    and any(
                        candidate["asset_id"] == selection["asset_id"]
                        for candidate in operation["candidates"]
                    )
                )
            elif operation["kind"] not in REVIEWED:
                continue
            if not complete:
                continue
            # One prepare step can produce plate, views, pin and sheet. A decision
            # resumes that step; it does not finish a later render review.
            step.status = "pending" if step.kind == "prepare" else "done"
            message = review_outcome(session, row["movie_id"], operation)
            review = maybe_review(session, operation["id"])
            if step.kind == "prepare" and not (review and review.get("rejection")):
                turns = self.store.turns(session, row["id"])
                user_message = latest_user_message(turns)
                if prepare_step_finished(step, user_message, operation):
                    from types import SimpleNamespace

                    index = plan.steps.index(step)
                    following = next(
                        (s for s in plan.steps[index + 1 :] if s.status == "pending"), None
                    )
                    state = scope_state(
                        session,
                        row["movie_id"],
                        row["scope_kind"],
                        row["scope_id"],
                        "off",
                        self.library,
                    )
                    reason = completion_refusal(
                        SimpleNamespace(
                            state=state,
                            document=document,
                            attachments=attachments(turns),
                            latest_user_message=user_message,
                            reference_notes=references_read(turns),
                            recent_user_words=recent_user_words(turns),
                            plate_rejections=plate_rejections(session, state),
                        ),
                        index,
                    )
                    message += (
                        " " + reason
                        if reason
                        else (
                            f" Step {index} ({step.title}): the step's requested work is done: "
                            "mark it done with plan_step. "
                            + (
                                f"Next: {following.title}."
                                if following
                                else "Then report the result."
                            )
                        )
                    )
            self.store.create_turn(
                session,
                row["id"],
                len(self.store.turns(session, row["id"])),
                "system",
                "accepted",
                action={"message": message, "review_operation_id": operation["id"]},
            )
            changed = True
        if changed:
            finish_plan(document, len(self.store.turns(session, row["id"])))
        return changed

    def resume_reviewed(self, session_id):
        with self.db.transaction() as session:
            row = self.store.read_session(session, session_id)
            if row["state"] not in {"waiting_human", "idle", "stopped"}:
                return False
            document = AgentSession.model_validate(row["document"])
            if not self._complete_reviews(session, row, document, throttle=True):
                return False
            self._reset(document)
            self._summarize(session, session_id, document)
            self.store.update_session(
                session, session_id, row["revision"], state="queued", document=document.model_dump()
            )
            return True

    @staticmethod
    def _link_receipts(document, receipts):
        plan = document.plan
        if not plan or plan.state != "executing":
            return
        current = next((s for s in plan.steps if s.status in {"pending", "awaiting_user"}), None)
        if current:
            current.receipts.extend(receipts)

    def _link_step(self, document, result, tool, value):
        plan = document.plan
        if not plan or plan.state != "executing":
            return
        receipts = list(result.receipts)
        if result.waits_on and not any(r.get("id") == result.waits_on for r in receipts):
            # A tool that joined an operation already running (prepare's attach) started nothing: the step follows
            # it and spends nothing on it.
            receipts.append(
                {"kind": "operation", "id": result.waits_on, "budget_reserved": 0, "attached": True}
            )
        self._link_receipts(document, receipts)
        if tool.name == "start_recast_run":
            # The run's results wait in the recast's own review queue, not a Generation review
            # set: the render step's next review waits for the user there.
            producer = next(
                (s for s in plan.steps if s.status in {"pending", "awaiting_user"}), None
            )
            if producer and producer.kind == "render":
                start = plan.steps.index(producer) + 1
                review = next(
                    (s for s in plan.steps[start:] if s.kind == "review" and s.status == "pending"),
                    None,
                )
                if review:
                    # The run's own range goes with the step: a later run over other shots must not
                    # complete this review (Astra R-C). No range is the whole film.
                    span = f"@{value.range.start}-{value.range.end}" if value.range else ""
                    review.status = "awaiting_user"
                    review.review_of = f"recast:{value.recast_id}{span}"
            return
        if tool.name == "edit_place" and value.op in {"add_place", "add_angle"}:
            current = next((s for s in plan.steps if s.status == "pending"), None)
            if current:
                current.target = (result.payload.get("place") or result.payload["angle"])["id"]
        operation_id = ""
        if tool.name == "read_candidates":
            operation_id = value.operation_id
        elif tool.name == "prepare_cut" and result.payload.get("review"):
            operation_id = result.payload.get("operation_id", "")
        if not operation_id:
            return
        with self.db.transaction() as session:
            operation = operations.operation(session, operation_id)
            self._link_review(document, operation, session)

    @staticmethod
    def _link_review(document, operation, session):
        plan = document.plan
        if not plan or plan.state != "executing":
            return
        if operation["kind"] == "set_build":
            link_build(document, operation, session)
            return
        operation_id = operation["id"]
        reviewable = (
            operation["kind"] in REVIEWED | {"render", "character_replacement"}
            and operation["state"] in FINISHED
            and bool(operation["candidates"])
        )
        # The user may act before the reading turn. Bind the review even then;
        # the driver's authoritative check will complete it on its next poll.
        if reviewable and not any(s.review_of == operation_id for s in plan.steps):
            kind = (
                "render" if operation["kind"] in {"render", "character_replacement"} else "prepare"
            )
            producer = next(
                (
                    s
                    for s in plan.steps
                    if s.kind == kind and any(r.get("id") == operation_id for r in s.receipts)
                ),
                None,
            )
            if producer is None:
                return
            if kind == "prepare":
                step = producer
            else:
                start = plan.steps.index(producer) + 1
                step = next(
                    (s for s in plan.steps[start:] if s.kind == "review" and s.status == "pending"),
                    None,
                )
            if step:
                step.status, step.review_of = "awaiting_user", operation_id


def review_outcome(session, movie_id, operation):
    """Human results in blind letters, with editorial selection distinct from ranking."""
    from gaiavfx.application.harness.tools.report_truth import no_usable, no_usable_report

    review = maybe_review(session, operation["id"])
    if review and review.get("rejection"):
        kind = {"plate": "plate", "angle_views": "view", "insertion": "pin"}[review["purpose"]]
        return (
            f"The user rejected the {kind} candidates: '{review['rejection']['note']}'; "
            "prepare again with corrected inputs or ask"
        )
    if operation["kind"] == "render" and no_usable(review):
        return f"The user reviewed render {operation['id']}. " + no_usable_report(review)
    if operation["kind"] == "removal":
        if plate_approved(session, operation):
            return (
                f"The user reviewed removal {operation['id']}: plate review complete; "
                "the plate is approved; continue with the survey using prepare_cut."
            )
        return (
            f"Plate review {operation['id']} is superseded; inspect the current cut's preparation."
        )
    outcomes = []
    if review:
        letters = {m["candidate_id"]: m["letter"] for m in review["members"]}
        if review["ranking"]["tiers"]:
            outcomes.append(
                "ranked "
                + " > ".join(
                    " = ".join(letters[c] for c in tier) for tier in review["ranking"]["tiers"]
                )
            )
    if operation["kind"] == "render":
        segment_id = operation["request"]["segment_id"]
        segment = direction.read_segment(session, segment_id)
        selection = selections.current(session, movie_id, segment_id)
        member = (
            next(
                (
                    m
                    for m in review["members"]
                    if selection and m["asset_id"] == selection["asset_id"]
                ),
                None,
            )
            if review
            else None
        )
        if selection and selection["authority"] == "user" and member:
            outcomes.append(
                f"selected {member['letter']} for segment {segment['ordinal'] + 1} ({segment_id})"
            )
        else:
            outcomes.append(
                f"reviewed segment {segment['ordinal'] + 1}; selection pending. "
                "Selection may follow this ranking; use the current view refreshed before every model call"
            )
    elif operation["kind"] == "character_replacement":
        outcomes.append("reviewed replacement candidates; final choice remains the user's")
    else:
        outcomes.append(f"approved the required {operation['kind']} references")
    return (
        f"The user reviewed {operation['kind']} {operation['id']}. The user "
        + " and ".join(outcomes)
        + "; continuing the plan."
    )


def link_build(document, operation, session):
    """A build step's stop (ADR 0019 §7, 0020 §8): a built set waits for the user's decision on its v1, keyed on the
    seed (`seed:<operation>`), and an accepted v1 ends the step while a discarded one skips it; a refused build skips
    it, since nothing waits for a person; a stopped one leaves it pending, so the agent may build once more. Filling
    more of a stage (`build:<operation>`) stops the same way for the version it proposed."""
    step = next(
        (
            s
            for s in document.plan.steps
            if s.kind == "build"
            and s.status in {"pending", "awaiting_user"}
            and any(r.get("id") == operation["id"] for r in s.receipts)
        ),
        None,
    )
    if step is None:
        return
    built = build_state(session, operation)
    if built["state"] == "done":
        # The user may have decided before the wait ended.
        decided = build_outcome(session, operation)["decided"]
        step.status = (
            "awaiting_user" if decided is None else "done" if decided == "accepted" else "skipped"
        )
        mode = "seed" if operation["request"].get("mode") == "seed" else "build"
        step.review_of = f"{mode}:{operation['id']}"
    elif built["state"] == "refused":
        step.status = "skipped"
    elif built["state"] == "failed" and not any(
        r.get("settled") and r.get("id") == operation["id"] for r in step.receipts
    ):
        # The renders a stopped build never allocated return to its step.
        step.receipts.append(settled(operation))
