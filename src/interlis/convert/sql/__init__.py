"""IlisMeta16 -> SQL DDL conversion: tables, columns, constraints and views.

Unsupported constructs are collected into `Table.notes` and rendered as `-- NOTE`, never dropped silently.
"""

from __future__ import annotations

from .identifiers import OID_COLUMN, _sql_identifier, _truncate_identifier
from .model import CheckConstraint, Column, ForeignKey, SqlView, Table, UniqueConstraint, UniqueViewTrigger
from .render import collect_diagnostics, render_gpkg, render_postgresql
from .tables import build_tables
from .views import build_views

__all__ = [
    "OID_COLUMN",
    "_sql_identifier",
    "_truncate_identifier",
    "Column",
    "ForeignKey",
    "UniqueConstraint",
    "CheckConstraint",
    "Table",
    "SqlView",
    "UniqueViewTrigger",
    "build_tables",
    "build_views",
    "render_postgresql",
    "render_gpkg",
    "collect_diagnostics",
]
