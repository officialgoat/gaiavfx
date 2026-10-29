"""CPU-only smoke command contracts, exercising the real HTTP gateway and builders."""

import argparse
import io
import json
import re
from types import SimpleNamespace

import httpx
import numpy as np
import pytest
from PIL import Image

from gaiavfx.infrastructure.media import MediaTools
from gaiavfx.integrations import minimax_h3
from gaiavfx.integrations.minimax_h3.graph import render_presets as h3_presets
from gaiavfx.integrations.qwen_image_edit.graph import render_presets as qwen_presets
from gaiavfx.integrations.worldbuilder.graph import settings as take_settings
from gaiavfx.setup import smoke

NODE_SETS = {
    "core": {
        "UNETLoader",
        "CLIPLoader",
        "VAELoader",
        "GaiaH3AudioVAE",
        "LoadImage",
        "MiniMaxH3ReferenceToVideo",
        "MiniMaxH3SigmaShift",
        "RandomNoise",
        "BasicGuider",
        "KSamplerSelect",
        "GaiaH3PowerShift",
        "SamplerCustomAdvanced",
        "VRAM_Debug",
        "VAEDecodeTiled",
        "VAEDecodeAudio",
        "CreateVideo",
        "SaveVideo",
    },
    "image-edit": {
        "UNETLoader",
        "LoraLoaderModelOnly",
        "CLIPLoader",
        "VAELoader",
        "ModelSamplingAuraFlow",
        "LoadImage",
        "ImageScaleToTotalPixels",
        "TextEncodeQwenImageEditPlus",
        "EmptySD3LatentImage",
        "KSampler",
        "VAEDecode",
        "SaveImage",
    },
    "stages": {
        "LoadVideo",
        "GetVideoComponents",
        "ImageFromBatch",
        "VHS_SelectEveryNthImage",
        "CheckpointLoaderSimple",
        "CLIPTextEncode",
        "SAM3_VideoTrack",
        "SAM3_TrackToMask",
        "LoadDA3Model",
        "DA3Inference",
        "GaiaSaveSetGeometry",
    },
}


@pytest.fixture
def schema():
    result = {kind: {"input": {"required": {}}} for kinds in NODE_SETS.values() for kind in kinds}
    for kind, minimum, step in (
        ("MiniMaxH3ReferenceToVideo", 64, 32),
        ("EmptySD3LatentImage", 16, 16),
    ):
        result[kind]["input"]["required"] = {
            name: ["INT", {"min": minimum, "max": 8192, "step": step}]
            for name in ("width", "height")
        }
    result["MiniMaxH3ReferenceToVideo"]["input"]["required"]["length"] = [
        "INT",
        {"min": 5, "step": 17},
    ]
    result["SaveVideo"]["input"]["required"]["format"] = ["COMFY_DYNAMICCOMBO_V3", {}]
    return result


@pytest.mark.parametrize("feature", smoke.FEATURES)
def test_graph_uses_real_builder_nodes_and_knowledge(feature, schema):
    graph, settings = smoke.build_smoke_graph(feature, "smoke/input", "smoke/output", schema)
    assert {node["class_type"] for node in graph.values()} == NODE_SETS[feature]
    if feature == "core":
        inputs = graph["conditioning"]["inputs"]
        grid = minimax_h3.capabilities().frame_grid
        assert inputs["length"] == grid.length_base == settings["frames"] == 5
        assert grid.valid_window(inputs["length"])
        assert inputs["width"] == inputs["height"] == 64
        assert graph["sigmas"]["inputs"]["steps"] == h3_presets().sampling["steps"] == 20
        assert graph["model"]["inputs"]["unet_name"] == h3_presets().models["unet"]
        assert "subject_definitions:" in inputs["prompt"]
        assert "<Picture 1>" in inputs["prompt"]
        assert graph["picture_0"]["inputs"]["image"] == "smoke/input"
        assert "save_context" not in graph
    elif feature == "image-edit":
        assert graph["latent"]["inputs"] == {"width": 16, "height": 16, "batch_size": 1}
        assert graph["sample"]["inputs"]["steps"] == qwen_presets().sampling["steps"] == 4
        assert graph["lora_0"]["inputs"]["lora_name"] == qwen_presets().models["lora"]
        assert graph["scaled_1"]["inputs"]["megapixels"] == qwen_presets().image["megapixels"]
        assert graph["positive"]["inputs"]["prompt"].startswith("Remove the blue square")
    else:
        assert graph["shot"]["inputs"]["length"] == settings["frames"] == 3
        assert graph["frames"]["inputs"]["select_every_nth"] == 1
        assert graph["da3"]["inputs"]["resolution"] == take_settings()["geometry"]["resolution"]
        assert (
            graph["people_model"]["inputs"]["ckpt_name"] == take_settings()["people"]["checkpoint"]
        )
        assert graph["save"]["inputs"]["people"] == ["people_masks", 0]


def test_connected_minima_are_rounded_onto_the_integration_grids(schema):
    bounds = schema["MiniMaxH3ReferenceToVideo"]["input"]["required"]
    bounds["width"][1]["min"] = 100
    bounds["height"][1]["min"] = 160
    bounds["length"][1]["min"] = 6
    graph, settings = smoke.build_smoke_graph("core", "s/i", "s/o", schema)
    assert (settings["width"], settings["height"], settings["frames"]) == (128, 160, 22)
    assert graph["conditioning"]["inputs"]["length"] == 22


@pytest.fixture
def media_payloads(tmp_path):
    media = MediaTools()
    image = io.BytesIO()
    Image.new("RGB", (16, 16), "grey").save(image, format="PNG")
    arrays = io.BytesIO()
    np.savez_compressed(
        arrays,
        image=np.zeros((3, 8, 8, 3), np.uint8),
        depth=np.ones((3, 8, 8)),
        confidence=np.ones((3, 8, 8)),
        people=np.zeros((3, 8, 8), bool),
        extrinsics=np.zeros((3, 4, 4)),
        intrinsics=np.zeros((3, 3, 3)),
    )
    # A tiny CPU-encoded output; ffprobe really reads the downloaded movie.
    video = smoke._prepare_input(
        "stages", tmp_path / "video", {"width": 64, "height": 64, "frames": 5, "fps": 24}, media
    )
    return {"core": video.read_bytes(), "image-edit": image.getvalue(), "stages": arrays.getvalue()}


class FakeComfy:
    def __init__(self, schema, payloads, feature="core", mode="pass"):
        self.schema, self.payloads, self.feature, self.mode = schema, payloads, feature, mode
        self.calls = []
        self.graph = None
        self.polls = 0

    def __call__(self, request):
        route = request.url.path
        self.calls.append((request.method, route, request.content))
        if self.mode == "unreachable":
            raise httpx.ConnectError("Connection refused", request=request)
        if route == "/object_info":
            return httpx.Response(200, json=self.schema)
        if route == "/upload/image":
            body = request.content
            name = re.search(rb'filename="([^"]+)"', body).group(1).decode()
            folder = re.search(rb'name="subfolder"\r\n\r\n([^\r]+)', body).group(1).decode()
            assert b'name="overwrite"\r\n\r\nfalse' in body
            return httpx.Response(
                200,
                json={
                    "name": "renamed.png" if self.mode == "renamed" else name,
                    "subfolder": folder,
                },
            )
        if route == "/prompt":
            self.graph = json.loads(request.content)["prompt"]
            if self.mode == "rejected":
                return httpx.Response(400, json={"node_errors": {"model": "model not found"}})
            return httpx.Response(200, json={"prompt_id": "our-prompt"})
        if route == "/queue":
            return httpx.Response(
                200,
                json={
                    "queue_running": [[0, "our-prompt"]] if self.polls >= 2 else [],
                    "queue_pending": [[0, "our-prompt"]] if self.polls < 2 else [],
                },
            )
        if route == "/history/our-prompt":
            self.polls += 1
            if self.mode == "timeout" or self.polls < 3:
                return httpx.Response(200, json={})
            if self.mode == "execution_error":
                return httpx.Response(
                    200,
                    json={
                        "our-prompt": {
                            "status": {
                                "status_str": "error",
                                "completed": False,
                                "messages": [
                                    [
                                        "execution_error",
                                        {
                                            "node_type": "UNETLoader",
                                            "exception_message": "missing weight",
                                        },
                                    ]
                                ],
                            }
                        }
                    },
                )
            node = "save_video" if self.feature == "core" else "save"
            field = "geometry" if self.feature == "stages" else "images"
            suffix = {"core": "mp4", "image-edit": "png", "stages": "npz"}[self.feature]
            outputs = {
                node: {
                    field: [
                        {"filename": f"result.{suffix}", "subfolder": "smoke", "type": "output"}
                    ]
                }
            }
            if self.feature == "core":
                outputs[node]["animated"] = [True]
            if self.mode == "no_output":
                outputs = {}
            return httpx.Response(
                200,
                json={
                    "our-prompt": {
                        "status": {
                            "completed": True,
                            "status_str": "success",
                            "messages": [
                                ["execution_start", {"timestamp": 1000}],
                                ["execution_success", {"timestamp": 1100}],
                            ],
                        },
                        "outputs": outputs,
                    }
                },
            )
        if route == "/view":
            assert request.url.params["type"] == "output"
            payload = b"" if self.mode == "empty_output" else self.payloads[self.feature]
            return httpx.Response(200, content=payload)
        if route in {"/free", "/interrupt"}:
            if self.mode == "free_error" and route == "/free":
                return httpx.Response(500, json={"error": "free failed"})
            return httpx.Response(200, json={})
        raise AssertionError(f"Unexpected request: {request.method} {route}")


@pytest.fixture
def install_fake(monkeypatch, schema, media_payloads):
    client = httpx.Client

    def install(feature="core", mode="pass"):
        fake = FakeComfy(schema, media_payloads, feature, mode)
        transport = httpx.MockTransport(fake)
        monkeypatch.setattr(httpx, "Client", lambda **kwargs: client(**kwargs, transport=transport))
        clock = SimpleNamespace(now=0.0)

        def sleep(seconds):
            clock.now += seconds

        monkeypatch.setattr(
            smoke, "time", SimpleNamespace(monotonic=lambda: clock.now, sleep=sleep)
        )
        return fake

    return install


@pytest.mark.parametrize("feature", smoke.FEATURES)
def test_full_pass_through_real_gateway(feature, install_fake, tmp_path):
    fake = install_fake(feature)
    result = smoke.run_smoke(feature=feature, out=tmp_path / "out")
    assert result["result"] == "PASS", result
    assert {node["class_type"] for node in fake.graph.values()} == NODE_SETS[feature]
    assert result["outputs"][0]["bytes"] > 0
    assert result["outputs"][0]["frames"] == {"core": 5, "image-edit": 1, "stages": 3}[feature]
    assert result["timings"] == {
        "queue_wait_seconds": 0.9,
        "run_seconds": 0.1,
        "source": "history timestamps",
    }
    assert all(stage["status"] == "ok" for stage in result["stages"])
    paths = [route for _, route, _ in fake.calls]
    assert paths.count("/object_info") == 2  # Preflight plus the gateway's real validation.
    assert paths.index("/object_info") < paths.index("/upload/image") < paths.index("/prompt")
    assert paths[-1] == "/free"
    assert json.loads(fake.calls[-1][2]) == {"unload_models": True, "free_memory": True}
    assert json.loads((tmp_path / "out" / "report.json").read_text()) == result
    assert json.loads((tmp_path / "out" / "graph.json").read_text()) == fake.graph
    if feature == "core":
        assert fake.graph["save_video"]["inputs"]["format.codec"] == "h264"


@pytest.mark.parametrize(
    "mode, message",
    [
        ("unreachable", "unreachable"),
        ("execution_error", "missing weight"),
        ("timeout", "exceeded"),
        ("no_output", "without a delivered output"),
        ("empty_output", "Empty output"),
        ("renamed", "renamed a staged input"),
        ("rejected", "model not found"),
        ("free_error", "Could not release"),
    ],
)
def test_failure_paths(mode, message, install_fake, tmp_path):
    fake = install_fake(mode=mode)
    result = smoke.run_smoke(out=tmp_path / "out", timeout=0.02)
    assert result["result"] == "FAIL", result
    assert message in result["error"]
    assert "python -m gaiavfx.setup doctor" in result["fix"]
    assert fake.calls[-1][1] == "/free"
    if mode == "timeout":
        assert ("POST", "/queue", b'{"delete":["our-prompt"]}') in fake.calls
        assert ("POST", "/interrupt", b'{"prompt_id":"our-prompt"}') in fake.calls
    if mode == "unreachable":
        assert fake.graph is None
        assert not (tmp_path / "out").exists()


def test_missing_node_names_the_fix_before_upload(install_fake, schema, tmp_path):
    fake = install_fake()
    del schema["GaiaH3PowerShift"]
    result = smoke.run_smoke(out=tmp_path / "out")
    assert result["result"] == "FAIL"
    assert "ComfyUI is missing GaiaH3PowerShift" in result["error"]
    assert "python -m gaiavfx.setup doctor" in result["fix"]
    assert not any(route in {"/upload/image", "/prompt"} for _, route, _ in fake.calls)


def test_cli_json_and_registered_handler(install_fake, tmp_path, capsys):
    install_fake("image-edit")
    parser = argparse.ArgumentParser()
    smoke.register(parser.add_subparsers(dest="command", required=True))
    args = parser.parse_args(
        [
            "smoke",
            "--feature",
            "image-edit",
            "--comfy-url",
            "http://fake:8188",
            "--out",
            str(tmp_path / "out"),
            "--timeout",
            "2.5",
            "--json",
        ]
    )
    assert args.timeout == 2.5
    assert args.func(args) == 0
    printed = json.loads(capsys.readouterr().out)
    assert printed["result"] == "PASS"
    assert printed["feature"] == "image-edit"
    assert printed["comfy_url"] == "http://fake:8188"
    assert set(printed["checks"][0]) == {"feature", "item", "status", "detail", "fix"}
    assert printed["checks"][0]["status"] == "ok"


def test_cli_failure_is_one_json_result(install_fake, tmp_path, capsys):
    install_fake(mode="unreachable")
    assert smoke.main(["--json", "--out", str(tmp_path / "out")]) == 1
    result = json.loads(capsys.readouterr().out)
    assert result["result"] == "FAIL"
    assert result["checks"][0]["status"] == "missing"


def test_cli_human_progress_and_final_line(install_fake, tmp_path, capsys):
    install_fake("image-edit")
    assert smoke.main(["--feature", "image-edit", "--out", str(tmp_path / "out")]) == 0
    output = capsys.readouterr().out
    for word in ("upload:", "validate:", "queue wait:", "run time:", "bytes, 1 frames"):
        assert word in output
    assert output.endswith("PASS: image-edit smoke\n")


@pytest.mark.parametrize(
    "args",
    [
        ["--feature", "other"],
        ["--timeout", "0"],
        ["--timeout", "-1"],
        ["--timeout", "nan"],
        ["--timeout", "inf"],
        ["--timeout", "oops"],
    ],
)
def test_cli_rejects_invalid_settings(args):
    with pytest.raises(SystemExit) as exc:
        smoke.main(args)
    assert exc.value.code == 2


def test_default_output_stays_in_runtime(install_fake, tmp_path, monkeypatch):
    install_fake("image-edit")
    monkeypatch.chdir(tmp_path)
    result = smoke.run_smoke(feature="image-edit")
    assert result["result"] == "PASS"
    assert result["out"].replace("\\", "/").startswith(".runtime/smoke/")


def test_output_mismatch_is_not_a_pass(install_fake, tmp_path):
    fake = install_fake("image-edit")
    wrong = io.BytesIO()
    Image.new("RGB", (32, 32)).save(wrong, format="PNG")
    fake.payloads["image-edit"] = wrong.getvalue()
    result = smoke.run_smoke(feature="image-edit", out=tmp_path / "out")
    assert result["result"] == "FAIL"
    assert "dimensions" in result["error"]
