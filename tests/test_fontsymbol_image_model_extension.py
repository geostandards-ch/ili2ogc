"""`FontSymbol_Image` model extension actually builds - `tests/fixtures/cartosym/fontsymbol_image_repo/`.

Proof that the proposed `FontSymbol_Image EXTENDS FontSymbol_Geometry =
Uri: TEXT; END FontSymbol_Image;` variant, added to `FontSymbol.Geometry`'s
`RESTRICTION`, is soundly constructible by this project's own builder -
not just a plausible-looking snippet. Separate from
`test_convert_cartosym_pycartosym_examples.py::test_example_15_image_marker`,
which exercises the `.xtf`-wire-shape/SLD-writing side (that test never
imports this adapted model at all - `SignLibrary` reads raw XTF tags
directly, needing no built schema).
"""

from pathlib import Path

from conftest import build_from_file

from interlis.metamodel.instance import MetaInstance

_MODEL = Path(__file__).parent / "fixtures" / "cartosym" / "fontsymbol_image_repo" / "StandardSymbology_ext.ili"


def test_font_symbol_geometry_restriction_includes_the_new_variant():
    builder = build_from_file(_MODEL)
    font_symbol = builder.symbol_table.resolve("StandardSymbologyFontSymbolImageExt.StandardSigns.FontSymbol")
    geometry_attr = next(a for a in font_symbol.ClassAttribute if a.Name == "Geometry")
    base_type = geometry_attr.Type.BaseType
    assert base_type.Name == "FontSymbol_Geometry"

    font_symbol_image = builder.symbol_table.resolve(
        "StandardSymbologyFontSymbolImageExt.StandardSigns.FontSymbol_Image"
    )
    assert isinstance(font_symbol_image, MetaInstance)
    assert font_symbol_image.Super.Name == "FontSymbol_Geometry"
    uri_attr = next(a for a in font_symbol_image.ClassAttribute if a.Name == "Uri")
    assert uri_attr is not None
