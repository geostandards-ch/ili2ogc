"""Dialect-neutral SQL schema/view intermediate representation.

`build_tables` produces `Table`/`Column`/...; `build_views` produces
`SqlView`/`UniqueViewTrigger`. Every renderer consumes these same
objects. Pure leaf module.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class Column:
    name: str
    sql_type: str
    """A dialect-portable scalar type name (e.g. "text"/"integer"/"varchar(20)") - ignored by every renderer when
    `geometry_type` is set (each renderer formats geometry columns its own way, see `render_postgresql`/`render_gpkg`).
    """
    nullable: bool = True
    geometry_type: str | None = None
    """SFA type name (e.g. "Point", "MultiPolygonZ") - set ONLY for a geometry column, structured (not pre-formatted) so
    each renderer can express it its own way.
    """
    srid: int | None = None
    """EPSG numeric code - set ONLY alongside `geometry_type`."""
    ili_name: str | None = None
    """The INTERLIS attribute/role this column holds (`Model.Topic.Class.Attr`), for T_ILI2DB_ATTRNAME."""
    check: str | None = None
    """A value-domain CHECK on this column alone, `{col}` standing for its quoted name (filled at render time, so a
    later rename still applies)."""
    source: tuple[str, ...] = ()
    """The attribute path this column holds, from its table's class (or BAG element): `("ModInfo", "ValidFrom")` for a
    flattened STRUCTURE, empty for a technical column or a BAG element's own value - what `import` reads data from."""


@dataclass
class ForeignKey:
    name: str
    columns: list[str]
    ref_table: str
    ref_columns: list[str]
    # Identity of the target Class, when the FK points at another class's
    # table rather than its own parent's. `ref_table` is first filled from
    # the target's bare Name, which two classes of the same name (an LV03
    # and an LV95 variant of one base model, say) share - `build_tables`
    # rewrites it from this id once it knows which of them got the
    # suffixed table name.
    ref_class_id: int | None = None
    ref_alternative_ids: tuple[int, ...] = ()
    """Every declared target of a role `-- A OR B` (ids), `ref_class_id` being the first."""
    on_delete: str | None = None
    """`"CASCADE"` for a part's link to its whole (a STRUCTURE child row, or a composition `-<#>` role)."""


@dataclass
class UniqueConstraint:
    name: str
    columns: list[str]


@dataclass
class CheckConstraint:
    name: str
    expression: str
    """A complete SQL boolean expression, already portable across PostgreSQL and SQLite (no dialect-specific syntax) -
    see `expressions.py::_expression_to_sql`.
    """


@dataclass
class Table:
    name: str
    columns: list[Column] = field(default_factory=list)
    unique_constraints: list[UniqueConstraint] = field(default_factory=list)
    foreign_keys: list[ForeignKey] = field(default_factory=list)
    check_constraints: list[CheckConstraint] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    """Human-readable reasons an attribute/constraint was skipped (RULE #5) - never a silent drop."""
    ili_name: str | None = None
    """The INTERLIS class/association this table holds (`Model.Topic.Class`), for T_ILI2DB_CLASSNAME."""
    has_tid: bool = False
    """Whether rows carry a transferred TID (`t_ili_tid`): a class's objects do, structure elements and
    association links don't."""
    parent: str | None = None
    """For a BAG/LIST child table: its parent table, and `source` the attribute path to the BAG from there."""
    source: tuple[str, ...] = ()
    union_of: list[str] = field(default_factory=list)
    """For an ABSTRACT class: the concrete subclass tables this is a polymorphic VIEW over (`UNION ALL` of
    `columns`), rendered as a VIEW, not a table."""


@dataclass
class SqlView:
    name: str
    body: str | None
    """A complete, dialect-portable `SELECT ... FROM ... [WHERE ...]` (comma-join, no dialect-specific syntax), or
    `None` when the View could not be translated - `notes` then says why (RULE #5).
    """
    notes: list[str] = field(default_factory=list)
    triggers: list[UniqueViewTrigger] = field(default_factory=list)
    """A `CREATE VIEW` carries no constraint of its own - a VIEW-level `UNIQUE` that resolves to plain columns of a
    single base table (no reference-hop `extra_joins`, no geometry column) becomes one of these instead of a
    `-- NOTE`, see `views.py::_view_unique_constraint_ddl`.
    """


@dataclass
class UniqueViewTrigger:
    """A VIEW-level `UniqueConstraint` translated into a `BEFORE INSERT`/`BEFORE UPDATE` trigger on its base table.

    Dialect-neutral (`where`/`columns` name only the base table's own
    alias/columns) - each renderer wraps the same predicate in its own
    `CREATE TRIGGER` form.
    """

    view_name: str
    label: str
    base_table: str
    alias: str
    columns: list[str]
    where: list[str]
    """The view's own `WHERE` conjuncts, still qualified with `alias` - reused both as-is (to test whether an
    EXISTING other row is itself part of the view) and with `alias` substituted for the trigger row reference (to
    test whether the row being written is itself part of the view), see `views.py::_view_unique_trigger_predicate`.
    """
