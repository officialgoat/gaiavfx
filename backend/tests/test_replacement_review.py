"""Blind review projections over public-service synthetic observations; no inference."""

import json

import pytest
from fastapi.testclient import TestClient
from test_production_api import AUTH, MIGRATIONS
from test_replacement_runner import work as work  # noqa: PLC0414 - fixture export

from gaiavfx.api.app import create_app
from gaiavfx.modules.direction.application import replacement_policy_text
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.service import OperationService


def finish(service, session, job, outputs):
    for state in ("claimed", "running", "collecting", "succeeded"):
        service.transition(
            session, job["id"], state, **({"outputs": outputs} if state == "succeeded" else {})
        )


def test_blind_errors_calibration_mapping_and_reveal(work, tmp_path):
    db, library, _, assets, _ = work
    service = OperationService()
    checks = [
        {
            "check": "letterbox_difference",
            "verdict": "pass",
            "measured": 0.0,
            "advice": "none",
            "reason": "unchanged",
        },
        {
            "check": "background_geometry",
            "verdict": "unsupported",
            "measured": None,
            "advice": "none",
            "reason": "geometry unmeasured",
        },
    ]
    path = tmp_path / "checks.json"
    path.write_text(json.dumps({"checks": checks}))
    prepared = library.prepare_blob(path, "analysis_report", {})
    with db.transaction() as session:
        artifact = library.register_artifact(session, prepared)
        operation = service.create(
            session,
            kind="character_replacement",
            request={
                "source_asset": assets[0],
                "policy": replacement_policy_text(),
                "facts": {
                    "shot_start": 1,
                    "shot_end": 52,
                    "fps": 24,
                    "performers": [{"track_id": "person"}],
                },
            },
            jobs=[
                JobSpec(
                    "video",
                    "local",
                    "synthetic",
                    step="replacement_composite",
                    seed=87,
                    snapshot={
                        "step_spec": {
                            "face_measurement": True,
                            "arm": {"round": 1, "treatment": "inverted", "framing": "full"},
                        }
                    },
                ),
                JobSpec(
                    "analysis",
                    "local",
                    "synthetic",
                    step="synthetic_face",
                    depends_on=0,
                    snapshot={"face_check": True},
                ),
                JobSpec(
                    "video",
                    "local",
                    "synthetic",
                    step="replacement_composite",
                    seed=42,
                    snapshot={
                        "step_spec": {"arm": {"round": 0, "treatment": "rgb", "framing": "crop"}}
                    },
                ),
            ],
            budget_limit=None,
        )
        jobs = operation["jobs"]
        for job in (jobs[0], jobs[2]):
            finish(service, session, job, [{"output_key": "checks", "artifact_id": artifact["id"]}])
        face_id = jobs[1]["id"]
        service.transition(session, face_id, "claimed")
        service.transition(
            session, face_id, "failed", error=f"staging/{face_id}/remote-secret failed"
        )
        # Candidate insertion order deliberately opposes composite order.
        other = service.add_candidate(session, jobs[2]["id"], assets[0], "composite", "video")
        failed = service.add_candidate(session, jobs[0]["id"], assets[0], "composite", "video")
    app = create_app(db.root, MIGRATIONS, "test-token", enable_runner=False)
    with TestClient(app, base_url="http://localhost", headers=AUTH) as client:
        summary = client.get(f"/api/production/operations/{operation['id']}/summary")
        assert summary.status_code == 200, summary.text
        assert summary.json() == {"id": operation["id"], "kind": "character_replacement"}
        response = client.post(f"/api/production/replacements/{operation['id']}/review")
        assert response.status_code == 200, response.text
        review = response.json()
        assert {k: review["context"][k] for k in ("shot_start", "shot_end", "fps")} == {
            "shot_start": 1,
            "shot_end": 52,
            "fps": 24,
        }
        for url in (
            f"/api/production/replacements/{operation['id']}/review",
            f"/api/production/reviews/{review['id']}",
        ):
            response = client.post(url) if url.endswith("/review") else client.get(url)
            assert response.status_code == 200, response.text
            members = response.json()["members"]
            serialized = json.dumps(members)
            assert face_id not in serialized and "remote-secret" not in serialized
            for member in members:
                assert not {"job_id", "operation_id", "arm", "round", "seed"} & member.keys()
                assert "letterbox_difference" not in serialized
                checks_by_name = {c["check"]: c for c in member["replacement_checks"]}
                assert checks_by_name["background_geometry"]["calibrated"] is True
                if member["candidate_id"] == failed["id"]:
                    assert checks_by_name["arcface_target"]["calibrated"] is False
                    assert checks_by_name["residual_source"]["calibrated"] is True
                    assert (
                        checks_by_name["residual_source"]["reason"]
                        == "face measurement unavailable"
                    )
                else:
                    assert "arcface_target" not in checks_by_name
        assert (
            client.put(
                f"/api/production/reviews/{review['id']}/ranking",
                json={
                    "expected_revision": review["ranking"]["revision"],
                    "tiers": [[failed["id"], other["id"]]],
                    "usable_tiers": 1,
                },
            ).status_code
            == 200
        )
        response = client.get(f"/api/production/reviews/{review['id']}/reveal")
        assert response.status_code == 200, response.text
        members = {m["candidate_id"]: m for m in response.json()["members"]}
        assert face_id in json.dumps(members[failed["id"]]["replacement_checks"])
        assert members[failed["id"]]["round"] == 2
        assert members[failed["id"]]["arm"] == {"treatment": "inverted", "framing": "full"}
        assert members[failed["id"]]["seed"] == 87
        assert members[other["id"]]["seed"] == 42
        assert "letterbox_difference" in json.dumps(members)


@pytest.mark.parametrize(
    "content", ["not json", "[]", '{"checks": null}', '{"checks": [{"check": "made-up"}]}']
)
def test_malformed_checks_remain_reviewable(work, tmp_path, content):
    db, library, _, assets, _ = work
    service = OperationService()
    path = tmp_path / "malformed.json"
    path.write_text(content)
    prepared = library.prepare_blob(path, "analysis_report", {})
    with db.transaction() as session:
        artifact = library.register_artifact(session, prepared)
        operation = service.create(
            session,
            kind="character_replacement",
            request={
                "source_asset": assets[0],
                "policy": replacement_policy_text(),
                "facts": {"shot_start": 0, "shot_end": 51, "fps": 24},
            },
            jobs=[
                JobSpec(
                    "video",
                    "local",
                    "synthetic",
                    step="replacement_composite",
                    seed=0,
                    snapshot={
                        "step_spec": {"arm": {"round": 0, "treatment": "rgb", "framing": "crop"}}
                    },
                )
            ],
            budget_limit=None,
        )
        finish(
            service,
            session,
            operation["jobs"][0],
            [{"output_key": "checks", "artifact_id": artifact["id"]}],
        )
        candidate = service.add_candidate(
            session, operation["jobs"][0]["id"], assets[0], "composite", "video"
        )
    app = create_app(db.root, MIGRATIONS, "test-token", enable_runner=False)
    with TestClient(app, base_url="http://localhost", headers=AUTH) as client:
        response = client.post(f"/api/production/replacements/{operation['id']}/review")
        assert response.status_code == 200, response.text
        review = response.json()
        assert review["members"][0]["replacement_checks"] == []
        assert (
            client.put(
                f"/api/production/reviews/{review['id']}/ranking",
                json={
                    "expected_revision": review["ranking"]["revision"],
                    "tiers": [[candidate["id"]]],
                    "usable_tiers": 1,
                },
            ).status_code
            == 200
        )
        response = client.get(f"/api/production/reviews/{review['id']}/reveal")
        assert response.status_code == 200, response.text
        assert response.json()["members"][0]["replacement_checks"] == []
        assert response.json()["members"][0]["seed"] == 0
