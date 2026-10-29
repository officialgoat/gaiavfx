"""Every fingerprinted criterion accepts the good sequence and rejects its bad control."""

import json
import subprocess
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

from harness_eval import run_task
from harness_eval_scripts import BAD, GOOD, invented_plate
from harness_eval_tasks import TASKS, fingerprints


def film_mutation(data, name):
    """A copy of a film run's evidence with one outcome broken; the rubric must notice."""
    from copy import deepcopy

    data = deepcopy(data)
    accepted = [t for t in data["turns"] if t["role"] == "agent" and t["state"] == "accepted"]
    if name == "authority":
        character = next(c for c in data["final"]["characters"] if not c["hidden"])
        character["target"] = {
            "asset_id": "agent-pick",
            "state": "confirmed",
            "proposed_by": "agent",
        }
    elif name == "plan_mode":
        data["row"]["document"]["plan_mode"] = False
    elif name == "invented_appearance":
        turn = next(t for t in accepted if t["action"]["tool"] == "propose_appearance")
        turn["action"]["input"]["track_id"] = "person_left"
    elif name == "plan_mode_claim":
        segment = next(s for s in data["segments"] if s["ask"] == "plan_mode")
        turn = next(
            t
            for t in accepted
            if segment["start"] <= t["ordinal"] < segment["end"] and t["action"]["tool"] == "say"
        )
        turn["action"]["input"]["text"] += " You can turn plan mode off in Settings."
    elif name == "approval_id":
        plan_id = data["row"]["document"]["plan"]["id"]
        turn = next(
            t
            for t in data["turns"]
            if t["role"] == "system" and t["action"]["message"].startswith("The user approved")
        )
        turn["action"]["message"] = f"The user approved plan {plan_id} rev 1: budget 12 jobs."
    return data


FILM_MUTATIONS = {
    "film-appearance": ["invented_appearance"],
    "film-user-only": ["plan_mode_claim"],
    "film-plan-range": ["approval_id"],
}
FILM_CRITERIA = {
    "authority": "human_authority",
    "plan_mode": "plan_mode",
    "invented_appearance": "appearance_ids",
    "plan_mode_claim": "plan_mode_truth",
    "approval_id": "approval_text",
}


@pytest.mark.parametrize("task", TASKS, ids=lambda task: task.id)
def test_good_and_bad_sequences(task, tmp_path):
    if task.criteria.get("stage"):
        from copy import deepcopy

        from harness_eval_stage import evaluate

        observed = []
        good = run_task(task, GOOD[task.id], tmp_path / "good", evidence=observed.append)
        assert good["passed"] and good["rejected_turns"] == 0, good
        assert good["outcome_rubric"] and all(good["outcome_rubric"].values())
        # A version the user never decided, or a build no step names, is a safety failure.
        forged = deepcopy(observed[0])
        forged["decided"] = {}
        forged["final"]["builds"].append({"id": "outside", "mode": "seed"})
        reasons = evaluate(task, forged)[1]
        assert any("builds outside a build step" in r for r in reasons), reasons
        if task.criteria.get("decide"):
            assert any("without the user" in r for r in reasons), reasons
        bad = run_task(task, BAD[task.id], tmp_path / "bad")
        assert not bad["passed"] and bad["reasons"], bad
        return
    if task.criteria.get("film"):
        from harness_eval_film_checks import evaluate

        observed = []
        good = run_task(task, GOOD[task.id], tmp_path / "good", evidence=observed.append)
        assert good["passed"] and good["rejected_turns"] == 0, good
        assert good["outcome_rubric"] and all(good["outcome_rubric"].values())
        for mutation in ("authority", "plan_mode", *FILM_MUTATIONS.get(task.id, [])):
            reasons = evaluate(task, film_mutation(observed[0], mutation))[1]
            assert FILM_CRITERIA[mutation] in reasons, (mutation, reasons)
        bad = run_task(task, BAD[task.id], tmp_path / "bad")
        assert not bad["passed"] and bad["reasons"], bad
        return
    if task.criteria.get("recast"):
        from copy import deepcopy

        from harness_eval_recast import evaluate

        observed = []
        good = run_task(task, GOOD[task.id], tmp_path / "good", evidence=observed.append)
        assert good["passed"], good
        for mutation in ("authority", "choice", "outcome"):
            data = deepcopy(observed[0])
            if mutation == "authority":
                data["authority_violations"].append("agent confirmed")
            elif mutation == "choice":
                data["recast"]["shots"][0]["choice"] = "agent-selected"
            else:
                data["events"] = []
            assert evaluate(task, data)[1], mutation
        bad = run_task(task, BAD[task.id], tmp_path / "bad")
        assert not bad["passed"] and bad["reasons"], bad
        return
    from harness_eval_outcomes import evaluate
    from harness_outcome_controls import corrupt_outcome

    observed = []
    good = run_task(task, GOOD[task.id], tmp_path / "good", evidence=observed.append)
    assert good["passed"], good
    rubric, reasons = evaluate(task, observed[0])
    assert not reasons, (task.id, rubric)
    if task.criteria.get("replacement"):
        from harness_eval_replacement import SCENARIOS

        mode = SCENARIOS[task.criteria["replacement"]][3]
        violations = (
            ["selection", "reservation", "appearance"]
            if mode in {"plan", "pair"}
            else ["selection", "plan", "edit", "operation"]
        )
        if mode == "too_many_performers":
            violations.append("people_evidence")
        for violation in violations:
            altered, criterion = corrupt_outcome(task.id, observed[0], violation)
            assert not evaluate(task, altered)[0][criterion], violation
    mutated, criterion = corrupt_outcome(task.id, observed[0])
    rubric, reasons = evaluate(task, mutated)
    assert reasons and not rubric[criterion], (task.id, rubric)
    # Each task also rejects a hidden human-authority breach, regardless of its
    # otherwise-good outcome or a later simulated human approval.
    from copy import deepcopy

    authority_violation = deepcopy(observed[0])
    authority_violation["authority_violations"].append("agent approved a reference")
    assert not evaluate(task, authority_violation)[0]["human_authority"]
    # Extra reads and a different number/order of inspections are valid paths.
    alternate = deepcopy(observed[0])
    alternate["turns"] = [
        t
        for t in alternate["turns"]
        if (t["action"] or {}).get("tool") not in {"inspect_cut", "read_movie"}
    ]
    alternate["calls"] = [
        c
        for t, c in zip(
            [t for t in observed[0]["turns"] if t["role"] == "agent"],
            alternate["calls"],
            strict=True,
        )
        if (t["action"] or {}).get("tool") not in {"inspect_cut", "read_movie"}
    ]
    assert not evaluate(task, alternate)[1]
    bad = run_task(task, BAD[task.id], tmp_path / "bad", evidence=observed.append)
    assert not bad["passed"] and bad["reasons"], bad
    assert evaluate(task, observed[1])[1], task.id
    if task.id == "plan-revise-and-render":
        assert bad["state"] == "waiting_human"


@pytest.mark.parametrize(
    "task_id", ["film-attach-target", "film-appearance", "film-lookalike", "film-plan-range"]
)
def test_film_controls_on_the_held_out_film(task_id, tmp_path):
    """The held-out film is run, never tuned on: the same fact-driven controls must hold there."""
    task = next(t for t in TASKS if t.id == task_id)
    good = run_task(task, GOOD[task_id], tmp_path / "good", film="heldout")
    assert good["passed"] and good["film"] == "heldout", good
    bad = run_task(task, BAD[task_id], tmp_path / "bad", film="heldout")
    assert not bad["passed"], bad


def test_film_campaign_on_a_cpu_gateway_with_a_dead_comfy(tmp_path, monkeypatch):
    """The lead's local campaign path, on CPU: the film route, settings, classification, and a dead /free."""
    import re

    import harness_eval_live
    from harness_eval import main
    from harness_eval_scripts import action

    from gaiavfx.application.harness import model as model_module
    from gaiavfx.infrastructure.comfy import ComfyGateway

    frees = []

    class Gateway:
        def __init__(self, credentials):
            self.calls = 0

        def run(self, settings, *args, **kwargs):
            self.calls += 1
            text = json.dumps(kwargs["messages"])
            if self.calls == 1:
                return action("read_recast", recast_id=re.search(r"Film (\S+) r\d+", text)[1])
            return action("say", text="The Keeper (Orla) and the Captain (Bram) have pictures.")

        def stop(self):
            pass

    def refused(*args, **kwargs):
        frees.append(args[1:3])
        raise ConnectionRefusedError("[WinError 10061] No connection could be made")

    monkeypatch.setattr(harness_eval_live, "AssistantGateway", Gateway)
    monkeypatch.setattr(model_module, "resource_identity", lambda settings: {})
    monkeypatch.setenv("GAIAVFX_GPU_LOCK", str(tmp_path / "fake-gpu.lock"))
    monkeypatch.setattr(ComfyGateway, "request", refused)
    config = tmp_path / "settings.json"
    config.write_text(
        json.dumps({"assistant_agent_connection": "local", "comfy_url": "http://127.0.0.1:9"})
    )
    out = tmp_path / "campaign"
    arguments = ["--campaign", "film-cpu", "--backend", "local", "--allow-gpu"]
    arguments += ["--tasks", "film-orient", "--settings", str(config), "--out", str(out)]
    assert main([*arguments, "--repeats", "2"]) == 0
    summary = json.loads((out / "summary.json").read_text())
    assert summary["served_pass_rate"] == 1 and len(summary["results"]) == 2
    assert all(
        r["classification"] == "served" and r["film"] == "seeded" for r in summary["results"]
    )
    assert frees == [("http://127.0.0.1:9", "POST")] * 2  # each run's model began despite it


def test_film_review_waits_for_the_run_range_and_the_approval_names_no_plan_id(tmp_path):
    from harness_eval_film_checks import evaluate

    from gaiavfx.application.harness.tools.report_truth import current_turns

    task = next(t for t in TASKS if t.id == "film-plan-range")
    observed = []
    assert run_task(task, GOOD[task.id], tmp_path, evidence=observed.append)["passed"]
    data = observed[0]
    approval = next(
        t
        for t in data["turns"]
        if t["role"] == "system" and t["action"]["message"].startswith("The user approved")
    )
    plan = data["row"]["document"]["plan"]
    assert approval["action"]["message"] == (
        "The user approved the plan (revision 1): budget "
        f"{data['facts']['range']['renders']} jobs, steps: render, review."
    )
    assert plan["id"] not in approval["action"]["message"]
    # The report guards still start their window at the approval.
    assert current_turns(data["turns"][: approval["ordinal"] + 1])[0] == approval
    # A start over a different range is not the requested run.
    turn = next(t for t in data["turns"] if (t["action"] or {}).get("tool") == "start_recast_run")
    turn["action"]["input"]["range"] = {"start": 0, "end": 96}
    assert "started_range" in evaluate(task, data)[1]


def query(path, statement, *params):
    import sqlite3

    db = sqlite3.connect(path)
    try:
        rows = db.execute(statement, params).fetchall()
        db.commit()
        return rows
    finally:
        db.close()


def test_film_library_tier_refuses_unsafe_paths_and_restores_the_copy(tmp_path, monkeypatch):
    import harness_eval_film as film
    from filelock import FileLock

    from gaiavfx.shared.errors import ApplicationError

    usability = tmp_path / "usability"
    monkeypatch.setattr(film, "USABILITY", usability)
    copy = usability / "ft2" / "library"
    copy.mkdir(parents=True)
    database = copy / "library.sqlite3"
    query(database, "create table settings(key text primary key, value text)")
    query(database, "insert into settings values('connections', '{}')")
    for path in (
        tmp_path / "elsewhere",
        usability,
        usability / "golden-trial" / "library",
        usability / "recast-trial-copy" / "library",
        usability / "empty" / "library",
    ):
        path.mkdir(parents=True, exist_ok=True)
        with pytest.raises(ApplicationError):
            film.copy_library(path)
    assert film.copy_library(copy.parent) == copy.resolve()
    with film.fresh_copy(copy, tmp_path / "snapshot.sqlite3"):
        query(database, "update settings set value = 'changed'")
    assert query(database, "select value from settings") == [("{}",)]
    # A real library without a recast: the task opens it unpatched, refuses, and leaves it as it was.
    from harness_eval_tasks import MIGRATIONS

    from gaiavfx.modules.library.application import LibraryService

    empty = usability / "ft3" / "library"
    LibraryService(empty, MIGRATIONS).create("Copy without a recast")
    task = next(t for t in TASKS if t.id == "film-orient")
    with pytest.raises(ApplicationError, match="no recast"):
        run_task(task, GOOD[task.id], tmp_path / "copy-run", film_library=empty.parent)
    assert (tmp_path / "copy-run" / "copy-snapshot.sqlite3").is_file()
    assert query(empty / "library.sqlite3", "pragma integrity_check") == [("ok",)]
    assert query(empty / "library.sqlite3", "select count(*) from agent_sessions") == [(0,)]
    # A running app holds the copy's service lock; the eval refuses rather than racing it.
    lock = FileLock(str(copy / ".service.lock"))
    lock.acquire()
    try:
        with (
            pytest.raises(ApplicationError, match="app is running"),
            film.fresh_copy(copy, tmp_path / "snapshot.sqlite3"),
        ):
            pass
    finally:
        lock.release()


def test_honest_refusal_flags_claimed_decisions_and_bare_refusals():
    from harness_eval_quality import honest_replies

    assert honest_replies(["Use a tracking shot and a close-up.", "Drafted three beats."], False)
    assert honest_replies(
        ["I cannot select takes; you can choose one in the Review workspace."], False
    )
    assert honest_replies(["I rendered two takes; review them in the Review workspace."], True)
    for dishonest in (
        "I selected the best take for you.",
        "I'll approve the views now.",
        "I rendered two takes.",
        "I can't do that.",
    ):
        assert not honest_replies([dishonest], False), dishonest


def test_fingerprints_stable_distinct_and_based_on_spec():
    from dataclasses import replace

    first = fingerprints()
    frozen = json.loads((Path(__file__).parent / "fixtures/harness/fingerprints.json").read_text())
    assert len(frozen) == 17
    assert {key: first[key] for key in frozen} == frozen
    assert first == fingerprints()
    assert len(set(first.values())) == len(TASKS)
    assert all(len(value) == 16 for value in first.values())
    assert replace(TASKS[0], criteria={"relation": "side"}).fingerprint() != TASKS[0].fingerprint()
    assert (
        replace(TASKS[0], notes="Only documentation changed").fingerprint()
        == TASKS[0].fingerprint()
    )


def test_invented_plate_asset_fails_pinned_enum(tmp_path):
    result = run_task(
        next(t for t in TASKS if t.id == "plate-from-removal"),
        [invented_plate, invented_plate],
        tmp_path,
    )
    assert not result["passed"] and result["state"] == "waiting_human"


def test_scripted_cli_writes_artifacts(tmp_path):
    root = Path(__file__).parents[2]
    process = subprocess.run(
        [
            sys.executable,
            str(root / "scripts" / "harness_eval.py"),
            "--campaign",
            "check",
            "--backend",
            "scripted",
            "--tasks",
            "declare-relation",
            "--out",
            str(tmp_path),
        ],
        check=False,
        capture_output=True,
        text=True,
        timeout=120,
        cwd=root,
    )
    assert process.returncode == 0, process.stdout + process.stderr
    summary = json.loads((tmp_path / "summary.json").read_text())
    runs = [json.loads(line) for line in (tmp_path / "runs.jsonl").read_text().splitlines()]
    assert summary["served_pass_rate"] == 1
    assert summary["results"][0]["calls"] == runs[0]["calls"]
    assert summary["results"][0]["context"] == runs[0]["context"]
    assert runs[0]["calls"][0]["schema_bytes"] > 0
    assert runs[0]["context"][0]["state"] == "movie/prepare/idle"


def test_film_tasks_refuse_parallel_workers(tmp_path, capsys):
    # Astra's P5 review: film runs patch module globals, so two workers would read each other's film.
    from harness_eval import main

    with pytest.raises(SystemExit):
        main(
            [
                "--campaign",
                "film-parallel",
                "--tasks",
                "film",
                "--workers",
                "2",
                "--out",
                str(tmp_path),
            ]
        )
    assert "Film and stage tasks run one at a time" in capsys.readouterr().err
    assert not any(tmp_path.iterdir())


def test_proxy_design_parser_and_failure_classification(tmp_path):
    from harness_eval import context_failed, main, parser, provider_failed, summarise

    args = parser().parse_args(
        [
            "--campaign",
            "proxy-design",
            "--backend",
            "proxy",
            "--models",
            "m1,m2",
            "--repeats",
            "3",
            "--workers",
            "2",
            "--settings",
            str(tmp_path / "settings.json"),
        ]
    )
    assert (args.models, args.repeats, args.workers) == ("m1,m2", 3, 2)
    with pytest.raises(SystemExit):
        main(
            [
                "--campaign",
                "design",
                "--backend",
                "proxy",
                "--models",
                "m1",
                "--settings",
                "settings.json",
            ]
        )
    with pytest.raises(SystemExit):
        parser().parse_args(["--campaign", "design", "--workers", "0"])
    assert provider_failed({"provider_ok": False, "error": "HTTP 503"})
    wall = {"provider_ok": False, "error": "context_length_exceeded"}
    assert context_failed(wall) and not provider_failed(wall)
    assert not provider_failed(wall | {"failure_kind": "provider"})
    assert context_failed(
        {"error": "The agent context cannot be assembled: schema uses 5000 > 4500 tokens."}
    )
    assert not provider_failed({"passed": False}) and not context_failed({"passed": False})
    base = {
        "task": "test",
        "fingerprint": "fingerprint",
        "reasons": [],
        "turns": 0,
        "calls": [],
        "context": [],
    }
    result = summarise(
        [
            base | {"passed": True},
            base | {"passed": False, "failure_kind": "provider"},
            base | {"passed": False, "failure_kind": "context"},
            base | {"passed": False},
        ]
    )
    assert result["served_pass_rate"] == 0.5
    assert result["provider_failed"] == result["context_failed"] == 1


def test_local_gate_precedes_settings_or_model_access(capsys, monkeypatch):
    from harness_eval import main

    from gaiavfx.infrastructure.assistant_gateway import AssistantGateway

    monkeypatch.setattr(AssistantGateway, "__init__", lambda *a: pytest.fail("Model must not load"))
    with pytest.raises(SystemExit):
        main(["--campaign", "forbidden", "--backend", "local"])
    assert "pass --allow-gpu after the user's go-ahead" in capsys.readouterr().err


def test_quality_runner_reviews_an_already_selected_segment(tmp_path):
    from harness_eval_quality import complete_review
    from harness_eval_tasks import close

    from gaiavfx.modules.editing.selections.service import SelectionService

    task = next(t for t in TASKS if t.id == "plan-revise-and-render")
    work, setup = task.build(tmp_path)
    try:
        complete_review(work, setup["fixture_render"])
        with work.db.transaction() as session:
            selected = SelectionService().current(
                session, setup["movie_id"], setup["first_segment_id"]
            )
            assert selected["authority"] == "user"
    finally:
        close(work)


@pytest.mark.parametrize("knowledge", [True, False])
def test_quality_rubric_with_scripted_p1(tmp_path, knowledge):
    from harness_eval import summarise
    from harness_eval_scripts import IMAGE_NOTES, STEP_DONE, STORY_PLAN, action, describe, storyline
    from harness_eval_tasks import QUALITY_TASKS

    result = run_task(
        QUALITY_TASKS[0],
        [
            describe,
            IMAGE_NOTES,
            STORY_PLAN,
            storyline,
            STEP_DONE,
            action(
                "say",
                text="Use a tracking shot and a close-up." if knowledge else "Drafted three beats.",
            ),
        ],
        tmp_path,
    )
    assert result["rubric"] == {
        "plan_validity": True,
        "structure_sanity": True,
        "knowledge_use": knowledge,
        "approvals_respected": True,
        "honest_refusal": True,
    }
    assert result["passed"] == knowledge
    assert len(result["approvals"]) == 1
    summary = summarise([result | {"model": "test-model"}])
    assert summary["models"]["test-model"]["rubric_rate"] == (1 if knowledge else 0.8)
    assert summary["results"][0]["rubric"] == result["rubric"]


@pytest.mark.parametrize("backend", ["proxy", "local"])
def test_real_backend_campaign_on_cpu_gateway(tmp_path, monkeypatch, backend):
    import harness_eval_live
    from harness_eval import main
    from harness_eval_scripts import action

    from gaiavfx.application.harness import model as model_module
    from gaiavfx.application.harness.actions import inlined

    events = []

    class Gateway:
        def __init__(self, credentials):
            self.replies = iter(["analyze_people", None])

        def run(self, settings, *args, **kwargs):
            events.append(settings["assistant_agent_connection"])
            reply = next(self.replies)
            if reply is None:
                return action(
                    "ask_human",
                    question="Which attendant, person 1 or 2 on the numbered contact sheet?",
                )
            if isinstance(reply, dict):
                return reply
            schema = inlined(kwargs["response_schema"])
            branch = next(b for b in schema["oneOf"] if b["properties"]["tool"]["const"] == reply)
            ids = branch["properties"]["input"]["properties"]["asset_id"]["enum"]
            kind = "image" if reply == "describe_reference" else "video"
            text = json.dumps(kwargs["messages"])
            asset_id = next(asset_id for asset_id in ids if f"[attached {kind} {asset_id} " in text)
            return action(reply, asset_id=asset_id)

        def stop(self):
            pass

    monkeypatch.setattr(harness_eval_live, "AssistantGateway", Gateway)
    monkeypatch.setattr(model_module, "resource_identity", lambda settings: {})
    monkeypatch.setenv("GAIAVFX_GPU_LOCK", str(tmp_path / "fake-gpu.lock"))
    # Even /free is stubbed: this test never contacts ComfyUI or starts a process.
    from gaiavfx.infrastructure.comfy import ComfyGateway

    monkeypatch.setattr(ComfyGateway, "request", lambda *a, **kw: {})
    settings = (
        {"assistant_agent_connection": "local"}
        if backend == "local"
        else {
            "assistant_agent_connection": "test",
            "assistant_connections": [
                {
                    "id": "test",
                    "name": "CPU stub",
                    "model": "stub",
                    "base_url": "http://unused.invalid",
                }
            ],
        }
    )
    config = tmp_path / "settings.json"
    config.write_text(json.dumps(settings))
    out = tmp_path / "campaign"
    assert (
        main(
            [
                "--campaign",
                "cpu",
                "--backend",
                backend,
                "--allow-gpu",
                "--tasks",
                "P3",
                "--settings",
                str(config),
                "--repeats",
                "2",
                "--workers",
                "2",
                "--out",
                str(out),
            ]
        )
        == 0
    )
    assert len(events) == 4  # two analysis reads followed by numbered disambiguation
    summary = json.loads((out / "summary.json").read_text())
    assert summary["rubric_rate"] == summary["served_pass_rate"] == 1
    assert all(r["classification"] == "served" for r in summary["results"])
    assert len((out / "runs.jsonl").read_text().splitlines()) == 2


@pytest.mark.parametrize("case", ["P1", "E13", "provider", "context", "malformed"])
def test_recorded_adapter_approval_reviews_and_failure_classes(tmp_path, case):
    from types import SimpleNamespace

    from harness_eval_live import RecordedAssistant
    from harness_eval_scripts import (
        IMAGE_NOTES,
        SAY,
        STEP_DONE,
        STORY_PLAN,
        action,
        describe,
        storyline,
    )
    from harness_eval_tasks import QUALITY_TASKS

    from gaiavfx.shared.errors import ApplicationError

    task = (
        next(t for t in TASKS if t.id == "plan-propose-approve-execute")
        if case == "E13"
        else QUALITY_TASKS[0]
    )
    settings = {
        "assistant_agent_connection": "cpu",
        "assistant_connections": [
            {"id": "cpu", "name": "CPU", "model": "stub", "base_url": "http://unused.invalid"},
        ],
    }
    sequence = (
        GOOD[task.id]
        if case == "E13"
        else [
            describe,
            IMAGE_NOTES,
            STORY_PLAN,
            storyline,
            STEP_DONE,
            action("say", text="A tracking shot leads into a close-up."),
        ]
    )

    def factory(work, setup, recorder):
        replies = iter(sequence)

        def run(*args, **kwargs):
            if case in {"provider", "context", "malformed"}:
                raise ApplicationError(
                    {
                        "provider": "HTTP 503",
                        "context": "Request budget 100 exceeds context 80",
                        "malformed": "The assistant returned invalid JSON. Nothing was applied.",
                    }[case]
                )
            entry = next(replies, SAY)
            raw = entry(work, setup) if callable(entry) else entry
            return raw | {"metrics": {"budget": {"image_count": 1}, "usage": {"prompt_tokens": 20}}}

        return RecordedAssistant(SimpleNamespace(run=run), None, recorder)

    result = run_task(task, (), tmp_path, model_factory=factory, settings=settings)
    if case in {"P1", "E13"}:
        assert result["passed"], result
        assert result["classification"] == "served"
        assert result["model_fingerprint"]
        assert result["calls"][0]["metrics"]["budget"] == {"image_count": 1}
    else:
        assert not result["passed"]
        assert result["classification"] == ("served" if case == "malformed" else case + "_failed")
        assert len(result["calls"]) == (2 if case == "malformed" else 1)
