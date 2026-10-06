"""Generated SQL views expose a `t_id` column (pygeoapi and OGR need an id): the base's, or joined bases' ids."""

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
    con.executescript(
        "CREATE TABLE road (t_id INTEGER, t_ili_tid TEXT, name TEXT); INSERT INTO road VALUES (1, 'r1', 'A1');"
    )
    body = _views()["roadnames"].body
    assert con.execute(f"SELECT t_id, t_ili_tid, roadname FROM ({body})").fetchall() == [(1, "r1", "A1")]


def test_join_view_id_combines_both_bases():
    con = sqlite3.connect(":memory:")
    con.executescript(
        "CREATE TABLE road (t_id INTEGER, name TEXT); CREATE TABLE sign (t_id INTEGER, label TEXT, road INTEGER);"
        "INSERT INTO road VALUES (1, 'A1'); INSERT INTO sign VALUES (11, 'Stop', 1), (12, 'Go', 1);"
    )
    rows = con.execute(f"SELECT t_id FROM ({_views()['roadsigns'].body}) ORDER BY t_id").fetchall()
    assert rows == [("1:11",), ("1:12",)]
