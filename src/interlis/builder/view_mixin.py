"""VIEW construction: FormationKind, ALL OF expansion, bare attribute assignments, VIEW attribute type resolution.

Mixed into `InterlisModelBuilder`: immediate hooks run from `_build_instance`, deferred `ALL OF`/bare-attribute
typing from `build()` after `resolve_all()`, once `RenamedBaseView.BaseView` is resolved.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from antlr4 import ParserRuleContext
from antlr4.tree.Tree import TerminalNode

from interlis.antlr.InterlisParser import InterlisParser
from interlis.builder import context_access as ca
from interlis.metamodel.instance import MetaInstance

if TYPE_CHECKING:
    from interlis.builder._builder_protocol import _BuilderHost

    _Base = _BuilderHost
else:
    _Base = object  # real runtime base - see _builder_protocol.py for why


class _ViewBuildingMixin(_Base):
    _FORMATION_KIND_BY_SUBRULE = {
        "projection": "Projection",
        "join": "Join",
        "union": "Union",
        "aggregation": "Aggregation",
        "inspection": "Inspection",
    }

    def _set_view_formation_kind(self, instance: MetaInstance, ctx: ParserRuleContext) -> None:
        """Set View.FormationKind from which formationDef() alternative matched.

        Reads the raw `ViewDefContext`, as the unclaimed-children sweep has already built the formation's children.
        A view using `EXTENDS viewRef` leaves it unset (inherited).
        """
        formation = ca.call(ctx, "formationDef")
        if formation is None:
            return
        for subrule, value in self._FORMATION_KIND_BY_SUBRULE.items():
            if ca.has_accessor(formation, subrule) and ca.call(formation, subrule) is not None:
                instance.FormationKind = value
                if subrule == "join":
                    self._set_join_or_null(instance, ca.call(formation, subrule))
                elif subrule == "inspection":
                    self._stash_inspection_path(instance, ca.call(formation, subrule))
                elif subrule == "aggregation":
                    self._stash_aggregation_key(instance, ca.call(formation, subrule))
                return

    def _stash_aggregation_key(self, view: MetaInstance, aggregation_ctx: ParserRuleContext) -> None:
        """Record `AGGREGATION OF ... EQUAL(uniqueEl)`'s grouping key as `view._aggregation_key`.

        The generic `Container` machinery does not materialize `uniqueEl` without a parent instance, so a fresh
        `PathOrInspFactor` is merged by hand. `ALL` leaves the key unset.
        """
        unique_el = ca.call(aggregation_ctx, "uniqueEl")
        if unique_el is None:
            return
        path = ca.call(unique_el, "objectOrAttributePath")
        if path is None:
            return
        bag = self.visit(path)
        factor = self.registry.new_instance("IlisMeta16.ModelData.PathOrInspFactor")
        self._merge_bag_into_instance(factor, bag, "aggregation")
        view._aggregation_key = factor

    @staticmethod
    def _stash_inspection_path(view: MetaInstance, inspection_ctx: ParserRuleContext) -> None:
        """Record the `-> Name (-> Name)*` chain of an `INSPECTION OF base -> attr` view, and whether it is an AREA one.

        The generic `Container` machinery does not materialize the `PathOrInspFactor`, so the raw attribute names
        go to `view._inspection_path` for the JSON-FG/SQL converters.
        """
        view._inspection_area = ca.call(inspection_ctx, "AREA") is not None
        names = ca.call(inspection_ctx, "Name")
        if names is None:
            return
        tokens = names if isinstance(names, list) else [names]
        view._inspection_path = [t.getText() for t in tokens if t is not None]

    def _set_join_or_null(self, view: MetaInstance, join_ctx: ParserRuleContext) -> None:
        """Set `RenamedBaseView.OrNull` for every base of a `JOIN OF` carrying a trailing `(OR NULL)`.

        The grammar attaches `(OR NULL)` to the IMMEDIATELY PRECEDING `renamedViewableRef` (not the 1st base) and
        offers no accessor for that sibling token, so `join_ctx.children` is read directly.
        """
        bases = [b for b in getattr(view, "RenamedBaseView", None) or [] if isinstance(b, MetaInstance)]
        children = list(join_ctx.children or [])
        for i, ref_ctx in enumerate(ca.call_list(join_ctx, "renamedViewableRef")):
            if i >= len(bases):
                break
            idx = children.index(ref_ctx)
            if idx + 1 < len(children) and children[idx + 1].getText() == "(":
                bases[i].OrNull = True

    def _expand_view_all_of(self, view: MetaInstance, ctx: ParserRuleContext) -> None:
        """Record a View's `ATTRIBUTE ALL OF <Name>;` for deferred expansion.

        `RenamedBaseView.BaseView` can still be an unresolved `ForwardRef` here; expansion happens in
        `_apply_pending_view_all_of`, after `resolve_all()`.
        """
        va = ca.call(ctx, "viewAttributes")
        if va is None or not ca.has_accessor(va, "ALL") or ca.call(va, "ALL") is None:
            return
        self._pending_view_all_of.append((view, ctx))

    def _apply_pending_view_all_of(self) -> None:
        """Expand every recorded `ALL OF` once references are resolved.

        Only the base's own attributes are used. Each `ALL` is paired with the name after its `OF`, by position.
        """
        pending = self._pending_view_all_of
        self._pending_view_all_of = []
        for view, ctx in pending:
            va = ca.call(ctx, "viewAttributes")
            for all_of_name in self._all_of_base_names(va):
                base = self._find_renamed_base_view(view, all_of_name)
                if base is None or not isinstance(base.BaseView, MetaInstance):
                    continue
                for attr in getattr(base.BaseView, "ClassAttribute", None) or []:
                    copy = self.registry.new_instance("IlisMeta16.ModelData.AttrOrParam")
                    copy.Name = attr.Name
                    self.attachment.attach(
                        copy, "Type", attr.Type, association="AttrOrParamType", role="Type", rule="viewAttributes"
                    )
                    copy.Derivates = [self._identity_view_path(all_of_name, attr.Name)]
                    self.attachment.attach(
                        view,
                        "ClassAttribute",
                        copy,
                        association="ClassAttr",
                        role="ClassAttribute",
                        rule="viewAttributes",
                    )
            self._reorder_view_class_attributes(view, va)

    @staticmethod
    def _reorder_view_class_attributes(view: MetaInstance, va: ParserRuleContext | None) -> None:
        """Reorder `view.ClassAttribute` to match the source order of the view attributes.

        Attributes are attached at different times, so the built order differs; a writer needs the source order.
        """
        if va is None:
            return
        attrs = list(getattr(view, "ClassAttribute", None) or [])
        if not attrs:
            return
        current_index = {id(a): i for i, a in enumerate(attrs)}
        by_name: dict[str | None, list[MetaInstance]] = {}
        for a in attrs:
            by_name.setdefault(getattr(a, "Name", None), []).append(a)

        children = list(va.children or [])
        all_of_indices: dict[int, str] = {}
        for node in ca.call_list(va, "ALL"):
            idx = children.index(node)
            if idx + 2 < len(children):
                all_of_indices[idx] = children[idx + 2].getText()

        position_of_id: dict[int, int] = {}
        consumed_by_name: dict[str, int] = {}
        slot = 0
        i, n = 0, len(children)
        while i < n:
            if i in all_of_indices:
                base_name = all_of_indices[i]
                for a in attrs:
                    if id(a) in position_of_id:
                        continue
                    derivates = getattr(a, "Derivates", None) or []
                    if not derivates or not getattr(derivates[0], "_all_of_identity", False):
                        continue
                    path_els = getattr(derivates[0], "PathEls", None) or []
                    if path_els and getattr(path_els[0], "Ref", None) == base_name:
                        position_of_id[id(a)] = slot
                        slot += 1
                i += 3  # ALL OF Name
                continue
            node = children[i]
            if isinstance(node, TerminalNode) and node.symbol.type == InterlisParser.Name:
                name = node.getText()
                candidates = by_name.get(name) or []
                used = consumed_by_name.get(name, 0)
                if used < len(candidates):
                    position_of_id[id(candidates[used])] = slot
                    consumed_by_name[name] = used + 1
                    slot += 1
            i += 1

        attrs.sort(key=lambda a: position_of_id.get(id(a), 1_000_000 + current_index[id(a)]))
        setattr(view, "ClassAttribute", attrs)  # noqa: B010 - dynamic MetaInstance field, no static attribute to assign

    def _identity_view_path(self, base_ref: str, attr_name: str) -> MetaInstance:
        """Build the `<base> -> <attr>` identity `PathOrInspFactor` for one `ALL OF` view attribute.

        Gives consumers that project by expression (`CREATE VIEW` `SELECT`) the same `Derivates` as an explicit
        `<attr> := <base> -> <attr>`; `Type` is set directly, so the path is never walked for type resolution.
        """
        factor = self.registry.new_instance("IlisMeta16.ModelData.PathOrInspFactor")
        els: list[MetaInstance] = []
        for ref in (base_ref, attr_name):
            el = self.registry.new_instance("IlisMeta16.ModelData.PathEl")
            el.Kind = "ReferenceAttr"
            el.Ref = ref
            els.append(el)
        factor.PathEls = els
        # Lets a converter tell an `ALL OF` pass-through (may drop an unprojectable attribute with a note) from an
        # explicit `Name := expression` (must fail loudly).
        factor._all_of_identity = True
        return factor

    @staticmethod
    def _all_of_base_names(va: ParserRuleContext) -> list[str]:
        """Extract every "ALL OF <Name>" base name from a ViewAttributesContext, in order.

        Positional walk: `ALL` is followed by `OF` then the base `Name`; the `Name` accessor alone is ambiguous
        with the `Name ':=' expression` alternative.
        """
        if not ca.has_accessor(va, "ALL"):
            return []
        children = list(va.children or [])
        names = []
        for node in ca.call_list(va, "ALL"):
            i = children.index(node)
            if i + 2 < len(children):
                names.append(children[i + 2].getText())
        return names

    @staticmethod
    def _find_renamed_base_view(view: MetaInstance, name: str) -> MetaInstance | None:
        """Find `view`'s RenamedBaseView referred to by "ALL OF <name>"/"<name> ASSIGN ...".

        `name` is the base's rename alias, or the base Class's own short name when no alias was given.
        """
        for base in getattr(view, "RenamedBaseView", None) or []:
            base_view = base.BaseView if isinstance(base.BaseView, MetaInstance) else None
            candidate = base.Name or (base_view.Name if base_view is not None else None)
            if candidate == name:
                return base
        return None

    _VIEW_ATTRIBUTE_MODIFIER_TOKENS = (
        InterlisParser.ABSTRACT,
        InterlisParser.EXTENDED,
        InterlisParser.FINAL,
        InterlisParser.TRANSIENT,
    )

    @staticmethod
    def _bare_view_attribute_assignments(va: ParserRuleContext) -> list[tuple[str, set[int], ParserRuleContext]]:
        """Extract every "Name (Properties<...>)? ASSIGN expression SEMI" occurrence from a ViewAttributesContext.

        Positional walk like `_all_of_base_names`: a top-level `Name` belongs here unless it is the base name of an
        "ALL OF Name" triple. Scans forward to its `ASSIGN`, collecting modifier tokens on the way.
        """
        children = list(va.children or [])
        all_of_name_ids = set()
        for node in ca.call_list(va, "ALL"):
            i = children.index(node)
            if i + 2 < len(children):
                all_of_name_ids.add(id(children[i + 2]))

        results: list[tuple[str, set[int], ParserRuleContext]] = []
        i, n = 0, len(children)
        while i < n:
            node = children[i]
            if (
                isinstance(node, TerminalNode)
                and node.symbol.type == InterlisParser.Name
                and id(node) not in all_of_name_ids
            ):
                modifier_tokens: set[int] = set()
                j = i + 1
                while j < n:
                    child = children[j]
                    if isinstance(child, TerminalNode) and child.symbol.type == InterlisParser.ASSIGN:
                        break
                    if isinstance(child, TerminalNode) and child.symbol.type == InterlisParser.SEMI:
                        j = -1
                        break
                    if (
                        isinstance(child, TerminalNode)
                        and child.symbol.type in _ViewBuildingMixin._VIEW_ATTRIBUTE_MODIFIER_TOKENS
                    ):
                        modifier_tokens.add(child.symbol.type)
                    j += 1
                if j != -1 and j < n and j + 1 < n and isinstance(children[j + 1], ParserRuleContext):
                    results.append((node.getText(), modifier_tokens, children[j + 1]))
                    i = j + 2
                    continue
            i += 1
        return results

    def _build_view_bare_attributes(self, view: MetaInstance, ctx: ParserRuleContext) -> None:
        """Build one `AttrOrParam` per `viewAttributes()` "Name := expression" occurrence.

        Always `Final=True`; of the modifier tokens only `TRANSIENT` maps onto an `AttrOrParam` attribute.
        `Type` is set later by `_apply_pending_view_bare_attr_types`, as `BaseView` can still be unresolved.
        """
        va = ca.call(ctx, "viewAttributes")
        if va is None:
            return
        for name, modifier_tokens, expr_ctx in self._bare_view_attribute_assignments(va):
            expr = self.visit(expr_ctx)
            attr = self.registry.new_instance("IlisMeta16.ModelData.AttrOrParam")
            attr.Name = name
            attr.Final = True
            if InterlisParser.TRANSIENT in modifier_tokens:
                attr.Transient = True
            if expr is not None:
                attr.Derivates = [expr]
            self.attachment.attach(
                view,
                "ClassAttribute",
                attr,
                association="ClassAttr",
                role="ClassAttribute",
                rule="viewAttributes",
            )
            if expr is not None:
                self._pending_view_bare_attrs.append((view, attr, expr))

    def _apply_pending_view_bare_attr_types(self) -> None:
        """Resolve `Type` for every bare `Name := expression` view attribute recorded earlier.

        A path is followed through the base view's attributes (a REFERENCE TO or STRUCTURE continues into its class)
        and association roles; only the last element gives the type. If anything is unresolvable, `Type` stays unset.
        """
        pending = self._pending_view_bare_attrs
        self._pending_view_bare_attrs = []
        for view, attr, expr in pending:
            type_instance = self._resolve_view_attribute_type(view, expr)
            if type_instance is not None:
                self.attachment.attach(
                    attr,
                    "Type",
                    type_instance,
                    association="AttrOrParamType",
                    role="Type",
                    rule="viewAttributes",
                )

    def _resolve_view_attribute_type(self, view: MetaInstance, expr: MetaInstance) -> MetaInstance | None:
        if not expr._qualified_class.endswith("PathOrInspFactor"):
            return None
        path_els = list(getattr(expr, "PathEls", None) or [])
        if len(path_els) < 2:
            return None
        base = self._find_renamed_base_view(view, getattr(path_els[0], "Ref", None))
        if base is None or not isinstance(base.BaseView, MetaInstance):
            return None
        current: MetaInstance = base.BaseView
        last_index = len(path_els) - 1
        for i, path_el in enumerate(path_els[1:], start=1):
            ref = getattr(path_el, "Ref", None)
            if ref is None:
                return None
            is_last = i == last_index
            found_attr = self._find_class_attribute(current, ref)
            if found_attr is not None:
                if is_last:
                    return found_attr.Type if isinstance(found_attr.Type, MetaInstance) else None
                target = self._hop_target_class(found_attr.Type)
            elif is_last:
                return None  # a role alone (no trailing attribute) has no scalar Type
            else:
                role = self._find_association_role(current, ref) or self._embedded_role(current, ref)
                target = self._role_target_class(role) if role is not None else None
            if target is None:
                return None
            current = target
        return None

    @staticmethod
    def _hop_target_class(attr_type: MetaInstance | None) -> MetaInstance | None:
        """The class a non-final path hop continues into: a REFERENCE TO target, or a STRUCTURE's own members."""
        if not isinstance(attr_type, MetaInstance):
            return None
        if attr_type._qualified_class.rsplit(".", 1)[-1] == "ReferenceType":
            base = getattr(attr_type, "BaseClass", None)
            base = base[0] if isinstance(base, list) and base else base
            return base if isinstance(base, MetaInstance) else None
        return attr_type if getattr(attr_type, "Kind", None) == "Structure" else None

    def _embedded_role(self, cls_instance: MetaInstance, name: str) -> MetaInstance | None:
        """The role of an embedded association (a foreign key on `cls_instance`) called `name`.

        The association may be declared in this model or in an imported one, so every loaded table is searched.
        """
        from interlis.xtf.schema import embedded_roles_of  # lazy: xtf.schema imports this package

        tables = [self.symbol_table, *self.repository.loaded_models().values()]
        return next((r for t in tables if (r := embedded_roles_of(cls_instance, t).get(name)) is not None), None)

    @staticmethod
    def _find_class_attribute(cls_or_assoc: MetaInstance, name: str) -> MetaInstance | None:
        """Own attribute by name, then inherited via the `Super` chain (`EXTENDS`/`(EXTENDED)`); own wins."""
        current: MetaInstance | None = cls_or_assoc
        seen: set[int] = set()
        while isinstance(current, MetaInstance) and id(current) not in seen:
            seen.add(id(current))
            for attr in getattr(current, "ClassAttribute", None) or []:
                if attr.Name == name:
                    return attr
            current = getattr(current, "Super", None)
        return None

    @staticmethod
    def _find_association_role(cls_or_assoc: MetaInstance, name: str) -> MetaInstance | None:
        for role in getattr(cls_or_assoc, "Role", None) or []:
            if role.Name == name:
                return role
        return None

    @staticmethod
    def _role_target_class(role: MetaInstance) -> MetaInstance | None:
        """Return a `Role`'s target `Class` via `BaseClass`.

        Duplicated from `xtf.schema` because that module imports from `interlis.builder`, not the reverse.
        """
        base = getattr(role, "BaseClass", None)
        if isinstance(base, list):
            base = base[0] if base else None
        return base if isinstance(base, MetaInstance) else None
