""".ili -> JSON Schema conversion: scalar types, STRUCTURE/BAG/LIST nesting, REFERENCE TO, embedded association
roles, and ABSTRACT structure polymorphism.

Walks IlisMeta16 instances already built by InterlisModelBuilder, reusing xtf.schema's type-resolution helpers.
"""

from collections.abc import Iterator
from typing import Any

import jsonschema

from interlis.builder.forward_refs import SymbolTable
from interlis.convert.constraint_catalogue import class_constraints
from interlis.convert.constraint_eval import describe_expression
from interlis.convert.geometry_kinds import GEOMETRY_KINDS, geometry_format
from interlis.convert.temporal import temporal_roles
from interlis.convert.view_formation import (
    geometry_inspection,
    inspection_reading,
    is_standard_count_call,
)
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.schema import (
    ResolvedAttribute,
    attributes_of,
    concrete_structure_subclasses,
    enum_values,
    is_integer_range,
    is_structure,
    nested_class,
    reference_external_status,
    reference_target_class,
    resolve_attribute,
    schema_members_of,
    wrapped_reference,
)

_MAX_INLINE_DEPTH = 8
JSON_SCHEMA_DRAFT = "https://json-schema.org/draft/2020-12/schema"


def _meta_marker(instance: MetaInstance | None) -> dict[str, str]:
    """Return `instance`'s `!!@Name=Value` meta-attributes as a `Name -> Value` dict."""
    return {
        m.Name: m.Value
        for m in (getattr(instance, "MetaAttribute", None) or [])
        if isinstance(m, MetaInstance) and getattr(m, "Name", None) is not None
    }


_FULL_CRUD = ("GET", "POST", "PUT", "PATCH", "DELETE")
_READ_ONLY = ("GET",)


def _crud_operations(class_instance: MetaInstance) -> tuple[str, ...] | None:
    """Return the HTTP operations an OGC API Features publication of `class_instance` could support, or `None`.

    `None` for Structure/Association and an abstract Class (never their own collection). A View is GET-only unless it
    is a plain `PROJECTION OF` (1:1 with its base, so writes map back, like an updatable SQL view); a Class gets full
    CRUD.
    """
    kind = getattr(class_instance, "Kind", None)
    if kind not in ("Class", "View") or getattr(class_instance, "Abstract", False):
        return None
    if kind == "View" and getattr(class_instance, "FormationKind", None) != "Projection":
        return _READ_ONLY
    return _FULL_CRUD


def _description(instance: MetaInstance | None) -> str | None:
    """The `/** ... */` documentation comment of a class or attribute, as a JSON Schema `description`."""
    texts = getattr(instance, "Documentation", None) or []
    return " ".join(str(t) for t in texts) or None


def _interlis_marker(cls: MetaInstance, symbol_table: SymbolTable | None) -> dict[str, Any]:
    """The qualified INTERLIS name of a class, the one it extends and whether it is abstract."""
    if symbol_table is None:
        return {}
    marker: dict[str, Any] = {}
    qualified = symbol_table.qualified_name_of(cls)
    if qualified:
        marker["class"] = qualified
    parent = getattr(cls, "Super", None)
    parent_name = symbol_table.qualified_name_of(parent) if isinstance(parent, MetaInstance) else None
    if parent_name:
        marker["extends"] = parent_name
    if getattr(cls, "Abstract", False):
        marker["abstract"] = True
    return marker


def _derivation(view: MetaInstance, symbol_table: SymbolTable | None) -> dict[str, Any]:
    """How a VIEW derives its feature type: formation, base classes with their alias, each attribute's path, filter."""
    kind = getattr(view, "FormationKind", None)
    if kind is None:
        return {}
    bases = []
    for base in getattr(view, "RenamedBaseView", None) or []:
        target = getattr(base, "BaseView", None)
        if isinstance(target, MetaInstance):
            qualified = symbol_table.qualified_name_of(target) if symbol_table is not None else None
            alias = getattr(base, "Name", None) or getattr(target, "Name", None)
            bases.append({"alias": alias, "class": qualified or getattr(target, "Name", None)})
    paths: dict[str, str] = {}
    for attr in getattr(view, "ClassAttribute", None) or []:
        derivates = getattr(attr, "Derivates", None) or []
        elements = getattr(derivates[0], "PathEls", None) if derivates else None
        if elements and getattr(attr, "Name", None):
            paths[attr.Name] = " -> ".join(str(getattr(el, "Ref", None)) for el in elements)
    derivation: dict[str, Any] = {"formation": str(kind).lower(), "of": bases}
    if paths:
        derivation["attributes"] = paths
    where = getattr(view, "Where", None)
    if isinstance(where, MetaInstance):
        try:
            derivation["where"] = describe_expression(where)
        except (AttributeError, IndexError, TypeError):
            pass
    return derivation


def _unit_name(type_instance: MetaInstance) -> str | None:
    """The INTERLIS name of a numeric type's unit (the last segment of an unresolved `Model.Unit` reference)."""
    unit = getattr(type_instance, "Unit", None)
    name = getattr(unit, "Name", None) or getattr(unit, "name", None)
    return str(name).rsplit(".", 1)[-1] if name else None


def _num_type_schema(type_instance: MetaInstance) -> dict[str, Any]:
    min_raw = getattr(type_instance, "Min", None)
    max_raw = getattr(type_instance, "Max", None)
    schema: dict[str, Any] = {"type": "integer" if is_integer_range(min_raw, max_raw) else "number"}
    cast = int if schema["type"] == "integer" else float
    for keyword, raw in (("minimum", min_raw), ("maximum", max_raw)):
        if raw is None:
            continue
        try:
            schema[keyword] = cast(raw)
        except ValueError:
            pass  # non-numeric Min/Max (e.g. a predefined domain) - out of scope, same as validate.py
    unit = _unit_name(type_instance)
    if unit:
        schema["x-ogc-unit"] = unit
    return schema


# NAME is `Letter {Letter|Digit|'_'}` up to 255 chars and URI up to 1023; `TextType.MaxLength` is never set for
# them, so the bounds are supplied here.
_INTERLIS_NAME_PATTERN = r"^[A-Za-z][A-Za-z0-9_]{0,254}$"


def _text_type_schema(type_instance: MetaInstance) -> dict[str, Any]:
    schema: dict[str, Any] = {"type": "string"}
    kind = getattr(type_instance, "Kind", None)
    if kind == "Name":
        schema["pattern"] = _INTERLIS_NAME_PATTERN
        schema["maxLength"] = 255
        return schema
    if kind == "Uri":
        schema["format"] = "uri"
        schema["maxLength"] = 1023
        return schema
    max_length = getattr(type_instance, "MaxLength", None)
    if max_length is not None:
        try:
            schema["maxLength"] = int(max_length)
        except (TypeError, ValueError):
            pass
    return schema


# The three predefined date/time formats. `date` has a JSON Schema `format`; a time and a date-time carry no time
# zone in INTERLIS, which `format: time`/`date-time` (RFC 3339) require, so they get the exact `pattern` instead.
# `FormattedType.Format` holds the bare name (no "INTERLIS." qualifier). Any other format stays a plain string.
_XML_DATE_TIME_PATTERNS = {
    "XMLTime": r"^\d{2}:\d{2}:\d{2}(\.\d+)?$",
    "XMLDateTime": r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?$",
}


def _formatted_type_schema(type_instance: MetaInstance) -> dict[str, Any]:
    """FormattedType -> `type: string`, plus `format: date` or the `pattern` of the two time formats."""
    schema: dict[str, Any] = {"type": "string"}
    name = getattr(type_instance, "Format", None)
    if name == "XMLDate":
        schema["format"] = "date"
    elif name in _XML_DATE_TIME_PATTERNS:
        schema["pattern"] = _XML_DATE_TIME_PATTERNS[name]
    return schema


def _blackbox_type_schema(type_instance: MetaInstance) -> dict[str, Any]:
    """BlackboxType -> `type: string`, with `x-blackbox-kind` and, for BINARY, `contentEncoding: base64`."""
    schema: dict[str, Any] = {"type": "string"}
    kind = getattr(type_instance, "Kind", None)
    if kind is not None:
        schema["x-blackbox-kind"] = kind
    if kind == "Binary":
        schema["contentEncoding"] = "base64"
    return schema


def _enum_type_schema(type_instance: MetaInstance) -> dict[str, Any]:
    # "OTHERS" is a transfer-time escape valid for any enum, not a declared value.
    values = enum_values(type_instance) - {"OTHERS"}
    return {"type": "string", "enum": sorted(values)}


def _scalar_type_schema(kind: str | None, type_instance: MetaInstance | None) -> dict[str, Any] | None:
    """Return the scalar mapping for one (kind, instance) pair, or None if unmapped."""
    if kind == "NumType" and type_instance is not None:
        return _num_type_schema(type_instance)
    if kind == "TextType" and type_instance is not None:
        return _text_type_schema(type_instance)
    if kind == "EnumType" and type_instance is not None:
        return _enum_type_schema(type_instance)
    if kind == "BooleanType" and type_instance is not None:
        return {"type": "boolean"}
    if kind == "FormattedType" and type_instance is not None:
        return _formatted_type_schema(type_instance)
    if kind == "BlackboxType" and type_instance is not None:
        return _blackbox_type_schema(type_instance)
    if kind in GEOMETRY_KINDS and type_instance is not None:
        return {"format": geometry_format(kind, type_instance, feature_level=False)}
    return None


def _reference_type_schema(resolved: ResolvedAttribute) -> dict[str, Any]:
    """Plain `REFERENCE TO X` -> the OID as `type: string` (the XTF wire format transfers a `REF`, never the object).

    The target class and `(EXTERNAL)` flag are surfaced as `x-reference-target` / `x-reference-external`.
    """
    schema: dict[str, Any] = {"type": "string"}
    target = reference_target_class(resolved)
    target_name = getattr(target, "Name", None) if target is not None else None
    if target_name:
        schema["x-reference-target"] = target_name
    if reference_external_status(resolved):
        schema["x-reference-external"] = True
    return schema


_concrete_subclasses = concrete_structure_subclasses


TYPE_KEY = "@type"
"""Key naming the concrete structure of an occurrence, as in ili2db's JSON; INTERLIS names cannot start with `@`."""


def is_polymorphic_structure(cls: MetaInstance | None, symbol_table: SymbolTable | None) -> bool:
    """Whether a structure is abstract, extends a structure, or is extended by one: its occurrences carry `@type`."""
    if cls is None or getattr(cls, "Kind", None) != "Structure" or symbol_table is None:
        return False
    if bool(getattr(cls, "Abstract", False)) or getattr(getattr(cls, "Super", None), "Kind", None) == "Structure":
        return True
    return bool(_concrete_subclasses(cls, symbol_table))


def structure_discriminant(cls: MetaInstance, symbol_table: SymbolTable | None) -> str:
    """The `@type` value of a structure: its qualified INTERLIS name, else its short name."""
    qualified = symbol_table.qualified_name_of(cls) if symbol_table is not None else None
    return qualified or str(getattr(cls, "Name", None))


def _class_ref_or_marker(
    class_instance: MetaInstance | None,
    ref_keys: dict[int, str],
    symbol_table: SymbolTable | None = None,
) -> dict[str, Any]:
    """Return a `$ref` to the class's `$defs` entry, or an `x-unsupported` marker if it is not in `ref_keys`.

    With a `symbol_table`, an ABSTRACT structure becomes an `anyOf` over its concrete subclasses.
    """
    if class_instance is not None:
        if symbol_table is not None and is_polymorphic_structure(class_instance, symbol_table):
            members = _concrete_subclasses(class_instance, symbol_table)
            if not getattr(class_instance, "Abstract", False):
                members = [class_instance, *members]
            refs = sorted(ref_keys[id(c)] for c in members if id(c) in ref_keys)
            if refs:
                return {"anyOf": [{"$ref": f"#/$defs/{r}"} for r in refs]}
        ref = ref_keys.get(id(class_instance))
        if ref is not None:
            schema: dict[str, Any] = {"$ref": f"#/$defs/{ref}"}
            if bool(getattr(class_instance, "Abstract", False)):
                schema["x-abstract"] = True
            return schema
    return {"x-unsupported": "Class"}


def _element_schema(
    kind: str | None,
    type_instance: MetaInstance | None,
    ref_keys: dict[int, str],
    symbol_table: SymbolTable | None = None,
) -> dict[str, Any]:
    """Return the schema for one leaf type: a plain attribute's type, or a MultiValue's BaseType.

    A `ReferenceType` or non-structure `Class` here (a BAG/LIST element) falls back to a bare `{"type": "string"}`.
    """
    scalar = _scalar_type_schema(kind, type_instance)
    if scalar is not None:
        return scalar
    if kind == "Class" and is_structure(type_instance):
        wrapped = wrapped_reference(type_instance)
        if wrapped is not None:
            return _reference_type_schema(wrapped)
        return _class_ref_or_marker(type_instance, ref_keys, symbol_table)
    if kind == "Class" and type_instance is not None:
        return {"type": "string"}
    if kind == "ReferenceType" and type_instance is not None:
        return {"type": "string"}
    return {"x-unsupported": kind or "unknown"}


def _multi_value_schema(
    multi_value: MetaInstance,
    ref_keys: dict[int, str],
    symbol_table: SymbolTable | None = None,
) -> dict[str, Any]:
    """`BAG {m..n} OF X` / `LIST {m..n} OF X` -> `type: array` over `BaseType`.

    `Ordered` (LIST vs BAG) has no JSON Schema equivalent (it is not `uniqueItems`), so it is kept as `x-ordered`.
    """
    base = getattr(multi_value, "BaseType", None)
    base = base if isinstance(base, MetaInstance) else None
    base_kind = base._qualified_class.rsplit(".", 1)[-1] if base is not None else None
    schema: dict[str, Any] = {"type": "array", "items": _element_schema(base_kind, base, ref_keys, symbol_table)}

    mult = getattr(multi_value, "Multiplicity", None)
    if isinstance(mult, MetaInstance):
        min_raw = getattr(mult, "Min", None)
        max_raw = getattr(mult, "Max", None)
        if min_raw is not None:
            try:
                schema["minItems"] = int(min_raw)
            except (TypeError, ValueError):
                pass
        if max_raw is not None and max_raw != "*":
            try:
                schema["maxItems"] = int(max_raw)
            except (TypeError, ValueError):
                pass

    ordered = getattr(multi_value, "Ordered", None)
    if isinstance(ordered, bool):
        schema["x-ordered"] = ordered
    return schema


def _attribute_schema(
    resolved: ResolvedAttribute,
    ref_keys: dict[int, str],
    symbol_table: SymbolTable | None = None,
) -> dict[str, Any]:
    """Return the JSON Schema for one attribute; an unmapped type gets an `x-unsupported` marker.

    Embedded association roles are transferred as OIDs, like a plain `REFERENCE TO`.
    """
    if resolved.type_kind == "MultiValue" and resolved.type_instance is not None:
        schema = _multi_value_schema(resolved.type_instance, ref_keys, symbol_table)
    elif resolved.type_kind == "Class" and is_structure(resolved.type_instance):
        wrapped = wrapped_reference(resolved.type_instance)
        schema = (
            _reference_type_schema(wrapped)
            if wrapped is not None
            else _class_ref_or_marker(resolved.type_instance, ref_keys, symbol_table)
        )
    elif resolved.type_kind in ("Class", "ReferenceType") and resolved.type_instance is not None:
        schema = _reference_type_schema(resolved)
    else:
        schema = _element_schema(resolved.type_kind, resolved.type_instance, ref_keys, symbol_table)
    meta = _meta_marker(resolved.attr)
    if meta:
        schema["x-meta"] = meta
    description = _description(resolved.attr)
    if description:
        schema["description"] = description
    return schema


def _discover_classes(roots: list[MetaInstance], symbol_table: SymbolTable | None = None) -> dict[int, MetaInstance]:
    """BFS over every Class reachable from `roots` via structure/BAG/LIST attributes, deduplicated by identity.

    Finds classes outside `roots` (e.g. imported structures); the dedup makes recursive structures terminate. With a
    `symbol_table`, an ABSTRACT structure also enqueues its concrete subclasses so each `anyOf` branch has an entry.
    """
    found: dict[int, MetaInstance] = {}
    queue: list[MetaInstance] = list(roots)
    while queue:
        cls = queue.pop(0)
        if id(cls) in found:
            continue
        found[id(cls)] = cls
        if (
            symbol_table is not None
            and getattr(cls, "Kind", None) == "Structure"
            and bool(getattr(cls, "Abstract", False))
        ):
            for concrete in _concrete_subclasses(cls, symbol_table):
                if id(concrete) not in found:
                    queue.append(concrete)
        feature = getattr(cls, "Kind", None) != "Structure"
        for attr in attributes_of(cls).values():
            resolved = resolve_attribute(attr)
            if feature and geometry_format(resolved.type_kind, resolved.type_instance, feature_level=True):
                continue
            nested = nested_class(resolved)
            if nested is not None and id(nested) not in found:
                queue.append(nested)
    return found


def _assign_keys(classes_by_id: dict[int, MetaInstance]) -> dict[int, str]:
    """Assign each discovered class a unique `$defs` key from its own `Name`."""
    keys: dict[int, str] = {}
    used: set[str] = set()
    for instance_id, cls in classes_by_id.items():
        base_name = getattr(cls, "Name", None) or "Unnamed"
        key = base_name
        suffix = 2
        while key in used:
            key = f"{base_name}_{suffix}"
            suffix += 1
        used.add(key)
        keys[instance_id] = key
    return keys


def _geometry_inspection_schemas(
    view: MetaInstance, ref_keys: dict[int, str], symbol_table: SymbolTable | None
) -> dict[str, dict[str, Any]]:
    """Property schemas of an `INSPECTION OF` a SURFACE/AREA/POLYLINE view, by view attribute name.

    Values are derived (boundary/line -> geometry, segment end point -> `Point`); a `PARENT`/`THISAREA`/`THATAREA`
    read keeps the owning attribute's schema.
    """
    bases = [b for b in getattr(view, "RenamedBaseView", None) or [] if isinstance(b.BaseView, MetaInstance)]
    if getattr(view, "FormationKind", None) != "Inspection" or len(bases) != 1:
        return {}
    base_view = bases[0].BaseView
    insp = geometry_inspection(view, base_view, symbol_table)
    if insp is None or insp.problem is not None:
        return {}
    members = schema_members_of(base_view, symbol_table) if symbol_table is not None else attributes_of(base_view)
    alias = getattr(bases[0], "Name", None) or getattr(base_view, "Name", None) or ""
    schemas: dict[str, dict[str, Any]] = {}
    for attr in getattr(view, "ClassAttribute", None) or []:
        derivates = getattr(attr, "Derivates", None) or []
        reading, _why = inspection_reading(derivates[0] if derivates else None, insp, alias)
        if reading is None or not getattr(attr, "Name", None):
            continue
        if reading.what == "geometry":
            schemas[attr.Name] = {"format": "geometry-any"}
        elif reading.what in ("endpoint", "arcpoint"):
            schemas[attr.Name] = {"format": "geometry-point"}
        elif reading.field in members:
            schemas[attr.Name] = _attribute_schema(resolve_attribute(members[reading.field]), ref_keys, symbol_table)
    return schemas


def _is_untyped_count_column(attr: MetaInstance) -> bool:
    """True for an aggregation-view attribute `Name := INTERLIS.objectCount(AGGREGATES)` declared without a type."""
    derivates = getattr(attr, "Derivates", None) or []
    return bool(derivates) and is_standard_count_call(derivates[0])


ROLE_KEY = "x-ogc-role"
PRIMARY_GEOMETRY = "primary-geometry"


def is_spatial_schema(prop: Any) -> bool:
    """Whether a property schema is an OGC API Schemas spatial property (`format: geometry-*`)."""
    fmt = prop.get("format") if isinstance(prop, dict) else None
    return isinstance(fmt, str) and fmt.startswith("geometry-")


def class_to_json_schema(
    class_instance: MetaInstance,
    ref_keys: dict[int, str] | None = None,
    symbol_table: SymbolTable | None = None,
) -> dict[str, Any]:
    """Convert one IlisMeta16 Class or Structure into a JSON Schema object.

    Nested classes resolve through `ref_keys` (instance id -> `$defs` key); without it they become `x-unsupported`.
    """
    ref_keys = ref_keys or {}
    properties: dict[str, Any] = {}
    required: list[str] = []
    members = (
        schema_members_of(class_instance, symbol_table) if symbol_table is not None else attributes_of(class_instance)
    )
    derived = _geometry_inspection_schemas(class_instance, ref_keys, symbol_table)
    is_feature = getattr(class_instance, "Kind", None) != "Structure"
    for name, attr in members.items():
        resolved = resolve_attribute(attr)
        if name in derived:
            properties[name] = derived[name]
        elif resolved.type_kind is None and _is_untyped_count_column(attr):
            properties[name] = {"type": "integer", "minimum": 0}
        else:
            properties[name] = _attribute_schema(resolved, ref_keys, symbol_table)
            fmt = geometry_format(resolved.type_kind, resolved.type_instance, feature_level=is_feature)
            if fmt and "format" not in properties[name]:
                properties[name] = {key: value for key, value in properties[name].items() if key == "x-meta"}
                properties[name]["format"] = fmt
        if resolved.mandatory:
            required.append(name)
    if is_feature:
        direct = {name: resolve_attribute(attr) for name, attr in members.items() if name in properties}
        for name, role in temporal_roles(direct).items():
            properties[name][ROLE_KEY] = role
    spatial = [name for name, prop in properties.items() if is_spatial_schema(prop)]
    if len(spatial) == 1:
        properties[spatial[0]][ROLE_KEY] = PRIMARY_GEOMETRY
    if is_polymorphic_structure(class_instance, symbol_table) and not getattr(class_instance, "Abstract", False):
        properties = {TYPE_KEY: {"const": structure_discriminant(class_instance, symbol_table)}, **properties}
        required.append(TYPE_KEY)
    schema: dict[str, Any] = {"type": "object", "properties": properties, "additionalProperties": False}
    class_name = getattr(class_instance, "Name", None)
    if class_name:
        schema["title"] = class_name
    if required:
        schema["required"] = sorted(required)
    description = _description(class_instance)
    if description:
        schema["description"] = description
    interlis = _interlis_marker(class_instance, symbol_table)
    derivation = _derivation(class_instance, symbol_table)
    if derivation:
        interlis["derivation"] = derivation
    outside = frozenset(name for name, prop in properties.items() if is_spatial_schema(prop))
    fragments, catalogue = class_constraints(class_instance, outside)
    if fragments:
        schema["allOf"] = fragments
    if catalogue:
        interlis["constraints"] = catalogue
    if interlis:
        schema["x-interlis"] = interlis
    class_meta = _meta_marker(class_instance)
    if class_meta:
        schema["x-meta"] = class_meta
    crud = _crud_operations(class_instance)
    if crud is not None:
        schema["x-crud"] = list(crud)
    return schema


def collection_schema(schema: dict[str, Any], key: str, *, schema_id: str | None = None) -> dict[str, Any]:
    """Return one `$defs` entry as a standalone schema, as served at `/collections/{id}/schema`.

    Structures are inlined and reference properties get `x-ogc-role`. Raises `KeyError` for an unknown class.
    """
    defs = schema.get("$defs", {})
    if key not in defs:
        raise KeyError(key)

    def inline(node: Any, depth: int) -> Any:
        if isinstance(node, list):
            return [inline(item, depth) for item in node]
        if not isinstance(node, dict):
            return node
        ref = node.get("$ref")
        if isinstance(ref, str) and ref.startswith("#/$defs/") and depth < _MAX_INLINE_DEPTH:
            return inline(defs[ref.removeprefix("#/$defs/")], depth + 1)
        out = {name: inline(value, depth) for name, value in node.items()}
        if "x-reference-target" in out and "x-ogc-role" not in out:
            out["x-ogc-role"] = "reference"
            out["x-ogc-collectionId"] = out["x-reference-target"]
        return out

    result: dict[str, Any] = {"$schema": schema.get("$schema", JSON_SCHEMA_DRAFT)}
    if schema_id:
        result["$id"] = schema_id
    result.update(inline(defs[key], 0))
    return result


def property_validator(schema: dict[str, Any], key: str, *, closed: bool = False) -> jsonschema.Draft202012Validator:
    """A validator of one feature's `properties` against `$defs[key]`; its spatial properties are not required.

    `closed=False` ignores `additionalProperties` and the constraint fragments: `validate-jsonfg` reports an unknown
    property as a warning and evaluates the constraints itself.

    Geometry travels outside `properties` in JSON-FG (`place`), so the `format: geometry-*` properties
    are left to a geometry check.
    """
    open_keys = ("additionalProperties", "allOf")  # `validate-jsonfg` evaluates the constraints itself
    defs = {
        k: d if closed else {n: v for n, v in d.items() if n not in open_keys}
        for k, d in schema.get("$defs", {}).items()
    }
    entry = dict(defs[key])
    spatial = {name for name, prop in entry.get("properties", {}).items() if is_spatial_schema(prop)}
    if spatial and "required" in entry:
        entry["required"] = [name for name in entry["required"] if name not in spatial]
    defs[key] = entry
    root = {"$schema": schema.get("$schema", JSON_SCHEMA_DRAFT), "$defs": defs, "$ref": f"#/$defs/{key}"}
    return jsonschema.Draft202012Validator(root)


def validate_feature_properties(properties: dict[str, Any], schema: dict[str, Any], key: str) -> list[str]:
    """Validate a candidate PUT/PATCH `properties` payload against `$defs[key]` of a `model_to_json_schema` document.

    `schema` must be the full document so the entry's `$ref`s resolve. Returns every violation message (empty list
    = valid); whether the write is allowed at all (`x-crud`) is not checked here.
    """
    return [error.message for error in property_validator(schema, key, closed=True).iter_errors(properties)]


def inherited_topic_classes(symbol_table: SymbolTable) -> Iterator[tuple[MetaInstance, MetaInstance]]:
    """`(topic, class)` pairs: the classes a registered topic inherits through `TOPIC ... EXTENDS` from its base topics.

    An extending topic inherits the classes of its base topic, so its objects can be typed by classes the
    extending model never declares itself.
    """
    seen: set[int] = set()
    for topic in symbol_table.all_registered():
        if not isinstance(topic, MetaInstance) or not topic._qualified_class.endswith("ModelData.SubModel"):
            continue
        data_unit = getattr(topic, "_twin", None)
        while data_unit is not None and id(data_unit) not in seen:
            seen.add(id(data_unit))
            data_unit = getattr(data_unit, "Super", None)
            base = getattr(data_unit, "_twin", None)
            for element in getattr(base, "Element", None) or []:
                if isinstance(element, MetaInstance) and element._qualified_class.endswith("ModelData.Class"):
                    yield topic, element


def topic_inherited_classes(symbol_table: SymbolTable) -> list[MetaInstance]:
    """The classes of `inherited_topic_classes`, without their topic."""
    return [element for _topic, element in inherited_topic_classes(symbol_table)]


SUPPORTED_VIEW_FORMATION_KINDS = ("Projection", "Join", "Union", "Aggregation", "Inspection")


def convertible_roots(symbol_table: SymbolTable) -> list[MetaInstance]:
    """The classes and supported VIEWs of a built model that `model_to_json_schema` takes as its roots."""
    instances = [i for i in symbol_table.all_registered() if isinstance(i, MetaInstance)]
    kind = lambda i: i._qualified_class.rsplit(".", 1)[-1]  # noqa: E731
    classes = [i for i in instances if kind(i) == "Class"] + topic_inherited_classes(symbol_table)
    views = [
        i
        for i in instances
        if kind(i) == "View" and getattr(i, "FormationKind", None) in SUPPORTED_VIEW_FORMATION_KINDS
    ]
    return classes + views


def class_schema_keys(classes: list[MetaInstance], symbol_table: SymbolTable | None = None) -> dict[int, str]:
    """The `$defs` key (by instance id) of every class `model_to_json_schema(classes)` emits."""
    return _assign_keys(_discover_classes(classes, symbol_table))


def model_to_json_schema(
    classes: list[MetaInstance],
    symbol_table: SymbolTable | None = None,
    model: MetaInstance | None = None,
) -> dict[str, Any]:
    """Convert every Class/Structure reachable from `classes` into one JSON Schema document.

    Each class becomes a `$defs` entry keyed by its short name (collisions get a numeric suffix).
    """
    reachable = _discover_classes(classes, symbol_table)
    ref_keys = _assign_keys(reachable)
    defs = {
        ref_keys[instance_id]: class_to_json_schema(cls, ref_keys, symbol_table)
        for instance_id, cls in reachable.items()
    }
    document: dict[str, Any] = {"$schema": JSON_SCHEMA_DRAFT}
    model_meta = _meta_marker(model) if model is not None else {}
    if model_meta:
        document["x-meta"] = model_meta
    document["$defs"] = defs
    return document


def collect_diagnostics(document: dict, *, file: str | None = None):
    """Walk a built JSON Schema for `x-unsupported` markers and emit a `Diagnostic` per marker.

    The marker stays in the document; this is the machine-readable counterpart feeding SARIF output and the exit
    code. `"Class"` means an unreachable nested structure, any other value an unmapped type.
    """
    from interlis.diagnostics import Diagnostic, Location

    out: list[Diagnostic] = []

    def _walk(node, path: str) -> None:
        if isinstance(node, dict):
            marker = node.get("x-unsupported")
            if marker is not None:
                if marker == "Class":
                    rule, sev = "JSONSCHEMA-CLASS-UNRESOLVED", "warning"
                    msg = f"{path or '<root>'}: a nested-structure Class is not reachable from the given roots"
                    hlp = "pass its model's directory to --repo"
                else:
                    rule, sev = "JSONSCHEMA-TYPE-UNSUPPORTED", "note"
                    msg = f"{path or '<root>'}: attribute type {marker!r} is outside the mapped set (x-unsupported)"
                    hlp = None
                out.append(Diagnostic(sev, rule, msg, Location(file=file, element_path=path or None), help=hlp))
            for key, value in node.items():
                if key.startswith("x-"):
                    continue
                _walk(value, f"{path}.{key}" if path else str(key))
        elif isinstance(node, list):
            for i, item in enumerate(node):
                _walk(item, f"{path}[{i}]")

    _walk(document, "")
    return out
