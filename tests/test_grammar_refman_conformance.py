"""INTERLIS 2.4 syntax from the reference manual (eCH-0031 V2.1.0) that the grammar must accept.

Each case is a minimal model fragment for one EBNF rule, cited in its id.
"""

from pathlib import Path

import pytest
from conftest import build_from_text

from interlis.runtime.parse import parse_file, parse_text


def _model(body: str) -> str:
    return f'INTERLIS 2.4;\nMODEL M AT "http://x" VERSION "1" =\n{body}\nEND M.\n'


def _topic(body: str) -> str:
    return _model(f"  TOPIC T =\n{body}\n  END T;")


_CASES = {
    # 3.8.9 OIDType = 'OID' ( 'ANY' | NumericType | TextType ).
    "3.8.9-oid-numeric": _model("  DOMAIN O = OID NUMERIC;"),
    # 3.8.11 ClassType = 'CLASS' [ 'RESTRICTION' '(' ViewableRef { ';' ViewableRef } ')' ] | ...
    "3.8.11-class-restriction": _topic(
        "    CLASS A = END A;\n    CLASS B = END B;\n    DOMAIN K = CLASS RESTRICTION (A; B);"
    ),
    # 3.8.11 AttributePathType = 'ATTRIBUTE' ... [ 'RESTRICTION' '(' AttrTypeDef { ';' AttrTypeDef } ')' ].
    "3.8.11-attribute-restriction": _model("  DOMAIN P = ATTRIBUTE RESTRICTION (TEXT; BOOLEAN);"),
    # 3.8.12 LineFormType = ( 'STRAIGHTS' | 'ARCS' | [ Model-Name '.' ] LineFormType-Name ).
    # 3.8.12 IntersectionDef = 'WITHOUT' 'OVERLAPS' [ '>' Dec ].
    # 3.8.12 LineFormTypeDef = 'LINE' 'FORM' { LineFormType-Name ':' LineStructure-Name ';' }.
    "3.8.12-line-forms": _model(
        "  STRUCTURE S = END S;\n"
        "  LINE FORM Bez : S; Clo : S;\n"
        "  DOMAIN C = COORD 0 .. 10, 0 .. 10;\n"
        "  DOMAIN L = POLYLINE WITH (STRAIGHTS, Bez, M.Clo) VERTEX C WITHOUT OVERLAPS;"
    ),
    # 3.10.1 MetaDataBasketDef = ( 'SIGN' | 'REFSYSTEM' ) 'BASKET' Basket-Name Properties<FINAL> ... '~' TopicRef
    #        { 'OBJECTS' 'OF' Class-Name ':' MetaObject-Name { ',' MetaObject-Name } } ';'.
    "3.10.1-basket-final": _model("  SIGN BASKET B (FINAL) ~ S.T\n    OBJECTS OF A: a, b;"),
    "3.10.1-basket-no-objects": _model("  SIGN BASKET B ~ S.T;"),
    # 3.2.3 String = '"' { <any character except '\\' or '"'> | ... } '"'.
    "3.2.3-string-apostrophe": _topic(
        '    CLASS A =\n      x : TEXT;\n      MANDATORY CONSTRAINT x == "l\'eau";\n    END A;'
    ),
    # 3.6.1 AttrTypeDef = ( 'MANDATORY' [ AttrType ] | ... ).
    "3.6.1-mandatory-without-type": _topic(
        "    CLASS A =\n      a : TEXT;\n    END A;\n"
        "    CLASS B EXTENDS A =\n      a (EXTENDED) : MANDATORY;\n    END B;"
    ),
    # 3.8 DomainDef = 'DOMAIN' { ... '=' ( 'MANDATORY' [ Type ] | Type ) [ 'CONSTRAINTS' Name ':' Logical-Expression
    #     { ',' Name ':' Logical-Expression } ] ';' }.
    "3.8-domain-mandatory-without-type": _model("  DOMAIN D = TEXT;\n  D2 EXTENDS D = MANDATORY;"),
    "3.8-domain-constraints": _model("  DOMAIN D = 0 .. 10 CONSTRAINTS c1: THIS > 1, c2: THIS < 9;"),
    # 3.8 / 3.9.3 / 3.8.8: 'DOMAIN' { ... }, 'UNIT' { ... }, 'CONTEXT' { ... } may be empty.
    "3.8-empty-sections": _model("  DOMAIN\n  UNIT\n  CONTEXT\n  DOMAIN D = TEXT;"),
    # 3.5.3 ClassOrStructureDef = [ 'ATTRIBUTE' ] { AttributeDef } { ConstraintDef } [ 'PARAMETER' { ParameterDef } ].
    "3.5.3-empty-attribute-and-parameter": _topic(
        "    CLASS A =\n      ATTRIBUTE\n    END A;\n    CLASS B EXTENDS INTERLIS.SIGN =\n      PARAMETER\n    END B;"
    ),
    # 3.7.1 AssociationDef = ... { RoleDef } [ 'ATTRIBUTE' ] { AttributeDef } ...  (keyword optional);
    #       [ 'DERIVED' 'FROM' RenamedViewableRef ]; RoleDef = ... [ ':=' Role-Factor ] ';'.
    "3.7.1-association-attributes": _topic(
        "    CLASS A = END A;\n    ASSOCIATION R =\n      a -- A;\n      b -- A;\n"
        "      x (FINAL) : TEXT*5;\n      y : 0 .. 10 := x;\n    END R;"
    ),
    "3.7.1-derived-from": _topic(
        "    CLASS A = END A;\n    VIEW V PROJECTION OF A; =\n    END V;\n"
        "    ASSOCIATION R DERIVED FROM v ~ M.T.V =\n      a -- A := THIS;\n      b -- A := THIS;\n    END R;"
    ),
    # 3.8.5 NumericType = ( ... | 'NUMERIC' ) [ 'CIRCULAR' ] [ '[' UnitRef ']' ] [ 'CLOCKWISE' | ... | RefSys ].
    "3.8.5-numeric-keyword-forms": _model(
        "  DOMAIN C = COORD 0 .. 10, 0 .. 10;\n"
        "  DOMAIN N1 = NUMERIC CIRCULAR [INTERLIS.rad] COUNTERCLOCKWISE;\n"
        "  DOMAIN N2 = NUMERIC [INTERLIS.rad] CLOCKWISE;\n"
        "  DOMAIN N3 = NUMERIC <C[1]>;"
    ),
    # 3.8.8 CoordinateType = ( 'COORD' | 'MULTICOORD' ) NumericType [ ',' NumericType [ ',' NumericType ]
    #       [ ',' RotationDef ] [ 'REFSYS' Name-String ] ].
    "3.8.8-coord-numeric-axes": _model(
        "  DOMAIN C1 = COORD NUMERIC [INTERLIS.m], NUMERIC [INTERLIS.m];\n"
        "  DOMAIN C2 = COORD NUMERIC {CHLV03[1]}, NUMERIC {CHLV03[2]};"
    ),
    # 3.8.11 AttributePathConst = '>>' [ ViewableRef '->' ] Attribute-Name.
    "3.8.11-attribute-path-const": _topic(
        "    CLASS A =\n      x : TEXT;\n    END A;\n    CLASS B =\n      p : ATTRIBUTE;\n"
        "      MANDATORY CONSTRAINT p == >>M.T.A->x;\n    END B;"
    ),
    # 3.10.2.2 ParameterDef = ... ( AttrTypeDef | 'METAOBJECT' [ 'OF' MetaObject-ClassRef ] ) ';'.
    "3.10.2-metaobject-of-class": _topic(
        "    CLASS A EXTENDS INTERLIS.SIGN =\n      PARAMETER\n        s : METAOBJECT OF INTERLIS.SIGN;\n    END A;"
    ),
    # 3.13 AttributeRef = Attribute-Name [ '[' ( 'FIRST' | 'LAST' | AxisListIndex-PosNumber ) ']' ] | ...
    # 3.13 FunctionCall = [ Model-Name '.' [ Topic-Name '.' ] ] Function-Name '(' [ Argument { ',' Argument } ] ')'.
    # 3.13 Argument = ( Expression | 'ALL' [ '(' ( RestrictedClassOrAssRef | ViewableRef ) ')' ] ).
    "3.13-calls-and-indexes": _model(
        "  FUNCTION f (): BOOLEAN;\n  TOPIC T =\n    CLASS A =\n      c : TEXT;\n"
        "      MANDATORY CONSTRAINT DEFINED(c[1]);\n      MANDATORY CONSTRAINT M.f();\n"
        "      MANDATORY CONSTRAINT INTERLIS.objectCount(ALL(M.T.A)) > 0;\n    END A;\n  END T;"
    ),
    # 3.8.3 INTERLIS.HALIGNMENT / VALIGNMENT are domains of the INTERLIS model.
    "3.8.3-alignment-domains": _topic(
        "    CLASS A =\n      h : INTERLIS.HALIGNMENT;\n      v : INTERLIS.VALIGNMENT;\n    END A;"
    ),
    # 3.2.7: UUIDOID and INTERLIS1 are not reserved words - usable as names.
    "3.2.7-non-reserved-keywords": _topic(
        "    CLASS A =\n      UUIDOID : OID TEXT*36;\n      k : (INTERLIS1, INTERLIS2);\n    END A;"
    ),
    # 3.9.3 UnitDef = ... Unit-Name [ '(' 'ABSTRACT' ')' | ... ] [ 'EXTENDS' ... ] [ '=' ... ] ';' - '=' is optional.
    "3.9.3-abstract-unit": _model("  UNIT\n    Angle (ABSTRACT);\n    Grad [gr] EXTENDS Angle;"),
    # 3.11 RunTimeParameterDef = 'PARAMETER' { RunTimeParameter-Name ':' AttrTypeDef ';' }.
    "3.11-runtime-parameters": _model("  PARAMETER\n    a : TEXT*10;\n    b : BOOLEAN;"),
    # 3.14 FunctionDef = 'FUNCTION' Function-Name '(' [ Argument-Name ':' ArgumentType { ';' ... } ] ')'
    #      ':' ArgumentType [ Explanation ] ';'.
    "3.14-function-explanation": _model("  FUNCTION f (a: TEXT): BOOLEAN // checks a //;"),
    "3.14-function-no-arguments": _model("  FUNCTION f (): BOOLEAN;"),
    "3.14-function-objects-result": _model("  FUNCTION f (a: TEXT): OBJECTS OF ANYCLASS;"),
    # 3.8.2 Enumeration = '(' ( EnumElement { ',' EnumElement } [ ':' 'FINAL' ] | 'FINAL' ) ')'.
    "3.8.2-enumeration-final": _model(
        "  DOMAIN F = (rot, blau);\n  DOMAIN FP EXTENDS F = (rot (FINAL), blau (hell, dunkel));"
    ),
    # 3.8.5 NumericType = ( Min-Dec '..' Max-Dec | 'NUMERIC' ) ... [ 'CLOCKWISE' | 'COUNTERCLOCKWISE' | RefSys ].
    #       RefSys = ( '{' RefSys-MetaObjectRef [ '[' Axis-PosNumber ']' ] '}' | '<' Coord-DomainRef [ ... ] '>' ).
    "3.8.5-refsys-after-range": _model(
        "  DOMAIN C = COORD 0 .. 10, 0 .. 10;\n"
        "  DOMAIN H1 = 0 .. 10 [INTERLIS.m] {CHLV03};\n"
        "  DOMAIN H2 = 0 .. 10 {B.CHLV03[1]};\n"
        "  DOMAIN H3 = 0 .. 10 <C[1]>;\n"
        "  DOMAIN H4 = 0 .. 10 <M.C[1]>;"
    ),
    # 3.8.5 Min-Dec / Max-Dec with signs: Dec = ( Number | Float ).
    "3.8.5-signed-bound": _model("  DOMAIN R = 1 .. +5;"),
    # 3.2.2 Name = Letter { Letter | Digit | '_' }  (a name like e2 is not a Float scaling).
    "3.2.2-names-like-scaling": _model("  DOMAIN E1 = TEXT;\n  DOMAIN D = (e2, a);"),
    # 3.8.6 FormattedType = 'FORMAT' 'BASED' 'ON' StructureRef FormatDef [ Min-String '..' Max-String ] | ...
    "3.8.6-format-based-on-bounds": _model(
        "  STRUCTURE S = h : 0 .. 23; m : 0 .. 59; END S;\n"
        '  DOMAIN T = FORMAT BASED ON S (h/2 ":" m/2) "00:00" .. "23:59";'
    ),
    # 3.16 DrawingRule = DrawingRule-Name Properties<ABSTRACT,EXTENDED,FINAL> [ 'OF' Sign-ClassRef ] ':' ...
    "3.16-drawing-rule-properties": _topic(
        "    CLASS A = END A;\n    GRAPHIC G BASED ON A =\n      Sym (EXTENDED) OF S.T.Sym : (P := 1);\n    END G;"
    ),
    # 3.15 Join = 'JOIN' 'OF' ... : 'JOIN' and 'OF' are two tokens, any whitespace between them.
    "3.15-join-of-whitespace": _topic(
        "    CLASS A = END A;\n    CLASS B = END B;\n    VIEW V JOIN\n      OF A, B; =\n    END V;"
    ),
    "3.16-based-on-whitespace": _topic("    CLASS A = END A;\n    GRAPHIC G BASED\n      ON A =\n    END G;"),
    # 3.13 Factor = ( ... | ( Inspection | 'INSPECTION' ... ) ... ): an Inspection inside an expression.
    "3.13-inspection-in-expression": _topic(
        "    STRUCTURE S = END S;\n    CLASS A =\n      g : BAG OF S;\n"
        "      MANDATORY CONSTRAINT INTERLIS.objectCount(INSPECTION OF M.T.A -> g) >= 0;\n    END A;"
    ),
    # 3.6.1 AttributeDef = ... ':' AttrTypeDef [ ':=' Expression { ',' Expression } ] ';'.
    "3.6.1-derived-expression": _topic("    CLASS A =\n      b : 0 .. 100;\n      c : 0 .. 100 := b + 1;\n    END A;"),
    # 3.6.1 AttrTypeDef = ... ( ( 'BAG' | 'LIST' ) [ Cardinality ] 'OF' AttrType ).
    "3.6.1-bag-of-reference": _topic(
        "    CLASS B = END B;\n    STRUCTURE S =\n      r : BAG OF REFERENCE TO B;\n    END S;"
    ),
    # 3.5.2 / 3.5.3 'OID' 'AS' OID-DomainRef, DomainRef = [ Model-Name '.' [ Topic-Name '.' ] ] Domain-Name.
    "3.5.2-topic-oid-interlis": _model(
        "  TOPIC T =\n    BASKET OID AS INTERLIS.STANDARDOID;\n    OID AS INTERLIS.I32OID;\n  END T;"
    ),
    "3.5.3-class-oid-qualified": _model(
        "  TOPIC T0 =\n    DOMAIN MyOID = OID TEXT*12;\n  END T0;\n"
        "  TOPIC T =\n    DEPENDS ON M.T0;\n    CLASS A =\n      OID AS M.T0.MyOID;\n    END A;\n  END T;"
    ),
}


@pytest.mark.parametrize("case", sorted(_CASES))
def test_reference_manual_syntax_parses(case: str):
    _tree, errors = parse_text(_CASES[case])
    assert errors == [], errors


def test_official_time_model_parses():
    """refhb24 `Time.ili`: a FUNCTION with an Explanation after its result type."""
    _tree, errors = parse_file(Path(__file__).parent / "fixtures" / "refhb24" / "Time.ili")
    assert errors == [], errors


_BUILT = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  PARAMETER
    a : TEXT*10;
    b : BOOLEAN;
  STRUCTURE S = END S;
  LINE FORM Bez : S; Clo : S;
  FUNCTION f (x: TEXT): BOOLEAN // checks x //;
  DOMAIN O = OID NUMERIC;
  UNIT
    L (ABSTRACT) = (INTERLIS.LENGTH);
    Mt [m] EXTENDS L;
    Km [km] = 1000 [Mt];
    F [oF] = FUNCTION // T = 9/5 * K - 459.67 // [Mt];
  TOPIC T =
    CLASS B = END B;
    STRUCTURE R =
      r : BAG OF REFERENCE TO B;
    END R;
    CLASS Sign EXTENDS INTERLIS.SIGN =
    PARAMETER
      p1 : TEXT*5;
      p2 : BOOLEAN;
    END Sign;
  END T;
END M.
"""


def _elements(builder, kind: str) -> dict:
    model = next(i for i in builder.symbol_table.all_registered() if i._qualified_class.endswith(".Model"))
    return {e.Name: e for e in model.Element if e._qualified_class.endswith("." + kind)}


def test_reference_manual_definitions_are_built():
    builder = build_from_text(_BUILT)
    params = _elements(builder, "AttrOrParam")
    assert set(params) == {"a", "b"}
    forms = _elements(builder, "LineForm")
    assert {name: form.Structure.Name for name, form in forms.items()} == {"Bez": "S", "Clo": "S"}
    assert _elements(builder, "FunctionDef")["f"].Explanation == "// checks x //"
    assert builder.symbol_table.resolve("M.O")._qualified_class.endswith(".NumType")
    units = _elements(builder, "Unit")
    assert {name: unit.Kind for name, unit in units.items()} == {
        "L": "ComposedU",
        "Mt": "BaseU",
        "Km": "DerivedU",
        "F": "DerivedU",
    }


def test_bag_of_reference_and_class_parameters_are_built():
    builder = build_from_text(_BUILT)
    r = builder.symbol_table.resolve("M.T.R").ClassAttribute[0]
    assert r.Type.BaseType._qualified_class.endswith(".ReferenceType")
    sign = builder.symbol_table.resolve("M.T.Sign")
    assert [p.Name for p in sign.ClassParameter] == ["p1", "p2"]
    assert not getattr(sign, "ClassAttribute", None)


def test_cross_checked_forms_are_built():
    builder = build_from_text(
        _model(
            "  DOMAIN\n  DOMAIN D = 0 .. 10 CONSTRAINTS c1: THIS > 1, c2: THIS < 9;\n"
            "  DOMAIN T = TEXT;\n  T2 EXTENDS T = MANDATORY;\n"
            "  TOPIC T0 =\n    CLASS A =\n      x : TEXT;\n    END A;\n"
            "    CLASS B =\n      p : ATTRIBUTE;\n      MANDATORY CONSTRAINT cp: p == >>M.T0.A->x;\n    END B;\n"
            "    ASSOCIATION R =\n      a -- A;\n      b -- A;\n      w (FINAL) : TEXT*5;\n    END R;\n"
            "  END T0;"
        )
    )
    st = builder.symbol_table
    assert [c.Name for c in st.resolve("M.D").Constraint] == ["c1", "c2"]
    t2 = st.resolve("M.T2")
    assert t2._qualified_class.endswith(".TextType") and t2.Mandatory is True
    assert [a.Name for a in st.resolve("M.T0.R").ClassAttribute] == ["w"]
    cp = next(c for c in st.resolve("M.T0.B").Constraint if c.Name == "cp")
    assert cp.LogicalExpression.SubExpressions[1].Value._qualified_class.endswith(".AttributeConst")
