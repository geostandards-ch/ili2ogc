"""`INSPECTION OF` a SURFACE / AREA / POLYLINE attribute: JSON-FG, SQL and JSON Schema agree.

eCH-0031 V2.1.0 SS3.15 (Aufschluesselung): the inspection of a surface
attribute yields `SurfaceBoundary` elements, of `-> Lines` `SurfaceEdge`
elements; `AREA INSPECTION` yields each boundary line of an area
partition once (`THISAREA` / `THATAREA` name its neighbours); the
inspection of a line attribute yields `LineGeometry` elements, of
`-> Segments` `LineSegment` elements (`StartSegment` first). Fixtures:
`tests/fixtures/views/inspection_of_{surface_edges,area,line,nested_parent,deep}.ili`.

The SQL views are executed against SpatiaLite when it is installed
(skipped otherwise); the PostGIS-only `AREA INSPECTION` body and the
PostGIS dialect are pinned textually.
"""

import pytest
from view_fixtures import (
    FIXTURES,
    build,
    features,
    json_schema,
    load_transfer,
    numbers,
    registered,
    run_ddl,
    spatialite_available,
    sql_views,
    view_named,
    wkt,
)

from interlis.convert.jsonfg import (
    _read_polyline,
    _read_surface,
    evaluate_view_objects,
    object_to_feature,
    unsupported_view_reason,
    view_skip_diagnostic,
)
from interlis.convert.sql import collect_diagnostics, render_gpkg, render_postgresql
from interlis.xtf.parse import parse_xtf

needs_spatialite = pytest.mark.skipif(not spatialite_available(), reason="SpatiaLite (mod_spatialite) not installed")


def _geometry_sql(node):
    """`GeomFromText` fragment for a polygon / polyline attribute node (straight geometry only)."""
    child = node.children[0]
    if child.tag.upper() in ("SURFACE", "AREA"):
        geometry = {"type": "Polygon", "coordinates": _read_surface(child)}
    else:
        geometry = {"type": "LineString", "coordinates": _read_polyline(child)}
    return "GeomFromText(?, 2056)", [wkt(geometry)]


def _spatial_db(builder, xtf_name, *, only_tids=None):
    tables, views = sql_views(builder)
    con = run_ddl(tables, views, spatialite=True)
    transfer = parse_xtf(FIXTURES / "xtf" / f"{xtf_name}.xtf")
    if only_tids is not None:
        transfer.baskets[0].objects[:] = [o for o in transfer.baskets[0].objects if o.tid in only_tids]
    load_transfer(con, transfer, geometry_sql=_geometry_sql)
    return con


def _flat(features_, name):
    return sorted(numbers(wkt(f["properties"][name])) for f in features_)


def _sql_flat(con, sql):
    return sorted(numbers(row[0]) for row in con.execute(sql))


# --- SURFACE: SurfaceBoundary and SurfaceEdge ---------------------------------


def test_surface_boundary_jsonfg_is_one_feature_per_surface_with_the_owner_field():
    feats = features(build("inspection_of_surface_edges"), "inspection_of_surface_edges", "ZoneBoundary")
    assert [f["id"] for f in feats] == ["z1", "z2"]
    assert [f["properties"]["OwnerName"] for f in feats] == ["Zone A", "Zone B"]
    assert feats[0]["properties"]["Boundary"]["type"] == "MultiLineString"  # exterior ring + hole
    assert len(feats[0]["properties"]["Boundary"]["coordinates"]) == 2
    assert feats[1]["properties"]["Boundary"]["type"] == "LineString"


def test_surface_edge_jsonfg_is_one_feature_per_boundary_line_and_has_no_id_clash():
    feats = features(build("inspection_of_surface_edges"), "inspection_of_surface_edges", "ZoneEdge")
    assert [f["id"] for f in feats] == ["z1_Geometrie_0", "z1_Geometrie_1", "z2_Geometrie_0"]
    assert all(f["properties"]["Line"]["type"] == "LineString" for f in feats)
    assert feats[0]["properties"]["Line"]["coordinates"][0] == feats[0]["properties"]["Line"]["coordinates"][-1]


def test_surface_sql_text_uses_sfa_functions_both_dialects_provide():
    tables, views = sql_views(build("inspection_of_surface_edges"))
    by_name = {v.name: v for v in views}
    assert 'ST_Boundary("zone"."geometrie") AS "boundary"' in by_name["zoneboundary"].body
    assert '"zone"."name" AS "ownername"' in by_name["zoneboundary"].body
    edge = by_name["zoneedge"].body
    assert edge.startswith("WITH RECURSIVE") and "ST_ExteriorRing" in edge and "ST_InteriorRingN" in edge
    assert render_postgresql(tables, tuple(views)).count('CREATE VIEW "zoneedge"') == 1
    assert render_gpkg(tables, tuple(views)).count('CREATE VIEW "zoneedge"') == 1


@needs_spatialite
def test_surface_boundary_and_edges_sql_equal_jsonfg_on_the_same_data():
    builder = build("inspection_of_surface_edges")
    con = _spatial_db(builder, "inspection_of_surface_edges")
    edges = features(builder, "inspection_of_surface_edges", "ZoneEdge")
    assert _flat(edges, "Line") == _sql_flat(con, 'SELECT AsText("line") FROM "zoneedge"')
    boundary = features(builder, "inspection_of_surface_edges", "ZoneBoundary")
    assert _flat(boundary, "Boundary") == _sql_flat(con, 'SELECT AsText("boundary") FROM "zoneboundary"')
    assert sorted(r[0] for r in con.execute('SELECT "ownername" FROM "zoneboundary"')) == ["Zone A", "Zone B"]


def test_surface_inspection_json_schema_describes_the_derived_values():
    schema = json_schema(build("inspection_of_surface_edges"))["$defs"]
    boundary = schema["ZoneBoundary"]["properties"]
    assert boundary["Boundary"]["properties"]["type"]["enum"] == ["LineString", "MultiLineString", "MultiCurve"]
    assert boundary["OwnerName"] == {"type": "string", "maxLength": 40}
    assert schema["ZoneEdge"]["properties"]["Line"]["properties"]["type"]["enum"][0] == "LineString"
    assert schema["ZoneEdge"]["x-crud"] == ["GET"]


# --- AREA INSPECTION --------------------------------------------------------


def test_area_inspection_jsonfg_yields_each_shared_edge_once_whatever_its_direction():
    feats = features(build("inspection_of_area"), "inspection_of_area", "ParcelEdge")
    assert len(feats) == 3  # A and B transfer the shared edge once each, reversed
    assert all("id" not in f for f in feats)


def test_area_inspection_exposes_the_neighbouring_areas():
    feats = features(build("inspection_of_area"), "inspection_of_area", "ParcelEdgeNeighbours")
    sides = sorted((f["properties"].get("Left") or "", f["properties"].get("Right") or "") for f in feats)
    assert sides == [("Parcel A", ""), ("Parcel A", "Parcel B"), ("Parcel B", "")]


def test_area_inspection_sql_is_postgis_only_and_geopackage_gets_a_note():
    builder = build("inspection_of_area")
    tables, views = sql_views(builder)
    edge = next(v for v in views if v.name == "parceledge")
    assert edge.postgis_only and "ST_Dump(ST_Union(ST_Boundary(" in edge.body
    assert 'CREATE VIEW "parceledge"' in render_postgresql(tables, tuple(views))
    gpkg = render_gpkg(tables, tuple(views))
    assert 'CREATE VIEW "parceledge"' not in gpkg and "not created in GeoPackage" in gpkg

    def rules(dialect):
        found = collect_diagnostics(tables, tuple(views), dialect=dialect)
        return [d.rule for d in found if d.location.element_path == "view parceledge"]

    gpkg_rules, pg_rules = rules("gpkg"), rules("postgresql")
    assert gpkg_rules == ["SQL-VIEW-FORMATION-UNSUPPORTED"] and pg_rules == []


def test_area_inspection_with_neighbours_has_no_sql_translation():
    _tables, views = sql_views(build("inspection_of_area"))
    neighbours = next(v for v in views if v.name == "parceledgeneighbours")
    assert neighbours.body is None and "THISAREA/THATAREA" in neighbours.notes[0]


@needs_spatialite
def test_area_inspection_noded_union_gives_the_same_edges_as_jsonfg():
    builder = build("inspection_of_area")
    con = _spatial_db(builder, "inspection_of_area")
    (union_wkt,) = con.execute('SELECT AsText(ST_Union(ST_Boundary("geometrie"))) FROM "parcel"').fetchone()
    sql_edges = {tuple(sorted(map(tuple, zip(*[iter(numbers(part))] * 2)))) for part in union_wkt.split("), (")}
    json_edges = {
        tuple(sorted(map(tuple, zip(*[iter(numbers(wkt(f["properties"]["Line"])))] * 2))))
        for f in features(builder, "inspection_of_area", "ParcelEdge")
    }
    assert sql_edges == json_edges and len(json_edges) == 3


# --- POLYLINE: LineGeometry and LineSegment -----------------------------------


def test_line_geometry_jsonfg_is_one_feature_per_line_and_keeps_arcs():
    feats = features(build("inspection_of_line"), "inspection_of_line", "RoadLine")
    assert [f["properties"]["RoadName"] for f in feats] == ["Road 1", "Road 2"]
    assert feats[0]["properties"]["Line"]["type"] == "LineString"
    assert feats[1]["properties"]["Line"]["type"] == "CompoundCurve"


def test_line_segments_start_with_the_start_segment_and_carry_arc_points():
    builder = build("inspection_of_line")
    ends = features(builder, "inspection_of_line", "RoadVertex")
    assert [f["properties"]["EndPoint"]["coordinates"][0] for f in ends][:4] == [
        2600000.0,
        2600100.0,
        2600100.0,
        2600200.0,
    ]
    assert len(ends) == 4 + 3
    arcs = features(builder, "inspection_of_line", "RoadArc")
    with_arc = [f for f in arcs if "MiddlePoint" in f["properties"]]
    assert [f["properties"]["MiddlePoint"]["coordinates"] for f in with_arc] == [[2601050.0, 1200070.0]]


@needs_spatialite
def test_line_geometry_and_segments_sql_equal_jsonfg_on_straight_data():
    builder = build("inspection_of_line")
    con = _spatial_db(builder, "inspection_of_line", only_tids={"r1"})
    transfer_features = features(builder, "inspection_of_line", "RoadVertex")[:4]
    assert [f["properties"]["EndPoint"]["coordinates"] for f in transfer_features] == [
        numbers(row[0]) for row in con.execute('SELECT AsText("endpoint") FROM "roadvertex"')
    ] or sorted(numbers(wkt(f["properties"]["EndPoint"])) for f in transfer_features) == _sql_flat(
        con, 'SELECT AsText("endpoint") FROM "roadvertex"'
    )
    line = features(builder, "inspection_of_line", "RoadLine")[0]
    ((line_wkt, name),) = con.execute('SELECT AsText("line"), "roadname" FROM "roadline"').fetchall()
    assert numbers(line_wkt) == numbers(wkt(line["properties"]["Line"])) and name == "Road 1"


def test_line_sql_has_no_arc_point_because_arcs_are_stroked():
    _tables, views = sql_views(build("inspection_of_line"))
    arc = next(v for v in views if v.name == "roadarc")
    assert arc.body is None and "ArcPoint" in arc.notes[0] and "SQL-VIEW-FORMATION-UNSUPPORTED" in arc.notes[0]


def test_an_unreadable_member_is_a_stable_diagnostic_in_every_target():
    builder = build("inspection_of_line")
    view = view_named(builder, "RoadBad")
    reason = unsupported_view_reason(view)
    assert reason is not None and "'Radius'" in reason
    assert view_skip_diagnostic(view).rule == "JSONFG-VIEW-FORMATION-UNSUPPORTED"
    _tables, views = sql_views(builder)
    bad = next(v for v in views if v.name == "roadbad")
    assert bad.body is None and "SQL-VIEW-FORMATION-UNSUPPORTED" in bad.notes[0]


def test_line_inspection_json_schema_types_points_and_geometry():
    schema = json_schema(build("inspection_of_line"))["$defs"]
    assert schema["RoadVertex"]["properties"]["EndPoint"]["properties"]["type"] == {"const": "Point"}
    assert schema["RoadLine"]["properties"]["Line"]["properties"]["type"]["enum"] == [
        "LineString",
        "CircularString",
        "CompoundCurve",
    ]


def test_geometry_inspection_has_no_xtf_transferable_shape():
    builder = build("inspection_of_line")
    view = view_named(builder, "RoadLine")
    with pytest.raises(ValueError, match="no XTF-transferable shape"):
        evaluate_view_objects(
            view, parse_xtf(FIXTURES / "xtf" / "inspection_of_line.xtf"), symbol_table=builder.symbol_table
        )


# --- structure paths: PARENT at depth, and the child-table bound -------------------


def test_nested_parent_reads_the_enclosing_structure_element_in_jsonfg_and_sql():
    builder = build("inspection_of_nested_parent")
    feats = features(builder, "inspection_of_nested_parent", "VB")
    json_rows = sorted((f["properties"]["Attr4"], f["properties"]["ParentLabel"]) for f in feats)
    assert json_rows == [("alpha", "first"), ("beta", "first"), ("gamma", "second")]
    tables, views = sql_views(builder)
    (vb,) = views
    assert 'JOIN "b_attr2" "parent" ON "insp"."b_attr2_fk" = "parent"."t_id"' in vb.body
    con = run_ddl(tables, views)
    con.execute('INSERT INTO "b" ("t_id", "t_basket") VALUES (1, 1)')
    con.executemany(
        'INSERT INTO "b_attr2" ("t_id", "t_basket", "b_fk", "attr1") VALUES (?, 1, 1, ?)', [(1, "first"), (2, "second")]
    )
    con.executemany(
        'INSERT INTO "b_attr2_attr3" ("t_id", "t_basket", "b_attr2_fk", "attr4") VALUES (?, 1, ?, ?)',
        [(1, 1, "alpha"), (2, 1, "beta"), (3, 2, "gamma")],
    )
    assert sorted(con.execute('SELECT "attr4", "parentlabel" FROM "vb"')) == json_rows


def test_a_path_deeper_than_the_emitted_child_tables_is_evaluated_by_jsonfg_and_reported_by_sql():
    builder = build("inspection_of_deep")
    assert sorted(f["properties"]["Attr5"] for f in features(builder, "inspection_of_deep")) == ["one", "three", "two"]
    tables, views = sql_views(builder)
    (vb,) = views
    assert vb.body is None and "no child table 'b_attr2_attr3_attr6'" in vb.notes[0]
    assert {t.name for t in tables} == {"b", "b_attr2", "b_attr2_attr3"}  # no table was invented


# --- a boundary split into consecutive Randlinien --------------------------------


def test_a_boundary_split_into_consecutive_polylines_is_one_closed_ring_in_place():
    builder = build("inspection_of_area")
    parcel = next(c for c in registered(builder, "Class") if c.Name == "Parcel")
    obj = parse_xtf(FIXTURES / "xtf" / "inspection_of_area.xtf").baskets[0].objects[0]
    ring = object_to_feature(obj, parcel, symbol_table=builder.symbol_table)["place"]["coordinates"][0]
    assert len(ring) == 5 and ring[0] == ring[-1]  # eCH-0031 SS4.3.11.15: the pieces chain end to start
