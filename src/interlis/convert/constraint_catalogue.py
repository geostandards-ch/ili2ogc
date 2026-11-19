"""The constraints of a class, each in the strongest form a JSON document can carry.

`schema`: exact JSON Schema fragment in the class's `allOf`. `cql2`: not expressible on one object's properties but
exact as a CQL2 filter. `declared`: stated as data (kind, expression, attributes) for a validator that sees the whole
transfer, because it concerns a population, a reference to another object or an operation no filter has.
"""

from __future__ import annotations

from typing import Any

from interlis.convert.constraint_eval import UnsupportedExpressionError, describe_expression
from interlis.convert.constraint_jsonschema import (
    _constraint_label,
    _read_properties,
    _unsupported_reason,
    constraint_to_json_schema,
)
from interlis.convert.cql2 import constraint_to_cql2, cql2_unsupported_reason
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.schema import (
    _class_related_base_class,
    association_embedding,
    inheritance_chain,
    is_class_compatible,
)

_KINDS = {
    "UniqueConstraint": "unique",
    "ExistenceConstraint": "existence",
    "SetConstraint": "set",
}
_PLAUSIBILITY = {"HighPercC": ">=", "LowPercC": "<="}


def class_constraints(
    cls: MetaInstance, outside: frozenset[str] = frozenset(), symbol_table: Any = None
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """`(allOf fragments, catalogue)` of a class: one catalogue entry per constraint, own and inherited.

    `outside` names the attributes a Feature holds outside `properties` (its geometries): a constraint reading one has
    no schema fragment. With a `symbol_table`, the role cardinalities a payload cannot carry are added as declarations.
    """
    fragments: list[dict[str, Any]] = []
    catalogue: list[dict[str, Any]] = []
    for level in inheritance_chain(cls):
        for constraint in getattr(level, "Constraint", None) or []:
            label = _constraint_label(constraint)
            entry: dict[str, Any] = {"name": label, "kind": _kind(constraint)}
            fragment, reason = _compile(constraint, outside)
            if fragment is not None:
                entry["enforcement"] = "schema"
                if fragment:
                    fragments.append({"description": f"INTERLIS constraint: {label}", **fragment})
            else:
                entry["reason"] = reason
                entry.update(_alternative(constraint))
            catalogue.append(entry)
    if symbol_table is not None:
        catalogue.extend(_cardinalities(cls, symbol_table))
    return fragments, catalogue


def _bounds(role: MetaInstance) -> tuple[str, str]:
    """`(min, max)` of a role; no clause means `{0..*}` (`{0..1}` for a composition)."""
    mult = getattr(role, "Multiplicity", None)
    if not isinstance(mult, MetaInstance):
        return ("0", "1") if getattr(role, "Strongness", None) == "Comp" else ("0", "*")
    lower = str(getattr(mult, "Min", None) or "0")
    upper = getattr(mult, "Max", None)
    return lower, lower if upper == "same_as_Min" else str(upper or "*")


def _cardinalities(cls: MetaInstance, symbol_table: Any) -> list[dict[str, Any]]:
    """Role cardinalities that count the objects related to an instance of `cls`: a population check.

    The end the reference points to is already in the schema (`required`, single value); the count of related objects
    on the other end is not, and no JSON Schema keyword says it.
    """
    entries: list[dict[str, Any]] = []
    for assoc in symbol_table.all_registered():
        if not isinstance(assoc, MetaInstance) or getattr(assoc, "Kind", None) != "Association":
            continue
        roles = [r for r in (getattr(assoc, "Role", None) or []) if isinstance(r, MetaInstance)]
        if len(roles) != 2:
            continue
        embedding = association_embedding(assoc)
        embedded = embedding[1] if embedding else None
        for here, other in (roles, roles[::-1]):
            target = _class_related_base_class(here)
            if target is None or other is embedded or not is_class_compatible(cls, target):
                continue
            lower, upper = _bounds(other)
            if (lower, upper) == ("0", "*"):
                continue
            entries.append(
                {
                    "name": f"{getattr(assoc, 'Name', None)}.{getattr(other, 'Name', None)}",
                    "kind": "cardinality",
                    "reason": "counts the objects related to one object: a population-level check",
                    "enforcement": "declared",
                    "role": getattr(other, "Name", None),
                    "of": getattr(_class_related_base_class(other), "Name", None),
                    "min": lower,
                    "max": upper,
                }
            )
    return entries


def _kind(constraint: MetaInstance) -> str:
    name = constraint._qualified_class.rsplit(".", 1)[-1]
    if name in _KINDS:
        return _KINDS[name]
    return "plausibility" if getattr(constraint, "Kind", None) in _PLAUSIBILITY else "mandatory"


def _compile(constraint: MetaInstance, outside: frozenset[str]) -> tuple[dict[str, Any] | None, str]:
    reason = _unsupported_reason(constraint)
    if reason is not None:
        return None, reason
    try:
        fragment = constraint_to_json_schema(constraint)
    except UnsupportedExpressionError as exc:
        return None, str(exc)
    if outside & _read_properties(fragment):
        return None, "reads a geometry attribute, which a Feature holds in `place`, not in `properties`"
    return fragment, ""


def _alternative(constraint: MetaInstance) -> dict[str, Any]:
    """The CQL2 filter of a constraint when it has one, else its declaration."""
    if cql2_unsupported_reason(constraint) is None:
        try:
            return {"enforcement": "cql2", "cql2": constraint_to_cql2(constraint)}
        except (UnsupportedExpressionError, AttributeError, TypeError):  # the builder reads some arithmetic as a list
            pass
    return {"enforcement": "declared", **_declaration(constraint)}


def _text(expr: Any) -> str | None:
    if not isinstance(expr, MetaInstance):
        return None
    try:
        text = describe_expression(expr)
    except (AttributeError, IndexError, TypeError):
        return None
    return None if "<expression>" in text else text  # a function call the description cannot render


def _declaration(constraint: MetaInstance) -> dict[str, Any]:
    kind = _kind(constraint)
    declared: dict[str, Any] = {}
    if kind == "unique":
        declared["attributes"] = [t for t in map(_text, getattr(constraint, "UniqueDef", None) or []) if t]
        declared["scope"] = "local" if getattr(constraint, "Kind", None) == "LocalU" else "global"
        where = _text(getattr(constraint, "Where", None))
        if where:
            declared["where"] = where
    elif kind == "existence":
        attribute = _text(getattr(constraint, "Attr", None))
        if attribute:
            declared["attribute"] = attribute
    elif kind == "set":
        for key, attr in (("where", "Where"), ("expression", "Constraint")):
            text = _text(getattr(constraint, attr, None))
            if text:
                declared[key] = text
    else:
        expressions = getattr(constraint, "LogicalExpression", None)
        texts = [t for t in map(_text, expressions if isinstance(expressions, list) else [expressions]) if t]
        if texts:
            declared["expression"] = " ".join(texts)
        percentage = getattr(constraint, "Percentage", None)
        if percentage:
            declared["percentage"] = {"operator": _PLAUSIBILITY.get(constraint.Kind), "value": percentage["Value"]}
    return declared
