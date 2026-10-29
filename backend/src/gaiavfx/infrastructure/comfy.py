from __future__ import annotations

from contextlib import nullcontext
from pathlib import Path

import httpx

from gaiavfx.shared.errors import ApplicationError
from gaiavfx.shared.telemetry import observation, observed


class ComfyGateway:
    def request(self, url: str, method: str, route: str, **kwargs):
        trace = (
            observation(
                "comfy." + route.strip("/").replace("/", "."),
                input={"method": method, "route": route, "body": kwargs.get("json")},
            )
            if method != "GET"
            else nullcontext()
        )
        with (
            trace as span,
            httpx.Client(timeout=httpx.Timeout(30, connect=5), follow_redirects=False) as client,
        ):
            response = client.request(method, url.rstrip("/") + route, **kwargs)
            if span:
                span.update(output={"status": response.status_code, "body": response.text})
            response.raise_for_status()
            return (
                response.json()
                if response.content and "json" in response.headers.get("content-type", "")
                else {}
            )

    @observed("comfy.validate_graph", as_type="guardrail")
    def validate_graph(self, url: str, graph: dict):
        schema = self.request(url, "GET", "/object_info")
        for node in graph.values():
            if node["class_type"] not in schema:
                raise ApplicationError("ComfyUI is missing " + node["class_type"], 422)
            node_schema = schema[node["class_type"]].get("input", {})
            inputs = {**node_schema.get("required", {}), **node_schema.get("optional", {})}
            # Validate actual numeric inputs after frame-grid rounding and context
            # expansion. Quality recommendations are never submission limits.
            for name, value in node["inputs"].items():
                spec = inputs.get(name)
                if not spec or spec[0] not in ("INT", "FLOAT") or isinstance(value, list):
                    continue
                bounds = spec[1] if len(spec) > 1 and isinstance(spec[1], dict) else {}
                if not isinstance(value, (int, float)) or isinstance(value, bool):
                    continue
                for bound, outside in (
                    ("min", value < bounds.get("min", float("-inf"))),
                    ("max", value > bounds.get("max", float("inf"))),
                ):
                    if outside:
                        raise ApplicationError(
                            f"{node['class_type']} input {name} is {value:g}; the connected workflow's {bound} is {bounds[bound]:g}. Adjust the generation settings.",
                            422,
                        )
            if node["class_type"] == "SaveVideo":
                formats = schema["SaveVideo"].get("input", {}).get("required", {}).get("format", [])
                if formats and formats[0] == "COMFY_DYNAMICCOMBO_V3":
                    node["inputs"]["format.codec"] = node["inputs"].pop("codec", "h264")
        return graph

    @observed("comfy.upload", media_inputs=("source",))
    def upload(self, url: str, source: Path, filename: str, directory_type: str = "input"):
        folder, name = filename.rsplit("/", 1)
        with source.open("rb") as file, httpx.Client(timeout=180) as client:
            response = client.post(
                url.rstrip("/") + "/upload/image",
                files={"image": (name, file, "application/octet-stream")},
                data={"subfolder": folder, "type": directory_type, "overwrite": "false"},
            )
            response.raise_for_status()
            result = response.json()
            actual = (result.get("subfolder", "").replace("\\", "/") + "/" + result["name"]).lstrip(
                "/"
            )
            if actual != filename:
                raise ApplicationError(
                    "ComfyUI renamed a staged input; submission stopped to preserve the frozen binding map."
                )

    @observed("comfy.download", media_outputs=("target",))
    def download(self, url: str, descriptor: dict, target: Path):
        target.parent.mkdir(parents=True, exist_ok=True)
        staged = target.with_suffix(target.suffix + ".download")
        with (
            httpx.Client(timeout=180) as client,
            client.stream(
                "GET",
                url.rstrip("/") + "/view",
                params={
                    key: descriptor[key]
                    for key in ("filename", "subfolder", "type")
                    if key in descriptor
                },
            ) as response,
        ):
            response.raise_for_status()
            with staged.open("wb") as file:
                for chunk in response.iter_bytes(1024 * 1024):
                    file.write(chunk)
        staged.replace(target)
