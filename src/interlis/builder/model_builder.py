"""Generic ModelBuilder engine.

`visit(ctx)` derives the rule name from the context type, loads the matching mapping entry and applies one
of 4 execution strategies by `kind`; there are no rule-specific visit methods.
"""

import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from antlr4 import ParserRuleContext
from antlr4.tree.Tree import TerminalNode

from interlis.antlr.InterlisParser import InterlisParser
from interlis.antlr.InterlisParserVisitor import InterlisParserVisitor
from interlis.builder import context_access as ca
from interlis.builder.attach import AttachmentResolver
from interlis.builder.context_mixin import _ContextMixin
from interlis.builder.derived_mixin import _DerivedAttributesMixin
from interlis.builder.errors import BuildError
from interlis.builder.forward_refs import ForwardRef, ForwardRefResolver, SymbolTable
from interlis.builder.oid_mixin import _OidMixin
from interlis.builder.repository import ModelRepository
from interlis.builder.source_resolver import resolve_source
from interlis.builder.translation_mixin import _TranslationMixin
from interlis.builder.view_mixin import _ViewBuildingMixin
from interlis.metamodel.instance import MetaInstance
from interlis.metamodel.registry import MetamodelRegistry
from interlis.metamodel.uml_schema import MetamodelSchema
from interlis.runtime.parse import unquote_interlis_string
from interlis.spec.models import Parent, SpecEntry
from interlis.spec.spec_index import load_spec

_ASSOCIATION_ATTRIBUTE_ENTRY = SpecEntry(
    grammar={"rule": "roleDef"},
    kind="Instance",
    target="IlisMeta16.ModelData.AttrOrParam",
    parent=Parent(association="ClassAttr", role="ClassAttribute"),
    attribute_bindings={"Name": {"source": {"field": "Name"}}},
)


def _plain_attribute_path(text: str) -> list[str] | None:
    """`Name->Name->...` as a list of names; `None` for any other path form (indexes, `THIS`, ...)."""
    names = text.split("->")
    return names if all(re.fullmatch(r"[A-Za-z_]\w*", name) for name in names) else None


class InterlisModelBuilder(
    _ViewBuildingMixin, _OidMixin, _TranslationMixin, _ContextMixin, _DerivedAttributesMixin, InterlisParserVisitor
):
    def __init__(self, mappings_dir: Path, spec_dir: Path, *, repository: ModelRepository | None = None):
        schema = MetamodelSchema.load(mappings_dir)
        registry = MetamodelRegistry.build(schema)
        spec: dict[str, SpecEntry] = load_spec(spec_dir)
        attachment = AttachmentResolver(schema.uml)
        self._init_shared(schema, registry, spec, attachment, repository)

    @classmethod
    def _from_shared(cls, schema, registry, spec, attachment, repository) -> "InterlisModelBuilder":
        """Build a sub-builder for an imported file, sharing the session's components.

        The registry's dynamic Pydantic classes must stay the same Python objects across files.
        """
        self = cls.__new__(cls)
        self._init_shared(schema, registry, spec, attachment, repository)
        return self

    def _init_shared(self, schema, registry, spec, attachment, repository) -> None:
        self.schema = schema
        self.registry = registry
        self.spec = spec
        self.attachment = attachment
        # A bare ModelRepository (no search directory) always backs the predefined INTERLIS model; real cross-file
        # resolution stays opt-in via explicit search directories.
        self.repository = repository if repository is not None else ModelRepository([])
        self.symbol_table = SymbolTable()
        self.forward_refs = ForwardRefResolver(self.symbol_table)
        self.parser_symbolic_names = InterlisParser.symbolicNames
        self._construction_stack: list[dict] = []
        self._parent_stack: list[MetaInstance] = []
        # eCH-0117 meta-attribute capture: empty unless build() receives meta_attributes (root file only).
        self._pending_meta_attributes: list[tuple[int, str, str]] = []
        self._meta_attribute_index = 0
        # Attributes whose `MANDATORY <named domain>` could not attach at construction (Type still a ForwardRef to a
        # possibly shared domain); applied after reference resolution.
        self._pending_mandatory_overrides: list[MetaInstance] = []
        # `ATTRIBUTE ALL OF <Name>;` needs RenamedBaseView.BaseView resolved, which is still a ForwardRef during
        # construction; recorded here and expanded in build() after reference resolution.
        self._pending_view_all_of: list[tuple[MetaInstance, ParserRuleContext]] = []
        # `Name := expression` view attributes need RenamedBaseView.BaseView resolved to type them; same deferral
        # as above.
        self._pending_view_bare_attrs: list[tuple[MetaInstance, MetaInstance, MetaInstance]] = []
        # `TRANSLATION OF` clause: `Model` has no field for it, so it is recorded here and aligned in build() after
        # reference resolution.
        self._pending_translations: list[tuple[MetaInstance, str]] = []
        # Instances for the predefined INTERLIS types whose names are reserved tokens (reaching the builder only as
        # opaque sentinels), materialised on demand by `_predefined_type`, one per token.
        self._predefined_type_cache: dict[str, MetaInstance] = {}
        self._predefined_line_forms: dict[str, MetaInstance] = {}
        self.repository.bind_builder_factory(self._make_sub_builder)

    def _make_sub_builder(self) -> "InterlisModelBuilder":
        return InterlisModelBuilder._from_shared(
            self.schema, self.registry, self.spec, self.attachment, self.repository
        )

    # --- Public entry point ---
    def build(self, tree: ParserRuleContext, *, meta_attributes: list[tuple[int, str, str]] | None = None) -> Any:
        """Build `tree`, attaching eCH-0117 `!!@Name=Value` comments to the first following construct.

        Only `tree` itself is covered; an imported model gets its own sub-builder `build()` call.
        """
        self._pending_meta_attributes = sorted(meta_attributes or [], key=lambda triple: triple[0])
        self._meta_attribute_index = 0
        result = self.visit(tree)
        self.forward_refs.resolve_all(repository=self.repository)
        self._apply_pending_mandatory_overrides()
        self._apply_pending_view_all_of()
        self._apply_pending_view_bare_attr_types()
        self._apply_pending_translations()
        self._apply_derived_role_flags()
        return result

    def _apply_pending_mandatory_overrides(self) -> None:
        """Give each queued attribute its own `Type` clone with `Mandatory=True`, after references resolve.

        A named domain is a single shared instance, so it must be cloned first. A STRUCTURE type is never cloned;
        `Mandatory` goes on the attribute instead.
        """
        for instance in self._pending_mandatory_overrides:
            resolved = getattr(instance, "Type", None)
            if not isinstance(resolved, MetaInstance) or bool(getattr(resolved, "Mandatory", False)):
                continue
            if resolved._qualified_class.rsplit(".", 1)[-1] == "Class":
                instance.Mandatory = True
                continue
            fields = {
                k: v for k, v in {**resolved.__dict__, **(resolved.model_extra or {})}.items() if not k.startswith("_")
            }
            fields["Mandatory"] = True
            instance.Type = self.registry.new_instance(resolved._qualified_class, **fields)

    # Predefined INTERLIS types whose names are reserved lexer tokens: HALIGNMENT and VALIGNMENT are
    # ordered enumerations; BOOLEAN -> BooleanType, URI -> TextType Kind=Uri, UUIDOID -> TextType (36 chars).
    _PREDEFINED_ENUM_ELEMENTS: dict[str, tuple[str, ...]] = {
        "HALIGNMENT": ("Left", "Center", "Right"),
        "VALIGNMENT": ("Top", "Cap", "Half", "Base", "Bottom"),
    }

    def _predefined_type(self, token: str) -> MetaInstance | None:
        """Return the shared instance for a predefined `INTERLIS.<token>` type (one per token per builder)."""
        cached = self._predefined_type_cache.get(token)
        if cached is not None:
            return cached
        instance: MetaInstance | None = None
        if token in self._PREDEFINED_ENUM_ELEMENTS:
            instance = self.registry.new_instance("IlisMeta16.ModelData.EnumType")
            instance.Name = token
            instance.Order = "Ordered"
            top = self.registry.new_instance("IlisMeta16.ModelData.EnumNode")
            top.Name = "TOP"
            self.attachment.attach(
                instance,
                "TopNode",
                top,
                association="TopNode",
                role="TopNode",
                rule="<predefined>",
            )
            for element_name in self._PREDEFINED_ENUM_ELEMENTS[token]:
                node = self.registry.new_instance("IlisMeta16.ModelData.EnumNode")
                node.Name = element_name
                self.attachment.attach(
                    top,
                    "Node",
                    node,
                    association="SubNode",
                    role="Node",
                    rule="<predefined>",
                )
        elif token == "BOOLEAN":
            instance = self.registry.new_instance("IlisMeta16.ModelData.BooleanType")
            instance.Name = "BOOLEAN"
        elif token == "URI":
            instance = self.registry.new_instance("IlisMeta16.ModelData.TextType")
            instance.Name = "URI"
            instance.Kind = "Uri"
        elif token == "UUIDOID":
            instance = self.registry.new_instance("IlisMeta16.ModelData.TextType")
            instance.Name = "UUIDOID"
            instance.Kind = "Text"
            instance.MaxLength = 36
        if instance is not None:
            self._predefined_type_cache[token] = instance
        return instance

    def _resolve_predefined_type_sentinel(self, instance: MetaInstance, bag: dict[str, Any]) -> None:
        """Replace a predefined-`INTERLIS`-token sentinel in an `attrTypeDef` bag with a real Type.

        The sentinel is an opaque string or a `structureRef` ForwardRef that resolves to nothing; a leading
        `MANDATORY` is queued in `_pending_mandatory_overrides`.
        """
        type_value = bag.get("Type")
        token: str | None = None
        if isinstance(type_value, str) and type_value in ("INTERLIS.HALIGNMENT", "INTERLIS.VALIGNMENT"):
            token = type_value.split(".", 1)[1]
        elif isinstance(type_value, ForwardRef) and type_value.name in (
            "INTERLIS.BOOLEAN",
            "INTERLIS.URI",
            "INTERLIS.UUIDOID",
        ):
            token = type_value.name.split(".", 1)[1]
        if token is None:
            return
        synthesized = self._predefined_type(token)
        if synthesized is None:
            return
        bag["Type"] = synthesized
        if bag.pop("Mandatory", False) is True:
            self._pending_mandatory_overrides.append(instance)

    @staticmethod
    def _ctx_line(ctx: Any) -> int | None:
        """Return the source line of a ParserRuleContext OR a bare Token."""
        start = getattr(ctx, "start", None)
        return getattr(start if start is not None else ctx, "line", None)

    def _attach_pending_meta_attributes(self, instance: MetaInstance, ctx: Any) -> None:
        """Attach every pending meta-attribute comment up to `ctx`'s own line.

        Instances are visited depth-first in source order, so the first one at or after a comment's line is
        its "first following construct".
        """
        line = self._ctx_line(ctx)
        if line is None or self._meta_attribute_index >= len(self._pending_meta_attributes):
            return
        while self._meta_attribute_index < len(self._pending_meta_attributes):
            comment_line, name, value = self._pending_meta_attributes[self._meta_attribute_index]
            if comment_line > line:
                break
            meta = self.registry.new_instance("IlisMeta16.ModelData.MetaAttribute")
            meta.Name = name
            meta.Value = value
            meta._line_gap = line - comment_line
            self.attachment.attach(
                instance,
                "MetaAttribute",
                meta,
                association="MetaAttributes",
                role="MetaAttribute",
                rule="metaAttribute",
            )
            self._meta_attribute_index += 1

    # --- Generic dispatch ---
    def visit(self, ctx: ParserRuleContext) -> Any:
        if ctx is None:
            return None
        rule_name = self._rule_name(ctx)
        entry = self.spec.get(rule_name)
        if entry is None:
            return super().visitChildren(ctx)

        if entry.kind in ("Instance", "ValueObject"):
            return self._build_instance(ctx, rule_name, entry)
        if entry.kind == "Conditional":
            return self._build_conditional(ctx, rule_name, entry)
        if entry.kind in ("Container", "Dispatcher"):
            return self._relay(ctx, rule_name, entry)
        if entry.kind == "Reference":
            return self._resolve_or_defer(ctx, rule_name, entry)
        raise BuildError(f"unknown kind {entry.kind!r}", rule=rule_name, ctx=ctx)

    @staticmethod
    def _rule_name(ctx: ParserRuleContext) -> str:
        name = type(ctx).__name__
        if name.endswith("Context"):
            name = name[: -len("Context")]
        return name[0].lower() + name[1:] if name else name

    # --- Strategy 1: BuildInstance (kind Instance / ValueObject) ---
    def _build_instance(self, ctx: ParserRuleContext, rule_name: str, entry: SpecEntry):
        if isinstance(entry.target, list):
            return self._build_multi_target(ctx, rule_name, entry)

        # roleDef's `Name : type ;` form is an association ATTRIBUTE (a RoleDef
        # always carries --/-<>/-<#>): built and attached like an attributeDef.
        association_attribute = rule_name == "roleDef" and ctx.COLON() is not None
        if association_attribute:
            entry = _ASSOCIATION_ATTRIBUTE_ENTRY

        if rule_name == "modeldef":
            names = ctx.Name()
            self.forward_refs.current_model = names[0].getText() if names else None
            self._register_unqualified_imports(ctx)

        instance = self.registry.new_instance(entry.target)
        instance._source_ctx = ctx
        self._attach_pending_meta_attributes(instance, ctx)

        outer_ctx = self._construction_stack[-1] if self._construction_stack else {}
        consumed: set[int] = set()

        if entry.discriminant:
            value = self._resolve_discriminant(ctx, rule_name, entry.discriminant, outer_ctx, consumed)
            if value is not None:
                setattr(instance, entry.discriminant.attribute, value)

        # Push before capturing this rule's binding context, so `context_key: <rule_name>` finds this instance and
        # not the enclosing rule's.
        with self._pushed_context(rule_name, instance):
            construction_ctx = self._construction_stack[-1]
            if entry.attribute_bindings:
                self._apply_bindings(instance, ctx, rule_name, entry.attribute_bindings, construction_ctx, consumed)
            sweep_results = self._sweep_unclaimed_children(ctx, consumed)
        self._attach_unclaimed_results(instance, sweep_results, rule_name)

        self._maybe_register_symbol(instance)

        if rule_name == "classDef":
            self._attach_class_oid(instance, ctx)
            self._fix_class_extended_super(instance, ctx)
        elif rule_name == "modeldef":
            self._register_translation_of(instance, ctx)
        elif rule_name == "unitDef":
            self._register_unit_alias(instance, ctx)
        elif rule_name == "viewDef":
            self._set_view_formation_kind(instance, ctx)
            self._expand_view_all_of(instance, ctx)
            self._build_view_bare_attributes(instance, ctx)
        elif rule_name == "constant":
            self._normalize_enumeration_const_value(instance)
        elif rule_name == "pathEl":
            self._set_path_el_kind(instance, ctx)
        elif rule_name == "existenceConstraint":
            self._fix_existence_constraint_attr(instance, ctx)
        elif rule_name == "contextDef":
            self._build_context_domain_pairs(instance, ctx)
        elif rule_name == "metaDataBasketDef":
            self._build_metadata_basket_members(instance, ctx)
        elif rule_name == "lineType":
            self._attach_line_forms(instance, ctx)
        elif rule_name == "formattedType":
            self._set_formatted_template(instance, ctx)
        if association_attribute and ctx.MANDATORY() is not None:
            self._pending_mandatory_overrides.append(instance)

        if entry.parent and self._parent_stack:
            self.attachment.attach(
                self._parent_stack[-1],
                entry.parent.role,
                instance,
                association=entry.parent.association,
                role=entry.parent.role,
                rule=rule_name,
            )

        return instance

    def _attach_line_forms(self, instance: MetaInstance, ctx: ParserRuleContext) -> None:
        """Bind `WITH (STRAIGHTS, ARCS)` to `LineType.LineForm` (association LinesForm).

        The two predefined forms are one shared LineForm each, named after the
        keyword; a custom `LINE FORM` reference (`Model.Name`) is not bound.
        """
        form_ctx = ctx.lineForm()
        if form_ctx is None:
            return
        forms = []
        for entry in form_ctx.lineFormType():
            keyword = "ARCS" if entry.ARCS() is not None else "STRAIGHTS" if entry.STRAIGHTS() is not None else None
            if keyword is None:
                continue
            form = self._predefined_line_forms.get(keyword)
            if form is None:
                form = self.registry.new_instance("IlisMeta16.ModelData.LineForm")
                form.Name = keyword
                self._predefined_line_forms[keyword] = form
            forms.append(form)
        if forms:
            instance.LineForm = forms

    def _register_unqualified_imports(self, ctx: ParserRuleContext) -> None:
        """Record which `IMPORTS` names are prefixed with `UNQUALIFIED` in `symbol_table.unqualified_imports`.

        `Import` has no attribute for it; the keyword always precedes the name it modifies, so a positional walk works.
        """
        if not ca.has_accessor(ctx, "UNQUALIFIED"):
            return
        children = list(ctx.children or [])
        for node in ca.call_list(ctx, "UNQUALIFIED"):
            idx = children.index(node)
            if idx + 1 < len(children):
                nxt = children[idx + 1]
                if isinstance(nxt, TerminalNode):
                    model = self.forward_refs.current_model or ""
                    self.symbol_table.unqualified_imports.setdefault(model, []).append(nxt.getText())

    def _build_multi_target(self, ctx: ParserRuleContext, rule_name: str, entry: SpecEntry):
        """Build the two linked targets of a topicDef: SubModel and DataUnit.

        topicDef is the only rule that produces two targets.
        """
        instances: dict[str, MetaInstance] = {}
        for target in entry.target:
            short = target.rsplit(".", 1)[-1]
            inst = self.registry.new_instance(target)
            inst._source_ctx = ctx
            instances[short] = inst

        construction_ctx = self._construction_stack[-1] if self._construction_stack else {}
        consumed: set[int] = set()

        prefixed_bindings: dict[str, dict[str, Any]] = {short: {} for short in instances}
        for key, binding in (entry.attribute_bindings or {}).items():
            if "." in key:
                prefix, sub_key = key.split(".", 1)
                if prefix in prefixed_bindings:
                    prefixed_bindings[prefix][sub_key] = binding
                    continue
            # unprefixed key: applied to the first instance (rare, defensive)
            prefixed_bindings[next(iter(instances))][key] = binding

        submodel = instances.get("SubModel")
        if submodel is not None:
            # A TopicDef's meta-attributes belong to the SubModel, never the DataUnit; `_build_multi_target` bypasses
            # the generic hook, so this is scoped explicitly.
            self._attach_pending_meta_attributes(submodel, ctx)
            with self._pushed_context(rule_name, submodel):
                self._apply_bindings(
                    submodel, ctx, rule_name, prefixed_bindings.get("SubModel", {}), construction_ctx, consumed
                )
            self._maybe_register_symbol(submodel)

        for short, inst in instances.items():
            if short == "SubModel":
                continue
            with self._pushed_context(rule_name, inst):
                self._apply_bindings(inst, ctx, rule_name, prefixed_bindings.get(short, {}), construction_ctx, consumed)

        if submodel is not None:
            for short, inst in instances.items():
                if inst is not submodel:
                    submodel._twin = inst
                    inst._twin = submodel

        self._sweep_unclaimed_children(ctx, consumed)

        self._apply_topic_oid_clauses(ctx, instances)

        if entry.parent and self._parent_stack:
            for inst in instances.values():
                self.attachment.attach(
                    self._parent_stack[-1],
                    entry.parent.role,
                    inst,
                    association=entry.parent.association,
                    role=entry.parent.role,
                    rule=rule_name,
                )

        return submodel if submodel is not None else next(iter(instances.values()))

    # --- Special case: multi_declaration (domainDef only) ---
    def _build_multi_declaration(self, ctx: ParserRuleContext, rule_name: str, entry: SpecEntry):
        """Split a domainDef() into its N declarations sharing one `DOMAIN` keyword.

        Splits `ctx.children` on SEMI; `RESTRICTION(...)` has internal SEMIs, so LPAR/RPAR depth is tracked.
        """
        children = list(ctx.children or [])
        segments: list[list[Any]] = []
        current: list[Any] = []
        paren_depth = 0
        for child in children:
            current.append(child)
            if isinstance(child, TerminalNode):
                if child.symbol.type == InterlisParser.LPAR:
                    paren_depth += 1
                elif child.symbol.type == InterlisParser.RPAR:
                    paren_depth -= 1
                elif child.symbol.type == InterlisParser.SEMI and paren_depth == 0:
                    segments.append(current)
                    current = []
        if current:
            segments.append(current)

        results = []
        for segment in segments:
            name_node = next(
                (c for c in segment if isinstance(c, TerminalNode) and c.symbol.type == InterlisParser.Name), None
            )
            if name_node is None:
                continue  # segment with no Name (e.g. leftover before the 1st token) - nothing to build
            mandatory = any(isinstance(c, TerminalNode) and c.symbol.type == InterlisParser.MANDATORY for c in segment)
            content_node = next(
                (
                    c
                    for c in segment
                    if isinstance(c, ParserRuleContext) and self._rule_name(c) in ("type", "numeric", "enumeration")
                ),
                None,
            )
            if content_node is None:
                class_token = next(
                    (c for c in segment if isinstance(c, TerminalNode) and c.symbol.type == InterlisParser.CLASS),
                    None,
                )
                if class_token is None:
                    # `D2 EXTENDS D = MANDATORY;` (eCH-0031 3.8, 'MANDATORY' [ Type ]): the base's type, mandatory.
                    instance = self._build_domain_mandatory_only(segment, rule_name) if mandatory else None
                    if not isinstance(instance, MetaInstance):
                        continue
                    instance.Name = name_node.getText()
                    instance.Mandatory = True
                    instance._source_ctx = name_node.symbol
                    self._maybe_register_symbol(instance)
                    self._attach_domain_extends(instance, segment, rule_name)
                    self._apply_domain_modifiers(instance, segment, name_node)
                    self._attach_domain_constraints(instance, segment, rule_name)
                    if entry.parent and self._parent_stack:
                        self.attachment.attach(
                            self._parent_stack[-1],
                            entry.parent.role,
                            instance,
                            association=entry.parent.association,
                            role=entry.parent.role,
                            rule=rule_name,
                        )
                    results.append(instance)
                    continue
                instance = self._build_domain_class_restriction(segment, rule_name)
                if not isinstance(instance, MetaInstance):
                    continue
                # This instance is built directly and has no _source_ctx; fall back to the segment's Name token
                # (_ctx_line handles both shapes).
                instance._source_ctx = instance._source_ctx or name_node.symbol
                self._attach_pending_meta_attributes(instance, instance._source_ctx)
                self._attach_domain_extends(instance, segment, rule_name)
                self._apply_domain_modifiers(instance, segment, name_node)
                if getattr(instance, "Name", None) is None:
                    instance.Name = name_node.getText()
                    self._maybe_register_symbol(instance)
                if mandatory and getattr(instance, "Mandatory", None) is None:
                    instance.Mandatory = True
                if entry.parent and self._parent_stack:
                    self.attachment.attach(
                        self._parent_stack[-1],
                        entry.parent.role,
                        instance,
                        association=entry.parent.association,
                        role=entry.parent.role,
                        rule=rule_name,
                    )
                results.append(instance)
                continue
            content_rule = self._rule_name(content_node)
            if content_rule == "type":
                # `_rule_name` sees the ANTLR class `TypeContext`, while the grammar rule method is `type_()` (renamed
                # to avoid the builtin), so match on "type" here.
                instance = self.visit(content_node)
            else:
                target = (
                    "IlisMeta16.ModelData.NumType" if content_rule == "numeric" else "IlisMeta16.ModelData.EnumType"
                )
                instance = self.visit_wrapped(content_node, target, rule_name)
            if not isinstance(instance, MetaInstance):
                continue
            self._attach_domain_extends(instance, segment, rule_name)
            self._apply_domain_modifiers(instance, segment, name_node)
            self._attach_domain_constraints(instance, segment, rule_name)
            if getattr(instance, "Name", None) is None:
                instance.Name = name_node.getText()
                self._maybe_register_symbol(instance)
            if mandatory and getattr(instance, "Mandatory", None) is None:
                instance.Mandatory = True
            if entry.parent and self._parent_stack:
                self.attachment.attach(
                    self._parent_stack[-1],
                    entry.parent.role,
                    instance,
                    association=entry.parent.association,
                    role=entry.parent.role,
                    rule=rule_name,
                )
            results.append(instance)

        if not results:
            return None
        return results[0] if len(results) == 1 else results

    @staticmethod
    def _set_formatted_template(instance: MetaInstance, ctx: ParserRuleContext) -> None:
        """Set `FormattedType.Format` to the template of `FORMAT BASED ON <struct> (<template>)`."""
        template_ctx = ctx.formatDef()
        if template_ctx is None:
            return
        skipped = (InterlisParser.LPAR, InterlisParser.RPAR, InterlisParser.INHERITANCE)
        parts = [
            c.getText()
            for c in template_ctx.children or []
            if getattr(getattr(c, "symbol", None), "type", None) not in skipped
        ]
        instance.Format = "".join(parts)

    @staticmethod
    def _apply_domain_modifiers(instance: MetaInstance, segment: list[Any], name_node: TerminalNode) -> None:
        """Set `Abstract`/`Final`/`Generic` from a domain's `(ABSTRACT, FINAL, GENERIC)` clause (ExtendableME)."""
        attributes = {
            InterlisParser.ABSTRACT: "Abstract",
            InterlisParser.FINAL: "Final",
            InterlisParser.GENERIC: "Generic",
        }
        start = segment.index(name_node) + 1
        for child in segment[start:]:
            if not isinstance(child, TerminalNode):
                break
            if child.symbol.type in (InterlisParser.ASSIGN, InterlisParser.EXTENDS):
                break
            if child.symbol.type in attributes:
                setattr(instance, attributes[child.symbol.type], True)

    def _build_metadata_basket_members(self, instance: MetaInstance, ctx: ParserRuleContext) -> None:
        """Materialize `MetaObjectDef` members for each `OBJECTS OF <Class>: <Name>(, <Name>)*` clause.

        `Name()` flattens every name token with no clause grouping, so clauses are split from the raw
        children; otherwise a `Sign := {member}` reference to a basket member would never resolve.
        """
        # The Class name is unqualified and resolved against the basket's own `~ topicRef` scope.
        topic_ref_ctx = ctx.topicRef()
        basket_topic_hint = topic_ref_ctx.getText() if topic_ref_ctx is not None else None
        clauses: list[list[Any]] = []
        current: list[Any] = []
        for child in ctx.children or []:
            if isinstance(child, TerminalNode) and child.symbol.type == InterlisParser.OBJECTS:
                if current:
                    clauses.append(current)
                current = []
            current.append(child)
        if current:
            clauses.append(current)
        for clause in clauses:
            if not (isinstance(clause[0], TerminalNode) and clause[0].symbol.type == InterlisParser.OBJECTS):
                continue  # leading segment before the 1st OBJECTS token (basket name/EXTENDS/TILDE topicRef)
            names = [c for c in clause if isinstance(c, TerminalNode) and c.symbol.type == InterlisParser.Name]
            if len(names) < 2:
                continue  # need a Class-Name plus at least one Member-Name
            class_name, member_names = names[0].getText(), names[1:]
            for member_token in member_names:
                metaobj = self.registry.new_instance("IlisMeta16.ModelData.MetaObjectDef")
                metaobj.Name = member_token.getText()
                metaobj.IsRefSystem = getattr(instance, "Kind", None) == "RefSystemB"
                self._maybe_register_symbol(metaobj)
                self.attachment.attach(
                    instance,
                    "Member",
                    metaobj,
                    association="MetaBasketMembers",
                    role="Member",
                    rule="metaDataBasketDef",
                )
                class_ref = ForwardRef(
                    name=class_name,
                    resolves_to_hint="Class",
                    rule="metaDataBasketDef",
                    home_model=self._current_model_name(),
                    topic_extends_hint=basket_topic_hint,
                )
                self.attachment.attach(
                    metaobj, "Class", class_ref, association="MetaObjectClass", role="Class", rule="metaDataBasketDef"
                )
                self.forward_refs.register_pending(class_ref, metaobj, "Class")

    def _attach_domain_extends(self, instance: MetaInstance, segment: list[Any], rule_name: str) -> None:
        """Attach `Super` for a `DOMAIN X EXTENDS Y = ...;` declaration, from the optional clause before `EQ`.

        Needed so `schema.line_coord_type` can walk the EXTENDS chain; unresolved targets degrade gracefully.
        """
        extends_idx = next(
            (
                i
                for i, c in enumerate(segment)
                if isinstance(c, TerminalNode) and c.symbol.type == InterlisParser.EXTENDS
            ),
            None,
        )
        if extends_idx is None:
            return
        domain_ref_node = next(
            (
                c
                for c in segment[extends_idx + 1 :]
                if isinstance(c, ParserRuleContext) and self._rule_name(c) == "domainRef"
            ),
            None,
        )
        if domain_ref_node is None:
            return
        value = self.visit(domain_ref_node)
        if not isinstance(value, ForwardRef):
            return
        value.graceful = True
        self.attachment.attach(instance, "Super", value, association="Inheritance", role="Super", rule=rule_name)
        self.forward_refs.register_pending(value, instance, "Super")

    def _build_domain_mandatory_only(self, segment: list[Any], rule_name: str) -> MetaInstance | None:
        """A new instance of the `EXTENDS` base's own type class, for `D2 EXTENDS D = MANDATORY;`.

        The base must already be declared (the usual order); otherwise nothing is built rather than a guessed type.
        """
        ref_node = next(
            (c for c in segment if isinstance(c, ParserRuleContext) and self._rule_name(c) == "domainRef"), None
        )
        ref = self.visit(ref_node) if ref_node is not None else None
        name = ref.name if isinstance(ref, ForwardRef) else None
        base = self.symbol_table.resolve(name, home_model=self._current_model_name()) if name else None
        if not isinstance(base, MetaInstance):
            return None
        return self.registry.new_instance(base._qualified_class)

    def _attach_domain_constraints(self, instance: MetaInstance, segment: list[Any], rule_name: str) -> None:
        """Build `CONSTRAINTS Name: Logical-Expression, ...` (eCH-0031 3.8) as mandatory constraints on the domain."""
        children = list(segment)
        start = next(
            (
                i
                for i, c in enumerate(children)
                if isinstance(c, TerminalNode) and c.symbol.type == InterlisParser.CONSTRAINTS
            ),
            None,
        )
        if start is None:
            return
        name = None
        for child in children[start + 1 :]:
            if isinstance(child, TerminalNode) and child.symbol.type == InterlisParser.Name:
                name = child.getText()
            elif isinstance(child, ParserRuleContext) and self._rule_name(child) == "expression":
                constraint = self.registry.new_instance("IlisMeta16.ModelData.SimpleConstraint")
                constraint.Name = name
                constraint.Kind = "MandC"
                constraint.LogicalExpression = self.visit(child)
                self.attachment.attach(
                    instance,
                    "Constraint",
                    constraint,
                    association="DomainConstraint",
                    role="Constraint",
                    rule=rule_name,
                )

    def _build_domain_class_restriction(self, segment: list[Any], rule_name: str) -> MetaInstance:
        """Build `DOMAIN X = CLASS RESTRICTION(A; B; ...);` as a `ReferenceType` with `External=False`.

        Attaches every `classOrAssociationRef` in the segment, since XTF decoding needs all candidates.
        """
        instance = self.registry.new_instance("IlisMeta16.ModelData.ReferenceType")
        instance.External = False
        refs = [
            c for c in segment if isinstance(c, ParserRuleContext) and self._rule_name(c) == "classOrAssociationRef"
        ]
        for ref_ctx in refs:
            value = self.visit(ref_ctx)
            if value is None:
                continue
            self.attachment.attach(
                instance,
                "BaseClass",
                value,
                association="BaseClass",
                role="BaseClass",
                rule=rule_name,
            )
            if isinstance(value, ForwardRef):
                self.forward_refs.register_pending(value, instance, "BaseClass")
        return instance

    def _build_type_string_range(self, ctx: ParserRuleContext) -> MetaInstance | None:
        """Build type()'s 3rd alternative, a bare `STRING .. STRING` domain, as a `FormattedType`.

        Built by hand because `ctx.getAltNumber()` is always 0 for unlabeled alternatives in this grammar.
        """
        strings = [
            c for c in (ctx.children or []) if isinstance(c, TerminalNode) and c.symbol.type == InterlisParser.STRING
        ]
        if len(strings) != 2:
            return None
        instance = self.registry.new_instance("IlisMeta16.ModelData.FormattedType")
        instance.Min = unquote_interlis_string(strings[0].getText())
        instance.Max = unquote_interlis_string(strings[1].getText())
        return instance

    def _build_control_points_ref(self, ctx: ParserRuleContext, rule_name: str) -> ForwardRef | None:
        """Build a ForwardRef from `controlPoints()` (`VERTEX Name(DOT Name)*`) using the `Name` tokens only.

        `ctx.getText()` would include the `VERTEX` token, so the generic `_resolve_or_defer` does not fit.
        """
        names = [n.getText() for n in ca.call_list(ctx, "Name")]
        if not names:
            return None
        hints = self._expand_kind_hint("CoordType")
        return ForwardRef(
            name=".".join(names),
            resolves_to_hint=hints or None,
            rule=rule_name,
            home_model=self._current_model_name(),
            topic_extends_hint=self._current_topic_extends_hint(ctx),
        )

    def _build_local_uniqueness_def(self, ctx: ParserRuleContext) -> dict[str, Any]:
        """Build `{Kind: "LocalU", UniqueDef: [PathOrInspFactor, ...]}` for `localUniqueness()`.

        The leading `(Name COLON)?` is ambiguous for `Role: Attr` and ANTLR picks the wrong branch, so the
        shape is re-derived from the raw children.
        """
        children = list(ctx.getChildren())[3:]  # skip LPAR LOCAL RPAR
        colon_index = next(
            (
                i
                for i, c in enumerate(children)
                if isinstance(c, TerminalNode) and c.symbol.type == InterlisParser.COLON
            ),
            None,
        )
        role_tokens = children[:colon_index] if colon_index is not None else children
        attr_tokens = children[colon_index + 1 :] if colon_index is not None else []

        def path_el(name: str) -> MetaInstance:
            el = self.registry.new_instance("IlisMeta16.ModelData.PathEl")
            el.Kind = "ReferenceAttr"
            el.Ref = name
            return el

        role_names = [
            t.getText() for t in role_tokens if isinstance(t, TerminalNode) and t.symbol.type == InterlisParser.Name
        ]
        attr_names = [
            t.getText() for t in attr_tokens if isinstance(t, TerminalNode) and t.symbol.type == InterlisParser.Name
        ]

        def factor(names: list[str]) -> MetaInstance:
            instance = self.registry.new_instance("IlisMeta16.ModelData.PathOrInspFactor")
            instance.PathEls = [
                path_el(name) for name in names
            ]  # a fresh PathEl per factor - never shared across UniqueDef entries
            return instance

        if not role_names:
            return {"Kind": "LocalU"}
        if not attr_names:
            return {"Kind": "LocalU", "UniqueDef": [factor(role_names)]}
        return {"Kind": "LocalU", "UniqueDef": [factor([*role_names, name]) for name in attr_names]}

    def _build_enumeration_tree(self, ctx: ParserRuleContext, rule_name: str, entry: SpecEntry) -> None:
        """Build the EnumNode tree of an `enumeration()`.

        Per-element self-attachment can only form a linear chain and would lose nested children. A top-level
        enumeration gets a synthetic `TOP` node; a nested one attaches to its enclosing element.
        """
        parent_context = self._parent_stack[-1] if self._parent_stack else None
        is_top_level = (
            isinstance(parent_context, MetaInstance)
            and parent_context._qualified_class == "IlisMeta16.ModelData.EnumType"
        )

        if is_top_level:
            top = self.registry.new_instance("IlisMeta16.ModelData.EnumNode")
            top.Name = "TOP"
            self.attachment.attach(
                parent_context,
                "TopNode",
                top,
                association="TopNode",
                role="TopNode",
                rule=rule_name,
            )
            children_target = top
            final_target = parent_context
        else:
            children_target = parent_context
            final_target = parent_context

        if children_target is not None and ca.has_accessor(ctx, "enumElement"):
            for el_ctx in ca.call_list(ctx, "enumElement"):
                node = self.visit(el_ctx)
                if isinstance(node, MetaInstance):
                    self.attachment.attach(
                        children_target,
                        "Node",
                        node,
                        association="SubNode",
                        role="Node",
                        rule=rule_name,
                    )

        construction_ctx = self._construction_stack[-1] if self._construction_stack else {}
        consumed: set[int] = set()
        final_binding = entry.attribute_bindings.get("Final") if entry.attribute_bindings else None
        if final_target is not None and isinstance(final_binding, dict):
            final_value = self._resolve_binding_value(
                ctx, rule_name, "Final", final_binding, construction_ctx, consumed
            )
            if final_value:
                self.attachment.attach(final_target, "Final", final_value, rule=rule_name)
        order_binding = entry.attribute_bindings.get("Order") if entry.attribute_bindings else None
        if is_top_level and isinstance(order_binding, dict):
            order_value = self._resolve_binding_value(
                ctx, rule_name, "Order", order_binding, construction_ctx, consumed
            )
            if order_value:
                self.attachment.attach(parent_context, "Order", order_value, rule=rule_name)
        return None

    # --- Strategy 2: Conditional ---
    def _build_conditional(self, ctx: ParserRuleContext, rule_name: str, entry: SpecEntry):
        for token_or_rule, branch in (entry.when_present or {}).items():
            if not ca.has_accessor(ctx, token_or_rule):
                continue
            node = ca.call(ctx, token_or_rule)
            if node is None or (isinstance(node, list) and not node):
                # Repeatable operator tokens return an empty list, never None, when absent; checking `is None` alone
                # would wrap a single operand in a spurious CompoundExpr.
                continue
            instance = self.registry.new_instance(branch.target)
            instance._source_ctx = ctx
            if branch.discriminant:
                value = branch.discriminant.model_extra.get("value")
                if rule_name == "term2" and token_or_rule == "relation":
                    # `Operation=Relation` is a group kind: use the concrete value relation() matched
                    # (`_relation_kind`), since its own binding cannot match composed accessor names.
                    value = self._relation_kind(node) or value
                setattr(instance, branch.discriminant.attribute, value)

            construction_ctx = self._construction_stack[-1] if self._construction_stack else {}
            consumed: set[int] = set()
            with self._pushed_context(rule_name, instance):
                if isinstance(node, ParserRuleContext):
                    # `token_or_rule` names a real grammar rule with its own spec entry: visit it and merge its bag into
                    # `instance`, as visit_wrapped does.
                    self._merge_bag_into_instance(instance, self.visit(node), rule_name)
                if entry.attribute_bindings:
                    self._apply_bindings(instance, ctx, rule_name, entry.attribute_bindings, construction_ctx, consumed)
                if rule_name == "predicate" and token_or_rule == "DEFINED":
                    self._set_defined_subexpression(instance, ctx)
                if rule_name == "factor" and token_or_rule == "INTERLIS":
                    self._set_predefined_function_call(instance, ctx)
            return instance

        # No when_present branch matched: plain pass-through. The rule's attribute_bindings describe the matched
        # branch, so a plain sweep of unclaimed children is used instead of the generic bag.
        consumed: set[int] = set()
        sweep_results = self._sweep_unclaimed_children(ctx, consumed)
        real_results = [v for _rule, v in sweep_results if v is not None]
        if real_results:
            return real_results[0] if len(real_results) == 1 else real_results
        return None

    # --- Strategy 3: Relay (Container / Dispatcher) ---
    def _relay(self, ctx: ParserRuleContext, rule_name: str, entry: SpecEntry):
        # multi_declaration: domainDef() loops over N declarations sharing one DOMAIN keyword; see
        # _build_multi_declaration.
        if entry.multi_declaration:
            return self._build_multi_declaration(ctx, rule_name, entry)

        # enumeration() needs a dedicated tree builder (see _build_enumeration_tree).
        if rule_name == "enumeration":
            return self._build_enumeration_tree(ctx, rule_name, entry)

        # type(): its 3rd bare alternative `STRING DOTDOT STRING` has no sub-rule or usable binding; see
        # _build_type_string_range.
        if rule_name == "type" and not ca.is_present(ctx, "baseType") and not ca.is_present(ctx, "lineType"):
            instance = self._build_type_string_range(ctx)
            if instance is not None:
                return instance

        # controlPoints(): see _build_control_points_ref.
        if rule_name == "controlPoints":
            return self._build_control_points_ref(ctx, rule_name)

        # localUniqueness() is ambiguous in the grammar, so UniqueDef is built from raw children.
        if rule_name == "localUniqueness":
            return self._build_local_uniqueness_def(ctx)

        # children: visit every occurrence (e.g. definitions -> classDef*, topicDef*).
        if entry.children:
            for child_rule in entry.children:
                if not ca.has_accessor(ctx, child_rule):
                    continue
                for node in ca.call_list(ctx, child_rule):
                    self.visit(node)
            return None

        # dispatches_to: if no named rule matched, fall through to the rule's own attribute_bindings rather than
        # returning None.
        if entry.dispatches_to:
            for name in entry.dispatches_to:
                if ca.has_accessor(ctx, name):
                    node = ca.call(ctx, name)
                    if node is not None:
                        return self.visit(node) if isinstance(node, ParserRuleContext) else node.getText()

        # Neither children nor dispatches_to matched: compute the rule's own bindings (exposed to the construction
        # context), then sweep the unclaimed children.
        bag: dict[str, Any] = {}
        consumed: set[int] = set()
        construction_ctx = self._construction_stack[-1] if self._construction_stack else {}
        if entry.attribute_bindings:
            for key, binding in entry.attribute_bindings.items():
                if not isinstance(binding, dict):
                    continue
                if "target" in binding:
                    bag[key] = self._build_nested(ctx, rule_name, key, binding, construction_ctx, consumed)
                    continue
                if not isinstance(binding.get("source"), dict):
                    continue  # notes-only entry (e.g. _dispatch): nothing to compute
                bag[key] = self._resolve_binding_value(ctx, rule_name, key, binding, construction_ctx, consumed)

        real_values = {k: v for k, v in bag.items() if v is not None}

        # A bag key may already hold a concrete instance (e.g. from a `wrap:` binding); that is the rule's content.
        # Hollow (all-empty) instances are skipped so they do not hide the bag from visit_wrapped.
        wrapped = [v for v in real_values.values() if isinstance(v, MetaInstance) and not self._is_hollow(v)]
        if len(wrapped) == 1:
            self._apply_sibling_bag_values(wrapped[0], real_values)
            return wrapped[0]

        if real_values:
            self._construction_stack.append({**construction_ctx, **real_values})
        try:
            sweep_results = self._sweep_unclaimed_children(ctx, consumed)
        finally:
            if real_values:
                self._construction_stack.pop()

        # A visited unclaimed child usually carries the rule's real content and wins over the bag of own values.
        real_results = [v for _rule, v in sweep_results if v is not None]
        if real_results:
            result = real_results[0] if len(real_results) == 1 else real_results
            if isinstance(result, MetaInstance) and real_values:
                self._apply_sibling_bag_values(result, real_values)
            return result

        if len(bag) == 1:
            ((only_key, only_value),) = bag.items()
            if only_key.startswith("_") or not entry.feeds_into:
                # Unwrap to the bare value for a single notes-only/relay key or a rule with no `feeds_into:`; the
                # parent's binding consumes the bare value directly.
                return only_value
            # A single real key on a rule with `feeds_into:` must stay a {key: value} bag: a Conditional branch merges
            # it via `_merge_bag_into_instance`, which requires a dict.
        if bag:
            return bag
        return None

    # --- Strategy 4: Reference (produces a ForwardRef) ---
    def _resolve_or_defer(self, ctx: ParserRuleContext, rule_name: str, entry: SpecEntry):
        # restrictedStructureRef() has a project-specific `type_()` alternative (an anonymous inline type such as
        # "TEXT*50"), which is not a name to resolve: relay to building the inline type.
        has_structure_ref = ca.has_accessor(ctx, "structureRef") and bool(ca.call_list(ctx, "structureRef"))
        # The same holds for a `REFERENCE TO` element (`BAG OF REFERENCE TO X`,
        # eCH-0031 3.6.1 `'OF' AttrType`): it builds its own ReferenceType.
        for inline in ("type_", "referenceAttr"):
            if ca.has_accessor(ctx, inline) and not has_structure_ref:
                node = ca.call(ctx, inline)
                if node is not None:
                    return self.visit(node)
        if has_structure_ref:
            # The base type is always structureRef(0); a trailing `RESTRICTION(...)` clause sits in the same rule, so
            # `ctx.getText()` would concatenate it into one bogus name.
            name = ca.call(ctx, "structureRef", 0).getText()
        else:
            name = ctx.getText()
        # `resolves_to` is a short name but `target` is fully qualified; SymbolTable compares short names, so
        # normalize here.
        hint = entry.resolves_to or entry.target
        if isinstance(hint, list):
            hint = [h.rsplit(".", 1)[-1] for h in hint]
        elif hint:
            hint = [hint.rsplit(".", 1)[-1]]
        else:
            hint = []
        expanded: list[str] = []
        for h in hint:
            expanded.extend(self._expand_kind_hint(h))
        return ForwardRef(
            name=name,
            resolves_to_hint=expanded or None,
            rule=rule_name,
            home_model=self._current_model_name(),
            topic_extends_hint=self._current_topic_extends_hint(ctx),
        )

    def _current_topic_extends_hint(self, ctx: ParserRuleContext) -> str | None:
        """Return the text of the enclosing TOPIC's EXTENDS target, or None if there is none.

        Read from the parse tree rather than built state (`DataUnit.Super`) to avoid depending on resolution order.
        """
        node = ctx.parentCtx
        while node is not None and type(node).__name__ != "TopicDefContext":
            node = node.parentCtx
        if node is None or node.EXTENDS() is None:
            return None
        refs = node.topicRef()
        if not refs:
            return None
        return refs[0].getText()

    def _current_model_name(self) -> str | None:
        """Return the name of the MODEL enclosing the current construction (disambiguates multi-MODEL files)."""
        for inst in reversed(self._parent_stack):
            if inst._qualified_class == "IlisMeta16.ModelData.Model":
                return getattr(inst, "Name", None)
        return None

    def _expand_kind_hint(self, short_name: str) -> list[str]:
        """Expand an abstract metamodel class hint (e.g. "DomainType") into its concrete subclasses.

        `SymbolTable.resolve` compares classes exactly, so an abstract hint would never match.
        """
        qualified = next((qn for qn in self.schema.uml.qualified if qn.rsplit(".", 1)[-1] == short_name), None)
        if qualified is None:
            return [short_name]
        element = self.schema.uml.qualified[qualified]
        if not element.get("abstract"):
            return [short_name]
        descendants = [
            qn.rsplit(".", 1)[-1]
            for qn, el in self.schema.uml.qualified.items()
            if qualified in (el.get("all_superclasses") or [])
        ]
        return descendants or [short_name]

    # --- Applying attribute_bindings ---
    def _apply_bindings(
        self,
        instance: MetaInstance,
        ctx: ParserRuleContext,
        rule_name: str,
        bindings: dict[str, Any],
        construction_ctx: dict,
        consumed: set[int],
    ) -> None:
        for key, binding in bindings.items():
            if not isinstance(binding, dict):
                continue
            if (
                binding.get("status") in ("not_applicable", "unresolved")
                and "source" not in binding
                and "target" not in binding
            ):
                continue

            try:
                self._apply_one_binding(instance, ctx, rule_name, key, binding, construction_ctx, consumed)
            except BuildError:
                source = binding.get("source") if isinstance(binding.get("source"), dict) else {}
                if source.get("optional") or binding.get("optional"):
                    # An optional binding whose resolution or attachment failed does not block the rest of the
                    # construction.
                    continue
                raise

    def _apply_one_binding(
        self,
        instance: MetaInstance,
        ctx: ParserRuleContext,
        rule_name: str,
        key: str,
        binding: dict,
        construction_ctx: dict,
        consumed: set[int],
    ) -> None:
        if "for_each" in binding:
            self._apply_for_each_binding(instance, ctx, rule_name, key, binding, construction_ctx, consumed)
            return

        if "target" in binding:
            nested = self._build_nested(ctx, rule_name, key, binding, construction_ctx, consumed)
            if nested is None:
                return
            self.attachment.attach(
                instance,
                key,
                nested,
                association=binding.get("association"),
                role=binding.get("role"),
                rule=rule_name,
            )
            return

        value = self._resolve_binding_value(ctx, rule_name, key, binding, construction_ctx, consumed)
        if key.startswith("_"):
            # Internal/documentation-only key (e.g. `_dispatch`): never a real attribute or role name, so only the
            # literal attach is skipped; the computation above is kept for side effects.
            return
        if value is None:
            # Nothing to attach: no source, an absent optional value, or a rule visited only for side effects.
            return
        if isinstance(value, list) and not value:
            # Empty multi-value list (nothing present in the .ili): nothing to attach.
            return
        if isinstance(value, ForwardRef):
            if binding.get("association") == "Inheritance" and binding.get("role") == "Super":
                # EXTENDS: never fatal if unresolved, see
                # ForwardRef.graceful (symmetric to domainRef/BaseClass).
                value.graceful = True
                hint = value.resolves_to_hint
                hints = hint if isinstance(hint, list) else ([hint] if hint else [])
                if "SubModel" in hints:
                    # TOPIC EXTENDS: only this case resolves to a SubModel rather than an ExtendableME subclass; see
                    # ForwardRef.resolve_via_twin.
                    value.resolve_via_twin = True
            if binding.get("association") == "LineCoord" and binding.get("role") == "CoordType":
                # A named CoordType may live in an imported model that was not loaded; this is not fatal.
                value.graceful = True
            self.attachment.attach(
                instance,
                key,
                value,
                association=binding.get("association"),
                role=binding.get("role"),
                rule=rule_name,
            )
            self.forward_refs.register_pending(value, instance, self._resolved_field_name(instance, key, binding))
            return
        self.attachment.attach(
            instance,
            key,
            value,
            association=binding.get("association"),
            role=binding.get("role"),
            rule=rule_name,
        )
        if isinstance(value, list):
            # A `multi` reference binding (e.g. roleDef's `A OR B` targets):
            # each ForwardRef in the list is resolved in place.
            field = self._resolved_field_name(instance, key, binding)
            for item in value:
                if isinstance(item, ForwardRef):
                    self.forward_refs.register_pending(item, instance, field)

    def _resolved_field_name(self, instance: MetaInstance, key: str, binding: dict) -> str:
        role = binding.get("role")
        if role:
            return role
        element = self.schema.uml.qualified.get(instance._qualified_class, {})
        if self.schema.uml.attribute_exists(element, key):
            return key
        found = self.attachment._find_association_for_role(instance._qualified_class, key)
        return found[1] if found else key

    def _build_nested(
        self,
        ctx: ParserRuleContext,
        rule_name: str,
        key: str,
        binding: dict,
        construction_ctx: dict,
        consumed: set[int],
    ) -> MetaInstance | None:
        target = binding["target"]
        sub_bindings = {
            k: v
            for k, v in binding.items()
            if k not in ("target", "note", "association", "role") and isinstance(v, dict) and "source" in v
        }
        if not sub_bindings:
            # A `target:` with no real source sub-binding documents procedural logic outside the generic engine; build
            # no hollow instance.
            return None
        instance = self.registry.new_instance(target)
        instance._source_ctx = ctx
        with self._pushed_parent(instance):
            try:
                self._apply_bindings(instance, ctx, rule_name, sub_bindings, construction_ctx, consumed)
            except BuildError:
                # A nested construction is one grammar alternative among others: if a required sub-binding (e.g. a
                # BAG|LIST discriminant) fails, the alternative was not taken, so discard it instead of raising.
                return None
        return instance

    def _apply_for_each_binding(
        self,
        instance: MetaInstance,
        ctx: ParserRuleContext,
        rule_name: str,
        key: str,
        binding: dict,
        construction_ctx: dict,
        consumed: set[int],
    ) -> None:
        """Build one `target` instance per element of a resolved list (`for_each:` bindings).

        Sub-bindings read the current element via `source: {field: null, context_key: '__item__'}`.
        """
        items = self._resolve_binding_value(
            ctx, rule_name, key, {"source": binding["for_each"]}, construction_ctx, consumed
        )
        if items is None:
            return
        if not isinstance(items, list):
            items = [items]
        if not items:
            return

        target = binding["target"]
        sub_bindings = {
            k: v
            for k, v in binding.items()
            if k not in ("for_each", "target", "note", "association", "role") and isinstance(v, dict)
        }
        for item in items:
            item_ctx = {**construction_ctx, "__item__": item}
            built = self.registry.new_instance(target)
            built._source_ctx = ctx
            self._apply_bindings(built, ctx, rule_name, sub_bindings, item_ctx, consumed)
            if binding.get("association"):
                self.attachment.attach(
                    instance,
                    key,
                    built,
                    association=binding.get("association"),
                    role=binding.get("role"),
                    rule=rule_name,
                )
            else:
                # No collection role on the metamodel side (e.g. Import): keep the instances under the binding's raw key
                # so they stay reachable.
                current = getattr(instance, key, None)
                if current is None:
                    current = []
                    setattr(instance, key, current)
                current.append(built)

    def _resolve_binding_value(
        self,
        ctx: ParserRuleContext,
        rule_name: str,
        key: str,
        binding: dict,
        construction_ctx: dict,
        consumed: set[int],
    ) -> Any:
        source = binding.get("source")
        if not isinstance(source, dict):
            return None
        # Consume unconditionally: an accessor's natural presence is already exclusive to the alternative taken
        # (`alt:` is inert in this grammar, since getAltNumber() is always 0).
        for name in self._accessor_names_in_source(source):
            if ca.has_accessor(ctx, name):
                for node in ca.call_list(ctx, name):
                    consumed.add(id(node))
        rule_map = binding.get("rule") if isinstance(binding.get("rule"), dict) else binding.get("mapping")
        wrap_map = binding.get("wrap") if isinstance(binding.get("wrap"), dict) else None
        return resolve_source(
            ctx,
            source,
            rule=rule_name,
            construction_context=construction_ctx,
            builder=self,
            rule_map=rule_map,
            binding_key=key,
            wrap_map=wrap_map,
        )

    def visit_wrapped(self, node: ParserRuleContext, target: str, rule_name: str) -> MetaInstance:
        """Visit a Container/ValueObject rule (e.g. `numeric()`), wrapping its bag in a `target` instance.

        The instance is pushed before the visit because children self-attach via their own `parent:`.
        """
        instance = self.registry.new_instance(target)
        instance._source_ctx = node
        self._attach_pending_meta_attributes(instance, node)
        with self._pushed_parent(instance):
            bag = self.visit(node)

        self._merge_bag_into_instance(instance, bag, rule_name)
        return instance

    def _merge_bag_into_instance(self, instance: MetaInstance, bag: Any, rule_name: str) -> None:
        """Merge the bag from `_relay` onto `instance`, attaching every non-empty field.

        Shared by `visit_wrapped` and `_build_conditional`'s `when_present` branch.
        """
        if isinstance(bag, MetaInstance):
            # An Instance rule (e.g. `textType`) yields the built instance itself, not a bag: take its own fields.
            bag = {k: v for k, v in {**bag.__dict__, **(bag.model_extra or {})}.items() if not k.startswith("_")}
        if not isinstance(bag, dict):
            return
        for field, value in bag.items():
            if field == "Elements":
                continue  # already attached via enumElement.parent: during the visit above
            if value is None or (isinstance(value, list) and not value) or self._is_hollow(value):
                # None, empty list or "hollow" instance (all fields empty): nothing to attach.
                continue
            if isinstance(value, ForwardRef):
                self.attachment.attach(instance, field, value, rule=rule_name)
                self.forward_refs.register_pending(value, instance, self._resolved_field_name(instance, field, {}))
                continue
            self.attachment.attach(instance, field, value, rule=rule_name)

    @staticmethod
    def _is_hollow(value: Any) -> bool:
        if not isinstance(value, MetaInstance):
            return False
        fields = {**value.__dict__, **(value.model_extra or {})}
        return all(v is None or v == [] for k, v in fields.items() if not k.startswith("_"))

    @staticmethod
    def _accessor_names_in_source(source: dict) -> list[str]:
        names: list[str] = []
        field = source.get("field")
        if field:
            names.extend(n.strip() for n in field.split("|"))
        anchor = source.get("anchor")
        if anchor:
            names.append(anchor)
        if "join" in source:
            for k, v in source.items():
                if v is None and k not in ("field", "optional"):
                    names.append(k)
        return names

    def _resolve_discriminant(self, ctx, rule_name, discriminant, construction_ctx, consumed) -> Any:
        extra = discriminant.model_extra or {}
        if "value" in extra and not isinstance(extra["value"], dict):
            return extra["value"]
        if isinstance(extra.get("value"), dict):
            nested = extra["value"]
            source = nested.get("source")
            if isinstance(source, dict):
                for name in self._accessor_names_in_source(source):
                    if ca.has_accessor(ctx, name):
                        for node in ca.call_list(ctx, name):
                            consumed.add(id(node))
                rule_map = nested.get("mapping") or nested.get("rule")
                return resolve_source(
                    ctx,
                    source,
                    rule=rule_name,
                    construction_context=construction_ctx,
                    builder=self,
                    rule_map=rule_map,
                )
        if extra.get("dynamic") and isinstance(extra.get("rule"), dict):
            for token_or_rule, value in extra["rule"].items():
                if ca.has_accessor(ctx, token_or_rule) and ca.is_present(ctx, token_or_rule):
                    return value
            # `default`: the value when no listed alternative matched (a unit with no '=' is a BaseU).
            return extra.get("default")
        return None

    # --- Construction context propagated to children (`field: null`) ---
    def _push_construction_context(self, rule_name: str, instance: MetaInstance) -> None:
        ctx_dict = dict(self._construction_stack[-1]) if self._construction_stack else {}
        ctx_dict[rule_name] = instance
        name = getattr(instance, "Name", None)
        if name is not None:
            ctx_dict[f"{rule_name}.Name"] = name
        self._construction_stack.append(ctx_dict)

    def _pop_construction_context(self) -> None:
        self._construction_stack.pop()

    @contextmanager
    def _pushed_parent(self, instance: MetaInstance) -> Iterator[MetaInstance]:
        """Push `instance` onto `_parent_stack` for the scope of the `with` block, popping it on any exit."""
        self._parent_stack.append(instance)
        try:
            yield instance
        finally:
            self._parent_stack.pop()

    @contextmanager
    def _pushed_context(self, rule_name: str, instance: MetaInstance) -> Iterator[MetaInstance]:
        """Push the construction context and `_parent_stack` for `instance`, popping both on exit."""
        self._push_construction_context(rule_name, instance)
        self._parent_stack.append(instance)
        try:
            yield instance
        finally:
            self._parent_stack.pop()
            self._pop_construction_context()

    # --- Symbol table ---
    def _maybe_register_symbol(self, instance: MetaInstance) -> None:
        element = self.schema.uml.qualified.get(instance._qualified_class, {})
        if not self.schema.uml.attribute_exists(element, "Name"):
            return
        name = getattr(instance, "Name", None)
        if not name:
            return
        qualified = self._qualify_name(name)
        self.symbol_table.register(qualified, instance)

    def _register_unit_alias(self, instance: MetaInstance, ctx: ParserRuleContext) -> None:
        """Register a UNIT's bracketed short-name alias (e.g. `[m3sec]`) for the same instance.

        `Unit` has no ShortName attribute, so a later reference by alias would otherwise fail.
        """
        names = ctx.Name()
        if len(names) < 2:
            return
        alias = names[1].getText()
        self.symbol_table.register(self._qualify_name(alias), instance)

    def _fix_existence_constraint_attr(self, instance: MetaInstance, ctx: ParserRuleContext) -> None:
        """Re-wrap `ExistenceConstraint.Attr` as a `PathOrInspFactor` and keep the `REQUIRED IN` paths.

        The declarative binding only sets the raw bag, and this grammar never goes through `factor()`'s merge.
        The required paths are not part of IlisMeta16, so they stay on the instance, aligned with `ExistsIn`.
        """
        instance._required_attribute_paths = [_plain_attribute_path(path.getText()) for path in ctx.attributePath()[1:]]
        bag = getattr(instance, "Attr", None)
        if not isinstance(bag, dict):
            return
        factor = self.registry.new_instance("IlisMeta16.ModelData.PathOrInspFactor")
        self._merge_bag_into_instance(factor, bag, "existenceConstraint")
        instance.Attr = factor

    def _fix_class_extended_super(self, instance: MetaInstance, ctx: ParserRuleContext) -> None:
        """Resolve `Super` for `CLASS X (EXTENDED)`: the same-named class in the enclosing TOPIC's base topic.

        Approximates the single merged class as ordinary inheritance. The reference is fully qualified
        because a bare name is ambiguous and could resolve back to this same instance.
        """
        if getattr(instance, "Super", None) is not None:
            return  # an explicit `EXTENDS classOrStructureRef` already won - mutually exclusive per spec
        if not ca.is_present(ctx, "EXTENDED"):
            return
        name = getattr(instance, "Name", None)
        hint = self._current_topic_extends_hint(ctx)
        if not name or not hint or "." not in hint:
            return
        ref = ForwardRef(
            name=f"{hint}.{name}",
            resolves_to_hint=["Class"],
            rule="classDef",
            graceful=True,
        )
        self.attachment.attach(instance, "Super", ref, association="Inheritance", role="Super", rule="classDef")
        self.forward_refs.register_pending(ref, instance, "Super")

    _RELATION_KIND_LT_GT = {(True, False): "Less", (False, True): "Greater", (True, True): "NotEqual"}

    def _relation_kind(self, ctx: ParserRuleContext) -> str | None:
        """Resolve `relation()`'s matched alternative to its `CompoundExpr.Operation` value.

        Multi-token operators have no accessor: two `EQ` mean "==", and `<>` is `LT` and `GT` together.
        """
        if len(ca.call_list(ctx, "EQ")) >= 2:
            return "Equal"
        if ca.call(ctx, "NOT_EQ") is not None:
            return "NotEqual"
        if ca.call(ctx, "LTEQ") is not None:
            return "LessOrEqual"
        if ca.call(ctx, "GTEQ") is not None:
            return "GreaterOrEqual"
        has_lt = ca.call(ctx, "LT") is not None
        has_gt = ca.call(ctx, "GT") is not None
        return self._RELATION_KIND_LT_GT.get((has_lt, has_gt))

    def _set_path_el_kind(self, instance: MetaInstance, ctx: ParserRuleContext) -> None:
        """Resolve `pathEl()`'s matched alternative to its `PathEl.Kind` value.

        Several alternatives share a leading `Name`: unambiguous ones are checked first, then `COLON` (Role),
        a double `EQ` (MetaObject) or `LSBR` (ViewBase); anything else is ReferenceAttr.
        """
        if ca.call(ctx, "THIS") is not None:
            instance.Kind = "This"
        elif ca.call(ctx, "THISAREA") is not None:
            instance.Kind = "ThisArea"
        elif ca.call(ctx, "THATAREA") is not None:
            instance.Kind = "ThatArea"
        elif ca.call(ctx, "PARENT") is not None:
            instance.Kind = "Parent"
        elif ca.call(ctx, "associationPath") is not None:
            instance.Kind = "AssocPath"
        elif ca.call(ctx, "attributeRef") is not None:
            instance.Kind = "Attribute"
        elif ca.call(ctx, "COLON") is not None:
            instance.Kind = "Role"
        elif len(ca.call_list(ctx, "EQ")) >= 2:
            instance.Kind = "MetaObject"
        elif ca.call(ctx, "LSBR") is not None:
            instance.Kind = "ViewBase"
        else:
            instance.Kind = "ReferenceAttr"

    def _normalize_enumeration_const_value(self, instance: MetaInstance) -> None:
        """Join a `Constant(Type=Enumeration)`'s raw `enumerationConst` bag into a dotted-path string.

        The generic `Value` binding keeps the bag (`Value` segments plus an `Others` flag) as-is.
        """
        value = getattr(instance, "Value", None)
        if not isinstance(value, dict):
            return
        segments = value.get("Value") or []
        if isinstance(segments, str):
            segments = [segments]
        if value.get("Others"):
            segments = [*segments, "OTHERS"]
        instance.Value = ".".join(segments)

    def _set_defined_subexpression(self, instance: MetaInstance, ctx: ParserRuleContext) -> None:
        """Set `UnaryExpr(Operation=Defined).SubExpression` from the `DEFINED (factor)` operand.

        `factor` has no attribute name for the generic binding; a `Factor` is an `Expression`, so assignment works.
        """
        factor_node = ca.call(ctx, "factor")
        if factor_node is None:
            return
        value = self.visit(factor_node)
        if value is not None:
            instance.SubExpression = value

    def _set_predefined_function_call(self, instance: MetaInstance, ctx: ParserRuleContext) -> None:
        """Set `FunctionCall.Function`/`.Arguments` for a predefined call (`INTERLIS.len(...)`, `factor` alt4).

        `Function` joins `INTERLIS` and the matched name token; each `argument()` builds its own `ActualArgument`.
        """
        names = ctx.Name()
        name_token = names[0] if names else (ctx.URI() or ctx.UUIDOID())
        if name_token is None:
            return
        instance.Function = f"INTERLIS.{name_token.getText()}"
        instance.Arguments = [a for a in (self.visit(arg) for arg in ctx.argument()) if a is not None]

    def _qualify_name(self, name: str) -> str:
        parts = [getattr(inst, "Name", None) for inst in self._parent_stack if getattr(inst, "Name", None)]
        parts.append(name)
        return ".".join(parts)

    def _apply_sibling_bag_values(self, result: MetaInstance, real_values: dict) -> None:
        """Apply sibling bag values onto `result` for the attributes it inherits or owns.

        Needed when a Container's real content is another bag value (e.g. a DOMAIN named via a bare
        enumeration), otherwise its `Name` is lost and it is never registered.
        """
        element = self.schema.uml.qualified.get(result._qualified_class, {})
        registered_name = False
        for key, value in real_values.items():
            if value is result:
                continue
            if getattr(result, key, None) is not None:
                continue
            if not self.schema.uml.attribute_exists(element, key):
                continue
            setattr(result, key, value)
            if key == "Name":
                registered_name = True
        if registered_name:
            self._maybe_register_symbol(result)

    # --- Children not claimed by attribute_bindings ---
    def _sweep_unclaimed_children(self, ctx: ParserRuleContext, consumed: set[int]) -> list[tuple[str, Any]]:
        results = []
        for child in ctx.children or []:
            if isinstance(child, ParserRuleContext) and id(child) not in consumed:
                results.append((self._rule_name(child), self.visit(child)))
        return results

    def _attach_unclaimed_results(
        self, instance: MetaInstance, sweep_results: list[tuple[str, Any]], rule_name: str
    ) -> None:
        """Best-effort attach of unclaimed children with no `parent:`, via the association connecting the classes."""
        for child_rule, value in sweep_results:
            if value is None:
                continue
            if child_rule == "constraintDef":
                # constraintDef is a Container whose dispatched constraints already self-attached via their own
                # `parent:`; attaching again here would duplicate it (viewDef has constraintDef as a direct child).
                continue
            if child_rule == "formationDef":
                # viewDef-only dispatcher relaying to projection()/join()/...: renamedViewableRef already self-attached
                # a single RenamedBaseView via its own `parent:`, so re-attaching it here would duplicate it.
                continue
            child_entry = self.spec.get(child_rule)
            if child_entry is not None and child_entry.parent:
                continue  # already self-attached via its own parent:
            if isinstance(value, dict):
                # The unclaimed rule is a bag: attach each non-empty key onto `instance` under its own name; a key that
                # does not attach (e.g. Mandatory) is retried on a sibling MetaInstance value from the same bag.
                if child_rule == "attrTypeDef":
                    self._resolve_predefined_type_sentinel(instance, value)
                siblings = [v for v in value.values() if isinstance(v, MetaInstance)]
                has_unresolved_sibling = any(isinstance(v, ForwardRef) for v in value.values())
                for key, sub_value in value.items():
                    if (
                        sub_value is None
                        or (isinstance(sub_value, list) and not sub_value)
                        or self._is_hollow(sub_value)
                    ):
                        continue
                    if key.startswith("_"):
                        # Internal key (e.g. _collection): never a real attribute or role name, so look for the
                        # association connecting the two known classes instead. Best-effort.
                        if isinstance(sub_value, MetaInstance):
                            found = self.attachment.find_association_connecting(
                                instance._qualified_class, sub_value._qualified_class
                            )
                            if found is not None:
                                _assoc_name, role, upper = found
                                self.attachment._set_field(instance, role, sub_value, upper)
                        continue
                    attached_on = None
                    attached_field = key
                    try:
                        self.attachment.attach(instance, key, sub_value, rule=rule_name)
                        attached_on = instance
                        attached_field = self._resolved_field_name(instance, key, {})
                    except BuildError:
                        for sibling in siblings:
                            if sibling is sub_value:
                                continue
                            try:
                                self.attachment.attach(sibling, key, sub_value, rule=rule_name)
                                attached_on = sibling
                                attached_field = self._resolved_field_name(sibling, key, {})
                                break
                            except BuildError:
                                continue
                        if attached_on is None:
                            # No Role<->(Class or DomainType) association is named `key`, so fall back to
                            # the association matching the known class.
                            candidate_classes: list[str] = []
                            if isinstance(sub_value, MetaInstance):
                                candidate_classes = [sub_value._qualified_class]
                            elif isinstance(sub_value, ForwardRef):
                                hints = (
                                    sub_value.resolves_to_hint
                                    if isinstance(sub_value.resolves_to_hint, list)
                                    else ([sub_value.resolves_to_hint] if sub_value.resolves_to_hint else [])
                                )
                                candidate_classes = [
                                    qn
                                    for hint in hints
                                    for qn in self.schema.uml.qualified
                                    if qn.rsplit(".", 1)[-1] == hint
                                ]
                            for qualified_class in candidate_classes:
                                found = self.attachment.find_association_connecting(
                                    instance._qualified_class, qualified_class
                                )
                                if found is not None:
                                    _assoc_name, role, upper = found
                                    self.attachment._set_field(instance, role, sub_value, upper)
                                    attached_on = instance
                                    attached_field = role
                                    break
                        if attached_on is None:
                            # `key`'s own attach failed (e.g. "Mandatory", not an AttrOrParam attribute). Two non-
                            # critical cases below (a `sub_value` that is itself a broken reference still raises):
                            if not isinstance(sub_value, ForwardRef) and has_unresolved_sibling:
                                # Type is an unresolved ForwardRef to a possibly shared DOMAIN: queue Mandatory
                                # so it is applied later to a private clone, never the shared instance.
                                if key == "Mandatory" and sub_value is True:
                                    self._pending_mandatory_overrides.append(instance)
                                continue
                            if not isinstance(sub_value, ForwardRef) and not siblings:
                                # No instance in the bag (e.g. HALIGNMENT/VALIGNMENT are plain tokens): drop Mandatory.
                                continue
                            raise
                    if attached_on is not None and isinstance(sub_value, ForwardRef):
                        # A ForwardRef nested inside the bag (e.g. attrTypeDef.Type via domainRef()) must be registered
                        # for deferred resolution, else it stays a literal ForwardRef.
                        self.forward_refs.register_pending(sub_value, attached_on, attached_field)
                continue
            if isinstance(value, list):
                continue
            if isinstance(value, ForwardRef):
                # A ForwardRef has no real class yet, so attach it via the association for its `resolves_to_hint`.
                hints = (
                    value.resolves_to_hint
                    if isinstance(value.resolves_to_hint, list)
                    else ([value.resolves_to_hint] if value.resolves_to_hint else [])
                )
                for hint in hints:
                    qualified_hint = next(
                        (qn for qn in self.schema.uml.qualified if qn.rsplit(".", 1)[-1] == hint), None
                    )
                    if qualified_hint is None:
                        continue
                    found = self.attachment.find_association_connecting(instance._qualified_class, qualified_hint)
                    if found is not None:
                        _assoc_name, role, upper = found
                        self.attachment._set_field(instance, role, value, upper)
                        self.forward_refs.register_pending(value, instance, role)
                        break
                continue
            if not isinstance(value, MetaInstance):
                continue
            found = self.attachment.find_association_connecting(instance._qualified_class, value._qualified_class)
            if found is not None:
                _assoc_name, role, upper = found
                self.attachment._set_field(instance, role, value, upper)
