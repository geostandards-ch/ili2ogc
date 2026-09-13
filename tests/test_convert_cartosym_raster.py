"""`convert/cartosym.py`'s `RasterSign` extension proposal - `ColorMap`, RGB/gray channel selection, hill-shading.

`RasterSign` is a project-proposed `StandardSymbology` extension (not the
official model - see `tests/fixtures/cartosym/rastersign_repo/NOTICE`).
All 3 targets (`Symbolizer.color_map`/`color_channels`/`single_channel`/
`hill_shading.factor`) are confirmed to write a real `se:RasterSymbolizer`.
"""

from pathlib import Path

from conftest import build_from_text
from pycartosym.models.styles import Style

from interlis.convert.cartosym import (
    SignLibrary,
    raster_sign_object_to_channel_selection,
    raster_sign_object_to_color_map,
    raster_sign_object_to_hill_shading,
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


def _drawing_rule(name: str = "ElevationRule"):
    builder = build_from_text(_ILI.read_text())
    graphic = builder.symbol_table.resolve("RasterSignExample.T.Coverage_Graphics")
    rules = graphic.DrawingRule if isinstance(graphic.DrawingRule, list) else [graphic.DrawingRule]
    return next(r for r in rules if r.Name == name), graphic


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


def test_rgb_bands_become_color_channels():
    library = _sign_library()
    orthophoto = library.by_name["Orthophoto"]
    assert raster_sign_object_to_channel_selection(library, orthophoto) == {
        "color_channels": [{"property": "band1"}, {"property": "band2"}, {"property": "band3"}]
    }


def test_gray_band_becomes_single_channel():
    library = _sign_library()
    hillshade = library.by_name["Hillshade"]
    assert raster_sign_object_to_channel_selection(library, hillshade) == {"single_channel": {"property": "band1"}}


def test_hill_shade_factor():
    library = _sign_library()
    hillshade = library.by_name["Hillshade"]
    assert raster_sign_object_to_hill_shading(library, hillshade) == {"factor": 1.8}
    assert raster_sign_object_to_hill_shading(library, library.by_name["Elevation"]) is None


def test_writes_a_real_se_channel_selection_rgb():
    rule, graphic = _drawing_rule("OrthophotoRule")
    styling_rule = styling_rule_from_drawing_rule(rule, sign_library=_sign_library(), feature_type=graphic.Base.Name)
    xml = write_sld(Style(styling_rules=[styling_rule]))
    assert "<se:RedChannel>" in xml
    assert "<se:SourceChannelName>band2</se:SourceChannelName>" in xml


def test_writes_a_real_se_gray_channel_and_shaded_relief():
    rule, graphic = _drawing_rule("HillshadeRule")
    styling_rule = styling_rule_from_drawing_rule(rule, sign_library=_sign_library(), feature_type=graphic.Base.Name)
    xml = write_sld(Style(styling_rules=[styling_rule]))
    assert "<se:GrayChannel>" in xml
    assert "<se:ShadedRelief>" in xml
    assert "<se:ReliefFactor>1.8</se:ReliefFactor>" in xml
