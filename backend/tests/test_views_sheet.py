"""Geometry and pixel parity for the tested H3 views-only reference sheets."""

import ast
import json
from pathlib import Path

import pytest

from gaiavfx.integrations.minimax_h3 import knowledge_pack
from gaiavfx.integrations.minimax_h3.sheet import views_sheet_layout
from gaiavfx.modules.direction.capabilities import ViewsSheetPreset, parse_pack

FIXTURES = Path(__file__).parent / "fixtures" / "parity"
ROWS = ("DZ", "EH", "EG")
BOTTOM_ROW = [
    (0, 758, 1344, 752),
    (1350, 758, 1344, 752),
    (2700, 758, 1344, 752),
]


def sheet_fixture(arm: str) -> dict:
    fixture = json.loads((FIXTURES / f"{arm}.json").read_text(encoding="utf-8"))
    return next(file["sheet"] for file in fixture["files"] if "sheet" in file)


@pytest.mark.parametrize("arm", ROWS)
def test_layout_matches_the_research_sheet_boxes(arm):
    sheet = sheet_fixture(arm)
    sizes = [tuple(tile["size"]) for tile in sheet["tiles"]]
    width, height, boxes = views_sheet_layout(sizes, sheet["columns"], 6)
    assert (width, height) == (4044, 1510)
    first_row = (
        [(0, 0, 1289, 752), (1295, 0, 1344, 752), (2645, 0, 1344, 752)]
        if arm == "DZ"
        else [(0, 0, 752, 752), (758, 0, 1344, 752), (2108, 0, 1344, 752)]
    )
    assert boxes == first_row + BOTTOM_ROW


def test_layout_scales_a_tall_tile_to_the_shortest_height():
    assert views_sheet_layout([(100, 200), (100, 100)], 2, 6) == (
        156,
        100,
        [(0, 0, 50, 100), (56, 0, 100, 100)],
    )


def test_layout_kernel_imports_no_image_or_filesystem_libraries():
    source = Path(views_sheet_layout.__code__.co_filename)
    tree = ast.parse(source.read_text(encoding="utf-8"))
    imports = {
        name.split(".")[0]
        for node in ast.walk(tree)
        for name in (
            [alias.name for alias in node.names]
            if isinstance(node, ast.Import)
            else [node.module or ""]
            if isinstance(node, ast.ImportFrom)
            else []
        )
    }
    assert not imports & {"PIL", "os", "pathlib"}


def test_h3_pack_exposes_the_tested_sheet_preset():
    assert knowledge_pack().views_sheet == ViewsSheetPreset(3, 6, (128, 128, 128))
    assert parse_pack('schema = 1\nintegration = "other"\n').views_sheet is None


@pytest.mark.parametrize(
    "table",
    [
        'columns = 3\ngap = 6\ngap_rgb = [128, 128, 128]\nlabel = "bad"',
        "columns = 3\ngap = 6",
        "columns = 0\ngap = 6\ngap_rgb = [128, 128, 128]",
        "columns = 3\ngap = -1\ngap_rgb = [128, 128, 128]",
        "columns = 3\ngap = 6\ngap_rgb = [128, 128, 256]",
        "columns = 3\ngap = 6\ngap_rgb = [128, 128]",
    ],
)
def test_pack_refuses_invalid_or_extra_views_sheet_values(table):
    with pytest.raises(ValueError, match="presets.views_sheet"):
        parse_pack('schema = 1\nintegration = "x"\n[presets.views_sheet]\n' + table)
