"""View formation semantics shared by the SQL and JSON-FG converters.

Both targets must agree on which `AGGREGATION OF` columns are computable
and on how an `INSPECTION OF` path decomposes; classifying them here keeps
the two translations from drifting apart.
"""

from __future__ import annotations

from dataclasses import dataclass

from interlis.metamodel.instance import MetaInstance
from interlis.xtf.schema import SymbolTable, attributes_of, resolve_attribute, schema_members_of

STANDARD_COUNT_FUNCTIONS = frozenset({"INTERLIS.objectCount", "INTERLIS.elementCount"})


def _is_kind(expr: MetaInstance, suffix: str) -> bool:
    return expr._qualified_class.endswith(suffix)


def is_aggregates_marker(expr: MetaInstance) -> bool:
    """True for the bare `AGGREGATES` argument (an attribute path element with no `Ref`)."""
    if not _is_kind(expr, "PathOrInspFactor"):
        return False
    path_els = getattr(expr, "PathEls", None) or []
    return (
        len(path_els) == 1
        and getattr(path_els[0], "Kind", None) == "Attribute"
        and getattr(path_els[0], "Ref", None) is None
    )


def is_standard_count_call(factor: MetaInstance) -> bool:
    """True for `INTERLIS.objectCount(AGGREGATES)` / `INTERLIS.elementCount(AGGREGATES)`."""
    if not _is_kind(factor, "FunctionCall"):
        return False
    args = getattr(factor, "Arguments", None) or []
    arg_expr = getattr(args[0], "Expression", None) if len(args) == 1 else None
    return (
        getattr(factor, "Function", None) in STANDARD_COUNT_FUNCTIONS
        and arg_expr is not None
        and is_aggregates_marker(arg_expr)
    )


def aggregation_column_problem(attr: MetaInstance) -> str | None:
    """Return why an `AGGREGATION OF` view attribute cannot be computed, or `None` when it can.

    Computable: a path or constant (a group attribute), or a standard
    count of `AGGREGATES`. A user `FUNCTION` is declared without a body in
    INTERLIS (it is implemented by the application), so no converter can
    evaluate it.
    """
    name = getattr(attr, "Name", None)
    for factor in getattr(attr, "Derivates", None) or []:
        if _is_kind(factor, "PathOrInspFactor") or _is_kind(factor, "Constant") or is_standard_count_call(factor):
            continue
        if _is_kind(factor, "FunctionCall"):
            func = getattr(factor, "Function", None)
            return (
                f"AGGREGATION attribute {name!r} calls FUNCTION {func!r} over the implicit AGGREGATES bag - "
                "INTERLIS declares a FUNCTION without a body (an external function engine is needed); only "
                "INTERLIS.objectCount/elementCount(AGGREGATES) are computed"
            )
        return (
            f"AGGREGATION attribute {name!r} is an expression over the implicit AGGREGATES bag - "
            "needs a function engine"
        )
    return None


# --- INSPECTION OF a geometry attribute -------------------------------------

SURFACE_BOUNDARY = "surface_boundary"  # INSPECTION OF C -> surf          (one SurfaceBoundary per surface)
SURFACE_EDGE = "surface_edge"  # INSPECTION OF C -> surf -> Lines  (one SurfaceEdge per boundary line)
AREA_EDGE = "area_edge"  # AREA INSPECTION OF C -> area      (every boundary line of the partition once)
LINE_GEOMETRY = "line_geometry"  # INSPECTION OF C -> line          (one LineGeometry per line)
LINE_SEGMENT = "line_segment"  # INSPECTION OF C -> line -> Segments (one LineSegment per segment)


@dataclass(frozen=True)
class GeometryInspection:
    """An `INSPECTION OF` whose path ends on a SURFACE/AREA/POLYLINE attribute."""

    kind: str
    attr: str
    problem: str | None = None


def geometry_inspection(
    view: MetaInstance, base_view: MetaInstance, symbol_table: SymbolTable | None
) -> GeometryInspection | None:
    """Classify `view`'s inspection path as a geometry decomposition, or `None` for a structure path.

    eCH-0031 SS3.15: the inspection of a surface attribute yields
    `SurfaceBoundary` elements, of the `Lines` attribute `SurfaceEdge`
    elements; `AREA INSPECTION` yields each boundary line of the area
    partition once; the inspection of a line attribute yields
    `LineGeometry` elements, of `Segments` `LineSegment` elements.
    `problem` is set when the path is a geometry one but outside what
    the converters translate (a MULTI form, an unknown trailing name).
    """
    path = [str(n) for n in (getattr(view, "_inspection_path", None) or [])]
    if not path:
        return None
    members = schema_members_of(base_view, symbol_table) if symbol_table is not None else attributes_of(base_view)
    attr = members.get(path[0])
    if attr is None:
        return None
    resolved = resolve_attribute(attr)
    geometry_kind = getattr(resolved.type_instance, "Kind", None)
    if resolved.type_kind != "LineType" or geometry_kind not in ("Surface", "Area", "Polyline", "DirectedPolyline"):
        return None
    name = path[0]
    area = bool(getattr(view, "_inspection_area", False))
    surface = geometry_kind in ("Surface", "Area")
    if bool(getattr(resolved.type_instance, "Multi", False)):
        return GeometryInspection(
            SURFACE_BOUNDARY if surface else LINE_GEOMETRY,
            name,
            "INSPECTION of a MULTI line/surface attribute is not defined by the reference manual",
        )
    if area:
        if not surface or len(path) != 1:
            return GeometryInspection(
                AREA_EDGE, name, "AREA INSPECTION applies to a single AREA/SURFACE attribute (INSPECTION OF C -> attr)"
            )
        return GeometryInspection(AREA_EDGE, name)
    if surface:
        if len(path) == 1:
            return GeometryInspection(SURFACE_BOUNDARY, name)
        if len(path) == 2 and path[1] == "Lines":
            return GeometryInspection(SURFACE_EDGE, name)
        return GeometryInspection(SURFACE_BOUNDARY, name, f"unsupported inspection path below a surface: {path[1:]}")
    if len(path) == 1:
        return GeometryInspection(LINE_GEOMETRY, name)
    if len(path) == 2 and path[1] == "Segments":
        return GeometryInspection(LINE_SEGMENT, name)
    return GeometryInspection(LINE_GEOMETRY, name, f"unsupported inspection path below a line: {path[1:]}")


@dataclass(frozen=True)
class InspectionReading:
    """What one view attribute reads off the inspected geometry element."""

    what: str  # "geometry" | "endpoint" | "arcpoint" | "parent" | "thisarea" | "thatarea"
    field: str | None = None  # attribute name for parent / thisarea / thatarea


def _path_refs(factor: MetaInstance) -> tuple[list[str | None], list[str | None]]:
    els = getattr(factor, "PathEls", None) or []
    return [getattr(el, "Kind", None) for el in els], [getattr(el, "Ref", None) for el in els]


def inspection_reading(
    factor: MetaInstance | None, insp: GeometryInspection, base_alias: str
) -> tuple[InspectionReading | None, str | None]:
    """Classify the expression of one geometry-inspection view attribute: `(reading, None)` or `(None, why not)`.

    The element is addressed through the base alias (`Alias -> Geometry`).
    Readable members: the inspected attribute (or `Lines`) of a
    `SurfaceBoundary`, `Geometry` of a `SurfaceEdge`, the inspected line
    attribute of a `LineGeometry`, `SegmentEndPoint`/`ArcPoint` of a
    `LineSegment`; `PARENT -> field` on the surface/line boundary level
    reads the owning object, `THISAREA`/`THATAREA -> field` the one or two
    area objects bordering an `AREA INSPECTION` edge.
    """
    if factor is None or not _is_kind(factor, "PathOrInspFactor") or getattr(factor, "Inspection", None):
        return None, "is not a plain element path"
    kinds, refs = _path_refs(factor)
    if len(refs) == 2 and kinds[0] == "Parent" and refs[1]:
        if insp.kind in (SURFACE_BOUNDARY, LINE_GEOMETRY):
            return InspectionReading("parent", refs[1]), None
        return None, "navigates PARENT-> from an element whose parent is a geometry structure with no attributes"
    if len(refs) == 2 and kinds[0] in ("ThisArea", "ThatArea") and refs[1]:
        if insp.kind == AREA_EDGE:
            return InspectionReading(kinds[0].lower(), refs[1]), None
        return None, "uses THISAREA/THATAREA outside an AREA INSPECTION"
    if len(refs) == 2 and (refs[0] or "").lower() == base_alias.lower() and refs[1]:
        member = refs[1]
        if insp.kind == SURFACE_BOUNDARY and member in (insp.attr, "Lines"):
            return InspectionReading("geometry"), None
        if insp.kind in (SURFACE_EDGE, AREA_EDGE) and member == "Geometry":
            return InspectionReading("geometry"), None
        if insp.kind == LINE_GEOMETRY and member == insp.attr:
            return InspectionReading("geometry"), None
        if insp.kind == LINE_SEGMENT and member == "SegmentEndPoint":
            return InspectionReading("endpoint"), None
        if insp.kind == LINE_SEGMENT and member == "ArcPoint":
            return InspectionReading("arcpoint"), None
        return None, f"reads {member!r}, which this element type does not offer as a translatable value"
    return None, "has an element path outside Alias -> member / PARENT / THISAREA / THATAREA"
