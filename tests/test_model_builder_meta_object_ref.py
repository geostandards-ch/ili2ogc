"""`metaObjectRef` name resolution - regression for a real name-collision bug.

Real corpus data (`RoadsExgm2ien.ili`) names a `DrawingRule` after the
SIGN BASKET member it references (`Building OF ... SurfaceSign: WHERE ...
(Sign := {Building}; ...)` - both legitimately "Building") - a completely
normal INTERLIS idiom. `metaObjectRef` had no `resolves_to` hint
(`07_constraints.yml`), so `SymbolTable.resolve`'s kind-based
disambiguation never engaged when both a `MetaObjectDef` and a
`DrawingRule` shared the short name "Building", raising `BuildError:
unresolved reference, not attributable to an import: 'Building'`.
"""

from conftest import build_from_text

_MODEL = """INTERLIS 2.4;
MODEL Foo AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS SurfaceSign = Dummy: TEXT*1; END SurfaceSign;
    SIGN BASKET B ~ T
      OBJECTS OF SurfaceSign: Building;

    CLASS LandCover =
      Type: (building, other);
      Geometry: TEXT*1;
    END LandCover;

    GRAPHIC G BASED ON LandCover =
      Building OF SurfaceSign:
        WHERE Type == #building (
          Sign := {Building};
          Geometry := Geometry;
          Priority := 1
        );
    END G;
  END T;
END Foo.
"""


def test_drawing_rule_and_its_own_sign_basket_member_can_share_a_name():
    builder = build_from_text(_MODEL)
    graphic = builder.symbol_table.resolve("Foo.T.G")
    rule = graphic.DrawingRule if isinstance(graphic.DrawingRule, list) else [graphic.DrawingRule]
    assert rule[0].Name == "Building"
