import ast
import copy
from pathlib import Path

import pytest

from gaiavfx.setup.manifest import (
    load_manifest,
    read_setup_file,
    selected_features,
    update_setup_file,
    validate_manifest,
)
from gaiavfx.setup.models import extra_model_paths, model_candidates, model_paths


def test_manifest_validated_and_pinned():
    manifest = load_manifest()
    assert len(manifest["models"]) == 18
    assert all(m["sha256"] and m["url"] for m in manifest["models"])
    assert manifest["comfyui"]["min_version"] == "0.35.0"
    assert manifest["comfyui"]["tested_version"] == "0.42.1"
    assert any(p.get("subdir") for p in manifest["node_packs"])


@pytest.mark.parametrize(
    "defect", ["duplicate", "folder", "reference", "hash", "feature", "manual"]
)
def test_manifest_rejects_drift(defect):
    data = copy.deepcopy(load_manifest())
    if defect == "duplicate":
        data["models"].append(data["models"][0])
    elif defect == "folder":
        data["models"][0]["folder"] = "../escape"
    elif defect == "reference":
        data["features"]["core"]["models"].append("unknown.safetensors")
    elif defect == "hash":
        data["models"][0]["sha256"] = "broken"
    elif defect == "feature":
        data["models"][0]["features"].append("unknown")
    else:
        data["models"][0].update(url="", note="")
    with pytest.raises(ValueError):
        validate_manifest(data)


def test_every_graph_class_is_declared_for_its_feature():
    root = Path(__file__).parents[1] / "src/gaiavfx/integrations"
    features = load_manifest()["features"]
    render_tree = ast.parse((root / "minimax_h3/graph.py").read_text(encoding="utf-8"))
    loader_choices = {
        value.value
        for node in ast.walk(render_tree)
        if isinstance(node, ast.Compare) and ast.unparse(node.left) == "replacement['lora_loader']"
        for allowed in node.comparators
        if isinstance(allowed, ast.Set)
        for value in allowed.elts
        if isinstance(value, ast.Constant)
    }
    assert loader_choices, "Keep the dynamic LoRA loader's validation statically enumerable"
    builders = 0
    for path in root.rglob("*.py"):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        # Every builder has an add(key, kind, **inputs) helper or literal class_type.
        has_builder = any(
            isinstance(n, ast.Constant) and n.value == "class_type" for n in ast.walk(tree)
        )
        if not has_builder:
            continue
        builders += 1
        relative = path.relative_to(root).as_posix()
        if relative == "minimax_h3/graph.py":
            feature = "core"
        elif relative.startswith("person_analysis/") or relative == "minimax_h3/replacement.py":
            feature = "recast"
        elif relative.startswith("qwen_image_edit/"):
            feature = "image-edit"
        elif relative.startswith("worldbuilder/") or relative in {
            "minimax_h3/probe.py",
            "minimax_h3/survey.py",
        }:
            feature = "stages"
        else:
            pytest.fail(f"Assign new builder {relative} to a setup feature")
        emitted = set()
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id == "add"
            ):
                kind = node.args[1]
                if isinstance(kind, ast.Constant):
                    emitted.add(kind.value)
                elif (
                    relative == "minimax_h3/replacement.py"
                    and ast.unparse(kind) == "repl['lora_loader']"
                ):
                    emitted.update(loader_choices)
                else:
                    pytest.fail(f"Resolve dynamic class_type in {relative}: {ast.unparse(kind)}")
            if isinstance(node, ast.Dict):
                for key, value in zip(node.keys, node.values):
                    if (
                        isinstance(key, ast.Constant)
                        and key.value == "class_type"
                        and isinstance(value, ast.Constant)
                    ):
                        emitted.add(value.value)
        declared = set(features[feature]["core_classes"] + features[feature]["node_classes"])
        assert emitted, f"Resolve the emitted class_types in {relative}"
        assert not emitted - declared, (relative, emitted - declared)
    assert builders >= 10


def test_extra_paths_resolve_blocks_quotes_relative_and_buffalo(tmp_path):
    config = tmp_path / "extra_model_paths.yaml"
    config.write_text(
        """# shared models
shared:
  base_path: './library with spaces' # shared cache
  is_default: true
  diffusion_models: |
    diffusion
    alternate
  clip: text # old alias
other:
  base_path: ../other
  vae: 'vae files'
  insightface/models/buffalo_l: ignored
""",
        encoding="utf-8",
    )
    paths = extra_model_paths(config)
    assert paths["diffusion_models"] == [
        (tmp_path / "library with spaces/diffusion").resolve(),
        (tmp_path / "library with spaces/alternate").resolve(),
    ]
    assert paths["text_encoders"] == [(tmp_path / "library with spaces/text").resolve()]
    assert paths["vae"] == [(tmp_path.parent / "other/vae files").resolve()]
    assert model_paths(tmp_path, "diffusion_models")[0] == paths["diffusion_models"][0]
    assert model_paths(tmp_path, "insightface/models/buffalo_l") == [
        tmp_path / "models/insightface/models/buffalo_l"
    ]


def test_extra_paths_rejects_unsupported_yaml(tmp_path):
    config = tmp_path / "paths.yaml"
    config.write_text("models:\n  vae: [a, b]\n")
    with pytest.raises(ValueError, match="plain folder"):
        extra_model_paths(config)


def test_model_candidates_accept_legacy_roots_but_not_nested_basenames(tmp_path):
    model = {"file": "tiny.safetensors", "folder": "diffusion_models"}
    nested = tmp_path / "models/diffusion_models/nested/tiny.safetensors"
    nested.parent.mkdir(parents=True)
    nested.touch()
    candidates = model_candidates(tmp_path, model)
    assert candidates == [
        tmp_path / "models/diffusion_models/tiny.safetensors",
        tmp_path / "models/unet/tiny.safetensors",
    ]
    assert nested not in candidates


def test_setup_file_merges_and_tolerates_invalid_input(tmp_path, monkeypatch):
    file = tmp_path / "defaults/setup.json"
    monkeypatch.setenv("GAIAVFX_SETUP_FILE", str(file))
    update_setup_file(comfy_url="http://localhost:8188")
    update_setup_file(engines_root=tmp_path / "engines")
    assert read_setup_file() == {
        "comfy_url": "http://localhost:8188",
        "engines_root": str(tmp_path / "engines"),
    }
    assert len(list(file.parent.iterdir())) == 1
    file.write_text("not JSON")
    assert read_setup_file() == {}
    assert selected_features(["core,stages", "core"]) == ["core", "stages"]
    with pytest.raises(ValueError):
        selected_features("not-a-feature")
