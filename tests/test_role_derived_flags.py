"""Role.Mandatory, Role.External, Role.EmbeddedTransfer and Class.EmbeddedRoleTransfer follow the metamodel."""

from conftest import build_from_text as _build

_HEAD = """INTERLIS 2.4;
MODEL T AT "http://example.org/" VERSION "1" =
"""


def test_association_roles_carry_the_derived_flags():
    builder = _build(_HEAD + """  TOPIC X =
    CLASS A = END A;
    CLASS B = END B;
    CLASS C = END C;
    ASSOCIATION AB =
      a -- {1} A;
      b -- {0..*} B;
    END AB;
    ASSOCIATION BC =
      b2 -- {0..*} B;
      c2 -- {0..*} C;
    END BC;
    ASSOCIATION AC =
      a3 (EXTERNAL) -- {1} A;
      c3 -- {0..1} C;
    END AC;
  END X;
END T.
""")
    table = builder.symbol_table
    roles = {r.Name: r for r in table.all_registered() if r._qualified_class.endswith("ModelData.Role")}
    assert all(r.Mandatory is True for r in roles.values())
    assert roles["a"].EmbeddedTransfer is True and roles["b"].EmbeddedTransfer is None
    assert roles["a3"].External is True and roles["c3"].External is False
    association = {c.Name: c for c in table.all_registered() if c._qualified_class.endswith("ModelData.Class")}
    assert association["AB"].EmbeddedRoleTransfer is True and association["BC"].EmbeddedRoleTransfer is None
