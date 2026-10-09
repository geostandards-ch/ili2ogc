"""XTF data instance -> JSON-FG conversion: Feature/FeatureCollection, scalar properties, OID, featureType, geometry.

`object_to_feature` converts one already-parsed XtfObject (xtf/parse.py,
structural layer) into a JSON-FG (OGC 21-045r1) Feature object;
`transfer_to_feature_collection` wraps every resolvable object of an
XtfTransfer into one FeatureCollection. Both conform to the "core" and
"types-schemas" requirements classes always, plus "circular-arcs"/
"polyhedra" when "place" needs them - single-attribute point/line/
polygon geometry -> "place" (never "geometry", which stays `null` - no
WGS84 reprojection, see module docs); a plain REFERENCE TO, an embedded
association role, or a 1-own-attribute STRUCTURE wrapping a REFERENCE TO
(all 3 real wire shapes, unified via the SAME `_extract_reference` search
already proven by xtf/validate.py) -> the referenced object's OID as a
plain string; a genuine STRUCTURE (no findable REF) -> a nested JSON
object, `BAG`/`LIST OF X` -> a JSON array, both recursively (same
"resolve schema, dispatch per kind" logic as the root object, mirroring
xtf/validate.py's own `_validate_attrs` recursion). A CoordType/LineType
NESTED inside a structure/list element becomes a plain GeoJSON geometry
object in `properties` (the SAME `_coord_geometry`/`_line_geometry`
builders as the top-level `place`, which stays the ONLY thing under
`geometry`/`place`). A class with 2+ own+inherited
geometry-typed attributes gets a single "place" of type
`GeometryCollection` bundling every one of them (JSON-FG issue #134 added
`GeometryCollection` to `place.json`'s allowed types - no separate
conformance class needed). A `Geometry3D_V2.Solid3D`-typed attribute
(matched by qualified name, not structurally - RULE #7 exception, no real
corpus evidence yet) becomes a `Polyhedron` the same way; a
`PolylineStraight3D`/`CompositeCurve3D`-typed attribute becomes a
`LineString`, and a standalone `Tin3D`/`SurfaceShell3D`/
`CompositeSurface3D`-typed attribute becomes a `MultiPolygon`; a
`PointCloud3D`-typed attribute becomes a `MultiPoint` (all 4 the same
RULE #7 exception, same matching approach). Reuses the schema resolution
already proven by
xtf/validate.py (resolve_attribute/attributes_of/coord_axes/
line_coord_type) AND its wire-tag helpers (_geom_tag/_find_child/
_axis_components/the BOUNDARY/SURFACE/LINE_KIND tag sets) rather than a
second parallel implementation of the same COORD/POLYLINE/SURFACE/AREA/
MULTI* wire conventions - convert() stays a decoupled stage from
validate(), same split already established by convert/jsonschema.py.
"""

import contextlib
import json
from collections.abc import Callable, Iterator
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
from interlis.convert.jsonschema import _is_integer_range
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
from interlis.metamodel.instance import MetaInstance
from interlis.runtime.parse import _unquote_interlis_string
from interlis.xtf.parse import RawNode, XtfObject, XtfTransfer
from interlis.xtf.schema import (
    ResolvedAttribute,
    attributes_of,
    home_symbol_table,
    is_class_compatible,
    line_coord_type,
    resolve_attribute,
    resolve_class,
    schema_members_of,
    wrapped_reference,
)
from interlis.xtf.validate import (
    _BOUNDARY_TAGS,
    _LINE_KIND_MULTI_TAGS,
    _LINE_KIND_TAGS,
    _REFERENCE_TYPE_KINDS,
    _SURFACE_TAGS,
    _axis_components,
    _extract_reference,
    _geom_tag,
    _group_by_tag,
)

JSON_FG_VERSION = "1.0"
CONF_CORE = f"http://www.opengis.net/spec/json-fg-1/{JSON_FG_VERSION}/conf/core"
CONF_TYPES_SCHEMAS = f"http://www.opengis.net/spec/json-fg-1/{JSON_FG_VERSION}/conf/types-schemas"
CONF_CIRCULAR_ARCS = f"http://www.opengis.net/spec/json-fg-1/{JSON_FG_VERSION}/conf/circular-arcs"
CONF_POLYHEDRA = f"http://www.opengis.net/spec/json-fg-1/{JSON_FG_VERSION}/conf/polyhedra"
CRS_URI_PREFIX = "http://www.opengis.net/def/crs/EPSG/0/"

_SCALAR_KINDS = {"NumType", "TextType", "EnumType", "BooleanType", "FormattedType", "BlackboxType"}
_GEOMETRY_KINDS = {"CoordType", "LineType"}
# JSON-FG Part 1 Core §7.5 (conformance class "circular-arcs") - geometry
# "type" values that require CONF_CIRCULAR_ARCS to be declared in
# "conformsTo".
_CIRCULAR_ARC_TYPES = frozenset({"CircularString", "CompoundCurve", "CurvePolygon", "MultiCurve", "MultiSurface"})
_POLYHEDRA_TYPES = frozenset({"Polyhedron", "MultiPolyhedron"})
# `Geometry3D_V2.Solid3D` (CHBase Part VIII, models.geo.admin.ch) is the
# only published INTERLIS 3D solid structure - matched by Name + its
# distinctive `OuterShell` attribute (see `_is_solid3d`), not by qualified
# model path (`SymbolTable.qualified_name_of` only resolves within the
# model actually being built, never an imported one - same cross-model
# identity limitation already documented on `concrete_structure_subclasses`
# in xtf/schema.py). No real corpus file uses this construct yet (RULE
# #7: a synthetic, unvalidated exception - see
# docs/dev-notes/solid3d-polyhedron-mapping.md).


def _place_type_names(place: dict[str, Any] | None) -> set[str]:
    """Collect the "type" value(s) a `place` actually carries, unwrapping a `GeometryCollection` one level.

    Shared between a single Feature's own `conformsTo` and a
    FeatureCollection's aggregate one, so a `GeometryCollection`-wrapped
    circular-arc/polyhedra shape is detected in both places alike.
    """
    if place is None:
        return set()
    if place.get("type") == "GeometryCollection":
        return {g.get("type") for g in place.get("geometries", [])}
    return {place.get("type")}


def _scalar_value(resolved: ResolvedAttribute, node: RawNode) -> Any:
    """Read ONE scalar attribute's wire value off its RawNode.

    Same wire conventions already relied on by xtf/validate.py's
    `_validate_scalar` (plain element text, "true"/"false" for BOOLEAN -
    eCH-0031 V2.1.0 SS4.3.11), just producing a value instead of a
    validation verdict. Never raises: a value that doesn't parse as
    expected (e.g. non-numeric text under a NumType) is passed through
    as-is rather than dropped, consistent with this converter's RULE #5
    stance of surfacing rather than hiding.
    """
    text = node.text
    if text is None:
        return None
    kind = resolved.type_kind
    # FormattedType (an ISO date/time string on the wire) and BlackboxType
    # (the inner XML/base64 text) both pass through verbatim - matches the
    # `type: string` their own JSON Schema `$defs` entry declares.
    if kind == "NumType":
        # Same integer-vs-number heuristic already used for this SAME
        # attribute's JSON Schema (convert/jsonschema.py) - keeps a
        # Feature's property values consistent with the type its own
        # $defs entry declares, rather than a second independent guess.
        as_int = _is_integer_range(
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
def _recording(sink: list[UnmappedValue] | None) -> Iterator[None]:
    token = _UNMAPPED.set(sink)
    try:
        yield
    finally:
        _UNMAPPED.reset(token)


def _raw_value(node: RawNode, *, unwrap: bool) -> Any:
    """A value whose type didn't resolve, kept as on the wire: its text, its REF, or `{tag: value}` of its content.

    The shape the typed conversion gives a scalar, a reference or a STRUCTURE (`unwrap` skips the attribute
    element down to the structure's content wrapper), only untyped: numbers stay strings.
    """
    if not node.children:
        return node.text if node.text is not None else _extract_reference(node)
    content = node.children[0] if unwrap and len(node.children) == 1 and node.children[0].children else node
    if all(not c.children and c.text is None for c in content.children):
        return _extract_reference(content)
    grouped = _group_by_tag(content.children)
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
    if kind in _GEOMETRY_KINDS and raw_nodes:
        # A CoordType/LineType NESTED inside a STRUCTURE or a BAG/LIST
        # element - the Feature's own `place` is a separate, top-level
        # attribute (`_place_and_crs`). A nested one has nowhere native to
        # go in JSON-FG, so it becomes a plain GeoJSON geometry object in
        # `properties` (reusing the SAME `_coord_geometry`/`_line_geometry`
        # builders, no parallel coordinate parsing) rather than being
        # left out - real corpus DATA has this (nested
        # `CaptureMethod` geometry, SIA405 symbol positions, ...).
        geometry = (
            _coord_geometry(resolved, raw_nodes[0]) if kind == "CoordType" else _line_geometry(resolved, raw_nodes[0])
        )
        return geometry
    if kind == "MultiValue":
        return _multi_value(resolved, raw_nodes, symbol_table=symbol_table)
    if kind in _REFERENCE_TYPE_KINDS:
        # Same dispatch as xtf/validate.py's _validate_resolved_attr: a
        # "ReferenceType"/"Class" kind doesn't always mean a plain
        # REFERENCE TO or an embedded association role - a 1-own-attribute
        # STRUCTURE wrapping a REFERENCE TO (the "MandatoryCatalogueReference"
        # pattern, real corpus example: RoadTrafficAccidentLocation_V2's
        # AccidentType/AccidentSeverityCategory/RoadType/AccidentWeekDay)
        # is ALSO transferred with a findable REF, several levels deep -
        # _extract_reference searches the whole subtree regardless of which
        # of these 3 real wire shapes produced it, so all 3 are handled by
        # this ONE lookup, never 3 separate cases.
        is_structure = kind == "Class" and getattr(resolved.type_instance, "Kind", None) == "Structure"
        if is_structure and wrapped_reference(resolved.type_instance) is None:
            return _structure_value(resolved, raw_nodes, symbol_table=symbol_table, already_unwrapped=already_unwrapped)
        ref = _extract_reference(raw_nodes[0]) if raw_nodes else None
        if ref is not None:
            return ref
        # A GENUINE STRUCTURE occurrence (Kind=Structure, no REF anywhere -
        # real nested content, e.g. MultilingualText) recurses into its own
        # content instead of falling through to the marker below.
        if kind == "Class" and getattr(resolved.type_instance, "Kind", None) == "Structure":
            return _structure_value(resolved, raw_nodes, symbol_table=symbol_table, already_unwrapped=already_unwrapped)
        # A `CLASS`/`STRUCTURE RESTRICTION` domain attribute whose exporter
        # flattened the value to a leaf scalar (real corpus:
        # RoadTrafficCensus_V1_1's `Owner`/`Canton`, `DOMAIN Owner = CLASS
        # RESTRICTION (...)`, on the wire as `<Owner>CH</Owner>`) - mirror
        # the JSON Schema pipeline, which emits `{"type": "string",
        # "x-reference-target": ...}` for the same construct: return the
        # text as a string.
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

    Value-producing counterpart of xtf/validate.py's `_validate_attrs`
    recursion (same real wire convention, same `already_unwrapped`
    distinction - a bug there, fixed once, is worth respecting exactly
    rather than re-deriving): `already_unwrapped=False` (a root/plain
    structure-typed attribute) - `raw_nodes[0]` is the node NAMED AFTER
    THE ATTRIBUTE (e.g. `<Name>`), whose ONLY child is the actual
    structure-content wrapper (`<...MultilingualText>`) - one level of
    unwrapping needed. `already_unwrapped=True` (a `MultiValue`
    occurrence, see `_multi_value`) - `raw_nodes[0]` IS ALREADY that
    wrapper - unwrapping it again would misgroup the grandchildren
    instead of the real attributes.
    """
    node = raw_nodes[0] if raw_nodes else None
    if node is None:
        return {}
    wrapper = node if already_unwrapped else (node.children[0] if node.children else None)
    if wrapper is None or not isinstance(resolved.type_instance, MetaInstance):
        return {}
    return _members_value(resolved.type_instance, _group_by_tag(wrapper.children), symbol_table=symbol_table)


def _multi_value(resolved: ResolvedAttribute, raw_nodes: list[RawNode], *, symbol_table: SymbolTable | None) -> Any:
    """`BAG {m..n} OF X` / `LIST {m..n} OF X` -> a JSON array of converted occurrences.

    Real wire convention (confirmed on `KGS_PBC_V2_2.ili`'s
    `Objektart`/`EGID`/`Adressen`, same as xtf/validate.py's own
    MultiValue handling): a SINGLE element named after the attribute,
    containing each occurrence as a DIRECT CHILD - never repeated
    attribute-name tags at the object level (that convention is what
    `XtfObject.attributes` itself already captures, a DIFFERENT kind of
    repetition). Each occurrence is re-dispatched through
    `_attribute_value` via a synthetic `ResolvedAttribute` for
    `MultiValue.BaseType` (Structure, Reference, scalar, ... - the exact
    same generic mechanism, unlimited recursion), `already_unwrapped=True`
    since an occurrence IS the content wrapper already (see
    `_structure_value`).
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
    """Lowercase - matches `convert/sql.py`'s `_sql_identifier` EXACTLY (kept manually in sync, not imported:
    `convert/sql.py` already imports FROM this module, `_meta_value`, so importing back would be circular).

    Only correct as long as both copies stay identical - the one real,
    documented limitation this creates: a child table
    name/FK column here is NOT recomputed with the SAME cross-class
    disambiguation `convert/sql.py`'s `build_tables` applies on a real
    `Class.Name` collision (`_2`/`_3` suffix) - this module has no
    visibility into sibling classes to detect that collision at all. Real
    corpus prevalence of BOTH a name collision AND a BAG/LIST attribute on
    the SAME class name: not observed so far.
    """
    return name.lower()


def _child_row_features(obj: XtfObject, cls: MetaInstance, *, symbol_table: SymbolTable | None) -> list[dict[str, Any]]:
    """Return one JSON-FG Feature per `BAG`/`LIST OF` occurrence across ALL of `obj`'s own+inherited attributes.

    Companion to `convert/sql.py`'s child tables (`_build_child_table`) -
    same schema, so GDAL's own `"featureType"`-based table splitting
    (already relied on for the main data)
    routes these into the SAME child tables in the SAME `ogr2ogr -append`
    call as everything else - no separate file, no separate GDAL
    invocation, no live-database dependency for this project.

    `featureType` = `"<parent_table>_<attr_name>"` (`convert/sql.py`'s own
    child table name). `properties` always carries `"<parent_table>_fk"`
    (the parent's own OID, matching the child table's own FK column) and,
    for `Ordered=True` (`LIST`), a `"seq"` 0-based index (`BAG` has none -
    order isn't significant). The element's own value: a genuine
    `STRUCTURE` occurrence spreads its OWN members directly (matching the
    child table's own flattened columns, one level, same as
    `_build_child_table`) ; anything else (scalar, or a `REFERENCE TO`
    target OID - though `BAG`/`LIST OF REFERENCE TO` is not actually
    constructible by this project's own grammar, per `convert/sql.py`'s
    own note) lands in a single `"value"` property,
    matching the child table's own `value` column. `id` is synthesized
    (`"<parent OID>_<attr name>_<index>"`) - a `STRUCTURE`/scalar
    occurrence has no OID of its own on the wire. `"geometry"` stays
    `null` like every other Feature this module produces (no WGS84
    reprojection); a geometry-typed `BAG`/`LIST` element's `"value"` is a
    plain GeoJSON geometry object (`_attribute_value` dispatches
    CoordType/LineType through `_coord_geometry`/`_line_geometry`).
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


def _members_value(
    cls: MetaInstance, attrs: dict[str, list[RawNode]], *, symbol_table: SymbolTable | None
) -> dict[str, Any]:
    """Convert every attribute present in `attrs` against `cls`'s own schema into a plain dict.

    Shared by `object_to_feature` (the root object) and `_structure_value`
    (nested STRUCTURE content) - same "resolve schema, dispatch per kind"
    logic applied to any Class/Structure's own attribute set, mirroring
    xtf/validate.py's `_validate_attrs` (there: valid/invalid issues;
    here: a value). An attribute present on the wire but not found in the
    schema is silently skipped - not this converter's job to flag,
    `validate()` does.
    """
    schema_attrs = schema_members_of(cls, symbol_table) if symbol_table is not None else attributes_of(cls)
    resolved_attrs = {name: resolve_attribute(attr) for name, attr in schema_attrs.items()}
    result: dict[str, Any] = {}
    for name, raw_nodes in attrs.items():
        if name not in resolved_attrs:
            continue
        value = _attribute_value(resolved_attrs[name], raw_nodes, symbol_table=symbol_table)
        kind = resolved_attrs[name].type_kind
        if value is not None or kind in _SCALAR_KINDS or kind in _GEOMETRY_KINDS:
            result[name] = value
    return result


# --- Geometry (`"place"`) ----------------------------------------------------
#
# Value-extraction counterparts of xtf/validate.py's _validate_coord_node/
# _validate_polyline_node/_validate_boundary_node/_validate_surface_node -
# same wire structure (reuses the SAME tag helpers/sets, imported directly
# rather than duplicated), but building a JSON-FG geometry value instead of
# a list of validation issues. Returns `None` (never raises) on anything
# this module doesn't represent - a custom LINE FORM segment, a missing/
# non-numeric component - so the caller falls back to leaving the attribute
# in "properties" (RULE #5: never silently misrepresent geometry that
# couldn't be read). An ARC segment IS representable (see `_read_polyline`
# below): a
# straight-only POLYLINE/BOUNDARY still returns a plain position list (as
# before, wrapped into LineString/Polygon by the caller), but one
# containing at least one ARC returns an already-typed JSON-FG geometry
# object (CircularString/CompoundCurve/CurvePolygon) instead - the caller
# (`_line_geometry`) tells the two apart via `isinstance(value, dict)`.


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


def _read_coord(node: RawNode) -> list[float] | None:
    if _geom_tag(node) != "COORD":
        return None
    return _positions_from(node, "C")


def _read_arc(node: RawNode) -> tuple[list[float], list[float]] | None:
    """Read an ArcSegment (eCH-0031 V2.1.0 SS4.3.11.14): `(intermediate point, end point)`.

    The intermediate point (A1/A2) is always 2D - INTERLIS never carries a
    3rd component for it (confirmed in xtf/validate.py's `_validate_arc_node`)
    even when the surrounding COORD/end point is 3D; the optional radius
    (R) is redundant with the 3 points a `CircularString` arc needs and is
    not read here.
    """
    if _geom_tag(node) != "ARC":
        return None
    end = _positions_from(node, "C")
    mid = _positions_from(node, "A")
    return None if end is None or mid is None else (mid, end)


def _read_polyline(node: RawNode) -> list[list[float]] | dict[str, Any] | None:
    """Read a POLYLINE's SegmentSequence into a plain position list (straight-only) or a curved geometry object.

    A SegmentSequence always starts with a COORD (the path's start point),
    then zero or more StraightSegment (COORD)/ArcSegment (ARC) - never
    ArcSegment first (eCH-0031 V2.1.0 SS4.3.11.14). Consecutive segments of
    the same kind are grouped into one run (a straight run -> `LineString`,
    an arc run -> `CircularString`, chained arcs sharing their shared
    endpoint per JSON-FG's own encoding rather than repeating it - see
    core/examples/multi-curve.json, opengeospatial/ogc-feat-geo-json); a
    run switch keeps the boundary point as the start of the next run. A
    straight-only polyline collapses back to the original plain position
    list (unchanged return shape, still what `_line_geometry` wraps into a
    "LineString"/"Polygon"); one made of a single arc run returns a bare
    `CircularString`; anything mixing runs returns a `CompoundCurve`
    (JSON-FG Part 1 Core SS7.5.2 - each item's first position equals the
    previous item's last, exactly how runs are chained here).
    """
    if _geom_tag(node) != "POLYLINE" or not node.children:
        return None
    segments = node.children
    if _geom_tag(segments[0]) != "COORD":
        return None
    start = _read_coord(segments[0])
    if start is None:
        return None
    parts: list[dict[str, Any]] = []
    kind = "line"
    points: list[list[float]] = [start]
    for seg in segments[1:]:
        tag = _geom_tag(seg)
        if tag == "COORD":
            pos = _read_coord(seg)
            if pos is None:
                return None
            if kind == "arc":
                parts.append({"type": "CircularString", "coordinates": points})
                kind = "line"
                points = [points[-1]]
            points.append(pos)
        elif tag == "ARC":
            arc = _read_arc(seg)
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
    if _geom_tag(node) not in _BOUNDARY_TAGS:
        return None
    pieces: list[dict[str, Any]] = []
    for polyline in (c for c in node.children if _geom_tag(c) == "POLYLINE"):
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

    A `CurvePolygon`'s "geometries" member is a list of closed curve
    geometries (JSON-FG Part 1 Core SS7.5.3) - every ring, straight or
    curved, is wrapped as a full geometry object there (a plain ring
    becomes `{"type": "LineString", ...}`), unlike the plain-`Polygon` case
    where rings stay bare position lists (`_read_boundary`'s straight-only
    return shape, unchanged).
    """
    if _geom_tag(node) not in _SURFACE_TAGS:
        return None
    boundaries = [c for c in node.children if _geom_tag(c) in _BOUNDARY_TAGS]
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
    if child is None or _geom_tag(child) != expected:
        return None
    if not multi:
        pos = _read_coord(child)
        return None if pos is None else {"type": "Point", "coordinates": pos}
    positions: list[list[float]] = []
    for c in (c for c in child.children if _geom_tag(c) == "COORD"):
        pos = _read_coord(c)
        if pos is None:
            return None
        positions.append(pos)
    return None if not positions else {"type": "MultiPoint", "coordinates": positions}


def _line_geometry(resolved: ResolvedAttribute, node: RawNode) -> dict[str, Any] | None:
    """Build the JSON-FG geometry value for a LineType attribute occurrence.

    `reader` (`_read_polyline`/`_read_surface`) returns either a plain
    position list (straight-only - wrapped here into "LineString"/"Polygon"/
    "MultiLineString"/"MultiPolygon", unchanged from before circular-arcs
    support) or an already-typed geometry object (`CircularString`/
    `CompoundCurve`/`CurvePolygon` - any run/ring containing an ARC segment,
    see `_read_polyline`/`_read_surface`), told apart via `isinstance(...,
    dict)`. In the `multi` case, ANY curved part switches the WHOLE
    attribute to JSON-FG's `MultiCurve`/`MultiSurface` (SS7.5.4/.5) instead
    of `MultiLineString`/`MultiPolygon` - both require every member to be a
    full geometry object, so a straight part is wrapped into a
    `LineString`/`Polygon` there too rather than left as a bare coordinate
    array.
    """
    line_type = resolved.type_instance
    kind = getattr(line_type, "Kind", None)
    multi = bool(getattr(line_type, "Multi", False))
    single_tags = _LINE_KIND_TAGS.get(kind)
    if single_tags is None:
        return None
    expected_tags = _LINE_KIND_MULTI_TAGS[kind] if multi else single_tags
    child = node.children[0] if node.children else None
    if child is None or _geom_tag(child) not in expected_tags:
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
    for part in (c for c in child.children if _geom_tag(c) in single_tags):
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


def _meta_value(instance: MetaInstance | None, name: str) -> str | None:
    if instance is None:
        return None
    for meta in getattr(instance, "MetaAttribute", None) or []:
        if getattr(meta, "Name", None) == name:
            return meta.Value
    return None


def _context_instances(symbol_table: SymbolTable | None, repository: ModelRepository | None) -> list[MetaInstance]:
    """Every `Context` (`CONTEXT <Name> = ...;`) declared directly in a MODEL actually IMPORTed, deduplicated.

    Scoped to `Model.Element` of the SPECIFIC model each name in
    `repository.loaded_models()` was resolved through (never a blanket
    scan of every instance in that model's file) - a file can declare
    SEVERAL sibling `MODEL`s sharing one symbol table (e.g. swisstopo's
    `CHBase_Part7_CONTEXT_V2.ili`: `ContextCH_V2`/`ContextCHLV03_V2`/
    `ContextCHLV95_V2` in one file), and only the model actually named in
    an `IMPORTS` clause should contribute its `CONTEXT` rebindings - an
    unrelated sibling's (possibly ambiguous, `OR`-joined) rebinding for
    the SAME generic domain must never shadow it.
    """
    models: list[MetaInstance] = []
    if symbol_table is not None:
        models.extend(
            inst
            for inst in symbol_table.all_registered()
            if isinstance(inst, MetaInstance) and inst._qualified_class == "IlisMeta16.ModelData.Model"
        )
    if repository is not None:
        for model_name, table in repository.loaded_models().items():
            if table is None:
                continue
            model = table.resolve(model_name, kind_hint="Model")
            if isinstance(model, MetaInstance):
                models.append(model)
    seen: set[int] = set()
    contexts: list[MetaInstance] = []
    for model in models:
        for element in getattr(model, "Element", None) or []:
            if (
                isinstance(element, MetaInstance)
                and element._qualified_class == "IlisMeta16.ModelData.Context"
                and id(element) not in seen
            ):
                seen.add(id(element))
                contexts.append(element)
    return contexts


def _context_concrete_domain(
    coord_type: MetaInstance | None, symbol_table: SymbolTable | None, repository: ModelRepository | None
) -> MetaInstance | None:
    """Rebind a GENERIC domain (e.g. `Geometry_V2.Coord3`) via a `CONTEXT default = Generic=Concrete;` clause.

    `None` (never a guessed default) when `coord_type` is not the
    `GenericDomain` of any built `Context`, or its pair-group offers
    several OR-ed concrete alternatives (ambiguous, e.g. `ContextCH_V2`'s
    `Coord3=GeometryCHLV03_V2.Coord3 OR GeometryCHLV95_V2.Coord3`) - same
    no-default policy as `_crs_uri` itself.
    """
    if coord_type is None:
        return None
    for context in _context_instances(symbol_table, repository):
        for generic_def in getattr(context, "GenericDef", None) or []:
            domains = getattr(generic_def, "GenericDomain", None) or []
            if not domains or domains[0] is not coord_type:
                continue
            links = getattr(generic_def, "ConcreteForGeneric", None) or []
            concretes = [
                c
                for link in links
                for c in (getattr(link, "ConcreteDomain", None) or [])
                if isinstance(c, MetaInstance)
            ]
            return concretes[0] if len(concretes) == 1 else None
    return None


def _crs_uri(
    coord_type: MetaInstance | None,
    *,
    symbol_table: SymbolTable | None = None,
    repository: ModelRepository | None = None,
    _seen: set[int] | None = None,
) -> str | None:
    """Resolve a CoordType's `!!@CRS=EPSG:<code>` meta-attribute (eCH-0117) into a JSON-FG `coordRefSys` URI.

    `None` (never a guessed default) when the meta-attribute is absent (and
    no `CONTEXT` rebinding resolves it either - see
    `_context_concrete_domain`) or not an `EPSG:<digits>` value - Swiss
    data is never WGS84, so omitting `coordRefSys` would make a JSON-FG
    reader assume CRS84/CRS84h by the spec's own default-CRS rule (a real
    misrepresentation, not just a gap). Requires
    `ModelRepository._get_table` to propagate `meta_attributes` into
    imported models (builder/repository.py) - without that fix this
    resolves to `None` for virtually every real Swiss geometry attribute,
    since they import their CoordType rather than declaring it locally.
    `_seen` guards against a `CONTEXT` rebinding cycle spanning more than
    one hop (A -> B -> A) - not just the direct A -> A case.
    """
    raw = _meta_value(coord_type, "CRS")
    if raw is None:
        concrete = _context_concrete_domain(coord_type, symbol_table, repository)
        seen = _seen or set()
        if concrete is None or concrete is coord_type or id(concrete) in seen:
            return None
        return _crs_uri(concrete, symbol_table=symbol_table, repository=repository, _seen=seen | {id(coord_type)})
    scheme, _, code = raw.partition(":")
    if scheme.strip().upper() != "EPSG" or not code.strip().isdigit():
        return None
    return f"{CRS_URI_PREFIX}{code.strip()}"


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
    crs = _crs_uri(coord_type, symbol_table=symbol_table, repository=repository)
    return None if crs is None else (geometry, crs)


def _resolve_named_attr(cls: MetaInstance, name: str) -> ResolvedAttribute | None:
    """Resolve `cls`'s attribute `name`, or `None` if it has none - shared by the 3D/CHBase coord-type walkers."""
    attr = attributes_of(cls).get(name)
    return resolve_attribute(attr) if attr is not None else None


def _resolve_base_type(cls: MetaInstance, attr_name: str) -> MetaInstance | None:
    """Resolve `cls.<attr_name>`, then unwrap ITS OWN `.type_instance.BaseType`.

    The shared two-step indirection behind every 3D/CHBase coord-type
    walker below (`Simplified`/`Points`/`Surfaces` -> the element type one
    level down) - was duplicated inline 5 times before being extracted here.
    """
    attr = _resolve_named_attr(cls, attr_name)
    base_type = getattr(attr.type_instance, "BaseType", None) if attr is not None else None
    return base_type if isinstance(base_type, MetaInstance) else None


def _place_and_crs_via(
    coord_type_fn: Callable[[MetaInstance], MetaInstance | None],
    geometry_fn: Callable[[MetaInstance, dict[str, Any]], dict[str, Any] | None],
) -> Callable[[ResolvedAttribute, Any, SymbolTable | None, ModelRepository | None], tuple[dict[str, Any], str] | None]:
    """Build one `_solid3d_place_and_crs`-shaped function from its shape-specific coord-type/geometry builders.

    Every 3D/CHBase geometry shape shares the same guard/lookup skeleton
    (`_solid3d_place_and_crs` and its 4 siblings used to duplicate it) -
    only `coord_type_fn`/`geometry_fn` differ per shape.
    """

    def _place_and_crs_for_shape(
        resolved: ResolvedAttribute,
        value: Any,
        symbol_table: SymbolTable | None,
        repository: ModelRepository | None = None,
    ) -> tuple[dict[str, Any], str] | None:
        if not isinstance(resolved.type_instance, MetaInstance) or not isinstance(value, dict):
            return None
        crs = _crs_uri(coord_type_fn(resolved.type_instance), symbol_table=symbol_table, repository=repository)
        if crs is None:
            return None
        geometry = geometry_fn(resolved.type_instance, value)
        return None if geometry is None else (geometry, crs)

    return _place_and_crs_for_shape


def _is_solid3d(type_instance: MetaInstance | None) -> bool:
    """Whether `type_instance` is (an occurrence of) `Geometry3D_V2.Solid3D` - see module-level constant."""
    return (
        isinstance(type_instance, MetaInstance)
        and getattr(type_instance, "Kind", None) == "Structure"
        and getattr(type_instance, "Name", None) == "Solid3D"
        and "OuterShell" in attributes_of(type_instance)
    )


def _solid3d_coord_type(solid_class: MetaInstance) -> MetaInstance | None:
    """Walk `Solid3D.OuterShell -> Simplified -> Triangle3D.Geometry` to the ultimate `CoordType`, for CRS lookup.

    Unlike a plain `CoordType`/`LineType` attribute, a `Solid3D` value has
    no single geometry-typed attribute of its own to read a `CoordType`
    off of - the schema has to be walked down to the one place a
    `CoordType` actually lives (every `Triangle3D.Geometry`, all sharing
    the same declared VERTEX domain by construction).
    """
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
    """One JSON-FG Polyhedron shell (a MultiPolygon-shaped patch list) from an already-decoded `SurfaceShell3D` dict.

    Only `Simplified` (guaranteed straight-only triangles, `BAG {1..*}`)
    feeds the shell - `Native` (arbitrary `Surface3D` subclasses, possibly
    non-planar) has no guaranteed straight-only representation and is left
    out, same non-lossy-preferred stance already taken for circular-arcs.
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
    """A `Geometry3D_V2.Solid3D` value (already decoded by `_structure_value`) -> a JSON-FG `Polyhedron` geometry.

    Each shell (`OuterShell`, then every `InnerShells` occurrence) becomes
    one entry of `coordinates` (JSON-FG Part 1 §7.4: "a solid defined by
    shells, each shell a closed, simple MultiPolygon") - `Solid3D`'s own
    `InnerShells` (voids enclosed by the outer shell) map onto this
    one-for-one.
    """
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


# `value` in each `_place_and_crs_via`-built function below is the attribute's
# already-decoded `properties` value (built once by `_members_value`/
# `_structure_value` for every attribute, geometry or not) - reused here
# rather than re-decoding the same raw wire nodes a second time.
_solid3d_place_and_crs = _place_and_crs_via(
    _solid3d_coord_type, lambda _type_instance, value: _solid3d_polyhedron(value)
)


def _is_curve3d(type_instance: MetaInstance | None) -> bool:
    """Whether `type_instance` is `Geometry3D_V2.PolylineStraight3D` or `.CompositeCurve3D`.

    Same Name+shape matching as `_is_solid3d` (cross-model qualified-name
    lookup doesn't work here either). `Pipe3D` (`EXTENDS CompositeCurve3D`,
    no JSON-FG target - no tube/extrusion primitive exists in any
    conformance class) is excluded by construction: its own `Name` is
    `"Pipe3D"`, never `"CompositeCurve3D"`.
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
    """A `CompositeCurve3D` value (already decoded by `_structure_value`) -> one JSON-FG `LineString`.

    `Simplified` (`LIST {1..*} OF PolylineStraight3D`) is a CONNECTED
    sequence (swisstopo "Basismodul 3D" SS2.3: each segment's start point
    equals the previous segment's end point, except the first/last) - the
    segments concatenate into a SINGLE `LineString`, never
    `MultiLineString`/`MultiCurve` (those would misrepresent it as
    disconnected pieces). The shared joint point is dropped once per
    junction only when it is actually identical on both sides - never
    guessed away otherwise (RULE #5).
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
    """The JSON-FG `LineString` for one already-decoded `Curve3D` value.

    `PolylineStraight3D` reads its single `Geometry` directly;
    `CompositeCurve3D` concatenates its segments via
    `_composite_curve3d_linestring`.
    """
    if getattr(type_instance, "Name", None) == "PolylineStraight3D":
        geometry = value.get("Geometry")
        return geometry if isinstance(geometry, dict) and geometry.get("type") == "LineString" else None
    return _composite_curve3d_linestring(value)


_curve3d_place_and_crs = _place_and_crs_via(_curve3d_coord_type, _curve3d_geometry)


def _is_composite_surface3d(type_instance: MetaInstance | None) -> bool:
    """Whether `type_instance` is `Geometry3D_V2.Tin3D`/`.SurfaceShell3D`/`.CompositeSurface3D`, standing alone.

    Same Name+shape matching as `_is_solid3d`. A `SurfaceShell3D` NESTED
    inside `Solid3D.OuterShell`/`.InnerShells` is never reached by this
    check - it is only ever a top-level Class attribute here, read
    directly via `_polyhedron_shell` as part of `Solid3D` otherwise.
    """
    return (
        isinstance(type_instance, MetaInstance)
        and getattr(type_instance, "Kind", None) == "Structure"
        and getattr(type_instance, "Name", None) in {"Tin3D", "SurfaceShell3D", "CompositeSurface3D"}
        and "Simplified" in attributes_of(type_instance)
    )


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
    """A `Tin3D`/`SurfaceShell3D`/`CompositeSurface3D` value (already decoded by `_structure_value`) -> `MultiPolygon`.

    Flattened as a plain mesh of triangle patches (`_polyhedron_shell`,
    shared with `Solid3D`'s own shell reading) - a standalone
    `SurfaceShell3D`'s closure is NOT reconstructed into a single-shell
    `Polyhedron` here (that would need detecting watertightness, which
    this converter has no way to check from the wire alone); the open
    (`Tin3D`) and closed (`SurfaceShell3D`) cases both get the same
    honest "here are the triangles" representation.
    """
    patches = _polyhedron_shell(value)
    return None if patches is None else {"type": "MultiPolygon", "coordinates": patches}


_composite_surface3d_place_and_crs = _place_and_crs_via(
    _composite_surface3d_coord_type, lambda _type_instance, value: _composite_surface3d_multipolygon(value)
)


def _is_chbase_multisurface(type_instance: MetaInstance | None) -> bool:
    """Whether `type_instance` is (an occurrence of) `GeometryCHLV95_V1`/`GeometryCHLV03_V1`'s `MultiSurface`.

    A STRUCTURE wrapping `Surfaces: BAG {1..*} OF SurfaceStructure`, each
    `SurfaceStructure` holding one `Surface` (`LineType`, Kind=Surface) -
    the CHBase base-module convention for a multi-part surface predating
    (or alongside) a native `LineType.Multi=True` attribute, which
    `_line_geometry` already handles directly. Real corpus evidence:
    `RichtplanungErneuerbareEnergien_V1.Objekte.Flaeche.Geometrie`.
    """
    return (
        isinstance(type_instance, MetaInstance)
        and getattr(type_instance, "Kind", None) == "Structure"
        and getattr(type_instance, "Name", None) == "MultiSurface"
        and "Surfaces" in attributes_of(type_instance)
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
    """A `MultiSurface` value (already decoded by `_structure_value`) -> `MultiPolygon`/`MultiSurface`.

    Each `SurfaceStructure.Surface` is already a fully-built `Polygon`/
    `CurvePolygon` dict (`_line_geometry` ran on it during the generic
    `_structure_value` recursion) - this just re-aggregates them one level
    up, mirroring `_line_geometry`'s own straight-vs-curved branching for a
    native `Multi=True` LineType attribute.
    """
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


def _is_pointcloud3d(type_instance: MetaInstance | None) -> bool:
    """Whether `type_instance` is (an occurrence of) `Geometry3D_V2.PointCloud3D` - see module-level constant."""
    return (
        isinstance(type_instance, MetaInstance)
        and getattr(type_instance, "Kind", None) == "Structure"
        and getattr(type_instance, "Name", None) == "PointCloud3D"
        and "Points" in attributes_of(type_instance)
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


def _feature_schema_ref(schema_url: str, feature_type: str) -> str:
    """One `$defs` entry of a `convert/jsonschema.py.model_to_json_schema` document, addressed by URI fragment.

    `schema_url` is caller-supplied (this pure-conversion runtime has no
    schema-hosting story of its own to derive it from - same stance as
    `--repo`/`--model`, always user-provided); `feature_type` is the SAME
    short `Name` `model_to_json_schema` already keys `$defs` by (see
    `class_to_json_schema`'s `title`), so the fragment always resolves.
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
    """Convert one XtfObject into a JSON-FG Feature object.

    `omit_multivalue` (opt-in, `False` by default - zero behavior change
    for every existing caller): drops every top-level `BAG`/`LIST OF`
    property instead of inlining it as a JSON array - used by
    `transfer_to_feature_collection`'s `include_child_rows=True`, where
    that same data is ALREADY represented as separate child-row Features
    (`_child_row_features`). Left inlined AND duplicated otherwise would
    be dead weight in practice (GDAL's own `-append` into the pre-created
    `convert/sql.py` schema has no matching column to receive it, per
    GDAL's own documented "-append does NOT add missing fields" - it
    would just be silently discarded on load, never a wrong load, but
    wasted file size and a confusing thing for a human to read). Does NOT
    affect a NESTED `BAG`/`LIST` inside a STRUCTURE property - only
    TOP-LEVEL `BAG`/`LIST` attributes have a child table at all
    (`convert/sql.py` flattens one level deep, never nested).

    `cls` is the already-resolved Class/Structure instance for
    `obj.qualified_class` (xtf.schema.resolve_class) - resolution stays
    the caller's job, same split as xtf/validate.py's `_validate_object`,
    so this function is a pure value transform.

    `symbol_table`, when given, additionally includes EMBEDDED
    ASSOCIATION ROLES (`xtf.schema.schema_members_of` instead of plain
    `attributes_of`) as pseudo-attributes - same opt-in precondition as
    `convert/jsonschema.py`'s `class_to_json_schema`. `None` (the
    default) means own+inherited `ClassAttribute`s only.

    `standalone=True` (default): produced as a JSON-FG "root object" in
    its own right (OGC 21-045r1 clause 8: "not contained in another
    JSON-FG object") - carries its own "conformsTo" (core, and
    types-schemas since "featureType" is always included, per core
    requirement /req/core/metadata.H; plus circular-arcs, SS7.5, whenever
    "place" ends up one of CircularString/CompoundCurve/CurvePolygon/
    MultiCurve/MultiSurface - see `_read_polyline`/`_read_surface`).
    `standalone=False` (used by
    `transfer_to_feature_collection` for a Feature nested inside a
    FeatureCollection, which becomes the root object instead) OMITS
    "conformsTo" - required, not a style choice: /req/core/metadata.C
    states "Every other JSON-FG object SHALL NOT include a 'conformsTo'
    member." "id" is included only when `obj.tid` is set (GeoJSON RFC
    7946 SS3.2: OPTIONAL, string or number) - never a literal `null`,
    unlike "geometry" which RFC 7946 requires as a member even when
    unlocated (`null`). "featureType" reuses the class's short `Name`
    (same identifier convert/jsonschema.py uses as its $defs key), so a
    Feature and its schema entry can be linked by name via "featureSchema"
    (clause 13, `/req/types-schemas/feature-schemas`) whenever `schema_url`
    is given - a plain string URI (`_feature_schema_ref`), valid here
    since a standalone Feature only ever has ONE "featureType" (clause 13
    requirement `single-feature-schema` - a string value requires every
    "featureType" in the document to match). `None` (the default) omits
    "featureSchema" entirely, unchanged from before this was wired - this
    runtime has no schema-hosting story of its own, so the link is only
    ever built when the caller supplies where the companion
    `model_to_json_schema` document will be reachable.

    Geometry ("place"/"coordRefSys"): every own+inherited attribute whose
    type resolves directly to CoordType/LineType, plus the specific
    STRUCTURE-wrapped BAG/LIST shapes this module recognizes by name
    (Solid3D, Curve3D, Tin3D/SurfaceShell3D/CompositeSurface3D,
    PointCloud3D, GeometryCHLV95_V1/GeometryCHLV03_V1's MultiSurface - a
    generic unnamed BAG/LIST wrapper stays out of scope, no real corpus
    evidence) AND whose actual wire value converts cleanly (see
    `_place_and_crs` - a `None` result, e.g. an unresolved CRS, leaves that
    one attribute in "properties" instead, still holding its own converted
    GeoJSON-object value; only a genuine parse failure, e.g. a custom LINE
    FORM segment, leaves it out, recorded as JSONFG-GEOMETRY-UNREADABLE - a
    STRUCTURE-wrapped shape (Solid3D and the others above) keeps its full
    nested-STRUCTURE value, already a complete, useful representation on
    its own - never a silent loss either way) is collected.
    Exactly one such attribute becomes "place" directly (unchanged
    behaviour); two or more become a single "place" of type
    `GeometryCollection` bundling all of them, in declaration order - no
    "primary" geometry is picked (real corpus evidence, e.g. `Station` in
    `ElektrischeAnlagenNennspannungUeber36kV_V1.ili`: a mandatory point +
    an optional area, both meaningful, neither disposable) - all members
    of the collection must share the exact same `coordRefSys` (they
    always do in every real case found; a class whose geometry attributes
    disagree on CRS gets no "place" at all instead of guessing which one
    wins). A class with zero resolvable geometry attributes still gets no
    "place" (nothing to build it from). "geometry" (the WGS84 GeoJSON
    fallback) always stays `null` here - reprojecting LV95/LV03 to WGS84
    would need a real coordinate-transform dependency, out of scope for
    this pure-Python runtime; JSON-FG core explicitly allows this
    ("geometry" is `null` when no valid WGS84 representation exists).
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
    properties = _members_value(cls, obj.attributes, symbol_table=symbol_table)
    if omit_multivalue:
        for name, resolved in resolved_attrs.items():
            if resolved.type_kind == "MultiValue":
                properties.pop(name, None)

    place: dict[str, Any] | None = None
    crs_uri: str | None = None
    geometry_names = [name for name, r in resolved_attrs.items() if r.type_kind in _GEOMETRY_KINDS]
    geometry_names_set = set(geometry_names)
    solid3d_names = {name for name, r in resolved_attrs.items() if _is_solid3d(r.type_instance)}
    curve3d_names = {name for name, r in resolved_attrs.items() if _is_curve3d(r.type_instance)}
    composite_surface3d_names = {name for name, r in resolved_attrs.items() if _is_composite_surface3d(r.type_instance)}
    pointcloud3d_names = {name for name, r in resolved_attrs.items() if _is_pointcloud3d(r.type_instance)}
    chbase_multisurface_names = {name for name, r in resolved_attrs.items() if _is_chbase_multisurface(r.type_instance)}
    # Single pass over `resolved_attrs` (declaration order) so a class
    # mixing a plain 2D/curve geometry with a 3D/CHBase shape gets its
    # `GeometryCollection` in the documented declaration order, not
    # grouped by shape category.
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

    # A top-level geometry attribute that could NOT become `place` (no
    # resolvable CRS, or a CRS mismatch across several) already holds the
    # same GeoJSON-object value `_attribute_value` computes for a NESTED
    # geometry (`_coord_geometry`/`_line_geometry`) - keep it rather than
    # discarding real coordinates. Only a genuine parse failure (custom
    # LINE FORM segment, unreadable wire data - `properties[name]` is
    # `None`) is left out: no GeoJSON can stand for it.
    for name in geometry_names:
        if name in properties and name not in placed_names and not isinstance(properties[name], dict):
            del properties[name]
            _record("JSONFG-GEOMETRY-UNREADABLE", resolved_attrs[name].attr, "geometry not readable: left out")

    feature: dict[str, Any] = {"type": "Feature"}
    if standalone:
        conforms_to = [CONF_CORE, CONF_TYPES_SCHEMAS]
        place_types = _place_type_names(place)
        if place_types & _CIRCULAR_ARC_TYPES:
            conforms_to.append(CONF_CIRCULAR_ARCS)
        if place_types & _POLYHEDRA_TYPES:
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


# --- VIEW evaluation --------------------------------------------------------
#
# Every FormationKind is evaluated in memory against an already-parsed
# XtfTransfer, producing JSON-FG Features shaped like the VIEW rather than
# its raw base class(es) - the FGDM4GS report's central equivalence (a VIEW
# is a FeatureType projection instruction):
#
#   PROJECTION OF   one base           re-tag each matching object
#   JOIN OF         N bases            cartesian product (`_join_combinations`)
#   UNION OF        N bases            concatenate every base's objects
#   AGGREGATION OF  one base, ALL/EQUAL  one Feature per group (counts computed)
#   INSPECTION OF   one base -> attr   one Feature per element of the attr (a
#                                      geometry attr: boundary/edge/segment)
#
# A `WHERE` clause narrows a JOIN/PROJECTION per combination. It is
# evaluated by `convert/constraint_eval.evaluate_expression` over a
# properties dict where each base alias maps to an `_ObjView` (navigable
# like the object's own attribute values, AND `==` its OID so a bare alias
# in the WHERE denotes "this object"). That reuses the CONSTRAINT
# evaluator's full node coverage (`And`/`Or`/`Not`/`Defined`/`Implication`/
# relational, constants, nested-STRUCTURE paths). Only a construct that
# evaluator itself doesn't support (arithmetic, `THIS`/`PARENT`, a
# function call - `_view_where_unsupported`) leaves the VIEW skipped with a
# clear diagnostic (RULE #5), never a silently wrong result.

_UNSET = object()

_ARITHMETIC_OPS = {"Mult", "Div", "Plus", "Minus"}


class _ObjView(dict):
    """A base object's value inside a view-WHERE properties dict.

    Behaves as a dict of the object's own attribute values (for path
    navigation, `constraint_eval._resolve_path`) but compares `==` to the
    object's OID string, so a bare base alias in the `WHERE` (`... == Road`)
    denotes that object's identity.
    """

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


def _raw_node_value(node: RawNode) -> Any:
    """One attribute occurrence -> a comparable value.

    A referenced OID, a nested dict, or a (numeric-coerced) scalar.
    """
    ref_tid = _extract_reference(node)
    if ref_tid is not None:
        return ref_tid
    if node.children:  # a STRUCTURE occurrence - navigable one more hop
        nested: dict[str, Any] = {}
        for child in node.children:
            nested.setdefault(child.tag, _raw_node_value(child))
        return nested
    return _try_number(node.text, node.text) if node.text is not None else None


def _obj_view(obj: XtfObject, by_tid: dict[str, XtfObject], seen: frozenset[str]) -> _ObjView:
    """One object as an `_ObjView`, with each reference attribute resolved to the target's own `_ObjView`.

    Lets a view `WHERE` navigate an association hop
    (`Grundstueck->Entstehung->Grundbucheintrag`, the DMAV `*_Gueltig`
    idiom): the middle hop is a reference whose target object's attributes
    must be reachable. A nested `_ObjView` still compares `==` to its OID,
    so `... == Alias` identity tests keep working. `seen` breaks reference
    cycles.
    """
    if obj.tid is not None and obj.tid in seen:
        return _ObjView(obj.tid, {})
    next_seen = seen | ({obj.tid} if obj.tid is not None else frozenset())
    attrs: dict[str, Any] = {}
    for name, nodes in obj.attributes.items():
        if not nodes:
            continue
        value = _raw_node_value(nodes[0])
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

    Reused by `cli.cmd_convert_jsonfg` to print a clear diagnostic for
    every VIEW it skips instead of a silent omission. The remaining
    reasons are a genuinely unavailable base model (RULE #9 - provide it
    via `--repo`), a `WHERE` construct outside the CONSTRAINT evaluator's
    scope (arithmetic / function call), and an AGGREGATION column that is
    a user-FUNCTION result over the implicit `AGGREGATES` bag (an external
    function the model only declares - no converter can run it).
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
    """Cartesian product of `objects_by_base`, one list per base, with `RenamedBaseView.OrNull` outer-join handling.

    Reference Manual eCH-0031 V2.1.0 SS3.16 (JOIN OF): "kartesisches Produkt
    der Basis-Klassen" - a literal cartesian product, no join KEY/condition
    of its own (a WHERE clause narrows the result on top, handled
    separately, see `unsupported_view_reason`). "(OR NULL)": "wenn zu
    einer bestimmten Kombination der vorangegangenen Objekte kein Objekt
    der gewuenschten weiteren Klasse gefunden wird" - since there is no
    WHERE here to narrow a combination-by-combination match, a base
    contributes "no object found" only when it has ZERO objects in the
    whole transfer; `OrNull` then keeps every partial combination alive
    with a `None` placeholder for that base (contributing no attributes)
    instead of collapsing the whole JOIN to the empty set.
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
    """Re-key `obj`'s wire attributes under `view`'s own `ClassAttribute` names.

    `object_to_feature`/`_members_value` look up each property by the
    Class's OWN schema attribute name - correct for a plain Class, and for
    an `ALL OF` view attribute (its synthetic identity `Derivates` is
    `<base> -> <attr>` with `attr == Name`, a no-op here: `source ==
    out_name` is skipped below). WRONG for any RENAMED view attribute
    (`Name := <alias> -> <attr>` with a different `Name`, e.g. a
    lower-cased WFS/ArcGIS field name) - PROJECTION/JOIN (pooled via
    `_merge_join_combo`)/AGGREGATION all fed the object straight through
    before this fix, so every such attribute was silently dropped (empty
    `properties`) - confirmed on real `xtf_corpus/geoadmin` data
    (`MainRoads_LV95_V1_1_d.view_roadsegment`), not a synthetic edge
    case: a VIEW derived from a published geodata service routinely
    names its attributes after the service's own field names, virtually
    never matching the base attribute's own spelling/case.

    Mirrors `_union_projected_object`'s remap (that one is per-base/
    `n_bases`-aware, for UNION's one-`Derivates`-entry-per-base shape);
    every other FormationKind's `ClassAttribute` carries a single source
    expression, `Derivates[0]`. For JOIN, `obj` is already the merged
    combo (`_merge_join_combo`) - a plain source-name lookup against the
    pooled attributes is correct here for the same reason
    `_merge_join_combo` itself pools blindly: no real corpus evidence of
    an attribute-name collision between two JOIN bases.
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

    `attributes` are pooled from every non-`None` participant (a real
    key collision between 2 bases' own attribute names would let the
    later participant win silently - no real corpus evidence of this, the
    2 known real JOIN OF examples are attribute-disjoint). `tid` joins
    every participant's own tid with `_` (`None` if none has one) - a
    simple, stable synthetic id; JSON-FG's "id" is OPTIONAL (RFC 7946
    SS3.2), so this is a convenience, not a spec requirement.
    `qualified_class` is set to the VIEW's own short name - never read by
    `object_to_feature` (it derives "featureType" from `cls.Name`, the
    VIEW itself, passed separately), kept only for readability/debugging.
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

    An explicit union assignment gives every attribute one source per base,
    in base declaration order (`Attr := C1->A, C2->B`): base `i`'s objects
    carry `A` on the wire, so `A` is copied to `Attr` for base `i` and `B`
    to `Attr` for base `i+1`, letting `object_to_feature` (which reads by
    the view's own attribute names) emit the union attribute whatever base
    an object came from. An `ALL OF <base>` union attribute has a single
    identity source and the base objects already carry it under the right
    name - it is left as a straight pass-through. A source that is not a
    plain one-hop path into that base is skipped for that object, the same
    best-effort the JOIN pooling takes.
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

    `View.FormationParameter` (a `PathOrInspFactor` per the spec) is where
    the builder should put it; until that binding actually populates
    (`spec/grammar/mapping/09_views_graphics.yml`'s `inspection` entry),
    `_inspection_path_from_ctx` in `InterlisModelBuilder` stashes the raw
    `Name` tokens on `view._inspection_path` instead.
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

    One combo per kept result row - a 1-element list for PROJECTION/
    AGGREGATION (one base), one element per base for JOIN
    (`_join_combinations`, `None` for an `OR NULL` outer-join miss).
    Shared by `evaluate_view` (JSON-FG) and `evaluate_view_objects` (the
    write-xtf path) so both stay in lockstep on which rows a VIEW produces
    - only the final per-combo SHAPE differs between the two callers.
    UNION/INSPECTION have no combo concept of their own (see `evaluate_view`)
    and never call this helper. Precondition (both callers already check
    it): `view.FormationKind` is one of these 3 kinds.
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
        target = _extract_reference(nodes[0]) if nodes else None
        current = by_tid.get(target) if target else None
    return []


def _constant_nodes(factor: MetaInstance, name: str) -> list[RawNode]:
    value = getattr(factor, "Value", None)
    if value is None:
        return []
    text = _unquote_interlis_string(value) if getattr(factor, "Type", None) == "Text" else str(value)
    return [RawNode(name, text, {}, [])]


def _aggregation_objects(
    view: MetaInstance,
    bases: list[MetaInstance],
    combos: list[list[XtfObject | None]],
    by_tid: dict[str, XtfObject],
) -> list[XtfObject]:
    """Evaluate `AGGREGATION OF base (ALL | EQUAL(key))` into one `XtfObject` per group.

    Mirrors the SQL translation exactly: `EQUAL(key)` groups on the key and
    on every plain attribute (`GROUP BY`); `ALL` without a count keeps the
    distinct rows (`SELECT DISTINCT`); `ALL` with a count column is a single
    group, present even when no base object survives (an ungrouped
    `COUNT(*)` always returns a row). `INTERLIS.objectCount/elementCount
    (AGGREGATES)` columns hold the group size. A group stands for several
    base objects, so its row has no TID - except a plain `ALL` row, which
    keeps its first member's TID.
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
    """Evaluate `view` into projected/merged `XtfObject`s - `evaluate_view`, one step earlier.

    Same PROJECTION/AGGREGATION/JOIN row selection (`_view_combos`) and
    `WHERE` filtering as `evaluate_view`, but stops before
    `object_to_feature`: each returned `XtfObject` already carries its
    `.attributes` keyed under the VIEW's own `ClassAttribute` names
    (`_project_object_under_view_names`/`_merge_join_combo`), as raw
    `RawNode` XML subtrees straight from `transfer` - never decoded into a
    JSON-FG "place"/properties split. This is the shape
    `convert/xtf_writer.py`'s `write_xtf` re-serializes losslessly (no
    reverse-engineering INTERLIS geometry XML from JSON-FG "place"), and
    what `evaluate_view` itself builds on for these 3 FormationKinds.

    UNION returns each base's own objects, re-keyed under the union's
    per-base attribute assignment (`_union_projected_object`). INSPECTION
    returns one `XtfObject` per inspected element (`_inspection_elements`)
    - EXCEPT an inspection of a SURFACE/AREA/POLYLINE attribute
    (`geometry_inspection`), which raises `ValueError`: its elements are
    derived geometry values with no element TYPE (see
    `_evaluate_geometry_inspection`), nothing an XTF writer could
    serialize as a named attribute.

    Raises `ValueError` if `unsupported_view_reason(view)` isn't `None`,
    same precondition as `evaluate_view`.
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
    """Evaluate `view` against `transfer` into JSON-FG Features - every FormationKind.

    Raises `ValueError` if `unsupported_view_reason(view)` isn't `None` -
    callers (e.g. `cmd_convert_jsonfg`) are expected to check that first
    and skip with a diagnostic, never call this blind.

    - `PROJECTION OF` (1 base): each matching base object re-tagged with
      `view` as its `cls` - the View's `ClassAttribute` list already
      carries the base's own wire attribute names (`ALL OF`), so
      `object_to_feature` emits exactly the View's declared properties.
    - `JOIN OF` (N bases): the cartesian product of each base's matching
      objects (`_join_combinations`), each combination merged
      (`_merge_join_combo`).
    - `UNION OF` (N bases): every base's matching objects, concatenated,
      each re-tagged with `view` (compatible base viewables - the union of
      their extensions).
    - `AGGREGATION OF` (1 base): one Feature per group (`_aggregation_objects`):
      `EQUAL(key)` groups on the key, `ALL` keeps the distinct rows;
      `INTERLIS.objectCount/elementCount(AGGREGATES)` columns count the group.
    - `INSPECTION OF base -> attr` (1 base): one Feature per element of the
      inspected `BAG`/`LIST`/reference attribute on each base object.

    A `WHERE` clause narrows `JOIN`/`PROJECTION` per combination, evaluated
    by `constraint_eval.evaluate_expression` over `_combo_properties`.
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

    `hop_is_multi[i]` says whether `path[i]` is itself a `BAG`/`LIST OF`
    attribute - `_evaluate_inspection` needs this for EVERY hop, not just
    the last (`is_multi`), to unwrap a multi-value wrapper into its
    occurrences before matching the NEXT hop's tag on each occurrence (an
    indirect path through a `BAG`/`LIST OF` structure at an intermediate
    level, not only at the end). Always `len(path)` long, even when a hop
    fails to resolve (padded with `False` - best-effort, same
    graceful-degradation stance as everywhere else, RULE #5).
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
    """Concatenate the consecutive POLYLINE pieces of one boundary into a single line.

    eCH-0031 SS3.15 builds the `SurfaceEdge`s of a boundary so that their
    number is minimal: consecutive polylines are merged into one.
    """
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
    if surface is None or _geom_tag(surface) not in _SURFACE_TAGS:
        return None
    boundaries: list[list[dict[str, Any]]] = []
    for boundary in (c for c in surface.children if _geom_tag(c) in _BOUNDARY_TAGS):
        pieces: list[dict[str, Any]] = []
        for polyline in (c for c in boundary.children if _geom_tag(c) == "POLYLINE"):
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
    if _geom_tag(polyline) != "POLYLINE" or not polyline.children or _geom_tag(polyline.children[0]) != "COORD":
        return None
    segments: list[tuple[list[float], list[float] | None]] = []
    for node in polyline.children:
        tag = _geom_tag(node)
        if tag == "COORD":
            end = _read_coord(node)
            if end is None:
                return None
            segments.append((end, None))
        elif tag == "ARC":
            arc = _read_arc(node)
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
    """Identity of an edge line for `AREA INSPECTION`: a line equals its reversal (eCH-0031 SS4.3.11.15)."""
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
    """One Feature per element of an `INSPECTION OF` a SURFACE/AREA/POLYLINE attribute (eCH-0031 SS3.15).

    Each view attribute is a derived value (a GeoJSON geometry, or a typed
    attribute of the owning object read through `PARENT`/`THISAREA`/
    `THATAREA`), set in `properties` and never promoted to `place`: it is
    not the object's own designated geometry. Mirrors the SQL translation
    (`views.py`); `view_formation.inspection_reading` fixes what is readable.
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
    """One Feature per element of the inspected attribute (`INSPECTION OF base -> attr`) on each base object.

    The element type is resolved from the path (`_inspection_target`) so
    `object_to_feature` emits the ELEMENT's own attributes, not the base's;
    `View` still supplies `featureType`. A `BAG`/`LIST OF` attribute
    transfers its occurrences as DIRECT CHILDREN of one wrapper element
    (the same wire convention `_multi_value` relies on) - those children
    are the elements; a single reference/structure attribute is itself the
    element. An INDIRECT path (`-> a -> b`) unwraps every multi-value hop
    (`hop_is_multi`), not just the last one. A path ending on a
    SURFACE/AREA/POLYLINE attribute has no element type at all and is
    decomposed by `_evaluate_geometry_inspection`.
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
    """Every element of `INSPECTION OF base -> path` as a synthetic `XtfObject`, its resolved element type and the
    attributes of each element's immediate parent (the owning object, or the enclosing structure element).

    Extracted from `_evaluate_inspection`'s own construction loop - shared
    with `evaluate_view_objects`'s INSPECTION branch (`convert/xtf_writer.py`'s
    write-xtf path), which needs the raw `XtfObject`s rather than
    `object_to_feature`'s JSON-FG output. See `_evaluate_inspection`'s
    docstring for the wire-unwrapping rule at each multi-value hop. Does
    NOT cover a geometry inspection (`geometry_inspection`): it has no
    element TYPE to resolve (see `_evaluate_geometry_inspection`).
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
                holder_attrs = _group_by_tag(holder.children)
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

    A `JOIN OF` collection is GET-only (no natural single writable
    target for a multi-base combination, by analogy with non-updatable
    SQL views). This is what makes that limitation actionable rather than
    a dead end: each base object this runtime ALREADY publishes as
    its own, independently fully-writable collection (a plain `Class` -
    `transfer_to_feature_collection` emits it via `resolve_class`
    regardless of any `views=` given), so a client that needs to edit a
    property coming from one specific base can resolve WHICH collection/
    id to `PUT`/`PATCH` instead, straight off the JOIN Feature itself -
    no need to reverse-engineer `_merge_join_combo`'s `tid` join
    convention. `featureType` mirrors `object_to_feature`'s own
    convention (`cls.Name`, falling back to the wire tag).
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

    `include_child_rows` (opt-in, `False` by default - zero behavior
    change for every existing caller): also appends one Feature per
    `BAG`/`LIST OF` occurrence (`_child_row_features`), each carrying its
    own `"featureType"` matching a `convert/sql.py` child table name.
    GDAL's own `"featureType"`-based table splitting (already relied on
    for the main data) routes them into the SAME child tables in the SAME
    `ogr2ogr -append` call as everything else.

    Walks all of `transfer`'s baskets (real corpus evidence: 11/12
    xtf_corpus/geoadmin files hold exactly 1 basket, the one exception
    holds 2 - not worth a separate per-basket entry point) and resolves
    each object's Class itself (xtf.schema.resolve_class), same
    architecture as xtf/validate.py's `validate_transfer` (which likewise
    resolves internally, unlike the single-object `object_to_feature`/
    `_validate_object` pair - a whole-transfer entry point naturally has
    `symbol_table`/`repository` on hand already, e.g. from
    `cli.cmd_convert_jsonfg`). An object whose class doesn't resolve is
    skipped - not this converter's job to flag (validate() does), same
    stance `object_to_feature` already takes for an unknown attribute
    NAME; there is no schema to draw even a featureType/properties from
    without a resolved class, so there is nothing to emit for it.

    Each Feature is produced with `standalone=False` (no per-feature
    "conformsTo" - the FeatureCollection is the JSON-FG root object here,
    RULE /req/core/metadata.C). When every produced Feature shares the
    same "featureType", it is hoisted once onto the collection itself
    (clause 13 Recommendation A, "homogeneous feature collections") AND
    removed from every Feature - same hoist-and-remove pattern as
    "coordRefSys" below, and matching the Standard's own official example
    (`core/examples/airports.json`: `"featureType"` on the collection,
    absent from every feature). Clause 13 Requirement B's "either...or"
    is satisfied by the collection alone; a real third-party validator
    (`geonovum.github.io/ogc-checker`) rejects a document carrying both.

    "coordRefSys" hoisting (uniform case only) is NOT an optional
    optimization - it is what `/req/core/same-crs` actually requires
    ("A 'coordRefSys' member SHALL only be included in the JSON-FG root
    object and not in any other JSON-FG objects") and what the
    Standard's OWN official example demonstrates verbatim
    (`core/examples/airports.json`, opengeospatial/ogc-feat-geo-json):
    `"coordRefSys"` declared once on the `FeatureCollection`, entirely
    ABSENT from each nested Feature. When every Feature that has a
    "place" shares the same "coordRefSys", it is moved to the collection
    and removed from every Feature. When they differ (heterogeneous CRS
    within one collection - no real corpus evidence found: every basket
    seen so far uses one CRS throughout), per-feature "coordRefSys" is
    left as a conservative fallback rather than inventing an unverified
    geometry-level placement with no official example to check it
    against.

    `views` (optional): PROJECTION OF/JOIN OF Views
    already filtered by the caller to ones `evaluate_view` can actually
    handle (same `unsupported_view_reason` check `cmd_convert_jsonfg`
    applies before calling this - this function assumes every given view
    IS evaluable, raising via `evaluate_view` rather than silently
    skipping one that isn't). Evaluated against the SAME `transfer` and
    folded into the SAME feature list BEFORE the featureType/coordRefSys
    uniformity hoisting below runs, so a transfer producing only
    view-shaped Features (or a homogeneous mix of both) still benefits
    from collection-level hoisting exactly like class-shaped Features do.

    `schema_url` (optional): when given, wires "featureSchema" (clause 13)
    at the COLLECTION level only (same "single root object carries it"
    stance as "conformsTo" - never duplicated per-Feature here). A
    homogeneous collection (single "featureType", the same condition
    already used above) gets a plain string URI (`_feature_schema_ref`,
    clause 13 requirement `single-feature-schema`); a heterogeneous one
    gets the OTHER value shape the standard allows instead - an object
    mapping every distinct "featureType" to its own `$defs` fragment
    (`featureschema.json`'s `oneOf` - a bare string would otherwise wrongly
    claim just one schema covers every Feature). `None` (the default)
    omits "featureSchema" entirely, same as `object_to_feature`.

    `unmapped` (optional, out): receives each value passed through untyped or left out, for
    `collect_diagnostics` - the document itself never carries a marker.
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


def _transfer_to_feature_collection(
    transfer: XtfTransfer,
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
    views: list[MetaInstance] | None = None,
    schema_url: str | None = None,
    include_child_rows: bool = False,
) -> dict[str, Any]:
    features: list[dict[str, Any]] = []
    for basket in transfer.baskets:
        for obj in basket.objects:
            cls = resolve_class(obj.qualified_class, symbol_table=symbol_table, repository=repository)
            if cls is None:
                continue
            # The declaring model's table holds the associations embedding a role on `cls`.
            cls_table = home_symbol_table(obj.qualified_class, symbol_table=symbol_table, repository=repository)
            features.append(
                object_to_feature(
                    obj,
                    cls,
                    standalone=False,
                    symbol_table=cls_table,
                    repository=repository,
                    omit_multivalue=include_child_rows,
                )
            )
            if include_child_rows:
                with _feature_scope(getattr(cls, "Name", None) or obj.qualified_class, obj.tid):
                    features.extend(_child_row_features(obj, cls, symbol_table=symbol_table))

    for view in views or []:
        features.extend(
            evaluate_view(view, transfer, symbol_table=symbol_table, repository=repository, standalone=False)
        )

    conforms_to = [CONF_CORE, CONF_TYPES_SCHEMAS]
    all_place_types: set[str] = set()
    for f in features:
        all_place_types |= _place_type_names(f.get("place"))
    if all_place_types & _CIRCULAR_ARC_TYPES:
        conforms_to.append(CONF_CIRCULAR_ARCS)
    if all_place_types & _POLYHEDRA_TYPES:
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
    """Return a `Diagnostic` for a VIEW `cmd_convert_jsonfg` has to skip, or `None` if it can be evaluated.

    Same check as `unsupported_view_reason`, classified onto a stable id:
    a missing base model is class C (`--repo`), a WHERE the CONSTRAINT
    evaluator can't do is class B, an unbuilt INSPECTION path is a class-A
    builder gap.
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


def collect_diagnostics(unmapped: list[UnmappedValue], *, file: str | None = None):
    """One `Diagnostic` per value `transfer_to_feature_collection` recorded in its `unmapped` list.

    An unresolved BAG/LIST element type is class C (provide the model); an untyped or unreadable value class A.
    """
    from interlis.diagnostics import Diagnostic, Location

    out: list[Diagnostic] = []
    for event in unmapped:
        resolvable = event.rule == "JSONFG-MULTIVALUE-UNRESOLVED"
        out.append(
            Diagnostic(
                "warning" if resolvable else "note",
                event.rule,
                f"{event.element_path}: {event.detail}",
                Location(file=file, element_path=event.element_path, tid=event.tid),
                help="pass its model's directory to --repo" if resolvable else None,
            )
        )
    return out
