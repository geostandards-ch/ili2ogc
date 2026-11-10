"""Value domains on SQL columns: closed enumerations and numeric ranges as CHECKs, decimal precision, binary blobs."""

from pathlib import Path

from interlis.cli import main

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS A =
      Kind : (low, high (very, extreme));
      Width : 0.00 .. 1000.00;
      Count : 0 .. 26;
      Data : BLACKBOX BINARY;
    END A;
  END T;
END M.
"""


def _table(tmp_path: Path) -> str:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    out = tmp_path / "out.sql"
    main(["convert-sql", str(tmp_path / "M.ili"), "--repo", str(tmp_path), "-o", str(out)])
    return out.read_text(encoding="utf-8").split('CREATE TABLE "a" (', 1)[1].split("\n);", 1)[0]


def test_closed_enumeration_check_lists_every_path(tmp_path: Path):
    assert """CHECK ("kind" IN ('high', 'high.extreme', 'high.very', 'low'))""" in _table(tmp_path)


def test_numeric_range_check_and_precision(tmp_path: Path):
    table = _table(tmp_path)
    assert '"width" numeric(6,2)' in table
    assert 'CHECK ("width" BETWEEN 0.00 AND 1000.00)' in table
    assert 'CHECK ("count" BETWEEN 0 AND 26)' in table


def test_binary_blackbox_is_bytea(tmp_path: Path):
    assert '"data" bytea' in _table(tmp_path)


def test_structured_domain_sits_next_to_the_check_string(tmp_path: Path):
    from conftest import build_from_text

    from interlis.builder.repository import ModelRepository
    from interlis.convert.sql.tables import build_tables
    from interlis.xtf.schema import resolve_class

    builder = build_from_text(_MODEL)
    cls = resolve_class("M.T.A", symbol_table=builder.symbol_table, repository=ModelRepository([tmp_path]))
    columns = {c.name: c for c in build_tables([cls], symbol_table=builder.symbol_table)[0].columns}
    assert columns["kind"].domain.enum_values == ("high", "high.extreme", "high.very", "low")
    assert (columns["width"].domain.minimum, columns["width"].domain.maximum) == ("0.00", "1000.00")
    assert columns["data"].domain is None
