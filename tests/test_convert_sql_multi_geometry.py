"""Multi-geometry wrapper STRUCTUREs become one Multi* column; GeoPackage keeps one geometry column per table."""

import sqlite3
from pathlib import Path

from interlis.cli import main

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  DOMAIN
    !!@CRS=EPSG:2056
    Coord2 = COORD 2460000.000 .. 2870000.000, 1045000.000 .. 1310000.000;
    Surface = SURFACE WITH (STRAIGHTS) VERTEX Coord2 WITHOUT OVERLAPS > 0.001;
    Line = POLYLINE WITH (STRAIGHTS) VERTEX Coord2;
  STRUCTURE PointStructure =
    Point : Coord2;
  END PointStructure;
  STRUCTURE MultiPoint =
    Points : BAG {1..*} OF PointStructure;
  END MultiPoint;
  STRUCTURE SurfaceStructure =
    Surface : Surface;
  END SurfaceStructure;
  STRUCTURE MultiSurface =
    Surfaces : BAG {1..*} OF SurfaceStructure;
  END MultiSurface;
  STRUCTURE LineStructure =
    Line : Line;
  END LineStructure;
  STRUCTURE MultiLine =
    Lines : BAG {1..*} OF LineStructure;
  END MultiLine;
  TOPIC T =
    CLASS Site =
      Area : MANDATORY MultiSurface;
      Spots : MultiPoint;
      Paths : MultiLine;
      Born : INTERLIS.XMLDateTime;
    END Site;
  END T;
END M.
"""

# GeoPackage 1.3 system tables (spec Annex C), as GDAL creates them.
_GPKG_SYSTEM = """
CREATE TABLE gpkg_spatial_ref_sys (srs_name TEXT NOT NULL, srs_id INTEGER PRIMARY KEY, organization TEXT NOT NULL,
  organization_coordsys_id INTEGER NOT NULL, definition TEXT NOT NULL, description TEXT);
CREATE TABLE gpkg_contents (table_name TEXT NOT NULL PRIMARY KEY, data_type TEXT NOT NULL, identifier TEXT UNIQUE,
  description TEXT DEFAULT '', last_change DATETIME, min_x DOUBLE, min_y DOUBLE, max_x DOUBLE, max_y DOUBLE,
  srs_id INTEGER, CONSTRAINT fk_gc_r_srs_id FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys(srs_id));
CREATE TABLE gpkg_geometry_columns (table_name TEXT NOT NULL, column_name TEXT NOT NULL,
  geometry_type_name TEXT NOT NULL, srs_id INTEGER NOT NULL, z TINYINT NOT NULL, m TINYINT NOT NULL,
  CONSTRAINT pk_geom_cols PRIMARY KEY (table_name, column_name), CONSTRAINT uk_gc_table_name UNIQUE (table_name),
  CONSTRAINT fk_gc_tn FOREIGN KEY (table_name) REFERENCES gpkg_contents(table_name),
  CONSTRAINT fk_gc_srs FOREIGN KEY (srs_id) REFERENCES gpkg_spatial_ref_sys (srs_id));
"""


def _convert(tmp_path: Path, dialect: str) -> str:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    out = tmp_path / f"out.{dialect}.sql"
    main(["convert-sql", str(tmp_path / "M.ili"), "--repo", str(tmp_path), "--dialect", dialect, "-o", str(out)])
    return out.read_text(encoding="utf-8")


def test_wrappers_become_one_multi_geometry_column(tmp_path: Path):
    sql = _convert(tmp_path, "postgresql")
    site = sql.split('CREATE TABLE "site" (', 1)[1].split("\n);", 1)[0]
    assert '"area" geometry(MultiPolygon, 2056) NOT NULL' in site
    assert '"spots" geometry(MultiPoint, 2056),' in site
    assert '"paths" geometry(MultiLineString, 2056),' in site
    assert "_surfaces" not in sql
    assert 'CREATE INDEX idx_site_area ON "site" USING GIST ("area");' in sql


def test_geopackage_keeps_one_geometry_per_table_and_applies(tmp_path: Path):
    sql = _convert(tmp_path, "gpkg")
    assert 'CREATE TABLE "site_spots"' in sql
    assert 'CREATE TABLE "site_paths"' in sql
    assert '"born" DATETIME' in sql
    assert "-- spatial index: ogrinfo <file>.gpkg -sql \"SELECT CreateSpatialIndex('site', 'area')\"" in sql
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(_GPKG_SYSTEM + sql)
    assert con.execute("SELECT count(*) FROM gpkg_geometry_columns").fetchone()[0] == 3
    con.execute("INSERT INTO site (t_id, t_basket, area) VALUES (100, 1, x'00')")
    con.execute("INSERT INTO site_spots (t_id, t_basket, site_fk) VALUES (101, 1, 100)")
    con.execute("DELETE FROM site")
    assert con.execute("SELECT count(*) FROM site_spots").fetchone()[0] == 0
    con.close()
