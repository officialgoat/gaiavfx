"""Opt-in real assistant campaigns; fixtures and generation remain isolated/fake."""

import hashlib
import json
import os
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from pathlib import Path

from gaiavfx.application.harness.context import BUDGET_ADMISSION_PREFIXES
from gaiavfx.application.harness.model import (
    RETRYABLE_REPLY_PREFIXES,
    AssistantModel,
)
from gaiavfx.application.workspace import DEFAULT_SETTINGS, Workspace
from gaiavfx.infrastructure.assistant_gateway import AssistantGateway
from gaiavfx.shared.assistant import connection_for
from harness_eval_tasks import QUALITY_TASKS, TASKS, close


class RecordedAssistant(AssistantModel):
    def __init__(self, gateway, comfy, recorder):
        super().__init__(gateway, comfy)
        self.recorder = recorder
        self.failure = ""
        self.admission_refusals = []
        self.fingerprint = None

    def begin(self, settings, cancelled):
        try:
            super().begin(settings, cancelled)
            self.fingerprint = hashlib.sha256(self.identity.encode()).hexdigest()[:16]
        except Exception as exc:
            self.failure = str(exc)
            raise

    def call(self, settings, messages, schema, cancelled, log_path):
        from harness_eval import conforms, missing_required

        if self.failure.startswith(BUDGET_ADMISSION_PREFIXES) and self.recorder.calls:
            previous = self.recorder.calls[-1]
            if previous.get("error", "").startswith(BUDGET_ADMISSION_PREFIXES):
                self.recorder.calls.pop()
                self.admission_refusals.append(
                    {
                        "before_call": len(self.recorder.calls),
                        "error": previous["error"],
                    }
                )
        call = {
            "conforms": False,
            "missing": [],
            "schema_bytes": len(json.dumps(schema, separators=(",", ":")).encode()),
            "branches": len(schema.get("oneOf", [schema])),
            "part_types": [
                p.get("type")
                for m in messages
                if isinstance(m.get("content"), list)
                for p in m["content"]
            ],
        }
        self.recorder.calls.append(call)
        try:
            reply = super().call(settings, messages, schema, cancelled, log_path)
        except Exception as exc:
            # Invalid output is served model behavior, not a provider outage.
            if not str(exc).startswith(RETRYABLE_REPLY_PREFIXES):
                self.failure = str(exc)
            call["error"] = str(exc)
            call["metrics"] = self.call_metrics
            raise
        if self.failure.startswith(BUDGET_ADMISSION_PREFIXES):
            self.failure = ""
        call.update(
            conforms=conforms(reply.raw, schema),
            missing=missing_required(reply.raw, schema),
            input_tokens=reply.input_tokens,
            output_tokens=reply.output_tokens,
            metrics=reply.metrics,
        )
        return reply


def read_settings(args, cli):
    library = args.library or Path(
        os.environ.get("GAIAVFX_LIBRARY", ".cache/library")
    )
    if args.settings:
        try:
            settings = json.loads(args.settings.read_text("utf-8"))
        except (OSError, ValueError) as exc:
            cli.error(f"Cannot read --settings: {exc}")
        if not isinstance(settings, dict):
            cli.error("--settings must contain a settings JSON object")
        settings = deepcopy(DEFAULT_SETTINGS) | settings
    elif (library / "library.sqlite3").is_file():
        work = Workspace(library)
        try:
            settings = work.settings()
        finally:
            close(work)
    else:
        cli.error(
            "Supply --settings FILE or --library DIR (default: GAIAVFX_LIBRARY or .cache/library)"
        )
    connection = connection_for(settings)
    if (connection["provider"] == "managed_local") != (args.backend == "local"):
        cli.error(f"--backend {args.backend} does not match assistant_agent_connection")
    return settings


def campaign(args, cli):
    from harness_eval import (
        ROOT,
        check_film_options,
        context_failed,
        run_task,
        summarise,
        task_names,
    )

    settings = read_settings(args, cli)
    selected = task_names(args.tasks) if args.tasks else [t.id for t in QUALITY_TASKS]
    tasks = [t for t in (*TASKS, *QUALITY_TASKS) if t.id in selected]
    if set(selected) - {t.id for t in tasks}:
        cli.error("Unknown tasks: " + ", ".join(set(selected) - {t.id for t in tasks}))
    check_film_options(args, cli, tasks)
    models = (
        args.models.split(",") if args.models else [connection_for(settings)["model"]]
    )
    if not tasks or len(set(models)) != len(models):
        cli.error("Choose at least one task and distinct models")
    out = args.out or ROOT / ".cache/harness-eval" / args.campaign
    out.mkdir(parents=True, exist_ok=True)
    if (out / "runs.jsonl").exists():
        cli.error("Campaign already has runs.jsonl; choose a fresh --campaign or --out")
    matrix = [
        (model, repeat, task)
        for model in models
        for repeat in range(1, args.repeats + 1)
        for task in tasks
    ]

    def execute(entry):
        model, repeat, task = entry
        configured = deepcopy(settings)
        if args.models:
            if args.backend == "local":
                configured["assistant_model"] = model
            else:
                for connection in configured["assistant_connections"]:
                    if connection["id"] == configured["assistant_agent_connection"]:
                        connection["model"] = model
        label = hashlib.sha256(model.encode()).hexdigest()[:12]
        path = out / "fixtures" / label / str(repeat) / task.id

        def factory(work, setup, recorder):
            # Credentials never enter settings, fixture records or campaign output.
            # A proxy key is optional (e.g. an unauthenticated localhost proxy).
            connection = connection_for(configured)
            credential_id = connection.get("credential_id")
            if credential_id and os.environ.get("GAIAVFX_ASSISTANT_API_KEY"):
                work.credentials.save(
                    credential_id,
                    os.environ["GAIAVFX_ASSISTANT_API_KEY"],
                    storage="session",
                )
            return RecordedAssistant(
                AssistantGateway(work.credentials), work.comfy, recorder
            )

        try:
            result = run_task(
                task,
                (),
                path,
                model_factory=factory,
                settings=configured,
                film=args.film,
                film_library=args.film_library,
            )
        except Exception as exc:  # noqa: BLE001 - retain an isolated failed run, continue campaign
            # Fixture/check failures remain visible and never inflate served quality.
            result = {
                "task": task.id,
                "fingerprint": task.fingerprint(),
                "passed": False,
                "reasons": [str(exc)],
                "turns": 0,
                "calls": [],
                "context": [],
                "error": str(exc),
                "failure_kind": "evaluation",
                "classification": "evaluation_failed",
            }
            if context_failed(result):
                result.update(failure_kind="context", classification="context_failed")
        return result | {"model": model, "repeat": repeat, "backend": args.backend}

    runs = []
    # Local workers serialize on the existing GPU file lease; each releases before
    # fake ComfyUI runs. Proxy workers need no lease and may execute concurrently.
    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        for result in pool.map(execute, matrix):
            runs.append(result)
            with (out / "runs.jsonl").open("a", encoding="utf-8") as stream:
                stream.write(json.dumps(result) + "\n")
            (out / "summary.json").write_text(
                json.dumps(summarise(runs), indent=2), "utf-8"
            )
    return (
        0
        if all(r["passed"] or r.get("classification") == "skipped" for r in runs)
        else 1
    )
