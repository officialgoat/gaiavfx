"""Sequential CPU face scan. Heavy inference/decoder imports stay inside execution."""

import json
from pathlib import Path
from threading import Lock
from uuid import uuid4

import numpy as np

_MODELS = {}
_LOCK = Lock()


def selected_frames(frames_json):
    frames = json.loads(frames_json)
    if (
        not isinstance(frames, list)
        or any(type(i) is not int or not 0 <= i < 2**31 for i in frames)
        or frames != sorted(set(frames))
    ):
        raise ValueError("frames_json must be sorted unique nonnegative frame indices")
    return frames


def assemble_faces(rows, landmarks=False):
    """Rows are (frame index, InsightFace-like mapping); preserve deterministic row identity.

    ``landmarks`` adds the 106-point ``landmark_106`` array a window mouth scan needs; the
    film scan's schema stays unchanged without it.
    """
    rows = sorted(
        rows, key=lambda r: (r[0], *map(float, r[1]["bbox"]), -float(r[1]["det_score"]))
    )
    n = len(rows)
    result = {
        "frame": np.empty(n, np.int32),
        "box": np.empty((n, 4), np.float32),
        "score": np.empty(n, np.float32),
        "kps": np.empty((n, 5, 2), np.float32),
        "embedding": np.empty((n, 512), np.float16),
    }
    if landmarks:
        result["landmark_106"] = np.empty((n, 106, 2), np.float32)
    for i, (frame, face) in enumerate(rows):
        embedding = np.asarray(face["embedding"], dtype=np.float64)
        norm = np.linalg.norm(embedding)
        if embedding.shape != (512,) or not np.isfinite(embedding).all() or norm <= 0:
            raise ValueError("Invalid buffalo_l embedding")
        result["frame"][i] = frame
        result["box"][i] = face["bbox"]
        result["score"][i] = face["det_score"]
        result["kps"][i] = face["kps"]
        result["embedding"][i] = embedding / norm
        if landmarks:
            points = np.asarray(face.get("landmark_2d_106"), dtype=np.float64)
            if points.shape != (106, 2):
                raise ValueError("Invalid buffalo_l 106-point landmarks")
            result["landmark_106"][i] = points
    if any(not np.isfinite(v).all() for v in result.values()):
        raise ValueError("Non-finite face scan output")
    return result


def _model(models_root, det_size, landmarks=False):
    from insightface.app import FaceAnalysis

    root = Path(models_root).expanduser().resolve()
    names = ("det_10g.onnx", "w600k_r50.onnx") + (
        ("2d106det.onnx",) if landmarks else ()
    )
    if any(not (root / "models" / "buffalo_l" / name).is_file() for name in names):
        raise ValueError(
            "Install buffalo_l detection, recognition and landmark models before scanning"
        )
    key = (str(root), det_size, landmarks)
    if key not in _MODELS:
        app = FaceAnalysis(
            name="buffalo_l",
            root=str(root),
            allowed_modules=["detection", "recognition"]
            + (["landmark_2d_106"] if landmarks else []),
            providers=["CPUExecutionProvider"],
        )
        app.prepare(ctx_id=-1, det_size=(det_size, det_size))
        _MODELS[key] = app
    return _MODELS[key]


class GaiaFaceScan:
    RETURN_TYPES = ()
    FUNCTION = "scan"
    CATEGORY = "GAIAVFX/People"
    OUTPUT_NODE = True

    @classmethod
    def INPUT_TYPES(cls):
        return {
            "required": {
                "video": ("STRING",),
                "frames_json": ("STRING",),
                "models_root": ("STRING", {"default": ""}),
                "det_size": ("INT", {"default": 640, "min": 32}),
            },
            "optional": {"landmarks": ("BOOLEAN", {"default": False})},
        }

    def scan(self, video, frames_json, models_root="", det_size=640, landmarks=False):
        import av
        import folder_paths

        frames = selected_frames(frames_json)
        root = Path(folder_paths.get_input_directory()).resolve()
        source = (root / video).resolve()
        if not source.is_relative_to(root) or not source.is_file():
            raise ValueError("Video must be an uploaded ComfyUI input file")
        models_root = models_root or str(Path(folder_paths.models_dir) / "insightface")
        rows, wanted = [], set(frames)
        try:
            from comfy.model_management import throw_exception_if_processing_interrupted
            from comfy.utils import ProgressBar
        except ImportError:
            interrupted = lambda: None
            progress = None
        else:
            interrupted = throw_exception_if_processing_interrupted
            progress = ProgressBar(len(frames))
        # Cached ORT sessions are reused serially; decoded pixels are never accumulated.
        with _LOCK:
            app = _model(models_root, det_size, landmarks)
            if frames:
                with av.open(str(source)) as container:
                    stream = container.streams.video[0]
                    stream.thread_type = "AUTO"
                    for index, frame in enumerate(container.decode(stream)):
                        if index in wanted:
                            interrupted()
                            rows.extend(
                                (index, face)
                                for face in app.get(frame.to_ndarray(format="bgr24"))
                            )
                            wanted.remove(index)
                            if progress is not None:
                                progress.update(1)
                        if index >= frames[-1]:
                            break
                if wanted:
                    raise ValueError(
                        "Requested face frames extend beyond the decoded video"
                    )
        folder = "gaiavfx/" + uuid4().hex
        output = Path(folder_paths.get_output_directory()) / folder / "faces.npz"
        output.parent.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(output, **assemble_faces(rows, landmarks))
        return {
            "ui": {
                "reports": [
                    {"filename": output.name, "subfolder": folder, "type": "output"}
                ]
            },
            "result": (),
        }
