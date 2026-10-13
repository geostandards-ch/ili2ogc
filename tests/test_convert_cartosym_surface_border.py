"""A `SurfaceSign` with a border and no fill color is written without `se:Fill`; one with a fill color keeps it.

An empty `se:Fill` is read as the SE 1.1.0 default fill (grey), so a border-only surface must not carry one.
`tests/fixtures/cartosym/surface_border_example.ili` draws the same class with both kinds of sign; the sign library
is synthetic.
"""

from pathlib import Path

import pytest
from conftest import build_from_file
from lxml import etree

from interlis.builder.repository import ModelRepository
from interlis.convert.cartosym import SignLibrary, graphic_to_style, write_sld
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.parse import parse_xtf

_FIXTURES = Path(__file__).parent / "fixtures" / "cartosym"
_MODEL = _FIXTURES / "surface_border_example.ili"
_SIGN_XTF = _FIXTURES / "surface_border_example.xtf"
_REPO_DIR = _FIXTURES / "scale_repo"
_SE = {"se": "http://www.opengis.net/se"}


@pytest.fixture(scope="module")
def graphics():
    builder = build_from_file(_MODEL, repository=ModelRepository([_REPO_DIR]))
    return {
        i.Name: i
        for i in builder.symbol_table.all_registered()
        if isinstance(i, MetaInstance) and i._qualified_class.rsplit(".", 1)[-1] == "Graphic"
    }


@pytest.fixture(scope="module")
def library() -> SignLibrary:
    return SignLibrary(parse_xtf(_SIGN_XTF).baskets[0])


def _sld(graphics, library, name: str) -> etree._Element:
    return etree.fromstring(write_sld(graphic_to_style(graphics[name], library)).encode("utf-8"))


def test_border_only_surface_has_no_fill_element(graphics, library):
    root = _sld(graphics, library, "Zone_Outline")
    assert root.find(".//se:Fill", _SE) is None
    assert root.find(".//se:Stroke", _SE) is not None


def test_border_only_surface_keeps_its_stroke_parameters(graphics, library):
    root = _sld(graphics, library, "Zone_Outline")
    stroke = {p.get("name"): p.text for p in root.iterfind(".//se:Stroke/se:SvgParameter", _SE)}
    assert stroke["stroke"] == "#e2001a"
    assert stroke["stroke-width"] == "4"


def test_surface_with_a_fill_color_keeps_its_fill_and_its_border(graphics, library):
    root = _sld(graphics, library, "Zone_Filled")
    fill = {p.get("name"): p.text for p in root.iterfind(".//se:Fill/se:SvgParameter", _SE)}
    assert fill["fill"] == "#ffed00"
    assert root.find(".//se:Stroke", _SE) is not None
