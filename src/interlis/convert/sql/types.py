"""INTERLIS attribute type -> SQL column type mapping: scalar types and geometry (SFA/PostGIS) columns.

A pure leaf module (only `tables.py` calls into it) - no other submodule
of this package needed to build it.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from typing import NamedTuple

from interlis.builder.forward_refs import SymbolTable
from interlis.builder.repository import ModelRepository
from interlis.xtf.crs import reason_rule, resolve_crs
from interlis.xtf.schema import (
    ResolvedAttribute,
    coord_axes,
    enum_values,
    is_integer_range,
    line_coord_type,
)

_REPOSITORY: ContextVar[ModelRepository | None] = ContextVar("_REPOSITORY", default=None)


@contextlib.contextmanager
def crs_repository(repository: ModelRepository | None) -> Iterator[None]:
    """Make `repository` the one whose `CONTEXT` rebindings resolve a geometry column's CRS, inside the block."""
    token = _REPOSITORY.set(repository)
    try:
        yield
    finally:
        _REPOSITORY.reset(token)


class GeometryColumnInfo(NamedTuple):
    """What `_geometry_column_info` found: a column type and SRID, or the `(rule, message)` that explains its absence.

    `warnings` are `(rule, message)` findings about the CRS annotation that did not prevent the column.
    """

    sfa_type: str | None
    srid: int | None
    problem: tuple[str, str] | None
    warnings: tuple[tuple[str, str], ...] = ()


def _geometry_column_info(
    resolved: ResolvedAttribute,
    symbol_table: SymbolTable | None = None,
    repository: ModelRepository | None = None,
) -> GeometryColumnInfo:
    """The SFA type and SRID of a CoordType/LineType attribute's column, or why it has none (CRS never guessed)."""
    if resolved.type_kind == "CoordType":
        coord_type = resolved.type_instance
        sfa = "Point"
    elif resolved.type_kind == "LineType":
        line_type = resolved.type_instance
        coord_type = line_coord_type(line_type)
        kind = getattr(line_type, "Kind", None)
        sfa = "LineString" if kind in ("Polyline", "DirectedPolyline") else "Polygon"
    else:
        return GeometryColumnInfo(None, None, ("SQL-GEOM-NO-CRS", "not a geometry type"))
    resolution = resolve_crs(coord_type, symbol_table=symbol_table, repository=repository or _REPOSITORY.get())
    if resolution.epsg is None:
        rule = reason_rule(resolution.reason or "", "SQL")
        message = resolution.detail
        if rule == "SQL-GEOM-NO-CRS":
            message += " - provide the geometry base model via --repo"
            if resolution.reason == "generic-unbound":
                message = message.replace("base model via", "base model or a CONTEXT via")
        return GeometryColumnInfo(None, None, (rule, message))
    if len(coord_axes(coord_type)) >= 3:
        sfa += "Z"
    if bool(getattr(resolved.type_instance, "Multi", False)):
        sfa = "Multi" + sfa
    return GeometryColumnInfo(sfa, resolution.epsg, None, resolution.warnings)


def _scalar_sql_type(resolved: ResolvedAttribute) -> str | None:
    """Return a scalar SQL column type, or `None` if `resolved` isn't one of the mapped scalar kinds."""
    kind = resolved.type_kind
    inst = resolved.type_instance
    if kind == "NumType" and inst is not None:
        if is_integer_range(getattr(inst, "Min", None), getattr(inst, "Max", None)):
            return "integer"
        precision = _decimal_precision(getattr(inst, "Min", None), getattr(inst, "Max", None))
        return f"numeric({precision[0]},{precision[1]})" if precision else "numeric"
    if kind == "TextType" and inst is not None:
        text_kind = getattr(inst, "Kind", None)
        if text_kind == "Name":
            return "varchar(255)"
        if text_kind == "Uri":
            return "varchar(1023)"
        max_length = getattr(inst, "MaxLength", None)
        if max_length is not None:
            try:
                return f"varchar({int(max_length)})"
            except (TypeError, ValueError):
                pass
        return "text"
    if kind == "EnumType":
        return "text"
    if kind == "BooleanType":
        return "boolean"
    if kind == "FormattedType" and inst is not None:
        return {"XMLDate": "date", "XMLTime": "time", "XMLDateTime": "timestamp"}.get(
            getattr(inst, "Format", None), "text"
        )
    if kind == "BlackboxType":
        return "bytea" if getattr(inst, "Kind", None) == "Binary" else "text"
    return None


def _numeric_bound(raw: str | None) -> str | None:
    """`raw` as a SQL numeric literal (no leading '+'), or `None` when it is not a plain number."""
    if raw is None:
        return None
    text = raw.strip().removeprefix("+")
    try:
        float(text)
    except ValueError:
        return None
    return text


def _decimal_precision(min_raw: str | None, max_raw: str | None) -> tuple[int, int] | None:
    """`(precision, scale)` covering both bounds' digits (`0.0 .. 10.0` -> `(3, 1)`), as ili2db derives decimal(p,s)."""
    bounds = [_numeric_bound(b) for b in (min_raw, max_raw)]
    if None in bounds or any("e" in b.lower() for b in bounds):
        return None
    parts = [b.lstrip("-").partition(".") for b in bounds]
    scale = max(len(frac) for _, _, frac in parts)
    integer_digits = max(len(whole.lstrip("0")) or 1 for whole, _, _ in parts)
    return integer_digits + scale, scale


def _domain_check(resolved: ResolvedAttribute) -> str | None:
    """A CHECK template (`{col}` = the column) for a numeric range or an enumeration, else `None`.

    Each table carries its own class's attribute type (an EXTENDED attribute's extended enumeration in a
    subclass table), so the values listed are exactly those its rows may hold.
    """
    inst = resolved.type_instance
    if resolved.type_kind == "NumType" and inst is not None:
        low, high = _numeric_bound(getattr(inst, "Min", None)), _numeric_bound(getattr(inst, "Max", None))
        return f"{{col}} BETWEEN {low} AND {high}" if low is not None and high is not None else None
    if resolved.type_kind == "EnumType" and inst is not None:
        values = enum_values(inst)
        if not values:
            return None
        quoted = ", ".join("'" + v.replace("'", "''") + "'" for v in sorted(values))
        return f"{{col}} IN ({quoted})"
    return None
