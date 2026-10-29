"""Consent, immutable grants, revision conflicts and explicit plan progress."""

import harness_support
import pytest
from harness_support import action, run

from gaiavfx.application.harness.actions import parse_action
from gaiavfx.application.harness.session import AgentSession
from gaiavfx.application.harness.tools import applicable_tools
from gaiavfx.application.harness.tools.edits import EditCast
from gaiavfx.application.harness.tools.picks import PickProvisional
from gaiavfx.application.harness.tools.plan import ProposePlan
from gaiavfx.application.harness.tools.prepare import PrepareCut
from gaiavfx.application.harness.tools.render import RenderCandidates
from gaiavfx.shared.errors import ApplicationError

kernel = harness_support.kernel


def propose(kind="edit", **kwargs):
    return action(
        "propose_plan",
        goal="Improve the movie",
        steps=[{"kind": kind, "title": "Improve the movie", **kwargs}],
    )


def context(k):
    row = k.harness.read(k.id)
    return k.harness.context(
        row, AgentSession.model_validate(row["document"]), k.harness.turns(k.id)
    )


def test_default_plan_mode_grant_survives_failure_and_cancel(kernel):
    k = kernel
    assert k.row["document"]["version"] == 2 and k.row["document"]["plan_mode"]
    k.settings.update(agent_autonomy="off", agent_budget_jobs=0)
    row = run(k, propose())
    assert row["state"] == "waiting_human"
    assert k.harness.payload(row)["activity"] == "awaiting_approval"
    with pytest.raises(ApplicationError, match="Agent session changed") as stale:
        k.harness.approve_plan(k.id, row["revision"] - 1)
    assert stale.value.status == 409
    approved = k.harness.approve_plan(k.id, row["revision"])
    plan = approved["document"]["plan"]
    assert plan["grant"] == {
        "budget_jobs": 0,
        "step_kinds": ["edit"],
        "approved_turn": 2,
        "reserved_at_approval": 0,
    }
    k.harness.request_cancel(k.id)
    assert k.runner.run_once()
    assert k.harness.read(k.id)["document"]["plan"] == plan
    assert k.harness.read(k.id)["state"] == "stopped"

    def fail(*args):
        raise ApplicationError("Provider unavailable")

    assert run(k, fail)["state"] == "failed"
    assert k.harness.read(k.id)["document"]["plan"] == plan
    row = run(
        k,
        action(
            "edit_cast",
            op="add",
            expected_revision=1,
            name="Walker",
            description="Storm lover",
            keeps=[],
        ),
        action("plan_step", step=0, status="done"),
        action("say", text="Done"),
    )
    assert row["document"]["plan"]["state"] == "done"
    assert row["document"]["plan"]["steps"][0]["receipts"][0]["command"] == "add_subject"
    assert any(
        "Plan " in entry["text"] and "done" in entry["text"]
        for entry in row["document"]["board"]["decisions"]
    )


def test_revision_note_in_message_changes_plan_and_reject_archives(kernel):
    k = kernel
    first = run(k, propose())
    original = first["document"]["plan"]
    k.harness.post_message(k.id, "Just answer instead.", "revise")
    assert k.harness.turns(k.id)[-1]["action"]["plan_revision_of"] == original["id"]
    k.model.replies = iter([propose(), propose("answer")])
    k.runner.run_once()
    turns = k.harness.turns(k.id)
    assert "plan_unchanged" in turns[-2]["result"]["discrepancy"]
    row = k.harness.read(k.id)
    assert row["document"]["plan"]["revision"] == 2
    assert row["document"]["plan"]["id"] == original["id"]
    assert row["document"]["plan_history"][-1]["state"] == "revised"
    rejected = k.harness.reject_plan(k.id, row["revision"], "Not now.")
    assert rejected["state"] == "idle" and rejected["document"]["plan"] is None
    assert rejected["document"]["plan_history"][-1]["note"] == "Not now."
    # The transcript names the revision, never the raw plan id (F6).
    line = k.harness.turns(k.id)[-1]["action"]["message"]
    assert line == "The user rejected plan revision 2. Not now." and original["id"] not in line
    with pytest.raises(ApplicationError):
        k.harness.approve_plan(k.id, rejected["revision"])


def test_plan_controls_reject_wrong_states_empty_note_and_bound_history(kernel):
    k = kernel
    row = run(k, propose())
    with pytest.raises(ApplicationError) as empty:
        k.harness.revise_plan(k.id, row["revision"], " ")
    assert empty.value.status == 422
    revised = k.harness.revise_plan(k.id, row["revision"], "Answer please.")
    with pytest.raises(ApplicationError):
        k.harness.approve_plan(k.id, revised["revision"])
    k.model.replies = iter([propose("answer")])
    k.runner.run_once()
    for index in range(10):
        row = k.harness.read(k.id)
        k.harness.revise_plan(k.id, row["revision"], f"Revision {index}")
        k.model.replies = iter(
            [
                action(
                    "propose_plan",
                    goal=f"Goal {index}",
                    steps=[{"kind": "answer", "title": "Answer"}],
                )
            ]
        )
        k.runner.run_once()
    row = k.harness.read(k.id)
    assert len(row["document"]["plan_history"]) == 8
    disabled = k.harness.set_plan_mode(k.id, row["revision"], False)
    assert disabled["document"]["plan"] is None
    assert disabled["document"]["plan_history"][-1]["state"] == "superseded"
    line = k.harness.turns(k.id)[-1]["action"]["message"]
    assert line.startswith("The user turned plan mode off; plan revision ")
    assert disabled["document"]["plan_history"][-1]["id"] not in line
    with k.db.transaction() as session:
        k.store.update_session(
            session, k.id, disabled["revision"], state="running", document=disabled["document"]
        )
    for command, args in (
        (k.harness.set_plan_mode, [True]),
        (k.harness.reject_plan, []),
        (k.harness.revise_plan, ["Note"]),
    ):
        with pytest.raises(ApplicationError) as running:
            command(k.id, k.harness.read(k.id)["revision"], *args)
        assert running.value.status == 409


def test_plan_validation_and_ignored_provider_pins(kernel):
    k = kernel
    ctx, tool = context(k), ProposePlan()
    cases = [
        ([{"kind": "edit", "title": "Edit", "jobs": 1}], "jobs_on_wrong_kind"),
        ([{"kind": "edit", "title": "Edit", "target": "invented"}], "unknown_target"),
        ([{"kind": "render", "title": "Render", "jobs": 1}], "review_missing"),
        ([{"kind": "prepare", "title": "Prepare", "jobs": 9}], "step_too_large"),
        (
            [{"kind": "prepare", "title": "Prepare", "jobs": 8}] * 6
            + [{"kind": "prepare", "title": "Prepare", "jobs": 3}],
            "plan_too_large",
        ),
    ]
    for steps, reason in cases:
        result = tool.run(ctx, tool.Input(goal="Goal", steps=steps))
        assert not result.ok and reason in result.discrepancy
    with pytest.raises(ApplicationError, match="target"):
        parse_action(action("propose_plan", goal="Goal", steps=cases[1][0]), applicable_tools(ctx))
    schema = tool.narrow(ctx)
    assert schema["properties"]["steps"]["maxItems"] == 12
    assert schema["properties"]["steps"]["items"]["properties"]["target"]["enum"] == [
        "",
        k.movie["id"],
    ]


def test_direct_tool_execution_rechecks_grant_and_reviews_cannot_be_skipped(kernel):
    k = kernel
    edit = EditCast()
    value = edit.Input(
        op="add", expected_revision=1, name="Walker", description="Storm lover", keeps=[]
    )
    assert "Not in the approved plan" in edit.run(context(k), value).discrepancy
    row = run(k, propose("answer"))
    k.harness.approve_plan(k.id, row["revision"])
    assert "Not in the approved plan" in edit.run(context(k), value).discrepancy
    for tool, supplied in (
        (PrepareCut(), {"cut_id": "cut"}),
        (RenderCandidates(), {"cut_id": "cut", "seeds": [1]}),
        (PickProvisional(), {"review_set_id": "review", "letter": "A", "expected_revision": 0}),
    ):
        assert (
            "Not in the approved plan" in tool.run(context(k), tool.Input(**supplied)).discrepancy
        )
    row = k.harness.read(k.id)
    k.harness.set_plan_mode(k.id, row["revision"], False)
    assert edit.run(context(k), value).ok
    row = k.harness.read(k.id)
    k.harness.set_plan_mode(k.id, row["revision"], True)
    row = run(
        k,
        action(
            "propose_plan",
            goal="Review takes",
            steps=[{"kind": "render", "title": "Render"}, {"kind": "review", "title": "Review"}],
        ),
    )
    k.harness.approve_plan(k.id, row["revision"])
    k.model.replies = iter(
        [
            action("plan_step", step=1, status="skipped"),
            action("say", text="Your review is required"),
        ]
    )
    k.runner.run_once()
    assert k.harness.read(k.id)["document"]["plan"]["steps"][1]["status"] == "pending"
    assert "user must complete" in k.harness.turns(k.id)[-2]["result"]["discrepancy"]


def test_version_one_documents_validate_with_safe_defaults():
    doc = AgentSession.model_validate({"version": 1})
    assert doc.plan_mode and doc.plan is None and doc.model_retries == 0


def test_plan_budget_is_the_grant_plus_prior_reservations_without_reading_settings():
    from gaiavfx.application.harness.loop import Harness
    from gaiavfx.application.harness.session import Plan, PlanGrant, PlanStep

    def forbidden_settings():
        raise AssertionError("Plan mode read autonomy/job settings")

    harness = Harness.__new__(Harness)
    harness.settings = forbidden_settings
    document = AgentSession(
        plan=Plan(
            id="plan",
            revision=1,
            state="executing",
            goal="Prepare",
            proposed_turn=0,
            steps=[PlanStep(kind="prepare", title="Prepare", jobs=3)],
            budget_jobs=3,
            grant=PlanGrant(
                budget_jobs=3, step_kinds=["prepare"], approved_turn=1, reserved_at_approval=4
            ),
        )
    )
    assert harness._authority(document) == ("off", 7)
    document.plan = None
    assert harness._authority(document) == ("off", 0)
