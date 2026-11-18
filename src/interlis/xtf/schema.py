"""Link transfer objects to the schema built by InterlisModelBuilder.

Classes are looked up by full qualified name (the same as `XtfObject.qualified_class`), so `validate.py` can
interpret each attribute by its declared type.
"""

import functools
from dataclasses import dataclass
from typing import Any, cast

from interlis.builder.forward_refs import SymbolTable
from interlis.builder.repository import ModelRepository
from interlis.metamodel.instance import MetaInstance


def resolve_class(
    qualified_class: str, *, symbol_table: SymbolTable, repository: ModelRepository | None
) -> MetaInstance | None:
    """Find the Class instance for a qualified name ("Model.Topic.ClassName").

    Checks the root model's table first, then the model named by the first segment in `repository`.
    """
    found = symbol_table.resolve(qualified_class, kind_hint=["Class"])
    if isinstance(found, MetaInstance):
        return found
    if repository is not None and "." in qualified_class:
        model_name = qualified_class.split(".", 1)[0]
        found = repository.resolve_external(model_name, qualified_class, kind_hint=["Class"])
        if isinstance(found, MetaInstance):
            return found
    return None


def home_symbol_table(
    qualified_class: str, *, symbol_table: SymbolTable, repository: ModelRepository | None
) -> SymbolTable:
    """Return the symbol table that declares `qualified_class`.

    That is the root table if it holds the class, else the table of its model from `repository`.
    """
    table = symbol_table
    if not isinstance(symbol_table.resolve(qualified_class, kind_hint=["Class"]), MetaInstance):
        if repository is not None and "." in qualified_class:
            table = repository.symbol_table_for(qualified_class.split(".", 1)[0]) or symbol_table
    if repository is None:
        return table
    others = _ancestor_tables(qualified_class, table, repository)
    return cast(SymbolTable, _WithAncestorTables(table, others)) if others else table


@functools.lru_cache(maxsize=1024)
def _ancestor_tables(qualified_class: str, table: SymbolTable, repository: ModelRepository) -> tuple[SymbolTable, ...]:
    """The loaded models' tables declaring an ancestor of `qualified_class` that `table` doesn't hold."""
    cls = table.resolve(qualified_class, kind_hint=["Class"])
    found: dict[int, SymbolTable] = {}
    seen: set[int] = set()
    ancestor = getattr(cls, "Super", None)
    while isinstance(ancestor, MetaInstance) and id(ancestor) not in seen:
        seen.add(id(ancestor))
        if table.qualified_name_of(ancestor) is None:
            for other in repository.loaded_models().values():
                if other is not table and other.qualified_name_of(ancestor) is not None:
                    found.setdefault(id(other), other)
        ancestor = getattr(ancestor, "Super", None)
    return tuple(found.values())


class _WithAncestorTables:
    """A class's home table that also lists the models of its ancestors.

    Needed because an association declared in a base model embeds roles on subclasses in an extending topic.
    """

    def __init__(self, home: SymbolTable, others: tuple[SymbolTable, ...]):
        self._home = home
        self._others = others

    def all_registered(self) -> list[Any]:
        return [*self._home.all_registered(), *(x for other in self._others for x in other.all_registered())]

    def __getattr__(self, name: str) -> Any:
        return getattr(self._home, name)


def _own_attributes_of(class_instance: MetaInstance) -> dict[str, MetaInstance]:
    """Map attribute name -> AttrOrParam instance, own attributes only (inherited ones: see `attributes_of`)."""
    return {
        a.Name: a
        for a in (getattr(class_instance, "ClassAttribute", None) or [])
        if isinstance(a, MetaInstance) and getattr(a, "Name", None)
    }


def single_own_attribute(class_instance: MetaInstance) -> MetaInstance | None:
    """Return the class's only own attribute if it has exactly one, else `None`.

    This detects wrapper STRUCTUREs such as catalogue references.
    """
    own = _own_attributes_of(class_instance)
    if len(own) == 1:
        return next(iter(own.values()))
    return None


def attributes_of(class_instance: MetaInstance) -> dict[str, MetaInstance]:
    """Map attribute name to its AttrOrParam, own attributes first, then inherited ones.

    An own attribute wins over an inherited one. The walk stops at a missing or unresolved parent and at cycles.
    """
    merged: dict[str, MetaInstance] = {}
    for level in reversed(inheritance_chain(class_instance)):
        merged.update(_own_attributes_of(level))
    return merged


def inheritance_chain(class_instance: MetaInstance) -> list[MetaInstance]:
    """`class_instance`, then each class up its `EXTENDS` chain (stops at an unresolved `Super` or a cycle)."""
    chain: list[MetaInstance] = []
    seen: set[int] = set()
    current: MetaInstance | None = class_instance
    while isinstance(current, MetaInstance) and id(current) not in seen:
        seen.add(id(current))
        chain.append(current)
        current = getattr(current, "Super", None)
    return chain


def _class_related_base_class(instance: MetaInstance) -> MetaInstance | None:
    """Return `instance`'s `.BaseClass`, for a `Role` or a `ReferenceType` (same generic association)."""
    base = getattr(instance, "BaseClass", None)
    if isinstance(base, list):
        base = base[0] if base else None
    return base if isinstance(base, MetaInstance) else None


def _all_class_related_base_classes(instance: MetaInstance) -> list[MetaInstance]:
    """Return the list of `.BaseClass` (not just the first), as `CLASS RESTRICTION(A; B; C)` attaches all candidates."""
    base = getattr(instance, "BaseClass", None)
    if base is None:
        return []
    if isinstance(base, list):
        return [b for b in base if isinstance(b, MetaInstance)]
    return [base] if isinstance(base, MetaInstance) else []


def _role_is_required(role: MetaInstance) -> bool:
    """Check whether the role's minimum cardinality is >= 1 (e.g. `{1}`, `{1..*}`); no clause means `{0..*}`."""
    mult = getattr(role, "Multiplicity", None)
    minimum = getattr(mult, "Min", None) if isinstance(mult, MetaInstance) else None
    return isinstance(minimum, str) and minimum.isdigit() and int(minimum) >= 1


def _role_is_multi(role: MetaInstance) -> bool:
    """Check whether the role's maximum cardinality is greater than 1.

    Without a cardinality clause a role is `{0..*}`, except a composition role, which is `{0..1}`.
    """
    mult = getattr(role, "Multiplicity", None)
    if not isinstance(mult, MetaInstance):
        return getattr(role, "Strongness", None) != "Comp"
    maximum = getattr(mult, "Max", None)
    if maximum == "same_as_Min":
        maximum = getattr(mult, "Min", None)
    return maximum == "*" or (isinstance(maximum, str) and maximum.isdigit() and int(maximum) > 1)


def line_allows_arcs(line_type: MetaInstance | None) -> bool:
    """Whether a LineType's `WITH (...)` admits ARCS (circular arc segments)."""
    forms = getattr(line_type, "LineForm", None) or []
    return any(getattr(f, "Name", None) == "ARCS" for f in (forms if isinstance(forms, list) else [forms]))


def multi_geometry_element(structure: MetaInstance | None) -> "ResolvedAttribute | None":
    """Return the single geometry attribute of a multi-geometry wrapper STRUCTURE, else `None`.

    The wrapper's only attribute is a BAG/LIST OF a STRUCTURE holding one coordinate or line attribute.
    """
    if not isinstance(structure, MetaInstance) or getattr(structure, "Kind", None) != "Structure":
        return None
    members = list(attributes_of(structure).values())
    if len(members) != 1:
        return None
    bag = resolve_attribute(members[0])
    element = getattr(bag.type_instance, "BaseType", None) if bag.type_kind == "MultiValue" else None
    if not isinstance(element, MetaInstance) or getattr(element, "Kind", None) != "Structure":
        return None
    parts = list(attributes_of(element).values())
    if len(parts) != 1:
        return None
    geometry = resolve_attribute(parts[0])
    return geometry if geometry.type_kind in ("CoordType", "LineType") else None


def association_roles(association: MetaInstance) -> list[MetaInstance]:
    """An association's roles: its own, then those it inherits through `EXTENDS` (an own role redefines a
    same-named inherited one).
    """
    roles: dict[str, MetaInstance] = {}
    for owner in inheritance_chain(association):
        for role in getattr(owner, "Role", None) or []:
            if isinstance(role, MetaInstance):
                roles.setdefault(getattr(role, "Name", None) or str(id(role)), role)
    return list(roles.values())


def association_is_embedded(association: MetaInstance) -> bool:
    """Whether an association is carried as a role attribute of a class rather than as its own object.

    True for exactly 2 roles where not both have a maximum cardinality above 1.
    """
    roles = association_roles(association)
    return len(roles) == 2 and not (_role_is_multi(roles[0]) and _role_is_multi(roles[1]))


def embedded_roles_of(class_instance: MetaInstance, symbol_table: SymbolTable) -> dict[str, MetaInstance]:
    """Map role name to Role for the embedded association roles of this class.

    An embedded role is a pseudo-attribute of the class owning it: the target of the only role with maximum above 1,
    else the second role's target. Only `symbol_table` is searched; abstract bases match concrete subclasses.
    """
    result: dict[str, MetaInstance] = {}
    for candidate in symbol_table.all_registered():
        if not isinstance(candidate, MetaInstance) or candidate._qualified_class.rsplit(".", 1)[-1] != "Class":
            continue
        embedding = association_embedding(candidate)
        if embedding is None:
            continue
        embed_on, embedded_role = embedding
        if is_class_compatible(class_instance, embed_on) and getattr(embedded_role, "Name", None):
            result[embedded_role.Name] = embedded_role
    return result


def association_embedding(association: MetaInstance) -> tuple[MetaInstance, MetaInstance] | None:
    """Return `(class it embeds on, embedded role)` for an embedded association, else `None`.

    Applies the rule of `embedded_roles_of` to one association.
    """
    if getattr(association, "Kind", None) != "Association":
        return None
    roles = [r for r in (getattr(association, "Role", None) or []) if isinstance(r, MetaInstance)]
    if len(roles) != 2:
        return None
    role_a, role_b = roles
    target_a, target_b = _class_related_base_class(role_a), _class_related_base_class(role_b)
    if target_a is None or target_b is None:
        return None
    multi_a, multi_b = _role_is_multi(role_a), _role_is_multi(role_b)
    if multi_a and multi_b:
        return None
    if multi_a:
        return target_a, role_b
    return target_b, role_a


def schema_members_of(class_instance: MetaInstance, symbol_table: SymbolTable) -> dict[str, MetaInstance]:
    """Return the union of `attributes_of` and `embedded_roles_of`, plus an association's own roles."""
    members = dict(attributes_of(class_instance))
    members.update(embedded_roles_of(class_instance, symbol_table))
    if getattr(class_instance, "Kind", None) == "Association":
        # A non-embedded association is transferred as its own object; its roles are reference elements.
        for role in association_roles(class_instance):
            if getattr(role, "Name", None):
                members.setdefault(role.Name, role)
    return members


@dataclass
class ResolvedAttribute:
    """A schema attribute ready to be interpreted.

    Holds its AttrOrParam or Role, its resolved Type or target class (None if unresolved), and the short name of
    the Type's concrete metamodel class.
    """

    attr: MetaInstance
    type_instance: MetaInstance | None
    type_kind: str | None
    mandatory: bool


def resolve_attribute(attr: MetaInstance) -> ResolvedAttribute:
    if attr._qualified_class.rsplit(".", 1)[-1] == "Role":
        # Role EXTENDS ReferenceType: its inherited `Mandatory` is always true, so requiredness comes
        # from the cardinality; the target class comes from BaseClass rather than a Type field.
        target = _class_related_base_class(attr)
        return ResolvedAttribute(
            attr=attr,
            type_instance=target,
            type_kind="Class" if target is not None else None,
            mandatory=_role_is_required(attr),
        )
    type_instance = getattr(attr, "Type", None)
    type_instance = type_instance if isinstance(type_instance, MetaInstance) else None
    type_kind = type_instance._qualified_class.rsplit(".", 1)[-1] if type_instance is not None else None
    # A MANDATORY STRUCTURE-typed attribute carries the flag itself (its
    # Type stays the shared declared STRUCTURE, see the builder).
    mandatory = bool(getattr(attr, "Mandatory", False)) or (
        bool(getattr(type_instance, "Mandatory", False)) if type_instance is not None else False
    )
    return ResolvedAttribute(attr=attr, type_instance=type_instance, type_kind=type_kind, mandatory=mandatory)


def reference_target_class(resolved: ResolvedAttribute) -> MetaInstance | None:
    """Return the Class declared as the target of a reference or role.

    For an embedded role or a restricted class reference the type already is that class; for a plain
    `REFERENCE TO X` it is a wrapper whose `.BaseClass` is the class.
    """
    if resolved.type_instance is None:
        return None
    if resolved.type_kind == "ReferenceType":
        return _class_related_base_class(resolved.type_instance)
    if resolved.type_kind == "Class":
        return resolved.type_instance
    return None


def reference_target_classes(resolved: ResolvedAttribute) -> list[MetaInstance]:
    """Every class declared as a reference/role target - several for a role `-- A OR B`."""
    if resolved.type_kind == "Class" and resolved.attr._qualified_class.rsplit(".", 1)[-1] == "Role":
        alternatives = _all_class_related_base_classes(resolved.attr)
    elif resolved.type_kind == "ReferenceType" and resolved.type_instance is not None:
        alternatives = _all_class_related_base_classes(resolved.type_instance)
    else:
        alternatives = []
    if alternatives:
        return alternatives
    first = reference_target_class(resolved)
    return [first] if first is not None else []


def is_class_compatible(actual: MetaInstance, declared: MetaInstance) -> bool:
    """Check whether `actual` is `declared` or one of its subclasses.

    Classes are compared by identity, which works because one validation run shares a single symbol table.
    """
    seen: set[int] = set()
    current: MetaInstance | None = actual
    while isinstance(current, MetaInstance) and id(current) not in seen:
        if current is declared:
            return True
        seen.add(id(current))
        current = getattr(current, "Super", None)
    return False


def concrete_structure_subclasses(
    abstract_class: MetaInstance, *symbol_tables: SymbolTable | None
) -> list[MetaInstance]:
    """Return every concrete STRUCTURE subclass of an abstract one, sorted by name.

    Subclasses are found by scanning all classes in the given tables, since `Super` only points to the parent.
    """
    seen: set[int] = set()
    result: list[MetaInstance] = []
    for symbol_table in symbol_tables:
        if symbol_table is None:
            continue
        for candidate in symbol_table.all_registered():
            if not isinstance(candidate, MetaInstance) or candidate._qualified_class.rsplit(".", 1)[-1] != "Class":
                continue
            if candidate is abstract_class or id(candidate) in seen:
                continue
            if getattr(candidate, "Kind", None) != "Structure" or bool(getattr(candidate, "Abstract", False)):
                continue
            if is_class_compatible(candidate, abstract_class):
                seen.add(id(candidate))
                result.append(candidate)
    result.sort(key=lambda c: getattr(c, "Name", None) or "")
    return result


def restriction_candidates(resolved: ResolvedAttribute) -> list[MetaInstance]:
    """Return every candidate class of a `CLASS RESTRICTION(A; B; C)`.

    A plain `REFERENCE TO` or role gives 0 or 1 (see `reference_target_class`).
    """
    if resolved.type_kind != "ReferenceType" or resolved.type_instance is None:
        return []
    return _all_class_related_base_classes(resolved.type_instance)


def coord_axes(coord_type: MetaInstance | None) -> list[MetaInstance]:
    """Return the ordered `NumType` axes of a `CoordType`, each possibly with Min/Max/Unit.

    Empty if `coord_type` is `None` or its axes are unresolved; callers then only check that values parse.
    """
    if coord_type is None:
        return []
    axes = getattr(coord_type, "Axis", None)
    if isinstance(axes, list):
        return [a for a in axes if isinstance(a, MetaInstance)]
    return [axes] if isinstance(axes, MetaInstance) else []


def line_coord_type(line_type: MetaInstance | None) -> MetaInstance | None:
    """Return the `CoordType` given by a `LineType`'s `VERTEX` clause, else `None`.

    The `Super` chain is followed because a derived line type has no VERTEX clause of its own.
    """
    seen: set[int] = set()
    current: MetaInstance | None = line_type
    while isinstance(current, MetaInstance) and id(current) not in seen:
        seen.add(id(current))
        ct = getattr(current, "CoordType", None)
        if isinstance(ct, MetaInstance):
            return ct
        current = getattr(current, "Super", None)
    return None


def reference_external_status(resolved: ResolvedAttribute) -> bool | None:
    """Return whether a reference is marked `(EXTERNAL)`: True, False, or `None` if it cannot be determined.

    `None` lets the caller tell a legitimate external reference from bad data. Handles direct reference
    attributes, 1-attribute wrapper STRUCTUREs and embedded roles.
    """
    if resolved.type_kind == "ReferenceType" and resolved.type_instance is not None:
        return bool(getattr(resolved.type_instance, "External", False))
    if resolved.attr._qualified_class.rsplit(".", 1)[-1] == "Role":
        return bool(getattr(resolved.attr, "External", None))
    if resolved.type_kind == "Class" and resolved.type_instance is not None:
        # `_own_attributes_of`, not `attributes_of`: an inherited attribute would add a 2nd entry
        # and break wrapper detection.
        wrapped = _own_attributes_of(resolved.type_instance)
        if len(wrapped) == 1:
            inner = resolve_attribute(next(iter(wrapped.values())))
            if inner.type_kind == "ReferenceType" and inner.type_instance is not None:
                return bool(getattr(inner.type_instance, "External", False))
    return None


def enum_values(enum_type: MetaInstance) -> set[str]:
    """Return every valid dotted path for this EnumType.

    A hierarchical enum transfers the full path (`A.A`), and every node counts, not only leaves.
    """
    values: set[str] = set()

    def walk(node: MetaInstance | None, prefix: str, *, is_synthetic_root: bool) -> None:
        if node is None:
            return
        name = getattr(node, "Name", None)
        if not name:
            return
        if is_synthetic_root:
            path = ""
        else:
            path = f"{prefix}.{name}" if prefix else name
            values.add(path)
        for child in getattr(node, "Node", None) or []:
            walk(child, path, is_synthetic_root=False)

    walk(getattr(enum_type, "TopNode", None), "", is_synthetic_root=True)
    return values


def wrapped_reference(structure: MetaInstance | None) -> ResolvedAttribute | None:
    """The reference of a STRUCTURE whose only attribute is a `REFERENCE TO` (a catalogue-reference wrapper).

    Both JSON converters write such a structure as the bare OID string; any other structure stays an object.
    """
    if structure is None or getattr(structure, "Kind", None) != "Structure":
        return None
    members = attributes_of(structure)
    if len(members) != 1:
        return None
    resolved = resolve_attribute(next(iter(members.values())))
    is_reference = resolved.type_kind == "ReferenceType" or (
        resolved.type_kind == "Class" and getattr(resolved.type_instance, "Kind", None) != "Structure"
    )
    return resolved if is_reference and resolved.type_instance is not None else None


def meta_value(instance: MetaInstance | None, name: str) -> str | None:
    if instance is None:
        return None
    for meta in getattr(instance, "MetaAttribute", None) or []:
        if getattr(meta, "Name", None) == name:
            return meta.Value
    return None


def is_integer_range(min_raw: str | None, max_raw: str | None) -> bool:
    """True if both bounds are present and neither has a decimal point."""
    return min_raw is not None and max_raw is not None and "." not in min_raw and "." not in max_raw


def is_structure(type_instance: MetaInstance | None) -> bool:
    """True if a `type_kind == "Class"` resolution is a genuine STRUCTURE nesting.

    An embedded association role also resolves to "Class", so `Kind` tells them apart.
    """
    return type_instance is not None and getattr(type_instance, "Kind", None) == "Structure"


def nested_class(resolved: ResolvedAttribute) -> MetaInstance | None:
    """Return the Class a structure/BAG/LIST-of-structure attribute points to, else None.

    Only genuine STRUCTURE nesting counts (`is_structure`); an embedded role's target is a reference, not containment.

    """
    if resolved.type_kind == "Class" and is_structure(resolved.type_instance):
        return resolved.type_instance
    if resolved.type_kind == "MultiValue" and resolved.type_instance is not None:
        base = getattr(resolved.type_instance, "BaseType", None)
        if (
            isinstance(base, MetaInstance)
            and base._qualified_class.rsplit(".", 1)[-1] == "Class"
            and is_structure(base)
        ):
            return base
    return None
