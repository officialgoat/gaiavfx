"""Panoramas through the running API: preview, start from words, the ready pair with its seam checks and signed
images, the person's choice, and two more."""

from fastapi.testclient import TestClient
from test_panoramas import PanoramaComfy
from test_production_api import get, post, run_idle, setup

DESCRIBED = {
    "mode": "description",
    "place": "A narrow city-flat hall with worn parquet",
    "landmarks": [{"what": "Living-room doorway", "heading": 0}],
    "light": "Overcast afternoon",
}


def test_a_place_described_becomes_a_chosen_panorama(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIAVFX_GPU_LOCK", str(tmp_path / "gpu.lock"))
    app = setup(tmp_path)
    app.state.operations.comfy = PanoramaComfy()
    with TestClient(app, base_url="http://localhost") as client:
        preview = post(client, "/api/panoramas/preview", DESCRIBED, status=200)
        assert preview["renders"] == 4 and preview["prompt"].startswith("img-txt-2-360, ")
        started = post(client, "/api/panoramas", DESCRIBED)
        assert started["state"] == "building" and started["candidates"] == []
        assert post(client, "/api/panoramas", DESCRIBED, status=200)["id"] == started["id"]
        run_idle(app)
        ready = get(client, f"/api/panoramas/{started['id']}")
        assert ready["state"] == "ready" and [c["letter"] for c in ready["candidates"]] == [
            "A",
            "B",
        ]
        first = ready["candidates"][0]
        assert first["seam"]["outside_identical"] and first["plate"] is None
        image = client.get(first["image"]["original"])
        assert image.status_code == 200 and image.headers["content-type"] == "image/png"
        chosen = post(
            client, f"/api/panoramas/candidates/{first['candidate_id']}/choose", status=200
        )
        assert chosen["state"] == "chosen" and chosen["chosen"] == first["candidate_id"]
        more = post(client, f"/api/panoramas/{started['id']}/more")
        assert more["attempt"] == 1
        listed = get(client, "/api/panoramas")
        assert [p["attempt"] for p in listed] == [1, 0]
        refused = client.post(
            "/api/panoramas",
            json={"mode": "description", "place": "x", "hfov": 10},
            headers={"Authorization": "Bearer test-token"},
        )
        assert refused.status_code == 422
