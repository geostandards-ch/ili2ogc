"""A GRAPHIC's `dataLayer.id` is the `featureType` the JSON-FG conversion gives its base VIEW.

`fixtures/cartosym/imported_views/` keeps the VIEWs in `RoadsViews.ili`, imported by the GRAPHICs' own model; `roads`
is declared in two VIEW TOPICs there, so the JSON-FG conversion keys the second one `roads_2`.
"""

from pathlib import Path

import pytest
from conftest import build_from_file
from lxml import etree

from interlis.builder.repository import ModelRepository
from interlis.cli import main
from interlis.cli_style import ExitCode
from interlis.convert.cartosym import graphic_to_style
from interlis.convert.jsonfg import feature_type_keys, feature_type_of
from interlis.metamodel.instance import MetaInstance

_FIXTURES = Path(__file__).parent / "fixtures" / "cartosym"
_DIR = _FIXTURES / "imported_views"
_REPOSITORY_DIRS = [_DIR, _FIXTURES / "roadsexgm2ien_repo"]
_SE = {"se": "http://www.opengis.net/se"}


def _instances(builder, kind: str) -> dict[str, MetaInstance]:
    return {
        i.Name: i
        for i in builder.symbol_table.all_registered()
        if isinstance(i, MetaInstance) and i._qualified_class.rsplit(".", 1)[-1] == kind
    }


@pytest.fixture(scope="module")
def symbology():
    repository = ModelRepository(_REPOSITORY_DIRS)
    builder = build_from_file(_DIR / "RoadsSymbology.ili", repository=repository)
    return builder, repository, _instances(builder, "Graphic")


def _data_layers(style) -> list:
    return [rule.selector for rule in style.styling_rules]


def _layer(feature_type: str) -> dict:
    return {"op": "=", "args": [{"sysId": "dataLayer.id"}, feature_type]}


def test_the_views_are_not_in_the_graphics_symbol_table(symbology):
    builder, _repository, _graphics = symbology
    assert feature_type_keys(builder.symbol_table) == {}


@pytest.mark.parametrize(
    ("graphic", "expected"), [("Main_Graphics", "roads"), ("Second_Graphics", "roads_2"), ("Lanes_Graphics", "lanes")]
)
def test_data_layer_id_is_the_json_fg_feature_type_of_the_imported_view(symbology, graphic, expected):
    builder, repository, graphics = symbology
    style = graphic_to_style(graphics[graphic], None, builder.symbol_table, repository)
    assert _data_layers(style) == [_layer(expected)]


def test_feature_types_equal_the_keys_of_the_views_model_built_alone(symbology):
    """`convert-jsonfg` builds the views model itself: its keys are the ones the GRAPHICs must name."""
    builder, repository, graphics = symbology
    views_builder = build_from_file(_DIR / "RoadsViews.ili", repository=ModelRepository(_REPOSITORY_DIRS))
    table = views_builder.symbol_table
    own_keys = feature_type_keys(table)
    by_name = {
        table.qualified_name_of(i): own_keys[id(i)]
        for i in table.all_registered()
        if isinstance(i, MetaInstance) and i._qualified_class.endswith(".View")
    }
    for graphic in graphics.values():
        qualified = repository.symbol_table_for("RoadsViews").qualified_name_of(graphic.Base)
        assert feature_type_of(graphic.Base, builder.symbol_table, repository) == by_name[qualified]
    assert sorted(by_name.values()) == ["lanes", "roads", "roads_2"]


def test_without_a_symbol_table_the_short_view_name_is_kept(symbology):
    _builder, _repository, graphics = symbology
    assert _data_layers(graphic_to_style(graphics["Second_Graphics"])) == [_layer("roads")]


def test_a_class_of_the_graphics_own_table_keeps_its_key():
    builder = build_from_file(_FIXTURES / "scale_example.ili", repository=ModelRepository([_FIXTURES / "scale_repo"]))
    (graphic,) = [g for g in _instances(builder, "Graphic").values() if g.Name == "Road_NoScale"]
    assert feature_type_of(graphic.Base, builder.symbol_table) == "Road"


def test_convert_sld_writes_the_feature_type_of_the_imported_view(tmp_path):
    exit_code = main(
        [
            "convert-sld",
            str(_DIR / "RoadsSymbology.ili"),
            "--repo",
            str(_DIR),
            "--repo",
            str(_FIXTURES / "roadsexgm2ien_repo"),
            "--sign-xtf",
            str(_DIR / "RoadsSymbols.xtf"),
            "-o",
            str(tmp_path),
        ]
    )
    assert exit_code == ExitCode.OK
    names = {
        path.stem: etree.parse(str(path)).findtext(".//se:FeatureTypeName", namespaces=_SE)
        for path in tmp_path.glob("*.sld")
    }
    assert names == {"Main_Graphics": "roads", "Second_Graphics": "roads_2", "Lanes_Graphics": "lanes"}
