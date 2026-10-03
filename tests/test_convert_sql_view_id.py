"""Generated SQL views expose an `id` column (pygeoapi and OGR need one): the base's id, or joined bases' ids."""

import sqlite3

from conftest import build_from_text as _build

from interlis.convert.sql import build_tables, build_views
from interlis.metamodel.instance import MetaInstance

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS Road =
      Name : TEXT*20;
    END Road;
    CLASS Sign =
      Label : TEXT*20;
    END Sign;
    ASSOCIATION RoadSign =
      road -- {0..1} Road;
      sign -- {0..*} Sign;
    END RoadSign;
    VIEW RoadNames
      PROJECTION OF r ~ Road;
    =
      RoadName := r->Name;
    END RoadNames;
    VIEW RoadSigns
      JOIN OF r ~ Road, s ~ Sign;
    =
      RoadName := r->Name;
      SignLabel := s->Label;
    END RoadSigns;
  END T;
END M.
"""


def _views():
    builder = _build(_MODEL)
    registered = [i for i in builder.symbol_table.all_registered() if isinstance(i, MetaInstance)]
    classes = [i for i in registered if i._qualified_class.endswith(".Class")]
    views = [i for i in registered if i._qualified_class.endswith(".View")]
    names: dict[int, str] = {}
    tables = build_tables(classes, builder.symbol_table, class_table_names=names)
    return {v.name: v for v in build_views(views, tables, symbol_table=builder.symbol_table, class_table_names=names)}


def test_projection_view_exposes_its_base_id():
    con = sqlite3.connect(":memory:")
    con.executescript("CREATE TABLE road (id TEXT, name TEXT); INSERT INTO road VALUES ('r1', 'A1');")
    body = _views()["roadnames"].body
    assert con.execute(f"SELECT id, roadname FROM ({body})").fetchall() == [("r1", "A1")]


def test_join_view_id_combines_both_bases():
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE road (id TEXT, name TEXT); CREATE TABLE sign (id TEXT, label TEXT, road TEXT);"
        "INSERT INTO road VALUES ('r1', 'A1'); INSERT INTO sign VALUES ('s1', 'Stop', 'r1'), ('s2', 'Go', 'r1');"
    )
    rows = con.execute(f"SELECT id FROM ({_views()['roadsigns'].body}) ORDER BY id").fetchall()
    assert rows == [("r1:s1",), ("r1:s2",)]
