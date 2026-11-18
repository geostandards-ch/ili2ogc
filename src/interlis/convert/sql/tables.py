"""IlisMeta16 `Class` -> SQL `Table`: columns, `BAG`/`LIST OF` child tables, ABSTRACT structure subclass tables,
UNIQUE/CHECK/FOREIGN KEY constraints. `build_tables` is the public entry point.
"""

from __future__ import annotations

from interlis.builder.forward_refs import SymbolTable
from interlis.builder.repository import ModelRepository
from interlis.convert.geometry_kinds import GEOMETRY_KINDS
from interlis.diagnostic_ids import note as _diag
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.schema import (
    ResolvedAttribute,
    association_is_embedded,
    attributes_of,
    concrete_structure_subclasses,
    embedded_roles_of,
    inheritance_chain,
    is_class_compatible,
    is_structure,
    line_allows_arcs,
    multi_geometry_element,
    reference_target_class,
    reference_target_classes,
    resolve_attribute,
    schema_members_of,
)

from .expressions import _MAX_STRUCT_FLATTEN_DEPTH, _expression_to_sql, _UnsupportedCheckExpression
from .identifiers import OID_COLUMN, RESERVED_COLUMNS, SEQ_COLUMN, _dedup_name, _sql_identifier, _truncate_identifier
from .model import CheckConstraint, Column, ForeignKey, Table, UniqueConstraint
from .types import (
    GeometryColumnInfo,
    _column_domain,
    _domain_check,
    _geometry_column_info,
    _scalar_sql_type,
    crs_repository,
)


def _avoid_identity_collision(columns: list[Column]) -> dict[str, str]:
    """Rename any attribute column named like a technical column (`t_id`, `t_basket`, ...) to `<name>_attr` (or
    `<name>_attr_2`, ...), IN PLACE - returns the `{old_name: new_name}` rename map, which the caller also applies to
    its UNIQUE constraints.
    """
    used = {c.name for c in columns}
    renamed: dict[str, str] = {}
    for column in columns:
        if column.name not in RESERVED_COLUMNS:
            continue
        base_name = f"{column.name}_attr"
        new_name = base_name
        suffix = 2
        while new_name in used:
            new_name = f"{base_name}_{suffix}"
            suffix += 1
        used.discard(column.name)
        used.add(new_name)
        renamed[column.name] = new_name
        column.name = new_name
    return renamed


def _geometry_notes(label: str, info: GeometryColumnInfo) -> list[str]:
    """The `-- NOTE`s for one geometry attribute: why it has no column, and any CRS-annotation finding."""
    findings = list(info.warnings)
    if info.problem is not None:
        findings.insert(0, info.problem)
    return [_diag(rule, f"{label}: {message}") for rule, message in findings]


def _columns_for_class(
    cls: MetaInstance,
    symbol_table: SymbolTable | None,
    *,
    prefix: str = "",
    depth: int = 0,
    role_tables: tuple[SymbolTable, ...] = (),
    presence_checks: list[tuple[str, list[str], list[str]]] | None = None,
    child_ili_names: dict[str, str] | None = None,
    name_tables: tuple[SymbolTable, ...] = (),
    path: tuple[str, ...] = (),
    child_paths: dict[str, tuple[str, ...]] | None = None,
) -> tuple[
    list[Column],
    list[ForeignKey],
    list[str],
    list[tuple[str, MetaInstance]],
    dict[str, list[list[str]]],
    list[tuple[str, MetaInstance, bool, bool]],
]:
    """Return `(columns, foreign_keys, notes, child_specs, local_unique, abstract_specs)` for the members of `cls`.

    STRUCTURE attributes are flattened inline up to `_MAX_STRUCT_FLATTEN_DEPTH`.
    """
    columns: list[Column] = []
    foreign_keys: list[ForeignKey] = []
    notes: list[str] = []
    child_specs: list[tuple[str, MetaInstance]] = []
    local_unique: dict[str, list[list[str]]] = {}
    abstract_specs: list[tuple[str, MetaInstance, bool, bool]] = []
    members = schema_members_of(cls, symbol_table) if symbol_table is not None else attributes_of(cls)
    for extra_table in role_tables:
        if extra_table is not symbol_table:
            for role_name, role in embedded_roles_of(cls, extra_table).items():
                members.setdefault(role_name, role)
    lookup_tables = [t for t in (symbol_table, *role_tables, *name_tables) if t is not None]

    def qualified(attr: MetaInstance) -> str | None:
        return next((q for t in lookup_tables if (q := t.qualified_name_of(attr))), None)

    def ili_name(attr: MetaInstance) -> str | None:
        """The attribute's qualified INTERLIS name - for a top-level column only, a flattened part has none."""
        return None if prefix else qualified(attr)

    for name, attr in members.items():
        resolved = resolve_attribute(attr)
        label = f"{prefix}{name}"
        here = (*path, name)
        col_name = _sql_identifier(label)

        if resolved.type_kind == "MultiValue":
            multi_value = resolved.type_instance
            if not isinstance(multi_value, MetaInstance):
                notes.append(
                    _diag(
                        "SQL-BAGLIST-ELEMENT-UNRESOLVED",
                        f"{label}: BAG/LIST OF element type not resolved - provide its model via --repo",
                    )
                )
                continue
            element = getattr(multi_value, "BaseType", None)
            is_abstract_struct = (
                isinstance(element, MetaInstance)
                and is_structure(element)
                and bool(getattr(element, "Abstract", False))
            )
            # A BAG/LIST's child table stands for the attribute even inside a flattened STRUCTURE.
            if child_ili_names is not None and qualified(attr):
                child_ili_names[label] = qualified(attr)
            if is_abstract_struct:
                abstract_specs.append((label, element, bool(getattr(multi_value, "Ordered", False)), True))
                continue
            child_specs.append((label, multi_value))
            if child_paths is not None:
                child_paths[label] = here
            continue

        if resolved.type_kind == "Class" and is_structure(resolved.type_instance):
            # A multi-geometry wrapper (CHBase MultiSurface/MultiLine/MultiPoint)
            # is one Multi* column, not a child table of single parts.
            multi_geometry = multi_geometry_element(resolved.type_instance)
            if multi_geometry is not None:
                info = _geometry_column_info(multi_geometry, symbol_table)
                notes.extend(_geometry_notes(label, info))
                if info.sfa_type is None:
                    continue
                sfa_type, srid = info.sfa_type, info.srid
                if not sfa_type.startswith("Multi"):
                    sfa_type = f"Multi{sfa_type}"
                if multi_geometry.type_kind == "LineType" and line_allows_arcs(multi_geometry.type_instance):
                    notes.append(_arcs_note(label))
                columns.append(
                    Column(
                        col_name,
                        "",
                        nullable=not resolved.mandatory,
                        geometry_type=sfa_type,
                        srid=srid,
                        ili_name=ili_name(attr),
                        source=here,
                    )
                )
                continue
            if depth >= _MAX_STRUCT_FLATTEN_DEPTH:
                notes.append(
                    _diag(
                        "SQL-STRUCT-NESTED-DEEP",
                        f"{label}: STRUCTURE nested more than {_MAX_STRUCT_FLATTEN_DEPTH} levels deep - not flattened",
                    )
                )
                continue
            if bool(getattr(resolved.type_instance, "Abstract", False)):
                abstract_specs.append((label, resolved.type_instance, False, False))
                continue
            sub_columns, sub_fks, sub_notes, sub_child_specs, sub_local_unique, sub_abstract_specs = _columns_for_class(
                resolved.type_instance,
                symbol_table,
                prefix=f"{label}_",
                depth=depth + 1,
                presence_checks=presence_checks,
                child_ili_names=child_ili_names,
                name_tables=name_tables,
                path=here,
                child_paths=child_paths,
            )
            if not resolved.mandatory:
                required = [c.name for c in sub_columns if not c.nullable]
                for sub_column in sub_columns:
                    sub_column.nullable = True
                # Only binds when the structure has more columns than its required ones.
                if required and len(required) < len(sub_columns) and presence_checks is not None:
                    presence_checks.append((label, [c.name for c in sub_columns], required))
            columns.extend(sub_columns)
            foreign_keys.extend(sub_fks)
            notes.extend(sub_notes)
            child_specs.extend(sub_child_specs)
            abstract_specs.extend(sub_abstract_specs)
            for nested_key, nested_groups in sub_local_unique.items():
                local_unique.setdefault(nested_key, []).extend(nested_groups)
            struct_local_unique, struct_local_unique_notes = _local_unique_constraints_for_class(resolved.type_instance)
            for role_attr, groups in struct_local_unique.items():
                local_unique.setdefault(f"{label}_{role_attr}", []).extend(groups)
            notes.extend(f"{label}: {note}" for note in struct_local_unique_notes)
            continue

        if resolved.type_kind in ("Class", "ReferenceType") and resolved.type_instance is not None:
            target = reference_target_class(resolved)
            if target is None:
                notes.append(
                    _diag(
                        "SQL-REF-TARGET-UNRESOLVED",
                        f"{label}: reference target not resolved - pass its model's directory "
                        f"to --repo, or the model file to --catalog",
                    )
                )
                continue
            target_table = _sql_identifier(getattr(target, "Name", None) or "")
            # A link-table row always carries both ends, whatever the role's cardinality.
            link_role = getattr(cls, "Kind", None) == "Association"
            columns.append(
                Column(
                    col_name,
                    "bigint",
                    nullable=not (resolved.mandatory or link_role),
                    ili_name=ili_name(attr),
                    source=here,
                )
            )
            fk_name = _truncate_identifier(_sql_identifier(f"fk_{getattr(cls, 'Name', '')}_{label}"))
            # A composition role (`-<#>`) names the whole: deleting it deletes its parts.
            on_delete = "CASCADE" if getattr(attr, "Strongness", None) == "Comp" else None
            foreign_keys.append(
                ForeignKey(
                    fk_name,
                    [col_name],
                    target_table,
                    [OID_COLUMN],
                    ref_class_id=id(target),
                    ref_alternative_ids=tuple(id(t) for t in reference_target_classes(resolved)),
                    on_delete=on_delete,
                )
            )
            continue

        if resolved.type_kind in GEOMETRY_KINDS:
            info = _geometry_column_info(resolved, symbol_table)
            notes.extend(_geometry_notes(label, info))
            if info.sfa_type is None:
                continue
            sfa_type, srid = info.sfa_type, info.srid
            if resolved.type_kind == "LineType" and line_allows_arcs(resolved.type_instance):
                notes.append(_arcs_note(label))
            columns.append(
                Column(
                    col_name,
                    sql_type="",
                    nullable=not resolved.mandatory,
                    ili_name=ili_name(attr),
                    source=here,
                    geometry_type=sfa_type,
                    srid=srid,
                )
            )
            continue

        scalar_type = _scalar_sql_type(resolved)
        if scalar_type is not None:
            columns.append(
                Column(
                    col_name,
                    scalar_type,
                    nullable=not resolved.mandatory,
                    check=_domain_check(resolved),
                    domain=_column_domain(resolved),
                    ili_name=ili_name(attr),
                    source=here,
                )
            )
            continue

        if resolved.type_kind is None:
            notes.append(
                _diag(
                    "BUILD-TYPE-UNRESOLVED",
                    f"{label}: attribute type not resolved by the model builder - "
                    f"provide the imported model via --repo",
                )
            )
        else:
            notes.append(_diag("SQL-ATTR-TYPE-UNMAPPED", f"{label}: unsupported type {resolved.type_kind!r}"))
    return columns, foreign_keys, notes, child_specs, local_unique, abstract_specs


def _build_child_table(
    parent_table: str,
    attr_name: str,
    multi_value: MetaInstance,
    symbol_table: SymbolTable | None,
    name_tables: tuple[SymbolTable, ...] = (),
) -> tuple[Table | None, dict[str, str], str | None, list[tuple[str, MetaInstance]]]:
    """Return `(child_table, renamed, None, nested_child_specs)` or `(None, {}, reason, [])` for a `BAG`/`LIST OF`.

    Schema: a `<parent>_fk` FOREIGN KEY, `seq` (if `Ordered`) and the element's value column(s). A nested
    `BAG`/`LIST OF` is returned as `nested_child_specs` for `build_tables` to build one level deeper.
    """
    base_type = getattr(multi_value, "BaseType", None)
    if not isinstance(base_type, MetaInstance):
        return None, {}, "BaseType not resolved", []
    base_kind = base_type._qualified_class.rsplit(".", 1)[-1]

    child_table_name = _sql_identifier(f"{parent_table}_{attr_name}")
    fk_column = _sql_identifier(f"{parent_table}_fk")
    columns: list[Column] = [Column(fk_column, "bigint", nullable=False)]
    foreign_keys: list[ForeignKey] = [
        ForeignKey(
            _truncate_identifier(_sql_identifier(f"fk_{child_table_name}_{fk_column}")),
            [fk_column],
            parent_table,
            [OID_COLUMN],
            on_delete="CASCADE",
        )
    ]
    notes: list[str] = []

    if bool(getattr(multi_value, "Ordered", False)):
        columns.append(Column(SEQ_COLUMN, "bigint", nullable=False))

    nested_child_specs: list[tuple[str, MetaInstance]] = []
    if base_kind == "Class" and is_structure(base_type):
        if bool(getattr(base_type, "Abstract", False)):
            # An abstract element is routed to `abstract_specs` by `_columns_for_class`
            # (one child table per concrete subclass) - never reaches here.
            return None, {}, "BAG/LIST OF an ABSTRACT structure - subclass polymorphism not mapped to a table", []
        sub_columns, sub_fks, sub_notes, sub_child_specs, _sub_local_unique, _sub_abstract = _columns_for_class(
            base_type, symbol_table, name_tables=name_tables
        )
        columns.extend(sub_columns)
        foreign_keys.extend(sub_fks)
        notes.extend(sub_notes)
        nested_child_specs = sub_child_specs
    elif base_kind in ("Class", "ReferenceType"):
        synthetic = ResolvedAttribute(attr=base_type, type_instance=base_type, type_kind=base_kind, mandatory=True)
        target = reference_target_class(synthetic)
        if target is None:
            return None, {}, "reference target not resolved - pass its model to --repo or --catalog", []
        target_table = _sql_identifier(getattr(target, "Name", None) or "")
        columns.append(Column("value", "bigint", nullable=True))
        foreign_keys.append(
            ForeignKey(
                _truncate_identifier(_sql_identifier(f"fk_{child_table_name}_value")),
                ["value"],
                target_table,
                [OID_COLUMN],
                ref_class_id=id(target),
            )
        )
    elif base_kind in GEOMETRY_KINDS:
        synthetic = ResolvedAttribute(attr=base_type, type_instance=base_type, type_kind=base_kind, mandatory=True)
        info = _geometry_column_info(synthetic, symbol_table)
        if info.sfa_type is None:
            return None, {}, info.problem[1] if info.problem else "no geometry column", []
        columns.append(Column("value", sql_type="", nullable=False, geometry_type=info.sfa_type, srid=info.srid))
    else:
        synthetic = ResolvedAttribute(attr=base_type, type_instance=base_type, type_kind=base_kind, mandatory=True)
        scalar_type = _scalar_sql_type(synthetic)
        if scalar_type is None:
            return None, {}, f"unsupported element type {base_kind!r}", []
        columns.append(
            Column(
                "value", scalar_type, nullable=False, check=_domain_check(synthetic), domain=_column_domain(synthetic)
            )
        )

    renamed = _avoid_identity_collision(columns)
    checks: list[CheckConstraint] = []
    if base_kind == "Class" and is_structure(base_type):
        # The element STRUCTURE's own MANDATORY CONSTRAINTs bind each child row.
        checks, check_notes = _check_constraints_for_class(
            base_type, child_table_name, {c.name for c in columns}, renamed
        )
        notes.extend(check_notes)
    table = Table(
        name=child_table_name, columns=columns, foreign_keys=foreign_keys, check_constraints=checks, notes=notes
    )
    return table, renamed, None, nested_child_specs


def _build_nested_child_tables(
    parent_child_table: Table,
    child_specs: list[tuple[str, MetaInstance]],
    symbol_table: SymbolTable | None,
    used_table_names: set[str],
    name_tables: tuple[SymbolTable, ...] = (),
) -> list[Table]:
    """Build one `<parent_child_table>_<attr>` table per `BAG`/`LIST OF` attribute found one level inside it.

    Bounded to ONE level; a further-nested `BAG`/`LIST OF` is noted, not built.
    """
    out: list[Table] = []
    for attr_name, multi_value in child_specs:
        child_table, _renamed, reason, deeper_specs = _build_child_table(
            parent_child_table.name, attr_name, multi_value, symbol_table, name_tables
        )
        if child_table is None:
            rule = (
                "SQL-BAGLIST-ELEMENT-UNRESOLVED"
                if reason and "not resolved" in reason
                else "SQL-BAGLIST-ELEMENT-UNMAPPED"
            )
            parent_child_table.notes.append(_diag(rule, f"{attr_name}: BAG/LIST OF - {reason}"))
            continue
        base_name = child_table.name
        name = base_name
        suffix = 2
        while name in used_table_names:
            name = f"{base_name}_{suffix}"
            suffix += 1
        used_table_names.add(name)
        child_table.name = name
        if deeper_specs:
            child_table.notes.append(
                _diag(
                    "SQL-BAGLIST-ELEMENT-UNMAPPED",
                    "a further nested BAG/LIST OF attribute is not built as a table (2 levels of nesting is the bound)",
                )
            )
        out.append(child_table)
    return out


def _structure_child_table(
    parent_table: str,
    table_name: str,
    struct_cls: MetaInstance,
    symbol_table: SymbolTable | None,
    *,
    ordered: bool,
    name_tables: tuple[SymbolTable, ...] = (),
) -> tuple[Table, dict[str, str]]:
    """One child table holding instances of a concrete STRUCTURE `struct_cls`.

    Used per concrete subclass of an ABSTRACT structure attribute: a `<parent>_fk`, `seq` when `ordered`, then the
    structure's flattened columns.
    """
    fk_column = _sql_identifier(f"{parent_table}_fk")
    columns: list[Column] = [Column(fk_column, "bigint", nullable=False)]
    foreign_keys: list[ForeignKey] = [
        ForeignKey(
            _truncate_identifier(_sql_identifier(f"fk_{table_name}_{fk_column}")),
            [fk_column],
            parent_table,
            [OID_COLUMN],
            on_delete="CASCADE",
        )
    ]
    if ordered:
        columns.append(Column(SEQ_COLUMN, "bigint", nullable=False))
    sub_columns, sub_fks, sub_notes, _sub_child_specs, _sub_local_unique, _sub_abstract = _columns_for_class(
        struct_cls, symbol_table, name_tables=name_tables
    )
    columns.extend(sub_columns)
    foreign_keys.extend(sub_fks)
    renamed = _avoid_identity_collision(columns)
    checks, check_notes = _check_constraints_for_class(struct_cls, table_name, {c.name for c in columns}, renamed)
    table = Table(
        name=table_name,
        columns=columns,
        foreign_keys=foreign_keys,
        check_constraints=checks,
        notes=[*sub_notes, *check_notes],
    )
    return table, renamed


def _check_constraints_for_class(
    cls: MetaInstance,
    table_name: str,
    column_names: set[str],
    renamed: dict[str, str],
) -> tuple[list[CheckConstraint], list[str]]:
    """Return `(constraints, notes)` for `cls`'s own row-local `MANDATORY CONSTRAINT`s.

    `UniqueConstraint` is handled separately. Set/existence/plausibility constraints are population-level and
    cannot be a single-row `CHECK`: they become a `-- NOTE`.
    """
    result: list[CheckConstraint] = []
    notes: list[str] = []
    counter = 0
    for constraint in _inherited_constraints(cls):
        qname = constraint._qualified_class.rsplit(".", 1)[-1]
        label = repr(getattr(constraint, "Name", None)) if getattr(constraint, "Name", None) else "<unnamed>"
        if qname == "UniqueConstraint":
            continue  # handled by _unique_constraints_for_class / _local_unique_constraints_for_class
        if qname == "ExistenceConstraint":
            notes.append(
                _diag(
                    "SQL-CONSTRAINT-EXISTENCE",
                    f"EXISTENCE CONSTRAINT {label}: a check that a value also occurs in another class - "
                    "no single-row SQL CHECK can express it",
                )
            )
            continue
        if qname == "SetConstraint":
            notes.append(
                _diag(
                    "SQL-CONSTRAINT-SET",
                    f"SET CONSTRAINT {label}: a whole-population check - no single-row SQL CHECK can express it",
                )
            )
            continue
        if qname != "SimpleConstraint":
            notes.append(
                _diag(
                    "SQL-CONSTRAINT-NOT-ROWLOCAL",
                    f"CONSTRAINT {label} ({qname}): not a row-local MANDATORY CONSTRAINT - no CHECK generated",
                )
            )
            continue
        if (
            getattr(constraint, "Kind", None) not in (None, "MandC")
            or getattr(constraint, "Percentage", None) is not None
        ):
            notes.append(
                _diag(
                    "SQL-CONSTRAINT-PLAUSIBILITY",
                    f"CONSTRAINT {label}: percentage-based plausibility form (Kind="
                    f"{getattr(constraint, 'Kind', None)!r}) - a population ratio no single-row SQL CHECK can express",
                )
            )
            continue
        expr = getattr(constraint, "LogicalExpression", None)
        if expr is None:
            continue
        counter += 1
        name = getattr(constraint, "Name", None)
        try:
            sql_expr = _expression_to_sql(expr, column_names, renamed)
        except _UnsupportedCheckExpression as exc:
            notes.append(
                _diag(
                    "SQL-CHECK-EXPR-UNSUPPORTED",
                    f"MANDATORY CONSTRAINT {name or f'#{counter}'!r}: {exc} - CHECK not generated",
                )
            )
            continue
        constraint_name = _truncate_identifier(
            _sql_identifier(f"chk_{table_name}_{name}" if name else f"chk_{table_name}_{counter}")
        )
        result.append(CheckConstraint(constraint_name, sql_expr))
    return result, notes


def _unique_constraints_for_class(
    cls: MetaInstance, table_name: str
) -> tuple[list[UniqueConstraint], list[str], dict[str, list[list[str]]]]:
    """Return `(constraints, notes, struct_global_unique)` for `cls`'s own `Kind=GlobalU` `UniqueConstraint`s.

    `constraints` are plain `UNIQUE (...)` over own columns; `struct_global_unique` maps a `BAG`/`LIST OF STRUCTURE`
    attribute to its column groups, which `build_tables` turns into a `UNIQUE` on the child table.
    """
    result: list[UniqueConstraint] = []
    notes: list[str] = []
    struct_global_unique: dict[str, list[list[str]]] = {}
    for constraint in _inherited_constraints(cls):
        if not constraint._qualified_class.endswith("UniqueConstraint"):
            continue
        kind = getattr(constraint, "Kind", None)
        if kind == "LocalU":
            continue
        path_defs = getattr(constraint, "UniqueDef", None) or []
        if kind != "GlobalU" or not path_defs:
            notes.append(_diag("SQL-UNIQUE-BASKET", f"UNIQUE ({kind}): basket-scoped UNIQUE not supported yet"))
            continue
        own_columns: list[str] = []
        two_hop: list[tuple[str, str]] = []  # (raw first-hop attr name, sql sub-column)
        bad = False
        for path in path_defs:
            path_els = getattr(path, "PathEls", None) or []
            if any(getattr(pe, "Kind", None) not in ("ReferenceAttr", "Attribute") for pe in path_els):
                bad = True
                break
            refs = [getattr(pe, "Ref", None) or "" for pe in path_els]
            if len(refs) == 1:
                own_columns.append(_sql_identifier(refs[0]))
            elif len(refs) == 2:
                two_hop.append((refs[0], _sql_identifier(refs[1])))
            else:
                bad = True
                break
        if bad or (own_columns and two_hop):
            # 3+ hops, an unexpected PathEl kind, or own and navigated columns mixed: not expressible as one UNIQUE.
            notes.append(
                _diag(
                    "SQL-UNIQUE-CROSS-REF",
                    "UNIQUE across a '->' reference - not expressible as a plain SQL table constraint",
                )
            )
            continue
        if two_hop:
            first_hops = {h[0] for h in two_hop}
            if len(first_hops) != 1:
                notes.append(
                    _diag(
                        "SQL-UNIQUE-CROSS-REF",
                        "UNIQUE mixing several '->' navigations in one constraint - not expressible in SQL",
                    )
                )
                continue
            struct_global_unique.setdefault(two_hop[0][0], []).append([h[1] for h in two_hop])
            continue
        if own_columns:
            name = _truncate_identifier(_sql_identifier(f"uq_{table_name}_{'_'.join(own_columns)}"))
            result.append(UniqueConstraint(name, own_columns))
    return result, notes, struct_global_unique


def _local_unique_constraints_for_class(cls: MetaInstance) -> tuple[dict[str, list[list[str]]], list[str]]:
    """Return `({BAG/LIST attr name: [[sub-attr column, ...], ...]}, notes)` for `cls`'s own `Kind=LocalU` constraints.

    The role path must be exactly ONE hop naming the `BAG`/`LIST OF` attribute, shared by every entry of the
    constraint; anything else is noted.
    """
    result: dict[str, list[list[str]]] = {}
    notes: list[str] = []
    for constraint in _inherited_constraints(cls):
        if not constraint._qualified_class.endswith("UniqueConstraint"):
            continue
        if getattr(constraint, "Kind", None) != "LocalU":
            continue
        path_defs = getattr(constraint, "UniqueDef", None) or []
        if not path_defs:
            notes.append(
                _diag("SQL-UNIQUE-LOCAL-UNSUPPORTED", "UNIQUE (LOCAL): role path could not be resolved - not supported")
            )
            continue
        role_attr: str | None = None
        columns: list[str] = []
        supported = True
        for path in path_defs:
            path_els = getattr(path, "PathEls", None) or []
            if len(path_els) < 2 or any(
                getattr(pe, "Kind", None) not in ("ReferenceAttr", "Attribute") for pe in path_els
            ):
                notes.append(_diag("SQL-UNIQUE-LOCAL-UNSUPPORTED", "UNIQUE (LOCAL): path shape not supported"))
                supported = False
                break
            *role_hops, sub_attr = path_els
            if len(role_hops) != 1:
                notes.append(
                    _diag("SQL-UNIQUE-LOCAL-UNSUPPORTED", "UNIQUE (LOCAL) across a multi-hop role path - not supported")
                )
                supported = False
                break
            hop_name = getattr(role_hops[0], "Ref", None) or ""
            if role_attr is None:
                role_attr = hop_name
            elif hop_name != role_attr:
                notes.append(
                    _diag(
                        "SQL-UNIQUE-LOCAL-UNSUPPORTED",
                        "UNIQUE (LOCAL) mixing several BAG/LIST attributes in one constraint - not supported",
                    )
                )
                supported = False
                break
            columns.append(_sql_identifier(getattr(sub_attr, "Ref", None) or ""))
        if supported and role_attr and columns:
            result.setdefault(role_attr, []).append(columns)
    return result, notes


def _arcs_note(label: str) -> str:
    return _diag(
        "SQL-GEOM-ARCS-STROKED",
        f"{label}: admits ARCS, column is linear - stroke arc segments on load (e.g. ST_CurveToLine)",
    )


def _inherited_constraints(cls: MetaInstance) -> list[MetaInstance]:
    """`cls`'s own constraints, then each base class's: its table inlines the inherited columns they constrain."""
    return [c for owner in inheritance_chain(cls) for c in getattr(owner, "Constraint", None) or []]


def _distinguishing_suffixes(paths: list[list[str]]) -> list[str | None]:
    """For same-named classes, the part of their container names (model first, then topic) not shared by all.

    `BaseModel_SectoralPlans_LV03_V1_4` vs `..._LV95_V1_4` gives `lv03` /
    `lv95`; `None` everywhere when no level tells them apart.
    """
    for level in range(max((len(p) for p in paths), default=0)):
        token_lists = [(p[level] if level < len(p) else "").split("_") for p in paths]
        common = set.intersection(*(set(tokens) for tokens in token_lists))
        suffixes = ["_".join(t for t in tokens if t not in common).lower() for tokens in token_lists]
        if all(suffixes) and len(set(suffixes)) == len(suffixes):
            return list(suffixes)
    return [None] * len(paths)


def _table_base_names(
    classes: list[MetaInstance],
    symbol_table: SymbolTable | None,
    class_symbol_tables: dict[int, SymbolTable] | None,
) -> dict[int, str]:
    """Each class's table name: its own name, or - when several classes share it - suffixed with what tells their
    declaring models (or topics) apart, never a counter that depends on order.
    """
    by_name: dict[str, list[MetaInstance]] = {}
    for cls in classes:
        by_name.setdefault(_sql_identifier(getattr(cls, "Name", None) or ""), []).append(cls)
    names: dict[int, str] = {}
    for base, group in by_name.items():
        if len(group) == 1:
            names[id(group[0])] = base
            continue
        paths = []
        for cls in group:
            table = (class_symbol_tables or {}).get(id(cls), symbol_table)
            qualified = table.qualified_name_of(cls) if table is not None else None
            paths.append(qualified.split(".")[:-1] if qualified else [])
        for cls, suffix in zip(group, _distinguishing_suffixes(paths)):
            names[id(cls)] = _truncate_identifier(f"{base}_{suffix}") if suffix else base
    return names


def _presence_check(table_name: str, label: str, columns: list[str], required: list[str]) -> CheckConstraint:
    """An OPTIONAL structure is either wholly absent or carries all its MANDATORY parts."""
    absent = " AND ".join(f'"{c}" IS NULL' for c in columns)
    present = " AND ".join(f'"{c}" IS NOT NULL' for c in required)
    name = _truncate_identifier(_sql_identifier(f"chk_{table_name}_{label}_present"))
    return CheckConstraint(name, f"({absent}) OR ({present})")


def _reference_target_classes(target: MetaInstance, converted: list[MetaInstance]) -> list[MetaInstance]:
    """Every converted concrete class a `REFERENCE TO target` may point at: `target` unless ABSTRACT, then subclasses.

    Each concrete class has its own table with inherited columns inlined.
    """
    subclasses = sorted(
        (
            c
            for c in converted
            if c is not target and not getattr(c, "Abstract", False) and is_class_compatible(c, target)
        ),
        key=lambda c: getattr(c, "Name", None) or "",
    )
    return ([] if getattr(target, "Abstract", False) else [target]) + subclasses


def _split_polymorphic_fk(
    table: Table, fk: ForeignKey, targets: list[tuple[MetaInstance, str]], *, keeps_column: bool
) -> None:
    """Replace `fk`'s single column by one nullable FK column per target table, plus a CHECK that at most one is set.

    Exactly one when MANDATORY. A concrete base keeps the original column; other targets get `<column>_<table>`.
    """
    column = next(c for c in table.columns if c.name == fk.columns[0])
    used = {c.name for c in table.columns} - {column.name}
    new_columns: list[Column] = []
    new_fks: list[ForeignKey] = []
    for index, (target, ref_table) in enumerate(targets):
        if index == 0 and keeps_column:
            name, fk_name = column.name, fk.name
        else:
            name = _dedup_name(_truncate_identifier(_sql_identifier(f"{column.name}_{ref_table}")), used)
            fk_name = _truncate_identifier(_sql_identifier(f"{fk.name}_{ref_table}"))
        new_columns.append(Column(name, column.sql_type, nullable=True, source=column.source))
        new_fks.append(
            ForeignKey(fk_name, [name], ref_table, fk.ref_columns, ref_class_id=id(target), on_delete=fk.on_delete)
        )
    position = table.columns.index(column)
    table.columns[position : position + 1] = new_columns
    position = table.foreign_keys.index(fk)
    table.foreign_keys[position : position + 1] = new_fks
    count = " + ".join(f'CASE WHEN "{c.name}" IS NOT NULL THEN 1 ELSE 0 END' for c in new_columns)
    check_name = _truncate_identifier(_sql_identifier(f"chk_{table.name}_{column.name}_target"))
    table.check_constraints.append(CheckConstraint(check_name, f"({count}) {'<=' if column.nullable else '='} 1"))


def build_tables(
    classes: list[MetaInstance],
    symbol_table: SymbolTable | None = None,
    *,
    class_symbol_tables: dict[int, SymbolTable] | None = None,
    class_table_names: dict[int, str] | None = None,
    name_tables: tuple[SymbolTable, ...] = (),
    repository: ModelRepository | None = None,
) -> list[Table]:
    """`_build_tables` with `repository` (whose imported models' `CONTEXT`s bind generic geometry domains) in scope."""
    with crs_repository(repository):
        return _build_tables(
            classes,
            symbol_table,
            class_symbol_tables=class_symbol_tables,
            class_table_names=class_table_names,
            name_tables=name_tables,
        )


def _build_tables(
    classes: list[MetaInstance],
    symbol_table: SymbolTable | None = None,
    *,
    class_symbol_tables: dict[int, SymbolTable] | None = None,
    class_table_names: dict[int, str] | None = None,
    name_tables: tuple[SymbolTable, ...] = (),
) -> list[Table]:
    """Convert every `Class(Kind=Class)` in `classes` into a `Table`.

    A STRUCTURE-typed attribute is flattened inline and never becomes a table of its own.
    """
    tables = []
    used_table_names: set[str] = set()
    table_name_by_class_id: dict[int, str] = {}
    # Abstract-STRUCTURE subclass discovery spans the root and every per-class table (a concrete subclass can live
    # in another model's table than its abstract base).
    _distinct_catalog_tables = {id(t): t for t in (class_symbol_tables or {}).values()}.values()
    scan_symbol_tables: list[SymbolTable] = [st for st in [symbol_table, *_distinct_catalog_tables] if st is not None]
    # A non-embedded association (n:m, or >2 roles) is its own object and needs a link table. ABSTRACT classes have
    # no rows: their concrete subclasses are the FOREIGN KEY targets (`_reference_target_classes`).
    table_classes = [
        c
        for c in classes
        if (getattr(c, "Kind", None) == "Class" and not getattr(c, "Abstract", False))
        or (
            getattr(c, "Kind", None) == "Association"
            and not getattr(c, "Abstract", False)
            and not association_is_embedded(c)
        )
    ]
    base_names = _table_base_names(table_classes, symbol_table, class_symbol_tables)
    for cls in table_classes:
        base_name = base_names[id(cls)]
        table_name = base_name
        suffix = 2
        while table_name in used_table_names:
            table_name = f"{base_name}_{suffix}"
            suffix += 1
        used_table_names.add(table_name)
        table_name_by_class_id[id(cls)] = table_name
        if class_table_names is not None:
            class_table_names[id(cls)] = table_name

        home_table = (class_symbol_tables or {}).get(id(cls), symbol_table)
        presence_checks: list[tuple[str, list[str], list[str]]] = []
        child_ili_names: dict[str, str] = {}
        child_paths: dict[str, tuple[str, ...]] = {}
        columns, foreign_keys, notes, child_specs, nested_local_unique, abstract_specs = _columns_for_class(
            cls,
            home_table,
            role_tables=tuple(scan_symbol_tables),
            presence_checks=presence_checks,
            child_ili_names=child_ili_names,
            name_tables=name_tables,
            child_paths=child_paths,
        )
        if getattr(cls, "Kind", None) == "Association" and not foreign_keys:
            notes.append(
                _diag(
                    "SQL-ASSOC-ROLES-UNRESOLVED",
                    "link table has no role columns: association roles not resolved - "
                    "provide the base association's model via --repo",
                )
            )
        renamed = _avoid_identity_collision(columns)
        unique_constraints, unique_notes, struct_global_unique = _unique_constraints_for_class(cls, table_name)
        for unique in unique_constraints:
            unique.columns = [renamed.get(c, c) for c in unique.columns]
        column_names = {c.name for c in columns}
        valid_unique_constraints = []
        seen_unique: set[tuple[str, ...]] = set()
        for unique in unique_constraints:
            if tuple(unique.columns) in seen_unique:
                continue  # redeclared by an EXTENDED class over the same columns
            seen_unique.add(tuple(unique.columns))
            missing = [c for c in unique.columns if c not in column_names]
            if missing:
                unique_notes.append(
                    _diag(
                        "SQL-UNIQUE-COL-UNMAPPED",
                        f"UNIQUE ({', '.join(unique.columns)}): column(s) {missing} have no mapped SQL type",
                    )
                )
                continue
            valid_unique_constraints.append(unique)
        check_constraints, check_notes = _check_constraints_for_class(cls, table_name, column_names, renamed)
        check_constraints += [_presence_check(table_name, *spec) for spec in presence_checks]
        local_unique, local_unique_notes = _local_unique_constraints_for_class(cls)
        for attr_name, groups in nested_local_unique.items():
            local_unique.setdefault(attr_name, []).extend(groups)

        parent_table = Table(
            name=table_name,
            columns=columns,
            unique_constraints=valid_unique_constraints,
            foreign_keys=foreign_keys,
            check_constraints=check_constraints,
            notes=notes + unique_notes + check_notes + local_unique_notes,
            ili_name=home_table.qualified_name_of(cls) if home_table is not None else None,
            has_tid=getattr(cls, "Kind", None) != "Association",
        )
        tables.append(parent_table)

        for attr_name, multi_value in child_specs:
            child_table, child_renamed, reason, nested_specs = _build_child_table(
                table_name, attr_name, multi_value, home_table, name_tables
            )
            if child_table is None:
                rule = (
                    "SQL-BAGLIST-ELEMENT-UNRESOLVED"
                    if reason and "not resolved" in reason
                    else "SQL-BAGLIST-ELEMENT-UNMAPPED"
                )
                tables[-1].notes.append(_diag(rule, f"{attr_name}: BAG/LIST OF - {reason}"))
                continue
            child_base_name = child_table.name
            child_name = child_base_name
            suffix = 2
            while child_name in used_table_names:
                child_name = f"{child_base_name}_{suffix}"
                suffix += 1
            used_table_names.add(child_name)
            child_table.name = child_name
            child_table.parent, child_table.source = table_name, child_paths.get(attr_name, (attr_name,))
            # Its link back to the parent stands for the multi-valued attribute itself.
            child_table.columns[0].ili_name = child_ili_names.get(attr_name)

            fk_column = _sql_identifier(f"{table_name}_fk")
            child_column_names = {c.name for c in child_table.columns}
            for group_columns in local_unique.pop(attr_name, []):
                remapped = [child_renamed.get(c, c) for c in group_columns]
                full_columns = [fk_column, *remapped]
                missing = [c for c in full_columns if c not in child_column_names]
                if missing:
                    child_table.notes.append(
                        _diag(
                            "SQL-UNIQUE-COL-UNMAPPED",
                            f"UNIQUE (LOCAL) {attr_name}: column(s) {missing} have no mapped SQL type",
                        )
                    )
                    continue
                name = _truncate_identifier(_sql_identifier(f"uq_{child_table.name}_{'_'.join(full_columns)}"))
                child_table.unique_constraints.append(UniqueConstraint(name, full_columns))

            # `UNIQUE <this attr>->Sub` (global, no per-parent scoping): a
            # plain UNIQUE over the child table's own sub-attribute columns.
            for group_columns in struct_global_unique.pop(attr_name, []):
                remapped = [child_renamed.get(c, c) for c in group_columns]
                missing = [c for c in remapped if c not in child_column_names]
                if missing:
                    cols = ", ".join(group_columns)
                    child_table.notes.append(
                        _diag(
                            "SQL-UNIQUE-COL-UNMAPPED",
                            f"UNIQUE {attr_name}->({cols}): column(s) {missing} have no mapped SQL type",
                        )
                    )
                    continue
                name = _truncate_identifier(_sql_identifier(f"uq_{child_table.name}_{'_'.join(remapped)}"))
                child_table.unique_constraints.append(UniqueConstraint(name, remapped))

            tables.append(child_table)
            if nested_specs:
                tables.extend(
                    _build_nested_child_tables(child_table, nested_specs, home_table, used_table_names, name_tables)
                )

        # An ABSTRACT structure attribute: one child table per concrete subclass (`<parent>_<attr>_<subclass>`, the
        # name is the discriminant). No subclass in the conversion -> a `-- NOTE`.
        for attr_name, abstract_cls, ordered, from_multivalue in abstract_specs:
            subclasses = concrete_structure_subclasses(abstract_cls, home_table, *scan_symbol_tables)
            if not subclasses:
                if from_multivalue:
                    rule, kind = "SQL-BAGLIST-ELEMENT-UNMAPPED", "BAG/LIST OF an ABSTRACT structure"
                else:
                    rule, kind = "SQL-STRUCT-ABSTRACT", "ABSTRACT structure"
                parent_table.notes.append(
                    _diag(
                        rule,
                        f"{attr_name}: {kind} - no concrete subclass in this conversion to build a "
                        f"table per subtype (provide the model that defines them via --repo/--catalog)",
                    )
                )
                continue
            groups_for_attr = local_unique.pop(attr_name, [])
            fk_column = _sql_identifier(f"{table_name}_fk")
            for sub in subclasses:
                sub_name = _sql_identifier(getattr(sub, "Name", None) or "")
                child_name = _dedup_name(_sql_identifier(f"{table_name}_{attr_name}_{sub_name}"), used_table_names)
                child_table, child_renamed = _structure_child_table(
                    table_name, child_name, sub, home_table, ordered=ordered, name_tables=name_tables
                )
                child_table.columns[0].ili_name = child_ili_names.get(attr_name)
                child_column_names = {c.name for c in child_table.columns}
                for group_columns in groups_for_attr:
                    full_columns = [fk_column, *(child_renamed.get(c, c) for c in group_columns)]
                    missing = [c for c in full_columns if c not in child_column_names]
                    if missing:
                        child_table.notes.append(
                            _diag(
                                "SQL-UNIQUE-COL-UNMAPPED",
                                f"UNIQUE (LOCAL) {attr_name}: column(s) {missing} have no mapped SQL type",
                            )
                        )
                        continue
                    name = _truncate_identifier(_sql_identifier(f"uq_{child_table.name}_{'_'.join(full_columns)}"))
                    child_table.unique_constraints.append(UniqueConstraint(name, full_columns))
                tables.append(child_table)

        # A UNIQUE (LOCAL) whose role hop matched no BAG/LIST OF attribute: noted, never silently dropped.
        for attr_name in local_unique:
            parent_table.notes.append(
                _diag("SQL-UNIQUE-LOCAL-UNSUPPORTED", f"UNIQUE (LOCAL) {attr_name}: no matching BAG/LIST OF attribute")
            )

        # `UNIQUE X->Y` whose first hop is not a `BAG`/`LIST OF STRUCTURE` attribute navigates to another table's
        # column; only a BEFORE INSERT/UPDATE trigger could express it.
        for attr_name, groups in struct_global_unique.items():
            for group_columns in groups:
                parent_table.notes.append(
                    _diag(
                        "SQL-UNIQUE-CROSS-REF",
                        f"UNIQUE {attr_name}->({', '.join(group_columns)}): navigates a '->' reference to another "
                        "table - needs a trigger, not a table constraint",
                    )
                )

    # A target from another model is not among `classes`: keep the FK column, drop the dangling constraint.
    # Retarget by identity, since `_columns_for_class` cannot know the suffix a name collision adds.
    converted = [c for c in classes if getattr(c, "Kind", None) == "Class"]
    class_by_id = {id(c): c for c in converted}
    for table in tables:
        for fk in list(table.foreign_keys):
            target = class_by_id.get(fk.ref_class_id) if fk.ref_class_id is not None else None
            if target is None:
                continue
            targets: list[MetaInstance] = []
            for alternative_id in fk.ref_alternative_ids or (fk.ref_class_id,):
                alternative = class_by_id.get(alternative_id)
                for candidate in _reference_target_classes(alternative, converted) if alternative else []:
                    if all(candidate is not t for t in targets):
                        targets.append(candidate)
            if not targets:
                table.foreign_keys.remove(fk)
                table.notes.append(
                    _diag(
                        "SQL-REF-TARGET-UNRESOLVED",
                        f"{', '.join(fk.columns)}: reference to ABSTRACT {getattr(target, 'Name', '?')!r} with no "
                        "concrete subclass in this conversion (FOREIGN KEY dropped, column kept) - provide the model "
                        "that defines them via --repo",
                    )
                )
                continue
            if len(targets) == 1:
                fk.ref_table = table_name_by_class_id[id(targets[0])]
                continue
            _split_polymorphic_fk(
                table, fk, [(t, table_name_by_class_id[id(t)]) for t in targets], keeps_column=targets[0] is target
            )

    # An ABSTRACT class with concrete subclasses becomes a polymorphic VIEW over their tables (INTERLIS views are
    # polymorphic).
    tables_by_name = {t.name: t for t in tables}
    for cls in classes:
        if getattr(cls, "Kind", None) != "Class" or not getattr(cls, "Abstract", False):
            continue
        subclass_tables = [
            tables_by_name[table_name_by_class_id[id(s)]] for s in _reference_target_classes(cls, converted)
        ]
        if not subclass_tables:
            continue
        own = {c.name for c in _columns_for_class(cls, (class_symbol_tables or {}).get(id(cls), symbol_table))[0]}
        shared = [
            column
            for column in subclass_tables[0].columns
            if any(column.name == n or column.name.startswith(f"{n}_") for n in own)
            and all(any(o.name == column.name for o in t.columns) for t in subclass_tables[1:])
        ]
        name = _dedup_name(_sql_identifier(getattr(cls, "Name", None) or ""), used_table_names)
        table_name_by_class_id[id(cls)] = name
        if class_table_names is not None:
            class_table_names[id(cls)] = name
        tables.append(
            Table(
                name=name,
                columns=[Column(c.name, c.sql_type, geometry_type=c.geometry_type, srid=c.srid) for c in shared],
                union_of=[t.name for t in subclass_tables],
            )
        )

    final_table_names = {t.name for t in tables}
    for table in tables:
        kept_fks = []
        for fk in table.foreign_keys:
            if fk.ref_table not in final_table_names:
                table.notes.append(
                    _diag(
                        "SQL-FK-CROSS-MODEL-DROPPED",
                        f"FOREIGN KEY ({', '.join(fk.columns)}): target table {fk.ref_table!r} belongs to a "
                        "different model, not created by this conversion - pass that model via --catalog "
                        "(constraint dropped, column kept)",
                    )
                )
                continue
            kept_fks.append(fk)
        table.foreign_keys = kept_fks
    return tables
