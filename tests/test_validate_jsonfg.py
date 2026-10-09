"""`validate_jsonfg`: each kind of defect is caught by its own layer, and a conforming document passes."""

import copy
import json
from pathlib import Path

import pytest
from conftest import ROOT, build_from_file, build_from_text

from interlis.runtime.parse import meta_attribute_comments
from interlis.xtf.jsonfg_validate import validate_jsonfg

_MODEL = """INTERLIS 2.4;
MODEL Jfg AT "http://x" VERSION "1" =
  DOMAIN
    !!@CRS=EPSG:2056
    Coord2D = COORD 2460000.000 .. 2870000.000, 1045000.000 .. 1310000.000;
    Line = POLYLINE WITH (STRAIGHTS, ARCS) VERTEX Coord2D;
    Colour = (red, green, blue);
  TOPIC T =
    CLASS Owner =
      Name : MANDATORY TEXT*10;
      UNIQUE Name;
    END Owner;
    CLASS Parcel =
      Name : MANDATORY TEXT*10;
      Tint : Colour;
      Low : 0 .. 100;
      High : 0 .. 100;
      Position : MANDATORY Coord2D;
      Holder : REFERENCE TO Owner;
    MANDATORY CONSTRAINT
      Low <= High;
    END Parcel;
    CLASS Road =
      Axis : Line;
    END Road;
  END T;
END Jfg.
"""
_CRS = "http://www.opengis.net/def/crs/EPSG/0/2056"


@pytest.fixture(scope="module")
def table():
    return build_from_text(_MODEL, meta_attributes=meta_attribute_comments(_MODEL)).symbol_table


def _parcel(fid="p1", **overrides):
    feature = {
        "type": "Feature",
        "id": fid,
        "featureType": "Parcel",
        "geometry": None,
        "place": {"type": "Point", "coordinates": [2600000.0, 1200000.0]},
        "coordRefSys": _CRS,
        "properties": {"Name": "north", "Tint": "red", "Low": 1, "High": 5},
    }
    for key, value in overrides.items():
        if key == "properties":
            feature["properties"].update(value)
        else:
            feature[key] = value
    return feature


def _owner(fid, name):
    return {"type": "Feature", "id": fid, "featureType": "Owner", "geometry": None, "properties": {"Name": name}}


def _document(*features):
    return {"type": "FeatureCollection", "features": list(features)}


def _problems(table, *features, **options):
    issues = validate_jsonfg(_document(*features), symbol_table=table, **options)
    return [(i.severity, i.attribute, i.message) for i in issues if i.severity != "info"]


def test_a_conforming_document_has_no_issue(table):
    assert _problems(table, _owner("o1", "ada"), _parcel(properties={"Holder": "o1"})) == []


def test_a_single_feature_is_accepted(table):
    assert validate_jsonfg(_parcel(), symbol_table=table) == []


def test_not_a_feature_collection(table):
    [issue] = validate_jsonfg({"type": "Polygon"}, symbol_table=table)
    assert issue.severity == "error" and issue.message.startswith("[model]")


def test_unknown_feature_type_is_a_model_error(table):
    [(severity, _attr, message)] = _problems(table, _parcel(featureType="Lot"))
    assert severity == "error" and message.startswith("[model]") and "'Lot'" in message


@pytest.mark.parametrize(
    "properties, attribute",
    [
        ({"Name": "a name that is far too long"}, "Name"),
        ({"Tint": "purple"}, "Tint"),
        ({"Low": 500}, "Low"),
        ({"Low": "many"}, "Low"),
    ],
)
def test_property_defects_are_caught_by_the_schema_layer(table, properties, attribute):
    problems = _problems(table, _parcel(properties=properties), check_constraints=False)
    assert [(a, m.split("]")[0]) for _s, a, m in problems] == [(attribute, "[properties")]


def test_a_missing_mandatory_property(table):
    feature = _parcel()
    del feature["properties"]["Name"]
    [(_severity, _attr, message)] = _problems(table, feature)
    assert message.startswith("[properties]") and "'Name' is a required property" in message


def test_an_unknown_property_is_a_warning(table):
    [(severity, attribute, message)] = _problems(table, _parcel(properties={"Extra": 1}))
    assert (severity, attribute) == ("warning", "Extra") and message.startswith("[properties]")


def test_the_geometry_is_not_a_property(table):
    feature = _parcel()
    assert "Position" not in feature["properties"]
    assert _problems(table, feature) == []


def test_a_missing_mandatory_geometry(table):
    [(_severity, attribute, message)] = _problems(table, _parcel(place=None))
    assert attribute == "Position" and message.startswith("[geometry]") and "missing" in message


def test_a_geometry_of_the_wrong_type(table):
    place = {"type": "LineString", "coordinates": [[2600000.0, 1200000.0], [2600001.0, 1200001.0]]}
    [(_severity, attribute, message)] = _problems(table, _parcel(place=place))
    assert attribute == "Position" and "'LineString' is not allowed" in message and "Point" in message


def test_a_coordinate_outside_the_domain(table):
    place = {"type": "Point", "coordinates": [1.0, 1200000.0]}
    [(_severity, attribute, message)] = _problems(table, _parcel(place=place))
    assert attribute == "Position" and "outside the domain" in message


def test_a_third_coordinate_in_a_two_dimensional_domain(table):
    place = {"type": "Point", "coordinates": [2600000.0, 1200000.0, 500.0]}
    [(_severity, _attr, message)] = _problems(table, _parcel(place=place))
    assert "3 coordinates, the domain has 2" in message


def test_another_coordinate_reference_system(table):
    other = "http://www.opengis.net/def/crs/EPSG/0/21781"
    [(_severity, _attr, message)] = _problems(table, _parcel(coordRefSys=other))
    assert message.startswith("[geometry]") and "21781" in message and "2056" in message


def test_place_without_a_coordinate_reference_system(table):
    feature = _parcel()
    del feature["coordRefSys"]
    [(_severity, _attr, message)] = _problems(table, feature)
    assert "without coordRefSys" in message


def test_the_collection_level_coordinate_reference_system_applies(table):
    feature = _parcel()
    del feature["coordRefSys"]
    document = {**_document(feature), "coordRefSys": _CRS}
    assert validate_jsonfg(document, symbol_table=table) == []


def test_a_circular_string_needs_an_odd_number_of_positions(table):
    road = {
        "type": "Feature",
        "id": "r1",
        "featureType": "Road",
        "geometry": None,
        "place": {"type": "CircularString", "coordinates": [[2600000.0, 1200000.0], [2600001.0, 1200001.0]]},
        "coordRefSys": _CRS,
        "properties": {},
    }
    [(_severity, _attr, message)] = _problems(table, road)
    assert "odd number of positions" in message


def test_a_geometry_for_a_class_without_one(table):
    [(_severity, _attr, message)] = _problems(table, {**_owner("o1", "ada"), "place": _parcel()["place"]})
    assert "no geometry attribute" in message


def test_a_duplicate_value_breaks_a_unique_constraint(table):
    problems = _problems(table, _owner("o1", "ada"), _owner("o2", "ada"))
    assert [a for _s, a, _m in problems] and all(m.startswith("[constraints]") for _s, _a, m in problems)
    assert "UNIQUE" in problems[0][2]


def test_a_violated_mandatory_constraint(table):
    [(severity, _attr, message)] = _problems(table, _parcel(properties={"Low": 50, "High": 10}))
    assert severity == "error" and message.startswith("[constraints]")


def test_constraints_can_be_switched_off(table):
    assert _problems(table, _owner("o1", "ada"), _owner("o2", "ada"), check_constraints=False) == []


def test_a_reference_to_no_feature_is_a_warning(table):
    [(severity, attribute, message)] = _problems(table, _parcel(properties={"Holder": "nobody"}))
    assert (severity, attribute) == ("warning", "Holder") and message.startswith("[references]")


def test_a_reference_resolves_in_the_document(table):
    assert _problems(table, _owner("o1", "ada"), _parcel(properties={"Holder": "o1"})) == []


def test_the_input_document_is_not_modified(table):
    document = _document(_owner("o1", "ada"), _parcel(properties={"Holder": "o1"}))
    before = copy.deepcopy(document)
    validate_jsonfg(document, symbol_table=table)
    assert document == before


_SHARED = """INTERLIS 2.4;
MODEL Two AT "http://x" VERSION "1" =
  TOPIC A =
    CLASS Item = Label : MANDATORY TEXT*3; END Item;
  END A;
  TOPIC B =
    CLASS Item = Count : MANDATORY 0 .. 9; END Item;
  END B;
END Two.
"""


def test_a_feature_type_shared_by_two_classes_conforms_to_either():
    table = build_from_text(_SHARED).symbol_table

    def feature(properties):
        return {"type": "Feature", "id": "i", "featureType": "Item", "geometry": None, "properties": properties}

    assert validate_jsonfg(_document(feature({"Label": "abc"})), symbol_table=table)[0].severity == "info"
    assert not [
        i for i in validate_jsonfg(_document(feature({"Count": 4})), symbol_table=table) if i.severity != "info"
    ]
    assert [i for i in validate_jsonfg(_document(feature({"Count": 40})), symbol_table=table) if i.severity == "error"]


_SOLID = ROOT / "tests/fixtures/solid3d"


def _solid_document():
    return json.loads((_SOLID / "building3d.jsonfg.json").read_text(encoding="utf-8"))


@pytest.fixture(scope="module")
def solid_table():
    from interlis.builder.repository import ModelRepository

    repository = ModelRepository([_SOLID])
    builder = build_from_file(_SOLID / "Building3D_V1.ili", repository=repository)
    return builder.symbol_table, repository


def test_a_solid_passes(solid_table):
    table, repository = solid_table
    assert validate_jsonfg(_solid_document(), symbol_table=table, repository=repository) == []


def test_a_point_is_not_a_solid(solid_table):
    table, repository = solid_table
    document = _solid_document()
    document["features"][0]["place"] = {"type": "Point", "coordinates": [2600000.0, 1200000.0, 500.0]}
    errors = [i for i in validate_jsonfg(document, symbol_table=table, repository=repository) if i.severity == "error"]
    assert errors and "Polyhedron" in errors[0].message


def test_a_flat_position_in_a_solid(solid_table):
    table, repository = solid_table
    document = _solid_document()
    polyhedron = document["features"][0]["place"]
    polyhedron["coordinates"][0][0][0][0] = polyhedron["coordinates"][0][0][0][0][:2]
    errors = [i for i in validate_jsonfg(document, symbol_table=table, repository=repository) if i.severity == "error"]
    assert errors and "2 coordinates, the domain has 3" in errors[0].message


def test_the_fixture_path_exists():
    assert Path(_SOLID / "building3d.jsonfg.json").is_file()


def test_a_decimal_in_an_integer_domain_is_only_a_warning(table):
    [(severity, attribute, message)] = _problems(table, _parcel(properties={"Low": 40.5, "High": 90}))
    assert (severity, attribute) == ("warning", "Low") and "is not of type 'integer'" in message


def test_an_empty_mandatory_list_is_only_a_warning():
    source = """INTERLIS 2.4;
MODEL Lst AT "http://x" VERSION "1" =
  TOPIC T =
    STRUCTURE Tag = Value : MANDATORY TEXT*5; END Tag;
    CLASS A = Tags : BAG {1..*} OF Tag; END A;
  END T;
END Lst.
"""
    feature = {"type": "Feature", "id": "a", "featureType": "A", "geometry": None, "properties": {"Tags": []}}
    issues = validate_jsonfg(_document(feature), symbol_table=build_from_text(source).symbol_table)
    assert [(i.severity, i.attribute) for i in issues] == [("warning", "Tags")]


_DEFINED = """INTERLIS 2.4;
MODEL Def AT "http://x" VERSION "1" =
  DOMAIN
    !!@CRS=EPSG:2056
    Coord2D = COORD 2460000.000 .. 2870000.000, 1045000.000 .. 1310000.000;
  TOPIC T =
    CLASS A =
      Pos : Coord2D;
      Note : TEXT*5;
    MANDATORY CONSTRAINT
      DEFINED(Pos) OR DEFINED(Note);
    END A;
  END T;
END Def.
"""


def test_a_geometry_counts_as_a_defined_attribute_in_a_constraint():
    table = build_from_text(_DEFINED, meta_attributes=meta_attribute_comments(_DEFINED)).symbol_table
    base = {"type": "Feature", "id": "a", "featureType": "A", "geometry": None, "properties": {}}
    with_geometry = {**base, "place": {"type": "Point", "coordinates": [2600000.0, 1200000.0]}, "coordRefSys": _CRS}
    assert validate_jsonfg(_document(with_geometry), symbol_table=table) == []
    assert [i.severity for i in validate_jsonfg(_document(base), symbol_table=table)] == ["error"]
