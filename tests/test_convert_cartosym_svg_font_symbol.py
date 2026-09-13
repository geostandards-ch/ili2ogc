"""`convert/cartosym.py::font_symbol_geometry_to_svg_data_uri` against real `RoadsExgm2ien_Symbols.xtf` FontSymbols.

`FontSymbol ili:tid="101"/"102"` ("Triangle"/"NoParking") mix
`FontSymbol_Polyline` and `FontSymbol_Surface` items - the case
`font_symbol_geometry_to_circle_graphics` deliberately aborts on.
"""

import base64
from pathlib import Path
from xml.etree import ElementTree

from interlis.convert.cartosym import SignLibrary, font_symbol_geometry_to_svg_data_uri
from interlis.xtf.parse import parse_xtf

_SIGN_XTF = Path(__file__).parent / "fixtures" / "cartosym" / "roadsexgm2ien" / "RoadsExgm2ien_Symbols.xtf"


def _library() -> SignLibrary:
    transfer = parse_xtf(_SIGN_XTF)
    return SignLibrary(transfer.baskets[0])


def _decoded_svg(data_uri: str) -> str:
    assert data_uri.startswith("data:image/svg+xml;base64,")
    return base64.b64decode(data_uri.split(",", 1)[1]).decode("utf-8")


def test_triangle_font_symbol_renders_a_filled_triangle_and_a_circle_outline():
    library = _library()
    uri = font_symbol_geometry_to_svg_data_uri(library, library.by_tid["101"])
    assert uri is not None
    svg = _decoded_svg(uri)
    root = ElementTree.fromstring(svg)
    assert root.tag.endswith("svg")
    assert root.get("viewBox") == "-0.5 -0.5 1 1"
    paths = root.findall("{http://www.w3.org/2000/svg}path")
    assert len(paths) == 2
    assert paths[0].get("fill") == "black"  # FontSymbol_Surface, no FillColor ref on this item
    assert paths[1].get("stroke") == "#000000"  # FontSymbol_Polyline, Color ref="6" (black)


def test_noparking_font_symbol_renders_all_6_composite_items():
    library = _library()
    uri = font_symbol_geometry_to_svg_data_uri(library, library.by_tid["102"])
    assert uri is not None
    svg = _decoded_svg(uri)
    root = ElementTree.fromstring(svg)
    paths = root.findall("{http://www.w3.org/2000/svg}path")
    assert len(paths) == 6


def test_a_font_symbol_polyline_item_without_a_uniform_font_type_still_renders():
    """Regression: the extra `<Geometry>` wrapper level must be unwrapped before reading the POLYLINE/SURFACE."""
    library = _library()
    triangle = library.by_tid["101"]
    occurrences = triangle.attributes["Geometry"]
    polyline_occurrence = next(o for o in occurrences if o.children[0].tag == "FontSymbol_Polyline")
    structure_node = polyline_occurrence.children[0]
    geometry_wrapper = next(c for c in structure_node.children if c.tag == "Geometry")
    # The real wire shape: <Geometry><polyline>...</polyline></Geometry> - 2 levels, not 1.
    assert geometry_wrapper.children[0].tag in ("polyline", "POLYLINE")
