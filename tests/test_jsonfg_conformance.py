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
    assert feature["properties"]["S"] == {"@type": "Foo.T.Circle", "Base": "b", "Radius": 7}


def test_schema_is_closed_and_carries_the_documentation_comments():
    from interlis.convert.jsonschema import class_to_json_schema
    from interlis.runtime.parse import meta_attribute_comments

    builder = build_from_text(_POLY_MODEL, meta_attributes=meta_attribute_comments(_POLY_MODEL))
    schema = class_to_json_schema(builder.symbol_table.resolve("A"))
    assert schema["additionalProperties"] is False
    assert schema["description"] == "A class holding a shape"
    assert schema["properties"]["S"]["description"] == "The shape"


def test_polymorphic_structure_branches_are_told_apart_by_a_type_constant():
    import jsonschema

    from interlis.convert.jsonschema import convertible_roots, model_to_json_schema

    builder = build_from_text(_POLY_MODEL)
    schema = model_to_json_schema(convertible_roots(builder.symbol_table), symbol_table=builder.symbol_table)
    validator = jsonschema.Draft202012Validator({"$defs": schema["$defs"], "$ref": "#/$defs/A"})
    assert validator.is_valid({"S": {"@type": "Foo.T.Circle", "Base": "b", "Radius": 7}})
    assert not validator.is_valid({"S": {"@type": "Foo.T.Circle", "Side": 7}})  # a Square's member on a Circle
    assert not validator.is_valid({"S": {"Base": "b", "Radius": 7}})  # no @type


_TIME_MODEL = """INTERLIS 2.4;
MODEL Foo AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS Plain = Day : INTERLIS.XMLDate; Name : TEXT*5; END Plain;
    CLASS Spanned =
      !!@primaryInterval=start
      From : INTERLIS.XMLDate;
      !!@primaryInterval=end
      Until : INTERLIS.XMLDateTime;
    END Spanned;
    CLASS Two = A : INTERLIS.XMLDate; B : INTERLIS.XMLDate; END Two;
  END T;
END Foo.
"""


def _feature(cls_name, **values):
    from interlis.convert.jsonfg import object_to_feature
    from interlis.runtime.parse import meta_attribute_comments

    builder = build_from_text(_TIME_MODEL, meta_attributes=meta_attribute_comments(_TIME_MODEL))
    attrs = {k: [RawNode(tag=k, text=v, attrib={}, children=[])] for k, v in values.items()}
    obj = XtfObject(tid="1", qualified_class=f"Foo.T.{cls_name}", attributes=attrs)
    return object_to_feature(obj, builder.symbol_table.resolve(cls_name), symbol_table=builder.symbol_table)


def test_a_single_date_attribute_is_the_primary_instant():
    feature = _feature("Plain", Day="2017-12-08", Name="x")
    assert feature["time"] == {"date": "2017-12-08"}
    assert feature["properties"]["Day"] == "2017-12-08"  # kept: the INTERLIS attribute is not lost


def test_designated_interval_ends_with_an_open_end_and_a_utc_timestamp():
    feature = _feature("Spanned", From="2001-04-12", Until="2020-06-30T14:30:00")
    assert feature["time"] == {"interval": ["2001-04-12", "2020-06-30T12:30:00Z"]}  # CEST is UTC+2
    assert _feature("Spanned", From="2001-04-12")["time"] == {"interval": ["2001-04-12", ".."]}
    assert _feature("Spanned", Until="2020-06-30T00:00:00.000")["time"] == {"interval": ["..", "2020-06-30"]}


def test_no_time_when_two_attributes_are_not_designated_or_all_are_absent():
    assert "time" not in _feature("Two", A="2000-01-01", B="2001-01-01")
    assert "time" not in _feature("Plain", Name="x")


def test_schema_tags_the_temporal_roles():
    from interlis.convert.jsonschema import class_to_json_schema
    from interlis.runtime.parse import meta_attribute_comments

    builder = build_from_text(_TIME_MODEL, meta_attributes=meta_attribute_comments(_TIME_MODEL))
    props = class_to_json_schema(builder.symbol_table.resolve("Spanned"))["properties"]
    assert props["From"]["x-ogc-role"] == "primary-interval-start"
    assert props["Until"]["x-ogc-role"] == "primary-interval-end"
    assert (
        class_to_json_schema(builder.symbol_table.resolve("Plain"))["properties"]["Day"]["x-ogc-role"]
        == "primary-instant"
    )


def test_x_interlis_carries_class_basket_and_transfer_header():
    from interlis.convert.jsonschema import class_to_json_schema
    from interlis.xtf.parse import XtfModelRef

    builder = build_from_text(_TWO_TOPICS)
    obj = XtfObject(
        tid="1",
        qualified_class="Foo.A.Doc",
        attributes={"Title": [RawNode(tag="Title", text="t", attrib={}, children=[])]},
        basket="b1",
    )
    basket = XtfBasket(bid="b1", qualified_topic="Foo.A", kind="FULL", endstate=None, objects=[obj])
    transfer = XtfTransfer(
        sender="me", ili_version="2.3", models=[XtfModelRef("Foo", "1", "http://x")], baskets=[basket]
    )
    collection = transfer_to_feature_collection(transfer, symbol_table=builder.symbol_table)
    assert collection["features"][0]["x-interlis"] == {"class": "Foo.A.Doc", "basket": "b1"}
    assert collection["x-interlis"] == {
        "sender": "me",
        "iliVersion": "2.3",
        "models": [{"name": "Foo", "version": "1", "uri": "http://x"}],
        "baskets": [{"bid": "b1", "topic": "Foo.A", "kind": "FULL"}],
    }
    schema = class_to_json_schema(builder.symbol_table.resolve("Foo.A.Doc"), symbol_table=builder.symbol_table)
    assert schema["x-interlis"]["class"].endswith("Doc")


_CONSTRAINT_MODEL = """INTERLIS 2.4;
MODEL Foo AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS A =
      Kind : (a, b);
      Width : 0 .. 100;
      Other : 0 .. 100;
      MANDATORY CONSTRAINT WidthIfB: Kind == #b => Width > 10;
      MANDATORY CONSTRAINT Cmp: Width < Other;
    END A;
  END T;
END Foo.
"""


def test_per_object_constraints_become_schema_fragments_and_the_rest_is_listed():
    from interlis.convert.jsonschema import convertible_roots, model_to_json_schema, validate_feature_properties

    builder = build_from_text(_CONSTRAINT_MODEL)
    schema = model_to_json_schema(convertible_roots(builder.symbol_table), symbol_table=builder.symbol_table)
    entry = schema["$defs"]["A"]
    assert len(entry["allOf"]) == 1 and "WidthIfB" in entry["allOf"][0]["description"]
    assert [u["constraint"] for u in entry["x-interlis"]["untranslatedConstraints"]] == ["Cmp"]
    assert validate_feature_properties({"Kind": "b", "Width": 50}, schema, "A") == []
    assert validate_feature_properties({"Kind": "b", "Width": 5}, schema, "A")
    assert validate_feature_properties({"Kind": "a", "Width": 5}, schema, "A") == []


def test_a_constraint_reading_a_geometry_attribute_is_left_out_of_the_schema():
    from interlis.convert.jsonschema import class_to_json_schema

    builder = build_from_text("""INTERLIS 2.4;
MODEL Foo AT "http://x" VERSION "1" =
  DOMAIN
    !!@CRS=EPSG:2056
    Lc = COORD 2460000.000 .. 2870000.000, 1045000.000 .. 1310000.000;
  TOPIC T =
    CLASS A =
      Pos : Lc;
      Name : TEXT*5;
      MANDATORY CONSTRAINT DEFINED(Pos) OR DEFINED(Name);
    END A;
  END T;
END Foo.
""")
    schema = class_to_json_schema(builder.symbol_table.resolve("A"), symbol_table=builder.symbol_table)
    assert "allOf" not in schema  # `Pos` is in `place`: the fragment would call it undefined
    assert "geometry attribute" in schema["x-interlis"]["untranslatedConstraints"][0]["reason"]
