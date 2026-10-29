"""Standalone node helpers and CPU execution protocol, without inference packages."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

FILE = Path(__file__).parents[2] / "comfy_nodes/gaiavfx/scan.py"
spec = importlib.util.spec_from_file_location("film_scan_node", FILE)
scan = importlib.util.module_from_spec(spec)
spec.loader.exec_module(scan)


def face(box=(1, 2, 31, 42)):
    return {"bbox": box, "det_score": 0.8, "kps": np.ones((5, 2)), "embedding": np.ones(512)}


@pytest.mark.parametrize(
    "value", ["{}", "[true]", "[1,0]", "[1,1]", "[-1]", "[0.5]", "[2147483648]"]
)
def test_frame_selection_rejects_bad_indices(value):
    with pytest.raises(ValueError):
        scan.selected_frames(value)


def test_npz_shapes_types_normalization_and_canonical_face_order():
    assert scan.selected_frames("[0,4,7]") == [0, 4, 7]
    rows = [(4, face()), (0, face((20, 2, 50, 42))), (0, face())]
    result = scan.assemble_faces(rows)
    assert result["frame"].tolist() == [0, 0, 4]
    assert result["box"][0].tolist() == [1, 2, 31, 42]
    assert result["embedding"].shape == (3, 512)
    np.testing.assert_allclose(
        np.linalg.norm(result["embedding"].astype(np.float64), axis=1), 1, atol=0.001
    )
    empty = scan.assemble_faces([])
    assert {key: (v.shape, v.dtype) for key, v in empty.items()} == {
        "frame": ((0,), np.dtype("int32")),
        "box": ((0, 4), np.dtype("float32")),
        "score": ((0,), np.dtype("float32")),
        "kps": ((0, 5, 2), np.dtype("float32")),
        "embedding": ((0, 512), np.dtype("float16")),
    }


def test_node_decodes_sequentially_only_analyzes_requested_frames(tmp_path, monkeypatch):
    source = tmp_path / "input" / "job" / "film.mkv"
    source.parent.mkdir(parents=True)
    source.touch()
    monkeypatch.setitem(
        sys.modules,
        "folder_paths",
        SimpleNamespace(
            get_input_directory=lambda: str(tmp_path / "input"),
            get_output_directory=lambda: str(tmp_path / "output"),
            models_dir=str(tmp_path / "models"),
        ),
    )
    decoded, analyzed = [], []

    stream = SimpleNamespace(thread_type=None)

    class Container:
        streams = SimpleNamespace(video=[stream])

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

        def decode(self, video):
            assert video is stream
            assert stream.thread_type == "AUTO"
            for i in range(10):
                decoded.append(i)
                yield SimpleNamespace(
                    to_ndarray=lambda format, i=i: np.full((50, 60, 3), i, np.uint8)
                )

    class Model:
        def get(self, pixels):
            analyzed.append(int(pixels[0, 0, 0]))
            return [face()]

    monkeypatch.setitem(sys.modules, "av", SimpleNamespace(open=lambda path: Container()))
    models = []
    monkeypatch.setattr(scan, "_model", lambda root, size, marks: models.append(marks) or Model())
    interrupted, progress = [], []

    class Progress:
        def __init__(self, count):
            progress.append(("total", count))

        def update(self, amount):
            progress.append(("update", amount))

    management = SimpleNamespace(
        throw_exception_if_processing_interrupted=lambda: interrupted.append(True)
    )
    monkeypatch.setitem(sys.modules, "comfy", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "comfy.model_management", management)
    monkeypatch.setitem(sys.modules, "comfy.utils", SimpleNamespace(ProgressBar=Progress))
    result = scan.GaiaFaceScan().scan("job/film.mkv", json.dumps([0, 4, 7]))
    assert decoded == list(range(8)) and analyzed == [0, 4, 7]
    assert interrupted == [True] * 3
    assert progress == [("total", 3)] + [("update", 1)] * 3
    report = result["ui"]["reports"][0]
    with np.load(tmp_path / "output" / report["subfolder"] / report["filename"]) as data:
        assert data["frame"].tolist() == [0, 4, 7]
        assert "landmark_106" not in data
    assert models == [False]
    with pytest.raises(ValueError, match="uploaded"):
        scan.GaiaFaceScan().scan("../outside.mkv", "[0]")
    with pytest.raises(ValueError, match="beyond"):
        scan.GaiaFaceScan().scan("job/film.mkv", "[12]")

    def cancel():
        raise RuntimeError("interrupted")

    management.throw_exception_if_processing_interrupted = cancel
    before = len(analyzed)
    with pytest.raises(RuntimeError, match="interrupted"):
        scan.GaiaFaceScan().scan("job/film.mkv", "[0]")
    assert len(analyzed) == before


def test_cached_model_requires_installed_files_and_cpu_provider(tmp_path, monkeypatch):
    calls = []

    class App:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def prepare(self, **kwargs):
            calls.append(kwargs)

    monkeypatch.setitem(sys.modules, "insightface", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "insightface.app", SimpleNamespace(FaceAnalysis=App))
    with pytest.raises(ValueError, match="Install"):
        scan._model(str(tmp_path), 640)
    root = tmp_path / "models/buffalo_l"
    root.mkdir(parents=True)
    for name in ("det_10g.onnx", "w600k_r50.onnx"):
        (root / name).touch()
    assert scan._model(str(tmp_path), 640) is scan._model(str(tmp_path), 640)
    assert len(calls) == 2
    assert calls[0]["providers"] == ["CPUExecutionProvider"]
    assert calls[0]["allowed_modules"] == ["detection", "recognition"]
    assert calls[1] == {"ctx_id": -1, "det_size": (640, 640)}
    with pytest.raises(ValueError, match="landmark"):
        scan._model(str(tmp_path), 640, True)
    (root / "2d106det.onnx").touch()
    assert scan._model(str(tmp_path), 640, True) is not scan._model(str(tmp_path), 640)
    assert calls[2]["allowed_modules"] == ["detection", "recognition", "landmark_2d_106"]


def test_window_scan_adds_106_point_landmarks_only_on_request():
    marked = {**face(), "landmark_2d_106": np.arange(212, dtype=float).reshape(106, 2)}
    result = scan.assemble_faces([(3, marked), (1, marked)], landmarks=True)
    assert result["landmark_106"].shape == (2, 106, 2)
    assert result["landmark_106"].dtype == np.float32
    assert result["landmark_106"][0, 105].tolist() == [210, 211]
    empty = scan.assemble_faces([], landmarks=True)
    assert empty["landmark_106"].shape == (0, 106, 2)
    with pytest.raises(ValueError, match="landmarks"):
        scan.assemble_faces([(0, face())], landmarks=True)
    assert scan.GaiaFaceScan.INPUT_TYPES()["optional"] == {
        "landmarks": ("BOOLEAN", {"default": False})
    }
