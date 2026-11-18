"""Convert parsed XTF data into JSON-FG Features and FeatureCollections.

Geometry goes to "place" ("geometry" stays `null`, no reprojection); references become OID strings.
"""

import contextlib
import json
from collections.abc import Callable, Iterable, Iterator
from contextvars import ContextVar
from dataclasses import dataclass
from typing import Any

from interlis.builder.forward_refs import SymbolTable
from interlis.builder.repository import ModelRepository
from interlis.convert.constraint_eval import (
    UnsupportedExpressionError,
    _try_number,
    evaluate_expression,
)
from interlis.convert.geometry_kinds import (
    GEOMETRY_KINDS,
    is_chbase_multisurface,
    is_composite_surface3d,
    is_curve3d,
    is_pointcloud3d,
    is_solid3d,
)
from interlis.convert.view_formation import (
    LINE_GEOMETRY,
    LINE_SEGMENT,
    SURFACE_BOUNDARY,
    SURFACE_EDGE,
    GeometryInspection,
    InspectionReading,
    aggregation_column_problem,
    geometry_inspection,
    inspection_reading,
    is_standard_count_call,
)
from interlis.diagnostic_ids import REGISTRY
from interlis.metamodel.instance import MetaInstance
from interlis.runtime.parse import unquote_interlis_string
from interlis.xtf.crs import reason_rule, resolve_crs
from interlis.xtf.parse import RawNode, XtfObject, XtfTransfer
from interlis.xtf.schema import (
    ResolvedAttribute,
    attributes_of,
    home_symbol_table,
    is_class_compatible,
    is_integer_range,
    line_coord_type,
    resolve_attribute,
    resolve_class,
    schema_members_of,
    wrapped_reference,
)
from interlis.xtf.validate import (
    _LINE_KIND_MULTI_TAGS,
    _LINE_KIND_TAGS,
    _REFERENCE_TYPE_KINDS,
    _SURFACE_TAGS,
    BOUNDARY_TAGS,
    _axis_components,
    extract_reference,
    geom_tag,
    group_by_tag,
)

JSON_FG_VERSION = "1.0"
CONF_CORE = f"http://www.opengis.net/spec/json-fg-1/{JSON_FG_VERSION}/conf/core"
CONF_TYPES_SCHEMAS = f"http://www.opengis.net/spec/json-fg-1/{JSON_FG_VERSION}/conf/types-schemas"
CONF_CIRCULAR_ARCS = f"http://www.opengis.net/spec/json-fg-1/{JSON_FG_VERSION}/conf/circular-arcs"
CONF_POLYHEDRA = f"http://www.opengis.net/spec/json-fg-1/{JSON_FG_VERSION}/conf/polyhedra"

_SCALAR_KINDS = {"NumType", "TextType", "EnumType", "BooleanType", "FormattedType", "BlackboxType"}
# Geometry types that require CONF_CIRCULAR_ARCS in "conformsTo".
CIRCULAR_ARC_TYPES = frozenset({"CircularString", "CompoundCurve", "CurvePolygon", "MultiCurve", "MultiSurface"})
POLYHEDRA_TYPES = frozenset({"Polyhedron", "MultiPolyhedron"})
# `Geometry3D_V2.Solid3D` is matched by Name plus its `OuterShell` attribute (`is_solid3d`), not by qualified
# model path: `SymbolTable.qualified_name_of` cannot resolve an imported model.


def place_type_names(place: dict[str, Any] | None) -> set[str]:
    """Collect the "type" value(s) a `place` carries, unwrapping a `GeometryCollection` one level."""
    if place is None:
        return set()
    if place.get("type") == "GeometryCollection":
        return {g.get("type") for g in place.get("geometries", [])}
    return {place.get("type")}


def _scalar_value(resolved: ResolvedAttribute, node: RawNode) -> Any:
    """Read ONE scalar attribute's wire value; never raises (an unparsable value is passed through as-is)."""
    text = node.text
    kind = resolved.type_kind
    if text is None:
        return node.blank if node.blank and kind == "TextType" else None
    # FormattedType/BlackboxType pass through verbatim (their JSON Schema is `type: string`).
    if kind == "NumType":
        # Same integer-vs-number heuristic as this attribute's JSON Schema.
        as_int = is_integer_range(
            getattr(resolved.type_instance, "Min", None), getattr(resolved.type_instance, "Max", None)
        )
        try:
            return int(text) if as_int else float(text)
        except ValueError:
            pass
        try:
            number = float(text)
        except ValueError:
            return text
        return int(number) if as_int and number.is_integer() else number
    if kind == "BooleanType":
        return text == "true"
    return text  # TextType/EnumType: the wire text itself (EnumType: a dotted path)


@dataclass(frozen=True)
class UnmappedValue:
    """A wire value `convert-jsonfg` passed through untyped, or left out, for `collect_diagnostics`."""

    rule: str
    element_path: str
    detail: str
    tid: str | None


_UNMAPPED: ContextVar[list[UnmappedValue] | None] = ContextVar("_UNMAPPED", default=None)
_FEATURE: ContextVar[tuple[str, str | None]] = ContextVar("_FEATURE", default=("?", None))


_CRS_SEEN: ContextVar[set[tuple[str, str]] | None] = ContextVar("_CRS_SEEN", default=None)


def _record(rule: str, attr: MetaInstance, detail: str) -> None:
    sink = _UNMAPPED.get()
    if sink is not None:
        feature_type, tid = _FEATURE.get()
        sink.append(UnmappedValue(rule, f"{feature_type}.{getattr(attr, 'Name', '?')}", detail, tid))


@contextlib.contextmanager
def _feature_scope(feature_type: str, tid: str | None) -> Iterator[None]:
    token = _FEATURE.set((feature_type, tid))
    try:
        yield
    finally:
        _FEATURE.reset(token)


@contextlib.contextmanager
def _recording(sink: list[UnmappedValue] | None, seen: set[tuple[str, str]] | None = None) -> Iterator[None]:
    token = _UNMAPPED.set(sink)
    seen_token = _CRS_SEEN.set(set() if seen is None else seen)
    try:
        yield
    finally:
        _CRS_SEEN.reset(seen_token)
        _UNMAPPED.reset(token)


def _raw_value(node: RawNode, *, unwrap: bool) -> Any:
    """A value whose type didn't resolve, kept as on the wire: its text, its REF, or `{tag: value}` of its content.

    The shape the typed conversion gives a scalar, a reference or a STRUCTURE (`unwrap` skips the attribute
    element down to the structure's content wrapper), only untyped: numbers stay strings.
    """
    if not node.children:
        return node.text if node.text is not None else extract_reference(node)
    content = node.children[0] if unwrap and len(node.children) == 1 and node.children[0].children else node
    if all(not c.children and c.text is None for c in content.children):
        return extract_reference(content)
    grouped = group_by_tag(content.children)
    return {
        tag: _raw_value(nodes[0], unwrap=True) if len(nodes) == 1 else [_raw_value(n, unwrap=False) for n in nodes]
        for tag, nodes in grouped.items()
    }


def _attribute_value(
    resolved: ResolvedAttribute,
    raw_nodes: list[RawNode],
    *,
    symbol_table: SymbolTable | None = None,
    already_unwrapped: bool = False,
) -> Any:
    kind = resolved.type_kind
    if kind in _SCALAR_KINDS:
        return _scalar_value(resolved, raw_nodes[0])
    if kind in GEOMETRY_KINDS and raw_nodes:
        # A CoordType/LineType nested in a STRUCTURE or BAG/LIST element becomes a plain GeoJSON geometry in
        # `properties`; the Feature's own `place` is built separately (`_place_and_crs`).
        geometry = (
            _coord_geometry(resolved, raw_nodes[0]) if kind == "CoordType" else _line_geometry(resolved, raw_nodes[0])
        )
        return geometry
    if kind == "MultiValue":
        return _multi_value(resolved, raw_nodes, symbol_table=symbol_table)
    if kind in _REFERENCE_TYPE_KINDS:
        # A "ReferenceType"/"Class" kind may be a plain reference, an embedded role, or a STRUCTURE wrapping a
        # REFERENCE TO; `extract_reference` finds the REF in all three wire shapes.
        is_structure = kind == "Class" and getattr(resolved.type_instance, "Kind", None) == "Structure"
        if is_structure and wrapped_reference(resolved.type_instance) is None:
            return _structure_value(resolved, raw_nodes, symbol_table=symbol_table, already_unwrapped=already_unwrapped)
        ref = extract_reference(raw_nodes[0]) if raw_nodes else None
        if ref is not None:
            return ref
        # A genuine STRUCTURE (no REF anywhere) recurses into its own content.
        if kind == "Class" and getattr(resolved.type_instance, "Kind", None) == "Structure":
            return _structure_value(resolved, raw_nodes, symbol_table=symbol_table, already_unwrapped=already_unwrapped)
        # A CLASS/STRUCTURE RESTRICTION domain flattened by the exporter to a leaf scalar: return the text,
        # as the JSON Schema pipeline does (`x-reference-target`).
        leaf = raw_nodes[0] if raw_nodes else None
        if leaf is not None and not leaf.children and leaf.text is not None:
            return leaf.text
    # An unresolved Type (type_kind is None - e.g. a model not loaded via
    # --repo): the wire value, untyped, rather than nothing.
    value = _raw_value(raw_nodes[0], unwrap=not already_unwrapped) if raw_nodes else None
    _record(
        "JSONFG-TYPE-UNSUPPORTED",
        resolved.attr,
        f"type {kind or 'unresolved'}: " + ("passed through untyped" if value is not None else "left out (no value)"),
    )
    return value


def _structure_value(
    resolved: ResolvedAttribute,
    raw_nodes: list[RawNode],
    *,
    symbol_table: SymbolTable | None,
    already_unwrapped: bool,
) -> dict[str, Any]:
    """Recurse into a genuine STRUCTURE occurrence's own attributes.

    With `already_unwrapped=False`, `raw_nodes[0]` is the node named after the attribute and its only child is the
    content wrapper (one unwrapping level); with `True` (a `MultiValue` occurrence) it already is that wrapper.
    """
    node = raw_nodes[0] if raw_nodes else None
    if node is None:
        return {}
    wrapper = node if already_unwrapped else (node.children[0] if node.children else None)
    if wrapper is None or not isinstance(resolved.type_instance, MetaInstance):
        return {}
    return members_value(resolved.type_instance, group_by_tag(wrapper.children), symbol_table=symbol_table)


def _multi_value(resolved: ResolvedAttribute, raw_nodes: list[RawNode], *, symbol_table: SymbolTable | None) -> Any:
    """`BAG`/`LIST {m..n} OF X` -> a JSON array of converted occurrences.

    The wire carries ONE element named after the attribute with each occurrence as a direct child; each occurrence
    is re-dispatched through `_attribute_value` via a synthetic attribute for `MultiValue.BaseType`.
    """
    base_type = getattr(resolved.type_instance, "BaseType", None)
    if not isinstance(base_type, MetaInstance):
        _record("JSONFG-MULTIVALUE-UNRESOLVED", resolved.attr, "BAG/LIST OF element type unresolved: untyped")
        return [_raw_value(occurrence, unwrap=False) for node in raw_nodes for occurrence in node.children]
    base_kind = base_type._qualified_class.rsplit(".", 1)[-1]
    values: list[Any] = []
    for node in raw_nodes:
        for occurrence in node.children:
            occ_resolved = ResolvedAttribute(
                attr=resolved.attr, type_instance=base_type, type_kind=base_kind, mandatory=False
            )
            values.append(
                _attribute_value(occ_resolved, [occurrence], symbol_table=symbol_table, already_unwrapped=True)
            )
    return values


def _sql_identifier(name: str) -> str:
    """Lowercase, mirroring `convert/sql.py`'s `_sql_identifier` (not imported: that module imports this one).

    Unlike `build_tables`, no `_2`/`_3` suffix disambiguation of colliding class names is applied.
    """
    return name.lower()


def _child_row_features(obj: XtfObject, cls: MetaInstance, *, symbol_table: SymbolTable | None) -> list[dict[str, Any]]:
    """One Feature per `BAG`/`LIST OF` element of `obj`, as a child-table row.

    `featureType` is `"<parent_table>_<attr>"`; `properties` holds the parent foreign key, a `"seq"` index for `LIST`
    and the element's members (or `"value"`).
    """
    features: list[dict[str, Any]] = []
    if obj.tid is None:
        return features
    schema_attrs = schema_members_of(cls, symbol_table) if symbol_table is not None else attributes_of(cls)
    parent_table = _sql_identifier(getattr(cls, "Name", None) or "")
    fk_property = f"{parent_table}_fk"
    for name, attr in schema_attrs.items():
        resolved = resolve_attribute(attr)
        if resolved.type_kind != "MultiValue":
            continue
        raw_nodes = obj.attributes.get(name)
        if not raw_nodes:
            continue
        base_type = getattr(resolved.type_instance, "BaseType", None)
        if not isinstance(base_type, MetaInstance):
            continue
        base_kind = base_type._qualified_class.rsplit(".", 1)[-1]
        ordered = bool(getattr(resolved.type_instance, "Ordered", False))
        table_name = _sql_identifier(f"{parent_table}_{name}")
        index = 0
        for node in raw_nodes:
            for occurrence in node.children:
                properties: dict[str, Any] = {fk_property: obj.tid}
                if ordered:
                    properties["seq"] = index
                occ_resolved = ResolvedAttribute(
                    attr=attr, type_instance=base_type, type_kind=base_kind, mandatory=False
                )
                value = _attribute_value(occ_resolved, [occurrence], symbol_table=symbol_table, already_unwrapped=True)
                if base_kind == "Class" and getattr(base_type, "Kind", None) == "Structure" and isinstance(value, dict):
                    properties.update(value)
                else:
                    properties["value"] = value
                features.append(
                    {
                        "type": "Feature",
                        "id": f"{obj.tid}_{name}_{index}",
                        "featureType": table_name,
                        "geometry": None,
                        "properties": properties,
                    }
                )
                index += 1
    return features


def members_value(
    cls: MetaInstance, attrs: dict[str, list[RawNode]], *, symbol_table: SymbolTable | None
) -> dict[str, Any]:
    """Convert every attribute present in `attrs` against `cls`'s schema into a plain dict.

    Shared by `object_to_feature` and `_structure_value`; an attribute absent from the schema is skipped
    (`validate()` flags it).
    """
    schema_attrs = schema_members_of(cls, symbol_table) if symbol_table is not None else attributes_of(cls)
    resolved_attrs = {name: resolve_attribute(attr) for name, attr in schema_attrs.items()}
    result: dict[str, Any] = {}
    for name, raw_nodes in attrs.items():
        if name not in resolved_attrs:
            continue
        value = _attribute_value(resolved_attrs[name], raw_nodes, symbol_table=symbol_table)
        kind = resolved_attrs[name].type_kind
        if value is not None or kind in _SCALAR_KINDS or kind in GEOMETRY_KINDS:
            result[name] = value
    return result


# --- Geometry (`"place"`): readers return `None` for anything not representable, so it stays in "properties".


def _positions_from(node: RawNode, prefix: str) -> list[float] | None:
    """Read `{prefix}1`, `{prefix}2`, ... as one position.

    Shared by COORD (`C`) and an ARC's intermediate point (`A`).
    """
    components = _axis_components(node, prefix)
    if not components:
        return None
    try:
        return [float(c.text) for c in components]  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def read_coord(node: RawNode) -> list[float] | None:
    if geom_tag(node) != "COORD":
        return None
    return _positions_from(node, "C")


def read_arc(node: RawNode) -> tuple[list[float], list[float]] | None:
    """Read an ArcSegment as `(intermediate point, end point)`.

    The intermediate point (A1/A2) is always 2D even when the end point is 3D; the optional radius is not read.
    """
    if geom_tag(node) != "ARC":
        return None
    end = _positions_from(node, "C")
    mid = _positions_from(node, "A")
    return None if end is None or mid is None else (mid, end)


def _read_polyline(node: RawNode) -> list[list[float]] | dict[str, Any] | None:
    """Read a POLYLINE into a plain position list (straight-only) or a curved geometry object.

    Consecutive segments of one kind form a run (`LineString` / `CircularString`, chained arcs sharing endpoints);
    a single arc run returns a bare `CircularString`, mixed runs a `CompoundCurve`.
    """
    if geom_tag(node) != "POLYLINE" or not node.children:
        return None
    segments = node.children
    if geom_tag(segments[0]) != "COORD":
        return None
    start = read_coord(segments[0])
    if start is None:
        return None
    parts: list[dict[str, Any]] = []
    kind = "line"
    points: list[list[float]] = [start]
    for seg in segments[1:]:
        tag = geom_tag(seg)
        if tag == "COORD":
            pos = read_coord(seg)
            if pos is None:
                return None
            if kind == "arc":
                parts.append({"type": "CircularString", "coordinates": points})
                kind = "line"
                points = [points[-1]]
            points.append(pos)
        elif tag == "ARC":
            arc = read_arc(seg)
            if arc is None:
                return None
            mid, end = arc
            if kind == "line":
                if len(points) > 1:
                    parts.append({"type": "LineString", "coordinates": points})
                    points = [points[-1]]
                kind = "arc"
            points.extend((mid, end))
        else:
            return None  # custom LINE FORM segment - not representable here
    parts.append({"type": "CircularString" if kind == "arc" else "LineString", "coordinates": points})
    if len(parts) == 1:
        sole = parts[0]
        return sole["coordinates"] if sole["type"] == "LineString" else sole
    return {"type": "CompoundCurve", "geometries": parts}


def _read_boundary(node: RawNode) -> list[list[float]] | dict[str, Any] | None:
    """Read one BOUNDARY: its consecutive POLYLINE pieces (Randlinien) joined into a single closed line."""
    if geom_tag(node) not in BOUNDARY_TAGS:
        return None
    pieces: list[dict[str, Any]] = []
    for polyline in (c for c in node.children if geom_tag(c) == "POLYLINE"):
        value = _read_polyline(polyline)
        if value is None:
            return None
        pieces.append(_as_line(value))
    if not pieces:
        return None
    joined = _join_pieces(pieces)
    return joined["coordinates"] if joined["type"] == "LineString" else joined


def _read_surface(node: RawNode) -> list[list[list[float]]] | dict[str, Any] | None:
    """Read a SURFACE/AREA into plain rings (straight-only) or a `CurvePolygon` (any ring with an arc).

    A `CurvePolygon` wraps every ring, even a straight one, as a full geometry object.
    """
    if geom_tag(node) not in _SURFACE_TAGS:
        return None
    boundaries = [c for c in node.children if geom_tag(c) in BOUNDARY_TAGS]
    if not boundaries:
        return None
    rings: list[list[list[float]] | dict[str, Any]] = []
    any_curved = False
    for boundary in boundaries:
        ring = _read_boundary(boundary)
        if ring is None:
            return None
        if isinstance(ring, dict):
            any_curved = True
        rings.append(ring)  # first = outer boundary, eCH-0031 SS4.3.11.15 (order-only, no tag distinction)
    if any_curved:
        geometries = [r if isinstance(r, dict) else {"type": "LineString", "coordinates": r} for r in rings]
        return {"type": "CurvePolygon", "geometries": geometries}
    return rings


def _coord_geometry(resolved: ResolvedAttribute, node: RawNode) -> dict[str, Any] | None:
    """`node` is the attribute's own node (e.g. `<AccidentLocation>`) - its 1st child is COORD/MULTICOORD."""
    coord_type = resolved.type_instance
    multi = bool(getattr(coord_type, "Multi", False))
    expected = "MULTICOORD" if multi else "COORD"
    child = node.children[0] if node.children else None
    if child is None or geom_tag(child) != expected:
        return None
    if not multi:
        pos = read_coord(child)
        return None if pos is None else {"type": "Point", "coordinates": pos}
    positions: list[list[float]] = []
    for c in (c for c in child.children if geom_tag(c) == "COORD"):
        pos = read_coord(c)
        if pos is None:
            return None
        positions.append(pos)
    return None if not positions else {"type": "MultiPoint", "coordinates": positions}


def _line_geometry(resolved: ResolvedAttribute, node: RawNode) -> dict[str, Any] | None:
    """Build the JSON-FG geometry for a line attribute, or `None` if it cannot be represented.

    In the multi case any curved part makes the whole attribute a `MultiCurve`/`MultiSurface`.
    """
    line_type = resolved.type_instance
    kind = getattr(line_type, "Kind", None)
    multi = bool(getattr(line_type, "Multi", False))
    single_tags = _LINE_KIND_TAGS.get(kind)
    if single_tags is None:
        return None
    expected_tags = _LINE_KIND_MULTI_TAGS[kind] if multi else single_tags
    child = node.children[0] if node.children else None
    if child is None or geom_tag(child) not in expected_tags:
        return None
    is_polyline = single_tags == frozenset({"POLYLINE"})
    reader = _read_polyline if is_polyline else _read_surface
    single_type = "LineString" if is_polyline else "Polygon"
    multi_type = "MultiLineString" if is_polyline else "MultiPolygon"
    curved_multi_type = "MultiCurve" if is_polyline else "MultiSurface"
    if not multi:
        value = reader(child)
        if value is None:
            return None
        return value if isinstance(value, dict) else {"type": single_type, "coordinates": value}
    values = []
    any_curved = False
    for part in (c for c in child.children if geom_tag(c) in single_tags):
        value = reader(part)
        if value is None:
            return None
        if isinstance(value, dict):
            any_curved = True
        values.append(value)
    if not values:
        return None
    if any_curved:
        geometries = [v if isinstance(v, dict) else {"type": single_type, "coordinates": v} for v in values]
        return {"type": curved_multi_type, "geometries": geometries}
    return {"type": multi_type, "coordinates": values}


def _resolved_crs_uri(
    coord_type: MetaInstance | None,
    attr: MetaInstance,
    *,
    symbol_table: SymbolTable | None,
    repository: ModelRepository | None,
) -> str | None:
    """The `coordRefSys` URI of `coord_type`, recording once per attribute why it has none (or what looked odd)."""
    resolution = resolve_crs(coord_type, symbol_table=symbol_table, repository=repository)
    findings = list(resolution.warnings)
    if resolution.reason is not None:
        findings.append((reason_rule(resolution.reason, "JSONFG"), resolution.detail))
    seen = _CRS_SEEN.get()
    for rule, detail in findings:
        key = (rule, f"{getattr(attr, 'Name', '?')}:{detail}")
        if seen is not None and key not in seen:
            seen.add(key)
            _record(rule, attr, detail)
    return resolution.uri


def _place_and_crs(
    resolved: ResolvedAttribute,
    node: RawNode,
    *,
    symbol_table: SymbolTable | None = None,
    repository: ModelRepository | None = None,
) -> tuple[dict[str, Any], str] | None:
    """Return `(geometry, coordRefSys)` for one geometry-typed attribute occurrence, or `None`."""
    if resolved.type_kind == "CoordType":
        geometry = _coord_geometry(resolved, node)
        coord_type = resolved.type_instance
    elif resolved.type_kind == "LineType":
        geometry = _line_geometry(resolved, node)
        coord_type = line_coord_type(resolved.type_instance)
    else:
        return None
    if geometry is None:
        return None
    crs = _resolved_crs_uri(coord_type, resolved.attr, symbol_table=symbol_table, repository=repository)
    return None if crs is None else (geometry, crs)


def _resolve_named_attr(cls: MetaInstance, name: str) -> ResolvedAttribute | None:
    """Resolve `cls`'s attribute `name`, or `None` if it has none - shared by the 3D/CHBase coord-type walkers."""
    attr = attributes_of(cls).get(name)
    return resolve_attribute(attr) if attr is not None else None


def _resolve_base_type(cls: MetaInstance, attr_name: str) -> MetaInstance | None:
    """Resolve `cls.<attr_name>`, then unwrap its `.type_instance.BaseType`."""
    attr = _resolve_named_attr(cls, attr_name)
    base_type = getattr(attr.type_instance, "BaseType", None) if attr is not None else None
    return base_type if isinstance(base_type, MetaInstance) else None


def _place_and_crs_via(
    coord_type_fn: Callable[[MetaInstance], MetaInstance | None],
    geometry_fn: Callable[[MetaInstance, dict[str, Any]], dict[str, Any] | None],
) -> Callable[[ResolvedAttribute, Any, SymbolTable | None, ModelRepository | None], tuple[dict[str, Any], str] | None]:
    """Build one `*_place_and_crs` function from its shape-specific coord-type and geometry builders."""

    def _place_and_crs_for_shape(
        resolved: ResolvedAttribute,
        value: Any,
        symbol_table: SymbolTable | None,
        repository: ModelRepository | None = None,
    ) -> tuple[dict[str, Any], str] | None:
        if not isinstance(resolved.type_instance, MetaInstance) or not isinstance(value, dict):
            return None
        crs = _resolved_crs_uri(
            coord_type_fn(resolved.type_instance), resolved.attr, symbol_table=symbol_table, repository=repository
        )
        if crs is None:
            return None
        geometry = geometry_fn(resolved.type_instance, value)
        return None if geometry is None else (geometry, crs)

    return _place_and_crs_for_shape


def _solid3d_coord_type(solid_class: MetaInstance) -> MetaInstance | None:
    """Walk `Solid3D.OuterShell -> Simplified -> Triangle3D.Geometry` to its `CoordType`, for CRS lookup."""
    shell = _resolve_named_attr(solid_class, "OuterShell")
    shell_class = shell.type_instance if shell is not None else None
    if not isinstance(shell_class, MetaInstance):
        return None
    triangle_class = _resolve_base_type(shell_class, "Simplified")
    if triangle_class is None:
        return None
    geometry = _resolve_named_attr(triangle_class, "Geometry")
    if geometry is None or geometry.type_kind != "LineType":
        return None
    return line_coord_type(geometry.type_instance)


def _polyhedron_shell(shell: Any) -> list[Any] | None:
    """One JSON-FG Polyhedron shell (a patch list) from a decoded `SurfaceShell3D` dict.

    Only `Simplified` (straight-only triangles) feeds the shell; `Native` has no guaranteed straight-only form.
    """
    if not isinstance(shell, dict):
        return None
    simplified = shell.get("Simplified")
    if not isinstance(simplified, list) or not simplified:
        return None
    patches: list[Any] = []
    for item in simplified:
        geometry = item.get("Geometry") if isinstance(item, dict) else None
        if not isinstance(geometry, dict) or geometry.get("type") != "Polygon":
            return None
        coordinates = geometry.get("coordinates")
        if not isinstance(coordinates, list):
            return None
        patches.append(coordinates)
    return patches


def _solid3d_polyhedron(value: dict[str, Any]) -> dict[str, Any] | None:
    """A decoded `Geometry3D_V2.Solid3D` value -> a JSON-FG `Polyhedron` (outer shell, then each inner shell)."""
    outer_shell = _polyhedron_shell(value.get("OuterShell"))
    if outer_shell is None:
        return None
    shells = [outer_shell]
    for inner in value.get("InnerShells") or []:
        inner_shell = _polyhedron_shell(inner)
        if inner_shell is None:
            return None
        shells.append(inner_shell)
    return {"type": "Polyhedron", "coordinates": shells}


# `value` is the attribute's already-decoded `properties` value, reused rather than re-decoding the wire nodes.
_solid3d_place_and_crs = _place_and_crs_via(
    _solid3d_coord_type, lambda _type_instance, value: _solid3d_polyhedron(value)
)


def _curve3d_coord_type(type_instance: MetaInstance) -> MetaInstance | None:
    """Walk a `PolylineStraight3D`/`CompositeCurve3D` type down to its ultimate `CoordType`, for CRS lookup."""
    name = getattr(type_instance, "Name", None)
    if name == "PolylineStraight3D":
        geometry = _resolve_named_attr(type_instance, "Geometry")
        if geometry is None or geometry.type_kind != "LineType":
            return None
        return line_coord_type(geometry.type_instance)
    if name == "CompositeCurve3D":
        segment_class = _resolve_base_type(type_instance, "Simplified")
        if segment_class is None:
            return None
        return _curve3d_coord_type(segment_class)
    return None


def _composite_curve3d_linestring(value: dict[str, Any]) -> dict[str, Any] | None:
    """A decoded `CompositeCurve3D` value -> one JSON-FG `LineString`.

    `Simplified` is a connected sequence, so segments concatenate into a single line; the shared joint point is
    dropped only when identical on both sides.
    """
    segments = value.get("Simplified")
    if not isinstance(segments, list) or not segments:
        return None
    coordinates: list[Any] = []
    for segment in segments:
        geometry = segment.get("Geometry") if isinstance(segment, dict) else None
        if not isinstance(geometry, dict) or geometry.get("type") != "LineString":
            return None
        points = geometry.get("coordinates")
        if not isinstance(points, list) or not points:
            return None
        if coordinates and coordinates[-1] == points[0]:
            coordinates.extend(points[1:])
        else:
            coordinates.extend(points)
    return {"type": "LineString", "coordinates": coordinates} if coordinates else None


def _curve3d_geometry(type_instance: MetaInstance, value: dict[str, Any]) -> dict[str, Any] | None:
    """The JSON-FG `LineString` for one decoded `Curve3D` value (a single `Geometry`, or concatenated segments)."""
    if getattr(type_instance, "Name", None) == "PolylineStraight3D":
        geometry = value.get("Geometry")
        return geometry if isinstance(geometry, dict) and geometry.get("type") == "LineString" else None
    return _composite_curve3d_linestring(value)


_curve3d_place_and_crs = _place_and_crs_via(_curve3d_coord_type, _curve3d_geometry)


def _composite_surface3d_coord_type(type_instance: MetaInstance) -> MetaInstance | None:
    """Walk a `Tin3D`/`SurfaceShell3D`/`CompositeSurface3D` type down to its ultimate `CoordType`, for CRS lookup."""
    triangle_class = _resolve_base_type(type_instance, "Simplified")
    if triangle_class is None:
        return None
    geometry = _resolve_named_attr(triangle_class, "Geometry")
    if geometry is None or geometry.type_kind != "LineType":
        return None
    return line_coord_type(geometry.type_instance)


def _composite_surface3d_multipolygon(value: dict[str, Any]) -> dict[str, Any] | None:
    """A decoded `Tin3D`/`SurfaceShell3D`/`CompositeSurface3D` value -> `MultiPolygon` of triangle patches.

    A closed `SurfaceShell3D` is not promoted to a `Polyhedron`: watertightness cannot be checked from the wire.
    """
    patches = _polyhedron_shell(value)
    return None if patches is None else {"type": "MultiPolygon", "coordinates": patches}


_composite_surface3d_place_and_crs = _place_and_crs_via(
    _composite_surface3d_coord_type, lambda _type_instance, value: _composite_surface3d_multipolygon(value)
)


def _chbase_multisurface_surface_type(type_instance: MetaInstance) -> ResolvedAttribute | None:
    """Resolve `MultiSurface.Surfaces`'s element type (`SurfaceStructure`) down to its own `Surface` LineType."""
    element_class = _resolve_base_type(type_instance, "Surfaces")
    if element_class is None:
        return None
    resolved = _resolve_named_attr(element_class, "Surface")
    return resolved if resolved is not None and resolved.type_kind == "LineType" else None


def _chbase_multisurface_coord_type(type_instance: MetaInstance) -> MetaInstance | None:
    """`GeometryCHLV95_V1`/`GeometryCHLV03_V1` `MultiSurface` counterpart of `_composite_surface3d_coord_type`."""
    surface = _chbase_multisurface_surface_type(type_instance)
    return None if surface is None else line_coord_type(surface.type_instance)


def _chbase_multisurface_multipolygon(value: dict[str, Any]) -> dict[str, Any] | None:
    """A decoded `MultiSurface` value -> `MultiPolygon`, or `MultiSurface` when any member is a `CurvePolygon`."""
    surfaces = value.get("Surfaces")
    if not isinstance(surfaces, list) or not surfaces:
        return None
    geometries: list[dict[str, Any]] = []
    for item in surfaces:
        surface = item.get("Surface") if isinstance(item, dict) else None
        if not isinstance(surface, dict):
            return None
        geometries.append(surface)
    if any(g.get("type") == "CurvePolygon" for g in geometries):
        return {"type": "MultiSurface", "geometries": geometries}
    return {"type": "MultiPolygon", "coordinates": [g["coordinates"] for g in geometries]}


_chbase_multisurface_place_and_crs = _place_and_crs_via(
    _chbase_multisurface_coord_type, lambda _type_instance, value: _chbase_multisurface_multipolygon(value)
)


def _pointcloud3d_coord_type(type_instance: MetaInstance) -> MetaInstance | None:
    """The `CoordType` of `PointCloud3D.Points` (`BAG {1..*} OF Coord3`), for CRS lookup."""
    return _resolve_base_type(type_instance, "Points")


def _pointcloud3d_multipoint(value: dict[str, Any]) -> dict[str, Any] | None:
    """A `PointCloud3D` value (already decoded by `_structure_value`) -> a JSON-FG `MultiPoint`."""
    points = value.get("Points")
    if not isinstance(points, list) or not points:
        return None
    positions: list[Any] = []
    for point in points:
        if not isinstance(point, dict) or point.get("type") != "Point":
            return None
        coordinates = point.get("coordinates")
        if not isinstance(coordinates, list):
            return None
        positions.append(coordinates)
    return {"type": "MultiPoint", "coordinates": positions}


_pointcloud3d_place_and_crs = _place_and_crs_via(
    _pointcloud3d_coord_type, lambda _type_instance, value: _pointcloud3d_multipoint(value)
)


def geometry_coord_type(resolved: ResolvedAttribute) -> MetaInstance | None:
    """The `CoordType` whose axes and CRS a geometry attribute uses, or `None` for any other attribute."""
    if resolved.type_kind == "CoordType":
        return resolved.type_instance
    if resolved.type_kind == "LineType":
        return line_coord_type(resolved.type_instance)
    for matches, coord_type_of in (
        (is_solid3d, _solid3d_coord_type),
        (is_curve3d, _curve3d_coord_type),
        (is_composite_surface3d, _composite_surface3d_coord_type),
        (is_pointcloud3d, _pointcloud3d_coord_type),
        (is_chbase_multisurface, _chbase_multisurface_coord_type),
    ):
        if matches(resolved.type_instance):
            return coord_type_of(resolved.type_instance)
    return None


def _feature_schema_ref(schema_url: str, feature_type: str) -> str:
    """A `$defs` entry of a `model_to_json_schema` document, addressed by URI fragment.

    `schema_url` is caller-supplied; `feature_type` is the short class `Name` that document keys `$defs` by.
    """
    return f"{schema_url}#/$defs/{feature_type}"


def object_to_feature(
    obj: XtfObject,
    cls: MetaInstance,
    *,
    standalone: bool = True,
    symbol_table: SymbolTable | None = None,
    repository: ModelRepository | None = None,
    schema_url: str | None = None,
    omit_multivalue: bool = False,
) -> dict[str, Any]:
    """Convert one XtfObject (of the resolved class `cls`) into a JSON-FG Feature.

    Geometry attributes become "place" (several become a `GeometryCollection`); others stay in "properties".
    `standalone=False` omits "conformsTo", which only a root object carries.
    """
    with _feature_scope(getattr(cls, "Name", None) or obj.qualified_class, obj.tid):
        return _object_to_feature(
            obj,
            cls,
            standalone=standalone,
            symbol_table=symbol_table,
            repository=repository,
            schema_url=schema_url,
            omit_multivalue=omit_multivalue,
        )


def _object_to_feature(
    obj: XtfObject,
    cls: MetaInstance,
    *,
    standalone: bool = True,
    symbol_table: SymbolTable | None = None,
    repository: ModelRepository | None = None,
    schema_url: str | None = None,
    omit_multivalue: bool = False,
) -> dict[str, Any]:
    """Body of `object_to_feature`, within its feature scope."""
    schema_attrs = schema_members_of(cls, symbol_table) if symbol_table is not None else attributes_of(cls)
    resolved_attrs = {name: resolve_attribute(attr) for name, attr in schema_attrs.items()}
    properties = members_value(cls, obj.attributes, symbol_table=symbol_table)
    if omit_multivalue:
        for name, resolved in resolved_attrs.items():
            if resolved.type_kind == "MultiValue":
                properties.pop(name, None)

    place: dict[str, Any] | None = None
    crs_uri: str | None = None
    geometry_names = [name for name, r in resolved_attrs.items() if r.type_kind in GEOMETRY_KINDS]
    geometry_names_set = set(geometry_names)
    solid3d_names = {name for name, r in resolved_attrs.items() if is_solid3d(r.type_instance)}
    curve3d_names = {name for name, r in resolved_attrs.items() if is_curve3d(r.type_instance)}
    composite_surface3d_names = {name for name, r in resolved_attrs.items() if is_composite_surface3d(r.type_instance)}
    pointcloud3d_names = {name for name, r in resolved_attrs.items() if is_pointcloud3d(r.type_instance)}
    chbase_multisurface_names = {name for name, r in resolved_attrs.items() if is_chbase_multisurface(r.type_instance)}
    # One pass in declaration order so a mixed class gets its GeometryCollection in that order.
    resolved_geometries: list[tuple[str, dict[str, Any], str]] = []
    for name, resolved in resolved_attrs.items():
        raw_nodes = obj.attributes.get(name)
        if not raw_nodes:
            continue
        if name in geometry_names_set:
            result = _place_and_crs(resolved, raw_nodes[0], symbol_table=symbol_table, repository=repository)
        elif name in solid3d_names:
            result = _solid3d_place_and_crs(resolved, properties.get(name), symbol_table, repository)
        elif name in curve3d_names:
            result = _curve3d_place_and_crs(resolved, properties.get(name), symbol_table, repository)
        elif name in composite_surface3d_names:
            result = _composite_surface3d_place_and_crs(resolved, properties.get(name), symbol_table, repository)
        elif name in pointcloud3d_names:
            result = _pointcloud3d_place_and_crs(resolved, properties.get(name), symbol_table, repository)
        elif name in chbase_multisurface_names:
            result = _chbase_multisurface_place_and_crs(resolved, properties.get(name), symbol_table, repository)
        else:
            continue
        if result is not None:
            resolved_geometries.append((name, *result))
    placed_names: set[str] = set()
    if len(resolved_geometries) == 1:
        geom_name, place, crs_uri = resolved_geometries[0]
        properties.pop(geom_name, None)
        placed_names.add(geom_name)
    elif len(resolved_geometries) >= 2:
        crs_values = {crs for _, _, crs in resolved_geometries}
        if len(crs_values) == 1:
            place = {"type": "GeometryCollection", "geometries": [g for _, g, _ in resolved_geometries]}
            crs_uri = crs_values.pop()
            for geom_name, _, _ in resolved_geometries:
                properties.pop(geom_name, None)
                placed_names.add(geom_name)

    # A top-level geometry attribute that did not become `place` keeps its GeoJSON value in `properties`;
    # only an unreadable one (`None`) is left out.
    for name in geometry_names:
        if name in properties and name not in placed_names and not isinstance(properties[name], dict):
            del properties[name]
            _record("JSONFG-GEOMETRY-UNREADABLE", resolved_attrs[name].attr, "geometry not readable: left out")

    feature: dict[str, Any] = {"type": "Feature"}
    if standalone:
        conforms_to = [CONF_CORE, CONF_TYPES_SCHEMAS]
        place_types = place_type_names(place)
        if place_types & CIRCULAR_ARC_TYPES:
            conforms_to.append(CONF_CIRCULAR_ARCS)
        if place_types & POLYHEDRA_TYPES:
            conforms_to.append(CONF_POLYHEDRA)
        feature["conformsTo"] = conforms_to
    if obj.tid is not None:
        feature["id"] = obj.tid
    feature_type = getattr(cls, "Name", None) or obj.qualified_class
    feature["featureType"] = feature_type
    if standalone and schema_url is not None:
        feature["featureSchema"] = _feature_schema_ref(schema_url, feature_type)
    feature["geometry"] = None
    if place is not None:
        feature["place"] = place
        feature["coordRefSys"] = crs_uri
    feature["properties"] = properties
    return feature


# --- VIEW evaluation: each VIEW kind is evaluated in memory into Features; an unsupported `WHERE` skips the VIEW.

_UNSET = object()

_ARITHMETIC_OPS = {"Mult", "Div", "Plus", "Minus"}


class _ObjView(dict):
    """A base object's value in a view-WHERE properties dict: a dict of its attributes that `==` its OID string."""

    def __init__(self, tid: str | None, attrs: dict[str, Any]) -> None:
        super().__init__(attrs)
        self._tid = tid

    def __eq__(self, other: Any) -> bool:
        return self._tid == (other._tid if isinstance(other, _ObjView) else other)

    def __ne__(self, other: Any) -> bool:
        return not self.__eq__(other)

    __hash__ = None  # type: ignore[assignment]


def _view_alias(base: MetaInstance) -> str | None:
    return getattr(base, "Name", None) or getattr(getattr(base, "BaseView", None), "Name", None)


def raw_node_value(node: RawNode) -> Any:
    """One attribute occurrence -> a comparable value.

    A referenced OID, a nested dict, or a (numeric-coerced) scalar.
    """
    ref_tid = extract_reference(node)
    if ref_tid is not None:
        return ref_tid
    if node.children:  # a STRUCTURE occurrence - navigable one more hop
        nested: dict[str, Any] = {}
        for child in node.children:
            nested.setdefault(child.tag, raw_node_value(child))
        return nested
    return _try_number(node.text, node.text) if node.text is not None else None


def _obj_view(obj: XtfObject, by_tid: dict[str, XtfObject], seen: frozenset[str]) -> _ObjView:
    """One object as an `_ObjView`, each reference attribute resolved to the target's own `_ObjView`.

    Lets a view `WHERE` navigate association hops; `seen` breaks reference cycles.
    """
    if obj.tid is not None and obj.tid in seen:
        return _ObjView(obj.tid, {})
    next_seen = seen | ({obj.tid} if obj.tid is not None else frozenset())
    attrs: dict[str, Any] = {}
    for name, nodes in obj.attributes.items():
        if not nodes:
            continue
        value = raw_node_value(nodes[0])
        if isinstance(value, str) and value in by_tid:
            attrs[name] = _obj_view(by_tid[value], by_tid, next_seen)
        else:
            attrs[name] = value
    return _ObjView(obj.tid, attrs)


def _combo_properties(
    aliases: list[str | None],
    combo: list[XtfObject | None],
    by_tid: dict[str, XtfObject],
) -> dict[str, _ObjView]:
    properties: dict[str, _ObjView] = {}
    for alias, obj in zip(aliases, combo):
        if alias is None:
            continue
        properties[alias] = _ObjView(None, {}) if obj is None else _obj_view(obj, by_tid, frozenset())
    return properties


def _view_where_unsupported(expr: MetaInstance) -> str | None:
    """Return a reason `evaluate_expression` can't evaluate `expr`'s SHAPE (data-independent), or `None`."""
    qname = expr._qualified_class.rsplit(".", 1)[-1]
    if qname == "CompoundExpr":
        if getattr(expr, "Operation", None) in _ARITHMETIC_OPS:
            return f"arithmetic operator {expr.Operation!r}"
        return next(
            (r for sub in (getattr(expr, "SubExpressions", None) or []) if (r := _view_where_unsupported(sub))), None
        )
    if qname == "UnaryExpr":
        if getattr(expr, "Operation", None) not in ("Not", "Defined"):
            return f"unary operator {getattr(expr, 'Operation', None)!r}"
        sub = getattr(expr, "SubExpression", None)
        return _view_where_unsupported(sub) if isinstance(sub, MetaInstance) else None
    if qname in ("PathOrInspFactor", "Constant"):
        return None
    return f"{qname} node (needs THIS/PARENT/aggregate/function-call context)"


def unsupported_view_reason(view: MetaInstance) -> str | None:
    """Return why `evaluate_view` can't evaluate `view`, or `None` if it can.

    Reasons: an unavailable base model (provide it via `--repo`), a `WHERE` construct outside the CONSTRAINT
    evaluator's scope, or an AGGREGATION column that is an external-function result.
    """
    kind = getattr(view, "FormationKind", None)
    if kind not in ("Projection", "Join", "Union", "Aggregation", "Inspection"):
        return f"unknown FormationKind {kind!r}"
    bases = [
        b
        for b in getattr(view, "RenamedBaseView", None) or []
        if isinstance(b, MetaInstance) and isinstance(b.BaseView, MetaInstance)
    ]
    if not bases:
        return "base model not resolvable - pass it via --repo"
    if kind == "Inspection" and not _inspection_path(view):
        return "INSPECTION path (the '-> attribute' chain) was not built - InterlisModelBuilder gap"
    if kind == "Inspection" and (problem := _geometry_inspection_problem(view, bases[0])) is not None:
        return problem
    if kind == "Aggregation":
        columns = list(getattr(view, "ClassAttribute", None) or [])
        for attr in columns:
            if (problem := aggregation_column_problem(attr)) is not None:
                return problem
        if getattr(view, "_aggregation_key", None) is None and _aggregation_mixes_count_and_plain(columns):
            return (
                "AGGREGATION ALL combines a count of AGGREGATES with a plain attribute - "
                "no EQUAL(...) grouping key makes that combination well-defined"
            )
    where = getattr(view, "Where", None)
    if where is not None and (reason := _view_where_unsupported(where)) is not None:
        return f"WHERE clause: {reason}"
    return None


def _geometry_inspection_problem(view: MetaInstance, base: MetaInstance) -> str | None:
    """Why an `INSPECTION` of a geometry attribute cannot be evaluated, or `None` (also for a structure path)."""
    insp = geometry_inspection(view, base.BaseView, None)
    if insp is None:
        return None
    if insp.problem is not None:
        return f"INSPECTION {insp.problem}"
    alias = _view_alias(base) or ""
    for attr in getattr(view, "ClassAttribute", None) or []:
        derivates = getattr(attr, "Derivates", None) or []
        _reading, why = inspection_reading(derivates[0] if derivates else None, insp, alias)
        if why is not None:
            return f"INSPECTION attribute {getattr(attr, 'Name', None)!r} {why}"
    return None


def _join_combinations(
    bases: list[MetaInstance], objects_by_base: list[list[XtfObject]]
) -> list[list[XtfObject | None]]:
    """Cartesian product of `objects_by_base`, with `RenamedBaseView.OrNull` outer-join handling.

    A base contributes "no object" only when it has zero objects in the whole transfer; `OrNull` then keeps every
    partial combination with a `None` placeholder instead of collapsing the JOIN to the empty set.
    """
    combos: list[list[XtfObject | None]] = [[]]
    for base, objs in zip(bases, objects_by_base):
        if objs:
            combos = [combo + [obj] for combo in combos for obj in objs]
        elif bool(getattr(base, "OrNull", False)):
            combos = [combo + [None] for combo in combos]
        else:
            return []
    return combos


def _project_object_under_view_names(view: MetaInstance, obj: XtfObject) -> XtfObject:
    """Rename `obj`'s attributes to the names the view gives them (`Name := <alias> -> <attr>`).

    Identity attributes (`ALL OF`) are left unchanged.
    """
    remapped: dict[str, list[RawNode]] = dict(obj.attributes)
    for attr in getattr(view, "ClassAttribute", None) or []:
        out_name = getattr(attr, "Name", None)
        derivates = getattr(attr, "Derivates", None) or []
        if out_name is None or not derivates:
            continue
        factor = derivates[0]
        if not isinstance(factor, MetaInstance) or not factor._qualified_class.endswith("PathOrInspFactor"):
            continue
        if getattr(factor, "Inspection", None):
            continue
        refs = [getattr(el, "Ref", None) for el in (getattr(factor, "PathEls", None) or [])]
        source = refs[-1] if len(refs) == 2 and refs[-1] else (refs[0] if len(refs) == 1 else None)
        if source is None or source == out_name:
            continue
        remapped.pop(out_name, None)
        if source in obj.attributes:
            remapped[out_name] = obj.attributes[source]
    return XtfObject(tid=obj.tid, qualified_class=obj.qualified_class, attributes=remapped)


def _merge_join_combo(combo: list[XtfObject | None], view_name: str) -> XtfObject:
    """Merge one JOIN OF combination into a single synthetic `XtfObject`, re-fed through `object_to_feature`.

    Attributes are pooled from every non-`None` participant (a name collision lets the later one win); `tid` joins
    the participants' tids with `_`; `qualified_class` is informational only.
    """
    attributes: dict[str, list[RawNode]] = {}
    tid_parts: list[str] = []
    for obj in combo:
        if obj is None:
            continue
        attributes.update(obj.attributes)
        if obj.tid is not None:
            tid_parts.append(obj.tid)
    return XtfObject(tid="_".join(tid_parts) or None, qualified_class=view_name, attributes=attributes)


def _union_projected_object(view: MetaInstance, base_index: int, n_bases: int, obj: XtfObject) -> XtfObject:
    """Re-key one UNION-base object's wire attributes under the union-view attribute names.

    An explicit assignment has one source per base in declaration order (`Attr := C1->A, C2->B`), so base `i`'s `A`
    is copied to `Attr`; an `ALL OF` attribute passes through; a source that is not a one-hop path is skipped.
    """
    remapped: dict[str, list[RawNode]] = dict(obj.attributes)
    for attr in getattr(view, "ClassAttribute", None) or []:
        out_name = getattr(attr, "Name", None)
        derivates = getattr(attr, "Derivates", None) or []
        # only a genuine per-base assignment (one Derivates entry per base)
        if out_name is None or len(derivates) != n_bases or base_index >= len(derivates):
            continue
        factor = derivates[base_index]
        if not factor._qualified_class.endswith("PathOrInspFactor") or getattr(factor, "Inspection", None):
            continue
        refs = [getattr(el, "Ref", None) for el in (getattr(factor, "PathEls", None) or [])]
        source = refs[-1] if len(refs) == 2 and refs[-1] else (refs[0] if len(refs) == 1 else None)
        remapped.pop(out_name, None)
        if source and source in obj.attributes:
            remapped[out_name] = obj.attributes[source]
    qname = getattr(view, "Name", None) or obj.qualified_class
    return XtfObject(tid=obj.tid, qualified_class=qname, attributes=remapped)


def _inspection_path(view: MetaInstance) -> list[str]:
    """Return the `-> attr (-> attr)*` chain of an `INSPECTION OF base -> attr` view, as attribute names.

    Prefers the raw `Name` tokens the builder stashes on `view._inspection_path`, else `FormationParameter`.
    """
    stashed = getattr(view, "_inspection_path", None)
    if isinstance(stashed, list) and stashed:
        return [str(n) for n in stashed]
    names: list[str] = []
    for factor in getattr(view, "FormationParameter", None) or []:
        for el in getattr(factor, "PathEls", None) or []:
            ref = getattr(el, "Ref", None)
            if ref:
                names.append(ref)
    return names


def _resolved_objects_of(
    transfer: XtfTransfer,
    symbol_table: SymbolTable,
    repository: ModelRepository | None,
) -> list[tuple[XtfObject, MetaInstance]]:
    resolved_by_qualified_class: dict[str, MetaInstance | None] = {}
    out: list[tuple[XtfObject, MetaInstance]] = []
    for basket in transfer.baskets:
        for obj in basket.objects:
            cls = resolved_by_qualified_class.get(obj.qualified_class, _UNSET)
            if cls is _UNSET:
                cls = resolve_class(obj.qualified_class, symbol_table=symbol_table, repository=repository)
                resolved_by_qualified_class[obj.qualified_class] = cls
            if cls is not None:
                out.append((obj, cls))
    return out


def _view_combos(
    view: MetaInstance,
    transfer: XtfTransfer,
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
) -> tuple[list[MetaInstance], list[list[XtfObject | None]], dict[str, XtfObject]]:
    """Shared PROJECTION/AGGREGATION/JOIN setup: resolve bases, their matching objects, and WHERE-filtered combos.

    One combo per kept row (`None` for an `OR NULL` JOIN miss); shared by `evaluate_view` and
    `evaluate_view_objects` so both select the same rows. Precondition: `view.FormationKind` is one of the 3 kinds.
    """
    kind = view.FormationKind
    view_name = getattr(view, "Name", None) or "View"
    bases = [b for b in view.RenamedBaseView if isinstance(b, MetaInstance) and isinstance(b.BaseView, MetaInstance)]
    resolved_objects = _resolved_objects_of(transfer, symbol_table, repository)
    objects_by_base = [
        [obj for obj, cls in resolved_objects if is_class_compatible(cls, base.BaseView)] for base in bases
    ]
    where = getattr(view, "Where", None)
    aliases = [_view_alias(base) for base in bases]
    by_tid = {obj.tid: obj for obj, _cls in resolved_objects if obj.tid is not None}

    def _passes_where(combo: list[XtfObject | None]) -> bool:
        if where is None:
            return True
        try:
            return bool(evaluate_expression(where, _combo_properties(aliases, combo, by_tid)))
        except UnsupportedExpressionError as exc:
            raise ValueError(f"cannot evaluate view {view_name!r}: WHERE clause: {exc}") from exc

    if kind in ("Projection", "Aggregation"):
        combos: list[list[XtfObject | None]] = [[obj] for obj in objects_by_base[0] if _passes_where([obj])]
    else:  # Join
        combos = [combo for combo in _join_combinations(bases, objects_by_base) if _passes_where(combo)]
    return bases, combos, by_tid


def _raw_node_key(node: RawNode) -> tuple[Any, ...]:
    """Structural identity of a `RawNode` subtree (tag, text, attributes, children), never Python identity."""
    return (node.tag, node.text, tuple(sorted(node.attrib.items())), tuple(_raw_node_key(c) for c in node.children))


def _count_column_names(view: MetaInstance) -> list[str]:
    return [
        a.Name
        for a in getattr(view, "ClassAttribute", None) or []
        if getattr(a, "Derivates", None) and is_standard_count_call(a.Derivates[0])
    ]


def _aggregation_mixes_count_and_plain(columns: list[MetaInstance]) -> bool:
    counts = [bool(a.Derivates) and is_standard_count_call(a.Derivates[0]) for a in columns if a.Derivates]
    return any(counts) and not all(counts)


def _factor_nodes(factor: MetaInstance, alias: str, obj: XtfObject, by_tid: dict[str, XtfObject]) -> list[RawNode]:
    """Evaluate a view attribute path (`Alias -> a (-> b)*`) on `obj` into the wire nodes it denotes.

    Every hop but the last must be a reference; an unresolved hop yields no
    nodes (the SQL translation's LEFT JOIN yields NULL there).
    """
    refs = [getattr(el, "Ref", None) for el in (getattr(factor, "PathEls", None) or [])]
    if len(refs) > 1 and refs[0] and refs[0].lower() == alias.lower():
        refs = refs[1:]
    current: XtfObject | None = obj
    for i, name in enumerate(refs):
        if name is None or current is None:
            return []
        nodes = current.attributes.get(name) or []
        if i == len(refs) - 1:
            return list(nodes)
        target = extract_reference(nodes[0]) if nodes else None
        current = by_tid.get(target) if target else None
    return []


def _constant_nodes(factor: MetaInstance, name: str) -> list[RawNode]:
    value = getattr(factor, "Value", None)
    if value is None:
        return []
    text = unquote_interlis_string(value) if getattr(factor, "Type", None) == "Text" else str(value)
    return [RawNode(name, text, {}, [])]


def _aggregation_objects(
    view: MetaInstance,
    bases: list[MetaInstance],
    combos: list[list[XtfObject | None]],
    by_tid: dict[str, XtfObject],
) -> list[XtfObject]:
    """Evaluate `AGGREGATION OF base (ALL | EQUAL(key))` into one `XtfObject` per group.

    `EQUAL(key)` groups by the key; `ALL` keeps distinct rows. Count columns hold the group size.
    """
    alias = _view_alias(bases[0]) or ""
    key_factor = getattr(view, "_aggregation_key", None)
    plain: list[tuple[str, MetaInstance]] = []
    counts: list[str] = []
    for attr in getattr(view, "ClassAttribute", None) or []:
        name, derivates = getattr(attr, "Name", None), getattr(attr, "Derivates", None) or []
        if not name or not derivates:
            continue
        if is_standard_count_call(derivates[0]):
            counts.append(name)
        else:
            plain.append((name, derivates[0]))
    groups: dict[tuple[Any, ...], tuple[XtfObject, dict[str, list[RawNode]], int]] = {}
    for obj in (combo[0] for combo in combos if combo[0] is not None):
        values: dict[str, list[RawNode]] = {}
        for name, factor in plain:
            values[name] = (
                _constant_nodes(factor, name)
                if factor._qualified_class.endswith("Constant")
                else _factor_nodes(factor, alias, obj, by_tid)
            )
        key = (
            (
                None
                if key_factor is None
                else tuple(_raw_node_key(n) for n in _factor_nodes(key_factor, alias, obj, by_tid))
            ),
            tuple(tuple(_raw_node_key(n) for n in values[name]) for name, _f in plain),
        )
        first, first_values, size = groups.get(key, (obj, values, 0))
        groups[key] = (first, first_values, size + 1)
    if counts and key_factor is None and not groups:
        groups[(None, ())] = (XtfObject(None, "", {}), {}, 0)
    view_name = getattr(view, "Name", None) or "View"
    grouped = key_factor is not None or bool(counts)
    out: list[XtfObject] = []
    for first, values, size in groups.values():
        attributes = {name: nodes for name, nodes in values.items() if nodes}
        for name in counts:
            attributes[name] = [RawNode(name, str(size), {}, [])]
        out.append(XtfObject(None if grouped else first.tid, view_name, attributes))
    return out


def evaluate_view_objects(
    view: MetaInstance,
    transfer: XtfTransfer,
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
) -> list[XtfObject]:
    """Evaluate `view` into `XtfObject`s keyed by the view's own attribute names.

    Raises `ValueError` if the view is unsupported or is an INSPECTION of a geometry attribute.
    """
    reason = unsupported_view_reason(view)
    if reason is not None:
        raise ValueError(f"cannot evaluate view {getattr(view, 'Name', None)!r}: {reason}")
    kind = view.FormationKind
    view_name = getattr(view, "Name", None) or "View"

    if kind in ("Inspection", "Union"):
        bases = [
            b for b in view.RenamedBaseView if isinstance(b, MetaInstance) and isinstance(b.BaseView, MetaInstance)
        ]
        resolved_objects = _resolved_objects_of(transfer, symbol_table, repository)
        objects_by_base = [
            [obj for obj, cls in resolved_objects if is_class_compatible(cls, base.BaseView)] for base in bases
        ]
        if kind == "Inspection":
            path = _inspection_path(view)
            base_view = bases[0].BaseView
            if geometry_inspection(view, base_view, symbol_table) is not None:
                raise ValueError(
                    f"evaluate_view_objects: INSPECTION {view_name!r} of a SURFACE/AREA/POLYLINE attribute "
                    "has no XTF-transferable shape (derived geometry values, JSON-FG-only)"
                )
            elements, _element_type, _parents = _inspection_elements(
                view, base_view, objects_by_base[0], path, symbol_table
            )
            return elements
        n_bases = len(bases)
        return [
            _union_projected_object(view, base_index, n_bases, obj)
            for base_index, objs in enumerate(objects_by_base)
            for obj in objs
        ]

    bases, combos, by_tid = _view_combos(view, transfer, symbol_table=symbol_table, repository=repository)
    if kind == "Aggregation":
        return _aggregation_objects(view, bases, combos, by_tid)
    if kind == "Join":
        return [_project_object_under_view_names(view, _merge_join_combo(combo, view_name)) for combo in combos]
    return [_project_object_under_view_names(view, combo[0]) for combo in combos if combo[0] is not None]


def evaluate_view(
    view: MetaInstance,
    transfer: XtfTransfer,
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
    standalone: bool = False,
) -> list[dict[str, Any]]:
    """Evaluate `view` against `transfer` into JSON-FG Features, whatever its formation kind.

    Raises `ValueError` if the view is unsupported (callers check `unsupported_view_reason` first).
    """
    reason = unsupported_view_reason(view)
    if reason is not None:
        raise ValueError(f"cannot evaluate view {getattr(view, 'Name', None)!r}: {reason}")

    kind = view.FormationKind
    view_name = getattr(view, "Name", None) or "View"

    if kind in ("Inspection", "Union"):
        bases = [
            b for b in view.RenamedBaseView if isinstance(b, MetaInstance) and isinstance(b.BaseView, MetaInstance)
        ]
        resolved_objects = _resolved_objects_of(transfer, symbol_table, repository)
        objects_by_base = [
            [obj for obj, cls in resolved_objects if is_class_compatible(cls, base.BaseView)] for base in bases
        ]
        if kind == "Inspection":
            return _evaluate_inspection(
                view,
                bases[0].BaseView,
                _view_alias(bases[0]) or "",
                objects_by_base[0],
                _inspection_path(view),
                standalone,
                symbol_table,
                repository,
            )
        n_bases = len(bases)
        return [
            object_to_feature(
                _union_projected_object(view, base_index, n_bases, obj),
                view,
                standalone=standalone,
                symbol_table=symbol_table,
                repository=repository,
            )
            for base_index, objs in enumerate(objects_by_base)
            for obj in objs
        ]

    bases, combos, by_tid = _view_combos(view, transfer, symbol_table=symbol_table, repository=repository)

    if kind == "Aggregation":
        count_columns = _count_column_names(view)
        aggregated = []
        for group in _aggregation_objects(view, bases, combos, by_tid):
            feature = object_to_feature(
                group, view, standalone=standalone, symbol_table=symbol_table, repository=repository
            )
            for name in count_columns:  # a count has no declared type unless the model gave one
                feature["properties"][name] = int(group.attributes[name][0].text)
            aggregated.append(feature)
        return aggregated

    if kind == "Projection":
        return [
            object_to_feature(
                _project_object_under_view_names(view, combo[0]),
                view,
                standalone=standalone,
                symbol_table=symbol_table,
                repository=repository,
            )
            for combo in combos
            if combo[0] is not None
        ]

    features = []
    for combo in combos:
        feature = object_to_feature(
            _project_object_under_view_names(view, _merge_join_combo(combo, view_name)),
            view,
            standalone=standalone,
            symbol_table=symbol_table,
            repository=repository,
        )
        members = _join_members(bases, combo)
        if members:
            feature["x-join-members"] = members
        features.append(feature)
    return features


def _inspection_target(
    base_view: MetaInstance,
    path: list[str],
    symbol_table: SymbolTable | None,
) -> tuple[MetaInstance | None, bool, list[bool]]:
    """Return `(element type, is_multi, hop_is_multi)` for the `INSPECTION OF base -> a -> b` path.

    `hop_is_multi[i]` says whether `path[i]` is a `BAG`/`LIST OF` attribute, so `_evaluate_inspection` can unwrap
    every intermediate hop; it is always `len(path)` long (padded with `False` when a hop fails to resolve).
    """
    current: MetaInstance | None = base_view
    is_multi = False
    hop_is_multi: list[bool] = []
    for hop in path:
        if not isinstance(current, MetaInstance):
            break
        members = schema_members_of(current, symbol_table) if symbol_table is not None else attributes_of(current)
        attr = members.get(hop)
        if attr is None:
            current = None
            break
        resolved = resolve_attribute(attr)
        nxt = resolved.type_instance
        is_multi = resolved.type_kind == "MultiValue"
        hop_is_multi.append(is_multi)
        if is_multi and isinstance(nxt, MetaInstance):
            nxt = getattr(nxt, "BaseType", None)  # BAG/LIST OF <element type>
        current = nxt if isinstance(nxt, MetaInstance) else None
    hop_is_multi += [False] * (len(path) - len(hop_is_multi))
    ok = isinstance(current, MetaInstance) and getattr(current, "Kind", None) in ("Class", "Structure")
    return (current if ok else None), (is_multi if ok else False), hop_is_multi


def _as_line(value: list[list[float]] | dict[str, Any]) -> dict[str, Any]:
    return value if isinstance(value, dict) else {"type": "LineString", "coordinates": value}


def _join_pieces(pieces: list[dict[str, Any]]) -> dict[str, Any]:
    """Concatenate the consecutive POLYLINE pieces of one boundary into a single line (minimal edge count)."""
    if len(pieces) == 1:
        return pieces[0]
    if all(p["type"] == "LineString" for p in pieces):
        coords = list(pieces[0]["coordinates"])
        for piece in pieces[1:]:
            tail = piece["coordinates"]
            coords.extend(tail[1:] if tail and tail[0] == coords[-1] else tail)
        return {"type": "LineString", "coordinates": coords}
    parts: list[dict[str, Any]] = []
    for piece in pieces:
        parts.extend(piece["geometries"] if piece["type"] == "CompoundCurve" else [piece])
    return {"type": "CompoundCurve", "geometries": parts}


def _surface_boundaries(attr_node: RawNode) -> list[list[dict[str, Any]]] | None:
    """Each BOUNDARY of a SURFACE/AREA attribute value as its POLYLINE pieces (line geometries), or `None`."""
    surface = attr_node.children[0] if attr_node.children else None
    if surface is None or geom_tag(surface) not in _SURFACE_TAGS:
        return None
    boundaries: list[list[dict[str, Any]]] = []
    for boundary in (c for c in surface.children if geom_tag(c) in BOUNDARY_TAGS):
        pieces: list[dict[str, Any]] = []
        for polyline in (c for c in boundary.children if geom_tag(c) == "POLYLINE"):
            value = _read_polyline(polyline)
            if value is None:
                return None
            pieces.append(_as_line(value))
        if not pieces:
            return None
        boundaries.append(pieces)
    return boundaries or None


def _boundary_geometry(edges: list[dict[str, Any]]) -> dict[str, Any]:
    """The boundary of one surface: its edge lines as `LineString` (one), `MultiLineString` or `MultiCurve`."""
    if len(edges) == 1:
        return edges[0]
    if all(edge["type"] == "LineString" for edge in edges):
        return {"type": "MultiLineString", "coordinates": [edge["coordinates"] for edge in edges]}
    return {"type": "MultiCurve", "geometries": edges}


def _line_segments(polyline: RawNode) -> list[tuple[list[float], list[float] | None]] | None:
    """`(end point, arc point)` of every segment of a POLYLINE; the first is the `StartSegment` (length 0)."""
    if geom_tag(polyline) != "POLYLINE" or not polyline.children or geom_tag(polyline.children[0]) != "COORD":
        return None
    segments: list[tuple[list[float], list[float] | None]] = []
    for node in polyline.children:
        tag = geom_tag(node)
        if tag == "COORD":
            end = read_coord(node)
            if end is None:
                return None
            segments.append((end, None))
        elif tag == "ARC":
            arc = read_arc(node)
            if arc is None:
                return None
            segments.append((arc[1], arc[0]))
        else:
            return None
    return segments


def _reverse_line(edge: dict[str, Any]) -> dict[str, Any]:
    """The same line traversed backwards (a circular string keeps its mid points, a compound reverses its parts)."""
    if edge["type"] == "CompoundCurve":
        return {"type": "CompoundCurve", "geometries": [_reverse_line(g) for g in reversed(edge["geometries"])]}
    return {"type": edge["type"], "coordinates": edge["coordinates"][::-1]}


def _edge_key(edge: dict[str, Any]) -> str:
    """Identity of an edge line for `AREA INSPECTION`: a line equals its reversal."""
    forward, backward = (json.dumps(e, sort_keys=True) for e in (edge, _reverse_line(edge)))
    return min(forward, backward)


@dataclass
class _GeometryElement:
    """One `SurfaceBoundary` / `SurfaceEdge` / `LineGeometry` / `LineSegment` produced by a geometry INSPECTION."""

    tid: str | None
    owners: list[XtfObject]
    geometry: dict[str, Any] | None = None
    end_point: list[float] | None = None
    arc_point: list[float] | None = None


def _geometry_elements(
    insp: GeometryInspection, base_objects: list[XtfObject], base_attr: MetaInstance
) -> list[_GeometryElement]:
    """Decompose the inspected geometry attribute of every base object into the elements INSPECTION yields."""
    elements: list[_GeometryElement] = []
    unique_edges: dict[str, _GeometryElement] = {}
    for obj in base_objects:
        nodes = obj.attributes.get(insp.attr)
        if not nodes:
            continue
        stem = f"{obj.tid}_{insp.attr}" if obj.tid else None
        if insp.kind in (LINE_GEOMETRY, LINE_SEGMENT):
            polyline = nodes[0].children[0] if nodes[0].children else None
            value = _read_polyline(polyline) if polyline is not None else None
            segments = _line_segments(polyline) if polyline is not None and insp.kind == LINE_SEGMENT else None
            if value is None or (insp.kind == LINE_SEGMENT and segments is None):
                _record("JSONFG-GEOMETRY-UNREADABLE", base_attr, "line value could not be read for INSPECTION")
                continue
            if insp.kind == LINE_GEOMETRY:
                elements.append(_GeometryElement(obj.tid, [obj], geometry=_as_line(value)))
            else:
                for i, (end, arc) in enumerate(segments or []):
                    elements.append(_GeometryElement(f"{stem}_{i}" if stem else None, [obj], None, end, arc))
            continue
        boundaries = _surface_boundaries(nodes[0])
        if boundaries is None:
            _record("JSONFG-GEOMETRY-UNREADABLE", base_attr, "surface value could not be read for INSPECTION")
            continue
        if insp.kind == SURFACE_BOUNDARY:
            edges = [_join_pieces(pieces) for pieces in boundaries]
            elements.append(_GeometryElement(obj.tid, [obj], geometry=_boundary_geometry(edges)))
        elif insp.kind == SURFACE_EDGE:
            for i, pieces in enumerate(boundaries):
                elements.append(_GeometryElement(f"{stem}_{i}" if stem else None, [obj], _join_pieces(pieces)))
        else:  # AREA_EDGE: every transferred boundary line once, whichever area(s) it borders
            for piece in (p for pieces in boundaries for p in pieces):
                known = unique_edges.setdefault(_edge_key(piece), _GeometryElement(None, [], piece))
                if known.owners == [] or known.owners[-1] is not obj:
                    known.owners.append(obj)
                if known not in elements:
                    elements.append(known)
    return elements


def _point(position: list[float] | None) -> dict[str, Any] | None:
    return None if position is None else {"type": "Point", "coordinates": position}


def _evaluate_geometry_inspection(
    view: MetaInstance,
    insp: GeometryInspection,
    base_view: MetaInstance,
    base_alias: str,
    base_objects: list[XtfObject],
    standalone: bool,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
) -> list[dict[str, Any]]:
    """One Feature per element of an `INSPECTION OF` a SURFACE/AREA/POLYLINE attribute.

    Each view attribute is a derived value set in `properties`, never promoted to `place`; mirrors the SQL
    translation, with `view_formation.inspection_reading` fixing what is readable.
    """
    members = schema_members_of(base_view, symbol_table)
    resolved_members = {name: resolve_attribute(attr) for name, attr in members.items()}
    readings: list[tuple[str, InspectionReading]] = []
    for attr in getattr(view, "ClassAttribute", None) or []:
        derivates = getattr(attr, "Derivates", None) or []
        reading, _why = inspection_reading(derivates[0] if derivates else None, insp, base_alias)
        if reading is not None and getattr(attr, "Name", None):
            readings.append((attr.Name, reading))
    view_name = getattr(view, "Name", None) or "View"
    features: list[dict[str, Any]] = []
    for element in _geometry_elements(insp, base_objects, members[insp.attr]):
        feature = object_to_feature(
            XtfObject(element.tid, view_name, {}),
            view,
            standalone=standalone,
            symbol_table=symbol_table,
            repository=repository,
        )
        for out_name, reading in readings:
            value: Any = None
            if reading.what == "geometry":
                value = element.geometry
            elif reading.what == "endpoint":
                value = _point(element.end_point)
            elif reading.what == "arcpoint":
                value = _point(element.arc_point)
            else:
                owner_index = 1 if reading.what == "thatarea" else 0
                resolved = resolved_members.get(reading.field or "")
                owner = element.owners[owner_index] if owner_index < len(element.owners) else None
                nodes = owner.attributes.get(reading.field or "") if owner is not None else None
                if resolved is not None and nodes:
                    value = _attribute_value(resolved, nodes, symbol_table=symbol_table)
            if value is not None:
                feature["properties"][out_name] = value
        features.append(feature)
    return features


def _evaluate_inspection(
    view: MetaInstance,
    base_view: MetaInstance,
    base_alias: str,
    base_objects: list[XtfObject],
    path: list[str],
    standalone: bool,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
) -> list[dict[str, Any]]:
    """One Feature per element of the inspected attribute (`INSPECTION OF base -> attr`) of each base object.

    Geometry attributes are handled by `_evaluate_geometry_inspection`.
    """
    insp = geometry_inspection(view, base_view, symbol_table)
    if insp is not None:
        return _evaluate_geometry_inspection(
            view, insp, base_view, base_alias, base_objects, standalone, symbol_table, repository
        )
    elements, element_type, parents = _inspection_elements(view, base_view, base_objects, path, symbol_table)
    parent_type = base_view if len(path) == 1 else _inspection_target(base_view, path[:-1], symbol_table)[0]
    parent_members = (
        {name: resolve_attribute(a) for name, a in schema_members_of(parent_type, symbol_table).items()}
        if parent_type is not None
        else {}
    )
    parent_readings: list[tuple[str, str]] = []
    for attr in getattr(view, "ClassAttribute", None) or []:
        derivates = getattr(attr, "Derivates", None) or []
        els = getattr(derivates[0], "PathEls", None) or [] if derivates else []
        if len(els) == 2 and getattr(els[0], "Kind", None) == "Parent" and getattr(els[1], "Ref", None):
            parent_readings.append((attr.Name, els[1].Ref))
    features: list[dict[str, Any]] = []
    for element, parent in zip(elements, parents):
        feature = object_to_feature(
            element,
            element_type if element_type is not None else view,
            standalone=standalone,
            symbol_table=symbol_table,
            repository=repository,
        )
        feature["featureType"] = getattr(view, "Name", None) or feature["featureType"]
        for out_name, field in parent_readings:
            resolved, nodes = parent_members.get(field), parent.get(field)
            if resolved is not None and nodes:
                feature["properties"][out_name] = _attribute_value(resolved, nodes, symbol_table=symbol_table)
        features.append(feature)
    return features


def _inspection_elements(
    view: MetaInstance,
    base_view: MetaInstance,
    base_objects: list[XtfObject],
    path: list[str],
    symbol_table: SymbolTable,
) -> tuple[list[XtfObject], MetaInstance | None, list[dict[str, list[RawNode]]]]:
    """Every element of `INSPECTION OF base -> path` as a synthetic `XtfObject`, with its element type and parents.

    The parent of each element is the owning object or enclosing structure element, given as its attributes.
    Shared by `_evaluate_inspection` and `evaluate_view_objects`; a geometry inspection is not covered.
    """
    element_type, is_multi, hop_is_multi = _inspection_target(base_view, path, symbol_table)
    elements: list[XtfObject] = []
    parents: list[dict[str, list[RawNode]]] = []
    for base_obj in base_objects:
        nodes: list[RawNode] = list(base_obj.attributes.get(path[0], []))
        owners = [base_obj.attributes] * len(nodes)
        for hop, previous_was_multi in zip(path[1:], hop_is_multi[:-1]):
            holders = [c for node in nodes for c in node.children] if previous_was_multi else nodes
            nodes, owners = [], []
            for holder in holders:
                holder_attrs = group_by_tag(holder.children)
                for gc in holder.children:
                    if gc.tag == hop:
                        nodes.append(gc)
                        owners.append(holder_attrs)
        occurrences = [
            (c, owner) for node, owner in zip(nodes, owners) for c in (node.children if is_multi else [node])
        ]
        for i, (node, owner) in enumerate(occurrences):
            element_attrs: dict[str, list[RawNode]] = {}
            for child in node.children:
                element_attrs.setdefault(child.tag, []).append(child)
            tid = node.attrib.get("TID") or (f"{base_obj.tid}_{path[-1]}_{i}" if base_obj.tid else None)
            elements.append(
                XtfObject(tid=tid, qualified_class=getattr(view, "Name", None) or "View", attributes=element_attrs)
            )
            parents.append(owner)
    return elements, element_type, parents


def _join_members(bases: list[MetaInstance], combo: list[XtfObject | None]) -> list[dict[str, Any]]:
    """Return `{"featureType": ..., "id": ...}` for every real (non-`None`) participant of one JOIN combination.

    A JOIN collection is GET-only, so this lets a client resolve which base collection/id to `PUT`/`PATCH` for a
    property coming from one specific base.
    """
    return [
        {"featureType": getattr(base.BaseView, "Name", None) or obj.qualified_class, "id": obj.tid}
        for base, obj in zip(bases, combo)
        if obj is not None
    ]


def transfer_to_feature_collection(
    transfer: XtfTransfer,
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
    views: list[MetaInstance] | None = None,
    schema_url: str | None = None,
    include_child_rows: bool = False,
    unmapped: list[UnmappedValue] | None = None,
) -> dict[str, Any]:
    """Convert every resolvable object of `transfer` into one JSON-FG FeatureCollection.

    Objects whose class does not resolve are skipped. A "featureType" or "coordRefSys" shared by all
    Features is moved onto the collection. `unmapped` collects values that were left untyped or dropped.
    """
    with _recording(unmapped):
        return _transfer_to_feature_collection(
            transfer,
            symbol_table=symbol_table,
            repository=repository,
            views=views,
            schema_url=schema_url,
            include_child_rows=include_child_rows,
        )


def _object_features(
    objects: Iterable[XtfObject],
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None,
    include_child_rows: bool,
) -> Iterator[dict[str, Any]]:
    """One Feature per resolvable object (plus its child rows), lazily - the loop both entry points share."""
    for obj in objects:
        cls = resolve_class(obj.qualified_class, symbol_table=symbol_table, repository=repository)
        if cls is None:
            continue
        # The declaring model's table holds the associations embedding a role on `cls`.
        cls_table = home_symbol_table(obj.qualified_class, symbol_table=symbol_table, repository=repository)
        yield object_to_feature(
            obj,
            cls,
            standalone=False,
            symbol_table=cls_table,
            repository=repository,
            omit_multivalue=include_child_rows,
        )
        if include_child_rows:
            with _feature_scope(getattr(cls, "Name", None) or obj.qualified_class, obj.tid):
                yield from _child_row_features(obj, cls, symbol_table=symbol_table)


def iter_features(
    objects: Iterable[XtfObject],
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
    include_child_rows: bool = False,
    unmapped: list[UnmappedValue] | None = None,
) -> Iterator[dict[str, Any]]:
    """The Features of `objects`, one at a time (flat memory with `xtf.parse.iter_objects`).

    Each Feature keeps its own `featureType` and `coordRefSys`: hoisting them to the collection needs every Feature
    first (`transfer_to_feature_collection`). VIEWs need every object and are not evaluated here.
    """
    seen: set[tuple[str, str]] = set()
    stream = _object_features(
        objects, symbol_table=symbol_table, repository=repository, include_child_rows=include_child_rows
    )
    while True:
        with _recording(unmapped, seen):
            feature = next(stream, None)
        if feature is None:
            return
        yield feature


def _transfer_to_feature_collection(
    transfer: XtfTransfer,
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
    views: list[MetaInstance] | None = None,
    schema_url: str | None = None,
    include_child_rows: bool = False,
) -> dict[str, Any]:
    features = list(
        _object_features(
            (obj for basket in transfer.baskets for obj in basket.objects),
            symbol_table=symbol_table,
            repository=repository,
            include_child_rows=include_child_rows,
        )
    )

    for view in views or []:
        features.extend(
            evaluate_view(view, transfer, symbol_table=symbol_table, repository=repository, standalone=False)
        )

    conforms_to = [CONF_CORE, CONF_TYPES_SCHEMAS]
    all_place_types: set[str] = set()
    for f in features:
        all_place_types |= place_type_names(f.get("place"))
    if all_place_types & CIRCULAR_ARC_TYPES:
        conforms_to.append(CONF_CIRCULAR_ARCS)
    if all_place_types & POLYHEDRA_TYPES:
        conforms_to.append(CONF_POLYHEDRA)
    collection: dict[str, Any] = {
        "type": "FeatureCollection",
        "conformsTo": conforms_to,
        "features": features,
    }
    feature_types = {f["featureType"] for f in features}
    if len(feature_types) == 1:
        collection["featureType"] = next(iter(feature_types))
        for f in features:
            f.pop("featureType", None)

    if schema_url is not None and feature_types:
        if len(feature_types) == 1:
            collection["featureSchema"] = _feature_schema_ref(schema_url, next(iter(feature_types)))
        else:
            collection["featureSchema"] = {ft: _feature_schema_ref(schema_url, ft) for ft in sorted(feature_types)}

    crs_values = {f["coordRefSys"] for f in features if "coordRefSys" in f}
    if len(crs_values) == 1:
        collection["coordRefSys"] = next(iter(crs_values))
        for f in features:
            f.pop("coordRefSys", None)

    return collection


def view_skip_diagnostic(view: MetaInstance, *, file: str | None = None):
    """Return a `Diagnostic` for a VIEW that `evaluate_view` cannot handle, or `None` if it can be evaluated.

    Same check as `unsupported_view_reason`, mapped onto a stable rule id.
    """
    from interlis.diagnostics import Diagnostic, Location

    reason = unsupported_view_reason(view)
    if reason is None:
        return None
    name = getattr(view, "Name", None)
    if "base model not resolvable" in reason or "base model not resolved" in reason:
        rule, sev, hlp = "JSONFG-VIEW-BASE-MISSING", "warning", "pass the base model's directory to --repo"
    elif reason.startswith("WHERE clause"):
        rule, sev, hlp = "JSONFG-VIEW-WHERE-UNSUPPORTED", "note", None
    elif "INSPECTION path" in reason:
        rule, sev, hlp = "JSONFG-VIEW-INSPECTION-GAP", "note", None
    elif reason.startswith(("AGGREGATION", "INSPECTION ")):
        rule, sev, hlp = "JSONFG-VIEW-FORMATION-UNSUPPORTED", "note", None
    else:
        rule, sev, hlp = "JSONFG-VIEW-BASE-MISSING", "warning", "pass the base model's directory to --repo"
    return Diagnostic(sev, rule, f"VIEW {name!r} skipped: {reason}", Location(file=file, element_path=name), help=hlp)


FG_MEDIA_TYPE = "application/vnd.ogc.fg+json"

GEOJSON_MEDIA_TYPE = "application/geo+json"
_GEOJSON_TYPES = frozenset({"Point", "LineString", "Polygon", "MultiPoint", "MultiLineString", "MultiPolygon"})
_METHOD_OF = {"INSERT": "POST", "UPDATE": "PUT"}


def _geojson_request_body(
    feature: dict[str, Any], rename: Callable[[dict[str, Any]], dict[str, Any]] | None = None
) -> tuple[dict[str, str], dict[str, Any]]:
    """`(headers, body)` of a request a plain GeoJSON server takes: `place` becomes `geometry`, in its own CRS.

    `rename` maps properties onto the server's keys. The CRS goes in the `Content-Crs` header; a `place` GeoJSON
    cannot hold (arcs, solids, ...) leaves `geometry` null.
    """
    place = feature.get("place")
    usable = place is not None and place_type_names(place) <= _GEOJSON_TYPES
    headers = {"Content-Type": GEOJSON_MEDIA_TYPE}
    if usable and feature.get("coordRefSys"):
        headers["Content-Crs"] = f"<{feature['coordRefSys']}>"
    body: dict[str, Any] = {"type": "Feature"}
    if "id" in feature:
        body["id"] = feature["id"]
    body["geometry"] = place if usable else None
    properties = feature.get("properties", {})
    body["properties"] = rename(properties) if rename else properties
    return headers, body


def iter_change_requests(
    objects: Iterable[XtfObject],
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
    unmapped: list[UnmappedValue] | None = None,
    skipped: list[str] | None = None,
    body_profile: str = "jsonfg",
    rename_properties: Callable[[str, dict[str, Any]], dict[str, Any]] | None = None,
) -> Iterator[dict[str, Any]]:
    """Yield the objects of an incremental transfer as OGC API Features Part 4 requests, in file order.

    INSERT is `POST /collections/{class}/items`, UPDATE `PUT .../items/{tid}`, DELETE `DELETE .../items/{tid}`.
    A delete names no class, so its TID goes to `skipped` instead.
    """
    seen: set[tuple[str, str]] = set()
    for obj in objects:
        operation = obj.operation or "INSERT"
        cls = resolve_class(obj.qualified_class, symbol_table=symbol_table, repository=repository)
        if cls is None:
            if operation == "DELETE" and not obj.qualified_class and skipped is not None:
                skipped.append(obj.tid or "")
            continue
        collection = getattr(cls, "Name", None) or obj.qualified_class
        items = f"/collections/{collection}/items"
        if operation == "DELETE":
            yield {"method": "DELETE", "href": f"{items}/{obj.tid}"}
            continue
        with _recording(unmapped, seen):
            feature = next(
                _object_features([obj], symbol_table=symbol_table, repository=repository, include_child_rows=False),
                None,
            )
        if feature is None:
            continue
        if body_profile == "geojson":
            rename = (lambda props: rename_properties(obj.qualified_class, props)) if rename_properties else None
            headers, body = _geojson_request_body(feature, rename)
        else:
            headers, body = {"Content-Type": FG_MEDIA_TYPE}, feature
        yield {
            "method": _METHOD_OF[operation],
            "href": items if operation == "INSERT" else f"{items}/{obj.tid}",
            "headers": headers,
            "body": body,
        }


def change_diagnostics(skipped: list[str], *, file: str | None = None):
    """A note per `DeleteObject` that `iter_change_requests` could not address to a collection."""
    from interlis.diagnostics import Diagnostic, Location

    return [
        Diagnostic(
            "note",
            "JSONFG-DELETEOBJECT-NO-COLLECTION",
            f"DeleteObject {tid}: names no class, so no collection to send DELETE to - left out",
            Location(file=file),
            help="OGC API Features addresses an item by its collection; use import-xtf to replay it on a database",
        )
        for tid in skipped
    ]


_REPO_HELP = frozenset({"JSONFG-MULTIVALUE-UNRESOLVED", "JSONFG-GEOM-NO-CRS"})


def collect_diagnostics(unmapped: list[UnmappedValue], *, file: str | None = None):
    """One `Diagnostic` per value `transfer_to_feature_collection` recorded in its `unmapped` list.

    An unresolved BAG/LIST element type is class C (provide the model); an untyped or unreadable value class A.
    """
    from interlis.diagnostics import Diagnostic, Location

    out: list[Diagnostic] = []
    for event in unmapped:
        resolvable = REGISTRY[event.rule][0] == "C"
        out.append(
            Diagnostic(
                "warning" if resolvable else "note",
                event.rule,
                f"{event.element_path}: {event.detail}",
                Location(file=file, element_path=event.element_path, tid=event.tid),
                help="pass its model's directory to --repo" if event.rule in _REPO_HELP else None,
            )
        )
    return out
