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
required class is present, the constraint is reported once per class as a warning, not evaluated).
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
from interlis.xtf.schema import (
    association_embedding,
    association_roles,
    home_symbol_table,
    inheritance_chain,
    resolve_attribute,
    resolve_class,
)
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

    def skip(
        self, constraint: MetaInstance, qualified_class: str, label: str, reason: str, severity: str = "info"
    ) -> None:
        self.skipped.add(id(constraint))
        key = (qualified_class, label, reason)
        if key not in self.not_evaluated:
            self.not_evaluated[key] = ValidationIssue(
                severity, "", None, qualified_class, None, f"{label} not evaluated: {reason}"
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

    def chain_ids(self, qualified_class: str) -> set[int]:
        if qualified_class not in self._chains:
            cls = resolve_class(qualified_class, symbol_table=self._symbol_table, repository=self._repository)
            self._chains[qualified_class] = set() if cls is None else {id(level) for level in inheritance_chain(cls)}
        return self._chains[qualified_class]

    def objects_of(self, cls: MetaInstance) -> list[XtfObject]:
        return [obj for obj in self._objects if id(cls) in self.chain_ids(obj.qualified_class)]


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
        report.skip(
            constraint,
            first_class,
            label,
            "no object of the required class is in the transfer or catalogues (pass the catalogue with --catalog)",
            severity="warning",
        )
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


def _role_bounds(role: MetaInstance) -> tuple[int, float]:
    """The `(min, max)` cardinality of a role; no clause means `{0..*}`, except a composition role, `{0..1}`."""
    mult = getattr(role, "Multiplicity", None)
    if not isinstance(mult, MetaInstance):
        return (0, 1) if getattr(role, "Strongness", None) == "Comp" else (0, float("inf"))
    low, high = getattr(mult, "Min", None), getattr(mult, "Max", None)
    if high == "same_as_Min":
        high = low
    minimum = int(low) if isinstance(low, str) and low.isdigit() else 0
    maximum = float("inf") if high == "*" else int(high) if isinstance(high, str) and high.isdigit() else float("inf")
    return minimum, maximum


def _association_links(
    assoc: MetaInstance,
    roles: list[MetaInstance],
    population: _Population,
    by_tid: dict[str, XtfObject],
) -> tuple[list[tuple[str, str]], list[int]] | None:
    """The `(tid at role 0, tid at role 1)` links of an association present in the population.

    The second value lists the roles whose cardinality the data can show: for an embedded association, only the
    role whose target class carries the reference (the embedded role's own cardinality is an attribute check).
    """
    links: list[tuple[str, str]] = []
    embedding = association_embedding(assoc)
    if embedding is not None:
        embed_on, embedded_role = embedding
        if embedded_role not in roles:
            return None
        embedded_index = roles.index(embedded_role)
        for obj in population.objects_of(embed_on):
            for node in obj.attributes.get(embedded_role.Name, []):
                target = _extract_reference(node)
                if obj.tid is not None and target is not None:
                    ends = ["", ""]
                    ends[embedded_index], ends[1 - embedded_index] = target, obj.tid
                    links.append((ends[0], ends[1]))
        return links, [1 - embedded_index]
    for obj in population.objects_of(assoc):
        refs = [[_extract_reference(n) for n in obj.attributes.get(role.Name, [])] for role in roles]
        if all(len(r) == 1 and r[0] is not None for r in refs):
            links.append((refs[0][0], refs[1][0]))  # type: ignore[arg-type]
    return links, [0, 1]


def _check_role_cardinalities(
    transfer: XtfTransfer,
    catalogs: list[XtfTransfer] | None,
    symbol_table: SymbolTable,
    repository: ModelRepository | None,
    by_tid: dict[str, XtfObject],
    report: _Report,
) -> None:
    """Report an object linked to too few or too many objects through an association role."""
    tables: dict[int, SymbolTable] = {}
    for basket in transfer.baskets:
        for qualified in {obj.qualified_class for obj in basket.objects}:
            table = home_symbol_table(qualified, symbol_table=symbol_table, repository=repository)
            tables[id(table)] = table
    associations: dict[int, MetaInstance] = {}
    for table in tables.values():
        for inst in table.all_registered():
            if (
                isinstance(inst, MetaInstance)
                and inst._qualified_class.rsplit(".", 1)[-1] == "Class"
                and getattr(inst, "Kind", None) == "Association"
            ):
                associations[id(inst)] = inst
    population = _Population(transfer, catalogs, symbol_table, repository)
    # An association a topic inherits through TOPIC EXTENDS exists once per topic, while a JSON-FG `featureType`
    # does not say which topic an object belongs to: the copies are counted as one association.
    merged: dict[tuple[str, ...], dict[str, Any]] = {}
    for assoc in associations.values():
        roles = [r for r in association_roles(assoc) if isinstance(r, MetaInstance) and getattr(r, "Name", None)]
        targets = [resolve_attribute(r).type_instance for r in roles]
        if len(roles) != 2 or any(t is None for t in targets):
            continue
        found = _association_links(assoc, roles, population, by_tid)
        if found is None:
            continue
        entry = merged.setdefault(
            (str(assoc.Name), *(r.Name for r in roles)),
            {"roles": roles, "links": [], "checked": found[1], "targets": [[], []]},
        )
        entry["links"].extend(found[0])
        for index in (0, 1):
            entry["targets"][index].append(targets[index])
    for entry in merged.values():
        roles, links, targets = entry["roles"], entry["links"], entry["targets"]
        for index in entry["checked"]:
            minimum, maximum = _role_bounds(roles[index])
            if minimum == 0 and maximum == float("inf"):
                continue
            counts: dict[str, int] = {}
            for link in links:
                counts[link[1 - index]] = counts.get(link[1 - index], 0) + 1
            subject_ids = {id(t) for t in targets[1 - index]}
            for basket in transfer.baskets:
                for obj in basket.objects:
                    if obj.tid is None or not subject_ids & population.chain_ids(obj.qualified_class):
                        continue
                    count = counts.get(obj.tid, 0)
                    if minimum <= count <= maximum:
                        continue
                    high = "*" if maximum == float("inf") else int(maximum)
                    report.issues.append(
                        ValidationIssue(
                            "error",
                            basket.bid,
                            obj.tid,
                            obj.qualified_class,
                            roles[index].Name,
                            f"{roles[index].Name} should associate {minimum} to {high} target objects "
                            f"(instead of {count})",
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
    _check_role_cardinalities(transfer, catalogs, symbol_table, repository, by_tid, report)
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
