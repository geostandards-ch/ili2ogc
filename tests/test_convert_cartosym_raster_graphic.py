"""A `GRAPHIC` `BASED ON` a real raster dataset class - `NonVector_Base_V3_1.ili`'s `ImageGraphicRasterObject`.

Proves the `RasterSign` extension proposal wires into a REAL (official,
unmodified) INTERLIS raster data model, not just the synthetic `Coverage`
class `rastersign_example.ili` uses for the `RasterSign` mapping tests
themselves - see `tests/fixtures/cartosym/nonvector_base_repo/NOTICE`.
"""

from pathlib import Path

from conftest import build_from_file
from pycartosym.models.styles import Style

from interlis.builder.errors import UnresolvedNamedReference
from interlis.builder.repository import ModelRepository
from interlis.convert.cartosym import SignLibrary, styling_rule_from_drawing_rule, write_sld
from interlis.xtf.parse import parse_xtf

_FIXTURES = Path(__file__).parent / "fixtures" / "cartosym"
_MODEL = _FIXTURES / "rastersign_graphic_example.ili"
_REPO_DIR = _FIXTURES / "nonvector_base_repo"
_SIGN_XTF = _FIXTURES / "rastersign_example.xtf"  # RasterSign itself stays synthetic - no real .xtf can exist yet


def _graphic():
    repository = ModelRepository([_REPO_DIR])
    builder = build_from_file(_MODEL, repository=repository)
    return builder.symbol_table.resolve("RasterSignGraphicExample.T.Orthophoto_Graphics")


def test_based_on_resolves_to_the_real_nonvector_base_subclass():
    graphic = _graphic()
    assert graphic.Base.Name == "OrthophotoDataset"
    assert not isinstance(graphic.Base, UnresolvedNamedReference)


def test_geometry_assignment_reaches_the_inherited_spatial_reference_polygon():
    graphic = _graphic()
    rules = graphic.DrawingRule if isinstance(graphic.DrawingRule, list) else [graphic.DrawingRule]
    cond = rules[0].Rule if not isinstance(rules[0].Rule, list) else rules[0].Rule[0]
    geometry_assignment = next(a for a in cond.Assignments if a.Param == "Geometry")
    path_el = geometry_assignment.Assignment.PathEls[0]
    assert path_el.Ref == "SpatialReference_Polygon"


def test_writes_a_real_raster_symbolizer_for_the_real_dataset_class():
    graphic = _graphic()
    rules = graphic.DrawingRule if isinstance(graphic.DrawingRule, list) else [graphic.DrawingRule]
    transfer = parse_xtf(_SIGN_XTF)
    library = SignLibrary(transfer.baskets[0])
    styling_rule = styling_rule_from_drawing_rule(rules[0], sign_library=library, feature_type=graphic.Base.Name)
    xml = write_sld(Style(styling_rules=[styling_rule]))
    assert "<se:CoverageName>OrthophotoDataset</se:CoverageName>" in xml
    assert "<se:RasterSymbolizer>" in xml
    assert "<se:RedChannel>" in xml
