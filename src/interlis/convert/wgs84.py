"""WGS 84 fallback `geometry` for a JSON-FG `place` GeoJSON cannot draw: circular arcs are stroked, then reprojected.

From LV95 the swisstopo grid shipped in `data/` is used (centimetres); any other CRS goes through PROJ's default path.
"""

from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

from pyproj import Transformer
from pyproj.exceptions import ProjError

_EPSG_URI = re.compile(r"/def/crs/EPSG/\d+/(\d+)$")
_SAGITTA = 0.02  # largest gap between a stroked arc and the true arc, in the units of the source CRS
_ARC_TYPES = frozenset({"CircularString", "CompoundCurve", "CurvePolygon", "MultiCurve", "MultiSurface"})
_DECIMALS = 7

Position = list[float]


def fallback_geometry(place: dict[str, Any] | None, crs_uri: str | None) -> dict[str, Any] | None:
    """The GeoJSON (WGS 84) geometry of a `place` holding arcs, or `None` when it has none or its CRS is unknown."""
    if place is None or place.get("type") not in _ARC_TYPES or crs_uri is None:
        return None
    match = _EPSG_URI.search(crs_uri)
    transformer = _transformer(int(match.group(1))) if match else None
    if transformer is None:
        return None
    return _reproject(_stroke(place), transformer)


_LV95_GRID = Path(__file__).parent / "data" / "ch_swisstopo_CHENyx06_ETRS.tif"
# LV95 (oblique Mercator on the Bessel ellipsoid), then swisstopo's CHENyx06 distortion grid to ETRS89, which GeoJSON
# readers take for WGS 84. The Helmert step PROJ otherwise uses is off by up to about a metre.
_LV95_PIPELINE = (
    "+proj=pipeline +step +inv +proj=somerc +lat_0=46.9524055555556 +lon_0=7.43958333333333 +k_0=1 "
    "+x_0=2600000 +y_0=1200000 +ellps=bessel +step +proj=hgridshift +grids={grid} "
    "+step +proj=unitconvert +xy_in=rad +xy_out=deg"
)


@lru_cache(maxsize=16)
def _transformer(epsg: int) -> Transformer | None:
    try:
        if epsg == 2056 and _LV95_GRID.is_file():
            return Transformer.from_pipeline(_LV95_PIPELINE.format(grid=_LV95_GRID))
        return Transformer.from_crs(f"EPSG:{epsg}", "EPSG:4326", always_xy=True)
    except ProjError:
        return None


def _stroke(geometry: dict[str, Any]) -> dict[str, Any]:
    """The same geometry with every arc replaced by a polyline (a GeoJSON geometry)."""
    kind = geometry["type"]
    if kind in ("CircularString", "LineString"):
        return {"type": "LineString", "coordinates": _line(geometry)}
    if kind == "CompoundCurve":
        return {"type": "LineString", "coordinates": _chain(geometry["geometries"])}
    if kind == "CurvePolygon":
        return {"type": "Polygon", "coordinates": [_chain([ring]) for ring in geometry["geometries"]]}
    if kind == "MultiCurve":
        return {"type": "MultiLineString", "coordinates": [_chain([curve]) for curve in geometry["geometries"]]}
    if kind == "MultiSurface":
        polygons = [_stroke(part) for part in geometry["geometries"]]
        return {"type": "MultiPolygon", "coordinates": [polygon["coordinates"] for polygon in polygons]}
    return geometry


def _chain(parts: list[dict[str, Any]]) -> list[Position]:
    """The positions of consecutive curves, the shared end point of each counted once."""
    points: list[Position] = []
    for part in parts:
        line = _chain(part["geometries"]) if part["type"] == "CompoundCurve" else _line(part)
        points.extend(line[1:] if points else line)
    return points


def _line(curve: dict[str, Any]) -> list[Position]:
    coords = curve["coordinates"]
    if curve["type"] == "LineString":
        return [list(p) for p in coords]
    points = [list(coords[0])]
    for i in range(0, len(coords) - 2, 2):
        points.extend(_arc(coords[i], coords[i + 1], coords[i + 2])[1:])
    return points


def _arc(a: Position, b: Position, c: Position) -> list[Position]:
    """Positions along the arc `a`, `b`, `c` (end points included); a straight line if the points are collinear."""
    ax, ay, bx, by, cx, cy = a[0], a[1], b[0], b[1], c[0], c[1]
    d = 2 * (ax * (by - cy) + bx * (cy - ay) + cx * (ay - by))
    if abs(d) < 1e-12:
        return [list(a), list(c)]
    ux = ((ax**2 + ay**2) * (by - cy) + (bx**2 + by**2) * (cy - ay) + (cx**2 + cy**2) * (ay - by)) / d
    uy = ((ax**2 + ay**2) * (cx - bx) + (bx**2 + by**2) * (ax - cx) + (cx**2 + cy**2) * (bx - ax)) / d
    radius = math.hypot(ax - ux, ay - uy)
    start, mid, end = (math.atan2(y - uy, x - ux) for x, y in ((ax, ay), (bx, by), (cx, cy)))
    sweep = (end - start) % (2 * math.pi)
    if (mid - start) % (2 * math.pi) > sweep:  # `b` is on the other side: the arc runs clockwise
        sweep -= 2 * math.pi
    step = 2 * math.acos(max(0.0, 1 - _SAGITTA / radius)) if radius > _SAGITTA else math.pi / 4
    count = max(2, math.ceil(abs(sweep) / max(step, 1e-3)))
    points = [list(a)]
    for k in range(1, count):
        angle = start + sweep * k / count
        extra = [a[2] + (c[2] - a[2]) * k / count] if len(a) > 2 and len(c) > 2 else []
        points.append([ux + radius * math.cos(angle), uy + radius * math.sin(angle), *extra])
    points.append(list(c))
    return points


def _reproject(geometry: dict[str, Any], transformer: Transformer) -> dict[str, Any] | None:
    def position(p: Position) -> Position:
        lon, lat = transformer.transform(p[0], p[1])
        return [round(lon, _DECIMALS), round(lat, _DECIMALS), *p[2:]]

    def walk(node: Any) -> Any:
        if node and isinstance(node[0], (int, float)):
            return position(node)
        return [walk(child) for child in node]

    try:
        return {"type": geometry["type"], "coordinates": walk(geometry["coordinates"])}
    except ProjError:
        return None
