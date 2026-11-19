"""`CONSTRAINT` -> JSON Schema fragments (`convert/constraint_jsonschema.py`), checked against the reference evaluator.

The property test builds each constraint-bearing fixture model, draws random `properties` (absent values included) and
requires the JSON Schema verdict to equal the verdict of `constraint_eval` (the reference for undefined values), or of
a small local reference where the evaluator has no support (`INTERLIS.len`, `== UNDEFINED`).
"""

import random
from pathlib import Path
from typing import Any

import jsonschema
import pytest
from conftest import build_from_file, build_from_text

from interlis.convert.constraint_eval import (
    UndefinedValueError,
    UnsupportedExpressionError,
    _as_bool,
    _compare,
    _resolve_path,
    evaluate_expression,
)
from interlis.convert.constraint_jsonschema import class_constraint_schemas, constraint_to_json_schema
from interlis.metamodel.instance import MetaInstance
from interlis.runtime.parse import unquote_interlis_string

FIXTURES = Path(__file__).parent / "fixtures"
MODELS = [
    "dmav/DMAV_Grundstuecke_V1_1.ili",
    "dmav/DMAV_Bodenbedeckung_V1_1.ili",
    "mgdm/Laermempfindlichkeitsstufen_V1_2.ili",
    "mgdm/Waldreservate_V2_0.ili",
    "mgdm/Wildruhezonen_V2_1.ili",
    "fgdm4gs/IVS_V3.ili",
    "xtf/xtf24allerrors/AllErrors24.ili",
]

_SOURCE = """INTERLIS 2.3;
MODEL Test AT "http://x" VERSION "1" =
  TOPIC T =
    STRUCTURE S =
      Sub: TEXT*10;
    END S;
    CLASS A =
      Flag: BOOLEAN;
      Name: TEXT*20;
      Code: TEXT*20;
      Anz: 0 .. 9999;
      Nested: S;
      {constraints}
    END A;
  END T;
END Test.
"""


def _class(*constraints: str) -> MetaInstance:
    src = _SOURCE.format(constraints="\n      ".join(f"MANDATORY CONSTRAINT {c};" for c in constraints))
    return build_from_text(src).symbol_table.resolve("Test.T.A")


def _schema(constraint_src: str) -> dict[str, Any]:
    return constraint_to_json_schema(_class(constraint_src).Constraint[0])


def _valid(schema: dict[str, Any], properties: dict[str, Any]) -> bool:
    return jsonschema.Draft202012Validator(schema).is_valid(properties)


# --- shape and exact semantics on hand-written cases ---


def test_atom_with_absent_property_is_satisfied():
    schema = _schema("Anz >= 10")
    assert schema == {"properties": {"Anz": {"minimum": 10}}}
    assert _valid(schema, {}) and _valid(schema, {"Anz": 10}) and not _valid(schema, {"Anz": 9})


def test_equality_and_boolean_constant():
    schema = _schema("Flag == #false")
    assert schema == {"properties": {"Flag": {"const": False}}}
    assert _valid(schema, {}) and _valid(schema, {"Flag": False}) and not _valid(schema, {"Flag": True})


def test_constant_on_the_left_is_flipped():
    assert _schema("3 < Anz") == {"properties": {"Anz": {"exclusiveMinimum": 3}}}


def test_implication_is_if_then():
    schema = _schema("Anz >= 10 => Flag == #false")
    assert "if" in schema and "then" in schema
    assert _valid(schema, {"Anz": 5, "Flag": True})
    assert not _valid(schema, {"Anz": 10, "Flag": True})
    assert _valid(schema, {"Anz": 10})  # consequent undefined: not computable, so satisfied
    assert _valid(schema, {"Flag": True})  # antecedent undefined


def test_defined_requires_the_property_and_nothing_else():
    schema = _schema("DEFINED (Code)")
    assert not _valid(schema, {}) and _valid(schema, {"Code": "x"})


def test_defined_inside_implication_is_the_documented_pattern():
    schema = _schema("Flag == #true => DEFINED (Code)")
    assert not _valid(schema, {"Flag": True})
    assert _valid(schema, {"Flag": True, "Code": "a"}) and _valid(schema, {"Flag": False}) and _valid(schema, {})


def test_and_stops_at_the_first_false_term_even_if_a_later_term_is_undefined():
    schema = _schema("Anz > 5 AND Flag == #true")
    assert not _valid(schema, {"Anz": 1})  # first term FALSE: violated, the undefined Flag is never read
    assert _valid(schema, {"Anz": 9})  # second term undefined: not computable
    assert _valid(schema, {"Flag": False})  # first term undefined: not computable
    assert not _valid(schema, {"Anz": 9, "Flag": False})


def test_or_stops_at_the_first_true_term():
    schema = _schema("Anz > 5 OR Flag == #true")
    assert _valid(schema, {"Anz": 9})
    assert not _valid(schema, {"Anz": 1, "Flag": False})
    assert _valid(schema, {"Anz": 1})  # second term undefined
    assert _valid(schema, {"Flag": False})  # first term undefined


def test_not_of_undefined_stays_undefined():
    schema = _schema("NOT (Anz > 5)")
    assert _valid(schema, {}) and _valid(schema, {"Anz": 1}) and not _valid(schema, {"Anz": 9})


def test_nested_structure_path():
    schema = _schema('Nested->Sub == "x"')
    assert _valid(schema, {}) and _valid(schema, {"Nested": {}}) and _valid(schema, {"Nested": {"Sub": "x"}})
    assert not _valid(schema, {"Nested": {"Sub": "y"}})
    assert _valid(schema, {"Nested": "not an object"})


def test_len_maps_to_string_length_and_empty_text_is_undefined():
    schema = _schema("INTERLIS.len (Name) <= 5")
    assert _valid(schema, {}) and _valid(schema, {"Name": "abcde"}) and not _valid(schema, {"Name": "abcdef"})
    equal = _schema("INTERLIS.len (Name) == 3")
    assert _valid(equal, {"Name": "abc"}) and not _valid(equal, {"Name": "ab"}) and not _valid(equal, {"Name": "abcd"})
    assert _valid(equal, {"Name": ""})  # a zero-length text is undefined
    assert not _valid(_schema("INTERLIS.len (Name) != 3"), {"Name": "abc"})


def test_comparison_with_undefined_is_the_negation_of_defined():
    assert _valid(_schema("Code == UNDEFINED"), {}) and not _valid(_schema("Code == UNDEFINED"), {"Code": "a"})
    assert _valid(_schema("Code != UNDEFINED"), {"Code": "a"}) and not _valid(_schema("Code != UNDEFINED"), {})


@pytest.mark.parametrize(
    ("source", "fragment"),
    [
        ("Anz == Flag", "two attributes"),
        ('Name > "a"', "numeric constant"),
        ("Anz + 1 > 3", "arithmetic"),
        ("INTERLIS.elementCount (Nested) > 0", "not translatable"),
        ("INTERLIS.len (Name) == Anz", "two attributes"),
    ],
)
def test_untranslatable_expressions_are_refused_with_a_reason(source, fragment):
    with pytest.raises(UnsupportedExpressionError, match=fragment):
        constraint_to_json_schema(_class(source).Constraint[0])


def test_class_constraint_schemas_reports_skipped_constraints():
    cls = _class("Anz >= 10", "Anz == Flag", "DEFINED (Code)")
    fragments, skipped = class_constraint_schemas(cls, None)
    assert len(fragments) == 2
    assert fragments[0]["properties"] == {"Anz": {"minimum": 10}}
    assert [label for label, _ in skipped] == ["Anz == Flag"]
    assert "two attributes" in skipped[0][1]


def test_non_simple_constraints_are_reported_as_skipped():
    src = _SOURCE.format(constraints="UNIQUE Name; SET CONSTRAINT WHERE Anz > 1: INTERLIS.objectCount (ALL) > 0;")
    cls = build_from_text(src).symbol_table.resolve("Test.T.A")
    fragments, skipped = class_constraint_schemas(cls, None)
    assert fragments == [] and len(skipped) == 2
    assert all("population-level" in reason for _, reason in skipped)


# --- reference evaluator (semantics of refman 3.12 / 3.13, extended with len and UNDEFINED) ---


def _reference_value(expr: MetaInstance, props: dict[str, Any]) -> Any:
    """`evaluate_expression`, plus `INTERLIS.len` and comparisons with `UNDEFINED`."""
    kind = expr._qualified_class.rsplit(".", 1)[-1]
    if kind == "FunctionCall":
        value = _reference_value(expr.Arguments[0].Expression, props)
        if not isinstance(value, str):
            raise UnsupportedExpressionError("len of a non-text")
        if value == "":
            raise UndefinedValueError("zero-length text is undefined")
        return len(value)
    if kind == "CompoundExpr":
        subs = expr.SubExpressions
        if expr.Operation in ("Equal", "NotEqual") and any(getattr(s, "Type", None) == "Undefined" for s in subs):
            operand = next(s for s in subs if getattr(s, "Type", None) != "Undefined")
            found, _ = _resolve_path(operand.PathEls, props)
            return found == (expr.Operation == "NotEqual")
        if expr.Operation == "And":
            return all(_as_bool(_reference_value(s, props)) for s in subs)
        if expr.Operation == "Or":
            return any(_as_bool(_reference_value(s, props)) for s in subs)
        if expr.Operation == "Implication":
            return (not _as_bool(_reference_value(subs[0], props))) or _as_bool(_reference_value(subs[1], props))
        return _compare(expr.Operation, _reference_value(subs[0], props), _reference_value(subs[1], props))
    if kind == "UnaryExpr" and expr.Operation == "Not":
        return not _as_bool(_reference_value(expr.SubExpression, props))
    return evaluate_expression(expr, props)


def _reference_satisfied(constraint: MetaInstance, props: dict[str, Any]) -> bool:
    try:
        return _as_bool(_reference_value(constraint.LogicalExpression, props))
    except UnsupportedExpressionError:
        return True


def _evaluator_satisfied(constraint: MetaInstance, props: dict[str, Any]) -> bool:
    try:
        return _as_bool(evaluate_expression(constraint.LogicalExpression, props))
    except UnsupportedExpressionError:
        return True


# --- random instances ---


def _walk(expr: Any, paths: dict[tuple[str, ...], list[Any]]) -> None:
    if not isinstance(expr, MetaInstance):
        return
    kind = expr._qualified_class.rsplit(".", 1)[-1]
    if kind == "PathOrInspFactor":
        paths.setdefault(tuple(str(el.Ref) for el in expr.PathEls), [])
    for child in [*(getattr(expr, "SubExpressions", None) or []), getattr(expr, "SubExpression", None)]:
        _walk(child, paths)
    for arg in getattr(expr, "Arguments", None) or []:
        _walk(getattr(arg, "Expression", None), paths)


def _typed_pool(expr: Any, paths: dict[tuple[str, ...], list[Any]]) -> None:
    """Fill `paths` with candidate values per path, typed by the constants they are compared with."""
    if not isinstance(expr, MetaInstance):
        return
    kind = expr._qualified_class.rsplit(".", 1)[-1]
    if kind == "CompoundExpr" and len(expr.SubExpressions) == 2:
        subs = expr.SubExpressions
        for operand, other in (subs, subs[::-1]):
            names = None
            is_len = operand._qualified_class.endswith("FunctionCall")
            target = operand.Arguments[0].Expression if is_len else operand
            if isinstance(target, MetaInstance) and target._qualified_class.endswith("PathOrInspFactor"):
                names = tuple(str(el.Ref) for el in target.PathEls)
            if names is None or getattr(other, "Type", None) is None:
                continue
            pool = paths.setdefault(names, [])
            if is_len:
                pool += ["", "a", "ab", "abc", "abcd", "abcde", "abcdef"]
            elif other.Type == "Numeric":
                number = float(other.Value)
                pool += [number - 1, number, number + 1, int(number), number + 0.5, "abc"]
            elif other.Type == "Text":
                pool += [unquote_interlis_string(other.Value), "zz", ""]
            elif other.Type == "Enumeration":
                pool += [True, False] if other.Value in ("true", "false") else [other.Value, "other"]
    if kind == "PathOrInspFactor":
        paths.setdefault(tuple(str(el.Ref) for el in expr.PathEls), [])
    for child in [*(getattr(expr, "SubExpressions", None) or []), getattr(expr, "SubExpression", None)]:
        _typed_pool(child, paths)
    for arg in getattr(expr, "Arguments", None) or []:
        _typed_pool(getattr(arg, "Expression", None), paths)


def _random_properties(rng: random.Random, paths: dict[tuple[str, ...], list[Any]]) -> dict[str, Any]:
    props: dict[str, Any] = {}
    for names, pool in paths.items():
        values = pool or [True, False]  # a bare boolean operand or a DEFINED-only path
        if rng.random() < 0.3:
            continue
        node = props
        for hop in names[:-1]:
            choice = rng.random()
            if choice < 0.1:
                break
            if choice < 0.15:
                node[hop] = "not an object"
                break
            child = node.setdefault(hop, {})
            if not isinstance(child, dict):
                break
            node = child
        else:
            node[names[-1]] = rng.choice(values)
    return props


def _model_constraints(path: str) -> list[tuple[MetaInstance, MetaInstance]]:
    builder = build_from_file(FIXTURES / path)
    found = []
    for instance in builder.symbol_table.all_registered():
        if isinstance(instance, MetaInstance) and instance._qualified_class.rsplit(".", 1)[-1] in (
            "Class",
            "Structure",
        ):
            found += [(instance, c) for c in getattr(instance, "Constraint", None) or []]
    return found


def _compile(constraint: MetaInstance) -> dict[str, Any] | None:
    try:
        return constraint_to_json_schema(constraint)
    except (UnsupportedExpressionError, ValueError):
        return None


@pytest.mark.parametrize("model", MODELS)
def test_schema_verdict_equals_the_reference_evaluator_on_random_instances(model):
    rng = random.Random(20261119)
    for _cls, constraint in _model_constraints(model):
        schema = _compile(constraint)
        if schema is None:
            continue
        validator = jsonschema.Draft202012Validator(schema)
        paths: dict[tuple[str, ...], list[Any]] = {}
        _typed_pool(constraint.LogicalExpression, paths)
        for _ in range(200):
            props = _random_properties(rng, paths)
            expected = _reference_satisfied(constraint, props)
            assert validator.is_valid(props) == expected, (model, schema, props)


def test_most_per_object_constraints_of_the_fixtures_compile():
    compiled = sum(_compile(c) is not None for model in MODELS for _cls, c in _model_constraints(model))
    assert compiled >= 18


def test_reference_agrees_with_constraint_eval_where_both_apply():
    rng = random.Random(7)
    cls = _class(
        'NOT (Flag == #false AND Name != "a") OR DEFINED (Code)',
        'Anz > 3 => Nested->Sub == "x"',
        "Anz <= 10 OR Flag",
    )
    for constraint in cls.Constraint:
        paths: dict[tuple[str, ...], list[Any]] = {}
        _typed_pool(constraint.LogicalExpression, paths)
        for _ in range(200):
            props = _random_properties(rng, paths)
            assert _reference_satisfied(constraint, props) == _evaluator_satisfied(constraint, props)
            assert _valid(constraint_to_json_schema(constraint), props) == _evaluator_satisfied(constraint, props)


def test_len_and_undefined_constraints_match_the_local_reference():
    rng = random.Random(11)
    cls = _class(
        "INTERLIS.len (Name) >= 2",
        "INTERLIS.len (Name) < 3 OR Anz == 1",
        "INTERLIS.len (Name) != 2 => Code != UNDEFINED",
        "3 >= INTERLIS.len (Code)",
    )
    for constraint in cls.Constraint:
        schema = constraint_to_json_schema(constraint)
        paths: dict[tuple[str, ...], list[Any]] = {}
        _typed_pool(constraint.LogicalExpression, paths)
        paths.setdefault(("Code",), [])
        paths[("Code",)] += ["", "a", "abcd"]
        for _ in range(300):
            props = _random_properties(rng, paths)
            assert _valid(schema, props) == _reference_satisfied(constraint, props), (schema, props)
