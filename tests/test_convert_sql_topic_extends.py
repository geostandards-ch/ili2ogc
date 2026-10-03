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


def test_same_named_folded_classes_are_named_after_their_model(tmp_path: Path, capsys):
    """Two same-named base classes from two models: each table names its own model, never a counter."""
    second = (
        _BASE.split("END Cat.\n", 1)[1]
        .replace("MODEL Base ", "MODEL Base2 ")
        .replace("END Base.", "END Base2.")
        .replace("Label : TEXT*10;", "Label : TEXT*10;\n      Extra : TEXT*3;")
    )
    (tmp_path / "Base.ili").write_text(_BASE + second, encoding="utf-8")
    (tmp_path / "Two.ili").write_text(
        _BARE_EXTENSION.replace("MODEL Bare", "MODEL Two").replace("END Bare.", "END Two.")
        + _BARE_EXTENSION.replace("MODEL Bare", "MODEL Two2")
        .replace("END Bare.", "END Two2.")
        .replace("Base", "Base2")
        .replace("INTERLIS 2.4;", ""),
        encoding="utf-8",
    )
    out = tmp_path / "out.sql"
    main(["convert-sql", str(tmp_path / "Two.ili"), "--repo", str(tmp_path), "-o", str(out)])
    sql = out.read_text(encoding="utf-8")
    first = sql.split('CREATE TABLE "facility_base" (', 1)[1].split(");", 1)[0]
    second = sql.split('CREATE TABLE "facility_base2" (', 1)[1].split(");", 1)[0]
    assert '"extra"' not in first
    assert '"extra"' in second
