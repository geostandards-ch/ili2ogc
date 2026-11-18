"""SQL identifier naming: lowercase folding, length truncation, reserved-word quoting. Pure leaf module."""

from __future__ import annotations

import hashlib

# ili2db's technical columns: a schema-wide surrogate `t_id` (the FK target), `t_basket`, and the transferred TID
# in `t_ili_tid`, so several datasets can share one schema.
OID_COLUMN = "t_id"
BASKET_COLUMN = "t_basket"
TID_COLUMN = "t_ili_tid"
SEQ_COLUMN = "t_seq"
SEQUENCE = "t_ili2db_seq"
RESERVED_COLUMNS = frozenset({OID_COLUMN, BASKET_COLUMN, TID_COLUMN})
"""Added to every table by the renderers - an attribute column of the same name must be renamed."""

_MAX_IDENTIFIER_LENGTH = 63  # PostgreSQL's own identifier length limit - a real ceiling, not an arbitrary one.


def _dedup_name(base: str, used: set[str]) -> str:
    """Return `base`, or `base_2`/`base_3`/... if already in `used`; records the result in `used`."""
    name = base
    suffix = 2
    while name in used:
        name = f"{base}_{suffix}"
        suffix += 1
    used.add(name)
    return name


def _sql_identifier(name: str) -> str:
    """Lowercase an INTERLIS `Name` into the identifier PostgreSQL/GDAL would produce anyway."""
    return name.lower()


def _truncate_identifier(name: str) -> str:
    """Truncate `name` to PostgreSQL's identifier limit with a hash tail, avoiding prefix collisions."""
    if len(name) <= _MAX_IDENTIFIER_LENGTH:
        return name
    digest = hashlib.sha1(name.encode("utf-8")).hexdigest()[:8]  # noqa: S324 - collision-avoidance, not security
    keep = _MAX_IDENTIFIER_LENGTH - len(digest) - 1
    return f"{name[:keep]}_{digest}"


def _quote(name: str) -> str:
    """Double-quote a table/column identifier (ANSI SQL), so a reserved word such as `Union` is safe."""
    return f'"{name}"'


def _quote_list(names: list[str]) -> str:
    return ", ".join(_quote(n) for n in names)


def _index_name(table: str, column: str) -> str:
    return _truncate_identifier(f"idx_{table}_{column}")
