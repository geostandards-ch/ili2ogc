"""Forward-reference resolution: each Reference-kind rule yields a ForwardRef, resolved after the visit.

`resolve_all` swaps each ForwardRef for the instance in the SymbolTable (current file) or, given a
ModelRepository, in the imported model's table; unfindable names become UnresolvedNamedReference.
"""

from dataclasses import dataclass
from typing import Any

from interlis.builder.errors import BuildError, UnresolvedNamedReference


@dataclass
class ForwardRef:
    """Placeholder set on a field, replaced by the final resolution pass."""

    name: str
    resolves_to_hint: str | list[str] | None = None
    rule: str = ""
    # References that always target an imported model (e.g. IMPORTS); bypasses the has_prefix heuristic.
    always_external: bool = False
    # MODEL enclosing the reference; disambiguates a short name declared in several models of one file.
    home_model: str | None = None
    # Text of the enclosing TOPIC's EXTENDS target, if any: its names are visible to unqualified lookups.
    topic_extends_hint: str | None = None
    # Super role of Inheritance (EXTENDS): an unresolved EXTENDS degrades to UnresolvedNamedReference
    # instead of raising BuildError. Set when the binding is applied, once the target role is known.
    graceful: bool = False
    # Super of a `TOPIC EXTENDS topicRef`: topicRef resolves to a SubModel, which is not an ExtendableME;
    # `resolve_all` substitutes its twin DataUnit after name resolution.
    resolve_via_twin: bool = False


@dataclass
class _Pending:
    ref: ForwardRef
    container: Any
    field: str
    is_list_item: bool = False


class SymbolTable:
    """Maps a qualified (or short, if unambiguous) name to a built instance.

    Populated on the fly for every Instance-kind instance built whose class
    has a Name attribute (direct or inherited).
    """

    def __init__(self):
        self._qualified: dict[str, Any] = {}
        self._by_short_name: dict[str, list[Any]] = {}
        # Models imported via `IMPORTS UNQUALIFIED`, per importing MODEL (e.g. {"Foo": ["INTERLIS"]}).
        self.unqualified_imports: dict[str, list[str]] = {}

    def register(self, qualified_name: str, instance: Any) -> None:
        self._qualified[qualified_name] = instance
        short = qualified_name.rsplit(".", 1)[-1]
        self._by_short_name.setdefault(short, []).append(instance)

    def all_registered(self) -> list[Any]:
        """Return all registered instances, deduplicated by identity (aliases share one object)."""
        seen: set[int] = set()
        result = []
        for instance in self._qualified.values():
            if id(instance) not in seen:
                seen.add(id(instance))
                result.append(instance)
        return result

    def qualified_name_of(self, instance: Any) -> str | None:
        """Reverse lookup: the longest key registered for `instance` (its full dotted path), or `None`.

        An alias can register a shorter second key for the same instance, so the longest is the qualified one.
        """
        candidates = [name for name, inst in self._qualified.items() if inst is instance]
        return max(candidates, key=len) if candidates else None

    def resolve(self, name: str, kind_hint: str | list[str] | None = None, home_model: str | None = None) -> Any | None:
        if name in self._qualified:
            return self._qualified[name]
        # A name qualified with an imported model must not use the short-name lookup, or a same-named
        # local symbol would win (e.g. `STRUCTURE ModInfo EXTENDS Other.ModInfo` resolving to itself).
        if "." in name and not self.has_prefix(name):
            return None
        short = name.rsplit(".", 1)[-1]
        candidates = self._by_short_name.get(short, [])
        if kind_hint is not None and len(candidates) > 1:
            # The same short name can denote different metamodel entities (e.g. a CLASS and an anonymous
            # role named after it); keep only instances of the hinted metamodel class(es).
            hints = kind_hint if isinstance(kind_hint, list) else [kind_hint]
            filtered = [c for c in candidates if getattr(c, "_qualified_class", "").rsplit(".", 1)[-1] in hints]
            if len(filtered) == 1:
                return filtered[0]
            candidates = filtered if filtered else candidates
        if len(candidates) > 1 and home_model:
            # Same short name in several MODELs of one file: kind_hint cannot tell them apart, so pick the
            # one under the requesting MODEL (candidates from `_by_short_name` carry no qualified name).
            candidate_ids = {id(c) for c in candidates}
            in_model = [
                v
                for k, v in self._qualified.items()
                if k.startswith(home_model + ".") and k.rsplit(".", 1)[-1] == short and id(v) in candidate_ids
            ]
            if len(in_model) == 1:
                return in_model[0]
        if len(candidates) == 1:
            return candidates[0]
        return None

    def rekey_model_prefix(self, old_prefix: str, new_prefix: str) -> None:
        """Add an alias `new_prefix.X` -> same instance for every `old_prefix.X` entry, keeping the original.

        Used for the predefined INTERLIS model, whose internal MODEL name differs from the documented one.
        Only `_qualified` is touched: re-adding to `_by_short_name` would break the single-candidate case.
        """
        prefix = old_prefix + "."
        for qualified, instance in list(self._qualified.items()):
            if qualified.startswith(prefix):
                self._qualified[new_prefix + "." + qualified[len(prefix) :]] = instance

    def has_prefix(self, name: str) -> bool:
        """Check whether a qualified `name`'s model prefix designates the current file (False if unqualified)."""
        if "." not in name:
            return False
        prefix = name.split(".", 1)[0]
        return any(qn.startswith(prefix + ".") or qn == prefix for qn in self._qualified)


class ForwardRefResolver:
    def __init__(self, symbol_table: SymbolTable):
        self.symbol_table = symbol_table
        self._pending: list[_Pending] = []
        # MODEL currently being built, stamped on each pending ref that
        # doesn't name its own `home_model` (models are built one after another).
        self.current_model: str | None = None

    def register_pending(self, ref: ForwardRef, container: Any, field: str) -> None:
        if ref.home_model is None:
            ref.home_model = self.current_model
        self._pending.append(_Pending(ref, container, field))

    def resolve_all(self, repository=None) -> None:
        for entry in self._pending:
            resolved = self._resolve_one(entry.ref, repository)
            if entry.ref.resolve_via_twin:
                twin = getattr(resolved, "_twin", None)
                if twin is not None:
                    resolved = twin
            current = getattr(entry.container, entry.field, None)
            if isinstance(current, list):
                for i, item in enumerate(current):
                    if item is entry.ref:
                        current[i] = resolved
            else:
                setattr(entry.container, entry.field, resolved)
        self._pending.clear()

    def _resolve_one(self, ref: ForwardRef, repository=None) -> Any:
        kind_hint = ref.resolves_to_hint if isinstance(ref.resolves_to_hint, (str, list)) else None
        if ref.always_external:
            # The name is the imported model's own bare name: load it through the repository if configured.
            if repository is not None:
                found = repository.resolve_external(ref.name, ref.name, kind_hint)
                if found is not None:
                    return found
            return UnresolvedNamedReference(ref.name, reason="external_import")
        found = self.symbol_table.resolve(ref.name, kind_hint=kind_hint, home_model=ref.home_model)
        if found is not None:
            return found
        if "." in ref.name:
            if not self.symbol_table.has_prefix(ref.name):
                # Prefix absent from this file: resolve cross-file against the targeted model's own table
                # (tables are never merged, so only a full qualified match counts).
                if repository is not None:
                    prefix = ref.name.split(".", 1)[0]
                    found = repository.resolve_external(prefix, ref.name, kind_hint)
                    if found is not None:
                        return found
                return UnresolvedNamedReference(ref.name, reason="external_import")
            if ref.graceful:
                # Unresolved EXTENDS is never fatal.
                return UnresolvedNamedReference(ref.name, reason="unresolved_extends")
            raise BuildError(f"unresolved reference, not attributable to an import: {ref.name!r}", rule=ref.rule)
        # Unqualified name not found locally: try the enclosing TOPIC EXTENDS namespace first.
        found = self._resolve_via_topic_extends(ref, kind_hint, repository)
        if found is not None:
            return found
        # Otherwise only an IMPORTS UNQUALIFIED model can legitimately own the name; else it is a real bug.
        by_model = self.symbol_table.unqualified_imports
        unqualified = (
            by_model[ref.home_model]
            if ref.home_model in by_model
            else [name for names in by_model.values() for name in names]
        )
        if not unqualified:
            if ref.graceful:
                return UnresolvedNamedReference(ref.name, reason="unresolved_extends")
            raise BuildError(f"unresolved reference, not attributable to an import: {ref.name!r}", rule=ref.rule)
        if repository is not None:
            for model_name in unqualified:
                # Try the model-qualified name first: X's file can also declare a base model with the same
                # short name, which makes the bare-name lookup ambiguous.
                for candidate in (f"{model_name}.{ref.name}", ref.name):
                    found = repository.resolve_external(model_name, candidate, kind_hint)
                    if found is not None:
                        return found
        return UnresolvedNamedReference(ref.name, reason="external_import")

    def _resolve_via_topic_extends(self, ref: ForwardRef, kind_hint, repository=None) -> Any | None:
        """Resolve an unqualified name through the enclosing TOPIC's qualified EXTENDS target.

        An unqualified (same-file) EXTENDS is already covered by the short-name lookup in `_resolve_one`.
        """
        hint = ref.topic_extends_hint
        if not hint or "." not in hint:
            return None
        candidate = f"{hint}.{ref.name}"
        found = self.symbol_table.resolve(candidate, kind_hint=kind_hint)
        if found is not None:
            return found
        if repository is not None:
            prefix = hint.split(".", 1)[0]
            return repository.resolve_external(prefix, candidate, kind_hint)
        return None
