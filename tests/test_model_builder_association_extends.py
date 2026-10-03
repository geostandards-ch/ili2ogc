"""A derived association (`ASSOCIATION X EXTENDS Y`) and an association's own attributes.

The `Name : type ;` lines of an association are attributes, not roles: the
grammar parses them through roleDef's second form.
"""

from pathlib import Path

from conftest import build_from_text as _build

from interlis.cli import main
from interlis.xtf.schema import association_roles, attributes_of, resolve_attribute

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS A =
      Code : TEXT*5;
    END A;
    CLASS B =
      Code : TEXT*5;
    END B;
    ASSOCIATION Base (ABSTRACT) =
      a -- {0..*} A;
      b -- {0..*} B;
      Weight : MANDATORY 0 .. 10;
    END Base;
    ASSOCIATION Derived EXTENDS Base =
      Extra : TEXT*5;
    END Derived;
  END T;
END M.
"""


def test_derived_association_inherits_roles_and_attributes():
    builder = _build(_MODEL)
    base = builder.symbol_table.resolve("M.T.Base")
    derived = builder.symbol_table.resolve("M.T.Derived")
    assert derived.Super is base
    assert [r.Name for r in association_roles(derived)] == ["a", "b"]
    attrs = attributes_of(derived)
    assert set(attrs) == {"Weight", "Extra"}
    assert resolve_attribute(attrs["Weight"]).mandatory is True
    assert resolve_attribute(attrs["Extra"]).mandatory is False


def test_association_attribute_is_not_a_role():
    base = _build(_MODEL).symbol_table.resolve("M.T.Base")
    assert [r.Name for r in base.Role] == ["a", "b"]


def test_derived_association_link_table(tmp_path: Path):
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    out = tmp_path / "out.sql"
    main(["convert-sql", str(tmp_path / "M.ili"), "--repo", str(tmp_path), "-o", str(out)])
    sql = out.read_text(encoding="utf-8")
    derived = sql.split('CREATE TABLE "derived" (', 1)[1].split("\n);", 1)[0]
    assert '"weight" integer NOT NULL' in derived
    assert '"extra" varchar(5),' in derived
    assert '"a" text NOT NULL' in derived
    assert '"b" text NOT NULL' in derived
    assert 'CREATE TABLE "base"' not in sql
