"""XTF validator: checks a parsed `XtfTransfer` against the schema built from the INTERLIS model.

Covers structure, MANDATORY, base types, references, geometry and nested content. Not covered: attributes
inherited from an unloaded imported model; a custom LINE FORM segment is only flagged as `info`.
"""

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

from interlis.builder.forward_refs import SymbolTable
from interlis.builder.repository import ModelRepository
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.parse import RawNode, XtfBasket, XtfObject, XtfTransfer, iter_objects
from interlis.xtf.schema import (
    ResolvedAttribute,
    coord_axes,
    enum_values,
    home_symbol_table,
    is_class_compatible,
    line_coord_type,
    reference_external_status,
    reference_target_classes,
    resolve_attribute,
    resolve_class,
    restriction_candidates,
    schema_members_of,
    single_own_attribute,
)

# Type classes whose values are references to an object: ReferenceType, and a Class (structure or class)
# reached directly.
_REFERENCE_TYPE_KINDS = {"ReferenceType", "Class"}


@dataclass
class ValidationIssue:
    severity: str  # "error" | "warning" | "info"
    basket_bid: str
    object_tid: str | None
    qualified_class: str
    attribute: str | None
    message: str

    def to_diagnostic(self, *, file: str | None = None):
        """Return this finding as a shared `Diagnostic` (rule `XTF-VALIDATION`; `info` maps to SARIF `note`)."""
        from interlis.diagnostics import Diagnostic, Location

        severity = "note" if self.severity == "info" else self.severity
        return Diagnostic(
            severity,
            "XTF-VALIDATION",
            self.message,
            Location(file=file, model=self.qualified_class, element_path=self.attribute, tid=self.object_tid),
        )


def extract_reference(node: RawNode) -> str | None:
    """Find a reference attribute's target TID/OID in one of three forms.

    An `ili:ref` attribute on the node, an uppercase `REF` attribute on a descendant, or `REF` on the node itself.
    """
    for key, val in node.attrib.items():
        if key.rsplit("}", 1)[-1] == "ref":
            return val
    for child in node.children:
        found = extract_reference(child)
        if found is not None:
            return found
    if "REF" in node.attrib:
        return node.attrib["REF"]
    return None


def _decimal_places(raw: str) -> int:
    """Return the number of digits after the decimal point in `raw` (0 if none)."""
    return len(raw.split(".", 1)[1]) if "." in raw else 0


def _numeric_range_tolerance(min_raw, max_raw) -> float:
    """Return the rounding tolerance for a numeric range: half a unit of the smallest decimal.

    Senders may round a value to the domain's precision in either direction, so both bounds get it.
    """
    decimals = 0
    for raw in (min_raw, max_raw):
        if raw is not None:
            decimals = max(decimals, _decimal_places(str(raw)))
    return 0.5 * (10**-decimals)


def _validate_scalar(resolved: ResolvedAttribute, node: RawNode, ctx: str) -> list[str]:
    """Run base-type checks on ONE present attribute node; return error messages (empty if compliant)."""
    problems: list[str] = []
    kind = resolved.type_kind
    if kind == "NumType":
        text = node.text
        if text is None:
            return problems
        try:
            numeric_value = float(text)
        except ValueError:
            return [f"{ctx}: value {text!r} is not numeric (Type=NumType)"]
        min_raw = getattr(resolved.type_instance, "Min", None)
        max_raw = getattr(resolved.type_instance, "Max", None)
        try:
            tolerance = _numeric_range_tolerance(min_raw, max_raw)
            if min_raw is not None and numeric_value < float(min_raw) - tolerance:
                problems.append(f"{ctx}: value {text!r} < Min {min_raw!r}")
            if max_raw is not None and numeric_value > float(max_raw) + tolerance:
                problems.append(f"{ctx}: value {text!r} > Max {max_raw!r}")
        except ValueError:
            pass  # Non-numeric Min/Max (e.g. a predefined domain) - out of scope here
    elif kind == "EnumType" and resolved.type_instance is not None:
        text = node.text
        if text is None:
            return problems
        allowed = enum_values(resolved.type_instance)
        # "OTHERS" is always a valid enumeration value.
        if allowed and text != "OTHERS" and text not in allowed:
            problems.append(f"{ctx}: value {text!r} not in the enumeration ({sorted(allowed)!r})")
    elif kind == "TextType":
        if node.text is None and not node.children and not node.blank:
            problems.append(f"{ctx}: TEXT attribute present but empty")
        max_length = getattr(resolved.type_instance, "MaxLength", None)
        if node.text is not None and max_length is not None and len(node.text) > int(max_length):
            problems.append(f"{ctx}: text of {len(node.text)} characters exceeds TEXT*{max_length}")
    return problems


# Geometry is encoded as `<COORD>`, `<POLYLINE>`, `<SURFACE>` (UPPERCASE, no namespace) in XTF 2.3 and as
# `geom:`-namespaced lowercase tags in XTF 2.4; AREA uses the SURFACE encoding. Tags are compared case-insensitively.


def geom_tag(node: RawNode) -> str:
    """Normalize a geometry tag for comparison (XTF 2.3 is UPPERCASE, XTF 2.4 lowercase)."""
    return node.tag.upper()


def find_child(node: RawNode, tag: str) -> RawNode | None:
    """Case-insensitive lookup by a fixed geometry keyword - `tag` is UPPERCASE (see `geom_tag`)."""
    return next((c for c in node.children if c.tag.upper() == tag), None)


def _numeric_problems(text: str | None, min_raw, max_raw, ctx: str) -> list[str]:
    """Run the NumType checks of `_validate_scalar`, reusable on coordinate components.

    `min_raw`/`max_raw` are `None` when the bound is unknown (parseability check only).
    """
    if text is None:
        return [f"{ctx}: missing component"]
    try:
        value = float(text)
    except ValueError:
        return [f"{ctx}: value {text!r} is not numeric"]
    problems: list[str] = []
    try:
        tolerance = _numeric_range_tolerance(min_raw, max_raw)
        if min_raw is not None and value < float(min_raw) - tolerance:
            problems.append(f"{ctx}: value {text!r} < Min {min_raw!r}")
        if max_raw is not None and value > float(max_raw) + tolerance:
            problems.append(f"{ctx}: value {text!r} > Max {max_raw!r}")
    except ValueError:
        pass
    return problems


def _axis_components(node: RawNode, prefix: str) -> list[RawNode]:
    """Return `{prefix}1`, `{prefix}2`, ... components present on `node`, in order up to the first gap."""
    out: list[RawNode] = []
    i = 1
    while True:
        child = find_child(node, f"{prefix}{i}")
        if child is None:
            break
        out.append(child)
        i += 1
    return out


def _validate_axis_values(components: list[RawNode], axes: list[MetaInstance], ctx: str, *, label: str) -> list[str]:
    """Check each component against its axis by position.

    The range is checked only when `axes` is known, as is a component-count mismatch.
    """
    problems: list[str] = []
    if axes and len(components) != len(axes):
        problems.append(f"{ctx}: {len(components)} {label} component(s), {len(axes)} expected (CoordType.Axis)")
    for i, comp in enumerate(components):
        axis = axes[i] if i < len(axes) else None
        problems.extend(
            _numeric_problems(
                comp.text,
                getattr(axis, "Min", None),
                getattr(axis, "Max", None),
                f"{ctx}.{label}{i + 1}",
            )
        )
    return problems


def _validate_coord_node(node: RawNode, axes: list[MetaInstance], ctx: str) -> list[str]:
    if geom_tag(node) != "COORD":
        return [f"{ctx}: expected COORD geometry, found tag {node.tag!r}"]
    return _validate_axis_values(_axis_components(node, "C"), axes, ctx, label="C")


def _validate_arc_node(node: RawNode, axes: list[MetaInstance], ctx: str) -> list[str]:
    """Validate an ArcSegment: point A1/A2 against the first 2 axes, radius R for parseability only."""
    if geom_tag(node) != "ARC":
        return [f"{ctx}: expected ARC geometry, found tag {node.tag!r}"]
    problems = _validate_axis_values(_axis_components(node, "C"), axes, ctx, label="C")
    mid = _axis_components(node, "A")
    if len(mid) < 2:
        problems.append(f"{ctx}: missing intermediate point A1/A2 (ARC)")
    else:
        problems.extend(_validate_axis_values(mid, axes[:2], ctx, label="A"))
    r_node = find_child(node, "R")
    if r_node is not None:
        problems.extend(_numeric_problems(r_node.text, None, None, f"{ctx}.R"))
    return problems


def _validate_polyline_node(node: RawNode, axes: list[MetaInstance], ctx: str) -> list[str]:
    if geom_tag(node) != "POLYLINE":
        return [f"{ctx}: expected POLYLINE geometry, found tag {node.tag!r}"]
    if not node.children:
        return [f"{ctx}: empty POLYLINE (no segment)"]
    problems: list[str] = []
    for i, seg in enumerate(node.children):
        seg_ctx = f"{ctx}[{i}]"
        seg_tag = geom_tag(seg)
        if seg_tag == "COORD":
            problems.extend(_validate_coord_node(seg, axes, seg_ctx))
        elif seg_tag == "ARC":
            problems.extend(_validate_arc_node(seg, axes, seg_ctx))
        # else: a custom LINE FORM segment is not interpreted nor flagged here; `_line_form_infos` reports it as `info`.
    return problems


# XTF 2.3 wraps each ring in <BOUNDARY> (first ring is the outer one); XTF 2.4 tags rings
# <exterior>/<interior>. Either way the ring holds one POLYLINE.
BOUNDARY_TAGS = frozenset({"BOUNDARY", "EXTERIOR", "INTERIOR"})


def _validate_boundary_node(node: RawNode, axes: list[MetaInstance], ctx: str) -> list[str]:
    if geom_tag(node) not in BOUNDARY_TAGS:
        return [f"{ctx}: expected BOUNDARY geometry, found tag {node.tag!r}"]
    polylines = [c for c in node.children if geom_tag(c) == "POLYLINE"]
    if not polylines:
        return [f"{ctx}: BOUNDARY without POLYLINE"]
    problems: list[str] = []
    for i, polyline in enumerate(polylines):
        problems.extend(_validate_polyline_node(polyline, axes, f"{ctx}/POLYLINE" + (f"[{i}]" if i else "")))
    return problems


# SURFACE and AREA share one encoding, so both tags are accepted whichever Kind the attribute declares.
_SURFACE_TAGS = frozenset({"SURFACE", "AREA"})


def _validate_surface_node(node: RawNode, axes: list[MetaInstance], ctx: str) -> list[str]:
    """Validate a SURFACE/AREA node."""
    if geom_tag(node) not in _SURFACE_TAGS:
        return [f"{ctx}: expected SURFACE geometry, found tag {node.tag!r}"]
    boundaries = [c for c in node.children if geom_tag(c) in BOUNDARY_TAGS]
    if not boundaries:
        return [f"{ctx}: SURFACE without any BOUNDARY"]
    problems: list[str] = []
    for i, boundary in enumerate(boundaries):
        problems.extend(_validate_boundary_node(boundary, axes, f"{ctx}[{i}]"))
    return problems


def _validate_coord_attribute(resolved: ResolvedAttribute, node: RawNode, ctx: str) -> list[str]:
    """Validate an attribute of CoordType: its first child must be COORD (MULTICOORD if `CoordType.Multi`)."""
    coord_type = resolved.type_instance
    multi = bool(getattr(coord_type, "Multi", False))
    axes = coord_axes(coord_type)
    expected_tag = "MULTICOORD" if multi else "COORD"
    child = node.children[0] if node.children else None
    if child is None or geom_tag(child) != expected_tag:
        found = child.tag if child is not None else "(empty)"
        return [f"{ctx}: expected {expected_tag} geometry (Type=CoordType, Multi={multi}), found {found!r}"]
    if not multi:
        return _validate_coord_node(child, axes, ctx)
    coords = [c for c in child.children if geom_tag(c) == "COORD"]
    if not coords:
        return [f"{ctx}: MULTICOORD without any inner COORD"]
    problems: list[str] = []
    for i, c in enumerate(coords):
        problems.extend(_validate_coord_node(c, axes, f"{ctx}[{i}]"))
    return problems


# Each Kind maps to the set of tags it accepts: AREA is encoded as SURFACE, and `geom_tag` handles case.
_LINE_KIND_TAGS = {
    "Polyline": frozenset({"POLYLINE"}),
    "DirectedPolyline": frozenset({"POLYLINE"}),
    "Surface": _SURFACE_TAGS,
    "Area": _SURFACE_TAGS,
}
_LINE_KIND_MULTI_TAGS = {
    "Polyline": frozenset({"MULTIPOLYLINE"}),
    "DirectedPolyline": frozenset({"MULTIPOLYLINE"}),
    "Surface": frozenset({"MULTISURFACE", "MULTIAREA"}),
    "Area": frozenset({"MULTISURFACE", "MULTIAREA"}),
}


def _custom_line_form_tags(node: RawNode) -> set[str]:
    """Recursively collect tags of POLYLINE segments that are neither COORD nor ARC, at any depth."""
    tags: set[str] = set()
    if geom_tag(node) == "POLYLINE":
        tags.update(seg.tag for seg in node.children if geom_tag(seg) not in ("COORD", "ARC"))
    for child in node.children:
        tags |= _custom_line_form_tags(child)
    return tags


def _line_form_infos(node: RawNode, ctx: str) -> list[str]:
    """Report custom LINE FORM segments as `info`, so they never pass as fully validated geometry."""
    tags = _custom_line_form_tags(node)
    if not tags:
        return []
    return [
        f"{ctx}: custom LINE FORM segment(s) present ({', '.join(sorted(tags))}) - structure not validated by this tool"
    ]


def _validate_line_attribute(resolved: ResolvedAttribute, node: RawNode, ctx: str) -> tuple[list[str], list[str]]:
    """Validate an attribute of LineType (POLYLINE/SURFACE/AREA/MULTI*).

    `axes` comes from `schema.line_coord_type`; without a VERTEX clause only a numeric check is made.
    Returns `(errors, infos)`; `infos` flags custom LINE FORM segments.
    """
    line_type = resolved.type_instance
    kind = getattr(line_type, "Kind", None)
    multi = bool(getattr(line_type, "Multi", False))
    axes = coord_axes(line_coord_type(line_type))
    single_tags = _LINE_KIND_TAGS.get(kind)
    if single_tags is None:
        return [], []  # Unresolved/unexpected Kind - nothing reliable to check
    expected_tags = _LINE_KIND_MULTI_TAGS[kind] if multi else single_tags
    child = node.children[0] if node.children else None
    if child is None or geom_tag(child) not in expected_tags:
        found = child.tag if child is not None else "(empty)"
        label = "/".join(sorted(expected_tags))
        return [f"{ctx}: expected {label} geometry (LineType Kind={kind!r}, Multi={multi}), found {found!r}"], []
    infos = _line_form_infos(child, ctx)
    validator = _validate_polyline_node if single_tags == frozenset({"POLYLINE"}) else _validate_surface_node
    if not multi:
        return validator(child, axes, ctx), infos
    parts = [c for c in child.children if geom_tag(c) in single_tags]
    if not parts:
        label = "/".join(sorted(single_tags))
        return [f"{ctx}: {child.tag} without any inner {label}"], infos
    errors: list[str] = []
    for i, part in enumerate(parts):
        errors.extend(validator(part, axes, f"{ctx}[{i}]"))
    return errors, infos


_GENERIC_RESTRICTION_INFO = (
    "reference/structure-typed attribute with no recognized REF (bare text value, " "likely a 1-attribute structure)"
)


def _validate_restriction_text(
    resolved: ResolvedAttribute,
    node: RawNode | None,
    *,
    basket_bid: str,
    tid: str | None,
    qualified_class: str,
    path: str,
    ctx: str,
) -> "ValidationIssue | None":
    """Validate a `CLASS RESTRICTION(A; B; C)` value transferred as bare text.

    The text is tried against every candidate with a verifiable inner type: one match gives no issue, no verifiable
    candidate gives `info`, and verifiable candidates with no match give a `warning` (the choice is a heuristic).
    """
    if node is None:
        return ValidationIssue("info", basket_bid, tid, qualified_class, path, f"{ctx}: {_GENERIC_RESTRICTION_INFO}")
    single_attr_candidates = [
        (candidate, resolve_attribute(attr))
        for candidate in restriction_candidates(resolved)
        if (attr := single_own_attribute(candidate)) is not None
    ]
    if not single_attr_candidates:
        return ValidationIssue("info", basket_bid, tid, qualified_class, path, f"{ctx}: {_GENERIC_RESTRICTION_INFO}")
    checkable = [(c, ir) for c, ir in single_attr_candidates if ir.type_kind in ("TextType", "NumType", "EnumType")]
    if any(not _validate_scalar(inner, node, ctx) for _, inner in checkable):
        return None
    if len(checkable) < len(single_attr_candidates):
        return ValidationIssue(
            "info",
            basket_bid,
            tid,
            qualified_class,
            path,
            f"{ctx}: bare text value {node.text!r} (CLASS RESTRICTION, 3rd encoding form) matches "
            f"none of the {len(checkable)}/{len(single_attr_candidates)} verifiable candidate(s) - the "
            "rest are unresolved (external model not loaded via --repo)",
        )
    names = [getattr(c, "Name", None) for c, _ in checkable]
    return ValidationIssue(
        "warning",
        basket_bid,
        tid,
        qualified_class,
        path,
        f"{ctx}: bare text value {node.text!r} (CLASS RESTRICTION, 3rd encoding form) matches "
        f"none of the {len(checkable)} declared candidate(s) ({names!r})",
    )


def unresolved_reference_detail(status: bool | None) -> str:
    """Say whether a REF resolving to no object is likely bad data or a legitimate external reference."""
    if status is True:
        return (
            "declared reference (EXTERNAL): target object expected in an external "
            "basket/catalogue (DEPENDS ON), unresolved for lack of a loaded catalogue - normal"
        )
    if status is False:
        return (
            "NOT declared as EXTERNAL: should normally resolve in this same "
            "basket (eCH-0031 V2.1.0 §3.6.3) - a more likely sign of bad data"
        )
    return (
        "likely external/catalogue reference, or a broken one - EXTERNAL status "
        "undetermined by this validator (unrecognized structure)"
    )


def build_tid_index(transfer: XtfTransfer, catalogs: list[XtfTransfer] | None = None) -> dict[str, XtfObject]:
    """Map TID to `XtfObject` across all baskets of the transfer, then of each catalogue transfer; first TID wins."""
    index: dict[str, XtfObject] = {}
    for basket in transfer.baskets:
        for obj in basket.objects:
            if obj.tid is not None:
                index.setdefault(obj.tid, obj)
    for catalog in catalogs or []:
        for basket in catalog.baskets:
            for obj in basket.objects:
                if obj.tid is not None:
                    index.setdefault(obj.tid, obj)
    return index


def _resolved_schema_of(
    cls: MetaInstance,
    symbol_table: SymbolTable,
    cache: dict[int, dict[str, ResolvedAttribute]],
) -> dict[str, ResolvedAttribute]:
    """Return `schema_members_of` and `resolve_attribute` for a class, memoized for one `validate_transfer`.

    Keyed by `id(cls)` so distinct same-named classes never share an entry; the uncached scan was slow.
    """
    key = id(cls)
    cached = cache.get(key)
    if cached is None:
        cached = {name: resolve_attribute(attr) for name, attr in schema_members_of(cls, symbol_table).items()}
        cache[key] = cached
    return cached


def group_by_tag(nodes: list[RawNode]) -> dict[str, list[RawNode]]:
    """Group a flat list of `RawNode` by tag name, as `XtfObject.attributes` does (reused for nested content)."""
    grouped: dict[str, list[RawNode]] = {}
    for n in nodes:
        grouped.setdefault(n.tag, []).append(n)
    return grouped


def _validate_object(
    obj: XtfObject,
    basket: XtfBasket,
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None,
    tid_index: dict[str, XtfObject],
    schema_cache: dict[int, dict[str, ResolvedAttribute]],
) -> list[ValidationIssue]:
    cls = resolve_class(obj.qualified_class, symbol_table=symbol_table, repository=repository)
    if cls is None:
        return [
            ValidationIssue(
                "error",
                basket.bid,
                obj.tid,
                obj.qualified_class,
                None,
                "class absent from the resolved schema (model not loaded/findable via --repo, or wrong qualified name)",
            )
        ]

    # The table that actually declares `cls` can differ from the root table (class resolved via ModelRepository).
    cls_table = home_symbol_table(obj.qualified_class, symbol_table=symbol_table, repository=repository)
    return _validate_attrs(
        cls,
        obj.attributes,
        path_prefix="",
        basket_bid=basket.bid,
        tid=obj.tid,
        qualified_class=obj.qualified_class,
        home_table=cls_table,
        symbol_table=symbol_table,
        repository=repository,
        tid_index=tid_index,
        schema_cache=schema_cache,
    )


def _validate_attrs(
    cls: MetaInstance,
    attrs: dict[str, list[RawNode]],
    *,
    path_prefix: str,
    basket_bid: str,
    tid: str | None,
    qualified_class: str,
    home_table: SymbolTable,
    symbol_table: SymbolTable,
    repository: ModelRepository | None,
    tid_index: dict[str, XtfObject],
    schema_cache: dict[int, dict[str, ResolvedAttribute]],
) -> list[ValidationIssue]:
    """Validate every attribute in `attrs` against `cls`'s schema (MANDATORY, base type, reference, geometry, ...).

    Serves a root object (`path_prefix=""`) and a nested STRUCTURE occurrence alike.
    """
    issues: list[ValidationIssue] = []
    schema_attrs = _resolved_schema_of(cls, home_table, schema_cache)
    for attr_name, raw_nodes in attrs.items():
        path = f"{path_prefix}.{attr_name}" if path_prefix else attr_name
        if attr_name not in schema_attrs:
            issues.append(
                ValidationIssue(
                    "warning",
                    basket_bid,
                    tid,
                    qualified_class,
                    path,
                    "attribute absent from schema (class known) - unknown, or inherited via EXTENDS from an "
                    "imported model not loaded (not covered currently)",
                )
            )
            continue
        resolved = schema_attrs[attr_name]
        ctx = f"{qualified_class}[{tid}].{path}"
        issues.extend(
            _validate_resolved_attr(
                resolved,
                raw_nodes,
                path=path,
                ctx=ctx,
                basket_bid=basket_bid,
                tid=tid,
                qualified_class=qualified_class,
                home_table=home_table,
                symbol_table=symbol_table,
                repository=repository,
                tid_index=tid_index,
                schema_cache=schema_cache,
            )
        )

    for attr_name, resolved in schema_attrs.items():
        if resolved.mandatory and attr_name not in attrs:
            path = f"{path_prefix}.{attr_name}" if path_prefix else attr_name
            issues.append(
                ValidationIssue(
                    "error",
                    basket_bid,
                    tid,
                    qualified_class,
                    path,
                    f"{qualified_class}[{tid}].{path}: MANDATORY attribute {attr_name!r} absent",
                )
            )
    return issues


def _validate_resolved_attr(
    resolved: ResolvedAttribute,
    raw_nodes: list[RawNode],
    *,
    path: str,
    ctx: str,
    basket_bid: str,
    tid: str | None,
    qualified_class: str,
    home_table: SymbolTable,
    symbol_table: SymbolTable,
    repository: ModelRepository | None,
    tid_index: dict[str, XtfObject],
    schema_cache: dict[int, dict[str, ResolvedAttribute]],
    already_unwrapped: bool = False,
) -> list[ValidationIssue]:
    """Dispatch by `type_kind` for one resolved attribute, recursing into BAG/LIST and STRUCTURE content.

    `already_unwrapped=False`: `raw_nodes[0]` still has the structure wrapper as its only child, so unwrap one level.
    `True`: it already is the wrapper, and unwrapping again would report every MANDATORY attribute as missing.
    """
    issues: list[ValidationIssue] = []
    kind = resolved.type_kind

    if kind == "MultiValue":
        # A `(BAG|LIST) OF` structure is one element named after the attribute, holding each occurrence as a
        # child; each child is re-dispatched as a simple value of the wrapped type.
        base_type = getattr(resolved.type_instance, "BaseType", None)
        if not isinstance(base_type, MetaInstance):
            issues.append(
                ValidationIssue(
                    "info",
                    basket_bid,
                    tid,
                    qualified_class,
                    path,
                    f"{ctx}: unchecked 'MultiValue' type (BaseType unresolved - external model not loaded via --repo)",
                )
            )
            return issues
        base_kind = base_type._qualified_class.rsplit(".", 1)[-1]
        for node in raw_nodes:
            for i, occurrence in enumerate(node.children):
                occ_path = f"{path}[{i}]"
                occ_resolved = ResolvedAttribute(
                    attr=resolved.attr, type_instance=base_type, type_kind=base_kind, mandatory=False
                )
                issues.extend(
                    _validate_resolved_attr(
                        occ_resolved,
                        [occurrence],
                        path=occ_path,
                        ctx=f"{qualified_class}[{tid}].{occ_path}",
                        basket_bid=basket_bid,
                        tid=tid,
                        qualified_class=qualified_class,
                        home_table=home_table,
                        symbol_table=symbol_table,
                        repository=repository,
                        tid_index=tid_index,
                        schema_cache=schema_cache,
                        already_unwrapped=True,
                    )
                )
        return issues

    if kind in _REFERENCE_TYPE_KINDS:
        # A Type resolved to ReferenceType/Class is not always a REF in the XML: `CLASS RESTRICTION(...)` on 1-attribute
        # STRUCTUREs is transferred as that attribute's bare TEXT (see `_validate_restriction_text`).
        if kind == "Class" and getattr(resolved.type_instance, "Kind", None) == "Structure":
            # STRUCTURE-typed attribute: validate its content like a root object's. A REF in its subtree belongs to the
            # inner attribute and is checked against that one's declared target.
            for node in raw_nodes:
                wrapper = node if already_unwrapped else (node.children[0] if node.children else None)
                child_attrs = group_by_tag(wrapper.children) if wrapper is not None else {}
                issues.extend(
                    _validate_attrs(
                        resolved.type_instance,
                        child_attrs,
                        path_prefix=path,
                        basket_bid=basket_bid,
                        tid=tid,
                        qualified_class=qualified_class,
                        home_table=home_table,
                        symbol_table=symbol_table,
                        repository=repository,
                        tid_index=tid_index,
                        schema_cache=schema_cache,
                    )
                )
            return issues
        ref = extract_reference(raw_nodes[0]) if raw_nodes else None
        if ref is None:
            restriction_issue = _validate_restriction_text(
                resolved,
                raw_nodes[0] if raw_nodes else None,
                basket_bid=basket_bid,
                tid=tid,
                qualified_class=qualified_class,
                path=path,
                ctx=ctx,
            )
            if restriction_issue is not None:
                issues.append(restriction_issue)
        elif ref not in tid_index:
            # REF found but no object has that TID: always a `warning`, since without a catalogue an external
            # reference cannot be told from a broken one; only the message depends on the external status.
            detail = unresolved_reference_detail(reference_external_status(resolved))
            issues.append(
                ValidationIssue(
                    "warning",
                    basket_bid,
                    tid,
                    qualified_class,
                    path,
                    f"{ctx}: REF {ref!r} not found in this transfer ({detail})",
                )
            )
        else:
            # REF resolved: check the target's class against the declared one. Silent when either class is uncertain or
            # they are compatible, to avoid false positives across models.
            alternatives = reference_target_classes(resolved)
            declared = alternatives[0] if alternatives else None
            if declared is not None:
                target_obj = tid_index[ref]
                actual_cls = resolve_class(target_obj.qualified_class, symbol_table=symbol_table, repository=repository)
                if actual_cls is not None and not any(is_class_compatible(actual_cls, alt) for alt in alternatives):
                    issues.append(
                        ValidationIssue(
                            "error",
                            basket_bid,
                            tid,
                            qualified_class,
                            path,
                            f"{ctx}: REF {ref!r} resolved to {target_obj.qualified_class!r}, incompatible "
                            f"with declared class {getattr(declared, 'Name', '?')!r} "
                            "(neither identical nor a subclass via EXTENDS)",
                        )
                    )
        return issues

    if kind == "CoordType":
        # `error`: once the Type resolves to CoordType, Multi/Axis are always known, as for MANDATORY/NUMERIC/ENUM.
        for node in raw_nodes:
            for problem in _validate_coord_attribute(resolved, node, ctx):
                issues.append(ValidationIssue("error", basket_bid, tid, qualified_class, path, problem))
        return issues

    if kind == "LineType":
        # `error` for the same reason as CoordType; an unresolved VERTEX only degrades to a parseability-only check.
        # A custom LINE FORM segment is reported separately as `info`.
        for node in raw_nodes:
            errors, infos = _validate_line_attribute(resolved, node, ctx)
            for problem in errors:
                issues.append(ValidationIssue("error", basket_bid, tid, qualified_class, path, problem))
            for note in infos:
                issues.append(ValidationIssue("info", basket_bid, tid, qualified_class, path, note))
        return issues

    if kind not in ("TextType", "NumType", "EnumType"):
        # Any other Type (FormattedType, BooleanType, ... or unresolved) is made visible as `info` rather than silently
        # ignored, so a "0 problems" total never implies complete conformance.
        issues.append(
            ValidationIssue(
                "info",
                basket_bid,
                tid,
                qualified_class,
                path,
                f"{ctx}: unchecked type {kind!r} (type not covered/unresolved)",
            )
        )
        return issues

    for node in raw_nodes:
        for problem in _validate_scalar(resolved, node, ctx):
            issues.append(ValidationIssue("error", basket_bid, tid, qualified_class, path, problem))
    return issues


def _duplicate_tid_issues(transfer: XtfTransfer) -> list[ValidationIssue]:
    """Flag an object whose TID already identifies another object of the transfer.

    In a FULL transfer (also INITIAL, or a basket without KIND) every TID is unique; UPDATE baskets are skipped.
    """
    first_seen: dict[str, str] = {}
    issues: list[ValidationIssue] = []
    for basket in transfer.baskets:
        if basket.kind == "UPDATE":
            continue
        for obj in basket.objects:
            if obj.tid is None:
                continue
            if obj.tid in first_seen:
                message = f"TID {obj.tid!r} already used by {first_seen[obj.tid]}"
                issues.append(ValidationIssue("error", basket.bid, obj.tid, obj.qualified_class, None, message))
            else:
                first_seen[obj.tid] = obj.qualified_class
    return issues


def validate_transfer(
    transfer: XtfTransfer,
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
    catalogs: list[XtfTransfer] | None = None,
    check_constraints: bool = True,
) -> list[ValidationIssue]:
    """Validate an XtfTransfer, returning the issues found.

    `check_constraints` also evaluates the model's CONSTRAINTs; `catalogs` are extra transfers resolving references.
    """
    tid_index = build_tid_index(transfer, catalogs)
    schema_cache: dict[int, dict[str, ResolvedAttribute]] = {}
    issues: list[ValidationIssue] = _duplicate_tid_issues(transfer)
    for basket in transfer.baskets:
        for obj in basket.objects:
            if obj.operation == "DELETE":
                continue  # an incremental DELETE carries no attributes: only its TID names the object to remove
            issues.extend(
                _validate_object(
                    obj,
                    basket,
                    symbol_table=symbol_table,
                    repository=repository,
                    tid_index=tid_index,
                    schema_cache=schema_cache,
                )
            )
    if check_constraints:
        # Deferred: constraints.py imports convert.jsonfg, which itself imports this module.
        from interlis.xtf.constraints import check_transfer_constraints

        issues.extend(
            check_transfer_constraints(transfer, symbol_table=symbol_table, repository=repository, catalogs=catalogs)
        )
    return issues


def validate_stream(
    path: Path,
    *,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
    catalogs: Iterable[XtfTransfer] = (),
    check_constraints: bool = True,
) -> list[ValidationIssue]:
    """Like `validate_transfer`, but reads `path` two or three times so only one object is held at a time.

    The first pass keeps a stub per object to resolve references and spot duplicate TIDs; the second validates
    each object and, with `check_constraints`, the constraints needing no other object.
    """
    tid_index: dict[str, XtfObject] = {}
    first_seen: dict[str, str] = {}
    issues: list[ValidationIssue] = []
    for basket, obj in iter_objects(path):
        if obj.tid is None:
            continue
        tid_index.setdefault(obj.tid, XtfObject(obj.tid, obj.qualified_class, {}))
        if basket.kind == "UPDATE":
            continue
        if obj.tid in first_seen:
            message = f"TID {obj.tid!r} already used by {first_seen[obj.tid]}"
            issues.append(ValidationIssue("error", basket.bid, obj.tid, obj.qualified_class, None, message))
        else:
            first_seen[obj.tid] = obj.qualified_class
    for catalog in catalogs:
        for basket in catalog.baskets:
            for obj in basket.objects:
                if obj.tid is not None:
                    tid_index.setdefault(obj.tid, obj)
    del first_seen
    schema_cache: dict[int, dict[str, ResolvedAttribute]] = {}
    for basket, obj in iter_objects(path):
        if obj.operation == "DELETE":
            continue
        issues.extend(
            _validate_object(
                obj,
                basket,
                symbol_table=symbol_table,
                repository=repository,
                tid_index=tid_index,
                schema_cache=schema_cache,
            )
        )
    if check_constraints:
        # Deferred: constraints.py imports convert.jsonfg, which itself imports this module.
        from interlis.xtf.constraints import check_stream_constraints

        issues.extend(check_stream_constraints(iter_objects(path), symbol_table=symbol_table, repository=repository))
    return issues
