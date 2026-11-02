"""A fake ComfyUI's answers to Panoramas' graphs, on the synthetic room (`synthetic_room.py`), for the durable runner's
tests and the browser service. No model; the room stands in for each:
- Krea draws the room as a panorama, tinted by its seed, with a seam (its joined edges are shifted apart);
- Fibo fills every green pixel of the picture it was given with the room, and answers at its own smaller size;
- MoGe measures a picture as a 70-degree lens, and a panorama as the room's exact distance;
- SAM finds nothing."""

import io
import math
from pathlib import Path

import numpy as np
import synthetic_room as room
from PIL import Image

WIDTH, HEIGHT = 512, 256  # Krea's panorama here (the real one is 2048 x 1024)
FILL = (384, 192)  # Fibo's answer, smaller than its input, as the real 1536 x 768 is


def _png(array) -> bytes:
    stream = io.BytesIO()
    Image.fromarray(np.asarray(array, np.uint8)).save(stream, format="PNG")
    return stream.getvalue()


def drawn(seed: int) -> np.ndarray:
    image, _ = room.panorama(WIDTH, HEIGHT, phase=0.1 * (seed % 7))
    tinted = np.clip(image.astype(np.int16) + (seed % 5) * 6, 0, 255).astype(np.uint8)
    # The seam: the leftmost columns darker, so the joined edges do not meet.
    tinted[:, : WIDTH // 64] = np.clip(tinted[:, : WIDTH // 64].astype(np.int16) - 40, 0, 255)
    return tinted


def _saved(field, name, payload):
    return {"save": {field: [{"filename": name, "subfolder": "x", "type": "output"}]}}, {
        f"save:{field}:0": payload
    }


class PanoramaAnswers:
    def __init__(self):
        self.files = {}
        self.graphs = []

    def keep(self, filename: str, source: Path) -> None:
        """Remember an upload's bytes, as ComfyUI's input folder would."""
        self.files[filename] = Path(source).read_bytes()

    def _input(self, name: str) -> np.ndarray:
        return np.asarray(Image.open(io.BytesIO(self.files[name])).convert("RGB"))

    def answer(self, graph: dict):
        """The fake history outputs and payloads for a panorama graph, or None for any other graph."""
        kinds = {node["class_type"] for node in graph.values()}
        if "TextEncodeFibo2" in kinds:
            self.graphs.append(graph)
            picture = self._input(graph["image_1"]["inputs"]["image"])
            scenery, _ = room.panorama(picture.shape[1], picture.shape[0])
            green = (np.abs(picture.astype(np.int16) - (0, 255, 0)).max(-1) <= 40)[..., None]
            filled = np.where(green, scenery, picture).astype(np.uint8)
            return _saved("images", "fill.png", _png(Image.fromarray(filled).resize(FILL)))
        if "LoraLoaderModelOnly" in kinds and "ConditioningZeroOut" in kinds:
            self.graphs.append(graph)
            return _saved(
                "images", "panorama.png", _png(drawn(int(graph["sample"]["inputs"]["seed"])))
            )
        if "MoGePanoramaInference" in kinds and "GaiaSaveMoGeGeometry" in kinds:
            self.graphs.append(graph)
            picture = self._input(graph["panorama"]["inputs"]["image"])
            _, distance = room.panorama(picture.shape[1], picture.shape[0])
            return _saved("geometry", "geometry.npz", room.panorama_npz(distance))
        if "MoGeInference" in kinds and "GaiaSaveMoGeGeometry" in kinds:
            self.graphs.append(graph)
            picture = self._input(graph["picture"]["inputs"]["image"])
            height, width = picture.shape[:2]
            # A 70-degree lens, its focal length normalised by the width as MoGe reports it.
            focal = 0.5 / math.tan(math.radians(35))
            stream = io.BytesIO()
            np.savez_compressed(
                stream,
                depth=np.full((height, width), 2.0, np.float32),
                mask=np.ones((height, width), bool),
                sky=np.zeros((height, width), bool),
                glass=np.zeros((height, width), bool),
                intrinsics=np.array(
                    [[focal, 0, 0.5], [0, focal * width / height, 0.5], [0, 0, 1]], np.float32
                ),
            )
            return _saved("geometry", "geometry.npz", stream.getvalue())
        return None
