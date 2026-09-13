"""`convert/cartosym.py::raster_sign_object_to_color_map` - the `RasterSign` extension proposal's `ColorMap` support.

`RasterSign` is a project-proposed `StandardSymbology` extension (not the
official model - see `tests/fixtures/cartosym/rastersign_repo/NOTICE`),
covering only `Symbolizer.color_map` (a value->color ramp) for now -
confirmed to write a real `se:CoverageStyle/se:RasterSymbolizer/
se:ColorMap`. Channel selection (`color_channels`/`single_channel`) and
`hill_shading.factor` are later phases, deliberately not built yet.
"""

from pathlib import Path

from conftest import build_from_text
from pycartosym.models.styles import Style

from interlis.convert.cartosym import (
    SignLibrary,
    raster_sign_object_to_color_map,
    styling_rule_from_drawing_rule,
    write_sld,
)
from interlis.xtf.parse import parse_xtf

_FIXTURES = Path(__file__).parent / "fixtures" / "cartosym"
_ILI = _FIXTURES / "rastersign_example.ili"
_XTF = _FIXTURES / "rastersign_example.xtf"


def _sign_library() -> SignLibrary:
    transfer = parse_xtf(_XTF)
    return SignLibrary(transfer.baskets[0])


def _drawing_rule():
    builder = build_from_text(_ILI.read_text())
    graphic = builder.symbol_table.resolve("RasterSignExample.T.Coverage_Graphics")
    rules = graphic.DrawingRule if isinstance(graphic.DrawingRule, list) else [graphic.DrawingRule]
    return rules[0], graphic


def test_color_map_entries_become_value_color_pairs_in_wire_order():
    library = _sign_library()
    elevation = library.by_name["Elevation"]
    color_map = raster_sign_object_to_color_map(library, elevation)
    assert [value for value, _color in color_map] == [0.0, 1000.0]
    low_color, high_color = color_map[0][1], color_map[1][1]
    assert (low_color.r, low_color.g, low_color.b) == (255, 255, 255)  # white
    assert (high_color.r, high_color.g, high_color.b) == (255, 165, 0)  # orange


def test_writes_a_real_se_coverage_style_raster_symbolizer():
    rule, graphic = _drawing_rule()
    styling_rule = styling_rule_from_drawing_rule(rule, sign_library=_sign_library(), feature_type=graphic.Base.Name)
    xml = write_sld(Style(styling_rules=[styling_rule]))
    assert "<se:CoverageStyle>" in xml
    assert "<se:CoverageName>Coverage</se:CoverageName>" in xml
    assert "<se:RasterSymbolizer>" in xml
    assert "<se:Categorize" in xml
    assert "<se:Threshold>1000</se:Threshold>" in xml
    assert ">#ffa500<" in xml


def test_no_color_map_entries_returns_none():
    class _NoEntries:
        attributes: dict = {}

    assert raster_sign_object_to_color_map(_sign_library(), _NoEntries()) is None
