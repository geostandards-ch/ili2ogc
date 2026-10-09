"""Which INTERLIS attributes `convert-jsonfg` writes as a feature geometry, and their OGC API Schemas `format`."""

from interlis.metamodel.instance import MetaInstance
from interlis.xtf.schema import attributes_of, line_allows_arcs

GEOMETRY_KINDS = {"CoordType", "LineType"}


def is_solid3d(type_instance: MetaInstance | None) -> bool:
    """Whether `type_instance` is (an occurrence of) `Geometry3D_V2.Solid3D`.

    The 3D structures are matched by Name and shape, not by qualified model path: a type of an imported
    model is not registered in the symbol table of the model being built.
    """
    return (
        isinstance(type_instance, MetaInstance)
        and getattr(type_instance, "Kind", None) == "Structure"
        and getattr(type_instance, "Name", None) == "Solid3D"
        and "OuterShell" in attributes_of(type_instance)
    )


def is_curve3d(type_instance: MetaInstance | None) -> bool:
    """Whether `type_instance` is `Geometry3D_V2.PolylineStraight3D` or `.CompositeCurve3D`.

    `Pipe3D` (`EXTENDS CompositeCurve3D`) has no JSON-FG target and is excluded by its own `Name`.
    """
    if not isinstance(type_instance, MetaInstance) or getattr(type_instance, "Kind", None) != "Structure":
        return False
    name = getattr(type_instance, "Name", None)
    attrs = attributes_of(type_instance)
    if name == "PolylineStraight3D":
        return "Geometry" in attrs
    if name == "CompositeCurve3D":
        return "Simplified" in attrs
    return False


def is_composite_surface3d(type_instance: MetaInstance | None) -> bool:
    """Whether `type_instance` is `Geometry3D_V2.Tin3D`/`.SurfaceShell3D`/`.CompositeSurface3D`, standing alone.

    A `SurfaceShell3D` nested inside `Solid3D.OuterShell`/`.InnerShells` is part of the solid, not a geometry.
    """
    return (
        isinstance(type_instance, MetaInstance)
        and getattr(type_instance, "Kind", None) == "Structure"
        and getattr(type_instance, "Name", None) in {"Tin3D", "SurfaceShell3D", "CompositeSurface3D"}
        and "Simplified" in attributes_of(type_instance)
    )


def is_chbase_multisurface(type_instance: MetaInstance | None) -> bool:
    """Whether `type_instance` is `GeometryCHLV95_V1`/`GeometryCHLV03_V1`'s `MultiSurface`.

    A STRUCTURE wrapping `Surfaces: BAG {1..*} OF SurfaceStructure`, each holding one `Surface` line type:
    the CHBase convention for a multi-part surface.
    """
    return (
        isinstance(type_instance, MetaInstance)
        and getattr(type_instance, "Kind", None) == "Structure"
        and getattr(type_instance, "Name", None) == "MultiSurface"
        and "Surfaces" in attributes_of(type_instance)
    )


def is_pointcloud3d(type_instance: MetaInstance | None) -> bool:
    """Whether `type_instance` is (an occurrence of) `Geometry3D_V2.PointCloud3D`."""
    return (
        isinstance(type_instance, MetaInstance)
        and getattr(type_instance, "Kind", None) == "Structure"
        and getattr(type_instance, "Name", None) == "PointCloud3D"
        and "Points" in attributes_of(type_instance)
    )


_FORMAT_OF_TYPE = {
    "Point": "geometry-point",
    "MultiPoint": "geometry-multipoint",
    "LineString": "geometry-linestring",
    "MultiLineString": "geometry-multilinestring",
    "Polygon": "geometry-polygon",
    "MultiPolygon": "geometry-multipolygon",
}
_CURVED_TYPES = {
    (False, False): ("CircularString", "CompoundCurve"),
    (False, True): ("CurvePolygon",),
    (True, False): ("MultiCurve",),
    (True, True): ("MultiSurface",),
}


def geometry_types(kind: str | None, instance: MetaInstance | None, *, feature_level: bool) -> frozenset[str]:
    """The JSON-FG geometry types `convert-jsonfg` can write for an attribute; empty for a non-geometry one.

    A COORD or line type is a geometry value at any nesting depth; the 3D structures are geometries only as
    feature attributes (`feature_level`), where `convert-jsonfg` moves them to `place`. A line type admitting
    ARCS may also yield a curve type.
    """
    multi = bool(getattr(instance, "Multi", False))
    if kind == "CoordType":
        return frozenset({"MultiPoint" if multi else "Point"})
    if kind == "LineType":
        surface = getattr(instance, "Kind", None) in ("Surface", "Area")
        straight = (
            ("MultiPolygon" if multi else "Polygon") if surface else ("MultiLineString" if multi else "LineString")
        )
        curved = _CURVED_TYPES[(multi, surface)] if line_allows_arcs(instance) else ()
        return frozenset({straight, *curved})
    if not feature_level:
        return frozenset()
    if is_curve3d(instance):
        return frozenset({"LineString"})
    if is_composite_surface3d(instance):
        return frozenset({"MultiPolygon"})
    if is_pointcloud3d(instance):
        return frozenset({"MultiPoint"})
    if is_solid3d(instance):
        return frozenset({"Polyhedron"})
    if is_chbase_multisurface(instance):
        return frozenset({"MultiPolygon", "MultiSurface"})
    return frozenset()


def geometry_format(kind: str | None, instance: MetaInstance | None, *, feature_level: bool) -> str | None:
    """The OGC API Schemas `format` of a geometry attribute, or `None` for any other attribute.

    The OGC list only names the plain GeoJSON types, so any other (or mixed) set of types is `geometry-any`.
    """
    types = geometry_types(kind, instance, feature_level=feature_level)
    if not types:
        return None
    return (
        _FORMAT_OF_TYPE[next(iter(types))]
        if len(types) == 1 and next(iter(types)) in _FORMAT_OF_TYPE
        else "geometry-any"
    )
