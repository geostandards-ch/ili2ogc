"""`restrictedStructureRef`'s optional `RESTRICTION(...)` clause - regression for a real StandardSymbology.ili bug.

Real corpus data (`StandardSymbology.ili`'s `FontSymbol.Geometry: LIST OF
FontSymbol_Geometry RESTRICTION(FontSymbol_Polyline; FontSymbol_Surface);`,
`tests/fixtures/cartosym/roadsexgm2ien_repo/`) raised `BuildError:
unresolved reference, not attributable to an import:
'FontSymbol_GeometryRESTRICTION(FontSymbol_Polyline;FontSymbol_Surface)'`
- `_resolve_or_defer` (`model_builder.py`) used `ctx.getText()` on the
whole `restrictedStructureRef` node, concatenating the base type ref with
the entire trailing RESTRICTION clause into one bogus name, instead of
reading just the base `structureRef(0)`.
"""

from conftest import build_from_text

_MODEL = """INTERLIS 2.4;
MODEL Foo AT "http://x" VERSION "1" =
  TOPIC T =
    STRUCTURE Base = END Base;
    STRUCTURE Sub1 EXTENDS Base = END Sub1;
    STRUCTURE Sub2 EXTENDS Base = END Sub2;
    CLASS Holder =
      Attr: LIST OF Base RESTRICTION(Sub1, Sub2);
    END Holder;
  END T;
END Foo.
"""


def test_restriction_clause_does_not_corrupt_the_base_type_name():
    builder = build_from_text(_MODEL)
    holder = builder.symbol_table.resolve("Foo.T.Holder")
    attr = holder.ClassAttribute[0]
    base_type = attr.Type.BaseType
    assert base_type.Name == "Base"
    assert base_type.Kind == "Structure"


def test_without_restriction_clause_still_resolves_the_bare_base_type():
    model = _MODEL.replace("Attr: LIST OF Base RESTRICTION(Sub1, Sub2);", "Attr: LIST OF Base;")
    builder = build_from_text(model)
    holder = builder.symbol_table.resolve("Foo.T.Holder")
    assert holder.ClassAttribute[0].Type.BaseType.Name == "Base"
