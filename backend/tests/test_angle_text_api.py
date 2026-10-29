"""Angle text edits use the ordinary guarded receipt/undo chain."""

from fastapi.testclient import TestClient
from test_production_api import AUTH, setup

from gaiavfx.modules.direction.service import DirectionService


def test_angle_name_description_receipt_undo_redo_and_stale_guard(tmp_path):
    app = setup(tmp_path)
    service = DirectionService()
    with app.state.workspace.db.transaction() as db:
        movie = service.create_movie(db, "Angle text")
        place = service.create_place(db, "Courtyard")
        angle = service.add_angle(db, place["id"], "Reverse", {"source": "unknown"})
    with TestClient(app, base_url="http://localhost") as client:
        url = f"/api/direction/angles/{angle['id']}"
        body = {
            "movie_id": movie["id"],
            "expected_revision": angle["revision"],
            "name": "Inside gallery",
            "description": "Sunlit courtyard behind the walker.",
        }
        assert client.patch(url, json=body).status_code == 401
        saved = client.patch(url, json=body, headers=AUTH)
        assert saved.status_code == 200, saved.text
        saved = saved.json()
        assert saved["name"] == body["name"] and saved["description"] == body["description"]
        assert saved["placement"] == angle["placement"]
        assert client.patch(url, json=body, headers=AUTH).status_code == 409
        receipt = saved["receipt"]
        undone = client.post(f"/api/direction/receipts/{receipt['id']}/undo", headers=AUTH)
        assert undone.status_code == 200, undone.text
        assert undone.json()["record"]["name"] == "Reverse"
        assert undone.json()["record"]["description"] == ""
        redone = client.post(f"/api/direction/receipts/{undone.json()['id']}/undo", headers=AUTH)
        assert redone.status_code == 200, redone.text
        assert redone.json()["record"]["name"] == body["name"]
        later = client.patch(
            url,
            json={
                **body,
                "expected_revision": redone.json()["record"]["revision"],
                "description": "Changed later",
            },
            headers=AUTH,
        )
        assert later.status_code == 200
        assert (
            client.post(
                f"/api/direction/receipts/{redone.json()['id']}/undo", headers=AUTH
            ).status_code
            == 409
        )
        assert (
            client.patch(
                url,
                json={**body, "expected_revision": later.json()["revision"], "name": "   "},
                headers=AUTH,
            ).status_code
            == 422
        )
