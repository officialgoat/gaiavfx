from fractions import Fraction
from itertools import pairwise

import pytest

from gaiavfx.modules.direction.windows import shot_crop_scale_loss, shot_windows


@pytest.mark.parametrize(
    "shot_scale,scales,limit,allowed",
    [
        (0.4, [0.5, 0.75, 1.0], 0.2, True),
        (0.399999, [0.5, 0.75, 1.0], 0.2, False),
        (0.5, [0.5, 1.0, 0.75], 0, True),
        (0.499999, [0.5, 1.0, 0.75], 0, False),
        (0.6, [1.0, 0.5, 0.75], 0.2, True),
        (0.8 * 0.7, [0.7, 0.75, 1.0], 0.2, False),
    ],
)
def test_shot_crop_scale_loss_uses_minimum_window_scale(shot_scale, scales, limit, allowed):
    loss = shot_crop_scale_loss(shot_scale, scales)
    assert (loss <= limit) is allowed
    assert loss == max(0, 1 - shot_scale / min(scales))


@pytest.mark.parametrize(
    "shot_scale,scales",
    [
        (0.4, []),
        (0.4, [0]),
        (0, [0.5]),
        (float("nan"), [0.5]),
        (0.4, [float("inf")]),
        (True, [0.5]),
        (None, [0.5]),
    ],
)
def test_shot_crop_refuses_invalid_scale_comparisons(shot_scale, scales):
    with pytest.raises(ValueError):
        shot_crop_scale_loss(shot_scale, scales)


@pytest.mark.parametrize("frames", [5, 73, 74, 124, 160, 240, 469])
def test_windows_cover_the_shot_without_a_redundant_short_tail(frames):
    windows = shot_windows(19, 19 + frames)
    covered = set()
    for index, (start, end, generated) in enumerate(windows):
        assert 0 < end - start <= 73
        assert generated >= end - start and (generated - 5) % 17 == 0
        assert generated - 17 < end - start
        covered.update(range(start, end))
        if index:
            assert windows[index - 1][1] - start == 22
            assert end - start >= 22
    assert covered == set(range(19, 19 + frames))
    assert len(windows) == (1 if frames <= 73 else 1 + (frames - 73 + 50) // 51)
    # The two overlap contributors sum to exactly one, including both endpoints.
    for j in range(22):
        assert Fraction(j, 21) + Fraction(21 - j, 21) == 1


@pytest.mark.parametrize(
    "start,end,length,overlap", [(0, 0, 73, 22), (-1, 5, 73, 22), (0, 5, 74, 22), (0, 5, 73, 73)]
)
def test_invalid_windows(start, end, length, overlap):
    with pytest.raises(ValueError):
        shot_windows(start, end, length=length, overlap=overlap)


@pytest.mark.parametrize("count", [3, 4, 7])
@pytest.mark.parametrize("tail", [23, 30, 60, 73])
def test_bridge_layout_clips_without_sliding(count, tail):
    from gaiavfx.modules.direction.windows import bridge_layout, delivery_plan

    end = (count - 1) * 51 + tail
    windows = shot_windows(0, end)
    assert len(windows) == count
    bridges = bridge_layout(windows, end)
    for i, bridge in enumerate(bridges, 1):
        start = windows[i - 1][1] - 47
        assert bridge == {
            "join": i,
            "start": start,
            "end": min(start + 73, end),
            "generated": (start + 22, min(start + 51, end)),
            "window_frames": 73,
        }
    assert all(a["generated"][1] <= b["generated"][0] for a, b in pairwise(bridges))
    segments = delivery_plan(windows, bridges)
    assert [f for s in segments for f in range(s["start"], s["end"])] == list(range(end))
    assert all(s["weight_start"] == 1 and s["denominator"] == 5 for s in segments if s["right"])


def test_delivery_crossfade_join_preserves_inclusive_weights():
    from gaiavfx.modules.direction.windows import bridge_layout, delivery_plan

    windows = shot_windows(0, 175)
    segments = delivery_plan(windows, bridge_layout(windows, 175)[:1])
    crossfade = [s for s in segments if s["right"] and s["denominator"] == 21]
    assert len(crossfade) == 1
    assert crossfade[0]["weight_start"] == 0 and crossfade[0]["end"] - crossfade[0]["start"] == 22
