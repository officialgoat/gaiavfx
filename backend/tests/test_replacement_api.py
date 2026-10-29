"""HTTP replacement orchestration and existing OpenAPI name stability."""

import pytest
from fastapi.testclient import TestClient
from test_production_api import AUTH, MIGRATIONS
from test_replacement_runner import (
    drain,
)
from test_replacement_runner import work as work  # noqa: PLC0414 - pytest fixture export

from gaiavfx.api.app import create_app


@pytest.mark.parametrize("renders", [1, 4])
def test_people_plan_render_review_approval_http(work, renders):
    db, _, runner, assets, _ = work
    app = create_app(db.root, MIGRATIONS, "test-token", enable_runner=False)
    app.state.operations.comfy = runner.comfy
    app.state.operations.poll_seconds = 0
    with TestClient(app, base_url="http://localhost", headers=AUTH) as client:
        body = {
            "source_asset_id": assets[0],
            "start": 1,
            "end": 52,
            "concepts": ["person"],
            "request_key": "http-analysis",
        }
        invalid = client.post(
            "/api/production/people-analyses",
            json={**body, "concepts": ["please detect everyone now"]},
        )
        assert invalid.status_code == 422
        created = client.post("/api/production/people-analyses", json=body)
        assert created.status_code == 201, created.text
        assert client.post("/api/production/people-analyses", json=body).status_code == 200
        drain(app.state.operations)
        analysed = client.get("/api/production/people-analyses", params={"asset_id": assets[0]})
        assert analysed.status_code == 200, analysed.text
        report = analysed.json()[0]["report"]
        request = {
            "renders": renders,
            "source_asset": assets[0],
            "analysis_operation": created.json()["id"],
            "performers": [{"track_id": report["tracks"][0]["id"], "target_asset": assets[1]}],
        }
        preview = client.post("/api/production/replacement-plans", json=request)
        assert preview.status_code == 200 and preview.json()["crop_preview"], preview.text
        assert preview.json()["arm"]["seed_count"] == min(renders, 2)
        for route in ("replacement-plans", "replacements"):
            for invalid_renders in (0, 5, 1.5):
                invalid = client.post(
                    f"/api/production/{route}", json={**request, "renders": invalid_renders}
                )
                assert invalid.status_code == 422
        started = client.post(
            "/api/production/replacements",
            json={**request, "budget_limit": 4, "request_key": "http-replacement"},
        )
        assert started.status_code == 201, started.text
        assert started.json()["budget_reserved"] == renders
        recorded = client.get(f"/api/production/replacements/{started.json()['id']}/plan")
        assert recorded.status_code == 200, recorded.text
        assert recorded.json() == preview.json()
        drain(app.state.operations)
        review = client.post(f"/api/production/replacements/{started.json()['id']}/review")
        assert review.status_code == 200, review.text
        operation = client.get(f"/api/production/operations/{started.json()['id']}").json()
        members = review.json()["members"]
        for member in members:
            assert member["replacement_checks"]
            assert (
                not {"seed", "arm", "round", "job_id", "operation_id", "request", "graph"}
                & member.keys()
            )
            assert all(
                set(check) <= {"check", "verdict", "measured", "advice", "reason", "calibrated"}
                for check in member["replacement_checks"]
            )
        review_id = review.json()["id"]
        assert client.get(f"/api/production/reviews/{review_id}").json()["members"] == members
        saved = client.put(
            f"/api/production/reviews/{review_id}/ranking",
            json={
                "expected_revision": review.json()["ranking"]["revision"],
                "tiers": [[m["candidate_id"] for m in members]],
                "usable_tiers": 1,
            },
        )
        assert saved.status_code == 200, saved.text
        revealed = client.get(f"/api/production/reviews/{review_id}/reveal")
        assert revealed.status_code == 200, revealed.text
        with db.transaction() as session:
            from gaiavfx.modules.generation.operations.service import OperationService

            full = OperationService().operation(session, started.json()["id"])
        candidates = {c["id"]: c for c in full["candidates"]}
        jobs = {j["id"]: j for j in full["jobs"]}
        for member in revealed.json()["members"]:
            job = jobs[candidates[member["candidate_id"]]["job_id"]]
            arm = job["snapshot"]["step_spec"]["arm"]
            assert member["round"] == arm["round"] + 1
            assert member["arm"] == {k: arm[k] for k in ("treatment", "framing")}
            assert member["seed"] == job["seed"]
            assert member["replacement_checks"]
        approval = client.post(
            "/api/production/replacements/approve",
            json={"candidate_id": operation["candidates"][0]["id"]},
        )
        assert approval.status_code == 200, approval.text
        assert approval.json()["provenance"]["type"] == "character_replacement"
        names = app.openapi()["components"]["schemas"]
        assert "OperationDTO" in names and "CandidateInput" in names and "SurveyInput" in names
        assert all("__" not in name for name in names)


def test_replacement_request_limits_and_typed_plan_schema():
    import pytest
    from pydantic import ValidationError

    from gaiavfx.api.production import (
        ReplacementPeopleAnalysisInput,
        ReplacementPlanDTO,
        ReplacementPlanInput,
    )

    body = {
        "source_asset": "a",
        "analysis_operation": "op",
        "performers": [{"track_id": "p", "target_asset": "t"}],
    }
    for key, value in (
        ("performers", body["performers"] * 3),
        ("required_context", [{"x0": 0, "y0": 0, "x1": 1, "y1": 1}] * 17),
        ("avoided_regions", [{"x0": 0, "y0": 0, "x1": 1, "y1": 1}] * 17),
        ("silent_intervals", [(0.0, 1.0)] * 9),
    ):
        with pytest.raises(ValidationError):
            ReplacementPlanInput.model_validate({**body, key: value})
    with pytest.raises(ValidationError):
        ReplacementPeopleAnalysisInput(
            source_asset_id="a", start=0, end=10, concepts=("person",) * 9
        )
    schema = ReplacementPlanDTO.model_json_schema()
    assert schema["properties"]["facts"]["$ref"].endswith("ReplacementFactsDTO")
    assert schema["properties"]["gaps"]["items"]["$ref"].endswith("ReplacementGapDTO")
    assert any(
        row.get("$ref", "").endswith("ReplacementArmDTO")
        for row in schema["properties"]["arm"]["anyOf"]
    )


def test_approval_missing_candidate_and_missing_job_are_typed(monkeypatch):
    import pytest

    import gaiavfx.application.replace_character as replacement
    from gaiavfx.shared.errors import ApplicationError

    def missing(*args):
        raise ApplicationError("Candidate not found.", 404)

    monkeypatch.setattr(replacement.operations, "candidate", missing)
    with pytest.raises(ApplicationError) as error:
        replacement.approve_replacement(None, "missing")
    assert error.value.status == 404
    monkeypatch.setattr(
        replacement.operations, "candidate", lambda *args: {"operation_id": "op", "job_id": "gone"}
    )
    monkeypatch.setattr(
        replacement.operations,
        "operation",
        lambda *args: {"jobs": [], "kind": "character_replacement"},
    )
    with pytest.raises(ApplicationError) as error:
        replacement.approve_replacement(None, "candidate")
    assert error.value.status == 409


def test_api_derives_distinct_selectors_for_two_people_in_one_third(work, tmp_path):
    import json

    from test_replacement_review import finish
    from test_replacement_runner import analysed

    from gaiavfx.modules.generation.operations.lifecycle import JobSpec
    from gaiavfx.modules.generation.operations.service import OperationService

    db, library, _, assets, _ = work
    _, report, _ = analysed(work)
    people = [t for t in report["tracks"] if t["concept"] == "person"]
    for index, track in enumerate(people):
        track["box"] = {"x0": 20 + index * 75, "x1": 80 + index * 75, "y0": 30, "y1": 300}
        track["presence"] = ["present"] * 51
    path = tmp_path / "same-third.json"
    path.write_text(json.dumps(report))
    prepared = library.prepare_blob(path, "analysis_report", {})
    service = OperationService()
    with db.transaction() as session:
        artifact = library.register_artifact(session, prepared)
        analysis = service.create(
            session,
            kind="person_analysis",
            request={"source_asset": assets[0], "start": 1, "end": 52, "concepts": ["person"]},
            jobs=[JobSpec("analysis", "local", "synthetic", step="people_report")],
            budget_limit=None,
        )
        finish(
            service,
            session,
            analysis["jobs"][0],
            [{"output_key": "people", "artifact_id": artifact["id"]}],
        )
    app = create_app(db.root, MIGRATIONS, "test-token", enable_runner=False)
    with TestClient(app, base_url="http://localhost", headers=AUTH) as client:
        response = client.post(
            "/api/production/replacements",
            json={
                "source_asset": assets[0],
                "analysis_operation": analysis["id"],
                "renders": 1,
                "performers": [{"track_id": p["id"], "target_asset": assets[1]} for p in people],
            },
        )
        assert response.status_code == 201, response.text
        performers = response.json()["request"]["facts"]["performers"]
        assert [p["position"] for p in performers] == [
            "on the left of the pair",
            "on the right of the pair",
        ]
        assert all(p["role"] == "performer" for p in performers)
