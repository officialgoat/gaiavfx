"""Movie-scope conversations: the outcome family on the DZ courtyard, and the run it shares with the replacement and
quality families.

The eval plays the user through the harness's public commands: it posts the task's message, drives the agent through
durable waits on the fake producers, approves or rejects a proposed plan, completes the reviews a human would, and
records each human decision, so the authority audit can tell the agent's changes from the user's.
"""

import hashlib
from contextlib import nullcontext
from copy import deepcopy
from unittest.mock import patch

from gaiavfx.application.harness.driver import AgentRunner
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.prepare_references import (
    approve_pin,
    approve_view,
    open_insertion_review,
    open_views_review,
)
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.service import OperationService
from harness_eval_family import Control, Family
from harness_eval_fixtures import (
    FIXTURE_DEFAULTS,
    MEDIA,
    authored,
    close,
    dz_library,
    prepare_fixture,
    prepare_segment_fixture,
    uncut_second_segment,
)
from harness_eval_hooks import ConcurrentCutEdit
from harness_eval_outcome_state import (
    audit,
    authority,
    collect,
    human_decided,
    read_state,
)

# Tasks whose evidence exercises Undo of the agent's direction receipts (always rolled back).
UNDO_CHECKED = {
    "drafts-structure-from-brief",
    "narrative-reverse-storyline",
    "draft-storyline-under-plan",
}


def movie_spec(task):
    """The DZ courtyard contract: the task, its budgets and fixture switches, the authored parity inputs and media."""
    result = {
        "id": task.id,
        "question": task.question,
        "message": task.message,
        "budgets": {
            "max_turns": task.max_turns,
            "jobs": task.fixture.get("budget", 6),
        },
        "autonomy": task.fixture.get("autonomy", "prepare"),
        "fixture": FIXTURE_DEFAULTS | task.fixture,
        "criteria": task.criteria,
        "expected_tools": task.expected_tools,
        "follow_up": task.follow_up,
        "authored": {name: authored(name) for name in ("DZ", "TC", "SV-C2B", "IN-DZ")},
        "media": {
            name: hashlib.sha256((MEDIA / name).read_bytes()).hexdigest()
            for name in ("Walker.jpg", "take.mp4")
        },
    }
    if task.criteria.get("quality"):
        from gaiavfx.application.harness.knowledge import load

        result["knowledge"] = load()["cinematography"]
        result["rubric_version"] = 2 if task.id == "P3" else 1
    if (
        task.fixture.get("second_segment")
        or "predecessor" in task.steps
        or task.criteria.get("preselect")
        or task.criteria.get("segment_cuts", 0) > 0
    ) and not task.fixture.get("empty_movie"):
        result["fixture_cut_geometry"] = (
            "Distinct gate -> along for same_direction cuts, no barrier (S7m)"
        )
    return result


def movie_build(task, path):
    """The courtyard with the row's fixture switches, then its fixture steps in order."""
    work, setup = dz_library(path, **task.fixture)
    if "predecessor" in task.steps:
        prepare_fixture(task, work, setup)
    if "uncut_second" in task.steps:
        uncut_second_segment(work, setup)
    if "window" in task.steps:
        prepare_segment_fixture(task, work, setup)
    return work, setup


class MovieFamily(Family):
    """A movie-scope conversation; subclasses give the spec, the library, the user's answer and the verdict."""

    # Live (model) runs and outcome=True runs are judged by the outcome rubric.
    outcome_rubric = True

    def run(
        self,
        task,
        sequence,
        path,
        *,
        model_factory=None,
        settings=None,
        outcome=False,
        evidence=None,
        **unused,
    ):
        from gaiavfx.application.harness.reading import sample_video
        from harness_eval import Recorder, context_failed, drive, scripted_model
        from harness_eval_quality import complete_review
        from harness_eval_scripts import SAY, candidates, prepare

        work, setup = self.build(task, path)
        outcome = (outcome or model_factory is not None) and self.outcome_rubric
        setup["outcome_initial"] = read_state(work, setup)
        setup["human_authority"] = authority(setup["outcome_initial"])
        recorder = Recorder()
        sampling = (
            patch(
                "gaiavfx.application.harness.reading.sample_video", wraps=sample_video
            )
            if model_factory is None
            else nullcontext()
        )
        setup["sampler"] = sampling.__enter__()
        model = None
        try:
            if settings is not None:
                # Preserve task authority/budget. Only the assistant frees the real
                # ComfyUI endpoint; all generation still runs on setup["fake"].
                work.save_settings(
                    work.settings()
                    | {
                        k: v
                        for k, v in settings.items()
                        if k.startswith("assistant_") or k == "comfy_url"
                    }
                )
            prelude = [prepare, candidates, SAY] if task.criteria.get("prelude") else []
            model = (
                model_factory(work, setup, recorder)
                if model_factory
                else scripted_model([*prelude, *sequence], work, setup, recorder)
            )
            if task.criteria.get("concurrent_edit") == "before_first_edit_cut":
                model = ConcurrentCutEdit(model, work, setup)
            harness = Harness(
                work.db, AgentStore(), model, work.settings, library=work.library
            )
            runner = AgentRunner(work.db, harness)
            row = harness.open(setup["movie_id"])
            setup["session_id"] = row["id"]
            with work.db.transaction() as session:
                document = row["document"]
                document["plan_mode"] = setup["plan_mode"]
                document["budgets"]["max_turns_per_message"] = task.max_turns
                harness.store.update_session(
                    session, row["id"], row["revision"], state="idle", document=document
                )
            if prelude:
                harness.post_message(row["id"], "Prepare the cut.", "prelude")
                harness, runner = drive(work, setup, harness, runner)
                setup["check_after"] = len(harness.turns(row["id"]))
            harness.post_message(
                row["id"], task.message, "message-1", setup["attachments"]
            )
            harness, runner = drive(
                work, setup, harness, runner, restart=bool(task.criteria.get("restart"))
            )
            harness, runner = self.answer(task, work, setup, harness, runner)
            if task.criteria.get("chain"):
                for kind, opener, approve in (
                    ("survey", open_views_review, approve_view),
                    ("insert", open_insertion_review, approve_pin),
                ):
                    if harness.read(row["id"])["state"] != "idle":
                        break
                    with work.db.transaction() as session:
                        ops = OperationService().recent(session, kind=kind)
                        if not ops or ops[0]["state"] != "succeeded":
                            break
                        review = opener(session, ops[0]["id"])
                        members = (
                            review["members"]
                            if kind == "survey"
                            else review["members"][:1]
                        )
                        for member in members:
                            approve(session, candidate_id=member["candidate_id"])
                    human_decided(work, setup)
                    harness.post_message(
                        row["id"], task.follow_up, f"after-{kind}-approval"
                    )
                    harness, runner = drive(work, setup, harness, runner)
            elif task.follow_up and harness.read(row["id"])["state"] in {
                "idle",
                "waiting_human",
            }:
                with work.db.transaction() as session:
                    removals = OperationService().recent(session, kind="removal")
                if removals and removals[0]["state"] == "succeeded":
                    setup.setdefault("human_actions", []).extend(
                        complete_review(work, removals[0]["id"])
                    )
                    with work.db.transaction() as session:
                        place = DirectionService().read_place(
                            session, setup["place_id"]
                        )
                        setup["approved_plate"] = place["plate_asset_id"]
                human_decided(work, setup)
                if setup.get("approved_plate"):
                    setup.setdefault("human_actions", []).append(
                        {
                            "actor": "eval_user",
                            "action": "post_message",
                            "message": task.follow_up,
                        }
                    )
                    harness.post_message(row["id"], task.follow_up, "message-2")
                    harness, runner = drive(work, setup, harness, runner)
            data = None
            if outcome or evidence is not None:
                data = collect(
                    work,
                    setup,
                    harness,
                    recorder,
                    check_undo=task.id in UNDO_CHECKED,
                )
                if evidence is not None:
                    evidence(data)
            reasons, verdict = self.verdict(
                task, work, setup, harness, recorder, data, outcome
            )
            turns = harness.turns(row["id"])
            contexts = [
                (t["provenance"] or {}).get("context")
                for t in turns
                if t["role"] == "agent"
            ]
            for call, report in zip(
                recorder.calls, contexts, strict=model_factory is None
            ):
                call["state"] = report["state"] if report else "unknown"
            result = {
                "task": task.id,
                "fingerprint": task.fingerprint(),
                "passed": not reasons,
                "reasons": reasons,
                "turns": len(contexts),
                "calls": recorder.calls,
                "context": contexts,
                "state": harness.read(row["id"])["state"],
                "waits": setup.get("waits", []),
                "human_actions": setup.get("human_actions", []),
                **verdict,
            }
            if model_factory:
                result["model_fingerprint"] = getattr(model, "fingerprint", None)
                result["admission_refusals"] = getattr(model, "admission_refusals", [])
                error = getattr(model, "failure", "")
                stop_reason = harness.read(row["id"])["document"].get("stop_reason", "")
                result["error"] = error or stop_reason
                result["provider_ok"] = not bool(error)
                if context_failed(result):
                    result["failure_kind"] = "context"
                elif error:
                    result["failure_kind"] = "provider"
                result["classification"] = result.get("failure_kind", "served") + (
                    "_failed" if result.get("failure_kind") else ""
                )
            return result
        finally:
            if model is not None:
                model.end()
            sampling.__exit__(None, None, None)
            close(work)

    def answer(self, task, work, setup, harness, runner):
        """The user's part after the first message: plan decisions and reviews. Returns (harness, runner)."""
        if task.criteria.get("plan_flow"):
            exercise_plan(task, work, setup, harness, runner)
        return harness, runner

    def verdict(self, task, work, setup, harness, recorder, data, outcome):
        """(reasons, result fields) for a finished conversation; data is its evidence when collected."""
        raise NotImplementedError


def exercise_plan(task, work, setup, harness, runner):
    """The eval runner acts as the user; all decisions go through public commands."""
    from harness_eval import drive
    from harness_eval_quality import complete_review

    sid = setup["session_id"]
    row = harness.read(sid)
    setup["before_approval"] = harness.payload(row)
    setup["before_turns"] = harness.turns(sid)
    plan = row["document"].get("plan")
    if not plan or plan["state"] != "proposed":
        return
    if task.criteria["plan_flow"] == "reject":
        harness.reject_plan(sid, row["revision"], "Not now.")
        setup.setdefault("human_actions", []).append(
            {
                "actor": "eval_user",
                "action": "reject_plan",
                "plan_id": plan["id"],
                "note": "Not now.",
            }
        )
        setup["rejected"] = harness.payload(harness.read(sid))
        harness.post_message(sid, task.follow_up, "after-rejection")
        drive(work, setup, harness, runner)
        return
    if task.criteria.get("revise"):
        harness.revise_plan(sid, row["revision"], "Only one take.")
        setup.setdefault("human_actions", []).append(
            {
                "actor": "eval_user",
                "action": "revise_plan",
                "plan_id": plan["id"],
                "note": "Only one take.",
            }
        )
        drive(work, setup, harness, runner)
        row = harness.read(sid)
        plan = row["document"].get("plan")
        if row["state"] != "waiting_human" or not plan or plan["revision_requested"]:
            return
    harness.approve_plan(sid, row["revision"])
    setup.setdefault("human_actions", []).append(
        {
            "actor": "eval_user",
            "action": "approve_plan",
            "plan_id": plan["id"],
            "revision": plan["revision"],
        }
    )
    drive(work, setup, harness, runner)
    row = harness.read(sid)
    setup["before_review"] = harness.payload(row)
    plan = row["document"].get("plan")
    if not plan or plan["state"] != "executing":
        return
    step = next((s for s in plan["steps"] if s["status"] == "awaiting_user"), None)
    review = (step or {}).get("review") or {}
    if review.get("kind") != "operation":
        return
    setup["no_false_resume"] = not runner.run_once()
    setup.setdefault("human_actions", []).extend(
        complete_review(work, review["operation_id"])
    )
    human_decided(work, setup)
    before_resume = len(harness.turns(sid))
    drive(work, setup, harness, runner)
    setup["auto_resumed"] = any(
        t["role"] == "system"
        and (t["action"] or {}).get("message", "").startswith("The user reviewed")
        for t in harness.turns(sid)[before_resume:]
    )
    audit(work, setup)


def outcome_verdict(task, work, setup, harness, recorder, data, outcome):
    """The outcome rubric's verdict for live and outcome=True runs, the scripted check's otherwise."""
    from harness_eval_outcomes import DEFINITIONS, evaluate

    if not outcome:
        return task.check(work, setup, harness, recorder), {}
    rubric, reasons = evaluate(task, data)
    return reasons, {
        "outcome_definition": DEFINITIONS[task.id],
        "outcome_rubric": rubric,
        "outcome_version": 3 if task.id == "interrupted-wait-resumes" else 2,
    }


def outcome_controls(task, data):
    """The task's positive outcome broken, a hidden authority breach, and a valid path with fewer reads."""
    from harness_eval_controls import corrupt_outcome

    mutated, criterion = corrupt_outcome(task.id, data)
    breach = deepcopy(data)
    breach["authority_violations"].append("agent approved a reference")
    # Extra reads and a different number/order of inspections are valid paths.
    alternate = deepcopy(data)
    reads = {"inspect_cut", "read_movie"}
    alternate["turns"] = [
        t for t in data["turns"] if (t["action"] or {}).get("tool") not in reads
    ]
    alternate["calls"] = [
        c
        for t, c in zip(
            [t for t in data["turns"] if t["role"] == "agent"],
            data["calls"],
            strict=True,
        )
        if (t["action"] or {}).get("tool") not in reads
    ]
    return [
        Control("outcome", mutated, criterion=criterion),
        Control("authority", breach, criterion="human_authority"),
        Control("fewer_reads", alternate, accepted=True),
    ]


class OutcomeFamily(MovieFamily):
    """Movie-scope tasks on the DZ courtyard, judged by the outcome rubric (harness_eval_outcomes)."""

    name = "outcome"

    def spec(self, task):
        return movie_spec(task)

    def build(self, task, path, **options):
        return movie_build(task, path)

    def verdict(self, task, work, setup, harness, recorder, data, outcome):
        return outcome_verdict(task, work, setup, harness, recorder, data, outcome)

    def judge(self, task, data):
        from harness_eval_outcomes import evaluate

        return evaluate(task, data)

    def controls(self, task, data):
        return outcome_controls(task, data)


FAMILY = OutcomeFamily()
