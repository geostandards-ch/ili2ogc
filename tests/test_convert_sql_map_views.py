"""`convert-sql --map-views`: one view per GRAPHIC exposing its SLD filter paths as columns."""

import sqlite3
from pathlib import Path

from interlis.cli import main

_DATA = """INTERLIS 2.4;
MODEL D AT "http://x" VERSION "1" =
  DOMAIN
    !!@CRS=EPSG:2056
    Coord2 = COORD 2460000.000 .. 2870000.000, 1045000.000 .. 1310000.000;
  TOPIC Cat =
    CLASS Kind =
      Code : MANDATORY TEXT*5;
    END Kind;
    STRUCTURE KindRef =
      Reference : MANDATORY REFERENCE TO (EXTERNAL) Kind;
    END KindRef;
  END Cat;
  TOPIC T =
    DEPENDS ON D.Cat;
    CLASS Site =
      Pos : MANDATORY Coord2;
      Kind : MANDATORY D.Cat.KindRef;
      Level : 1 .. 3;
    END Site;
  END T;
END D.
"""

_SYMBOLOGY = """INTERLIS 2.4;
MODEL S AT "http://x" VERSION "1" =
  IMPORTS D, StandardSymbology;
  SIGN BASKET Signs ~ StandardSymbology.StandardSigns
    OBJECTS OF SymbolSign: Dot;
  TOPIC G =
    DEPENDS ON D.T;
    GRAPHIC Site_Graphics BASED ON D.T.Site =
      big OF StandardSymbology.StandardSigns.SymbolSign:
        WHERE (Kind -> Reference -> Code == "a") AND (Level == 3) (
          Sign := {Dot};
          Geometry := Pos;
          Priority := 1);
      other OF StandardSymbology.StandardSigns.SymbolSign:
        WHERE Kind -> Reference -> Code == "b" (
          Sign := {Dot};
          Geometry := Pos;
          Priority := 2);
    END Site_Graphics;
  END G;
END S.
"""


def _sql(tmp_path: Path) -> str:
    symbology = Path(__file__).parent / "fixtures" / "cartosym" / "roadsexgm2ien_repo"
    for name in ("AbstractSymbology.ili", "StandardSymbology.ili"):
        (tmp_path / name).write_text((symbology / name).read_text(encoding="utf-8"), encoding="utf-8")
    (tmp_path / "D.ili").write_text(_DATA, encoding="utf-8")
    (tmp_path / "S.ili").write_text(_SYMBOLOGY, encoding="utf-8")
    out = tmp_path / "out.sql"
    main(
        [
            "convert-sql",
            str(tmp_path / "D.ili"),
            "--repo",
            str(tmp_path),
            "--map-views",
            str(tmp_path / "S.ili"),
            "--dialect",
            "gpkg",
            "-o",
            str(out),
        ]
    )
    return out.read_text(encoding="utf-8")


def test_graphic_view_exposes_each_filter_path_once(tmp_path: Path):
    view = _sql(tmp_path).split('CREATE VIEW "site_graphics" AS', 1)[1].split(";", 1)[0]
    assert '"g"."pos" AS "pos"' in view
    assert 'AS "Kind.Reference.Code"' in view
    assert '"g"."level" AS "Level"' in view
    assert view.count('"kind" "j') == 1


def test_graphic_view_answers_the_sld_filter(tmp_path: Path):
    ddl = _sql(tmp_path)
    con = sqlite3.connect(":memory:")
    con.executescript("\n".join(line for line in ddl.splitlines() if "gpkg_" not in line))  # GPKG core tables aside
    con.execute("INSERT INTO kind (t_id, t_basket, code) VALUES (1, 1, 'a')")
    con.execute("INSERT INTO site (t_id, t_basket, pos, kind_reference, level) VALUES (10, 1, x'00', 1, 3)")
    rows = con.execute('SELECT t_id FROM site_graphics WHERE "Kind.Reference.Code" = \'a\' AND "Level" = 3').fetchall()
    assert rows == [(10,)]
