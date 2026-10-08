"""Constraint checks of an XTF transfer (eCH-0031 section 3.12).

Evaluated: `MANDATORY CONSTRAINT` on each object (references are followed
to the target object), plausibility constraints (`CONSTRAINT >= 95% ...`)
over all objects of the class, and `UNIQUE` - over the whole transfer,
per basket with `(BASKET)`, per object with `(LOCAL)`, optionally
restricted by `WHERE`. Constraints of a base class apply to the objects of
its extensions.

A constraint that cannot be computed because a value is undefined counts
as satisfied (manual, section 3.12); an object with an undefined `UNIQUE`
key takes no part in the check. `EXISTENCE` constraints are checked against
the objects of the transfer and of the given catalogues (when none of the
required class is present, the constraint is reported as not evaluated).
`SET` constraints, and any expression outside the evaluator's subset, are
reported once per class as `info` issues rather than skipped silently.

Uniqueness is checked against the objects present in the transfer only: the
manual notes that such constraints are conceptually global and not always
fully checkable. A `UNIQUE (LOCAL)` declared on a STRUCTURE is not checked
(structure elements are not objects of the transfer).
"""

from dataclasses import dataclass, field
from typing import Any

from interlis.builder.forward_refs import SymbolTable
from interlis.builder.repository import ModelRepository
from interlis.convert.constraint_eval import (
    UndefinedValueError,
    UnsupportedExpressionError,
    _as_bool,
    _try_number,
    describe_expression,
    evaluate_expression,
)
from interlis.convert.jsonfg import _raw_node_value
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.parse import RawNode, XtfBasket, XtfObject, XtfTransfer
from interlis.xtf.schema import inheritance_chain, resolve_class
from interlis.xtf.validate import ValidationIssue, _build_tid_index, _extract_reference, _group_by_tag

_UNDEFINED = object()
_ATTRIBUTE_KINDS = ("ReferenceAttr", "Attribute")


class _ObjectScope(dict):
    """One object's attributes as the expression evaluator reads them, resolved on demand.

    A reference attribute yields the target object's own scope (so a path
    can navigate `->`), and the scope compares `==` to its OID.
    """

    def __init__(self, obj: XtfObject, by_tid: dict[str, XtfObject]) -> None:
        super().__init__()
        self._obj = obj
        self._by_tid = by_tid

    def __contains__(self, name: object) -> bool:
        return bool(self._obj.attributes.get(name))

    def __getitem__(self, name: str) -> Any:
        node = self._obj.attributes[name][0]
        target = _extract_reference(node)
        if target is not None and target in self._by_tid:
            return _ObjectScope(self._by_tid[target], self._by_tid)
        return _raw_node_value(node)

    def __eq__(self, other: object) -> bool:
        return self._obj.tid == (other._obj.tid if isinstance(other, _ObjectScope) else other)

    def __ne__(self, other: object) -> bool:
        return not self.__eq__(other)

    __hash__ = None  # type: ignore[assignment]


@dataclass
class _Plausibility:
    kind: str
    percentage: float
    description: str
    qualified_class: str
    satisfied: int = 0
    evaluated: int = 0


@dataclass
class _Report:
    issues: list[ValidationIssue] = field(default_factory=list)
    not_evaluated: dict[tuple[str, str, str], ValidationIssue] = field(default_factory=dict)
    skipped: set[int] = field(default_factory=set)

    def skip(self, constraint: MetaInstance, qualified_class: str, label: str, reason: str) -> None:
        self.skipped.add(id(constraint))
        key = (qualified_class, label, reason)
        if key not in self.not_evaluated:
            self.not_evaluated[key] = ValidationIssue(
                "info", "", None, qualified_class, None, f"{label} not evaluated: {reason}"
            )


def _kind(constraint: MetaInstance) -> str:
    return constraint._qualified_class.rsplit(".", 1)[-1]


def _truth(expr: MetaInstance, scope: _ObjectScope) -> bool | None:
    """The boolean value of `expr`, or `None` when an attribute it reads is undefined."""
    try:
        return _as_bool(evaluate_expression(expr, scope))
    except UndefinedValueError:
        return None


def _constraints_of(
    obj: XtfObject,
    cache: dict[str, list[MetaInstance]],
    symbol_table: SymbolTable,
    repository: ModelRepository | None,
) -> list[MetaInstance]:
    """Every constraint that applies to `obj`: its class's own and those inherited via EXTENDS."""
    cached = cache.get(obj.qualified_class)
    if cached is None:
        cls = resolve_class(obj.qualified_class, symbol_table=symbol_table, repository=repository)
        cached = (
            []
            if cls is None
            else [c for level in inheritance_chain(cls) for c in getattr(level, "Constraint", None) or []]
        )
        cache[obj.qualified_class] = cached
    return cached


def _constraint_label(constraint: MetaInstance) -> str:
    name = getattr(constraint, "Name", None)
    return f"constraint {name!r}" if name else "constraint"


def _check_mandatory(
    constraint: MetaInstance, obj: XtfObject, basket: XtfBasket, scope: _ObjectScope, report: _Report
) -> None:
    expr = constraint.LogicalExpression
    if expr is None:
        return
    try:
        satisfied = _truth(expr, scope)
    except UnsupportedExpressionError as exc:
        report.skip(constraint, obj.qualified_class, _constraint_label(constraint), str(exc))
        return
    if satisfied is False:
        report.issues.append(
            ValidationIssue(
                "error",
                basket.bid,
                obj.tid,
                obj.qualified_class,
                None,
                f"{_constraint_label(constraint)} is not satisfied: {describe_expression(expr)}",
            )
        )


def _path_value(obj: XtfObject, path_els: list[MetaInstance], by_tid: dict[str, XtfObject]) -> Any:
    """The text (or referenced OID) at the end of `path_els`, `_UNDEFINED` when any step has no value."""
    current = obj
    for index, path_el in enumerate(path_els):
        if getattr(path_el, "Kind", None) not in _ATTRIBUTE_KINDS or getattr(path_el, "NumIndex", None) is not None:
            raise UnsupportedExpressionError(f"path element kind {getattr(path_el, 'Kind', None)!r} is not supported")
        nodes = current.attributes.get(path_el.Ref)
        if not nodes:
            return _UNDEFINED
        node = nodes[0]
        target = _extract_reference(node)
        if index < len(path_els) - 1:
            if target is None:
                raise UnsupportedExpressionError("navigation through a structure attribute is not supported")
            if target not in by_tid:
                return _UNDEFINED
            current = by_tid[target]
            continue
        if target is not None:
            return target
        if node.children:
            raise UnsupportedExpressionError("a structure attribute cannot take part in a UNIQUE constraint")
        return node.text if node.text is not None else _UNDEFINED
    return _UNDEFINED


def _unique_description(constraint: MetaInstance) -> str:
    paths = ["->".join(str(getattr(el, "Ref", "?")) for el in path.PathEls) for path in constraint.UniqueDef]
    return f"UNIQUE {', '.join(paths)}"


def _check_unique(
    constraint: MetaInstance,
    members: list[tuple[XtfBasket, XtfObject]],
    by_tid: dict[str, XtfObject],
    report: _Report,
) -> None:
    kind = constraint.Kind
    description = _unique_description(constraint)
    first_seen: dict[tuple[Any, tuple[Any, ...]], str | None] = {}
    for basket, obj in members:
        try:
            if constraint.Where is not None and not _truth(constraint.Where, _ObjectScope(obj, by_tid)):
                continue
            key = tuple(_path_value(obj, list(path.PathEls), by_tid) for path in constraint.UniqueDef)
        except UnsupportedExpressionError as exc:
            report.skip(constraint, obj.qualified_class, description, str(exc))
            return
        if any(part is _UNDEFINED for part in key):
            continue
        scope = (basket.bid if kind == "BasketU" else None, key)
        if scope in first_seen:
            report.issues.append(
                ValidationIssue(
                    "error",
                    basket.bid,
                    obj.tid,
                    obj.qualified_class,
                    None,
                    f"{description} is violated: {_format_key(key)} is already used by object {first_seen[scope]!r}",
                )
            )
        else:
            first_seen[scope] = obj.tid


def _format_key(key: tuple[Any, ...]) -> str:
    return "(" + ", ".join(repr(part) for part in key) + ")"


def _structure_elements(obj: XtfObject, hops: list[str]) -> list[dict[str, list[RawNode]]]:
    """The attribute groups of every structure element reached from `obj` along `hops` (BAG/LIST occurrences)."""
    holders = [obj.attributes]
    for hop in hops:
        holders = [
            _group_by_tag(occurrence.children)
            for holder in holders
            for node in holder.get(hop, [])
            for occurrence in node.children
        ]
    return holders


def _check_local_unique(constraint: MetaInstance, obj: XtfObject, basket: XtfBasket, report: _Report) -> None:
    description = _unique_description(constraint).replace("UNIQUE", "UNIQUE (LOCAL)", 1)
    paths = [[str(el.Ref) for el in path.PathEls] for path in constraint.UniqueDef]
    hops = paths[0][:-1]
    if not hops or any(path[:-1] != hops for path in paths):
        report.skip(constraint, obj.qualified_class, description, "the paths do not share one structure attribute")
        return
    seen: set[tuple[Any, ...]] = set()
    for holder in _structure_elements(obj, hops):
        values = []
        for path in paths:
            nodes = holder.get(path[-1])
            values.append(nodes[0].text if nodes and nodes[0].text is not None else _UNDEFINED)
        key = tuple(values)
        if any(part is _UNDEFINED for part in key):
            continue
        if key in seen:
            report.issues.append(
                ValidationIssue(
                    "error",
                    basket.bid,
                    obj.tid,
                    obj.qualified_class,
                    hops[0],
                    f"{description} is violated: {_format_key(key)} occurs more than once in the object",
                )
            )
        seen.add(key)


def _attribute_values(obj: XtfObject, names: list[str], by_tid: dict[str, XtfObject]) -> list[Any]:
    """Every text (or referenced OID) reached from `obj` along `names`; BAG/LIST/structure hops yield each element."""
    holders = [obj.attributes]
    for name in names[:-1]:
        following: list[dict[str, list[RawNode]]] = []
        for holder in holders:
            for node in holder.get(name, []):
                target = _extract_reference(node)
                if target is not None:
                    if target in by_tid:
                        following.append(by_tid[target].attributes)
                else:
                    following.extend(_group_by_tag(occurrence.children) for occurrence in node.children)
        holders = following
    values: list[Any] = []
    for holder in holders:
        for node in holder.get(names[-1], []):
            target = _extract_reference(node)
            if target is not None:
                values.append(target)
            elif node.text is not None and not node.children:
                values.append(node.text)
    return values


class _Population:
    """The objects of the transfer and of the catalogues, indexed by the classes they belong to (built on demand)."""

    def __init__(
        self,
        transfer: XtfTransfer,
        catalogs: list[XtfTransfer] | None,
        symbol_table: SymbolTable,
        repository: ModelRepository | None,
    ) -> None:
        self._objects = [
            obj for source in [transfer, *(catalogs or [])] for basket in source.baskets for obj in basket.objects
        ]
        self._symbol_table = symbol_table
        self._repository = repository
        self._chains: dict[str, set[int]] = {}

    def _chain_ids(self, qualified_class: str) -> set[int]:
        if qualified_class not in self._chains:
            cls = resolve_class(qualified_class, symbol_table=self._symbol_table, repository=self._repository)
            self._chains[qualified_class] = set() if cls is None else {id(level) for level in inheritance_chain(cls)}
        return self._chains[qualified_class]

    def objects_of(self, cls: MetaInstance) -> list[XtfObject]:
        return [obj for obj in self._objects if id(cls) in self._chain_ids(obj.qualified_class)]


def _check_existence(
    constraint: MetaInstance,
    members: list[tuple[XtfBasket, XtfObject]],
    by_tid: dict[str, XtfObject],
    population: _Population,
    report: _Report,
) -> None:
    attr_path = [str(el.Ref) for el in constraint.Attr.PathEls]
    required = list(zip(getattr(constraint, "ExistsIn", None) or [], constraint._required_attribute_paths))
    first_class = members[0][1].qualified_class
    label = f"EXISTENCE CONSTRAINT {'->'.join(attr_path)}"
    if not required or any(path is None for _cls, path in required):
        report.skip(constraint, first_class, label, "a REQUIRED IN path is not a plain attribute path")
        return
    allowed: set[Any] = set()
    present = 0
    for cls, path in required:
        objects = population.objects_of(cls)
        present += len(objects)
        for obj in objects:
            allowed.update(_attribute_values(obj, path, by_tid))
    if not present:
        report.skip(constraint, first_class, label, "no object of the required class is in the transfer or catalogues")
        return
    for basket, obj in members:
        for value in _attribute_values(obj, attr_path, by_tid):
            if value not in allowed:
                report.issues.append(
                    ValidationIssue(
                        "error",
                        basket.bid,
                        obj.tid,
                        obj.qualified_class,
                        attr_path[0],
                        f"{label} is violated: {value!r} does not exist in the required attribute",
                    )
                )


def _plausibility_issue(entry: _Plausibility) -> ValidationIssue | None:
    if not entry.evaluated:
        return None
    share = 100.0 * entry.satisfied / entry.evaluated
    if entry.kind == "HighPercC" and share < entry.percentage:
        verdict = f"at least {entry.percentage:g}% required"
    elif entry.kind == "LowPercC" and share > entry.percentage:
        verdict = f"at most {entry.percentage:g}% allowed"
    else:
        return None
    message = (
        f"plausibility constraint not met: {share:.1f}% of {entry.evaluated} objects "
        f"satisfy {entry.description} ({verdict})"
    )
    return ValidationIssue("warning", "", None, entry.qualified_class, None, message)


def check_transfer_constraints(
    transfer: XtfTransfer,
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
    catalogs: list[XtfTransfer] | None = None,
) -> list[ValidationIssue]:
    """Check the constraints of every object of `transfer`, returning the issues found (see module docstring)."""
    by_tid = _build_tid_index(transfer, catalogs)
    report = _Report()
    cache: dict[str, list[MetaInstance]] = {}
    unique_members: dict[int, tuple[MetaInstance, list[tuple[XtfBasket, XtfObject]]]] = {}
    plausibility: dict[int, _Plausibility] = {}
    existence_members: dict[int, tuple[MetaInstance, list[tuple[XtfBasket, XtfObject]]]] = {}
    for basket in transfer.baskets:
        for obj in basket.objects:
            scope = _ObjectScope(obj, by_tid)
            for constraint in _constraints_of(obj, cache, symbol_table, repository):
                if id(constraint) in report.skipped:
                    continue
                kind = _kind(constraint)
                if kind == "SimpleConstraint":
                    _visit_simple(constraint, obj, basket, scope, report, plausibility)
                elif kind == "UniqueConstraint":
                    if constraint.Kind == "LocalU":
                        _check_local_unique(constraint, obj, basket, report)
                    else:
                        unique_members.setdefault(id(constraint), (constraint, []))[1].append((basket, obj))
                elif kind == "SetConstraint":
                    report.skip(constraint, obj.qualified_class, "SET CONSTRAINT", "set functions are not evaluated")
                elif kind == "ExistenceConstraint":
                    existence_members.setdefault(id(constraint), (constraint, []))[1].append((basket, obj))
    for constraint, members in unique_members.values():
        if id(constraint) not in report.skipped:
            _check_unique(constraint, members, by_tid, report)
    if existence_members:
        population = _Population(transfer, catalogs, symbol_table, repository)
        for constraint, members in existence_members.values():
            _check_existence(constraint, members, by_tid, population, report)
    report.issues.extend(issue for entry in plausibility.values() if (issue := _plausibility_issue(entry)))
    report.issues.extend(report.not_evaluated.values())
    return report.issues


def _visit_simple(
    constraint: MetaInstance,
    obj: XtfObject,
    basket: XtfBasket,
    scope: _ObjectScope,
    report: _Report,
    plausibility: dict[int, _Plausibility],
) -> None:
    percentage = getattr(constraint, "Percentage", None)
    if percentage is None:
        if getattr(constraint, "Kind", None) in (None, "MandC"):
            _check_mandatory(constraint, obj, basket, scope, report)
        return
    expr = constraint.LogicalExpression
    if expr is None:
        return
    entry = plausibility.setdefault(
        id(constraint),
        _Plausibility(
            constraint.Kind,
            float(_try_number(percentage["Value"], 0)),
            describe_expression(expr),
            obj.qualified_class,
        ),
    )
    try:
        satisfied = _truth(expr, scope)
    except UnsupportedExpressionError as exc:
        report.skip(constraint, obj.qualified_class, _constraint_label(constraint), str(exc))
        plausibility.pop(id(constraint), None)
        return
    if satisfied is not None:
        entry.evaluated += 1
        entry.satisfied += int(satisfied)
