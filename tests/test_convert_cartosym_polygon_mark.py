"""`convert/cartosym.py`'s square/diamond/triangle `FontSymbol_Surface` mapping, through to the SLD it produces.

SE 1.1.0 has no `diamond` well-known mark name, so a diamond can only
reach SLD as a `square` mark turned 45 degrees.
"""

from pathlib import Path
from xml.etree import ElementTree

from pycartosym.models.styles import Metadata, Style, StylingRule, Symbolizer

from interlis.convert.cartosym import (
    SignLibrary,
    font_symbol_geometry_to_polygon_graphics,
    symbol_sign_object_to_marker,
    write_sld,
)
from interlis.xtf.parse import parse_xtf

_SIGN_XTF = Path(__file__).parent / "fixtures" / "cartosym" / "square_diamond_symbols.xtf"
_SE = "{http://www.opengis.net/se}"


def _library() -> SignLibrary:
    return SignLibrary(parse_xtf(_SIGN_XTF).baskets[0])


def test_axis_aligned_square_needs_no_rotation():
    library = _library()
    graphics = font_symbol_geometry_to_polygon_graphics(library, library.by_tid["20"])
    assert graphics is not None and len(graphics) == 1
    assert graphics[0].width == graphics[0].height
    assert graphics[0].width.value == 1.0
    assert graphics[0].transform is None


def test_diamond_becomes_a_square_turned_45_degrees():
    library = _library()
    graphics = font_symbol_geometry_to_polygon_graphics(library, library.by_tid["21"])
    assert graphics is not None and len(graphics) == 1
    assert graphics[0].width == graphics[0].height
    assert graphics[0].transform is not None
    assert graphics[0].transform.orientation == 45.0


def test_an_oblong_rectangle_is_left_to_the_svg_fallback():
    """A non-square rectangle's rotation sign is unsettled - no native mark rather than a guessed one."""
    library = _library()
    assert font_symbol_geometry_to_polygon_graphics(library, library.by_tid["22"]) is None


def test_scale_multiplies_the_side_length():
    library = _library()
    graphics = font_symbol_geometry_to_polygon_graphics(library, library.by_tid["20"], scale=3.0)
    assert graphics is not None
    assert graphics[0].width.value == 3.0


def test_diamond_symbol_sign_writes_a_rotated_square_mark():
    library = _library()
    marker = symbol_sign_object_to_marker(library, library.by_tid["100"])
    style = Style(
        styling_rules=[StylingRule(name="DiamondMark", symbolizer=Symbolizer(marker=marker))],
        metadata=Metadata(title="diamond"),
    )
    root = ElementTree.fromstring(write_sld(style))
    graphic = root.find(f".//{_SE}PointSymbolizer/{_SE}Graphic")
    assert graphic is not None
    assert graphic.find(f"{_SE}Mark/{_SE}WellKnownName").text == "square"
    assert graphic.find(f"{_SE}Rotation").text == "45"
    assert graphic.find(f"{_SE}Mark/{_SE}Fill/{_SE}SvgParameter").text == "#cc0000"


def test_triangle_matches_whatever_its_start_vertex_winding_and_offset():
    library = _library()
    graphics = font_symbol_geometry_to_polygon_graphics(library, library.by_tid["23"])
    assert graphics is not None and len(graphics) == 1
    assert [(n.x.value, n.y.value) for n in graphics[0].nodes] == [(0.0, 1.0), (-1.0, -1.0), (1.0, -1.0)]


def test_a_triangle_wider_than_tall_is_left_to_the_svg_fallback():
    """SE's `triangle` has one fixed aspect ratio - any other triangle has no well-known name."""
    library = _library()
    assert font_symbol_geometry_to_polygon_graphics(library, library.by_tid["24"]) is None


def test_triangle_symbol_sign_writes_a_triangle_mark():
    library = _library()
    marker = symbol_sign_object_to_marker(library, library.by_tid["101"])
    style = Style(
        styling_rules=[StylingRule(name="TriangleMark", symbolizer=Symbolizer(marker=marker))],
        metadata=Metadata(title="triangle"),
    )
    graphic = ElementTree.fromstring(write_sld(style)).find(f".//{_SE}PointSymbolizer/{_SE}Graphic")
    assert graphic is not None
    assert graphic.find(f"{_SE}Mark/{_SE}WellKnownName").text == "triangle"
    assert graphic.find(f"{_SE}Size").text == "15"
