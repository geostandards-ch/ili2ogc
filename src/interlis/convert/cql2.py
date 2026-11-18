"""Compile a `CONSTRAINT`'s expression tree to a CQL2-JSON filter.

Handles the same subset as `constraint_eval.py`; anything else raises `UnsupportedExpressionError`
rather than producing a partial filter.
"""

from typing import Any

from interlis.convert.constraint_eval import (
    UnsupportedExpressionError,
    _evaluate_constant,
)
from interlis.metamodel.instance import MetaInstance

_RELATIONAL_OPS = {
    "Equal": "=",
    "NotEqual": "<>",
    "Less": "<",
    "Greater": ">",
    "LessOrEqual": "<=",
    "GreaterOrEqual": ">=",
}


def _describe_path(path_els: list[MetaInstance]) -> str:
    return "->".join(str(getattr(el, "Ref", None) or "?") for el in path_els)


def _path_property_name(path_els: list[MetaInstance]) -> str:
    """Dotted CQL2 `property` name for a `PathEls` list.

    Static check mirroring `constraint_eval._resolve_path`: each hop must be a plain attribute/reference with no index.
    """
    names = []
    for path_el in path_els:
        kind = getattr(path_el, "Kind", None)
        if kind not in ("ReferenceAttr", "Attribute"):
            raise UnsupportedExpressionError(
                f"path element kind {kind!r} needs object-graph context beyond a single Feature's properties",
            )
        if getattr(path_el, "NumIndex", None) is not None or getattr(path_el, "SpecIndex", None) is not None:
            raise UnsupportedExpressionError("indexed path elements ([FIRST]/[LAST]/[n]) are not supported")
        names.append(str(getattr(path_el, "Ref", None)))
    return ".".join(names)


def to_cql2(expr: MetaInstance) -> Any:
    """Compile one `Expression` node to its CQL2-JSON value (a `dict` predicate/property, or a bare JSON literal).

    Raises `UnsupportedExpressionError` for anything `constraint_eval.evaluate_expression` cannot evaluate.
    """
    qualified = expr._qualified_class
    if qualified.endswith("CompoundExpr"):
        return _compile_compound(expr)
    if qualified.endswith("UnaryExpr"):
        return _compile_unary(expr)
    if qualified.endswith("PathOrInspFactor"):
        return _compile_path_factor(expr)
    if qualified.endswith("Constant"):
        return _compile_constant(expr)
    raise UnsupportedExpressionError(
        f"expression node {qualified} needs THIS/PARENT/aggregate/function-call context "
        f"beyond a single Feature's properties",
    )


def _compile_compound(expr: MetaInstance) -> dict[str, Any]:
    op = expr.Operation
    subs = expr.SubExpressions
    if op in _RELATIONAL_OPS:
        if len(subs) != 2:
            raise UnsupportedExpressionError(f"relational operator {op!r} needs exactly 2 operands, got {len(subs)}")
        return {"op": _RELATIONAL_OPS[op], "args": [to_cql2(subs[0]), to_cql2(subs[1])]}
    if op == "And":
        return {"op": "and", "args": [to_cql2(sub) for sub in subs]}
    if op == "Or":
        return {"op": "or", "args": [to_cql2(sub) for sub in subs]}
    if op == "Implication":
        # CQL2 has no direct implication operator - `A => B` == `NOT A OR B`.
        if len(subs) != 2:
            raise UnsupportedExpressionError("implication needs exactly 2 operands")
        return {"op": "or", "args": [{"op": "not", "args": [to_cql2(subs[0])]}, to_cql2(subs[1])]}
    raise UnsupportedExpressionError(
        f"operator {op!r} needs numeric-domain context beyond boolean constraint evaluation",
    )


def _compile_unary(expr: MetaInstance) -> dict[str, Any]:
    op = expr.Operation
    if op == "Not":
        return {"op": "not", "args": [to_cql2(expr.SubExpression)]}
    if op == "Defined":
        sub = expr.SubExpression
        if sub is None or not sub._qualified_class.endswith("PathOrInspFactor"):
            raise UnsupportedExpressionError("DEFINED(...) is only supported for a plain attribute path")
        is_null = {"op": "isNull", "args": [_compile_path_factor(sub)]}
        return {"op": "not", "args": [is_null]}
    raise UnsupportedExpressionError(f"unary operator {op!r} is not supported")


def _compile_path_factor(expr: MetaInstance) -> dict[str, Any]:
    if getattr(expr, "Inspection", None):
        raise UnsupportedExpressionError("INSPECTION-based path factors are not supported")
    return {"property": _path_property_name(expr.PathEls)}


def _compile_constant(expr: MetaInstance) -> Any:
    """`_evaluate_constant`'s value, with `#true`/`#false` coerced to a real JSON boolean.

    Mirrors `constraint_eval._as_bool`, which treats those enum literals as booleans.
    """
    value = _evaluate_constant(expr)
    if value == "true":
        return True
    if value == "false":
        return False
    return value


def cql2_unsupported_reason(constraint: MetaInstance) -> str | None:
    """Why `constraint_to_cql2(constraint)` would raise, or `None` if it is in scope.

    Only a plain `SimpleConstraint` (`Kind` `None`/`"MandC"`, no `Percentage`) is a per-Feature filter.
    """
    qualified = constraint._qualified_class
    if not qualified.endswith("SimpleConstraint"):
        kind = qualified.rsplit(".", 1)[-1]
        return f"{kind} is a population/basket-level check, not a per-Feature CQL2 filter"
    if getattr(constraint, "Kind", None) not in (None, "MandC"):
        return (
            f"plausibility constraint (Kind={constraint.Kind!r}) needs statistical population "
            "context, not a boolean filter"
        )
    if getattr(constraint, "Percentage", None) is not None:
        return "percentage-based plausibility constraint needs population context, not a boolean filter"
    if getattr(constraint, "LogicalExpression", None) is None:
        return "no LogicalExpression to compile"
    return None


def constraint_to_cql2(constraint: MetaInstance) -> dict[str, Any]:
    """CQL2-JSON filter for one per-Feature `SimpleConstraint`'s `LogicalExpression`.

    Raises `ValueError` if `cql2_unsupported_reason` is not `None` (callers check it first) and
    `UnsupportedExpressionError` for any expression outside the evaluated subset.
    """
    reason = cql2_unsupported_reason(constraint)
    if reason is not None:
        raise ValueError(f"cannot compile constraint to CQL2: {reason}")
    return to_cql2(constraint.LogicalExpression)


__all__ = [
    "UnsupportedExpressionError",
    "constraint_to_cql2",
    "cql2_unsupported_reason",
    "to_cql2",
]
