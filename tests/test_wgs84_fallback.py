"""The WGS 84 fallback `geometry` of a JSON-FG `place` that holds circular arcs."""

from interlis.convert.wgs84 import fallback_geometry

LV95 = "http://www.opengis.net/def/crs/EPSG/0/2056"
ARC = {"type": "CircularString", "coordinates": [[2600000, 1200000], [2600010, 1200010], [2600020, 1200000]]}


def test_an_arc_is_stroked_and_reprojected():
    geometry = fallback_geometry(ARC, LV95)
    assert geometry["type"] == "LineString"
    assert len(geometry["coordinates"]) > 10
    lon, lat = geometry["coordinates"][0]
    assert 7.43 < lon < 7.44 and 46.95 < lat < 46.96
    assert geometry["coordinates"][0] != geometry["coordinates"][-1]


def test_a_curve_polygon_gives_a_closed_polygon():
    ring = {
        "type": "CompoundCurve",
        "geometries": [ARC, {"type": "LineString", "coordinates": [[2600020, 1200000], [2600000, 1200000]]}],
    }
    geometry = fallback_geometry({"type": "CurvePolygon", "geometries": [ring]}, LV95)
    shell = geometry["coordinates"][0]
    assert geometry["type"] == "Polygon" and shell[0] == shell[-1]


def test_nothing_to_draw_without_arcs_or_without_a_known_crs():
    assert fallback_geometry({"type": "Point", "coordinates": [1, 2]}, LV95) is None
    assert fallback_geometry(ARC, None) is None
    assert fallback_geometry(ARC, "http://www.opengis.net/def/crs/EPSG/0/999999") is None


def test_lv95_uses_the_swisstopo_grid_not_the_helmert_fallback():
    from pyproj import Transformer

    from interlis.convert.wgs84 import _transformer

    helmert = Transformer.from_crs("EPSG:2056", "EPSG:4326", always_xy=True).transform(2683000, 1248000)
    grid = _transformer(2056).transform(2683000, 1248000)
    metres = abs(grid[0] - helmert[0]) * 111320 * 0.68
    assert 0.3 < metres < 2  # the two differ by the Helmert's residual, about a metre here
