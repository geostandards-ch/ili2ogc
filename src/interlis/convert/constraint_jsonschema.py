"""Compile per-object `MANDATORY CONSTRAINT`s to JSON Schema 2020-12 fragments over a Feature's `properties`.

A constraint is violated only when its expression evaluates to FALSE. Evaluation runs left to right and stops as soon
as the value is known; reading an undefined attribute makes the whole constraint not computable, hence satisfied.
Each fragment encodes exactly that: an absent property fails the schema only when the constraint was already violated.
Anything outside the exactly translatable subset raises `UnsupportedExpressionError` rather than being approximated.
"""

from typing import Any

from interlis.builder.forward_refs import SymbolTable
from interlis.convert.constraint_eval import UnsupportedExpressionError, _try_number, describe_expression
from interlis.metamodel.instance import MetaInstance
from interlis.runtime.parse import unquote_interlis_string
from interlis.xtf.schema import inheritance_chain

Schema = Any  # a JSON Schema: a dict, or the boolean schemas True / False

MAX_FRAGMENT_CHARS = 60_000

_ORDERING = {"Less", "Greater", "LessOrEqual", "GreaterOrEqual"}
_FLIPPED = {
    "Equal": "Equal",
    "NotEqual": "NotEqual",
    "Less": "Greater",
    "Greater": "Less",
    "LessOrEqual": "GreaterOrEqual",
    "GreaterOrEqual": "LessOrEqual",
}
# operator -> (true-case keyword, false-case keyword); the false case is the complement over numbers.
_BOUNDS = {
    "Less": ("exclusiveMaximum", "minimum"),
    "Greater": ("exclusiveMinimum", "maximum"),
    "LessOrEqual": ("maximum", "exclusiveMinimum"),
    "GreaterOrEqual": ("minimum", "exclusiveMaximum"),
}


def _all_of(parts: list[Schema]) -> Schema:
    kept = [p for p in parts if p is not True]
    if any(p is False for p in kept):
        return False
    if not kept:
        return True
    return kept[0] if len(kept) == 1 else {"allOf": kept}


def _any_of(parts: list[Schema]) -> Schema:
    kept = [p for p in parts if p is not False]
    if any(p is True for p in kept):
        return True
    if not kept:
        return False
    return kept[0] if len(kept) == 1 else {"anyOf": kept}


def _not(schema: Schema) -> Schema:
    if schema is True:
        return False
    if schema is False:
        return True
    if isinstance(schema, dict) and set(schema) == {"not"}:
        return schema["not"]
    return {"not": schema}


def _strong(names: list[str], leaf: Schema) -> Schema:
    """Holds when every hop of the path is present (each intermediate hop an object) and the value matches `leaf`."""
    inner: Schema = leaf
    for index in range(len(names) - 1, -1, -1):
        node: dict[str, Any] = {"required": [names[index]], "properties": {names[index]: inner}}
        inner = {"type": "object", **node} if index > 0 else node
    return inner


def _weak(names: list[str], leaf: Schema) -> Schema:
    """Holds when the path is not fully present, or the value matches `leaf`."""
    inner: Schema = leaf
    for name in reversed(names):
        inner = {"properties": {name: inner}}
    return inner


class _Atom:
    """The three outcomes of a leaf test: `t` evaluates TRUE, `f` evaluates FALSE, `s` is "not FALSE"."""

    def __init__(self, t: Schema, f: Schema, s: Schema) -> None:
        self.t, self.f, self.s = t, f, s


def _path_names(expr: MetaInstance) -> list[str]:
    if getattr(expr, "Inspection", None):
        raise UnsupportedExpressionError("INSPECTION-based path factors are not supported")
    names = []
    for path_el in expr.PathEls:
        kind = getattr(path_el, "Kind", None)
        if kind not in ("ReferenceAttr", "Attribute"):
            raise UnsupportedExpressionError(f"path element kind {kind!r} needs object-graph context")
        if getattr(path_el, "NumIndex", None) is not None or getattr(path_el, "SpecIndex", None) is not None:
            raise UnsupportedExpressionError("indexed path elements ([FIRST]/[LAST]/[n]) are not supported")
        names.append(str(path_el.Ref))
    return names


def _is(expr: Any, suffix: str) -> bool:
    return isinstance(expr, MetaInstance) and expr._qualified_class.endswith(suffix)


def _operand(expr: Any) -> tuple[str, Any]:
    """Classify a relational operand as ("path", names), ("len", names), or ("const", python value)."""
    if _is(expr, "PathOrInspFactor"):
        return "path", _path_names(expr)
    if _is(expr, "FunctionCall"):
        if getattr(expr, "Function", None) != "INTERLIS.len":
            raise UnsupportedExpressionError(f"function call {getattr(expr, 'Function', None)!r} is not translatable")
        args = [getattr(a, "Expression", None) for a in getattr(expr, "Arguments", None) or []]
        if len(args) != 1 or not _is(args[0], "PathOrInspFactor"):
            raise UnsupportedExpressionError("INTERLIS.len is only translatable on a plain attribute path")
        return "len", _path_names(args[0])
    if _is(expr, "Constant"):
        return "const", _constant(expr)
    kind = getattr(expr, "_qualified_class", type(expr).__name__).rsplit(".", 1)[-1]
    raise UnsupportedExpressionError(f"operand {kind} is arithmetic or needs context beyond one object's properties")


_UNDEFINED = object()


def _constant(expr: MetaInstance) -> Any:
    type_, value = expr.Type, expr.Value
    if type_ == "Undefined":
        return _UNDEFINED
    if type_ == "Numeric":
        number = _try_number(value, value)
        if isinstance(number, str) or number != number or number in (float("inf"), float("-inf")):
            raise UnsupportedExpressionError(f"numeric constant {value!r} is not a finite number")
        return number
    if type_ == "Text":
        return unquote_interlis_string(value)
    if type_ == "Enumeration":
        return {"true": True, "false": False}.get(value, value)
    raise UnsupportedExpressionError(f"constant of type {type_!r} is not supported")


def _len_interval(op: str, n: int) -> tuple[int, int | None]:
    lows = {"Less": (1, n - 1), "LessOrEqual": (1, n), "Greater": (n + 1, None), "GreaterOrEqual": (n, None)}
    low, high = lows.get(op, (n, n))
    return max(low, 1), high


def _complement(interval: tuple[int, int | None]) -> list[tuple[int, int | None]]:
    low, high = interval
    if high is not None and high < low:
        return [(1, None)]
    pieces: list[tuple[int, int | None]] = [(1, low - 1)] if low > 1 else []
    if high is not None:
        pieces.append((high + 1, None))
    return pieces


def _len_schema(intervals: list[tuple[int, int | None]]) -> Schema:
    parts: list[Schema] = []
    for low, high in intervals:
        if high is not None and high < low:
            continue
        parts.append({"type": "string", **{"minLength": low}, **({"maxLength": high} if high is not None else {})})
    return _any_of(parts)


def _len_atom(names: list[str], op: str, n: Any) -> _Atom:
    if isinstance(n, bool) or not isinstance(n, int):
        raise UnsupportedExpressionError("INTERLIS.len is only translatable against an integer constant")
    interval = _len_interval(op, n)
    positive = [interval] if op != "NotEqual" else _complement(_len_interval("Equal", n))
    negative = _complement(interval) if op != "NotEqual" else [_len_interval("Equal", n)]
    f_leaf = _len_schema(negative)
    return _Atom(_strong(names, _len_schema(positive)), _strong(names, f_leaf), _weak(names, _not(f_leaf)))


def _compare_atom(names: list[str], op: str, value: Any) -> _Atom:
    if value is _UNDEFINED:
        if op not in ("Equal", "NotEqual"):
            raise UnsupportedExpressionError("UNDEFINED is only comparable with == and !=")
        present = _strong(names, True)
        absent = _not(present)
        return _Atom(*((absent, present, absent) if op == "Equal" else (present, absent, present)))
    if op in _ORDERING:
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            raise UnsupportedExpressionError(
                "ordering comparison needs a numeric constant (text and enumeration order)"
            )
        true_kw, false_kw = _BOUNDS[op]
        return _Atom(
            _strong(names, {"type": "number", true_kw: value}),
            _strong(names, {"type": "number", false_kw: value}),
            _weak(names, {true_kw: value}),
        )
    equal, other = {"const": value}, {"not": {"const": value}}
    t_leaf, f_leaf = (equal, other) if op == "Equal" else (other, equal)
    return _Atom(_strong(names, t_leaf), _strong(names, f_leaf), _weak(names, _not(f_leaf)))


def _relational_atom(expr: MetaInstance) -> _Atom:
    subs = expr.SubExpressions
    if len(subs) != 2:
        raise UnsupportedExpressionError(f"relational operator {expr.Operation!r} needs exactly 2 operands")
    (left_kind, left), (right_kind, right) = _operand(subs[0]), _operand(subs[1])
    op = expr.Operation
    if left_kind == "const" and right_kind != "const":
        left_kind, left, right_kind, right, op = right_kind, right, left_kind, left, _FLIPPED[op]
    if right_kind != "const" or left_kind == "const":
        raise UnsupportedExpressionError(
            "comparison of two attributes (or of two constants) cannot be expressed in JSON Schema",
        )
    if left_kind == "len":
        if right is _UNDEFINED:
            raise UnsupportedExpressionError("INTERLIS.len compared with UNDEFINED is not translatable")
        return _len_atom(left, op, right)
    return _compare_atom(left, op, right)


def _atom(expr: Any) -> _Atom | None:
    """Leaf test of `expr`, or `None` when `expr` is a logical connective."""
    if _is(expr, "CompoundExpr") and expr.Operation in _FLIPPED:
        return _relational_atom(expr)
    if _is(expr, "UnaryExpr") and expr.Operation == "Defined":
        sub = expr.SubExpression
        if not _is(sub, "PathOrInspFactor"):
            raise UnsupportedExpressionError("DEFINED(...) is only supported for a plain attribute path")
        present = _strong(_path_names(sub), True)
        return _Atom(present, _not(present), present)
    if _is(expr, "PathOrInspFactor"):
        names = _path_names(expr)
        return _Atom(
            _strong(names, {"const": True}), _strong(names, {"const": False}), _weak(names, _not({"const": False}))
        )
    if _is(expr, "Constant"):
        value = _constant(expr)
        if isinstance(value, bool):
            return _Atom(value, not value, value)
        raise UnsupportedExpressionError("a non-boolean constant cannot stand alone as a condition")
    return None


def _children(expr: MetaInstance, arity: int | None = None) -> list[MetaInstance]:
    subs = list(expr.SubExpressions or [])
    if arity is not None and len(subs) != arity:
        raise UnsupportedExpressionError(f"operator {expr.Operation!r} needs exactly {arity} operands")
    return subs


def _check_expression(expr: Any) -> None:
    if not isinstance(expr, MetaInstance):
        raise UnsupportedExpressionError("expression tree is not a single well-formed expression (arithmetic?)")


def _true(expr: Any) -> Schema:
    """Schema of "`expr` evaluates to TRUE"."""
    _check_expression(expr)
    atom = _atom(expr)
    if atom is not None:
        return atom.t
    if _is(expr, "UnaryExpr") and expr.Operation == "Not":
        return _false(expr.SubExpression)
    if _is(expr, "CompoundExpr"):
        op, subs = expr.Operation, _children(expr)
        if op == "And":
            return _all_of([_true(sub) for sub in subs])
        if op == "Or":
            return _any_of([_all_of([*(_false(p) for p in subs[:i]), _true(sub)]) for i, sub in enumerate(subs)])
        if op == "Implication":
            left, right = _children(expr, 2)
            return _any_of([_false(left), _all_of([_true(left), _true(right)])])
    raise _unsupported(expr)


def _false(expr: Any) -> Schema:
    """Schema of "`expr` evaluates to FALSE" (the constraint is violated)."""
    _check_expression(expr)
    atom = _atom(expr)
    if atom is not None:
        return atom.f
    if _is(expr, "UnaryExpr") and expr.Operation == "Not":
        return _true(expr.SubExpression)
    if _is(expr, "CompoundExpr"):
        op, subs = expr.Operation, _children(expr)
        if op == "And":
            return _any_of([_all_of([*(_true(p) for p in subs[:i]), _false(sub)]) for i, sub in enumerate(subs)])
        if op == "Or":
            return _all_of([_false(sub) for sub in subs])
        if op == "Implication":
            left, right = _children(expr, 2)
            return _all_of([_true(left), _false(right)])
    raise _unsupported(expr)


def _satisfied(expr: Any) -> Schema:
    """Schema of "`expr` does not evaluate to FALSE", written with `if`/`then` where it keeps the output readable."""
    _check_expression(expr)
    atom = _atom(expr)
    if atom is not None:
        return atom.s
    if _is(expr, "UnaryExpr") and expr.Operation == "Not":
        return _not(_true(expr.SubExpression))
    if _is(expr, "CompoundExpr"):
        op, subs = expr.Operation, _children(expr)
        if op == "And":
            parts = [_satisfied(subs[0])] if subs else []
            for index in range(1, len(subs)):
                then = _satisfied(subs[index])
                parts.append(
                    {"if": _all_of([_true(p) for p in subs[:index]]), "then": then} if then is not True else True
                )
            return _all_of(parts)
        if op == "Or":
            return _any_of([_satisfied(sub) for sub in subs])
        if op == "Implication":
            left, right = _children(expr, 2)
            then = _satisfied(right)
            return {"if": _true(left), "then": then} if then is not True else True
    raise _unsupported(expr)


def _unsupported(expr: MetaInstance) -> UnsupportedExpressionError:
    qualified = expr._qualified_class
    if qualified.endswith("CompoundExpr"):
        return UnsupportedExpressionError(f"operator {expr.Operation!r} needs numeric-domain context")
    if qualified.endswith("UnaryExpr"):
        return UnsupportedExpressionError(f"unary operator {expr.Operation!r} is not supported")
    return UnsupportedExpressionError(f"expression node {qualified} needs THIS/PARENT/aggregate/function context")


def _unsupported_reason(constraint: MetaInstance) -> str | None:
    """Why `constraint` is not a per-object `MANDATORY CONSTRAINT` (`None` when it is)."""
    qualified = constraint._qualified_class
    if not qualified.endswith("SimpleConstraint"):
        return f"{qualified.rsplit('.', 1)[-1]} is a population-level check, not expressible on one object"
    if getattr(constraint, "Kind", None) not in (None, "MandC"):
        return f"plausibility constraint (Kind={constraint.Kind!r}) needs statistical population context"
    if getattr(constraint, "Percentage", None) is not None:
        return "percentage-based plausibility constraint needs population context"
    if getattr(constraint, "LogicalExpression", None) is None:
        return "no LogicalExpression to compile"
    return None


def constraint_to_json_schema(constraint: MetaInstance) -> dict[str, Any]:
    """JSON Schema fragment that a Feature's `properties` satisfy exactly when the constraint is not violated.

    Raises `ValueError` when the constraint is not a per-object `MANDATORY CONSTRAINT`
    and `UnsupportedExpressionError` when its expression has no exact JSON Schema equivalent.
    """
    reason = _unsupported_reason(constraint)
    if reason is not None:
        raise ValueError(f"cannot compile constraint to JSON Schema: {reason}")
    schema = _satisfied(constraint.LogicalExpression)
    if schema is True:
        return {}
    if schema is False:
        return {"not": {}}
    if len(repr(schema)) > MAX_FRAGMENT_CHARS:
        raise UnsupportedExpressionError("the exact schema is too large (deeply nested logic over many attributes)")
    return dict(schema)


def _constraint_label(constraint: MetaInstance) -> str:
    name = getattr(constraint, "Name", None)
    if name:
        return str(name)
    expr = getattr(constraint, "LogicalExpression", None)
    if isinstance(expr, MetaInstance):
        try:
            return describe_expression(expr)
        except (AttributeError, IndexError, TypeError):
            pass
    return constraint._qualified_class.rsplit(".", 1)[-1]


def _read_properties(node: Any) -> set[str]:
    """Every top-level property name a fragment reads (`properties` keys and `required` entries)."""
    found: set[str] = set()
    if isinstance(node, dict):
        for key, value in node.items():
            if key == "properties" and isinstance(value, dict):
                found |= set(value)
            elif key == "required" and isinstance(value, list):
                found |= {v for v in value if isinstance(v, str)}
            found |= _read_properties(value)
    elif isinstance(node, list):
        for item in node:
            found |= _read_properties(item)
    return found


def class_constraint_schemas(
    cls: MetaInstance, symbol_table: SymbolTable | None = None, outside: frozenset[str] = frozenset()
) -> tuple[list[dict[str, Any]], list[tuple[str, str]]]:
    """Fragments for the `allOf` of a class schema, plus `(label, reason)` for each constraint left out.

    Covers the class's own constraints and those inherited through `EXTENDS`. `outside` names the attributes a Feature
    does not hold in `properties` (its geometries, in `place`): a constraint that reads one is left out, since the
    fragment would see it as undefined. `symbol_table` is not needed by the compilation itself.
    """
    del symbol_table
    fragments: list[dict[str, Any]] = []
    skipped: list[tuple[str, str]] = []
    for level in inheritance_chain(cls):
        for constraint in getattr(level, "Constraint", None) or []:
            label = _constraint_label(constraint)
            reason = _unsupported_reason(constraint)
            if reason is None:
                try:
                    fragment = constraint_to_json_schema(constraint)
                except UnsupportedExpressionError as exc:
                    reason = str(exc)
            if reason is None and outside & _read_properties(fragment):
                reason = "reads a geometry attribute, which a Feature holds in `place`, not in `properties`"
            if reason is not None:
                skipped.append((label, reason))
            elif fragment:
                fragments.append({"description": f"INTERLIS constraint: {label}", **fragment})
    return fragments, skipped


__all__ = [
    "UnsupportedExpressionError",
    "class_constraint_schemas",
    "constraint_to_json_schema",
]
