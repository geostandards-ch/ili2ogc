"""TRANSLATION OF: positional alignment of a translation model against its base, run after reference resolution."""

from __future__ import annotations

import warnings
from typing import TYPE_CHECKING

from antlr4 import ParserRuleContext
from antlr4.tree.Tree import TerminalNode

from interlis.builder import context_access as ca
from interlis.metamodel.instance import MetaInstance

if TYPE_CHECKING:
    from interlis.builder._builder_protocol import _BuilderHost

    _Base = _BuilderHost
else:
    _Base = object


class _TranslationMixin(_Base):
    def _register_translation_of(self, model: MetaInstance, ctx: ParserRuleContext) -> None:
        """Record `MODEL X (fr) ... TRANSLATION OF Y ["ver"]` for deferred alignment against `Y`.

        `Model` has no field for this clause; alignment is positional and happens in
        `_apply_pending_translations`.
        """
        if ca.call(ctx, "TRANSLATION") is None:
            return
        children = list(ctx.children or [])
        node = ca.call(ctx, "TRANSLATION")
        idx = children.index(node)
        # The base model name is two tokens after TRANSLATION.
        if idx + 2 < len(children) and isinstance(children[idx + 2], TerminalNode):
            self._pending_translations.append((model, children[idx + 2].getText()))

    _TRANSLATION_CHILD_COLLECTIONS = ("Element", "ClassAttribute", "EnumElement")

    def _apply_pending_translations(self) -> None:
        """Align every `TRANSLATION OF` model with its base and build a `Translation` for it.

        Renamed elements become translation entries on the model. An unresolved base is skipped and a structural
        mismatch aligns the common prefix, each with a warning.
        """
        pending = self._pending_translations
        self._pending_translations = []
        for model, base_name in pending:
            base = self._find_model_by_name(base_name)
            if base is None:
                warnings.warn(
                    f"[BUILD-TRANSLATION-BASE-MISSING] TRANSLATION OF {base_name!r}: base model not resolved "
                    f"(pass its directory to --repo) - no name map built for {model.Name!r}",
                    stacklevel=2,
                )
                continue
            names: dict[str, str] = {}
            elements: dict[str, str] = {}
            attributes: dict[tuple[str, str], str] = {}
            pairs: list[tuple[MetaInstance, str]] = []
            self._align_translation(base, model, base.Name or base_name, None, names, elements, attributes, pairs)

            translation = self.registry.new_instance("IlisMeta16.ModelTranslation.Translation")
            translation.Language = getattr(model, "Language", None)
            # METranslation.Of holds the base element's fully-qualified name.
            translation.Translations = [
                self.registry.new_instance(
                    "IlisMeta16.ModelTranslation.METranslation",
                    Of=base_qname,
                    TranslatedName=translated_name,
                )
                for base_qname, translated_name in pairs
            ]
            model._translation_object = translation
            model._translation = {
                "language": getattr(model, "Language", None),
                "of": base.Name,
                "names": names,  # {fully-qualified base name: translated short name}
                "elements": elements,  # {base class/view/topic/domain short name: translated}
                "attributes": attributes,  # {(owner short name, attribute short name): translated}
            }

    def _find_model_by_name(self, name: str) -> MetaInstance | None:
        def model_in(table) -> MetaInstance | None:
            for inst in table.all_registered() if table is not None else ():
                if (
                    isinstance(inst, MetaInstance)
                    and inst._qualified_class == "IlisMeta16.ModelData.Model"
                    and inst.Name == name
                ):
                    return inst
            return None

        # TRANSLATION OF is not an IMPORTS, so the base is not loaded yet; build it from the repository.
        return model_in(self.symbol_table) or model_in(self.repository.symbol_table_for(name))

    def _align_translation(
        self,
        base: MetaInstance,
        translated: MetaInstance,
        base_qname: str,
        owner_name: str | None,
        names: dict[str, str],
        elements: dict[str, str],
        attributes: dict[tuple[str, str], str],
        pairs: list[tuple[str, str]],
    ) -> None:
        b_name = getattr(base, "Name", None)
        t_name = getattr(translated, "Name", None)
        is_attr = base._qualified_class.rsplit(".", 1)[-1] == "AttrOrParam"
        if b_name is not None and t_name is not None and b_name != t_name:
            names[base_qname] = t_name
            pairs.append((base_qname, t_name))
            if is_attr and owner_name is not None:
                attributes[(owner_name, b_name)] = t_name
            elif not is_attr:
                elements[b_name] = t_name
        next_owner = b_name if base._qualified_class.rsplit(".", 1)[-1] in ("Class", "View") else owner_name
        for collection in self._TRANSLATION_CHILD_COLLECTIONS:
            b_children = [c for c in getattr(base, collection, None) or [] if isinstance(c, MetaInstance)]
            t_children = [c for c in getattr(translated, collection, None) or [] if isinstance(c, MetaInstance)]
            if len(b_children) != len(t_children):
                warnings.warn(
                    f"[BUILD-TRANSLATION-MISMATCH] TRANSLATION OF {base.Name}: {collection} count differs on "
                    f"{base_qname!r} ({len(b_children)} vs {len(t_children)}) - aligning the common prefix only",
                    stacklevel=2,
                )
            for b_child, t_child in zip(b_children, t_children):
                child_name = getattr(b_child, "Name", None)
                self._align_translation(
                    b_child,
                    t_child,
                    f"{base_qname}.{child_name}" if child_name else base_qname,
                    next_owner,
                    names,
                    elements,
                    attributes,
                    pairs,
                )
