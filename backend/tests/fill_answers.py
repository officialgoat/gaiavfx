"""A fake ComfyUI's answers to a stage's fill round (ADR 0020 §4), on the synthetic room (`synthetic_room.py`), for the
durable runner's tests and the browser service:
- H3's memory probe (`minimax_h3.probe`) renders what its memory shows: the memory video it was given;
- a still's geometry (`worldbuilder.fill`) is the room's true depth where the stills task placed that still (its
  view.json in the operation's scratch), at another scale, as MoGe's would be.
No model; the room stands in for both."""

import io
import json
import re
from pathlib import Path

import numpy as np
import synthetic_room as room


class FillAnswers:
    def __init__(self, root):
        self.root = root  # the library's root, whose staging holds the engine's scratch: a path, or a callable
        self.files = {}
        # Stills (`r<n>p<k>`) whose MoGe depth is bent, so consolidation sets them aside (ADR 0020 §8's kept stills).
        self.bent = set()

    def keep(self, filename: str, source: Path) -> None:
        """Remember an upload's bytes, as ComfyUI's input folder would."""
        self.files[filename] = Path(source).read_bytes()

    def answer(self, graph: dict):
        """The fake history outputs and payloads for a fill graph, or None for any other graph."""
        kinds = {node["class_type"] for node in graph.values()}
        if "MiniMaxH3FunControlNetApply" in kinds:
            video = self.files[graph["source_file"]["inputs"]["file"]]
            saved = {"images": [{"filename": "video.mp4", "subfolder": "x", "type": "output"}]}
            return {"save_video": saved | {"animated": [True]}}, {"save_video:images:0": video}
        if "GaiaSaveStillGeometry" in kinds:
            name = graph["still"]["inputs"]["image"]
            operation, rnd, probe = re.search(r"fill-([^/]+)/r(\d+)/p(\d+)-still", name).groups()
            root = Path(self.root() if callable(self.root) else self.root)
            scratch = root / "staging" / "scratch" / operation / "fill" / f"r{rnd}" / f"p{probe}"
            view = json.loads((scratch / "view.json").read_text(encoding="utf-8"))
            _, depth = room.seen(view["viewmat"], view["K"], view["width"], view["height"])
            if f"r{rnd}p{probe}" in self.bent:
                # A depth no one scale aligns: its residual against the stage is far above 5%.
                depth = depth * np.linspace(0.5, 2.0, depth.shape[1])[None, :]
            stream = io.BytesIO()
            np.savez(
                stream,
                depth=0.7 * depth[None],
                mask=np.ones((1, *depth.shape), np.float32),
                glass=np.zeros(depth.shape, bool),
            )
            saved = {"geometry": [{"filename": "geometry.npz", "subfolder": "x", "type": "output"}]}
            return {"save": saved}, {"save:geometry:0": stream.getvalue()}
        return None
