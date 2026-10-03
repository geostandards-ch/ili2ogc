"""`WITH (STRAIGHTS, ARCS)` binds LineType.LineForm, and convert-sql flags a linear column admitting arcs."""

from pathlib import Path

from conftest import build_from_text as _build

from interlis.cli import main
from interlis.xtf.schema import line_allows_arcs

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  DOMAIN
    !!@CRS=EPSG:2056
    Coord2 = COORD 2460000.000 .. 2870000.000, 1045000.000 .. 1310000.000;
    Curved = POLYLINE WITH (STRAIGHTS, ARCS) VERTEX Coord2;
    Straight = POLYLINE WITH (STRAIGHTS) VERTEX Coord2;
  TOPIC T =
    CLASS A =
      C : Curved;
      S : Straight;
    END A;
  END T;
END M.
"""


def test_line_forms_are_bound():
    table = _build(_MODEL).symbol_table
    assert line_allows_arcs(table.resolve("M.Curved"))
    assert not line_allows_arcs(table.resolve("M.Straight"))


def test_linear_column_admitting_arcs_is_flagged(tmp_path: Path, capsys):
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    main(["convert-sql", str(tmp_path / "M.ili"), "--repo", str(tmp_path), "-o", str(tmp_path / "out.sql")])
    err = capsys.readouterr().err
    assert "SQL-GEOM-ARCS-STROKED" in err and "C: admits ARCS" in err
    assert "S: admits ARCS" not in err
