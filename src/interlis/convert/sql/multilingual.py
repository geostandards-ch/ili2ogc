"""LocalisationCH multilingual texts in the SQL layout: one `<table>_<attr>_localisedtext` child table each."""

from __future__ import annotations

from .identifiers import OID_COLUMN, _quote
from .model import Table

_LOCALISED = "_localisedtext"


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


def _text_of(child: Table, fk: str, owner: str, lang: str | None) -> str:
    """The text in exactly `lang` (`None`: the text whose language is undefined), NULL when absent."""
    language = '"c"."language" IS NULL' if lang is None else '"c"."language" = \'' + lang.replace("'", "''") + "'"
    return (
        f'(SELECT "c"."text" FROM {_quote(child.name)} "c" WHERE "c".{_quote(fk)} = {owner}."{OID_COLUMN}" '
        f"AND {language} LIMIT 1)"
    )
