"""Reproductions of the reviewed schema/conversion limitations (H1, H2, H4, H6, H8).

A confirmed limitation is an `xfail(strict=True)` test asserting the desired behaviour: it flips to a hard failure the
moment the limitation is fixed, forcing the marker to be dropped. An unconfirmed one is a plain characterization test.
"""

import re
from pathlib import Path

from interlis.cli import main
from interlis.convert.sql.data import _geometry_sql
from interlis.convert.sql.model import Column
from interlis.xtf import parse as xtf_parse

FIX = Path(__file__).parent / "fixtures"
SRC = Path(__file__).parent.parent / "src" / "interlis"


def test_h1_xtf_objects_can_be_streamed():
    assert hasattr(xtf_parse, "iter_objects")


def test_h2_sql_follows_the_same_context_crs_as_jsonfg(tmp_path: Path):
    d = FIX / "solid3d"
    out = tmp_path / "o.sql"
    main(["convert-sql", str(d / "Building3D_V1.ili"), "--repo", str(d), "-o", str(out)])
    assert "SQL-GEOM-NO-CRS" not in out.read_text(encoding="utf-8")


def test_h2_jsonfg_resolves_the_context_crs(tmp_path: Path):
    d = FIX / "solid3d"
    out = tmp_path / "o.json"
    main(
        [
            "convert-jsonfg",
            str(d / "building3d.xtf"),
            "--model",
            str(d / "Building3D_V1.ili"),
            "--repo",
            str(d),
            "-o",
            str(out),
        ]
    )
    assert "EPSG/0/2056" in out.read_text(encoding="utf-8")


def test_h3_coord_domain_without_crs_is_never_guessed(tmp_path: Path):
    (tmp_path / "M.ili").write_text(
        'INTERLIS 2.4;\nMODEL M AT "http://x" VERSION "1" =\n'
        "  DOMAIN C = COORD 2460000.000 .. 2870000.000, 1045000.000 .. 1310000.000;\n"
        "  TOPIC T = CLASS A = G : C; END A; END T;\nEND M.\n",
        encoding="utf-8",
    )
    out = tmp_path / "o.sql"
    main(["convert-sql", str(tmp_path / "M.ili"), "--repo", str(tmp_path), "-o", str(out)])
    assert "SQL-GEOM-CRS-UNDECLARED" in out.read_text(encoding="utf-8")


def test_h4_arc_geometry_is_stroked_on_load_not_dropped():
    column = Column("g", "geometry", geometry_type="LineString", srid=2056)
    geometry = {
        "type": "CompoundCurve",
        "geometries": [{"type": "CircularString", "coordinates": [[0, 0], [1, 1], [2, 0]]}],
    }
    sql = _geometry_sql(column, geometry)
    assert sql.startswith("ST_CurveToLine(") and "CIRCULARSTRING" in sql


def test_h8_sql_package_has_no_private_cross_package_imports():
    pattern = re.compile(r"from interlis\.convert\.(jsonfg|jsonschema) import [^\n]*\b_\w+")
    offenders = [
        p.name for p in (SRC / "convert" / "sql").glob("*.py") if pattern.search(p.read_text(encoding="utf-8"))
    ]
    assert offenders == []
