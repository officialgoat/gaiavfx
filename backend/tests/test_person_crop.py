import pytest

from gaiavfx.modules.direction.person_crop import (
    canvas_candidates,
    choose,
    fit,
    forward_matrix,
    grow,
    limits,
    to_canvas,
    to_letterbox,
    to_native,
    to_source,
    union,
)
from gaiavfx.modules.direction.replacement import presence
from gaiavfx.modules.direction.timebase import FrameGrid

BUDGET = 896 * 512

FIT, OFFSET, NATIVE = (896, 504), (0, 4), (1280, 720)
# dev-mug, measured from the 2 October SAM masks (letterbox pixel edges).
ACTOR = grow(to_native((588, 4, 863, 508), FIT, OFFSET, NATIVE), 3, NATIVE)
MUG = grow(to_native((717, 132, 780, 187), FIT, OFFSET, NATIVE), 3, NATIVE)
CHAIR = grow(to_native((508, 292, 666, 512), FIT, OFFSET, NATIVE), 0, NATIVE)
CUP = grow(to_native((426, 355, 478.5, 440), FIT, OFFSET, NATIVE), 0, NATIVE)
WOMAN = grow(to_native((50, 173, 471, 508), FIT, OFFSET, NATIVE), 3, NATIVE)


def dev_mug():
    required = union([ACTOR, MUG, CHAIR])
    bounds = limits(required, [("cup", CUP), ("woman", WOMAN)], NATIVE)
    return (
        required,
        bounds,
        choose(required, NATIVE, 32, bounds, canvas_candidates(BUDGET, 32, 256, 1280, (0.5, 2.0))),
    )


def test_canvas_candidates_stay_on_the_grid_and_budget():
    rows = canvas_candidates(BUDGET, 32, 256, 1280, (0.5, 2.0))
    assert (896, 512) in rows and (608, 736) in rows
    for w, h in rows:
        assert w % 32 == 0 and h % 32 == 0 and w * h <= BUDGET
        assert 0.5 <= w / h <= 2.0


def test_dev_mug_chooses_608x736_and_rejects_tall_portraits():
    required, bounds, (chosen, rows) = dev_mug()
    assert chosen["canvas"] == [608, 736]
    box = chosen["box"]
    assert box[1:] == [0.0, 1280.0, 720.0]
    assert chosen["interior_edges"] == {
        "left": True,
        "top": False,
        "right": False,
        "bottom": False,
    }
    assert (box[2] - box[0]) / (box[3] - box[1]) == pytest.approx(608 / 736)
    assert chosen["scale"] == pytest.approx(736 / 720)
    # The only interior edge keeps feather + safety from the required content and
    # stays clear of the seated woman's cup.
    assert required[0] - box[0] >= 32
    assert box[0] >= bounds[0] == pytest.approx(CUP[2])
    reasons = {tuple(r["canvas"]): r["reason"] for r in rows}
    assert reasons[(512, 896)].startswith("no exact-aspect box")
    assert reasons[(576, 768)].startswith("no exact-aspect box")
    assert reasons[(608, 736)] is None


def test_avoided_region_overlapping_the_subject_is_refused():
    with pytest.raises(ValueError, match="overlaps"):
        limits(ACTOR, [("bad", (900.0, 100.0, 1000.0, 200.0))], NATIVE)


def test_impossible_requirement_returns_none():
    assert fit((0.0, 0.0, 1280.0, 720.0), (256, 512), NATIVE, 32, (0, 0, 1280, 720)) is None


def test_interior_span_keeps_margin_on_both_sides():
    result = fit((500.0, 200.0, 700.0, 400.0), (512, 512), NATIVE, 32, (0, 0, 1280, 720))
    x0, y0, x1, y1 = result["box"]
    assert 500 - x0 >= 32 and x1 - 700 >= 32 and 200 - y0 >= 32 and y1 - 400 >= 32
    assert all(result["interior_edges"].values())


def test_coordinate_round_trips():
    box, scale = [685.2173913043479, 0.0, 1280.0, 720.0], 736 / 720
    for point in [(685.2173913043479 - 0.5, -0.5), (1000.25, 333.75), (1279.5, 719.5)]:
        back = to_source(to_canvas(point, box, scale), box, scale)
        assert back == pytest.approx(point)
    # Box edges map to canvas edges (pixel-centre convention).
    assert to_canvas((box[0] - 0.5, -0.5), box, scale) == pytest.approx((-0.5, -0.5))
    assert to_canvas((1279.5, 719.5), box, scale) == pytest.approx((607.5, 735.5))
    m = forward_matrix(box, scale)
    assert m[0][0] * 1000 + m[0][2] == pytest.approx(to_canvas((1000, 0), box, scale)[0])
    lb = to_letterbox(to_native((10.0, 20.0, 30.0, 40.0), FIT, OFFSET, NATIVE), FIT, OFFSET, NATIVE)
    assert lb == pytest.approx((10.0, 20.0, 30.0, 40.0))


def test_frame_grid_uses_the_supplied_integration_rule():
    grid = FrameGrid(24, 5, 17, 22)
    assert grid.valid_window(73) and grid.valid_window(124) and not grid.valid_window(72)


def test_presence_never_treats_a_missing_face_as_absence():
    assert presence(0, 100.0, None, 0.0) == "absent"
    assert presence(90, 100.0, 0.9, 0.0) == "present"
    assert presence(40, 100.0, 0.9, 0.0) == "unresolved"
    assert presence(90, 100.0, 0.3, 0.0) == "unresolved"
    assert presence(90, 100.0, 0.9, 0.05) == "unresolved"


def test_scalar_feather_core_and_interior_margins():
    from gaiavfx.modules.direction.person_crop import core_box, interior_margins, ramp

    required, _, (chosen, _) = dev_mug()
    edges, box = chosen["interior_edges"], chosen["box"]
    assert core_box(box, edges, 24) == pytest.approx((box[0] + 24, 0, 1280, 720))
    margins = interior_margins(required, box, edges, 24)
    assert set(margins) == {"left"}
    assert margins["left"] == pytest.approx(required[0] - box[0] - 24)
    assert margins["left"] >= 8
    assert [ramp(d, 24) for d in (-10, 0, 12, 24, 30)] == pytest.approx([0, 0, 0.5, 1, 1])
    with pytest.raises(ValueError):
        ramp(1, 0)


def test_choose_prefers_cheaper_canvas_at_equal_subject_scale():
    # Both fits reach scale 1; the wider canvas includes extra context.
    chosen, _ = choose((0, 0, 100, 200), (200, 200), 0, (0, 0, 200, 200), [(200, 200), (100, 200)])
    assert chosen["canvas"] == [100, 200]
    with pytest.raises(ValueError, match="No supported canvas"):
        choose((0, 0, 1280, 720), NATIVE, 32, (0, 0, 1280, 720), [(256, 512)])
