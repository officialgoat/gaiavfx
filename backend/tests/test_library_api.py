"""The retained shell uses synchronous imports and the operation queue."""

import io
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from gaiavfx.api.app import create_app
from gaiavfx.shared.errors import ApplicationError

MIGRATIONS = Path(__file__).parents[1] / "migrations"
SETTINGS = {
    "comfy_url",
    "assistant_executable",
    "assistant_model",
    "assistant_projector",
    "assistant_context",
    "assistant_agent_context",
    "assistant_connections",
    "assistant_agent_connection",
    "assistant_agent_thinking",
    "agent_autonomy",
    "agent_budget_jobs",
    "agent_keep_warm_seconds",
    "engines_root",
    "expressive_library",
}


@pytest.fixture
def api(tmp_path):
    app = create_app(tmp_path, MIGRATIONS, "token", enable_runner=False)
    with TestClient(
        app, base_url="http://localhost", headers={"Authorization": "Bearer token"}
    ) as client:
        assert client.post("/api/v1/library", json={"name": "Library shell"}).status_code == 201
        yield client


def png(color="red"):
    buffer = io.BytesIO()
    Image.new("RGB", (64, 64), color).save(buffer, "PNG")
    return buffer.getvalue()


def upload(api, data, name="source.png", **associations):
    return api.post("/api/v1/imports", files={"file": (name, data, "image/png")}, data=associations)


def test_imported_asset_filter_precedes_pagination_and_preserves_reused_sources(api):
    from gaiavfx.modules.library.assets import AssetRecord

    with api.app.state.workspace.db.transaction() as session:
        for index in range(45):
            session.add(
                AssetRecord(
                    id=f"video-{index:02}",
                    sha256=str(index),
                    name="shot.mkv",
                    kind="video",
                    path=f"media/{index}.mkv",
                    mime="video/x-matroska",
                    info={},
                    provenance=[{"type": "shot_assembly", "source_asset_id": "film"}],
                )
            )
        for index in range(2):
            session.add(
                AssetRecord(
                    id=f"source-{index}",
                    sha256=f"source-{index}",
                    name=f"Film {index}",
                    kind="video",
                    path=f"media/source-{index}.mp4",
                    mime="video/mp4",
                    info={},
                    provenance=[
                        {"type": "import", "original_name": f"Film {index}"},
                        {"type": "collection_correction"},
                    ],
                )
            )
    params = {"kind": "video", "imported_only": True, "limit": 1}
    first = api.get("/api/v1/assets", params=params).json()
    second = api.get("/api/v1/assets", params={**params, "offset": 1}).json()
    assert first["total"] == second["total"] == 2
    assert {first["items"][0]["id"], second["items"][0]["id"]} == {"source-0", "source-1"}
    assert api.get("/api/v1/assets", params={"kind": "video"}).json()["total"] == 47
    assert api.get("/api/v1/assets", params={**params, "q": "shot"}).json()["total"] == 0
    # `exclude` leaves out a step's media by provenance type, or one output of a step.
    hidden = api.get("/api/v1/assets", params={"kind": "video", "exclude": "shot_assembly"}).json()
    assert hidden["total"] == 2
    assert {a["id"] for a in hidden["items"]} == {"source-0", "source-1"}
    one_output = api.get(
        "/api/v1/assets",
        params={"kind": "video", "exclude": "shot_assembly:preview,import:other"},
    ).json()
    assert one_output["total"] == 47
    # `keep` lists media of a kept step whatever `exclude` says: an approved replacement keeps its composite step.
    with api.app.state.workspace.db.transaction() as session:
        session.get(AssetRecord, "video-00").provenance = [
            {"type": "shot_assembly", "source_asset_id": "film"},
            {"type": "character_replacement", "source_asset": "film"},
        ]
    kept = api.get(
        "/api/v1/assets",
        params={"kind": "video", "exclude": "shot_assembly", "keep": "character_replacement"},
    ).json()
    assert {a["id"] for a in kept["items"]} == {"source-0", "source-1", "video-00"}


def test_import_reuses_content_and_preserves_aliases_associations_and_signed_media(api, tmp_path):
    collection = api.post("/api/v1/collections", json={"name": "References"}).json()
    entity = api.post("/api/v1/entities", json={"kind": "character", "name": "Mara"}).json()
    first = upload(
        api, png(), collection_id=collection["id"], entity_id=entity["id"], role="identity"
    )
    assert first.status_code == 201, first.text
    result = first.json()
    assert result["disposition"] == "created"
    asset = result["asset"]
    assert asset["provenance"] == [{"type": "import", "original_name": "source.png"}]
    second = upload(api, png(), "alias.png")
    assert second.status_code == 201 and second.json()["disposition"] == "reused"
    assert second.json()["asset"]["id"] == asset["id"]
    assert second.json()["asset"]["provenance"][-1]["original_name"] == "alias.png"
    assert not list((tmp_path / "staging").iterdir())
    page = api.get("/api/v1/assets", params={"q": "alias", "collection": collection["id"]}).json()
    assert [a["id"] for a in page["items"]] == [asset["id"]]
    assert api.get("/api/v1/entities").json()[0]["assets"] == [
        {"asset_id": asset["id"], "role": "identity"}
    ]
    urls = api.get(f"/api/v1/assets/{asset['id']}/urls").json()
    assert api.get(urls["original"]).content == png()
    assert api.get(urls["original"].replace("signature=", "signature=bad")).status_code == 401
    assert api.get("/api/production/operations").json() == []
    assert not hasattr(api.app.state, "runner")


def test_import_cleans_staging_on_empty_invalid_media_and_registration_rollback(
    api, tmp_path, monkeypatch
):
    assert upload(api, b"").status_code == 422
    assert upload(api, b"not an image").status_code == 422
    w = api.app.state.workspace

    def fail(*args):
        raise ApplicationError("Association failed", 422)

    monkeypatch.setattr(w.catalog, "assign", fail)
    response = upload(api, png(), entity_id="missing")
    assert response.status_code == 422 and "Association failed" in response.text
    assert api.get("/api/v1/assets").json()["total"] == 0
    assert not list((tmp_path / "staging").iterdir())


def test_settings_are_strict_and_comfy_health_is_transport_only(api, monkeypatch):
    settings = api.get("/api/v1/settings/connections").json()
    assert set(settings) == SETTINGS
    for key in (
        "assistant_enabled",
        "comfy_directory",
        "targeted_interrupt",
        "assistant_writing_connection",
        "assistant_observation_connection",
        "assistant_specialists",
        "assistant_specialist_connection",
        "assistant_render_autonomy",
    ):
        assert (
            api.put("/api/v1/settings/connections", json={**settings, key: False}).status_code
            == 422
        )
    assert (
        api.put(
            "/api/v1/settings/connections",
            json={**settings, "comfy_url": "http://user:pass@localhost"},
        ).status_code
        == 422
    )
    assert (
        api.put(
            "/api/v1/settings/connections",
            json={**settings, "assistant_agent_connection": "missing"},
        ).status_code
        == 422
    )
    saved = api.put("/api/v1/settings/connections", json={**settings, "agent_budget_jobs": 3})
    assert saved.status_code == 200 and saved.json()["agent_budget_jobs"] == 3
    calls = []

    def offline(*args):
        calls.append(args)
        raise httpx.ConnectError("No connection")

    monkeypatch.setattr(api.app.state.workspace.comfy, "request", offline)
    assert api.get("/api/v1/settings/comfy").json() == {
        "connected": False,
        "version": None,
        "error": "No connection",
    }
    assert calls == [(settings["comfy_url"], "GET", "/system_stats")]
    monkeypatch.setattr(
        api.app.state.workspace.comfy,
        "request",
        lambda *args: {"system": {"comfyui_version": "fixture"}},
    )
    assert api.get("/api/v1/settings/comfy").json() == {
        "connected": True,
        "version": "fixture",
        "error": None,
    }


def test_connection_probe_uses_the_process_gateway_and_agent_connection(api, monkeypatch):
    from gaiavfx.infrastructure.assistant_gateway import AssistantGateway

    gateway = api.app.state.agent.harness.model.gateway
    assert isinstance(gateway, AssistantGateway)
    settings = api.get("/api/v1/settings/connections").json()
    settings["assistant_connections"] = [
        {
            "id": "remote",
            "name": "Remote",
            "base_url": "http://localhost:9000/v1",
            "model": "fixture",
            "vision": False,
        }
    ]
    assert api.put("/api/v1/settings/connections", json=settings).status_code == 200
    calls = []

    def probe(settings, *args, **kwargs):
        calls.append(settings)
        return {"ok": True, "color": "none", "model": "fixture"}

    monkeypatch.setattr(gateway, "run", probe)
    assert (
        api.post(
            "/api/v1/settings/assistant/connections/remote/test", json={"modality": "text"}
        ).status_code
        == 200
    )
    assert calls[0]["assistant_agent_connection"] == "remote"
    assert calls[0]["assistant_max_tokens"] == 512
    assert api.get("/api/v1/settings/connections").json()["assistant_agent_connection"] == "local"


def test_the_engines_root_setting_and_readiness(api, tmp_path, monkeypatch):
    monkeypatch.delenv("GAIAVFX_ENGINES_ROOT", raising=False)
    settings = api.get("/api/v1/settings/connections").json()
    assert settings["engines_root"] == ""
    assert api.get("/api/v1/settings/engines").json()["root_source"] == "none"
    refused = api.put(
        "/api/v1/settings/connections", json={**settings, "engines_root": "relative/engines"}
    )
    assert refused.status_code == 422 and "absolute folder" in refused.text
    root = str(tmp_path / "engines")
    saved = api.put("/api/v1/settings/connections", json={**settings, "engines_root": root})
    assert saved.status_code == 200 and saved.json()["engines_root"] == root
    engines = api.get("/api/v1/settings/engines").json()
    assert (engines["root"], engines["root_source"]) == (root, "setting")
    assert isinstance(engines["engines"], list)
    monkeypatch.setenv("GAIAVFX_ENGINES_ROOT", str(tmp_path / "override"))
    assert api.get("/api/v1/settings/engines").json()["root_source"] == "environment"


def test_the_expressive_library_setting_and_its_scan(api, tmp_path):
    """ADR 0016 P5: the expressive recordings folder is an absolute folder or empty; Settings sees its counts."""
    settings = api.get("/api/v1/settings/connections").json()
    assert settings["expressive_library"] == ""
    empty = {"folder": "", "state": "not_set", "clips": 0, "categories": {}, "skipped": 0}
    assert api.get("/api/v1/settings/voice-library").json() == empty
    refused = api.put(
        "/api/v1/settings/connections", json={**settings, "expressive_library": "relative/screams"}
    )
    assert refused.status_code == 422
    assert "Enter an absolute folder for the expressive recordings" in refused.text
    assert api.get("/api/v1/settings/connections").json()["expressive_library"] == ""

    folder = tmp_path / "expressive"
    saved = api.put(
        "/api/v1/settings/connections", json={**settings, "expressive_library": f" {folder} "}
    )
    assert saved.status_code == 200 and saved.json()["expressive_library"] == str(folder)
    assert api.get("/api/v1/settings/connections").json()["expressive_library"] == str(folder)
    missing = api.get("/api/v1/settings/voice-library").json()
    assert missing == {**empty, "folder": str(folder), "state": "missing"}

    for relative in ("screams/a.wav", "Sobs/alex/b.flac", "music/c.wav"):
        (folder / relative).parent.mkdir(parents=True, exist_ok=True)
        (folder / relative).write_bytes(b"RIFF")
    ready = api.get("/api/v1/settings/voice-library").json()
    assert ready == {
        "folder": str(folder),
        "state": "ready",
        "clips": 2,
        "categories": {"scream": 1, "sob": 1},
        "skipped": 1,
    }

    cleared = api.put("/api/v1/settings/connections", json={**settings, "expressive_library": ""})
    assert cleared.status_code == 200 and cleared.json()["expressive_library"] == ""
    assert api.get("/api/v1/settings/voice-library").json() == empty
