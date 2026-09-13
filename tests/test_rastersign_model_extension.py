"""`RasterSign` model extension actually builds - `tests/fixtures/cartosym/rastersign_repo/`.

Proof that the proposed `CLASS RasterSign EXTENDS INTERLIS.SIGN =
ColorMapEntries: LIST OF RasterColorMapEntry; ...` (a value->color ramp
only so far) is soundly constructible by this project's own builder -
not just a plausible-looking snippet.
"""

from pathlib import Path

from conftest import build_from_file

_MODEL = Path(__file__).parent / "fixtures" / "cartosym" / "rastersign_repo" / "StandardSymbology_ext.ili"


def test_raster_sign_color_map_entries_resolve_to_the_new_structure():
    builder = build_from_file(_MODEL)
    raster_sign = builder.symbol_table.resolve("StandardSymbologyRasterSignExt.StandardSigns.RasterSign")
    entries_attr = next(a for a in raster_sign.ClassAttribute if a.Name == "ColorMapEntries")
    base_type = entries_attr.Type.BaseType
    assert base_type.Name == "RasterColorMapEntry"
    assert base_type.Kind == "Structure"

    entry = builder.symbol_table.resolve("StandardSymbologyRasterSignExt.StandardSigns.RasterColorMapEntry")
    assert {a.Name for a in entry.ClassAttribute} == {"Value", "Color", "Label"}


def test_raster_sign_has_a_priority_parameter():
    builder = build_from_file(_MODEL)
    raster_sign = builder.symbol_table.resolve("StandardSymbologyRasterSignExt.StandardSigns.RasterSign")
    assert any(p.Name == "Priority" for p in raster_sign.ClassParameter)
