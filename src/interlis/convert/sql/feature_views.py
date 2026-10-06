"""`convert-sql --feature-views LANG`: one readable view per class table, for feature services.

Tables hold references as the target's `t_id` and multilingual texts in child tables; the view shows
the target's key and names instead, and every text in the chosen language.
"""

from __future__ import annotations

from .identifiers import OID_COLUMN, TID_COLUMN, _dedup_name, _quote, _truncate_identifier
from .model import SqlView, Table

_LOCALISED = "_localisedtext"
_BASE = '"g"'


def _localised_texts(table: Table, tables: list[Table]) -> list[tuple[str, Table, str]]:
    """`(attribute, child table, fk column)` for each LocalisationCH multilingual attribute of `table`.

    Recognised by shape: a child table `<table>_<attr>_localisedtext` with `language`/`text` columns and a
    `<table>_fk` back to it (`MultilingualText`/`MText`/`Uri`'s `LocalisedText` BAG).
    """
    fk = f"{table.name}_fk"
    found = []
    for child in tables:
        names = {c.name for c in child.columns}
        if (
            child.name.startswith(f"{table.name}_")
            and child.name.endswith(_LOCALISED)
            and {fk, "language", "text"} <= names
            and any(f.columns == [fk] and f.ref_table == table.name for f in child.foreign_keys)
        ):
            found.append((child.name[len(table.name) + 1 : -len(_LOCALISED)], child, fk))
    return found


def _text_in(child: Table, fk: str, owner: str, lang: str) -> str:
    """The text in `lang`, else in the first other language present (a value need not carry every language)."""
    literal = "'" + lang.replace("'", "''") + "'"
    return (
        f'(SELECT "c"."text" FROM {_quote(child.name)} "c" WHERE "c".{_quote(fk)} = {owner}."{OID_COLUMN}" '
        f'ORDER BY "c"."language" = {literal} DESC, "c"."language" LIMIT 1)'
    )


def _text_of(child: Table, fk: str, owner: str, lang: str | None) -> str:
    """The text in exactly `lang` (`None`: the text whose language is undefined), NULL when absent."""
    language = '"c"."language" IS NULL' if lang is None else '"c"."language" = \'' + lang.replace("'", "''") + "'"
    return (
        f'(SELECT "c"."text" FROM {_quote(child.name)} "c" WHERE "c".{_quote(fk)} = {owner}."{OID_COLUMN}" '
        f"AND {language} LIMIT 1)"
    )


def build_feature_views(tables: list[Table], lang: str, used_names: set[str]) -> list[SqlView]:
    """One `<table>_features` view per class table with a reference or a multilingual text to make readable.

    A reference `<ref>` becomes `<ref>_<key>` (the target's UNIQUE key, else `<ref>_tid` and its plain attributes)
    and `<ref>_<attr>` per multilingual target attribute, LEFT JOINed; own multilingual attributes become `<attr>`.
    """
    by_name = {t.name: t for t in tables}
    result = []
    for table in tables:
        if not table.ili_name or table.union_of or not table.has_tid:
            continue
        references = {
            fk.columns[0]: by_name[fk.ref_table]
            for fk in table.foreign_keys
            if len(fk.columns) == 1 and fk.ref_table in by_name
        }
        own_texts = _localised_texts(table, tables)
        if not references and not own_texts:
            continue
        items = [f'"g"."{OID_COLUMN}"', f'"g"."{TID_COLUMN}"']
        joins = []
        # Own columns keep their names; the readable columns added beside them yield on a clash.
        columns = {OID_COLUMN, TID_COLUMN, *(c.name for c in table.columns if c.name not in references)}

        def added(base: str) -> str:
            return _quote(_dedup_name(_truncate_identifier(base), columns))

        for column in table.columns:
            target = references.get(column.name)
            if target is None:
                items.append(f'"g".{_quote(column.name)}')
                continue
            alias = f'"j{len(joins) + 1}"'
            joins.append(
                f'LEFT JOIN {_quote(target.name)} {alias} ON {alias}."{OID_COLUMN}" = "g".{_quote(column.name)}'
            )
            key = next((u.columns for u in target.unique_constraints), None)
            if not key:
                # No declared key: the TID, and the target's plain attributes to recognise it by.
                if target.has_tid:
                    items.append(f'{alias}."{TID_COLUMN}" AS {added(f"{column.name}_tid")}')
                links = {c for fk in target.foreign_keys for c in fk.columns}
                key = [
                    c.name
                    for c in target.columns
                    if not c.geometry_type and c.sql_type != "bytea" and c.name not in links
                ]
            items += [f"{alias}.{_quote(k)} AS {added(f'{column.name}_{k}')}" for k in key]
            for attribute, child, fk in _localised_texts(target, tables):
                items.append(f"{_text_in(child, fk, alias, lang)} AS {added(f'{column.name}_{attribute}')}")
        for attribute, child, fk in own_texts:
            items.append(f"{_text_in(child, fk, _BASE, lang)} AS {added(attribute)}")
        name = _dedup_name(_truncate_identifier(f"{table.name}_features"), used_names)
        body = "SELECT\n    " + ",\n    ".join(items) + f'\nFROM {_quote(table.name)} "g"'
        body += "".join(f"\n{join}" for join in joins)
        result.append(SqlView(name, body))
    return result
