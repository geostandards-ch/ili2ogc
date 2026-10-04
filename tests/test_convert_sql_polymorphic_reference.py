"""`REFERENCE TO <class>` whose instances may live in several tables (the class and/or its concrete subclasses).

Tables are one per concrete class with inherited columns inlined, so a
subclass instance never has a row in its base class's table: a single FK
to the base table would reject valid data.
"""

from pathlib import Path

import pytest

from interlis.cli import main

_BASE = """INTERLIS 2.4;
MODEL Base AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS Facility =
      Code : MANDATORY TEXT*10;
    END Facility;
    CLASS AbstractSite (ABSTRACT) =
      Code : MANDATORY TEXT*10;
    END AbstractSite;
    CLASS Measure =
      facility : MANDATORY REFERENCE TO Facility;
      site : REFERENCE TO AbstractSite;
    END Measure;
  END T;
END Base.
"""

_ONE_SUBCLASS = """INTERLIS 2.4;
MODEL Derived AT "http://x" VERSION "1" =
  IMPORTS Base;
  TOPIC DT EXTENDS Base.T =
    CLASS LineFacility EXTENDS Base.T.Facility =
      Voltage : 0 .. 1000;
    END LineFacility;
    CLASS Plant EXTENDS Base.T.AbstractSite =
    END Plant;
  END DT;
END Derived.
"""

_TWO_SUBCLASSES = """INTERLIS 2.4;
MODEL Derived AT "http://x" VERSION "1" =
  IMPORTS Base;
  TOPIC DT EXTENDS Base.T =
    CLASS Plant EXTENDS Base.T.AbstractSite =
    END Plant;
    CLASS Depot EXTENDS Base.T.AbstractSite =
    END Depot;
  END DT;
END Derived.
"""


def _convert(tmp_path: Path, derived: str, capsys) -> str:
    (tmp_path / "Base.ili").write_text(_BASE, encoding="utf-8")
    (tmp_path / "Derived.ili").write_text(derived, encoding="utf-8")
    argv = ["convert-sql", str(tmp_path / "Derived.ili"), "--repo", str(tmp_path), "--catalog"]
    assert main([*argv, str(tmp_path / "Base.ili"), "--dialect", "postgresql"]) == 0
    return capsys.readouterr().out


@pytest.fixture
def one_subclass_ddl(tmp_path: Path, capsys) -> str:
    return _convert(tmp_path, _ONE_SUBCLASS, capsys)


def test_concrete_base_gets_one_fk_column_per_target_table(one_subclass_ddl: str):
    ddl = one_subclass_ddl
    assert 'REFERENCES "facility" ("t_id")' in ddl
    assert '"facility_linefacility" bigint' in ddl
    assert 'FOREIGN KEY ("facility_linefacility") REFERENCES "linefacility" ("t_id")' in ddl


def test_mandatory_polymorphic_reference_requires_exactly_one_target(one_subclass_ddl: str):
    measure = one_subclass_ddl.split('CREATE TABLE "measure" (', 1)[1].split(");", 1)[0]
    # Each per-target column is nullable; MANDATORY moves to the CHECK.
    assert '"facility" bigint NOT NULL' not in measure
    assert (
        '(CASE WHEN "facility" IS NOT NULL THEN 1 ELSE 0 END'
        ' + CASE WHEN "facility_linefacility" IS NOT NULL THEN 1 ELSE 0 END) = 1'
    ) in measure


def test_abstract_base_with_one_concrete_subclass_is_retargeted(one_subclass_ddl: str):
    assert 'FOREIGN KEY ("site") REFERENCES "plant" ("t_id")' in one_subclass_ddl
    assert '"site_plant"' not in one_subclass_ddl


def test_abstract_base_with_several_subclasses_drops_the_base_column(tmp_path: Path, capsys):
    ddl = _convert(tmp_path, _TWO_SUBCLASSES, capsys)
    measure = ddl.split('CREATE TABLE "measure" (', 1)[1].split(");", 1)[0]
    assert '"site" text' not in measure
    assert 'FOREIGN KEY ("site_depot") REFERENCES "depot" ("t_id")' in ddl
    assert 'FOREIGN KEY ("site_plant") REFERENCES "plant" ("t_id")' in ddl
    assert ") <= 1" in measure
