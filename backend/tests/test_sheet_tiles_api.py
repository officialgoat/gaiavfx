"""A read-only default assembly layout stays owned by Python's maintained integration."""

from fastapi.testclient import TestClient
from test_production_api import get, post, run_idle, setup, source_assets

from gaiavfx.modules.direction.service import DirectionService


def test_default_sheet_tiles_are_scoped_padded_and_accepted_by_assembly(tmp_path):
    app = setup(tmp_path)
    assets, _ = source_assets(app, tmp_path)
    direction = DirectionService()
    with TestClient(app, base_url="http://localhost") as client:
        place = post(client, "/api/direction/places", {"name": "Arcade"})
        other = post(client, "/api/direction/places", {"name": "Other place"})
        endpoint = f"/api/production/places/{place['id']}/sheet-tiles"
        assert client.get(endpoint).status_code == 401
        assert get(client, endpoint) == {"place_id": place["id"], "tiles": []}
        get(client, "/api/production/places/missing/sheet-tiles", status=404)
        with app.state.workspace.db.transaction() as session:
            front = direction.add_angle(session, place["id"], "Front")
            back = direction.add_angle(session, place["id"], "Back")
            outside = direction.add_angle(session, other["id"], "Outside")
            # Approve out of angle order; the read owns the canonical ordering.
            second = direction.record_evidence(session, back["id"], "imported", assets[1])
            direction.approve_evidence(session, second["id"])
            first = direction.record_evidence(session, front["id"], "imported", assets[0])
            direction.approve_evidence(session, first["id"])
            direction.record_evidence(session, front["id"], "imported", assets[2])
            unrelated = direction.record_evidence(session, outside["id"], "imported", assets[2])
            direction.approve_evidence(session, unrelated["id"])
        before = get(client, "/api/production/operations")
        layout = get(client, endpoint)
        assert layout == {"place_id": place["id"], "tiles": [first["id"], second["id"], [32, 24]]}
        assert get(client, endpoint) == layout
        assert get(client, "/api/production/operations") == before
        operation = post(client, "/api/production/sheets", {**layout, "origin": "walk_around"})
        run_idle(app)
        assembled = get(client, f"/api/production/operations/{operation['id']}")
        assert assembled["state"] == "succeeded"
        assert assembled["request"]["tiles"] == [first["id"], second["id"], {"blank": [32, 24]}]
        assert len(assembled["candidates"]) == 1
