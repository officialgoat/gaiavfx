"""CPU InsightFace measurements; reports are observations, never quality verdicts."""

import json
from pathlib import Path

import numpy as np

from .metrics import measure
from .scan import GaiaFaceScan


class GaiaFaceCheck:
    """models_root contains models/buffalo_l; blank uses models/insightface.

    Selected-face observations do not detect a duplicated source person elsewhere
    in the frame. That requires a later person-count check.
    """

    RETURN_TYPES = ("STRING",)
    FUNCTION = "check"
    CATEGORY = "GAIAVFX/People"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "candidate": ("IMAGE",),
                "source": ("IMAGE",),
                "target": ("IMAGE",),
                "performer": ("MASK",),
                "models_root": ("STRING", {"default": ""}),
                "min_face_height_fraction": (
                    "FLOAT",
                    {"default": 0.0, "min": 0.0, "max": 1.0},
                ),
                "min_face_frames_fraction": (
                    "FLOAT",
                    {"default": 0.0, "min": 0.0, "max": 1.0},
                ),
            }
        }

    def check(
        self,
        candidate,
        source,
        target,
        performer,
        models_root,
        min_face_height_fraction=0.0,
        min_face_frames_fraction=0.0,
    ):
        from insightface.app import FaceAnalysis

        if not models_root:
            import folder_paths

            models_root = str(Path(folder_paths.models_dir) / "insightface")
        # Require installed models: FaceAnalysis otherwise downloads implicitly.
        root = Path(models_root).expanduser().resolve()
        required = ("det_10g.onnx", "w600k_r50.onnx", "2d106det.onnx")
        if any(
            not (root / "models" / "buffalo_l" / name).is_file() for name in required
        ):
            raise ValueError(
                "buffalo_l detection, recognition and 106-point models must be installed"
            )
        arrays = [
            v.detach().cpu().numpy() for v in (candidate, source, target, performer)
        ]
        candidate, source, target, masks = arrays
        if (
            candidate.shape != source.shape
            or candidate.ndim != 4
            or candidate.shape[-1] != 3
            or masks.shape != source.shape[:3]
            or target.ndim != 4
            or not len(target)
            or target.shape[-1] != 3
            or not len(source)
        ):
            raise ValueError(
                "face checks require aligned candidate, source and performer batches"
            )
        app = FaceAnalysis(
            name="buffalo_l",
            root=str(root),
            providers=["CPUExecutionProvider"],
            allowed_modules=["detection", "recognition", "landmark_2d_106"],
        )
        app.prepare(ctx_id=-1, det_size=(640, 640))

        def faces(frame):
            bgr = np.ascontiguousarray(
                np.rint(np.clip(frame, 0, 1) * 255).astype(np.uint8)[..., ::-1]
            )
            return app.get(bgr)

        report = measure(
            [faces(f) for f in candidate],
            [faces(f) for f in source],
            faces(target[0]),
            masks,
            min_face_height_fraction=min_face_height_fraction,
            min_face_frames_fraction=min_face_frames_fraction,
            target_frame_height=target.shape[1],
        )
        text = json.dumps(report, allow_nan=False)
        return {"ui": {"text": [text]}, "result": (text,)}

