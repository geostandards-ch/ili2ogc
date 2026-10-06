"""`convert/cartosym.py`: well-known shapes in any rotation, and a `LineStyle_Pattern` as a pattern stroke."""

from pathlib import Path
from xml.etree import ElementTree

from pycartosym.models.styles import Style, StylingRule, Symbolizer

from interlis.convert.cartosym import (
    SignLibrary,
    font_symbol_geometry_to_polygon_graphics,
    polyline_sign_object_to_stroke,
    write_sld,
)
from interlis.xtf.parse import parse_xtf

_SIGN_XTF = Path(__file__).parent / "fixtures" / "cartosym" / "pattern_x_symbols.xtf"
_SE = "{http://www.opengis.net/se}"


def _library() -> SignLibrary:
    return SignLibrary(parse_xtf(_SIGN_XTF).baskets[0])


def _sld(**symbolizer) -> ElementTree.Element:
    style = Style(styling_rules=[StylingRule(symbolizer=Symbolizer(**symbolizer))])
    return ElementTree.fromstring(write_sld(style))


def test_an_x_from_another_start_vertex_is_the_x_mark():
    library = _library()
    (graphic,) = font_symbol_geometry_to_polygon_graphics(library, library.by_tid["30"])
    assert graphic.type == "ClosedPath" and graphic.transform is None
    from pycartosym.models.symbolizers import Marker

    root = _sld(marker=Marker(elements=[graphic]))
    assert root.find(f".//{_SE}WellKnownName").text == "x"


def test_a_triangle_pointing_right_is_a_triangle_turned_clockwise():
    library = _library()
    (graphic,) = font_symbol_geometry_to_polygon_graphics(library, library.by_tid["31"])
    assert round(graphic.transform.orientation) == 90  # se:Rotation is clockwise


def test_pattern_line_style_repeats_its_symbol_along_the_line():
    library = _library()
    stroke = polyline_sign_object_to_stroke(library, library.by_tid["50"])
    assert (stroke.pattern_gap, stroke.pattern_initial_gap) == (25, 4)
    root = _sld(stroke=stroke)
    graphic_stroke = root.find(f".//{_SE}GraphicStroke")
    assert graphic_stroke.find(f".//{_SE}WellKnownName").text == "x"
    assert graphic_stroke.find(f"{_SE}Gap").text == "25"
    assert graphic_stroke.find(f".//{_SE}Size").text == "18"
