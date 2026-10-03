"""`convert-sql` on a model whose TOPIC EXTENDS a base topic declared in an imported model.

The extended topic's baskets carry objects of the base topic's classes, so
those classes (and the catalogue classes they reference through a
MANDATORY catalogue-reference STRUCTURE) must become tables from `--repo`
alone, without `--catalog`.
"""

from pathlib import Path

from interlis.cli import main

_BASE = """INTERLIS 2.4;
MODEL Cat AT "http://x" VERSION "1" =
  TOPIC Items =
    CLASS Item =
      Code : MANDATORY TEXT*5;
    END Item;
    STRUCTURE ItemRef =
      Reference : MANDATORY REFERENCE TO (EXTERNAL) Item;
    END ItemRef;
  END Items;
END Cat.

MODEL Base AT "http://x" VERSION "1" =
  IMPORTS Cat;
  TOPIC Plans =
    DEPENDS ON Cat.Items;
    CLASS Thing (ABSTRACT) =
      Label : TEXT*10;
    END Thing;
    CLASS Facility EXTENDS Thing =
      Kind : MANDATORY Cat.Items.ItemRef;
    END Facility;
    CLASS Measure =
      facility : REFERENCE TO Facility;
    END Measure;
  END Plans;
END Base.
"""

_EXTENSION = """INTERLIS 2.4;
MODEL Ext AT "http://x" VERSION "1" =
  IMPORTS Base;
  TOPIC Plans EXTENDS Base.Plans =
    CLASS SubFacility EXTENDS Base.Plans.Facility =
    END SubFacility;
  END Plans;
END Ext.
"""

_BARE_EXTENSION = """INTERLIS 2.4;
MODEL Bare AT "http://x" VERSION "1" =
  IMPORTS Base;
  TOPIC Plans EXTENDS Base.Plans =
  END Plans;
END Bare.
"""


def _convert_sql(tmp_path: Path, name: str, text: str, capsys) -> tuple[str, str]:
    (tmp_path / "Base.ili").write_text(_BASE, encoding="utf-8")
    (tmp_path / f"{name}.ili").write_text(text, encoding="utf-8")
    out = tmp_path / "out.sql"
    main(["convert-sql", str(tmp_path / f"{name}.ili"), "--repo", str(tmp_path), "-o", str(out)])
    return out.read_text(encoding="utf-8"), capsys.readouterr().err


def test_inherited_base_topic_classes_become_tables(tmp_path: Path, capsys):
    sql, _ = _convert_sql(tmp_path, "Bare", _BARE_EXTENSION, capsys)
    assert 'CREATE TABLE "facility"' in sql
    assert 'CREATE TABLE "measure"' in sql
    assert 'CREATE TABLE "thing"' not in sql  # ABSTRACT: never instantiated


def test_catalogue_target_behind_a_mandatory_structure_is_folded(tmp_path: Path, capsys):
    sql, err = _convert_sql(tmp_path, "Ext", _EXTENSION, capsys)
    assert 'CREATE TABLE "item"' in sql
    assert 'FOREIGN KEY ("kind_reference") REFERENCES "item"' in sql
    assert "SQL-FK-CROSS-MODEL-DROPPED" not in err


def test_reference_to_extended_base_class_is_split_per_table(tmp_path: Path, capsys):
    sql, _ = _convert_sql(tmp_path, "Ext", _EXTENSION, capsys)
    assert 'REFERENCES "facility"' in sql
    assert '"facility_subfacility" text' in sql
    assert 'REFERENCES "subfacility"' in sql
