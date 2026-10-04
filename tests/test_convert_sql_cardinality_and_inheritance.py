"""convert-sql nullability, link tables and inherited constraints, checked on the generated DDL."""

from pathlib import Path

from interlis.cli import main

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  TOPIC T =
    STRUCTURE Info =
      Note : TEXT*10;
      Stamp : MANDATORY TEXT*10;
    END Info;
    CLASS Plan =
      Code : MANDATORY TEXT*5;
      Meta : Info;
      Required : MANDATORY Info;
      UNIQUE Code;
    END Plan;
    CLASS SubPlan EXTENDS Plan =
    END SubPlan;
    CLASS Part =
      Label : TEXT*5;
    END Part;
    CLASS Tag =
      Label : TEXT*5;
    END Tag;
    ASSOCIATION Plan_Part =
      Plan -<#> {1} Plan;
      Part -- {0..*} Part;
    END Plan_Part;
    ASSOCIATION Part_Tag =
      Part -- {0..*} Part;
      Tag -- {0..*} Tag;
    END Part_Tag;
  END T;
END M.
"""


def _sql(tmp_path: Path) -> str:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    out = tmp_path / "out.sql"
    main(["convert-sql", str(tmp_path / "M.ili"), "--repo", str(tmp_path), "-o", str(out)])
    return out.read_text(encoding="utf-8")


def _table(sql: str, name: str) -> str:
    return sql.split(f'CREATE TABLE "{name}" (', 1)[1].split("\n);", 1)[0]


def test_mandatory_part_of_an_optional_structure_is_nullable_with_a_presence_check(tmp_path: Path):
    plan = _table(_sql(tmp_path), "plan")
    assert '"meta_stamp" varchar(10),' in plan
    assert '"required_stamp" varchar(10) NOT NULL' in plan
    assert 'CHECK (("meta_note" IS NULL AND "meta_stamp" IS NULL) OR ("meta_stamp" IS NOT NULL))' in plan


def test_required_polymorphic_role_needs_exactly_one_target_and_cascades(tmp_path: Path):
    """`Plan {1}` with a subclass `SubPlan`: one column per table, exactly one set, both cascading."""
    sql = _sql(tmp_path)
    part = _table(sql, "part")
    assert '"plan" bigint,' in part
    assert '"plan_subplan" bigint,' in part
    assert "ELSE 0 END) = 1)" in part
    assert 'FOREIGN KEY ("plan") REFERENCES "plan" ("t_id") ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;' in sql
    assert (
        'FOREIGN KEY ("plan_subplan") REFERENCES "subplan" ("t_id") ON DELETE CASCADE DEFERRABLE INITIALLY DEFERRED;'
        in sql
    )


def test_n_to_m_association_gets_a_link_table(tmp_path: Path):
    link = _table(_sql(tmp_path), "part_tag")
    assert '"part" bigint NOT NULL' in link
    assert '"tag" bigint NOT NULL' in link


def test_embedded_association_gets_no_table(tmp_path: Path):
    assert 'CREATE TABLE "plan_part"' not in _sql(tmp_path)


def test_subclass_table_keeps_inherited_unique(tmp_path: Path):
    assert 'UNIQUE ("code")' in _table(_sql(tmp_path), "subplan")
