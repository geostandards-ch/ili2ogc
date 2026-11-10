"""Incremental transfers (INSERT/UPDATE/DELETE objects): replayed like REST calls by `import-xtf`."""

from pathlib import Path

from interlis.cli import main
from interlis.xtf.parse import parse_xtf

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS A =
      Name : MANDATORY TEXT*20;
    END A;
  END T;
END M.
"""

_XTF = """<?xml version="1.0" encoding="UTF-8"?>
<TRANSFER xmlns="http://www.interlis.ch/INTERLIS2.3">
<HEADERSECTION SENDER="x" VERSION="2.3"><MODELS><MODEL NAME="M" VERSION="1" URI="http://x"></MODEL></MODELS></HEADERSECTION>
<DATASECTION>
<M.T BID="b1" KIND="UPDATE">
<M.T.A TID="new"><Name>n</Name></M.T.A>
<M.T.A TID="old" OPERATION="UPDATE"><Name>renamed</Name></M.T.A>
<M.T.A TID="gone" OPERATION="DELETE"></M.T.A>
</M.T>
</DATASECTION>
</TRANSFER>
"""


def _setup(tmp_path: Path) -> Path:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    xtf = tmp_path / "incr.xtf"
    xtf.write_text(_XTF, encoding="utf-8")
    return xtf


def test_parser_keeps_the_operation_per_object(tmp_path: Path):
    objects = parse_xtf(_setup(tmp_path)).baskets[0].objects
    assert [o.operation for o in objects] == [None, "UPDATE", "DELETE"]


def test_import_replays_post_put_delete(tmp_path: Path):
    xtf = _setup(tmp_path)
    out = tmp_path / "o.sql"
    main(["import-xtf", str(xtf), "--model", str(tmp_path / "M.ili"), "--dataset", "ds", "-o", str(out)])
    sql = out.read_text(encoding="utf-8")
    assert "INSERT INTO T_ILI2DB_DATASET" not in sql  # the dataset must already exist
    assert "404 Not Found: dataset ds" in sql
    assert "409 Conflict: INSERT a new" in sql
    assert 'INSERT INTO "a"' in sql and "'new'" in sql
    assert """UPDATE "a" SET "name" = 'renamed' WHERE "t_ili_tid" = 'old'""" in sql
    assert "404 Not Found: UPDATE a old" in sql
    assert 'DELETE FROM "a" WHERE "t_ili_tid" = \'gone\'' in sql
    assert "404 Not Found: DELETE a gone" in sql


def test_insert_only_transfer_still_creates_its_dataset(tmp_path: Path):
    xtf = _setup(tmp_path)
    xtf.write_text(_XTF.replace(' OPERATION="UPDATE"', "").replace(' OPERATION="DELETE"', ""), encoding="utf-8")
    out = tmp_path / "o.sql"
    main(["import-xtf", str(xtf), "--model", str(tmp_path / "M.ili"), "-o", str(out)])
    sql = out.read_text(encoding="utf-8")
    assert "INSERT INTO T_ILI2DB_DATASET" in sql and "409 Conflict" not in sql


def test_validate_skips_attribute_checks_of_a_delete(tmp_path: Path):
    """A DELETE carries only its TID: the MANDATORY `Name` must not be reported missing."""
    xtf = _setup(tmp_path)
    out = tmp_path / "r.txt"
    code = main(["validate-xtf", str(xtf), "--model", str(tmp_path / "M.ili"), "--report", str(out)])
    assert code == 0, out.read_text(encoding="utf-8") if out.exists() else ""


def test_convert_jsonfg_warns_that_operations_are_not_carried(tmp_path: Path, capsys):
    xtf = _setup(tmp_path)
    main(["convert-jsonfg", str(xtf), "--model", str(tmp_path / "M.ili"), "-o", str(tmp_path / "o.json")])
    assert "JSONFG-INCREMENTAL-UNSUPPORTED" in capsys.readouterr().err
