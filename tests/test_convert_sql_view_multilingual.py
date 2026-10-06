"""`convert-sql`: a VIEW attribute naming a multilingual text, one column per language."""

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


_VIEWS = """INTERLIS 2.4;
MODEL V AT "http://x" VERSION "1" =
  IMPORTS M;
  VIEW TOPIC VT EXTENDS M.T =
    VIEW site_view
      PROJECTION OF S ~ M.T.Site;
      WHERE DEFINED (S -> Kind);
      =
      ATTRIBUTE
        name := S -> Name;
        kind_name := S -> Kind -> Name;
    END site_view;
  END VT;
END V.
"""


def _view_ddl(tmp_path: Path, *options: str) -> str:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    (tmp_path / "V.ili").write_text(_VIEWS, encoding="utf-8")
    out = tmp_path / "views.sql"
    main(
        ["convert-sql", str(tmp_path / "V.ili"), "--repo", str(tmp_path), *options, "--dialect", "gpkg", "-o", str(out)]
    )
    return out.read_text(encoding="utf-8")


def test_view_attribute_naming_a_multilingual_text_is_one_column_per_language(tmp_path: Path):
    """Its whole value, as ili2db's --expandMultilingual: `<attr>` (no language) and `<attr>_<lang>` per language."""
    ddl = _view_ddl(tmp_path)
    con = sqlite3.connect(":memory:")
    con.executescript("\n".join(line for line in ddl.splitlines() if "gpkg_" not in line))
    con.execute("INSERT INTO kind (t_id, t_basket, t_ili_tid, code) VALUES (1, 1, 'k1', 'A')")
    con.execute("INSERT INTO kind_name_localisedtext (t_basket, kind_fk, language, text) VALUES (1, 1, 'fr', 'Maison')")
    con.execute("INSERT INTO site (t_id, t_basket, t_ili_tid, pos, kind) VALUES (10, 1, 's1', x'00', 1)")
    con.execute("INSERT INTO site (t_id, t_basket, t_ili_tid, pos) VALUES (11, 1, 's2', x'00')")
    con.execute("INSERT INTO site_name_localisedtext (t_basket, site_fk, language, text) VALUES (1, 10, 'de', 'Nord')")
    con.execute("INSERT INTO site_name_localisedtext (t_basket, site_fk, text) VALUES (1, 10, 'North')")
    columns = [c[1] for c in con.execute("PRAGMA table_info(site_view)")]
    assert columns == ["t_id", "t_ili_tid", "name", "name_de", "name_fr", "kind_name", "kind_name_de", "kind_name_fr"]
    rows = con.execute("SELECT t_ili_tid, name, name_de, name_fr, kind_name_fr FROM site_view").fetchall()
    # s2 has no Kind: filtered out by the VIEW's WHERE.
    assert rows == [("s1", "North", "Nord", None, "Maison")]
