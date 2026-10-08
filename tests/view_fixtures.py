"""Helpers shared by the VIEW formation tests: build a fixture, translate it, load an XTF into SQLite.

Plain functions (like `conftest.py`'s), importable from any sibling test file.
"""

import re
import sqlite3
from pathlib import Path

from conftest import build_from_file

from interlis.convert.jsonfg import evaluate_view
from interlis.convert.jsonschema import model_to_json_schema
from interlis.convert.sql import build_tables, build_views, render_gpkg
from interlis.metamodel.instance import MetaInstance
from interlis.runtime.parse import meta_attribute_comments_in_file
from interlis.xtf.parse import parse_xtf
from interlis.xtf.validate import _extract_reference

ROOT = Path(__file__).resolve().parent.parent
FIXTURES = ROOT / "tests" / "fixtures" / "views"


def build(name: str):
    path = FIXTURES / f"{name}.ili"
    return build_from_file(path, meta_attributes=meta_attribute_comments_in_file(path))


def registered(builder, suffix: str):
    return [
        inst
        for inst in builder.symbol_table.all_registered()
        if isinstance(inst, MetaInstance) and inst._qualified_class == f"IlisMeta16.ModelData.{suffix}"
    ]


def json_schema(builder):
    return model_to_json_schema(
        registered(builder, "Class") + registered(builder, "View"), symbol_table=builder.symbol_table
    )


def sql_views(builder):
    tables = build_tables(registered(builder, "Class"), symbol_table=builder.symbol_table)
    views = build_views(registered(builder, "View"), tables, symbol_table=builder.symbol_table)
    return tables, views


def view_named(builder, name: str):
    (view,) = [v for v in registered(builder, "View") if v.Name == name]
    return view


def features(builder, xtf_name: str, view: int | str = 0):
    """JSON-FG Features of a view of the fixture (by position or by name) over `tests/fixtures/views/xtf/<xtf_name>`."""
    view = registered(builder, "View")[view] if isinstance(view, int) else view_named(builder, view)
    transfer = parse_xtf(FIXTURES / "xtf" / f"{xtf_name}.xtf")
    return evaluate_view(view, transfer, symbol_table=builder.symbol_table)


def gpkg_schema_only(ddl: str) -> str:
    """The DDL up to the GeoPackage bootstrap rows (SRS first), which need GDAL's gpkg_* tables."""
    return re.split(r"\n(?:-- TODO: verify|INSERT (?:OR IGNORE )?INTO gpkg_)", ddl, maxsplit=1)[0]


def run_ddl(tables, views, *, spatialite: bool = False):
    """Create every table and `CREATE VIEW` against live SQLite (with SpatiaLite loaded when asked)."""
    con = sqlite3.connect(":memory:")
    if spatialite:
        con.enable_load_extension(True)
        con.load_extension("mod_spatialite")
    con.executescript(gpkg_schema_only(render_gpkg(tables)))
    for view in views:
        if view.body is not None and not view.postgis_only:
            con.execute(f'CREATE VIEW "{view.name}" AS {view.body}')
    return con


def spatialite_available() -> bool:
    try:
        con = sqlite3.connect(":memory:")
        con.enable_load_extension(True)
        con.load_extension("mod_spatialite")
        return True
    except (sqlite3.Error, AttributeError, OSError):
        return False


def load_transfer(con, transfer, *, geometry_sql=None) -> None:
    """Insert every object of `transfer` into its table: text attributes as is, references as the target's `t_id`.

    Table = lower-cased class short name, column = lower-cased attribute name (the names `build_tables` emits for
    these fixtures). `geometry_sql(node)` may turn a geometry attribute node into a `(sql, params)` fragment.
    """
    objects = [obj for basket in transfer.baskets for obj in basket.objects]
    ids = {obj.tid: i for i, obj in enumerate(objects, 1)}
    for obj in objects:
        table = obj.qualified_class.rsplit(".", 1)[-1].lower()
        columns, marks, params = ['"t_id"', '"t_basket"'], ["?", "?"], [ids[obj.tid], 1]
        for name, nodes in obj.attributes.items():
            node = nodes[0]
            ref = _extract_reference(node)
            if ref is not None:
                value = ids[ref]
            elif node.children and geometry_sql is not None:
                fragment = geometry_sql(node)
                columns.append(f'"{name.lower()}"')
                marks.append(fragment[0])
                params.extend(fragment[1])
                continue
            else:
                value = node.text
            columns.append(f'"{name.lower()}"')
            marks.append("?")
            params.append(value)
        con.execute(f'INSERT INTO "{table}" ({", ".join(columns)}) VALUES ({", ".join(marks)})', params)


def wkt(geometry: dict) -> str:
    """A straight GeoJSON geometry (`LineString`, `MultiLineString`, `Point`, `Polygon`) as WKT."""

    def pos(p):
        return " ".join(f"{c:.3f}" for c in p)

    def seq(points):
        return "(" + ", ".join(pos(p) for p in points) + ")"

    kind, coords = geometry["type"], geometry["coordinates"]
    if kind == "Point":
        return f"POINT({pos(coords)})"
    if kind == "LineString":
        return "LINESTRING" + seq(coords)
    if kind == "MultiLineString":
        return "MULTILINESTRING(" + ", ".join(seq(line) for line in coords) + ")"
    return "POLYGON(" + ", ".join(seq(ring) for ring in coords) + ")"


def numbers(text: str) -> list[float]:
    """Every number of a WKT string, in order: geometry equality up to formatting."""
    return [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", text)]
