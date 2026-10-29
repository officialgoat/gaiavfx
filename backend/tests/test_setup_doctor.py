import copy
import hashlib
import json
import subprocess
import sys
import time
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from gaiavfx.api.app import create_app
from gaiavfx.setup import doctor
from gaiavfx.setup.manifest import Check, load_manifest, update_setup_file


class FakeGateway:
    def __init__(self, change=None):
        manifest = load_manifest()
        nodes = {
            c: {}
            for f in manifest["features"].values()
            for c in f["core_classes"] + f["node_classes"]
        }
        self.routes = {
            "/system_stats": {"system": {"comfyui_version": "0.42.1"}},
            "/object_info": nodes,
            "/models": sorted({m["folder"] for m in manifest["models"]}),
            "/gaiavfx/health": {
                "pack": "gaiavfx",
                "version": "0.1.0",
                "nodes": [n for n in nodes if n.startswith("Gaia")],
                "insightface": {"importable": True},
                "onnxruntime": {"importable": True},
                "buffalo_l": {"present": True},
            },
        }
        for folder in self.routes["/models"]:
            self.routes["/models/" + folder] = [
                m["file"] for m in manifest["models"] if m["folder"] == folder
            ]
        if change:
            change(self.routes)
        self.calls = []
        self.client = httpx.Client(transport=httpx.MockTransport(self.handle))

    def handle(self, request):
        if request.url.path not in self.routes:
            return httpx.Response(404)
        return httpx.Response(200, json=self.routes[request.url.path])

    def request(self, url, method, route, **kwargs):
        self.calls.append((url, route))
        response = self.client.request(method, url + route, **kwargs)
        response.raise_for_status()
        return response.json()


def test_doctor_all_ok():
    checks = doctor.check_setup(gateway=FakeGateway(), include_runtime=False)
    assert set(doctor.verdicts(checks).values()) == {"ok"}
    assert any("non-commercial research" in c.detail for c in checks)
    assert any("no declared licence" in c.detail for c in checks)


@pytest.mark.parametrize(
    "case,feature,item,status",
    [
        ("class", "core", "node:MiniMaxH3ReferenceToVideo", "missing"),
        ("model", "core", "model:minimax_h3_video_vae_fp16.safetensors", "missing"),
        ("old", "core", "ComfyUI", "mismatch"),
        ("health", "recast", "GAIAVFX node pack health", "missing"),
        ("faces", "recast", "insightface", "missing"),
    ],
)
def test_doctor_gaps(case, feature, item, status):
    fake = FakeGateway()
    if case == "class":
        fake.routes["/object_info"].pop("MiniMaxH3ReferenceToVideo")
    elif case == "model":
        fake.routes["/models/vae"] = []
    elif case == "old":
        fake.routes["/system_stats"]["system"]["comfyui_version"] = "0.34.6"
    elif case == "health":
        fake.routes.pop("/gaiavfx/health")
    else:
        fake.routes["/gaiavfx/health"]["insightface"]["importable"] = False
    checks = doctor.check_setup(gateway=fake, include_runtime=False)
    gap = next(c for c in checks if c.feature == feature and c.item == item)
    assert gap.status == status
    assert gap.fix.startswith("python -m gaiavfx.setup ")
    assert doctor.verdicts(checks)[feature] != "ok"


def test_untested_version_warns_without_failing():
    fake = FakeGateway()
    fake.routes["/system_stats"]["system"]["comfyui_version"] = "0.35.0"
    checks = doctor.check_setup(gateway=fake, features="core", include_runtime=False)
    assert doctor.verdicts(checks) == {"core": "ok"}
    assert any(c.status == "optional" and "Untested" in c.detail for c in checks)


def test_nested_model_name_does_not_satisfy_the_graph_filename():
    fake = FakeGateway()
    fake.routes["/models/vae"] = ["nested/" + name for name in fake.routes["/models/vae"]]
    checks = doctor.check_setup(gateway=fake, features="core", include_runtime=False)
    assert (
        next(c for c in checks if c.item == "model:minimax_h3_video_vae_fp16.safetensors").status
        == "missing"
    )


def test_local_size_hash_and_manual_download(tmp_path):
    manifest = copy.deepcopy(load_manifest())
    model = manifest["models"][0]
    payload = b"test weights"
    model.update(bytes=len(payload), sha256=hashlib.sha256(payload).hexdigest())
    target = tmp_path / "models" / model["folder"] / model["file"]
    target.parent.mkdir(parents=True)
    target.write_bytes(payload)
    kwargs = {
        "gateway": FakeGateway(),
        "features": "core",
        "include_runtime": False,
        "manifest": manifest,
        "comfy_dir": tmp_path,
        "verify": True,
    }

    def check():
        return next(c for c in doctor.check_setup(**kwargs) if c.item == "model:" + model["file"])

    assert check().status == "ok"
    target.write_bytes(b"x" * len(payload))
    assert check().status == "mismatch"
    assert "--verify" in check().fix
    model.update(url="", note="Download from the publisher after accepting its terms.")
    assert "manual download" in check().detail


def test_down_cli_is_quick_and_json_without_traceback():
    start = time.monotonic()
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "gaiavfx.setup",
            "doctor",
            "--comfy-url",
            "http://127.0.0.1:1",
            "--json",
        ],
        capture_output=True,
        text=True,
        timeout=12,
        check=False,
    )
    assert result.returncode == 1
    assert time.monotonic() - start < 10
    assert "Traceback" not in result.stderr
    checks = json.loads(result.stdout)
    assert any("not reachable at http://127.0.0.1:1" in c["detail"] for c in checks)


def test_api_cache_refresh_features_and_new_library_defaults(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIAVFX_SETUP_FILE", str(tmp_path / "setup.json"))
    monkeypatch.setenv("GAIAVFX_COMFY_URL", "http://override:8188")
    update_setup_file(
        comfy_url="http://default:8188",
        assistant_executable="llama",
        assistant_model="model.gguf",
        assistant_projector="projector.gguf",
        engines_root=str(tmp_path / "engines"),
    )
    monkeypatch.setattr(
        doctor, "runtime_checks", lambda: [Check("runtime", "media", "ok", "Ready")]
    )
    app = create_app(
        tmp_path / "library", Path(__file__).parents[1] / "migrations", "token", enable_runner=False
    )
    fake = FakeGateway()
    app.state.workspace.comfy = fake
    with TestClient(
        app, base_url="http://localhost", headers={"Authorization": "Bearer token"}
    ) as client:
        assert client.post("/api/v1/library", json={"name": "Setup"}).status_code == 201
        settings = client.get("/api/v1/settings/connections").json()
        assert settings["comfy_url"] == "http://override:8188"
        assert settings["assistant_projector"] == "projector.gguf"
        assert settings["engines_root"] == str(tmp_path / "engines")
        monkeypatch.setenv("GAIAVFX_COMFY_URL", "http://changed:8188")
        update_setup_file(assistant_model="different.gguf")
        assert client.get("/api/v1/settings/connections").json() == settings
        first = client.get("/api/v1/setup/status?features=core").json()
        assert first["features"]["core"]["verdict"] == "ok"
        calls = len(fake.calls)
        assert client.get("/api/v1/setup/status?features=core").json() == first
        assert len(fake.calls) == calls
        assert {url for url, _ in fake.calls} == {"http://override:8188"}
        fake.routes.pop("/system_stats")
        response = client.get("/api/v1/setup/status?features=core&refresh=true")
        assert response.status_code == 200
        assert response.json()["features"]["core"]["verdict"] == "missing"
        assert client.get("/api/v1/setup/status?features=invalid").status_code == 422
        assert (
            client.get(
                "/api/v1/setup/status", headers={"Authorization": "Bearer wrong"}
            ).status_code
            == 401
        )


def test_existing_library_without_settings_is_not_seeded(tmp_path, monkeypatch):
    from gaiavfx.application.workspace import DEFAULT_SETTINGS, Workspace
    from gaiavfx.modules.library.application import LibraryService

    monkeypatch.setenv("GAIAVFX_SETUP_FILE", str(tmp_path / "setup.json"))
    monkeypatch.setenv("GAIAVFX_COMFY_URL", "http://new-default:8188")
    root = tmp_path / "old"
    LibraryService(root, Path(__file__).parents[1] / "migrations").create("Existing")
    assert Workspace(root).settings() == DEFAULT_SETTINGS


def test_saved_local_directory_only_informs_fixes_not_remote_presence(tmp_path, monkeypatch):
    monkeypatch.setenv("GAIAVFX_SETUP_FILE", str(tmp_path / "setup.json"))
    update_setup_file(comfy_url="http://127.0.0.1:8188", comfy_dir=str(tmp_path / "local-comfy"))
    fake = FakeGateway()
    fake.routes.pop("/system_stats")
    checks = doctor.check_setup(gateway=fake, features="core", include_runtime=False)
    assert "main.py" in checks[0].fix
    assert "local-comfy" in checks[0].fix
    healthy = doctor.check_setup(gateway=FakeGateway(), features="core", include_runtime=False)
    assert doctor.verdicts(healthy) == {"core": "ok"}
    remote = doctor.check_setup(
        "http://other-host:8188", gateway=fake, features="core", include_runtime=False
    )
    assert "local-comfy" not in remote[0].fix


def test_runtime_dependencies_contribute_to_their_feature(monkeypatch):
    from types import SimpleNamespace

    def runtime(name):
        if name.endswith("engines"):
            return SimpleNamespace(
                check=lambda: [
                    Check(
                        "stages",
                        "worldbuilder",
                        "missing",
                        "Missing engine",
                        "python -m gaiavfx.setup engines install worldbuilder",
                    )
                ]
            )
        return SimpleNamespace(
            check=lambda: [Check("assistant", "model", "optional", "Use a remote endpoint")]
        )

    monkeypatch.setattr(doctor.importlib, "import_module", runtime)
    checks = doctor.check_setup(gateway=FakeGateway(), features="stages")
    assert doctor.verdicts(checks) == {"stages": "missing", "runtime": "missing"}
    core = doctor.check_setup(gateway=FakeGateway(), features="core")
    assert "stages" not in doctor.verdicts(core)
    assert doctor.verdicts(core)["core"] == "ok"
