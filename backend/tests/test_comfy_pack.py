"""Import the real pack with CPU stubs, checking registration and HTTP health."""

import asyncio
import importlib.util
import sys
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest

PACK = Path(__file__).parents[2] / "comfy_nodes" / "gaiavfx"
CORE = {
    "GaiaH3AudioVAE",
    "GaiaH3PowerShift",
    "GaiaH3LoraLoader",
    "GaiaH3MaskedSpan",
    "GaiaH3ContinuityTrim",
    "GaiaSaveSetGeometry",
    "GaiaSaveStillGeometry",
}
FACE = {"GaiaFaceScan", "GaiaFaceCheck"}


def load_pack(tmp_path, monkeypatch, *, insightface=False, onnxruntime=False):
    routes = {}

    def register(path):
        def decorate(handler):
            routes[path] = handler
            return handler

        return decorate

    monkeypatch.setitem(sys.modules, "torch", ModuleType("torch"))
    comfy = ModuleType("comfy")
    comfy.__path__ = []
    monkeypatch.setitem(sys.modules, "comfy", comfy)
    for name in ("lora", "lora_convert"):
        module = ModuleType(f"comfy.{name}")
        monkeypatch.setitem(sys.modules, module.__name__, module)
        setattr(comfy, name, module)
    monkeypatch.setitem(sys.modules, "folder_paths", SimpleNamespace(models_dir=str(tmp_path)))
    monkeypatch.setitem(
        sys.modules,
        "server",
        SimpleNamespace(
            PromptServer=SimpleNamespace(
                instance=SimpleNamespace(routes=SimpleNamespace(get=register))
            )
        ),
    )
    monkeypatch.setitem(
        sys.modules,
        "aiohttp",
        SimpleNamespace(web=SimpleNamespace(json_response=lambda data: data)),
    )
    for name, available in (("insightface", insightface), ("onnxruntime", onnxruntime)):
        monkeypatch.setitem(sys.modules, name, ModuleType(name) if available else None)
    spec = importlib.util.spec_from_file_location(
        "test_gaiavfx_pack", PACK / "__init__.py", submodule_search_locations=[str(PACK)]
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module, routes


@pytest.mark.parametrize(
    "insightface,onnxruntime", [(False, False), (True, False), (False, True), (True, True)]
)
def test_health_matches_registered_nodes_and_optional_dependencies(
    tmp_path, monkeypatch, caplog, insightface, onnxruntime
):
    pack, routes = load_pack(
        tmp_path, monkeypatch, insightface=insightface, onnxruntime=onnxruntime
    )
    expected = CORE | FACE if insightface and onnxruntime else CORE
    assert set(pack.NODE_CLASS_MAPPINGS) == expected
    assert set(routes) == {"/gaiavfx/health"}
    report = asyncio.run(routes["/gaiavfx/health"](None))
    assert report["pack"] == "gaiavfx" and report["version"] == "0.1.0"
    assert set(report["nodes"]) == expected
    assert report["insightface"]["importable"] is insightface
    assert bool(report["insightface"]["error"]) is (not insightface)
    assert report["onnxruntime"] == {"importable": onnxruntime}
    assert all(name.startswith("GAIAVFX ") for name in pack.NODE_DISPLAY_NAME_MAPPINGS.values())
    if not (insightface and onnxruntime):
        assert "face nodes are unavailable" in caplog.text
        assert "Other GAIAVFX nodes remain available" in caplog.text
    models = tmp_path / "insightface" / "models" / "buffalo_l"
    assert report["buffalo_l"] == {"present": False, "path": str(models)}
    models.mkdir(parents=True)
    for name in ("det_10g.onnx", "w600k_r50.onnx"):
        (models / name).touch()
    assert not pack.health_status()["buffalo_l"]["present"]
    (models / "2d106det.onnx").touch()
    assert pack.health_status()["buffalo_l"]["present"]


def test_audio_guard_copies_vae_without_mutating_original(tmp_path, monkeypatch):
    pack, _ = load_pack(tmp_path, monkeypatch)
    vae = SimpleNamespace(
        first_stage_model=type("MiniMaxH3AudioVAE", (), {})(),
        audio_sample_rate=32000,
        downscale_ratio=800,
        crop_input=True,
    )
    (safe,) = pack.NODE_CLASS_MAPPINGS["GaiaH3AudioVAE"]().guard(vae)
    assert safe is not vae and safe.crop_input is False and vae.crop_input is True
    vae.audio_sample_rate = 44100
    with pytest.raises(ValueError, match="verified H3 Audio VAE contract"):
        pack.GaiaH3AudioVAE().guard(vae)
