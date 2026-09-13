"""`convert/cartosym.py::graphic_to_style` - one pycartosym `Style` per real INTERLIS `GRAPHIC`.

Uses the real `RoadsExgm2ien.ili`/`RoadsExgm2ien_Symbols.xtf` fixture (the
INTERLIS reference manual's own canonical `GRAPHIC` example, `FGDM4GS`/
`models.interlis.ch` - see `tests/fixtures/cartosym/NOTICE`).
"""

from pathlib import Path

from conftest import build_from_file

from interlis.builder.repository import ModelRepository
from interlis.convert.cartosym import SignLibrary, graphic_to_style, write_sld
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.parse import parse_xtf

_FIXTURES = Path(__file__).parent / "fixtures" / "cartosym"
_MODEL = _FIXTURES / "roadsexgm2ien" / "RoadsExgm2ien.ili"
_SIGN_XTF = _FIXTURES / "roadsexgm2ien" / "RoadsExgm2ien_Symbols.xtf"
_REPO_DIR = _FIXTURES / "roadsexgm2ien_repo"


def _graphic(name: str):
    repository = ModelRepository([_REPO_DIR])
    builder = build_from_file(_MODEL, repository=repository)
    return next(
        i
        for i in builder.symbol_table.all_registered()
        if isinstance(i, MetaInstance) and i._qualified_class.rsplit(".", 1)[-1] == "Graphic" and i.Name == name
    )


def _sign_library() -> SignLibrary:
    transfer = parse_xtf(_SIGN_XTF)
    return SignLibrary(transfer.baskets[0])


def test_one_styling_rule_per_drawing_rule_with_the_graphics_own_feature_type():
    style = graphic_to_style(_graphic("Surface_Graphics"), _sign_library())
    assert [r.name for r in style.styling_rules] == ["Building", "Street", "Water", "Other"]
    xml = write_sld(style)
    assert "<se:FeatureTypeName>LandCover</se:FeatureTypeName>" in xml
    assert '"fill">#3b3b3b' in xml  # Building's real FillColor (LCh -> sRGB)


def test_style_metadata_title_is_the_graphics_own_name():
    style = graphic_to_style(_graphic("Text_Graphics"), _sign_library())
    assert style.metadata.title == "Text_Graphics"


def test_none_sign_library_still_builds_a_style_without_resolving_signs():
    style = graphic_to_style(_graphic("Surface_Graphics"), None)
    assert len(style.styling_rules) == 4
    assert all(r.symbolizer.fill is None for r in style.styling_rules)


def test_polyline_sign_color_falls_back_to_its_line_styles_own_color():
    """Real data (`continuous`/`dotted` `PolylineSign`) sets `LineStyle.Color`, never `PolylineSign.Color` itself."""
    xml = write_sld(graphic_to_style(_graphic("Polyline_Graphics"), _sign_library()))
    assert '"stroke">#000000' in xml


def test_dash_pattern_reads_every_repeated_dashes_occurrence_not_just_the_first():
    """`LineStyle_Dashed.Dashes` (`LIST OF DashRec`) is 2 separate top-level `<Dashes>` siblings on real data.

    Regression: reading only the first occurrence's children (as a
    hand-built synthetic fixture elsewhere had assumed) silently dropped
    every dash length after the first. Checked at the `SignLibrary` level
    directly (not through `write_sld`) - `polyline_sign_to_stroke` rounds
    each value to an int for pycartosym's `Stroke.dash_pattern: list[int]`
    (a separate, already-documented lossy step: real data here is 0.1/0.1,
    both rounding to 0, which would mask a "dropped the 2nd value" bug
    just as well as a real fix).
    """
    library = _sign_library()
    dashed = library.resolve_ref(library.by_name["dotted"], "Style")
    assert dashed.qualified_class.endswith("LineStyle_Dashed")
    assert library.dash_pattern(dashed) == [0.1, 0.1]


def test_point_graphics_composite_font_symbol_renders_as_svg():
    """`Tree`/`GP` `SymbolSign`s reference composite `FontSymbol`s mixing `_Polyline` and `_Surface` items.

    `font_symbol_geometry_to_circle_graphics` aborts on these (not
    all-`FontSymbol_Surface`-circular) - `font_symbol_geometry_to_svg_data_uri`
    picks up the fallback, previously left this GRAPHIC with no
    representable content at all.
    """
    xml = write_sld(graphic_to_style(_graphic("Point_Graphics"), _sign_library()))
    assert xml.count("<se:PointSymbolizer>") == 2
    assert xml.count('xlink:href="data:image/svg+xml;base64,') == 2
    assert xml.count("<se:Format>image/svg+xml</se:Format>") == 2
