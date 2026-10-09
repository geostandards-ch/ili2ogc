"""`DIRECTED POLYLINE` domains get the `DirectedPolyline` kind, with the multi flag kept apart."""

from conftest import build_from_text as _build

_HEAD = """INTERLIS 2.4;
MODEL T AT "http://example.org/" VERSION "1" =
  DOMAIN
    C = COORD 0.000 .. 100.000 [INTERLIS.m], 0.000 .. 100.000 [INTERLIS.m], ROTATION 2 -> 1;
"""


def test_directed_polyline_kind():
    src = """    Plain = POLYLINE VERTEX C;
    Directed = DIRECTED POLYLINE VERTEX C;
    DirectedMulti = DIRECTED MULTIPOLYLINE VERTEX C;
    Multi = MULTIPOLYLINE VERTEX C;
"""
    builder = _build(_HEAD + src + "END T.\n")
    kinds = {
        n: (builder.symbol_table.resolve(n).Kind, builder.symbol_table.resolve(n).Multi)
        for n in ("Plain", "Directed", "DirectedMulti", "Multi")
    }
    assert kinds == {
        "Plain": ("Polyline", False),
        "Directed": ("DirectedPolyline", False),
        "DirectedMulti": ("DirectedPolyline", True),
        "Multi": ("Polyline", True),
    }
