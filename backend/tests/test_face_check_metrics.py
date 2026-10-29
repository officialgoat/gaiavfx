"""Numerical diagnostics and the InsightFace adapter, without an inference runtime."""

import importlib.util
import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

NODES = Path(__file__).parents[2] / "comfy_nodes" / "gaiavfx"
spec = importlib.util.spec_from_file_location("face_metrics", NODES / "metrics.py")
metrics = importlib.util.module_from_spec(spec)
spec.loader.exec_module(metrics)


def face(vector=(1, 0), box=(0, 0, 10, 20), gap=4, score=0.9):
    landmarks = np.zeros((106, 2))
    landmarks[62] = (5, 8)
    landmarks[60] = (5, 8 + gap)
    return {
        "bbox": np.array(box),
        "embedding": np.array(vector),
        "det_score": score,
        "landmark_2d_106": landmarks,
    }


def test_face_selection_mask_overlap_and_front_score_then_width():
    other, selected = face(box=(10, 0, 20, 20)), face(score=0.5)
    mask = np.zeros((20, 20))
    mask[:4, :10] = 1  # exactly 20% of the selected box
    assert metrics.choose_face([other, selected], mask) is selected
    mask[3] = 0
    assert metrics.choose_face([selected], mask) is None
    assert metrics.choose_face([face(vector=(0, 0))], np.ones((20, 20))) is None
    wide = face(box=(0, 0, 30, 20))
    assert metrics.front_face([other, wide]) is wide
    assert metrics.front_face([wide, selected]) is wide
    assert metrics.front_face([]) is None


def test_cosine_aperture_and_missing_data():
    assert metrics.cosine([3, 0], [0, 4]) == 0
    assert metrics.cosine([3, 0], [4, 0]) == 1
    assert metrics.cosine([3, 0], [-4, 0]) == -1
    for bad in (None, [0, 0], [float("nan"), 0]):
        assert metrics.cosine(bad, [1, 0]) is None
    f = face()
    assert metrics.mouth_aperture(f["landmark_2d_106"], f["bbox"]) == 0.2
    assert metrics.mouth_aperture(None, f["bbox"]) is None
    assert metrics.mouth_aperture(np.zeros((68, 2)), f["bbox"]) is None
    assert metrics.summary([None, None]) == {"frames": 0, "median": None, "p25": None, "p75": None}
    assert metrics.summary([1, 2, 3, 4]) == {"frames": 4, "median": 2.5, "p25": 1.25, "p75": 3.75}
    assert metrics.summary([None, 3])["p25"] == 3


def test_mouth_pairing_pearson_ratio_and_undefined_constant_series():
    constant = metrics.mouth_agreement([0.1] * 10, [0.2] * 10)
    assert constant["pearson_r"] is None and constant["reason"] == "constant mouth aperture series"
    assert metrics.mouth_agreement([2, None, 4, 6] * 3, [1, 9, 2, 3] * 3) == {
        "pearson_r": 1,
        "max_ratio": 2,
        "paired_frames": 9,
        "reason": "",
    }
    assert metrics.mouth_agreement([1, 1], [0, 0]) == {
        "pearson_r": None,
        "max_ratio": None,
        "paired_frames": 2,
        "reason": "too few paired mouth frames",
    }
    assert metrics.mouth_agreement([1, 2] * 4, [2, 1] * 4)["pearson_r"] == pytest.approx(-1)
    assert metrics.mouth_agreement([0.01] * 8, [0] * 8)["max_ratio"] == 5
    assert metrics.mouth_agreement([0.01] * 8, [0.001] * 8)["max_ratio"] == 5
    # Eight pairs satisfy a 16-frame shot; a 17-frame shot needs nine.
    assert metrics.mouth_agreement(list(range(8)) + [None] * 8, list(range(16)))["pearson_r"] == 1
    sparse = metrics.mouth_agreement(list(range(8)) + [None] * 9, list(range(17)))
    assert sparse["pearson_r"] is sparse["max_ratio"] is None
    assert sparse["reason"] == "too few paired mouth frames"
    with pytest.raises(ValueError):
        metrics.mouth_agreement([1], [])


def test_measure_normalizes_source_average_and_keeps_null_frames():
    result = metrics.measure(
        [[face((1, 0), gap=2)], [], [face((0, 1), gap=6)]],
        [[face((3, 0), gap=1)], [face((0, 4), gap=2)], [face((0, 4), gap=3)]],
        [face((1, 0))],
        np.ones((3, 20, 20)),
    )
    assert result["frames"][1]["candidate"] is None
    assert result["frames"][0]["candidate"]["to_source"] == pytest.approx(1 / np.sqrt(5))
    assert result["summary"]["candidate"]["frames_with_face"] == 2
    assert result["summary"]["candidate"]["to_target"]["median"] == 0.5
    assert result["mouth"]["pearson_r"] is result["mouth"]["max_ratio"] is None
    assert result["mouth"]["reason"] == "too few paired mouth frames"
    missing = metrics.measure([[]], [[]], [], np.ones((1, 20, 20)))
    assert missing["frames"] == [{"candidate": None, "source": None}]
    assert missing["summary"]["candidate"]["to_source"]["median"] is None
    json.dumps(missing, allow_nan=False)
    with pytest.raises(ValueError):
        metrics.measure([[]], [], [], [])


@pytest.mark.parametrize("blank_root", [False, True])
def test_node_uses_cpu_and_reports_ui_text_with_mocked_insightface(
    tmp_path, monkeypatch, blank_root
):
    spec = importlib.util.spec_from_file_location(
        "test_people_node", NODES / "people.py", submodule_search_locations=[str(NODES)]
    )
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    calls = []

    class Analyzer:
        def __init__(self, **kwargs):
            calls.append(kwargs)

        def prepare(self, **kwargs):
            calls.append(kwargs)

        def get(self, image):
            assert image.dtype == np.uint8
            assert image[0, 0].tolist() == [0, 0, 255]  # RGB -> BGR
            return [face()]

    monkeypatch.setitem(sys.modules, "insightface", SimpleNamespace())
    monkeypatch.setitem(sys.modules, "insightface.app", SimpleNamespace(FaceAnalysis=Analyzer))
    model_root = tmp_path / "models" / "insightface" if blank_root else tmp_path
    monkeypatch.setitem(
        sys.modules, "folder_paths", SimpleNamespace(models_dir=str(tmp_path / "models"))
    )
    root = model_root / "models" / "buffalo_l"
    root.mkdir(parents=True)
    for name in ("det_10g.onnx", "w600k_r50.onnx", "2d106det.onnx"):
        (root / name).touch()

    class Tensor:
        def __init__(self, array):
            self.array = array

        def detach(self):
            return self

        def cpu(self):
            return self

        def numpy(self):
            return self.array

    image = Tensor(np.broadcast_to([1.0, 0.0, 0.0], (2, 20, 20, 3)))
    mask = Tensor(np.ones((2, 20, 20)))
    output = module.GaiaFaceCheck().check(
        image,
        image,
        image,
        mask,
        "" if blank_root else str(model_root),
        min_face_height_fraction=0.06,
        min_face_frames_fraction=0.25,
    )
    assert output["ui"]["text"] == list(output["result"])
    assert json.loads(output["result"][0])["summary"]["candidate"]["to_source"]["median"] == 1
    assert calls[0]["providers"] == ["CPUExecutionProvider"]
    assert calls[0]["root"] == str(model_root.resolve())
    assert calls[0]["allowed_modules"] == ["detection", "recognition", "landmark_2d_106"]
    assert calls[1]["ctx_id"] == -1
    with pytest.raises(ValueError, match="must be installed"):
        module.GaiaFaceCheck().check(image, image, image, mask, str(tmp_path / "absent"))


def test_small_faces_are_ignored_and_sparse_frames_keep_observations_with_reason():
    mask = np.ones((100, 100))
    small, usable = face(box=(0, 0, 10, 5)), face(box=(0, 0, 10, 6))
    assert metrics.choose_face([small], mask, 0.06) is None
    assert metrics.choose_face([small, usable], mask, 0.06) is usable
    assert metrics.front_face([small], min_height=6) is None
    for count, reason in [(0, "no usable face"), (1, "too few usable face frames"), (2, "")]:
        report = metrics.measure(
            [[usable]] * count + [[small]] * (8 - count),
            [[usable]] * 8,
            [usable],
            np.ones((8, 100, 100)),
            min_face_height_fraction=0.06,
            min_face_frames_fraction=0.25,
            target_frame_height=100,
        )
        assert report["reason"] == reason
        assert report["summary"]["candidate"]["frames_with_face"] == count
        assert report["summary"]["candidate"]["to_source"]["median"] == (1 if count else None)
