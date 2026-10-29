"""A selected take remains selected while authored revisions move independently."""

import pytest
from fastapi.testclient import TestClient
from test_production_api import AUTH, get, post, setup, source_assets

from gaiavfx.modules.editing.selections.staleness import changed_since_take
from gaiavfx.modules.generation.operations.lifecycle import JobSpec
from gaiavfx.modules.generation.operations.review import ReviewService
from gaiavfx.modules.generation.operations.service import OperationService


@pytest.mark.parametrize("changed", ["segment", "phase", "subject"])
def test_selection_read_marks_changes_without_clearing(tmp_path, changed):
    app = setup(tmp_path)
    with TestClient(app, base_url="http://localhost") as client:
        assets, _ = source_assets(app, tmp_path)
        movie = post(client, "/api/direction/movies", {"title": "Selected take"})
        subject = post(
            client,
            f"/api/direction/movies/{movie['id']}/subjects",
            {
                "expected_revision": movie["revision"],
                "name": "Arin",
                "description": "A walker",
                "keeps": [],
                "identity_asset_id": assets[0],
            },
        )
        segment = post(
            client,
            f"/api/direction/movies/{movie['id']}/segments",
            {
                "expected_revision": subject["receipt"]["after"]["revision"],
                "delivered_frames": 96,
                "continues": False,
                "content": {
                    "subjects": [subject["id"]],
                    "place": {
                        "noun": "courtyard",
                        "description": "Empty yard",
                        "landmarks": "wall",
                    },
                },
                "phases": [
                    {
                        "start_frame": 0,
                        "content": {
                            "description": "Walk",
                            "screen_direction": "forward",
                            "sound": "steps",
                        },
                    }
                ],
            },
        )
        phase = segment["phases"][0]
        with app.state.workspace.db.transaction() as session:
            operations = OperationService()
            operation = operations.create(
                session,
                kind="render",
                request={
                    "segment_revision": segment["revision"],
                    "phase_revisions": {phase["id"]: phase["revision"]},
                    "subject_revisions": {subject["id"]: subject["revision"]},
                },
                jobs=[JobSpec("image", "local", "fake", step="fixture")],
                budget_limit=None,
            )
            candidate = operations.add_candidate(
                session, operation["jobs"][0]["id"], assets[0], "test", "image"
            )
            review = ReviewService().create(
                session,
                purpose="segment_takes",
                candidate_ids=[candidate["id"]],
                context={"movie_id": movie["id"], "segment_id": segment["id"]},
            )
        selected = post(
            client,
            f"/api/production/reviews/{review['id']}/select",
            {"expected_revision": 0, "candidate_id": candidate["id"]},
            status=200,
        )
        params = {"movie_id": movie["id"], "segment_id": segment["id"]}
        assert not get(client, "/api/production/selections", params)["current"]["stale"]
        path, body = {
            "segment": (
                f"/api/direction/segments/{segment['id']}",
                {"expected_revision": segment["revision"], "delivered_frames": 120},
            ),
            "phase": (
                f"/api/direction/phases/{phase['id']}",
                {
                    "expected_revision": phase["revision"],
                    "content": {**phase["content"], "description": "Stop"},
                },
            ),
            "subject": (
                f"/api/direction/subjects/{subject['id']}",
                {"expected_revision": subject["revision"], "description": "A runner"},
            ),
        }[changed]
        response = client.patch(path, json=body, headers=AUTH)
        assert response.status_code == 200, response.text
        for _ in range(2):
            state = get(client, "/api/production/selections", params)
            assert state["current"]["id"] == selected["id"]
            assert state["current"]["candidate_id"] == candidate["id"]
            assert state["current"]["stale"]
            assert any(changed in reason.lower() for reason in state["current"]["stale_reasons"])
            assert state["revision"] == 1
            assert len(state["history"]) == 1
            assert state["history"][0]["stale"]

        undone = post(
            client, f"/api/direction/receipts/{response.json()['receipt']['id']}/undo", status=200
        )
        state = get(client, "/api/production/selections", params)
        assert not state["current"]["stale"] and not state["history"][0]["stale"]
        redone = post(client, f"/api/direction/receipts/{undone['id']}/undo", status=200)
        assert get(client, "/api/production/selections", params)["current"]["stale"]
        restored = post(client, f"/api/direction/receipts/{redone['id']}/undo", status=200)
        assert not get(client, "/api/production/selections", params)["current"]["stale"]
        # An ordinary command restoring the same value is still a new change.
        same = {key: restored["record"][key] for key in body if key != "expected_revision"}
        response = client.patch(
            path, json=same | {"expected_revision": restored["record"]["revision"]}, headers=AUTH
        )
        assert response.status_code == 200, response.text
        assert get(client, "/api/production/selections", params)["current"]["stale"]


def test_missing_and_removed_revision_evidence_is_not_a_fresh_take():
    current = {"segment_revision": 1, "phase_revisions": {"p": 1}, "subject_revisions": {"s": 1}}
    assert changed_since_take(current, current) == []
    assert changed_since_take(current, None) == ["The segment no longer exists."]
    assert len(changed_since_take({}, current)) == 3
    assert changed_since_take(current, {**current, "phase_revisions": {}}) == [
        "Phases changed since this take."
    ]
