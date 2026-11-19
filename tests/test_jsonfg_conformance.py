"""JSON-FG documents stay valid against the official schemas where the model allows it."""

from conftest import build_from_text

from interlis.convert.jsonfg import transfer_to_feature_collection
from interlis.xtf.parse import RawNode, XtfBasket, XtfObject, XtfTransfer

_TWO_TOPICS = """INTERLIS 2.4;
MODEL Foo AT "http://x" VERSION "1" =
  TOPIC A =
    CLASS Doc = Title : TEXT*10; END Doc;
  END A;
  TOPIC B =
    CLASS Doc = Title : TEXT*10; END Doc;
  END B;
END Foo.
"""


def _doc(tid: str, topic: str) -> XtfObject:
    title = RawNode(tag="Title", text="t", attrib={}, children=[])
    return XtfObject(tid=tid, qualified_class=f"Foo.{topic}.Doc", attributes={"Title": [title]})


def test_same_class_name_in_two_topics_gets_distinct_feature_types_matching_the_schema_keys():
    builder = build_from_text(_TWO_TOPICS)
    baskets = [
        XtfBasket(bid=topic, qualified_topic=f"Foo.{topic}", kind=None, endstate=None, objects=[_doc(topic, topic)])
        for topic in ("A", "B")
    ]
    transfer = XtfTransfer(sender=None, ili_version=None, models=[], baskets=baskets)
    collection = transfer_to_feature_collection(transfer, symbol_table=builder.symbol_table, schema_url="schema.json")
    types = sorted(f["featureType"] for f in collection["features"])
    assert types == ["Doc", "Doc_2"]
    assert sorted(collection["featureSchema"]) == ["Doc", "Doc_2"]
    assert collection["featureSchema"]["Doc_2"] == "schema.json#/$defs/Doc_2"


def test_a_model_without_collision_keeps_the_short_class_name():
    builder = build_from_text(
        _TWO_TOPICS.replace(
            "CLASS Doc = Title : TEXT*10; END Doc;\n  END B;", "CLASS Other = Title : TEXT*10; END Other;\n  END B;"
        )
    )
    obj = XtfObject(
        tid="1",
        qualified_class="Foo.A.Doc",
        attributes={"Title": [RawNode(tag="Title", text="t", attrib={}, children=[])]},
    )
    basket = XtfBasket(bid="A", qualified_topic="Foo.A", kind=None, endstate=None, objects=[obj])
    transfer = XtfTransfer(sender=None, ili_version=None, models=[], baskets=[basket])
    collection = transfer_to_feature_collection(transfer, symbol_table=builder.symbol_table)
    assert collection["featureType"] == "Doc"


_JOIN_SAME_NAME = """INTERLIS 2.4;
MODEL Test AT "http://x" VERSION "1" =
  TOPIC Base =
    CLASS B = Label : TEXT*20; END B;
    CLASS C = Label : TEXT*20; END C;
  END Base;
  TOPIC Views =
    DEPENDS ON Test.Base;
    VIEW VJ
      JOIN OF B ~ Test.Base.B, C ~ Test.Base.C;
      =
      ATTRIBUTE
        FromB := B -> Label;
        FromC := C -> Label;
    END VJ;
  END Views;
END Test.
"""


def test_join_reads_each_alias_own_attribute_when_two_bases_share_a_name():
    from interlis.convert.jsonfg import evaluate_view

    builder = build_from_text(_JOIN_SAME_NAME)
    view = next(
        i
        for i in builder.symbol_table.all_registered()
        if getattr(i, "_qualified_class", None) == "IlisMeta16.ModelData.View" and i.Name == "VJ"
    )

    def obj(tid, cls, text):
        return XtfObject(
            tid=tid,
            qualified_class=f"Test.Base.{cls}",
            attributes={"Label": [RawNode(tag="Label", text=text, attrib={}, children=[])]},
        )

    basket = XtfBasket(
        bid="b",
        qualified_topic="Test.Base",
        kind=None,
        endstate=None,
        objects=[obj("b1", "B", "x"), obj("c1", "C", "y")],
    )
    transfer = XtfTransfer(sender=None, ili_version=None, models=[], baskets=[basket])
    features = evaluate_view(view, transfer, symbol_table=builder.symbol_table)
    assert features[0]["properties"] == {"FromB": "x", "FromC": "y"}


def test_view_all_of_includes_the_attributes_the_base_class_inherits():
    from interlis.convert.jsonschema import convertible_roots, model_to_json_schema

    builder = build_from_text("""INTERLIS 2.4;
MODEL Foo AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS A = Base : TEXT*10; END A;
    CLASS B EXTENDS A = Own : TEXT*10; END B;
  END T;
  TOPIC V =
    DEPENDS ON Foo.T;
    VIEW VB
      PROJECTION OF B ~ Foo.T.B;
      =
      ATTRIBUTE
        ALL OF B;
    END VB;
  END V;
END Foo.
""")
    schema = model_to_json_schema(convertible_roots(builder.symbol_table), symbol_table=builder.symbol_table)
    assert list(schema["$defs"]["VB"]["properties"]) == ["Base", "Own"]


def test_numeric_attribute_with_a_unit_carries_x_ogc_unit():
    from interlis.convert.jsonschema import class_to_json_schema

    builder = build_from_text("""INTERLIS 2.4;
MODEL Foo AT "http://x" VERSION "1" =
  UNIT Kilometer [km] = 1000 [INTERLIS.m];
  TOPIC T =
    CLASS A =
      Length : 0.0 .. 100.0 [Kilometer];
      Plain : 0 .. 10;
    END A;
  END T;
END Foo.
""")
    schema = class_to_json_schema(builder.symbol_table.resolve("A"))
    assert schema["properties"]["Length"]["x-ogc-unit"] == "Kilometer"
    assert "x-ogc-unit" not in schema["properties"]["Plain"]


_POLY_MODEL = """INTERLIS 2.4;
MODEL Foo AT "http://x" VERSION "1" =
  TOPIC T =
    STRUCTURE Shape (ABSTRACT) = Base : TEXT*5; END Shape;
    STRUCTURE Circle EXTENDS Shape = Radius : 0 .. 100; END Circle;
    /** A class holding a shape */
    CLASS A =
      /** The shape */
      S : Shape;
    END A;
  END T;
END Foo.
"""


def _node(tag, text=None, children=()):
    return RawNode(tag=tag, text=text, attrib={}, children=list(children))


def test_a_structure_occurrence_keeps_the_attributes_of_its_concrete_subclass():
    from interlis.convert.jsonfg import object_to_feature

    builder = build_from_text(_POLY_MODEL)
    wire = _node("S", children=[_node("Foo.T.Circle", children=[_node("Base", "b"), _node("Radius", "7")])])
    obj = XtfObject(tid="1", qualified_class="Foo.T.A", attributes={"S": [wire]})
    feature = object_to_feature(obj, builder.symbol_table.resolve("A"), symbol_table=builder.symbol_table)
    assert feature["properties"]["S"] == {"Base": "b", "Radius": 7}


def test_schema_is_closed_and_carries_the_documentation_comments():
    from interlis.convert.jsonschema import class_to_json_schema
    from interlis.runtime.parse import meta_attribute_comments

    builder = build_from_text(_POLY_MODEL, meta_attributes=meta_attribute_comments(_POLY_MODEL))
    schema = class_to_json_schema(builder.symbol_table.resolve("A"))
    assert schema["additionalProperties"] is False
    assert schema["description"] == "A class holding a shape"
    assert schema["properties"]["S"]["description"] == "The shape"
