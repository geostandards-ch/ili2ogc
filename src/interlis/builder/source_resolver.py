"""Interpret a `source:` binding entry against a parse-tree node.

A token yields its raw text and a rule node is visited recursively. Supported forms: field+index, presence,
alt_*, multi, optional, anchor, path, constant, composed segments+join, sequence_pattern, `field: null`.
"""

import warnings
from typing import Any

from antlr4 import ParserRuleContext
from antlr4.tree.Tree import TerminalNode

from interlis.builder import context_access as ca
from interlis.builder.errors import BuildError
from interlis.builder.forward_refs import ForwardRef
from interlis.runtime.parse import unquote_interlis_string

ALT_KINDS = {"alt_token", "alt_rule", "alt_token_or_rule", "alt_token_presence"}


def _resolve_node(node: Any, builder: Any, rule: str) -> Any:
    if node is None:
        return None
    if isinstance(node, TerminalNode):
        return node.getText()
    if isinstance(node, ParserRuleContext):
        return builder.visit(node)
    if isinstance(node, list):
        # A multi accessor returns its full list even when no list was requested: resolve each element.
        resolved = [r for r in (_resolve_node(n, builder, rule) for n in node) if r is not None]
        if len(resolved) == 1:
            return resolved[0]
        return resolved if resolved else None
    return node


def _resolve_multi_node(node: Any, builder: Any, rule: str, wrap_map: dict) -> Any:
    """Like `_resolve_node`, but promotes a rule-bag or bare token to a typed instance when `wrap_map` covers it.

    Needed for `coordinateType.Axis`: each bare `numeric()`/`NUMERIC` must become its own `NumType` (an empty one
    for a bare keyword), never a shared raw bag that downstream code expecting a `MetaInstance` cannot use.
    """
    if isinstance(node, ParserRuleContext):
        rule_name = builder._rule_name(node)
        if rule_name in wrap_map:
            return builder.visit_wrapped(node, wrap_map[rule_name], rule)
        return _resolve_node(node, builder, rule)
    if isinstance(node, TerminalNode):
        token_name = _token_name(node, builder)
        if token_name is not None and token_name in wrap_map:
            return builder.registry.new_instance(wrap_map[token_name])
        return node.getText()
    return _resolve_node(node, builder, rule)


def _token_name(node: Any, builder: Any) -> str | None:
    symbol = getattr(node, "symbol", None)
    if symbol is None:
        return None
    names = builder.parser_symbolic_names
    idx = symbol.type
    return names[idx] if 0 <= idx < len(names) else None


def resolve_source(
    ctx: Any,
    source: dict,
    *,
    rule: str,
    construction_context: dict,
    builder: Any,
    rule_map: dict | None = None,
    binding_key: str | None = None,
    wrap_map: dict | None = None,
) -> Any:
    value = _resolve_source(
        ctx,
        source,
        rule=rule,
        construction_context=construction_context,
        builder=builder,
        rule_map=rule_map,
        binding_key=binding_key,
        wrap_map=wrap_map,
    )
    if source and source.get("as_forward_ref") and source.get("field") is not None and isinstance(value, str):
        # A name read from the parse tree (e.g. a LINE FORM's structure) names an element to resolve.
        return ForwardRef(name=value, rule=rule, home_model=builder._current_model_name())
    return value


def _resolve_source(
    ctx: Any,
    source: dict,
    *,
    rule: str,
    construction_context: dict,
    builder: Any,
    rule_map: dict | None = None,
    binding_key: str | None = None,
    wrap_map: dict | None = None,
) -> Any:
    if not source:
        return None

    optional = bool(source.get("optional"))

    if source.get("kind") == "constant":
        return source.get("value")

    # --- child_index: raw position in ctx.children, for operands at a fixed position
    # (e.g. numeric Min/Max) that a name-based accessor could confuse with other nodes. ---
    if "child_index" in source:
        idx = source["child_index"]
        children = list(ctx.children or [])
        node = children[idx] if 0 <= idx < len(children) else None
        if node is None:
            if optional:
                return None
            raise BuildError(f"child_index={idx!r} missing on {type(ctx).__name__}", rule=rule, ctx=ctx)
        return _resolve_node(node, builder, rule)

    field = source.get("field")

    # field: null alone -> value propagated by the enclosing rule under the same attribute key.
    if field is None:
        key = source.get("context_key") or binding_key or rule
        value = construction_context.get(key)
        if source.get("as_forward_ref") and isinstance(value, str):
            # Wrap a raw value (e.g. an imported name) into a named reference resolved later like any other.
            return ForwardRef(name=value, rule=rule, always_external=bool(source.get("always_external_ref")))
        return value

    kind = source.get("kind")

    # --- multi: list, each element resolved. Checked before the alt case: a composed field combined with
    # multi:true must list ALL occurrences of every alternative. ---
    if source.get("multi"):
        names = [n.strip() for n in field.split("|")] if "|" in field else [field]
        nodes: list[Any] = []
        for name in names:
            if ca.has_accessor(ctx, name):
                nodes.extend(ca.call_list(ctx, name))
        if source.get("by_position") and ctx.children:
            children = list(ctx.children)
            nodes.sort(key=children.index)
        between = source.get("between")
        if between and nodes:
            # The same accessor is reused at several positions in one rule (e.g. modeldef's `Name`): keep only
            # the occurrences between two anchor tokens, by position in ctx.children.
            start_name, end_name = between
            children = list(ctx.children or [])
            start_positions = [children.index(n) for n in ca.call_list(ctx, start_name) if n in children]
            end_positions = [children.index(n) for n in ca.call_list(ctx, end_name) if n in children]
            if start_positions and end_positions:
                lo, hi = min(start_positions), min(end_positions)
                nodes = [n for n in nodes if lo < children.index(n) < hi]
            else:
                nodes = []
        if wrap_map:
            return [_resolve_multi_node(n, builder, rule, wrap_map) for n in nodes]
        if "path" in source:
            # Each occurrence is a sub-rule bag: extract the same key from all
            # of them (e.g. roleDef's `A OR B` alternative targets).
            bags = [_resolve_node(n, builder, rule) for n in nodes]
            return [bag.get(source["path"]) for bag in bags if isinstance(bag, dict)]
        return [_resolve_node(n, builder, rule) for n in nodes]

    # --- alternatives (alt_token / alt_rule / alt_token_or_rule / alt_token_presence): '|'-separated field names,
    # first present one wins; the mapping key is the matched field NAME, or "absent". ---
    if kind in ALT_KINDS or "|" in field:
        names = [n.strip() for n in field.split("|")]
        alt_index = source.get("index")
        for name in names:
            if not ca.has_accessor(ctx, name):
                continue
            # An index selects which occurrence of the matched name to use, but may not apply to every
            # alternative (e.g. cardinality.Max `MUL|PosNumber`): fall back to no index rather than a TypeError.
            try:
                node = ca.call(ctx, name, alt_index)
            except TypeError:
                node = ca.call(ctx, name)
            if isinstance(node, list):
                # A multi accessor without a matching index returns its full list (never None, even empty);
                # without this guard an empty list counted as "alternative present".
                node = node[0] if node else None
            if node is not None:
                if kind == "alt_token_presence":
                    value = name
                elif wrap_map is not None and name in wrap_map and isinstance(node, ParserRuleContext):
                    # Promote the rule-bag to a typed instance BEFORE visiting: some children self-attach to the
                    # top of the construction stack during the visit and would otherwise get the wrong parent.
                    return builder.visit_wrapped(node, wrap_map[name], rule)
                else:
                    value = _resolve_node(node, builder, rule)
                if rule_map is not None:
                    return rule_map.get(name, value)
                return value
        if rule_map is not None and "absent" in rule_map:
            return rule_map["absent"]
        # No alternative present and no "absent" default: treated as optional and only warned about.
        warnings.warn(
            f"[BUILD-SPEC-GAP-ALT-ABSENT] [{rule}] no alternative present among {names!r} - treated as absent/None",
        )
        return None

    # --- sequence_pattern: an exact run of consecutive tokens from `field` (e.g. roleDef.Strongness '--' vs
    # '-<>' vs '-<#>'); the mapping key is the token NAMES (e.g. "MINUS LT GT"). ---
    if source.get("sequence_pattern"):
        if rule_map is None:
            raise BuildError("sequence_pattern with no associated rule:/mapping:", rule=rule, ctx=ctx)
        return _match_sequence_pattern(ctx, field, rule_map, builder, rule, optional)

    # --- anchor: position relative to an anchor token, for repeated field types. ---
    if "anchor" in source:
        return _resolve_anchor(ctx, field, source["anchor"], optional=optional, builder=builder, rule=rule)

    # --- path: delegates to a sub-rule (Container/ValueObject, visited via
    # _resolve_node -> returns a bag dict), extracts one key. ----------
    if "path" in source:
        if not ca.has_accessor(ctx, field):
            raise BuildError(f"accessor {field!r} not found on {type(ctx).__name__}", rule=rule, ctx=ctx)
        node = ca.call(ctx, field)
        if node is None:
            if optional:
                return None
            raise BuildError(f"{field!r} missing (path={source['path']!r})", rule=rule, ctx=ctx)
        bag = _resolve_node(node, builder, rule)
        if not isinstance(bag, dict):
            raise BuildError(
                f"path={source['path']!r} expected on a bag (dict), got {type(bag).__name__}", rule=rule, ctx=ctx
            )
        return bag.get(source["path"])

    # --- presence: boolean (does the field appear or not). ------------------
    if source.get("presence"):
        present = ca.is_present(ctx, field, source.get("index"))
        if rule_map is not None:
            key = f"{field}_present" if present else f"{field}_absent"
            if key in rule_map:
                return rule_map[key]
        return present

    # --- composed segments + join: concatenation of sibling-keyed sub-values (null = presence as-is); negative
    # indexes supported. ---
    if "join" in source:
        return _resolve_join(ctx, source, builder, rule)

    # --- standard case: field (+ index) (+ optional). No `alt:` filter: `ctx.getAltNumber()` is always 0 in this
    # grammar (no labelled alternatives), and the accessor name is already exclusive to its alternative.
    if not ca.has_accessor(ctx, field):
        if optional:
            return None
        raise BuildError(f"accessor {field!r} not found on {type(ctx).__name__}", rule=rule, ctx=ctx)

    node = ca.call(ctx, field, source.get("index"))
    if node is None:
        if optional:
            return None
        raise BuildError(f"{field!r} missing (not optional) on {type(ctx).__name__}", rule=rule, ctx=ctx)
    value = _resolve_node(node, builder, rule)
    if source.get("unquote") and isinstance(value, str):
        value = unquote_interlis_string(value)
    if rule_map is not None:
        return rule_map.get(value, value)
    return value


def _resolve_anchor(ctx: Any, field: str, anchor: str, *, optional: bool, builder: Any, rule: str) -> Any:
    if not ca.has_accessor(ctx, anchor):
        if optional:
            return None
        raise BuildError(f"anchor {anchor!r} not found on {type(ctx).__name__}", rule=rule, ctx=ctx)
    anchor_node = ca.call(ctx, anchor)
    if anchor_node is None:
        if optional:
            return None
        raise BuildError(f"anchor {anchor!r} missing", rule=rule, ctx=ctx)
    children = list(ctx.children or [])
    try:
        anchor_index = children.index(anchor_node)
    except ValueError:
        raise BuildError(f"anchor {anchor!r} not found in ctx.children", rule=rule, ctx=ctx)
    for node in ca.call_list(ctx, field):
        try:
            idx = children.index(node)
        except ValueError:
            continue
        if idx > anchor_index:
            return _resolve_node(node, builder, rule)
    if optional:
        return None
    raise BuildError(f"{field!r} missing after anchor {anchor!r}", rule=rule, ctx=ctx)


def _resolve_join(ctx: Any, source: dict, builder: Any, rule: str) -> str:
    separator = source["join"]
    field = source.get("field")
    index = source.get("index")
    segments: list[str] = []
    if field is not None:
        if index is not None:
            nodes = ca.call_list(ctx, field)
            if not nodes:
                raise BuildError(f"{field!r} empty (join, index={index})", rule=rule, ctx=ctx)
            segments.append(_resolve_node(nodes[index], builder, rule))
        else:
            segments.append(_resolve_node(ca.call(ctx, field), builder, rule))
    for key, value in source.items():
        if key in ("field", "index", "join", "optional") or value is not None:
            continue
        if not ca.has_accessor(ctx, key):
            continue
        node = ca.call(ctx, key)
        if node is not None:
            segments.append(_resolve_node(node, builder, rule))
    # A reference rule among the joined parts (e.g. `>>M.T.A->x`'s viewableRef) contributes its name.
    return separator.join(s.name if isinstance(s, ForwardRef) else s for s in segments if s is not None)


def _match_sequence_pattern(
    ctx: Any, field: str, rule_map: dict, builder: Any, rule: str, optional: bool = False
) -> Any:
    if not ca.has_accessor(ctx, field):
        raise BuildError(f"accessor {field!r} not found on {type(ctx).__name__}", rule=rule, ctx=ctx)
    # `field` may be a multi accessor (e.g. '--' = two MINUS tokens): anchor on the first occurrence and read the
    # sequence from its position in ctx.children. Absent is legitimate (roleDef's restriction form has no symbol).
    candidates = ca.call_list(ctx, field)
    if not candidates:
        if optional:
            return None
        raise BuildError(f"{field!r} missing (sequence_pattern)", rule=rule, ctx=ctx)
    anchor_node = candidates[0]
    children = list(ctx.children or [])
    try:
        start = children.index(anchor_node)
    except ValueError:
        raise BuildError(f"{field!r} not found in ctx.children", rule=rule, ctx=ctx)

    max_len = max(len(pattern.split()) for pattern in rule_map)
    window = children[start : start + max_len]
    names = [_token_name(n, builder) for n in window]
    for length in range(max_len, 0, -1):
        candidate = " ".join(n for n in names[:length] if n)
        if candidate in rule_map:
            return rule_map[candidate]
    raise BuildError(
        f"no token sequence matches {sorted(rule_map)} starting at {field!r} (got {names!r})",
        rule=rule,
        ctx=ctx,
    )
