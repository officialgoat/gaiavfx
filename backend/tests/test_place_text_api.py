"""User-authored empty-place text has guarded, reversible movie receipts."""

import pytest
from fastapi.testclient import TestClient
from test_production_api import AUTH, setup

from gaiavfx.modules.direction.place_text import EMPTY_PLACE_REFUSAL
from gaiavfx.modules.direction.service import DirectionService


def test_place_text_receipt_validation_conflict_undo_redo(tmp_path):
    app = setup(tmp_path)
    direction = DirectionService()
    with app.state.workspace.db.transaction() as session:
        movie = direction.create_movie(session, "Courtyard")
        direction.add_subject(
            session,
            movie["id"],
            movie["revision"],
            name="Arin",
            description="A walker in plaid",
            keeps=[],
            identity_asset_id=None,
        )
        place = direction.create_place(session, "Arcade")
        angle = direction.add_angle(session, place["id"], "Reverse")
    with TestClient(app, base_url="http://localhost") as client:
        url = f"/api/direction/places/{place['id']}"
        body = {
            "movie_id": movie["id"],
            "expected_revision": place["revision"],
            "description": "Empty stone courtyard",
            "sound": "Wind in the trees",
            "overhead": "Straight down over the roof and railing",
        }
        assert client.patch(url, json=body).status_code == 401
        for field in ("description", "sound", "overhead"):
            for text in ("Arin by the railing", "A person in the gallery"):
                refused = client.patch(url, json=body | {field: text}, headers=AUTH)
                assert refused.status_code == 422 and EMPTY_PLACE_REFUSAL in refused.text
        # The API adds who makes the place's views (ADR 0019 §7).
        assert client.get(url, headers=AUTH).json() == place | {"view_producer": "survey"}
        refused = client.patch(
            f"/api/direction/angles/{angle['id']}",
            headers=AUTH,
            json={
                "movie_id": movie["id"],
                "expected_revision": angle["revision"],
                "name": angle["name"],
                "description": "Behind Arin",
            },
        )
        assert refused.status_code == 422 and EMPTY_PLACE_REFUSAL in refused.text
        saved = client.patch(url, json=body, headers=AUTH)
        assert saved.status_code == 200, saved.text
        receipt = saved.json()["receipt"]
        assert receipt["target_kind"] == "place" and receipt["command"] == "update_place"
        assert client.patch(url, json=body, headers=AUTH).status_code == 409
        undone = client.post(f"/api/direction/receipts/{receipt['id']}/undo", headers=AUTH)
        assert undone.status_code == 200, undone.text
        assert all(
            undone.json()["record"][key] == "" for key in ("description", "sound", "overhead")
        )
        redone = client.post(f"/api/direction/receipts/{undone.json()['id']}/undo", headers=AUTH)
        assert redone.status_code == 200, redone.text
        assert redone.json()["record"]["sound"] == body["sound"]
        saved = client.patch(
            url,
            json=body | {"expected_revision": redone.json()["record"]["revision"]},
            headers=AUTH,
        )
        assert saved.status_code == 200
        assert (
            client.post(
                f"/api/direction/receipts/{redone.json()['id']}/undo", headers=AUTH
            ).status_code
            == 409
        )


def test_place_text_receipt_rolls_back_with_command(tmp_path):
    app = setup(tmp_path)
    direction = DirectionService()
    with app.state.workspace.db.transaction() as session:
        movie = direction.create_movie(session, "Rollback")
        place = direction.create_place(session, "Place")
    with pytest.raises(RuntimeError), app.state.workspace.db.transaction() as session:
        direction.update_place(
            session, place["id"], place["revision"], sound="Wind", movie_id=movie["id"]
        )
        raise RuntimeError("abort")
    with app.state.workspace.db.transaction() as session:
        assert direction.read_place(session, place["id"]) == place
        assert len(direction.receipts(session, movie["id"])) == 1
    app.state.workspace.db.close()
