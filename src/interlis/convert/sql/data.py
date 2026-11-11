"""`interlis import-xtf`: the objects of XTF transfers as PostgreSQL `INSERT`s into the schema `convert-sql` creates.

ili2db's data layout: one `T_ILI2DB_DATASET` row, one `T_ILI2DB_BASKET` row per basket, each object in its class
table with its basket and TID, references as the target's `t_id`, BAG/LIST elements (multilingual texts included)
in their child tables, geometries from the decoded GeoJSON with arcs stroked and parts gathered into Multi*.
Ids are taken from `t_ili2db_seq` in one block, so the script can run against a schema that already holds data;
foreign keys are DEFERRABLE, so rows go in any order inside the transaction.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator
from dataclasses import dataclass, field
from itertools import chain
from pathlib import Path
from typing import Any, TextIO

from interlis.convert.jsonfg import members_value
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.parse import XtfTransfer, iter_events

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


def _raise(message: str) -> str:
    return f"RAISE EXCEPTION '%', {_sql_literal(message)};"


def _guard(condition: str, message: str) -> str:
    """A PL/pgSQL block aborting the transaction with `message` when `condition` holds."""
    return f"DO $$ BEGIN IF {condition} THEN {_raise(message)} END IF; END $$;"


def _guard_delete(target: str, message: str) -> str:
    """A `DELETE FROM target` aborting the transaction with `message` when it removes no row."""
    return f"DO $$ BEGIN DELETE FROM {target}; IF NOT FOUND THEN {_raise(message)} END IF; END $$;"


def _guard_update(statement: str, message: str) -> str:
    """`statement` (an UPDATE) aborting the transaction with `message` when it touches no row."""
    return f"DO $$ BEGIN {statement} IF NOT FOUND THEN {_raise(message)} END IF; END $$;"


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
    operation: str = "INSERT"


class _Sink:
    """The `statements` list of a streamed import: each statement goes straight to the output file."""

    def __init__(self, out: TextIO):
        self._out = out

    def append(self, statement: str) -> None:
        self._out.write(statement + "\n")

    def __iadd__(self, statements: list[str]) -> _Sink:
        for statement in statements:
            self.append(statement)
        return self


@dataclass
class _DeleteAnywhere:
    """A `DeleteObject`: remove the object with this TID, whichever class table holds it."""

    tid: str | None


def _delete_anywhere(tables: list[Table], tid: str | None, scope: str) -> str:
    """One block deleting `tid` from every table of `tables`, aborting with a 404 when none held it."""
    deletes = " ".join(
        f"DELETE FROM {_quote(t.name)} WHERE {_quote(TID_COLUMN)} = {_sql_literal(tid)} AND {scope}; "
        "GET DIAGNOSTICS c = ROW_COUNT; n := n + c;"
        for t in tables
    )
    return (
        f"DO $$ DECLARE n bigint := 0; c bigint; BEGIN {deletes} "
        f"IF n = 0 THEN {_raise(f'404 Not Found: DeleteObject {tid}')} END IF; END $$;"
    )


def transfer_inserts(
    transfers: list[XtfTransfer] | list[Path],
    tables: list[Table],
    table_of: Callable[[str], tuple[MetaInstance, str, Any] | None],
    *,
    dataset: str,
    attachment: str,
    out: TextIO | None = None,
) -> tuple[str, dict[str, int], list[str]]:
    """Return `(sql, rows per table, notes)` for every object of `transfers`, as one dataset.

    `transfers` are parsed transfers, or paths read in two passes (flat memory: one pass numbers the objects and
    indexes their TIDs, the second renders each statement straight into `out`, which then gets the SQL, not `sql`).

    A transfer holding `UPDATE`/`DELETE` objects, or a basket of KIND `UPDATE`/`INCREMENTAL`, is incremental and is
    replayed like a sequence of REST calls on an EXISTING dataset (`dataset` must name it): `INSERT` = POST (409 if
    the TID is already there), `UPDATE` = PUT (the object is replaced in place, 404 if absent), `DELETE` = DELETE
    (404 if absent). A failing call aborts and rolls back the whole transaction. A full transfer is a POST of a
    new dataset: 409 if `dataset` already exists.
    `table_of(qualified_class)` gives an object's class, its table and the symbol table declaring the class (for its
    embedded association roles), `None` when that class has no table here.
    A reference to an object absent from the transfers stays NULL (noted).
    """
    if transfers and isinstance(transfers[0], Path) and out is None:
        raise ValueError("reading paths requires an `out` stream")

    def events() -> Iterator[tuple[str, Any]]:
        if out is not None:
            yield from chain.from_iterable(iter_events(path) for path in transfers)
            return
        for transfer in transfers:
            for basket in transfer.baskets:
                yield "basket", basket
                for obj in basket.objects:
                    yield "object", (basket, obj)

    by_name = {t.name: t for t in tables}
    children_of: dict[str, list[Table]] = {}
    for t in tables:
        if t.parent:
            children_of.setdefault(t.parent, []).append(t)
    notes: list[str] = []
    skipped: dict[str, int] = {}
    dataset_id = 1
    basket_rows: list[tuple[int, str, str]] = []
    located: dict[str, tuple[str, int]] = {}  # TID -> (table, t_id)
    incremental = False
    counter = 0

    def walk() -> Iterator[_Row | _DeleteAnywhere]:
        """Every row of the transfers in order, numbered as a single pass would: the same on each call."""
        nonlocal counter, incremental
        counter = dataset_id
        basket_rows.clear()
        skipped.clear()

        def next_id() -> int:
            nonlocal counter
            counter += 1
            return counter

        basket_id = dataset_id
        for kind, payload in events():
            if kind == "basket":
                basket = payload
                basket_id = next_id()
                basket_rows.append((basket_id, basket.qualified_topic, basket.bid))
                incremental = incremental or (basket.kind or "").upper() in ("UPDATE", "INCREMENTAL")
                continue
            _basket, obj = payload
            if obj.operation == "DELETE" and not obj.qualified_class:
                incremental = True
                yield _DeleteAnywhere(obj.tid)  # a DeleteObject: the class is unknown, search every table
                continue
            found = table_of(obj.qualified_class)
            if found is None or found[1] not in by_name:
                skipped[obj.qualified_class] = skipped.get(obj.qualified_class, 0) + 1
                continue
            cls, table_name, home_table = found
            operation = obj.operation or "INSERT"
            incremental = incremental or operation in ("UPDATE", "DELETE")
            by_roles = not by_name[table_name].has_tid
            values = (
                {}
                if operation == "DELETE" and not by_roles
                else members_value(cls, obj.attributes, symbol_table=home_table)
            )
            row = _Row(by_name[table_name], next_id(), basket_id, values, tid=obj.tid, operation=operation)
            if obj.tid is not None and operation == "INSERT":
                located[obj.tid] = (table_name, row.t_id)
            if operation != "DELETE":
                _add_children(row, children_of, next_id)
            yield row

    if out is None:
        rows = list(walk())
    else:
        for _row in walk():  # first pass: numbering and TID index only, no row kept
            pass
    for qualified_class, count in sorted(skipped.items()):
        notes.append(f"{count} {qualified_class} object(s) skipped: no table for that class in this schema")

    statements: Any = _Sink(out) if out is not None else []
    statements += [
        "BEGIN;",
        "CREATE TEMP TABLE _ili_import ON COMMIT DROP AS SELECT nextval('t_ili2db_seq') AS base;",
        f"SELECT setval('t_ili2db_seq', {_BASE} + {counter});",
    ]
    baskets_by_id = {bid: tid for bid, _topic, tid in basket_rows}
    if incremental:
        in_dataset = "(SELECT id FROM _ili_dataset)"
        scope = f"{_quote(BASKET_COLUMN)} IN (SELECT T_Id FROM T_ILI2DB_BASKET WHERE dataset = {in_dataset})"
        statements += [
            "CREATE TEMP TABLE _ili_dataset ON COMMIT DROP AS SELECT T_Id AS id FROM T_ILI2DB_DATASET "
            f"WHERE datasetName = {_sql_literal(dataset)};",
            _guard("NOT EXISTS (SELECT 1 FROM _ili_dataset)", f"404 Not Found: dataset {dataset}"),
        ]
        statements += [
            "INSERT INTO T_ILI2DB_BASKET (T_Id, dataset, topic, T_Ili_Tid, attachmentKey) "
            f"SELECT {_BASE} + {bid}, {in_dataset}, {_sql_literal(topic)}, {_sql_literal(tid)}, "
            f"{_sql_literal(attachment)} WHERE NOT EXISTS (SELECT 1 FROM T_ILI2DB_BASKET "
            f"WHERE dataset = {in_dataset} AND T_Ili_Tid = {_sql_literal(tid)});"
            for bid, topic, tid in basket_rows
        ]
    else:
        statements.append(
            _guard(
                f"EXISTS (SELECT 1 FROM T_ILI2DB_DATASET WHERE datasetName = {_sql_literal(dataset)})",
                f"409 Conflict: dataset {dataset} already exists",
            )
        )
        statements.append(
            "INSERT INTO T_ILI2DB_DATASET (T_Id, datasetName) "
            f"VALUES ({_BASE} + {dataset_id}, {_sql_literal(dataset)});"
        )
        statements += [
            "INSERT INTO T_ILI2DB_BASKET (T_Id, dataset, topic, T_Ili_Tid, attachmentKey) VALUES "
            f"({_BASE} + {bid}, {_BASE} + {dataset_id}, {_sql_literal(topic)}, {_sql_literal(tid)}, "
            f"{_sql_literal(attachment)});"
            for bid, topic, tid in basket_rows
        ]

    def basket_ref(basket: int) -> str:
        if not incremental:
            return f"{_BASE} + {basket}"
        return (
            f"(SELECT T_Id FROM T_ILI2DB_BASKET WHERE dataset = {in_dataset} "
            f"AND T_Ili_Tid = {_sql_literal(baskets_by_id[basket])})"
        )

    def of_tid(row: _Row) -> str:
        return f"{_quote(row.table.name)} WHERE {_quote(TID_COLUMN)} = {_sql_literal(row.tid)} AND {scope}"

    unresolved = 0
    counts: dict[str, int] = {}

    def insert_by_roles(row: _Row) -> None:
        """DELETE of a link without OID: it is the one joining the objects its roles name (UPDATE has no identity)."""
        if row.operation == "UPDATE":
            notes.append(f"UPDATE of a link in {row.table.name} skipped: a link without OID cannot be identified")
            return
        references = {fk.columns[0]: fk.ref_table for fk in row.table.foreign_keys if len(fk.columns) == 1}
        conditions = []
        for column in row.table.columns:
            value = _at(row.values, column.source) if column.source else None
            if column.name in references and isinstance(value, str):
                conditions.append(
                    f"{_quote(column.name)} = (SELECT T_Id FROM {_quote(references[column.name])} "
                    f"WHERE {_quote(TID_COLUMN)} = {_sql_literal(value)} AND {scope})"
                )
        if not conditions:
            notes.append(f"DELETE of a link in {row.table.name} skipped: it names no referenced object")
            return
        where = " AND ".join(conditions)
        statements.append(
            _guard_delete(
                f"{_quote(row.table.name)} WHERE {where} AND {scope}", f"404 Not Found: DELETE {row.table.name}"
            )
        )
        counts[row.table.name] = counts.get(row.table.name, 0) + 1

    def insert(row: _Row, basket: int, parent_ref: str | None = None) -> None:
        nonlocal unresolved
        update = row.operation == "UPDATE"
        if not row.table.has_tid and row.operation in ("UPDATE", "DELETE") and not row.table.parent:
            insert_by_roles(row)
            return
        if row.operation == "DELETE":
            statements.append(
                _guard_delete(of_tid(row), f"404 Not Found: DELETE {row.table.name} {row.tid}"),
            )
            counts[row.table.name] = counts.get(row.table.name, 0) + 1
            return
        if incremental and row.operation == "INSERT" and row.tid is not None and row.table.has_tid:
            statements.append(
                _guard(f"EXISTS (SELECT 1 FROM {of_tid(row)})", f"409 Conflict: INSERT {row.table.name} {row.tid}")
            )
        names = [OID_COLUMN, BASKET_COLUMN]
        values = [f"{_BASE} + {row.t_id}", basket_ref(basket)]
        if row.table.has_tid:
            names.append(TID_COLUMN)
            values.append(_sql_literal(row.tid))
        if row.parent is not None:
            names.append(f"{row.table.parent}_fk")
            values.append(parent_ref or f"{_BASE} + {row.parent}")
        if row.seq is not None:
            names.append(SEQ_COLUMN)
            values.append(str(row.seq))
        technical = set(names)
        references = {fk.columns[0]: fk.ref_table for fk in row.table.foreign_keys if len(fk.columns) == 1}
        for column in row.table.columns:
            if column.name in names or (not column.source and column.name != "value"):
                continue
            value = row.values if not column.source else _at(row.values, column.source)
            if column.geometry_type:
                sql = _geometry_sql(column, value)
            elif column.name in references and not isinstance(value, (dict, list)):
                target = located.get(value) if isinstance(value, str) else None
                if target and target[0] == references[column.name]:
                    sql = f"{_BASE} + {target[1]}"
                elif incremental and isinstance(value, str):
                    sql = (
                        f"(SELECT T_Id FROM {_quote(references[column.name])} "
                        f"WHERE {_quote(TID_COLUMN)} = {_sql_literal(value)} AND {scope})"
                    )
                else:
                    if value is not None and target is None:
                        unresolved += 1
                    sql = "NULL"
            elif isinstance(value, (dict, list)):
                continue
            else:
                sql = _sql_literal(value)
            if sql != "NULL" or update:
                names.append(column.name)
                values.append(sql)
        if update:
            assignments = ", ".join(f"{_quote(n)} = {v}" for n, v in zip(names, values) if n not in technical)
            body = f"UPDATE {_quote(row.table.name)} SET {assignments} WHERE {_quote(TID_COLUMN)} = " + (
                f"{_sql_literal(row.tid)} AND {scope};"
            )
            statements.append(_guard_update(body, f"404 Not Found: UPDATE {row.table.name} {row.tid}"))
            own = f"(SELECT {_quote(OID_COLUMN)} FROM {of_tid(row)})"
            for child_table in children_of.get(row.table.name, []):
                statements.append(
                    f"DELETE FROM {_quote(child_table.name)} WHERE {_quote(row.table.name + '_fk')} = {own};"
                )
            for child in row.children:
                insert(child, basket, own)
        else:
            columns_sql = ", ".join(_quote(n) for n in names)
            statements.append(f"INSERT INTO {_quote(row.table.name)} ({columns_sql}) VALUES ({', '.join(values)});")
            for child in row.children:
                insert(child, basket)
        counts[row.table.name] = counts.get(row.table.name, 0) + 1

    for row in rows if out is None else walk():
        if isinstance(row, _DeleteAnywhere):
            if row.tid is None:
                notes.append("a DeleteObject without a TID was skipped")
            else:
                statements.append(_delete_anywhere([t for t in tables if t.has_tid and not t.parent], row.tid, scope))
            continue
        insert(row, row.basket or dataset_id)

    statements.append("COMMIT;")
    if unresolved:
        notes.append(f"{unresolved} reference(s) to an object not in these transfers left NULL")
    return ("" if out is not None else "\n".join(statements) + "\n"), counts, notes


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
