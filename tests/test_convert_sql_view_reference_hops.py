"""`convert-sql` VIEW paths through references: LEFT JOINs, and a reference to a class with subclass tables."""

import sqlite3
from pathlib import Path

from interlis.cli import main

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS Facility =
      Label : TEXT*20;
    END Facility;
    CLASS SpecialFacility EXTENDS Facility =
      Number : 1 .. 99;
    END SpecialFacility;
    CLASS Measure =
      Code : TEXT*5;
      Fac : REFERENCE TO Facility;
    END Measure;
  END T;
END M.
"""

_VIEWS = """INTERLIS 2.4;
MODEL V AT "http://x" VERSION "1" =
  IMPORTS M;
  VIEW TOPIC VT EXTENDS M.T =
    VIEW measure_view
      PROJECTION OF X ~ M.T.Measure;
      =
      ATTRIBUTE
        code := X -> Code;
        facility_label := X -> Fac -> Label;
    END measure_view;
  END VT;
END V.
"""


def _connection(tmp_path: Path) -> sqlite3.Connection:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    (tmp_path / "V.ili").write_text(_VIEWS, encoding="utf-8")
    out = tmp_path / "out.sql"
    main(["convert-sql", str(tmp_path / "V.ili"), "--repo", str(tmp_path), "--dialect", "gpkg", "-o", str(out)])
    con = sqlite3.connect(":memory:")
    con.executescript("\n".join(line for line in out.read_text().splitlines() if "gpkg_" not in line))
    return con


def test_reference_to_a_base_or_a_subclass_row_and_an_unset_one(tmp_path: Path):
    con = _connection(tmp_path)
    con.execute("INSERT INTO facility (t_id, t_basket, t_ili_tid, label) VALUES (1, 1, 'f1', 'plain')")
    con.execute(
        "INSERT INTO specialfacility (t_id, t_basket, t_ili_tid, label, number) VALUES (2, 1, 'f2', 'special', 7)"
    )
    con.execute("INSERT INTO measure (t_id, t_basket, t_ili_tid, code, fac) VALUES (10, 1, 'm1', 'a', 1)")
    con.execute(
        "INSERT INTO measure (t_id, t_basket, t_ili_tid, code, fac_specialfacility) VALUES (11, 1, 'm2', 'b', 2)"
    )
    con.execute("INSERT INTO measure (t_id, t_basket, t_ili_tid, code) VALUES (12, 1, 'm3', 'c')")
    rows = con.execute("SELECT t_ili_tid, code, facility_label FROM measure_view ORDER BY t_id").fetchall()
    # The facility lives in the base or the subclass table; an unset reference leaves the attribute undefined.
    assert rows == [("m1", "a", "plain"), ("m2", "b", "special"), ("m3", "c", None)]
