"""Validation of a JSON-FG document against its INTERLIS model, in three layers.

1. `[properties]`: each feature's `properties` against the JSON Schema `interlis convert` writes for its class,
   geometry left out (it travels in `place`).
2. `[geometry]`: the geometry against the model - presence, JSON-FG type, dimension, `coordRefSys` and
   coordinate domain - read from the built model, not from the schema.
3. `[constraints]` / `[references]`: what a JSON Schema cannot say - the model's constraints, evaluated by the
   XTF validator's checks on objects rebuilt from the features, and the targets of reference attributes.

A JSON-FG document carries neither baskets nor qualified class names: each topic's features form one synthetic
basket, and a `featureType` shared by several classes is accepted when the feature conforms to any of them.
"""

from dataclasses import dataclass, field, replace
from typing import Any

from interlis.builder.forward_refs import SymbolTable
from interlis.builder.repository import ModelRepository
from interlis.convert.geometry_kinds import geometry_types
from interlis.convert.jsonfg import _context_concrete_domain, _crs_uri, geometry_coord_type
from interlis.convert.jsonschema import (
    class_schema_keys,
    convertible_roots,
    inherited_topic_classes,
    model_to_json_schema,
    property_validator,
)
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.constraints import check_transfer_constraints
from interlis.xtf.parse import RawNode, XtfBasket, XtfObject, XtfTransfer
from interlis.xtf.schema import (
    ResolvedAttribute,
    coord_axes,
    reference_external_status,
    resolve_attribute,
    schema_members_of,
)
from interlis.xtf.validate import ValidationIssue, _build_tid_index, unresolved_reference_detail

_CURVE_TYPES_WITH_POSITIONS = ("CircularString",)


@dataclass
class _GeometryAttr:
    name: str
    types: frozenset[str]
    mandatory: bool
    coord_type: MetaInstance | None


@dataclass
class _ClassInfo:
    qualified: str
    topic: str
    is_view: bool
    validator: Any
    members: dict[str, ResolvedAttribute]
    geometry: list[_GeometryAttr]
    references: dict[str, ResolvedAttribute] = field(default_factory=dict)


@dataclass
class _Checked:
    feature: dict[str, Any]
    info: _ClassInfo
    issues: list[ValidationIssue]
    placed: list[str]


def _is_tolerated(error: Any) -> bool:
    """A schema violation that `interlis validate` does not raise on the XTF: reported as a warning only.

    A decimal value in an integer domain, and an empty list where the model wants at least one element.
    """
    if error.validator == "type" and error.validator_value == "integer":
        return isinstance(error.instance, float)
    return error.validator == "minItems" and error.instance == []


def _leaf_positions(geometry: Any):
    """Every position (a list of numbers) of a GeoJSON/JSON-FG geometry, whatever its nesting."""
    if isinstance(geometry, dict):
        yield from _leaf_positions(geometry.get("coordinates"))
        for member in geometry.get("geometries") or []:
            yield from _leaf_positions(member)
    elif isinstance(geometry, list):
        if geometry and all(isinstance(item, (int, float)) and not isinstance(item, bool) for item in geometry):
            yield geometry
        else:
            for item in geometry:
                yield from _leaf_positions(item)


def _bound(axis: MetaInstance, name: str) -> float | None:
    raw = getattr(axis, name, None)
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def _node(tag: str, value: Any, *, reference: bool = False) -> RawNode:
    """One attribute value as the XTF element tree the constraint checks read."""
    if reference and isinstance(value, str):
        return RawNode(tag, None, {"REF": value}, [])
    if isinstance(value, dict):
        return RawNode(tag, None, {}, [_occurrence(tag, value)])
    if isinstance(value, list):
        return RawNode(tag, None, {}, [_occurrence(tag, v) if isinstance(v, dict) else _node(tag, v) for v in value])
    text = str(value).lower() if isinstance(value, bool) else str(value)
    return RawNode(tag, text, {}, [])


def _occurrence(tag: str, fields: dict[str, Any]) -> RawNode:
    return RawNode(tag, None, {}, [_node(name, value) for name, value in fields.items() if value is not None])


class _JsonFgValidator:
    def __init__(self, symbol_table: SymbolTable, repository: ModelRepository | None) -> None:
        self.symbol_table = symbol_table
        self.repository = repository
        roots = convertible_roots(symbol_table)
        self.schema = model_to_json_schema(roots, symbol_table=symbol_table)
        self.keys = class_schema_keys(roots, symbol_table)
        self.topic_of_inherited = {id(cls): topic for topic, cls in inherited_topic_classes(symbol_table)}
        self.by_name: dict[str, list[MetaInstance]] = {}
        for cls in roots:
            if getattr(cls, "Kind", None) != "Structure" and id(cls) in self.keys:
                self.by_name.setdefault(str(getattr(cls, "Name", None)), []).append(cls)
        self._infos: dict[int, _ClassInfo] = {}

    def _qualified(self, cls: MetaInstance) -> str:
        qualified = self.symbol_table.qualified_name_of(cls)
        if qualified is not None:
            return qualified
        topic = self.topic_of_inherited.get(id(cls))
        topic_name = self.symbol_table.qualified_name_of(topic) if topic is not None else None
        return f"{topic_name}.{cls.Name}" if topic_name else str(cls.Name)

    def info(self, cls: MetaInstance) -> _ClassInfo:
        cached = self._infos.get(id(cls))
        if cached is not None:
            return cached
        key = self.keys[id(cls)]
        qualified = self._qualified(cls)
        members = {name: resolve_attribute(attr) for name, attr in schema_members_of(cls, self.symbol_table).items()}
        geometry = [
            _GeometryAttr(name, types, bool(resolved.mandatory), geometry_coord_type(resolved))
            for name, resolved in members.items()
            if (types := geometry_types(resolved.type_kind, resolved.type_instance, feature_level=True))
        ]
        properties = self.schema["$defs"][key].get("properties", {})
        references = {
            name: members[name]
            for name, prop in properties.items()
            if name in members
            and ("x-reference-target" in prop or "x-reference-target" in (prop.get("items") or {}))
            and reference_external_status(members[name]) is not None
        }
        info = _ClassInfo(
            qualified=qualified,
            topic=qualified.rsplit(".", 1)[0],
            is_view=getattr(cls, "_qualified_class", "").endswith("View"),
            validator=property_validator(self.schema, key),
            members=members,
            geometry=geometry,
            references=references,
        )
        self._infos[id(cls)] = info
        return info

    def validate(
        self, document: Any, catalogs: list[XtfTransfer] | None, check_constraints: bool
    ) -> list[ValidationIssue]:
        features, collection = _features_of(document)
        if features is None:
            return [ValidationIssue("error", "", None, "", None, "[model] not a JSON-FG FeatureCollection or Feature")]
        issues: list[ValidationIssue] = []
        checked: list[_Checked] = []
        noted: set[str] = set()
        for feature in features:
            feature_type = feature.get("featureType") or collection.get("featureType")
            feature_type = str(feature_type)
            candidates = self.by_name.get(feature_type)
            fid = feature.get("id")
            if not candidates:
                issues.append(
                    ValidationIssue(
                        "error",
                        "",
                        fid,
                        str(feature_type),
                        None,
                        f"[model] featureType {feature_type!r} is not a class of the model",
                    )
                )
                continue
            if len(candidates) > 1 and feature_type not in noted:
                noted.add(feature_type)
                issues.append(
                    ValidationIssue(
                        "info",
                        "",
                        None,
                        feature_type,
                        None,
                        f"[model] featureType {feature_type!r} names {len(candidates)} classes of the model: "
                        "a feature is accepted when it conforms to one of them",
                    )
                )
            attempts = []
            for cls in candidates:
                info = self.info(cls)
                found, placed = self._check_feature(info, feature, collection)
                attempts.append(_Checked(feature, info, found, placed))
                if not any(i.severity == "error" for i in found):
                    break
            best = min(attempts, key=lambda a: sum(i.severity == "error" for i in a.issues))
            issues.extend(best.issues)
            checked.append(best)
        issues.extend(self._references(checked, catalogs))
        if check_constraints:
            issues.extend(self._constraints(checked, catalogs))
        return issues

    # -- layers 1 and 2 -------------------------------------------------------

    def _check_feature(
        self, info: _ClassInfo, feature: dict[str, Any], collection: dict[str, Any]
    ) -> tuple[list[ValidationIssue], list[str]]:
        """The issues of one feature against one class, and the geometry attributes the feature carries."""
        fid = feature.get("id")

        def issue(attribute: str | None, message: str, severity: str = "error") -> ValidationIssue:
            return ValidationIssue(severity, info.topic, fid, info.qualified, attribute, message)

        properties = feature.get("properties")
        if properties is None:
            properties = {}
        if not isinstance(properties, dict):
            return [issue(None, "[properties] 'properties' is not an object")], []
        found = [
            issue(
                ".".join(str(p) for p in error.absolute_path) or None,
                f"[properties] {error.message}",
                "warning" if _is_tolerated(error) else "error",
            )
            for error in info.validator.iter_errors(properties)
        ]
        found += [
            issue(name, f"[properties] {name!r} is not an attribute of the class", "warning")
            for name in properties
            if name not in info.members
        ]
        geometry_issues, placed = self._geometry(info, feature, collection, properties, issue)
        return found + geometry_issues, placed

    def _geometry(self, info, feature, collection, properties, issue) -> tuple[list[ValidationIssue], list[str]]:
        pool: list[tuple[str, dict[str, Any]]] = []
        place = feature.get("place")
        if isinstance(place, dict):
            members = place.get("geometries") if place.get("type") == "GeometryCollection" else [place]
            pool += [("place", g) for g in members or [] if isinstance(g, dict)]
        if isinstance(feature.get("geometry"), dict):
            pool.append(("geometry", feature["geometry"]))
        assigned: dict[str, tuple[str, dict[str, Any]]] = {
            attr.name: ("properties", properties[attr.name])
            for attr in info.geometry
            if isinstance(properties.get(attr.name), dict)
        }
        for attr in info.geometry:
            if attr.name in assigned:
                continue
            for position, (where, geometry) in enumerate(pool):
                if geometry.get("type") in attr.types:
                    assigned[attr.name] = (where, pool.pop(position)[1])
                    break
        found: list[ValidationIssue] = []
        unassigned = [attr for attr in info.geometry if attr.name not in assigned]
        if len(unassigned) == 1 and len(pool) == 1:
            attr, (_where, geometry) = unassigned[0], pool[0]
            found.append(
                issue(
                    attr.name,
                    f"[geometry] type {geometry.get('type')!r} is not allowed "
                    f"(expected: {', '.join(sorted(attr.types))})",
                )
            )
            unassigned, pool = [], []
        found += [
            issue(attr.name, f"[geometry] mandatory geometry attribute {attr.name!r} is missing")
            for attr in unassigned
            if attr.mandatory
        ]
        if pool and not info.is_view:
            expected = sorted({t for attr in info.geometry for t in attr.types})
            hint = f" (expected: {', '.join(expected)})" if expected else " (the class has no geometry attribute)"
            found += [
                issue(None, f"[geometry] type {g.get('type')!r} matches no geometry attribute{hint}") for _, g in pool
            ]
        crs_checked = False
        for attr in info.geometry:
            if attr.name not in assigned:
                continue
            where, geometry = assigned[attr.name]
            found += self._geometry_value(attr, geometry, issue)
            if where == "place" and not crs_checked:
                crs_checked = True
                found += self._crs(attr, feature, collection, issue)
        return found, list(assigned)

    def _geometry_value(self, attr: _GeometryAttr, geometry: dict[str, Any], issue) -> list[ValidationIssue]:
        found: list[ValidationIssue] = []
        concrete = _context_concrete_domain(attr.coord_type, self.symbol_table, self.repository) or attr.coord_type
        axes = coord_axes(concrete)
        positions = list(_leaf_positions(geometry))
        if axes and any(len(position) != len(axes) for position in positions):
            wrong = next(len(p) for p in positions if len(p) != len(axes))
            found.append(issue(attr.name, f"[geometry] a position has {wrong} coordinates, the domain has {len(axes)}"))
        elif axes:
            outside = [
                (index, value)
                for position in positions
                for index, value in enumerate(position)
                if (lo := _bound(axes[index], "Min")) is not None
                and (hi := _bound(axes[index], "Max")) is not None
                and not lo <= value <= hi
            ]
            if outside:
                index, value = outside[0]
                lo, hi = _bound(axes[index], "Min"), _bound(axes[index], "Max")
                found.append(
                    issue(
                        attr.name,
                        f"[geometry] coordinate {value} of axis {index + 1} is outside the domain [{lo}, {hi}] "
                        f"({len(outside)} such coordinate(s))",
                    )
                )
        if geometry.get("type") in _CURVE_TYPES_WITH_POSITIONS:
            count = len(geometry.get("coordinates") or [])
            if count < 3 or count % 2 == 0:
                found.append(
                    issue(attr.name, f"[geometry] a CircularString needs an odd number of positions >= 3, got {count}")
                )
        return found

    def _crs(self, attr: _GeometryAttr, feature, collection, issue) -> list[ValidationIssue]:
        expected = _crs_uri(attr.coord_type, symbol_table=self.symbol_table, repository=self.repository)
        actual = feature.get("coordRefSys") or collection.get("coordRefSys")
        if expected is None:
            return []
        if actual is None:
            return [issue(attr.name, f"[geometry] 'place' without coordRefSys (the model's is {expected})")]
        if actual != expected:
            return [issue(attr.name, f"[geometry] coordRefSys is {actual}, the model's is {expected}")]
        return []

    # -- layer 3 --------------------------------------------------------------

    def _transfer(self, checked: list[_Checked]) -> XtfTransfer:
        baskets: dict[str, XtfBasket] = {}
        for item in checked:
            info = item.info
            properties = item.feature.get("properties")
            if not isinstance(properties, dict):
                continue
            attributes = {
                name: [_node(name, value, reference=name in info.references)]
                for name, value in properties.items()
                if value is not None and name in info.members
            }
            attributes.update({name: [RawNode(name, None, {}, [])] for name in item.placed})  # defined, value not read
            basket = baskets.setdefault(info.topic, XtfBasket(info.topic, info.topic, None, None))
            basket.objects.append(XtfObject(item.feature.get("id"), info.qualified, attributes))
        return XtfTransfer(None, None, [], list(baskets.values()))

    def _references(self, checked: list[_Checked], catalogs: list[XtfTransfer] | None) -> list[ValidationIssue]:
        known = _build_tid_index(self._transfer(checked), catalogs)
        found: list[ValidationIssue] = []
        for item in checked:
            properties = item.feature.get("properties")
            if not isinstance(properties, dict):
                continue
            for name, resolved in item.info.references.items():
                value = properties.get(name)
                for target in value if isinstance(value, list) else [value]:
                    if isinstance(target, str) and target not in known:
                        detail = unresolved_reference_detail(reference_external_status(resolved))
                        found.append(
                            ValidationIssue(
                                "warning",
                                item.info.topic,
                                item.feature.get("id"),
                                item.info.qualified,
                                name,
                                f"[references] REF {target!r} not found in this document ({detail})",
                            )
                        )
        return found

    def _constraints(self, checked: list[_Checked], catalogs: list[XtfTransfer] | None) -> list[ValidationIssue]:
        found = check_transfer_constraints(
            self._transfer(checked), symbol_table=self.symbol_table, repository=self.repository, catalogs=catalogs
        )
        return [replace(issue, message=f"[constraints] {issue.message}") for issue in found]


def _features_of(document: Any) -> tuple[list[dict[str, Any]] | None, dict[str, Any]]:
    """The features of a FeatureCollection (or the single Feature) and the document's collection-level members."""
    if not isinstance(document, dict):
        return None, {}
    if document.get("type") == "Feature":
        return [document], {}
    features = document.get("features")
    if document.get("type") != "FeatureCollection" or not isinstance(features, list):
        return None, {}
    return [f for f in features if isinstance(f, dict)], document


def validate_jsonfg(
    document: Any,
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
    catalogs: list[XtfTransfer] | None = None,
    check_constraints: bool = True,
) -> list[ValidationIssue]:
    """Validate a parsed JSON-FG document against the built model in `symbol_table`; see the module docstring."""
    return _JsonFgValidator(symbol_table, repository).validate(document, catalogs, check_constraints)
