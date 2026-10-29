"""Deterministic in-process ComfyUI gateway for operation and CLI tests."""

from __future__ import annotations

import hashlib
from pathlib import Path


class Crash(BaseException):
    """Simulate process death without invoking the runner's error handler."""


class FakeComfy:
    def __init__(self, outputs_for=None, polls_to_complete=2):
        self.max_upload_bytes = 100000000
        self.upload_sizes = []
        self.outputs_for = outputs_for or (lambda graph: ({}, {}))
        self.polls_to_complete = polls_to_complete
        self.calls = []
        self.posts = []
        self.uploads = []
        self.upload_directories = []
        self.downloads = []
        self.prompts = {}
        self.history = {}
        self.payloads = {}
        self.crash_after_accept = None
        self.crash_on_poll = None
        self.execution_error = False
        self.fail_graph = None
        self.on_request = None
        self.drop_prompt = False
        self._polls = 0

    def validate_graph(self, _url, graph):
        return graph

    def request(self, _url, method, route, **kwargs):
        self.calls.append((method, route, kwargs))
        if self.on_request:
            self.on_request()
        if method == "POST" and route == "/prompt":
            body = kwargs["json"]
            prompt_id = str(len(self.posts) + 1)
            prompt = [0, prompt_id, body["prompt"], body["extra_data"]]
            self.posts.append(prompt)
            if not self.drop_prompt:
                self.prompts[prompt_id] = {"prompt": prompt, "polls": 0, "state": "pending"}
            if self.crash_after_accept:
                error = self.crash_after_accept
                self.crash_after_accept = None
                raise error("lost response")
            return {"prompt_id": prompt_id}
        if method == "GET" and route == "/queue":
            return {
                "queue_running": [
                    item["prompt"] for item in self.prompts.values() if item["state"] == "running"
                ],
                "queue_pending": [
                    item["prompt"] for item in self.prompts.values() if item["state"] == "pending"
                ],
            }
        if method == "GET" and route.startswith("/history?max_items="):
            return dict(self.history)
        if method == "GET" and route.startswith("/history/"):
            self._polls += 1
            if self.crash_on_poll == self._polls:
                self.crash_on_poll = None
                raise Crash("poll crash")
            prompt_id = route.removeprefix("/history/")
            item = self.prompts.get(prompt_id)
            if item is None:
                return {prompt_id: self.history[prompt_id]} if prompt_id in self.history else {}
            if item["state"] == "pending" and item["polls"] > 0:
                item["state"] = "running"
            item["polls"] += 1
            if item["polls"] < self.polls_to_complete:
                return {}
            if self.execution_error or (self.fail_graph and self.fail_graph(item["prompt"][2])):
                entry = {
                    "prompt": item["prompt"],
                    "status": {
                        "status_str": "error",
                        "messages": [["execution_error", "fake error"]],
                    },
                    "outputs": {},
                }
            else:
                outputs, payloads = self.outputs_for(item["prompt"][2])
                self.payloads.update(payloads)
                entry = {
                    "prompt": item["prompt"],
                    "status": {"status_str": "success"},
                    "outputs": outputs,
                }
            self.history[prompt_id] = entry
            del self.prompts[prompt_id]
            return {prompt_id: entry}
        if method == "POST" and route == "/queue":
            for prompt_id in kwargs["json"].get("delete", []):
                self.prompts.pop(prompt_id, None)
            return {}
        if method == "POST" and route == "/interrupt":
            prompt_id = kwargs["json"]["prompt_id"]
            item = self.prompts.pop(prompt_id, None)
            if item:
                self.history[prompt_id] = {
                    "prompt": item["prompt"],
                    "status": {
                        "status_str": "error",
                        "messages": [["execution_interrupted", "interrupted"]],
                    },
                    "outputs": {},
                }
            return {}
        raise AssertionError((method, route))

    def upload(self, _url, source: Path, filename: str, directory_type="input"):
        folder, name = filename.rsplit("/", 1)
        assert folder and name and "\\" not in filename, "ComfyUI uploads require folder/name"
        size = source.stat().st_size
        if size >= self.max_upload_bytes:
            raise ValueError("ComfyUI upload exceeds max_upload_bytes")
        self.upload_sizes.append(size)
        self.calls.append(("UPLOAD", filename, {}))
        if self.on_request:
            self.on_request()
        self.uploads.append((hashlib.sha256(source.read_bytes()).hexdigest(), filename))
        self.upload_directories.append(directory_type)

    def download(self, _url, descriptor: dict, target: Path):
        self.calls.append(("DOWNLOAD", descriptor["output_key"], {}))
        if self.on_request:
            self.on_request()
        self.downloads.append(descriptor["output_key"])
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.payloads[descriptor["output_key"]])


class ReplacementComfy(FakeComfy):
    """Deterministic SAM mask batches and H3 crop videos, with no inference runtime."""

    def __init__(self, directory, media, people=2, shift=0):
        super().__init__(self.replacement_outputs, polls_to_complete=1)
        self.directory, self.media, self.people, self.shift = directory, media, people, shift
        self.files = {}
        self.short_tracks = {}
        self.mask_transform = None
        self.face_reports = None
        # Window face scans: (box, inner-lip gap in pixels); the default face is unmeasured.
        # None imitates a ComfyUI started before the node change: no landmarks at all.
        self.window_face = ((60, 60, 110, 120), 0.0)

    def upload(self, url, source, filename, directory_type="input"):
        super().upload(url, source, filename, directory_type)
        self.files[filename] = source

    def replacement_outputs(self, graph):
        import io
        import json

        import numpy as np
        from PIL import Image

        from gaiavfx.application.replacement_steps import decode_rgb, lossless_video

        scans = {key: node for key, node in graph.items() if node["class_type"] == "GaiaFaceScan"}
        scan_outputs, scan_payloads = {}, {}
        for key, node in scans.items():
            inputs = node["inputs"]
            assert inputs["video"] in self.files
            frames = np.asarray(json.loads(inputs["frames_json"]), dtype=np.int32)
            n = len(frames)
            embedding = np.zeros((n, 512), np.float16)
            embedding[:, 0] = 1
            arrays = {}
            box = (60, 60, 110, 120)
            if inputs.get("landmarks") and self.window_face is not None:
                box, gap = self.window_face
                landmarks = np.zeros((n, 106, 2), np.float32)
                landmarks[:, 62, 1] = gap
                arrays["landmark_106"] = landmarks
            data = io.BytesIO()
            np.savez_compressed(
                data,
                frame=frames,
                box=np.tile(np.array(box, np.float32), (n, 1)),
                score=np.full(n, 0.9, np.float32),
                kps=np.zeros((n, 5, 2), np.float32),
                embedding=embedding,
                **arrays,
            )
            scan_outputs[key] = {"reports": [{"filename": "faces.npz", "type": "output"}]}
            scan_payloads[f"{key}:reports:0"] = data.getvalue()
        if scans and "concept_0_track" not in graph:
            return scan_outputs, scan_payloads

        faces = {key: node for key, node in graph.items() if node["class_type"] == "GaiaFaceCheck"}
        if faces:
            report = {
                "schema": 1,
                "frames": [],
                "summary": {
                    "candidate": {"to_target": {"median": None}, "to_source": {"median": None}}
                },
                "mouth": {"pearson_r": None, "max_ratio": None},
            }
            return (
                {
                    key: {"text": [json.dumps((self.face_reports or {}).get(key, report))]}
                    for key in faces
                },
                {},
            )
        source = self.files[graph["source"]["inputs"]["file"]]
        if "concept_0_track" in graph:
            frames = decode_rgb(self.media, source)
            height, width = frames.shape[1:3]
            outputs, payloads = {}, {}
            for key, node in graph.items():
                if node["class_type"] != "SaveImage":
                    continue
                concept_index, instance = int(key.split("_")[1]), int(key.split("_")[3])
                concept_node = graph.get(f"concept_{concept_index}_text")
                concept = concept_node["inputs"]["text"] if concept_node else "person"
                mask = np.zeros((height, width), np.uint8)
                if concept == "person" and instance < self.people:
                    x = int(width * (0.23 + 0.30 * instance))
                    mask[height // 3 : height * 2 // 3, x - 16 : x + 16] = 255
                elif concept != "person" and instance == 0:
                    x = int(width * 0.23) + 16
                    mask[height // 2 : height // 2 + 10, x - 2 : x + 10] = 255
                data = io.BytesIO()
                Image.fromarray(mask).save(data, "PNG")
                outputs[key] = {"images": []}
                for i in range(self.short_tracks.get(key, len(frames))):
                    outputs[key]["images"].append(
                        {"filename": f"{key}-{i:04}.png", "type": "output"}
                    )
                    if self.mask_transform:
                        data = io.BytesIO()
                        Image.fromarray(self.mask_transform(key, i, mask.copy())).save(data, "PNG")
                    payloads[f"{key}:images:{i}"] = data.getvalue()
            outputs.update(scan_outputs)
            payloads.update(scan_payloads)
            return outputs, payloads
        frames = decode_rgb(self.media, source).copy()
        canvas = graph["conditioning"]["inputs"]
        assert frames.shape[1:3] == (canvas["height"], canvas["width"])
        if self.shift:
            frames = np.roll(frames, self.shift, axis=2)
        h, w = frames.shape[1:3]
        frames[:, h // 2 - 8 : h // 2 + 8, w // 2 - 8 : w // 2 + 8] = (190, 60, 110)
        target = self.directory / f"fake-render-{len(self.posts)}.mkv"
        lossless_video(self.media, frames, target, 24)
        return (
            {"save_video": {"videos": [{"filename": target.name, "type": "output"}]}},
            {"save_video:videos:0": target.read_bytes()},
        )
