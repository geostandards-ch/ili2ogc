"""`convert/cartosym.py::SignLibrary`/`styling_rule_from_drawing_rule` wired to a real `Sign := {name}` `.xtf` object.

`tests/fixtures/cartosym/Point_Graphics_Signatures.xtf` is real,
unmodified FGDM4GS corpus data (see its NOTICE) - a `SymbolSign`
(`ili:Name = Symbol`) referencing a `Color`/text-`FontSymbol`/`ClipSymbol`
by `ili:ref` within the same `SIGN BASKET`. The `.ili` model below is
synthetic (`Point_Graphics.ili` itself has this exact `DrawingRule`
commented out, and doesn't line up with this particular `.xtf` anyway) but
mirrors the real shape exactly (`SIGN BASKET ... OBJECTS OF SymbolSign:
Symbol`, `Sign := {Symbol}`) - see `test_convert_cartosym_graphic_to_style.py`
for a fully real (not synthetic) `.ili`+`.xtf` pair exercising this same
mechanism end to end (`tests/fixtures/cartosym/roadsexgm2ien/`).
"""

from pathlib import Path

from conftest import build_from_text
from pycartosym.models.styles import Style

from interlis.convert.cartosym import SignLibrary, styling_rule_from_drawing_rule, write_sld
from interlis.xtf.parse import parse_xtf

_MODEL = """INTERLIS 2.3;

MODEL TestSymbolSignWiring (en)
AT "mailto:test@example.org"
VERSION "2024-01-01" =
  TOPIC T =
    CLASS Point =
      Position: TEXT*40;
    END Point;

    CLASS SymbolSign =
      Dummy: TEXT*1;
    END SymbolSign;

    SIGN BASKET MyBasket ~ T
      OBJECTS OF SymbolSign: Symbol;

    GRAPHIC Point_Graphics BASED ON Point =
      Tree OF SymbolSign:
        (
          Sign := {Symbol};
          Geometry := Position;
          Priority := 1
        );
    END Point_Graphics;
  END T;
END TestSymbolSignWiring.
"""

_FIXTURE = Path(__file__).parent / "fixtures" / "cartosym" / "Point_Graphics_Signatures.xtf"


def _drawing_rule():
    builder = build_from_text(_MODEL)
    graphic = builder.symbol_table.resolve("TestSymbolSignWiring.T.Point_Graphics")
    rules = graphic.DrawingRule if isinstance(graphic.DrawingRule, list) else [graphic.DrawingRule]
    return rules[0]


def _sign_library() -> SignLibrary:
    transfer = parse_xtf(_FIXTURE)
    return SignLibrary(transfer.baskets[0])


def test_sign_library_indexes_the_real_symbol_sign_by_name_and_resolves_its_refs():
    library = _sign_library()
    obj = library.by_name["Symbol"]
    assert obj.qualified_class.endswith("SymbolSign")
    color_obj = library.resolve_ref(obj, "Color")
    assert library.scalar(color_obj, "Name") == "black"
    symbol_obj = library.resolve_ref(obj, "Symbol")
    assert library.scalar(symbol_obj, "Name") == "Tree"
    font_obj = library.resolve_ref(symbol_obj, "Font")
    assert library.scalar(font_obj, "Name") == "CadastraSymbol-Regular"
    assert library.scalar(font_obj, "Type") == "text"


def test_styling_rule_from_drawing_rule_resolves_sign_to_a_real_marker_with_the_glyph():
    styling_rule = styling_rule_from_drawing_rule(_drawing_rule(), sign_library=_sign_library())
    marker = styling_rule.symbolizer.marker
    assert marker is not None
    assert marker.opacity == 1.0  # Color "black"'s T
    graphic = marker.elements[0]
    # UCS4 "0077" is decimal (the fixture's own comment: "convert unicodes
    # from base 16 to base 10" before writing them) - chr(77) == "M", the
    # remapped glyph slot for "Tree" in this custom cadastral symbol font.
    assert graphic.text == "M"
    assert graphic.font.face == "CadastraSymbol-Regular"

    xml = write_sld(Style(styling_rules=[styling_rule]))
    # A Marker wrapping a single-character TextGraphic writes as
    # se:Mark/OnlineResource(ttf://<face>)/MarkIndex (a font-glyph
    # reference, SE 1.1.0 Annex B "Text (inside Marker)"), not a
    # TextSymbolizer - pycartosym's own real convention, confirmed
    # empirically against pycartosym>=0.3.4.
    assert "<se:Mark>" in xml
    assert 'xlink:href="ttf://CadastraSymbol-Regular"' in xml
    assert "<se:MarkIndex>77</se:MarkIndex>" in xml  # ord("M")
    assert "z_order" not in xml  # confirms the earlier "no visible effect" finding still holds
