"""An ABSTRACT class has no table of its own, only a polymorphic VIEW over its concrete subclasses' tables."""

from pathlib import Path

from interlis.cli import main

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS Base (ABSTRACT) =
      Code : MANDATORY TEXT*5;
    END Base;
    CLASS One EXTENDS Base =
      Extra : TEXT*5;
    END One;
    CLASS Two EXTENDS Base =
    END Two;
    CLASS Holder =
      base : REFERENCE TO Base;
    END Holder;
  END T;
END M.
"""


def _sql(tmp_path: Path) -> str:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    out = tmp_path / "out.sql"
    main(["convert-sql", str(tmp_path / "M.ili"), "--repo", str(tmp_path), "-o", str(out)])
    return out.read_text(encoding="utf-8")


def test_abstract_class_is_a_union_view(tmp_path: Path):
    sql = _sql(tmp_path)
    assert 'CREATE TABLE "base"' not in sql
    assert 'CREATE VIEW "base" AS SELECT "id", "code" FROM "one" UNION ALL SELECT "id", "code" FROM "two";' in sql


def test_reference_to_abstract_class_targets_its_concrete_tables(tmp_path: Path):
    sql = _sql(tmp_path)
    assert 'REFERENCES "one" ("id")' in sql
    assert 'REFERENCES "two" ("id")' in sql
    assert 'REFERENCES "base"' not in sql
