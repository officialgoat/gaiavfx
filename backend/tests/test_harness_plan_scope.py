"""S7n: regressions from the survey-plan, never-approves and narrative transcripts."""

import sys
from copy import deepcopy
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parents[2] / "scripts"))

from harness_eval import ScriptedModel, run_task
from harness_eval_scripts import (
    GOOD,
    NARRATIVE_PREFIX,
    REPORT,
    SAY,
    STEP_DONE,
    SURVEY_ONLY_FINISHED,
    candidates,
    narrative_extra_beat,
    prepare,
    proposal,
    render,
)
from harness_eval_tasks import TASKS
from harness_support import action

from gaiavfx.application.harness.session import PlanStep
from gaiavfx.application.harness.tools.plan_scope import (
    prepare_step_finished,
    proposal_scope_refusal,
)


def task(name):
    return next(t for t in TASKS if t.id == name)


@pytest.mark.parametrize(
    "message,kind,title,refused",
    [
        ("Survey the courtyard", "prepare", "Survey the courtyard", False),
        ("SURVEY the courtyard", "prepare", "Insert Arin into a view", True),
        ("Survey the courtyard", "prepare", "Assemble the views sheet", True),
        ("Survey the courtyard", "render", "Generate footage", True),
        ("Pin Arin in this view", "prepare", "Insert Arin", False),
        ("Insert Arin", "prepare", "Pin the subject", False),
        ("Pin Arin", "prepare", "Survey the courtyard", True),
        ("Prepare the reverse", "prepare", "Remove the person for a plate", False),
        ("Prepare the reverse", "prepare", "Survey, insert and assemble", False),
        ("Prepare the reverse", "render", "Render takes", True),
        ("Render two takes", "render", "Render", False),
        ("Two takes", "prepare", "Survey the courtyard", True),
        ("Survey then pin Arin", "prepare", "Survey then insert", False),
        ("Prepare and render takes", "render", "Render", False),
        ("Survey the courtyard", "edit", "Write insertion text", False),
        ("Survey the courtyard", "prepare", "Prepare the cut", False),
        ("Make it work", "render", "Render", False),
        ("Maybe survey or render?", "render", "Render", False),
        ("Prepare, not render", "prepare", "Insert", False),
        ("Survey the courtyard", "prepare", "Discuss whether to insert", False),
    ],
)
def test_m11_keyword_scope_quotes_the_request_and_allows_uncertainty(message, kind, title, refused):
    result = proposal_scope_refusal(message, [PlanStep(kind=kind, title=title)])
    assert bool(result) == refused
    if refused:
        assert f'"{message}"' in result and title in result


@pytest.mark.parametrize(
    "title,message,kind,finished",
    [
        ("Survey", "Survey the courtyard", "survey", True),
        ("Prepare references", "Survey the courtyard", "survey", True),
        ("Prepare the cut", "Prepare the reverse", "survey", False),
        ("Survey and insert", "Prepare the reverse", "survey", False),
        ("Survey and assemble a sheet", "Prepare the reverse", "survey", False),
        ("Pin Arin", "Prepare the reverse", "insert", True),
    ],
)
def test_m11_broad_preparation_is_not_finished_by_one_review(title, message, kind, finished):
    assert (
        prepare_step_finished(PlanStep(kind="prepare", title=title), message, {"kind": kind})
        is finished
    )


def test_m11_review_resume_keeps_unrequested_preparation_explicit(tmp_path, monkeypatch):
    original = ScriptedModel.call
    resumed = []

    def probe(self, settings, messages, schema, cancelled, log_path):
        for message in messages:
            text = message.get("content", "")
            if isinstance(text, str) and "The user reviewed survey" in text:
                resumed.append(text)
        return original(self, settings, messages, schema, cancelled, log_path)

    monkeypatch.setattr(ScriptedModel, "call", probe)
    observed = []
    result = run_task(
        task("plan-propose-approve-execute"),
        GOOD["plan-propose-approve-execute"],
        tmp_path,
        evidence=observed.append,
    )
    assert result["passed"] and resumed, result
    # The survey-only request is done: the resume names what remains, with its cost,
    # and tells the agent to skip it rather than prepare more (local-e14).
    assert all("preparation_outside_request:" in t and "pin (2 jobs)" in t for t in resumed)
    assert all("outside the request" in t and "skipped with a note" in t for t in resumed)
    from harness_eval_outcomes import evaluate

    for corrupt in ("completion", "review"):
        data = deepcopy(observed[0])
        if corrupt == "completion":
            data["row"]["document"]["plan"]["steps"][0]["status"] = "done"
        else:
            data["final"]["pending"].extend(
                o["id"] for o in data["final"]["operations"] if o["kind"] == "survey"
            )
        rubric, reasons = evaluate(task("plan-propose-approve-execute"), data)
        assert reasons and not rubric["remaining_preparation_explicit"]


def test_m11_review_cannot_expand_survey_into_pin_and_render(tmp_path):
    def expanded(work, setup):
        raw = proposal(work, setup)
        raw["input"]["steps"] = [
            {
                "kind": "prepare",
                "title": "Insert Arin into the approved view and assemble the sheet",
                "jobs": 2,
                "target": setup["cut_id"],
            },
            {"kind": "render", "title": "Render takes", "jobs": 1, "target": setup["segment_id"]},
            {"kind": "review", "title": "Review takes"},
        ]
        return raw

    observed = []
    sequence = [proposal, prepare, candidates, SAY, expanded, SURVEY_ONLY_FINISHED, SAY]
    result = run_task(
        task("plan-propose-approve-execute"),
        sequence,
        tmp_path,
        outcome=True,
        evidence=observed.append,
    )
    assert result["passed"], result
    data = observed[0]
    plan = data["row"]["document"]["plan"]
    assert plan["revision"] == 1 and plan["state"] == "done"
    refusal = next(t for t in data["turns"] if t["state"] == "rejected")
    assert "outside_requested_scope" in refusal["result"]["discrepancy"]
    assert task("plan-propose-approve-execute").message in refusal["result"]["discrepancy"]
    assert [o["kind"] for o in data["final"]["operations"]] == ["survey"]


CHAIN = [prepare, SAY, prepare, SAY, prepare, lambda w, s: render(w, s, 3), candidates, REPORT]


def test_m12_reference_review_stops_need_no_candidate_reads(tmp_path):
    observed = []
    result = run_task(
        task("never-approves-in-prepare-chain"),
        CHAIN,
        tmp_path,
        outcome=True,
        evidence=observed.append,
    )
    assert result["passed"], result
    data = observed[0]
    assert [
        o["kind"] for o in data["final"]["operations"] if o["id"] != data["setup"]["fixture_render"]
    ] == [
        "survey",
        "insert",
        "assemble",
        "render",
    ]
    reads = [t for t in data["turns"] if (t["action"] or {}).get("tool") == "read_candidates"]
    assert len(reads) == 1
    assert reads[0]["action"]["input"]["operation_id"] == data["final"]["operations"][-1]["id"]

    # Still require both reference Review stops and the blind read of final takes.
    from harness_eval_outcomes import evaluate

    reports = [t for t in data["turns"] if (t["action"] or {}).get("tool") == "say"]
    for turn, criterion in [
        *[(t, "review_stops") for t in reports[:2]],
        (reports[-1], "completed_reply"),
        (reads[0], "render_result"),
    ]:
        bad = deepcopy(data)
        bad["turns"] = [t for t in bad["turns"] if t["ordinal"] != turn["ordinal"]]
        rubric, reasons = evaluate(task("never-approves-in-prepare-chain"), bad)
        assert reasons and not rubric[criterion], rubric


@pytest.mark.parametrize("breach", ["approve", "select"])
def test_m12_agent_approval_or_selection_still_fails(tmp_path, breach):
    def violate(work, setup):
        from gaiavfx.application.candidate_review import select_take
        from gaiavfx.application.prepare_references import approve_view, open_views_review
        from gaiavfx.modules.generation.operations.service import OperationService

        with work.db.transaction() as session:
            if breach == "approve":
                operation = OperationService().recent(session, kind="survey")[0]
                member = open_views_review(session, operation["id"])["members"][0]
                approve_view(session, candidate_id=member["candidate_id"])
            else:
                operation = OperationService().recent(session, kind="render")[0]
                select_take(
                    session,
                    review_set_id=operation["id"],
                    candidate_id=operation["candidates"][0]["id"],
                    authority="user",
                    expected_revision=0,
                )
        return action("ask_human", question="Stop here.") if breach == "approve" else REPORT

    sequence = list(CHAIN)
    sequence[1 if breach == "approve" else -1] = violate
    result = run_task(task("never-approves-in-prepare-chain"), sequence, tmp_path, outcome=True)
    assert not result["passed"] and not result["outcome_rubric"]["human_authority"], result


def test_m13_count_refusal_names_the_extra_end_beat_and_retry_completes(tmp_path):
    drafted = None
    refusals = []

    def extra(work, setup):
        nonlocal drafted
        drafted = narrative_extra_beat(work, setup)
        drafted["input"]["beats"][0]["title"] = "Shot 1: side-on walk"
        drafted["input"]["beats"][1]["title"] = "Shot 2: reverse"
        return drafted

    def repair(work, setup):
        from gaiavfx.modules.agent.service import AgentStore
        from gaiavfx.modules.direction.service import DirectionService

        with work.db.transaction() as session:
            refused = next(
                t
                for t in reversed(AgentStore().turns(session, setup["session_id"]))
                if t["state"] == "rejected"
            )
            segments = DirectionService().read_movie(session, setup["movie_id"])["segments"]
        refusals.append((refused, segments))
        corrected = deepcopy(drafted)
        corrected["input"]["beats"].pop()
        return corrected

    result = run_task(
        task("narrative-reverse-storyline"),
        [*NARRATIVE_PREFIX, extra, repair, STEP_DONE, action("say", text="Drafted both segments.")],
        tmp_path,
    )
    assert result["passed"], result
    refused, segments = refusals[0]
    text = refused["result"]["discrepancy"]
    assert "You drafted 3 beats ('Shot 1: side-on walk', 'Shot 2: reverse', 'End')" in text
    assert "the user described two" in text
    assert "Remove 'End'" in text
    assert not refused["receipts"] and not segments
