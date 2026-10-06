"""`interlis import`: the objects of XTF transfers as PostgreSQL `INSERT`s into the schema `convert-sql` creates.

ili2db's data layout: one `T_ILI2DB_DATASET` row, one `T_ILI2DB_BASKET` row per basket, each object in its class
table with its basket and TID, references as the target's `t_id`, BAG/LIST elements (multilingual texts included)
in their child tables, geometries from the decoded GeoJSON with arcs stroked and parts gathered into Multi*.
Ids are taken from `t_ili2db_seq` in one block, so the script can run against a schema that already holds data;
foreign keys are DEFERRABLE, so rows go in any order inside the transaction.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from interlis.convert.jsonfg import _members_value
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.parse import XtfTransfer

from .identifiers import BASKET_COLUMN, OID_COLUMN, SEQ_COLUMN, TID_COLUMN, _quote
from .model import Column, Table

_BASE = "(SELECT base FROM _ili_import)"
_SFA_PARTS = {"Point": 1, "LineString": 2, "Polygon": 3}


def _sql_literal(value: Any) -> str:
    if value is None:
        return "NULL"
    if isinstance(value, bool):
        return "TRUE" if value else "FALSE"
    if isinstance(value, (int, float)):
        return repr(value)
    return "'" + str(value).replace("'", "''") + "'"


def _wkt(geometry: dict, tagged: bool = True) -> str:
    """A GeoJSON/JSON-FG geometry, curves included, as WKT (PostGIS's untagged form inside a curved geometry)."""
    kind = geometry["type"]

    def points(coordinates: list) -> str:
        return "(" + ", ".join(" ".join(str(v) for v in xy) for xy in coordinates) + ")"

    def rings(polygon: list) -> str:
        return "(" + ", ".join(points(ring) for ring in polygon) + ")"

    coordinates = geometry.get("coordinates")
    if kind == "Point":
        return f"POINT {points([coordinates])}"
    if kind == "LineString":
        return f"LINESTRING {points(coordinates)}" if tagged else points(coordinates)
    if kind == "CircularString":
        return f"CIRCULARSTRING {points(coordinates)}"
    if kind == "Polygon":
        return f"POLYGON {rings(coordinates)}" if tagged else rings(coordinates)
    if kind == "MultiPoint":
        return "MULTIPOINT (" + ", ".join(points([xy]) for xy in coordinates) + ")"
    if kind == "MultiLineString":
        return "MULTILINESTRING (" + ", ".join(points(line) for line in coordinates) + ")"
    if kind == "MultiPolygon":
        return "MULTIPOLYGON (" + ", ".join(rings(polygon) for polygon in coordinates) + ")"
    nested = kind != "GeometryCollection"  # CompoundCurve, CurvePolygon, MultiCurve, MultiSurface
    return f"{kind.upper()} (" + ", ".join(_wkt(g, tagged=not nested) for g in geometry["geometries"]) + ")"


def _geometries(value: Any) -> list[dict]:
    """Every geometry in `value`: itself, or the parts of a multi-geometry STRUCTURE (`{"Points": [...]}`)."""
    if isinstance(value, dict):
        if isinstance(value.get("type"), str) and ("coordinates" in value or "geometries" in value):
            return [value]
        return [g for v in value.values() for g in _geometries(v)]
    if isinstance(value, list):
        return [g for v in value for g in _geometries(v)]
    return []


def _geometry_sql(column: Column, value: Any) -> str:
    parts = _geometries(value)
    if not parts:
        return "NULL"
    base = (column.geometry_type or "").removeprefix("Multi").rstrip("ZM")
    linear = base != "Point"
    if column.geometry_type and column.geometry_type.startswith("Multi"):
        wkt = parts[0] if len(parts) == 1 else {"type": "GeometryCollection", "geometries": parts}
        sql = f"ST_GeomFromText({_sql_literal(_wkt(wkt))}, {column.srid})"
        if linear:
            sql = f"ST_CurveToLine({sql})"
        return f"ST_Multi(ST_CollectionExtract({sql}, {_SFA_PARTS.get(base, 3)}))"
    sql = f"ST_GeomFromText({_sql_literal(_wkt(parts[0]))}, {column.srid})"
    return f"ST_CurveToLine({sql})" if linear else sql


def _at(value: Any, path: tuple[str, ...]) -> Any:
    """`value` followed along `path`; a catalogue reference already reduced to its TID ends the walk early."""
    for index, step in enumerate(path):
        if not isinstance(value, dict):
            return value if index == len(path) - 1 else None
        value = value.get(step)
    return value


@dataclass
class _Row:
    table: Table
    t_id: int
    basket: int | None
    values: dict[str, Any]
    tid: str | None = None
    parent: int | None = None
    seq: int | None = None
    children: list[_Row] = field(default_factory=list)


def transfer_inserts(
    transfers: list[XtfTransfer],
    tables: list[Table],
    table_of: Callable[[str], tuple[MetaInstance, str, Any] | None],
    *,
    dataset: str,
    attachment: str,
) -> tuple[str, dict[str, int], list[str]]:
    """Return `(sql, rows per table, notes)` for every object of `transfers`, as one dataset.

    `table_of(qualified_class)` gives an object's class, its table and the symbol table declaring the class (for its
    embedded association roles), `None` when that class has no table here.
    A reference to an object absent from the transfers stays NULL (noted).
    """
    by_name = {t.name: t for t in tables}
    children_of: dict[str, list[Table]] = {}
    for t in tables:
        if t.parent:
            children_of.setdefault(t.parent, []).append(t)
    counter = 0
    notes: list[str] = []
    skipped: dict[str, int] = {}

    def next_id() -> int:
        nonlocal counter
        counter += 1
        return counter

    dataset_id = next_id()
    basket_rows: list[tuple[int, str, str]] = []
    rows: list[_Row] = []
    located: dict[str, tuple[str, int]] = {}  # TID -> (table, t_id)
    for transfer in transfers:
        for basket in transfer.baskets:
            basket_id = next_id()
            basket_rows.append((basket_id, basket.qualified_topic, basket.bid))
            for obj in basket.objects:
                found = table_of(obj.qualified_class)
                if found is None or found[1] not in by_name:
                    skipped[obj.qualified_class] = skipped.get(obj.qualified_class, 0) + 1
                    continue
                cls, table_name, home_table = found
                values = _members_value(cls, obj.attributes, symbol_table=home_table)
                row = _Row(by_name[table_name], next_id(), basket_id, values, tid=obj.tid)
                rows.append(row)
                if obj.tid is not None:
                    located[obj.tid] = (table_name, row.t_id)
                _add_children(row, children_of, next_id)
    for qualified_class, count in sorted(skipped.items()):
        notes.append(f"{count} {qualified_class} object(s) skipped: no table for that class in this schema")

    statements = [
        "BEGIN;",
        "CREATE TEMP TABLE _ili_import ON COMMIT DROP AS SELECT nextval('t_ili2db_seq') AS base;",
        f"SELECT setval('t_ili2db_seq', {_BASE} + {counter});",
        f"INSERT INTO T_ILI2DB_DATASET (T_Id, datasetName) VALUES ({_BASE} + {dataset_id}, {_sql_literal(dataset)});",
    ]
    statements += [
        "INSERT INTO T_ILI2DB_BASKET (T_Id, dataset, topic, T_Ili_Tid, attachmentKey) VALUES "
        f"({_BASE} + {bid}, {_BASE} + {dataset_id}, {_sql_literal(topic)}, {_sql_literal(tid)}, "
        f"{_sql_literal(attachment)});"
        for bid, topic, tid in basket_rows
    ]
    unresolved = 0
    counts: dict[str, int] = {}

    def insert(row: _Row, basket: int) -> None:
        nonlocal unresolved
        names = [OID_COLUMN, BASKET_COLUMN]
        values = [f"{_BASE} + {row.t_id}", f"{_BASE} + {basket}"]
        if row.table.has_tid:
            names.append(TID_COLUMN)
            values.append(_sql_literal(row.tid))
        if row.parent is not None:
            names.append(f"{row.table.parent}_fk")
            values.append(f"{_BASE} + {row.parent}")
        if row.seq is not None:
            names.append(SEQ_COLUMN)
            values.append(str(row.seq))
        references = {fk.columns[0]: fk.ref_table for fk in row.table.foreign_keys if len(fk.columns) == 1}
        for column in row.table.columns:
            if column.name in names or (not column.source and column.name != "value"):
                continue
            value = row.values if not column.source else _at(row.values, column.source)
            if column.geometry_type:
                sql = _geometry_sql(column, value)
            elif column.name in references and not isinstance(value, (dict, list)):
                target = located.get(value) if isinstance(value, str) else None
                if value is not None and target is None:
                    unresolved += 1
                sql = f"{_BASE} + {target[1]}" if target and target[0] == references[column.name] else "NULL"
            elif isinstance(value, (dict, list)):
                continue
            else:
                sql = _sql_literal(value)
            if sql != "NULL":
                names.append(column.name)
                values.append(sql)
        columns_sql = ", ".join(_quote(n) for n in names)
        statements.append(f"INSERT INTO {_quote(row.table.name)} ({columns_sql}) VALUES ({', '.join(values)});")
        counts[row.table.name] = counts.get(row.table.name, 0) + 1
        for child in row.children:
            insert(child, basket)

    for row in rows:
        insert(row, row.basket or dataset_id)
    statements.append("COMMIT;")
    if unresolved:
        notes.append(f"{unresolved} reference(s) to an object not in these transfers left NULL")
    return "\n".join(statements) + "\n", counts, notes


def _add_children(row: _Row, children_of: dict[str, list[Table]], next_id: Callable[[], int]) -> None:
    """Child-table rows for each BAG/LIST element of `row` (one level: a multilingual text's LocalisedText...)."""
    for child_table in children_of.get(row.table.name, []):
        elements = _at(row.values, child_table.source)
        if elements is None:
            continue
        if not isinstance(elements, list):
            elements = [elements]
        ordered = any(c.name == SEQ_COLUMN for c in child_table.columns)
        for index, element in enumerate(elements):
            values = element if isinstance(element, dict) else {"value": element}
            child = _Row(child_table, next_id(), None, values, parent=row.t_id, seq=index if ordered else None)
            if not isinstance(element, dict):
                child.values = element  # a scalar/geometry/reference element fills the `value` column itself
            row.children.append(child)
