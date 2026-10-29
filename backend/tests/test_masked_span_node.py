"""CPU-only production span contract; no ComfyUI or torch import."""

import importlib.util
from pathlib import Path

import pytest

_spec = importlib.util.spec_from_file_location(
    "gaiavfx_masked_span", Path(__file__).parents[2] / "comfy_nodes/gaiavfx/masked_span.py"
)
span = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(span)


def test_tail_copies_cycle_aligned_tail():
    assert span.span_mask(22, 7, 0, "tail_to_head", 22) == [(k, 15 + k) for k in range(7)]


def test_aligned_freezes_both_ends():
    assert span.span_mask(22, 7, 7, "aligned", 22) == [(k, k) for k in [*range(7), *range(15, 22)]]


@pytest.mark.parametrize(
    "args",
    [
        (22, 7, 7, "tail_to_head", 22),
        (22, 11, 11, "aligned", 22),
        (22, 7, 7, "aligned", 17),
        (22, 7, 0, "tail_to_head", 23),
        (22, 7, 0, "tail_to_head", 6),
        (22, -1, 0, "aligned", 22),
        (22, 7, 7, "unknown", 22),
    ],
)
def test_span_refusals(args):
    with pytest.raises(ValueError):
        span.span_mask(*args)


@pytest.mark.parametrize("shape", [(1, 16, 22, 64, 56), (1, 8, 22, 64, 64)])
def test_mismatched_canvas_or_channels_refused(shape):
    with pytest.raises(ValueError, match="channels or canvas"):
        span.validate_canvas((1, 16, 22, 64, 64), shape)


def test_source_can_be_unbatched_and_target_must_be_av():
    from types import SimpleNamespace

    span.validate_canvas((1, 16, 22, 64, 64), (16, 22, 64, 64))
    with pytest.raises(ValueError, match="AV latent"):
        span._pair({"samples": object()}, "target")
    with pytest.raises(ValueError, match="joint H3 AV"):
        span.GaiaH3MaskedSpan().apply(
            {"samples": SimpleNamespace(ndim=5)}, {}, "tail_to_head", 7, 0
        )
    video = SimpleNamespace(ndim=5, shape=(1, 16, 22, 64, 64))
    source = SimpleNamespace(ndim=5, shape=(1, 16, 22, 64, 56))
    with pytest.raises(ValueError, match="channels or canvas"):
        span.GaiaH3MaskedSpan().apply(
            {"samples": [video, object()]}, {"samples": source}, "tail_to_head", 7, 0
        )


def test_node_is_registered_and_reported_by_health(tmp_path, monkeypatch):
    from test_comfy_pack import load_pack

    pack, _ = load_pack(tmp_path, monkeypatch)
    assert pack.NODE_CLASS_MAPPINGS["GaiaH3MaskedSpan"].__name__ == "GaiaH3MaskedSpan"
    assert "GaiaH3MaskedSpan" in pack.health_status()["nodes"]
    assert pack.health_status()["version"] == "0.1.0"
