"""`convert/cartosym.py::surface_sign_object_to_hatch_pattern` against real `Surface_Hatching_Stipples_GS.xtf` data.

`Fill.pattern` writes correctly as of `pycartosym` v0.3.0
(`se:GraphicFill`) - a previous "blocked, NotImplementedError" finding
in this project was stale, tested against 0.2.2 and never rechecked
after later version bumps.
"""

import base64
from pathlib import Path
from xml.etree import ElementTree

from pycartosym.models.styles import Style, StylingRule, Symbolizer

from interlis.convert.cartosym import SignLibrary, surface_sign_object_to_fill, write_sld
from interlis.xtf.parse import parse_xtf

_XTF = Path(__file__).parent / "fixtures" / "cartosym" / "Surface_Hatching_Stipples_GS.xtf"


def _library() -> SignLibrary:
    transfer = parse_xtf(_XTF)
    return SignLibrary(transfer.baskets[0])


def test_hatch_symb_becomes_a_fill_pattern_image_graphic():
    library = _library()
    fill = surface_sign_object_to_fill(library, library.by_name["fill"])
    assert fill.pattern is not None
    assert fill.pattern.image.uri.startswith("data:image/svg+xml;base64,")

    svg = base64.b64decode(fill.pattern.image.uri.split(",", 1)[1]).decode("utf-8")
    root = ElementTree.fromstring(svg)
    assert root.get("viewBox") == "-0.5 -0.5 1 1"
    line = root.find("{http://www.w3.org/2000/svg}line")
    assert line is not None
    assert line.get("stroke") == "#000000"  # HatchSymb -> "continuous" PolylineSign -> LineStyle_Solid.Color = black


def test_hatch_ang_absent_leaves_the_pattern_unrotated():
    """Real corpus data (`Surface_Hatching_Stipples_GS.xtf`) never sets `HatchAng` - the untransformed case."""
    library = _library()
    fill = surface_sign_object_to_fill(library, library.by_name["fill"])
    assert fill.pattern.transform is None


def test_writes_a_valid_se_graphic_fill():
    library = _library()
    fill = surface_sign_object_to_fill(library, library.by_name["fill"])
    xml = write_sld(Style(styling_rules=[StylingRule(name="r", symbolizer=Symbolizer(fill=fill))]))
    assert "<se:GraphicFill>" in xml
    assert "<se:ExternalGraphic>" in xml
    assert 'xlink:href="data:image/svg+xml;base64,' in xml


def test_no_hatch_symb_leaves_the_pattern_none():
    library = _library()
    fill = surface_sign_object_to_fill(library, library.by_name["fill"])
    assert fill.pattern is not None  # sanity: this fixture DOES have one

    # A SurfaceSign without HatchSymb (any resolve_ref miss) must not crash.
    class _NoHatch:
        attributes: dict = {}

    from interlis.convert.cartosym import surface_sign_object_to_hatch_pattern

    assert surface_sign_object_to_hatch_pattern(library, _NoHatch()) is None
