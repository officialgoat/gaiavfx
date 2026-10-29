"""Reference decisions release preparation without granting the agent human authority."""

import json

import harness_support
import pytest
from fastapi.testclient import TestClient
from harness_support import action
from test_production_api import AUTH, setup, source_assets

from gaiavfx.application.prepare_references import reject_reference_review
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService
from gaiavfx.shared.errors import ApplicationError

world = harness_support.world


@pytest.mark.parametrize("purpose,key", [("angle_views", "place_id"), ("insertion", "cut_id")])
def test_rejected_pixels_are_scoped_and_a_new_candidate_can_reuse_them(tmp_path, purpose, key):
    app = setup(tmp_path)
    assets, _ = source_assets(app, tmp_path)
    with app.state.workspace.db.transaction() as session:
        operations, reviews = OperationService(), ReviewService()

        def offer(output, asset=assets[0]):
            operation = operations.create(
                session,
                kind="survey" if purpose == "angle_views" else "insert",
                request={},
                jobs=[JobSpec("image", "local", "fake", step="fixture")],
                budget_limit=None,
            )
            candidate = operations.add_candidate(
                session, operation["jobs"][0]["id"], asset, output, "image"
            )
            return reviews.create(
                session, purpose=purpose, candidate_ids=[candidate["id"]], context={key: "here"}
            )

        first = offer("first")
        reviews.reject_references(
            session, first["id"], expected_revision=1, note="Wrong view", rejected_by="user"
        )
        assert reviews.inactive_reference_assets(
            session, purpose=purpose, context={key: "here"}
        ) == {assets[0]}
        assert not reviews.inactive_reference_assets(
            session, purpose=purpose, context={key: "elsewhere"}
        )
        newer = offer("newer")
        assert newer["members"][0]["candidate_id"] != first["members"][0]["candidate_id"]
        assert not reviews.inactive_reference_assets(
            session, purpose=purpose, context={key: "here"}
        )
        assert reviews.blind(session, first["id"])["rejection"]
        latest = offer("latest", assets[1])
        reviews.reject_references(
            session, latest["id"], expected_revision=1, note="Still wrong", rejected_by="user"
        )
        # Survey supersession must not resurrect older undecided evidence when the
        # newest set is rejected. Pins retain their independent pending reviews.
        assert reviews.inactive_reference_assets(
            session, purpose=purpose, context={key: "here"}
        ) == ({assets[0], assets[1]} if purpose == "angle_views" else {assets[1]})


@pytest.mark.parametrize("purpose", ["plate", "angle_views", "insertion"])
def test_rejection_api_is_required_guarded_durable_and_blind(tmp_path, purpose):
    app = setup(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        assets, _ = source_assets(app, tmp_path)
        with app.state.workspace.db.transaction() as session:
            operations = OperationService()
            operation = operations.create(
                session,
                kind="survey",
                request={},
                jobs=[JobSpec("image", "local", "fake", step="fixture", seed=99171)],
                budget_limit=None,
            )
            candidate = operations.add_candidate(
                session, operation["jobs"][0]["id"], assets[0], "test", "image"
            )
            review = ReviewService().create(
                session, purpose=purpose, candidate_ids=[candidate["id"]], context={}
            )
            with pytest.raises(ApplicationError, match="Only the user"):
                ReviewService().reject_references(
                    session, review["id"], expected_revision=1, note="Wrong", rejected_by="agent"
                )
        url = f"/api/production/reviews/{review['id']}"
        body = {
            "expected_revision": 1,
            "note": "The reverse looks along the gallery, not across the railing.",
        }
        assert client.post(url + "/reject", json=body).status_code == 401
        for note in ("", "   ", "x" * 2001):
            assert (
                client.post(url + "/reject", json=body | {"note": note}, headers=AUTH).status_code
                == 422
            )
        assert (
            client.post(
                url + "/reject", json=body | {"expected_revision": 2}, headers=AUTH
            ).status_code
            == 409
        )
        response = client.post(url + "/reject", json=body, headers=AUTH)
        assert response.status_code == 200, response.text
        rejected = response.json()
        assert rejected["rejection"] == {
            "rejected_by": "user",
            "rejected_at": rejected["rejection"]["rejected_at"],
            "note": body["note"],
            "candidate_ids": [candidate["id"]],
        }
        assert rejected["rejection"]["rejected_at"]
        assert not rejected["ranking"]["tiers"]
        assert rejected["unranked"] == [candidate["id"]]
        assert (
            not {"seed", "job_id", "request", "operation_id", "graph"}
            & rejected["members"][0].keys()
        )
        assert "99171" not in json.dumps(rejected)
        assert client.get(url, headers=AUTH).json() == rejected
        assert client.get(url + "/reveal", headers=AUTH).status_code == 409
        assert client.post(url + "/reject", json=body, headers=AUTH).status_code == 409
        with app.state.workspace.db.transaction() as session:
            ReviewService().reference_approved(session, review["id"], candidate["id"])
        approved = client.get(url, headers=AUTH).json()
        assert not approved.get("rejection")
        assert approved["context"]["reference_rejections"] == [rejected["rejection"]]


def test_run4_wrong_reverse_rejected_resumes_prepare_and_offers_new_survey(world):
    from harness_eval import ScriptedModel, drive
    from harness_eval_scripts import SAY, candidates, prepare
    from harness_eval_tasks import run_idle

    from gaiavfx.application.harness.driver import AgentRunner
    from gaiavfx.application.harness.loop import Harness
    from gaiavfx.application.harness.session import AgentSession
    from gaiavfx.application.harness.tools import applicable_tools
    from gaiavfx.application.harness.world import direction, review_pending
    from gaiavfx.application.prepare_references import approve_view, open_views_review
    from gaiavfx.modules.agent.service import AgentStore

    work, data = world
    model = ScriptedModel(
        [
            action(
                "propose_plan",
                goal="Survey the reverse",
                steps=[
                    {
                        "kind": "prepare",
                        "title": "Survey views",
                        "target": data["cut_id"],
                        "jobs": 1,
                    }
                ],
            ),
            prepare,
            candidates,
            SAY,
            action("prepare_cut", cut_id=data["cut_id"], seed=18),
            candidates,
            SAY,
        ],
        work,
        data,
    )
    harness = Harness(work.db, AgentStore(), model, work.settings)
    runner = AgentRunner(work.db, harness)
    row = harness.open(data["movie_id"])
    data["session_id"] = row["id"]
    harness.post_message(row["id"], "Survey the reverse", "first")
    assert runner.run_once()
    harness.approve_plan(row["id"], harness.read(row["id"])["revision"])
    drive(work, data, harness, runner)
    row = harness.read(row["id"])
    step = row["document"]["plan"]["steps"][0]
    assert step["status"] == "awaiting_user"
    assert not runner.run_once()
    note = "The reverse looks along the gallery; it must look across the railing."
    with work.db.transaction() as session:
        review = ReviewService().blind(session, step["review_of"])
        reject_reference_review(
            session, review["id"], expected_revision=review["revision"], note=note
        )
        assert not review_pending(session, OperationService().operation(session, review["id"]))
    assert harness.resume_reviewed(row["id"])
    row = harness.read(row["id"])
    assert row["document"]["plan"]["steps"][0]["status"] == "pending"
    context = harness.context(
        row, AgentSession.model_validate(row["document"]), harness.turns(row["id"])
    )
    offered = {tool.name for tool, _ in applicable_tools(context)}
    assert "prepare_cut" in offered
    assert not any("approve" in name or "reject_reference" in name for name in offered)
    assert (
        harness.turns(row["id"])[-1]["action"]["message"]
        == f"The user rejected the view candidates: '{note}'; prepare again with corrected inputs or ask"
    )
    # The user corrects the rejected camera geography. This test still requires a
    # fresh job grant; a changed seed alone is no longer an honest retry (S7p).
    corrected_angle = "Camera inside the gallery, looking across the railing into the courtyard."
    with work.db.transaction() as session:
        cut = direction.read_cut(session, data["cut_id"])
        angle = direction.read_angle(session, cut["to_angle_id"])
        direction.update_angle(session, angle["id"], angle["revision"], description=corrected_angle)
    retry = action("prepare_cut", cut_id=data["cut_id"], seed=18)
    model.sequence = iter(
        [
            retry,
            action(
                "propose_plan",
                goal="Survey the corrected reverse",
                steps=[
                    {
                        "kind": "prepare",
                        "title": "Survey corrected views",
                        "target": data["cut_id"],
                        "jobs": 1,
                    }
                ],
            ),
            retry,
            candidates,
            SAY,
        ]
    )
    assert runner.run_once()
    assert any(
        "0 jobs remaining" in (t.get("result") or {}).get("discrepancy", "")
        for t in harness.turns(row["id"])
    )
    # Rejection releases the review; it does not grant another survey job.
    harness.approve_plan(row["id"], harness.read(row["id"])["revision"])
    assert runner.run_once()
    newer_id = harness.read(row["id"])["document"]["waiting_on"]
    assert newer_id and newer_id != review["id"], json.dumps(
        [(t["action"], (t["result"] or {}).get("discrepancy")) for t in harness.turns(row["id"])],
        indent=2,
    )
    run_idle(work, data["fake"])
    with work.db.transaction() as session:
        newer = OperationService().operation(session, newer_id)
        assert newer["kind"] == "survey"
        assert dict(newer["request"]["plan"]["views"])[angle["id"]] == corrected_angle
        assert not review_pending(session, OperationService().operation(session, review["id"]))
        open_views_review(session, newer_id)
        # Reconsidering an earlier rejected set also remains a user action.
        for member in review["members"]:
            approve_view(session, candidate_id=member["candidate_id"])
        assert ReviewService().blind(session, review["id"])["rejection"] is None
