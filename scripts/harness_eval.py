"""Fingerprinted harness campaigns; generation always uses fake ComfyUI.

Scripted is CPU-only. Proxy uses the configured remote adapter. Local loads the
real assistant only with --allow-gpu. P1-P4 score store/pack invariants, never
render quality or a judge model. Provider failures and context walls are separate
from served quality. Each model/repeat/task gets an isolated fixture and logs.
"""

import argparse
import json
import sys
from contextlib import nullcontext
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend" / "src"))
sys.path.insert(0, str(ROOT / "scripts"))

from gaiavfx.application.harness.driver import AgentRunner
from gaiavfx.application.harness.loop import Harness
from gaiavfx.application.harness.model import ModelReply
from gaiavfx.application.prepare_references import (
    approve_pin,
    approve_view,
    open_insertion_review,
    open_views_review,
)
from gaiavfx.modules.agent.service import AgentStore
from gaiavfx.modules.direction.service import DirectionService
from gaiavfx.modules.generation.operations.service import OperationService
from harness_eval_hooks import ConcurrentCutEdit
from harness_eval_outcome_state import (
    audit,
    authority,
    collect,
    human_decided,
    read_state,
)
from harness_eval_scripts import GOOD, SAY, candidates, prepare
from harness_eval_tasks import (
    FILM_TASKS,
    QUALITY_TASKS,
    STAGE_TASKS,
    TASKS,
    close,
    run_idle,
)


def missing_required(raw, schema=None, prefix=""):
    if not isinstance(raw, dict):
        return [prefix or "action"]
    if schema is None:
        return [key for key in ("tool", "thought") if key not in raw]
    if not prefix:
        from gaiavfx.application.harness.actions import inlined

        schema = inlined(schema)
    if "oneOf" in schema:
        branch = next(
            (
                b
                for b in schema["oneOf"]
                if all(
                    raw.get(key) == rule["const"]
                    for key, rule in b.get("properties", {}).items()
                    if "const" in rule
                )
            ),
            None,
        )
        if branch is None:
            return [prefix + "branch"]
        schema = branch
    missing = [prefix + key for key in schema.get("required", []) if key not in raw]
    for key, rule in schema.get("properties", {}).items():
        if key in raw and isinstance(raw[key], dict):
            missing += missing_required(raw[key], rule, prefix + key + ".")
    return missing


def conforms(raw, schema=None):
    return not missing_required(raw, schema)


class Recorder:
    def __init__(self):
        self.calls = []


class ScriptedModel:
    def __init__(self, sequence, work, setup, recorder=None):
        self.sequence = iter(sequence)
        self.work, self.setup = work, setup
        self.recorder = recorder or Recorder()
        self.begins = self.ends = 0
        self.active = False

    def begin(self, settings, cancelled):
        self.begins += 1
        self.active = True

    def end(self):
        self.ends += 1
        self.active = False

    def call(self, settings, messages, schema, cancelled, log_path):
        entry = next(self.sequence, SAY)
        raw = entry(self.work, self.setup) if callable(entry) else entry
        self.recorder.calls.append(
            {
                "conforms": conforms(raw, schema),
                "missing": missing_required(raw, schema),
                "schema_bytes": len(json.dumps(schema, separators=(",", ":")).encode()),
                "branches": len(schema.get("oneOf", [schema])),
                "part_types": [
                    p.get("type")
                    for m in messages
                    if isinstance(m.get("content"), list)
                    for p in m["content"]
                ],
                "sample_calls": self.setup["sampler"].call_count
                if self.setup.get("sampler") is not None
                else 0,
            }
        )
        return ModelReply(raw, 100, 20)


def scripted_model(sequence, work, setup, recorder=None):
    return ScriptedModel(sequence, work, setup, recorder)


def drive(work, setup, harness, runner, *, restart=False):
    for _ in range(12):
        if not runner.run_once():
            break
        row = harness.read(setup["session_id"])
        if row["state"] != "waiting_operation":
            break
        assert not harness.model.active, "Model lifetime crossed a durable wait"
        setup.setdefault("waits", []).append(
            {
                "operation": row["document"]["waiting_on"],
                "updated_at": row["updated_at"],
            }
        )
        if restart:
            model = harness.model
            harness = Harness(
                work.db, AgentStore(), model, work.settings, library=work.library
            )
            runner = AgentRunner(work.db, harness)
            runner.recover()
            restart = False
        # The waiting session is not claimable until the operation completes.
        assert not runner.run_once()
        run_idle(work, setup["fake"])
    audit(work, setup)
    return harness, runner


def run_task(
    task,
    sequence,
    path,
    *,
    model_factory=None,
    settings=None,
    outcome=False,
    evidence=None,
    film="seeded",
    film_library=None,
):
    if task.criteria.get("film"):
        from harness_eval_film import run_task as run_film_task

        return run_film_task(
            task,
            sequence,
            path,
            model_factory=model_factory,
            settings=settings,
            evidence=evidence,
            film=film,
            film_library=film_library,
        )
    if task.criteria.get("recast"):
        from harness_eval_recast import run_task as run_recast_task

        return run_recast_task(
            task, sequence, path, model_factory=model_factory, evidence=evidence
        )
    if task.criteria.get("stage"):
        from harness_eval_stage import run_task as run_stage_task

        return run_stage_task(
            task,
            sequence,
            path,
            model_factory=model_factory,
            settings=settings,
            evidence=evidence,
        )
    work, setup = task.build(path)
    outcome = (outcome or model_factory is not None) and not task.criteria.get(
        "quality"
    )
    if (
        outcome
        or task.criteria.get("replacement")
        or evidence is not None
        or task.id in {"hard-cut-at-frame-36", "narrative-reverse-storyline"}
    ):
        setup["outcome_initial"] = read_state(work, setup)
        setup["human_authority"] = authority(setup["outcome_initial"])
    recorder = Recorder()
    from gaiavfx.application.harness.reading import sample_video

    sampling = (
        patch("gaiavfx.application.harness.reading.sample_video", wraps=sample_video)
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
        harness.post_message(row["id"], task.message, "message-1", setup["attachments"])
        harness, runner = drive(
            work, setup, harness, runner, restart=bool(task.criteria.get("restart"))
        )
        if (
            task.criteria.get("replacement")
            and task.follow_up
            and harness.read(row["id"])["state"] == "waiting_human"
        ):
            harness.post_message(row["id"], task.follow_up, "replacement-choice")
            harness, runner = drive(work, setup, harness, runner)
        if task.criteria.get("replacement") and setup["replacement_mode"] in {
            "plan",
            "pair",
        }:
            current = harness.read(row["id"])
            if (current["document"].get("plan") or {}).get("state") == "proposed":
                harness.approve_plan(row["id"], current["revision"])
                human_decided(work, setup)
                harness, runner = drive(work, setup, harness, runner)
        elif task.criteria.get("quality"):
            from harness_eval_quality import auto_approve

            auto_approve(work, setup, harness, runner, drive)
        elif task.criteria.get("plan_flow"):
            exercise_plan(task, work, setup, harness, runner)
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
                        review["members"] if kind == "survey" else review["members"][:1]
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
            from harness_eval_quality import complete_review

            with work.db.transaction() as session:
                removals = OperationService().recent(session, kind="removal")
            if removals and removals[0]["state"] == "succeeded":
                setup.setdefault("human_actions", []).extend(
                    complete_review(work, removals[0]["id"])
                )
                with work.db.transaction() as session:
                    place = DirectionService().read_place(session, setup["place_id"])
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
        rubric = None
        if outcome or evidence is not None:
            data = collect(
                work,
                setup,
                harness,
                recorder,
                check_undo=task.id
                in {
                    "drafts-structure-from-brief",
                    "narrative-reverse-storyline",
                    "draft-storyline-under-plan",
                },
            )
            if evidence is not None:
                evidence(data)
        if task.criteria.get("quality"):
            from harness_eval_quality import score

            rubric, reasons = score(task, work, setup, harness)
        elif outcome:
            from harness_eval_outcomes import DEFINITIONS, evaluate

            outcome_rubric, reasons = evaluate(task, data)
        else:
            reasons = task.check(work, setup, harness, recorder)
        turns = harness.turns(row["id"])
        contexts = [
            (t["provenance"] or {}).get("context")
            for t in turns
            if t["role"] == "agent"
        ]
        for call, report in zip(recorder.calls, contexts, strict=model_factory is None):
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
        }
        if rubric is not None:
            result.update(rubric=rubric, approvals=setup.get("approvals", []))
        if outcome:
            result.update(
                outcome_definition=DEFINITIONS[task.id],
                outcome_rubric=outcome_rubric,
                outcome_version=3 if task.id == "interrupted-wait-resumes" else 2,
            )
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


def exercise_plan(task, work, setup, harness, runner):
    """The eval runner acts as the user; all decisions go through public commands."""
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
    if not step:
        return
    setup["no_false_resume"] = not runner.run_once()
    setup.setdefault("human_actions", []).extend(
        complete_review(work, step["review_of"])
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


def served_run(run):
    return (
        not provider_failed(run)
        and not context_failed(run)
        and run.get("failure_kind") != "evaluation"
        and run.get("classification") != "skipped"
    )


def summarise(runs):
    served = [r for r in runs if served_run(r)]
    quality = [r for r in served if r.get("rubric")]
    models = sorted({r.get("model", "scripted") for r in runs})
    return {
        "tasks": len(runs),
        "passed": sum(run["passed"] for run in runs),
        "provider_failed": sum(provider_failed(r) for r in runs),
        "context_failed": sum(context_failed(r) for r in runs),
        "evaluation_failed": sum(r.get("failure_kind") == "evaluation" for r in runs),
        "skipped": sum(r.get("classification") == "skipped" for r in runs),
        "served_pass_rate": sum(run["passed"] for run in served) / len(served)
        if served
        else 0,
        "rubric_rate": sum(sum(r["rubric"].values()) for r in quality)
        / sum(len(r["rubric"]) for r in quality)
        if quality
        else None,
        "models": {
            model: model_summary(
                [r for r in runs if r.get("model", "scripted") == model]
            )
            for model in models
        },
        "results": [
            {
                **{
                    key: run[key]
                    for key in (
                        "task",
                        "fingerprint",
                        "passed",
                        "reasons",
                        "turns",
                        "calls",
                        "context",
                    )
                },
                **{
                    k: run[k]
                    for k in (
                        "model",
                        "model_fingerprint",
                        "repeat",
                        "rubric",
                        "approvals",
                        "human_actions",
                        "classification",
                        "error",
                        "outcome_definition",
                        "outcome_rubric",
                        "outcome_version",
                        "film",
                        "messages",
                        "rejected_turns",
                        "rejected_tools",
                    )
                    if k in run
                },
            }
            for run in runs
        ],
    }


def model_summary(runs):
    served = [r for r in runs if served_run(r)]
    quality = [r for r in served if r.get("rubric")]
    return {
        "runs": len(runs),
        "served": len(served),
        "provider_failed": sum(map(provider_failed, runs)),
        "context_failed": sum(map(context_failed, runs)),
        "evaluation_failed": sum(r.get("failure_kind") == "evaluation" for r in runs),
        "served_pass_rate": sum(r["passed"] for r in served) / len(served)
        if served
        else 0,
        "rubric_rate": sum(sum(r["rubric"].values()) for r in quality)
        / sum(len(r["rubric"]) for r in quality)
        if quality
        else None,
    }


def provider_failed(run):
    """Transport/provider failures are not orchestration failures or context walls."""
    return not context_failed(run) and (
        run.get("failure_kind") == "provider" or run.get("provider_ok") is False
    )


def context_failed(run):
    if run.get("failure_kind") == "context":
        return True
    error = str(run.get("error", "")).lower()
    if "request budget" in error and "exceeds context" in error:
        return True
    return any(
        text in error
        for text in (
            "context_length_exceeded",
            "maximum context length",
            "context window",
            "agent context cannot be assembled",
            "context size exceeded",
            "exceeds this connection's context budget",
        )
    )


def parser():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign", required=True)
    parser.add_argument(
        "--backend", choices=["scripted", "proxy", "local"], default="scripted"
    )
    parser.add_argument("--allow-gpu", action="store_true")
    parser.add_argument(
        "--library", type=Path, help="Read saved assistant settings from this library"
    )
    parser.add_argument("--tasks")
    parser.add_argument("--out", type=Path)
    parser.add_argument("--models")
    parser.add_argument("--repeats", type=positive_int, default=1)
    parser.add_argument("--workers", type=positive_int, default=1)
    parser.add_argument("--settings", type=Path)
    parser.add_argument(
        "--film",
        choices=["seeded", "heldout"],
        default="seeded",
        help="Synthetic film for film-* tasks; heldout is run and reported, never tuned on",
    )
    parser.add_argument(
        "--film-library",
        type=Path,
        help="Run film-* tasks on a sanitized copy under .cache/usability/ (restored after each task)",
    )
    return parser


def task_names(text):
    """Task ids from --tasks; "film" names every film-* task, "stage" every stage-* task, E<n> the n-th task."""
    aliases = {f"E{i}": t.id for i, t in enumerate(TASKS, 1)}
    families = {"film": FILM_TASKS, "stage": STAGE_TASKS}
    names = []
    for name in text.split(","):
        names.extend(
            [t.id for t in families[name]]
            if name in families
            else [aliases.get(name, name)]
        )
    return names


def check_film_options(args, cli, tasks):
    # Film tasks patch module globals (the seeded film) or hold one copy: overlapping runs would read each other's
    # film (Astra's P5 review).
    if args.workers != 1 and any(
        t.criteria.get("film") or t.criteria.get("stage") for t in tasks
    ):
        cli.error("Film and stage tasks run one at a time; use --workers 1")
    if args.film_library is None:
        return
    if any(not t.criteria.get("film") for t in tasks):
        cli.error("--film-library applies to film-* tasks only")
    from gaiavfx.shared.errors import ApplicationError
    from harness_eval_film import copy_library

    try:
        copy_library(args.film_library)
    except ApplicationError as exc:
        cli.error(str(exc))


def positive_int(value):
    number = int(value)
    if number < 1:
        raise argparse.ArgumentTypeError("must be positive")
    return number


def main(argv=None):
    cli = parser()
    args = cli.parse_args(argv)
    if args.backend == "local" and not args.allow_gpu:
        cli.error(
            "The local backend loads the assistant on the GPU; pass --allow-gpu after the user's go-ahead"
        )
    if args.backend != "scripted":
        from harness_eval_live import campaign

        return campaign(args, cli)
    out = args.out or ROOT / ".cache" / "harness-eval" / args.campaign
    selected = task_names(args.tasks) if args.tasks else None
    tasks = [
        t for t in (*TASKS, *QUALITY_TASKS) if selected is None or t.id in selected
    ]
    if selected and set(selected) - {t.id for t in tasks}:
        cli.error("Unknown tasks: " + ", ".join(set(selected) - {t.id for t in tasks}))
    if not tasks:
        cli.error("No matching tasks")
    check_film_options(args, cli, tasks)
    out.mkdir(parents=True, exist_ok=True)
    runs = [
        run_task(
            task,
            GOOD[task.id],
            out / "fixtures" / task.id,
            film=args.film,
            film_library=args.film_library,
        )
        for task in tasks
    ]
    (out / "runs.jsonl").write_text(
        "".join(json.dumps(run) + "\n" for run in runs), "utf-8"
    )
    (out / "summary.json").write_text(json.dumps(summarise(runs), indent=2), "utf-8")
    return (
        0
        if all(run["passed"] or run.get("classification") == "skipped" for run in runs)
        else 1
    )


if __name__ == "__main__":
    raise SystemExit(main())
