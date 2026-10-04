"""`convert-sql --feature-views LANG`: references and multilingual texts as readable columns."""

import sqlite3
from pathlib import Path

from interlis.cli import main

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  DOMAIN
    !!@CRS=EPSG:2056
    Coord2 = COORD 2460000.000 .. 2870000.000, 1045000.000 .. 1310000.000;
  STRUCTURE LocalisedText =
    Language : (de, fr);
    Text : MANDATORY TEXT*50;
  END LocalisedText;
  STRUCTURE MultilingualText =
    LocalisedText : BAG {1..*} OF LocalisedText;
  END MultilingualText;
  TOPIC T =
    CLASS Kind =
      Code : MANDATORY TEXT*5;
      Name : MANDATORY MultilingualText;
    UNIQUE Code;
    END Kind;
    CLASS Owner =
      Label : TEXT*10;
    END Owner;
    CLASS Site =
      Pos : MANDATORY Coord2;
      Kind : REFERENCE TO Kind;
      Owner : REFERENCE TO Owner;
      Name : MultilingualText;
    END Site;
  END T;
END M.
"""


def _connection(tmp_path: Path) -> sqlite3.Connection:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    out = tmp_path / "out.sql"
    main(["convert-sql", str(tmp_path / "M.ili"), "--feature-views", "fr", "--dialect", "gpkg", "-o", str(out)])
    con = sqlite3.connect(":memory:")
    con.executescript("\n".join(line for line in out.read_text().splitlines() if "gpkg_" not in line))
    return con


def test_reference_shows_key_and_name_in_the_language(tmp_path: Path):
    con = _connection(tmp_path)
    con.execute("INSERT INTO kind (t_id, t_basket, t_ili_tid, code) VALUES (1, 1, 'k1', 'A')")
    con.execute("INSERT INTO kind_name_localisedtext (t_basket, kind_fk, language, text) VALUES (1, 1, 'de', 'Haus')")
    con.execute("INSERT INTO kind_name_localisedtext (t_basket, kind_fk, language, text) VALUES (1, 1, 'fr', 'Maison')")
    con.execute("INSERT INTO owner (t_id, t_basket, t_ili_tid, label) VALUES (2, 1, 'o1', 'x')")
    con.execute("INSERT INTO site (t_id, t_basket, t_ili_tid, pos, kind, owner) VALUES (10, 1, 's1', x'00', 1, 2)")
    con.execute("INSERT INTO site_name_localisedtext (t_basket, site_fk, language, text) VALUES (1, 10, 'de', 'Nord')")
    row = con.execute(
        "SELECT t_ili_tid, kind_code, kind_name, owner_tid, owner_label, name FROM site_features WHERE t_id = 10"
    ).fetchone()
    # No UNIQUE on Owner: its TID and plain attributes stand for it. No French name for the site: German is shown.
    assert row == ("s1", "A", "Maison", "o1", "x", "Nord")


def test_unset_reference_keeps_the_row(tmp_path: Path):
    con = _connection(tmp_path)
    con.execute("INSERT INTO site (t_id, t_basket, t_ili_tid, pos) VALUES (10, 1, 's1', x'00')")
    assert con.execute("SELECT t_id, kind_code, name FROM site_features").fetchall() == [(10, None, None)]
