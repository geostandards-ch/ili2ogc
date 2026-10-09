"""Metamodel attributes the builder fills the way the reference manual and IlisMeta16 define them."""

from conftest import build_from_text as _build

_HEAD = """INTERLIS 2.4;
MODEL T AT "http://example.org/" VERSION "1" =
  DOMAIN
    C = COORD 0.000 .. 100.000 [INTERLIS.m], 0.000 .. 100.000 [INTERLIS.m], ROTATION 2 -> 1;
"""


def test_domain_modifiers_and_enumeration_order():
    builder = _build(_HEAD + """    Plain = (a, b);
    Sealed (FINAL) = (a, b);
    Sorted = (a, b) ORDERED;
    Wrap = (a, b) CIRCULAR;
    Generic1 (GENERIC) = COORD 0.000 .. 100.000 [INTERLIS.m], 0.000 .. 100.000 [INTERLIS.m], ROTATION 2 -> 1;
END T.
""")
    table = builder.symbol_table
    assert [table.resolve(n).Order for n in ("Plain", "Sorted", "Wrap")] == ["Unordered", "Ordered", "Circular"]
    assert (table.resolve("Sealed").Final, table.resolve("Plain").Final) == (True, None)
    assert table.resolve("Generic1").Generic is True


def test_attribute_modifiers_and_subdivision_kind():
    builder = _build(_HEAD + """  STRUCTURE S =
    Locked (FINAL): 0 .. 9;
    SUBDIVISION Month: 1 .. 12;
    CONTINUOUS SUBDIVISION Second: 0.000 .. 59.999;
    Plain: 0 .. 9;
  END S;
END T.
""")
    attributes = {a.Name: a for a in builder.symbol_table.resolve("S").ClassAttribute}
    assert attributes["Locked"].Final is True and attributes["Plain"].Final is False
    kinds = {name: attributes[name].SubdivisionKind for name in ("Month", "Second", "Plain")}
    assert kinds == {"Month": "SubDiv", "Second": "ContSubDiv", "Plain": "NoSubDiv"}


def test_model_and_formatted_type_texts_are_unquoted():
    builder = _build(_HEAD + """  STRUCTURE Clock =
    SUBDIVISION Hour: 0 .. 23;
    SUBDIVISION Minute: 0 .. 59;
  END Clock;
  DOMAIN
    Time = FORMAT BASED ON Clock (Hour ":" Minute);
    Day = FORMAT INTERLIS.XMLDate "2000-01-01" .. "2099-12-31";
    Late EXTENDS Day = "2090-01-01" .. "2099-12-31";
END T.
""")
    table = builder.symbol_table
    model = table.resolve("T")
    assert (model.At, model.Version) == ("http://example.org/", "1")
    assert table.resolve("Time").Format == 'Hour":"Minute'
    assert (table.resolve("Day").Min, table.resolve("Day").Max) == ("2000-01-01", "2099-12-31")
    assert (table.resolve("Late").Min, table.resolve("Late").Max) == ("2090-01-01", "2099-12-31")
