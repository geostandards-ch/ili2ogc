"""The JSON Schema of a model describes the properties `convert-jsonfg` writes for its objects."""

import jsonschema
import pytest
from conftest import build_from_text

from interlis.convert.jsonfg import object_to_feature
from interlis.convert.jsonschema import model_to_json_schema
from interlis.xtf.parse import RawNode, XtfObject

_MODEL = """INTERLIS 2.4;
MODEL Agree AT "http://x" VERSION "1" =
  DOMAIN
    Coord2 = COORD 0.000 .. 100.000, 0.000 .. 100.000;
    Coord3 = COORD NUMERIC, NUMERIC, NUMERIC;
    Points = MULTICOORD 0.000 .. 100.000, 0.000 .. 100.000;
    Path = POLYLINE WITH (STRAIGHTS) VERTEX Coord2;
    Ring = SURFACE WITH (STRAIGHTS) VERTEX Coord2 WITHOUT OVERLAPS > 0.001;
  TOPIC T =
    CLASS Item =
      Code : TEXT*10;
    END Item;
    STRUCTURE ItemRef =
      To : MANDATORY REFERENCE TO Item;
    END ItemRef;
    STRUCTURE Detail =
      Label : TEXT*5;
      Via : REFERENCE TO Item;
      Spot : Coord3;
    END Detail;
    CLASS Thing =
      Name : TEXT*10;
      Where : Coord2;
      Many : Points;
      Line : Path;
      Area : Ring;
      Kind : ItemRef;
      Info : Detail;
      Kinds : BAG {0..*} OF ItemRef;
    END Thing;
  END T;
END Agree.
"""


def _node(tag, text=None, *, ref=None, children=()):
    return RawNode(tag=tag, text=text, attrib={"REF": ref} if ref else {}, children=list(children))


def _coord(*values):
    return _node("COORD", children=[_node(f"C{i}", v) for i, v in enumerate(values, 1)])


def _line(*coords):
    return _node("POLYLINE", children=[_coord(*c) for c in coords])


def _thing():
    ring = _node("BOUNDARY", children=[_line((0, 0), (10, 0), (10, 10), (0, 0))])
    attributes = {
        "Name": [_node("Name", "abc")],
        "Where": [_node("Where", children=[_coord("1", "2")])],
        "Many": [_node("Many", children=[_node("MULTICOORD", children=[_coord("1", "2"), _coord("3", "4")])])],
        "Line": [_node("Line", children=[_line(("1", "2"), ("3", "4"))])],
        "Area": [_node("Area", children=[_node("SURFACE", children=[ring])])],
        "Kind": [_node("Kind", children=[_node("ItemRef", children=[_node("To", ref="i1")])])],
        "Info": [
            _node(
                "Info",
                children=[
                    _node(
                        "Detail",
                        children=[
                            _node("Label", "ok"),
                            _node("Via", ref="i1"),
                            _node("Spot", children=[_coord("1", "2", "3")]),
                        ],
                    )
                ],
            )
        ],
        "Kinds": [
            _node("Kinds", children=[_node("ItemRef", children=[_node("To", ref="i1")]) for _ in range(2)]),
        ],
    }
    return XtfObject(tid="t1", qualified_class="Agree.T.Thing", attributes=attributes)


@pytest.fixture(scope="module")
def validated():
    builder = build_from_text(_MODEL)
    thing = builder.symbol_table.resolve("Thing")
    classes = [builder.symbol_table.resolve(name) for name in ("Thing", "Item", "ItemRef", "Detail")]
    schema = model_to_json_schema(classes, symbol_table=builder.symbol_table)
    feature = object_to_feature(_thing(), thing, symbol_table=builder.symbol_table)
    root = {"$schema": schema["$schema"], "$defs": schema["$defs"], "$ref": "#/$defs/Thing"}
    return feature, jsonschema.Draft202012Validator(root)


def test_feature_properties_validate_against_the_model_schema(validated):
    feature, validator = validated
    assert [e.message for e in validator.iter_errors(feature["properties"])] == []


def test_a_reference_wrapper_structure_is_the_bare_oid(validated):
    feature, _ = validated
    assert feature["properties"]["Kind"] == "i1"
    assert feature["properties"]["Kinds"] == ["i1", "i1"]


def test_a_structure_with_a_reference_among_other_attributes_stays_an_object(validated):
    feature, _ = validated
    info = feature["properties"]["Info"]
    assert info["Label"] == "ok"
    assert info["Via"] == "i1"
    assert info["Spot"]["coordinates"] == [1.0, 2.0, 3.0]


def test_a_value_breaking_the_schema_is_reported(validated):
    feature, validator = validated
    broken = dict(feature["properties"], Name="x" * 11)
    assert any("too long" in e.message for e in validator.iter_errors(broken))
