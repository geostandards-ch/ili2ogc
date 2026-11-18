"""Multi-file resolution (IMPORTS): load an imported model on demand from local directories.

Never touches the network during a build. Each loaded model keeps its own isolated SymbolTable, so short names
never become ambiguous between unrelated models; only fully qualified names cross files.
"""

import re
from pathlib import Path
from typing import Any

from interlis.builder.errors import BuildError
from interlis.runtime.parse import meta_attribute_comments, meta_attribute_comments_in_file, parse_file, parse_text

# `CONTRACTED? (TYPE | REFSYSTEM | SYMBOLOGY)? MODEL Name`: MODEL is always directly followed by the Name,
# whatever optional keyword precedes it.
_MODEL_NAME_RE = re.compile(r"\bMODEL\s+([A-Za-z_][A-Za-z0-9_]*)")

# Strings, block comments and line comments, in one alternation so that a `!!` or `/*` inside a string
# literal is not taken for a comment (and the other way round).
_STRING_OR_COMMENT_RE = re.compile(r'"(?:[^"\\]|\\.)*"|/\*.*?\*/|!![^\n]*', re.DOTALL)


def _strip_strings_and_comments(text: str) -> str:
    """Blank out string literals and comments, so a prose "MODEL X" in a comment never indexes a model."""
    return _STRING_OR_COMMENT_RE.sub(" ", text)


# The predefined "INTERLIS" namespace, built through the normal pipeline. ANYOID/UUIDOID/BOOLEAN are
# reserved tokens, so the builder synthesizes them (`_predefined_type`); the rest are declared here.
_PREDEFINED_MODEL_INTERNAL_NAME = "PredefinedInterlisNamespace"
_PREDEFINED_INTERLIS_SOURCE = f"""\
INTERLIS 2.4;

MODEL {_PREDEFINED_MODEL_INTERNAL_NAME} AT "http://www.interlis.ch" VERSION "2024-04-24" =

  DOMAIN
    NOOID = OID ANY;
    I32OID = OID 0..2147483647;
    STANDARDOID = OID TEXT*16;
    GregorianYear = 1582..2999;
    INTERLIS_1_DATE = TEXT*8;
    XMLDate = FORMAT INTERLIS.XMLDate "0001-01-01" .. "9999-12-31";
    XMLTime = FORMAT INTERLIS.XMLTime "00:00:00" .. "23:59:59";
    XMLDateTime = FORMAT INTERLIS.XMLDateTime "0001-01-01T00:00:00" .. "9999-12-31T23:59:59";

END {_PREDEFINED_MODEL_INTERNAL_NAME}.
"""
_BUILTIN_SOURCES = {"INTERLIS": _PREDEFINED_INTERLIS_SOURCE}


class ModelRepository:
    """Index a set of directories by declared MODEL/REFSYSTEM name, via a text scan (not a full parse).

    The declared name cannot be taken from the file name, which may differ from it.
    """

    def __init__(self, search_dirs: list[Path]):
        self._index: dict[str, Path] = {}
        for directory in search_dirs:
            for path in sorted(Path(directory).glob("*.ili")):
                try:
                    text = path.read_text(encoding="utf-8", errors="replace")
                except OSError:
                    continue
                for match in _MODEL_NAME_RE.finditer(_strip_strings_and_comments(text)):
                    # first found wins: a text-scan false positive must not overwrite a correct entry
                    self._index.setdefault(match.group(1), path)
        # model name -> SymbolTable (possibly still being built), or None if not found/failed (never retried)
        self._cache: dict[str, Any] = {}
        # Reverse of `_index`, built lazily: avoids O(index) stat calls per built model.
        self._names_by_path: dict[Path, list[str]] | None = None
        # Names actually asked for, as opposed to cached only because a sibling model of the same file was built.
        self._requested: set[str] = set()
        self._make_sub_builder = None  # injected by InterlisModelBuilder

    def path_for(self, model_name: str) -> Path | None:
        """Return the indexed `.ili` path for a MODEL/REFSYSTEM name, or None (predefined or absent from `--repo`)."""
        return self._index.get(model_name)

    def declared_model_names(self, path: Path) -> list[str]:
        """Every MODEL/REFSYSTEM name indexed to `path`, in declaration order.

        One `.ili` can declare several models; a caller holding only the file needs them to reach the same built
        table as `--repo` resolution instead of building the file twice.
        """
        if self._names_by_path is None:
            by_path: dict[Path, list[str]] = {}
            for name, indexed in self._index.items():
                by_path.setdefault(indexed.resolve(), []).append(name)
            self._names_by_path = by_path
        return self._names_by_path.get(path.resolve(), [])

    def register_prebuilt(self, model_name: str, symbol_table) -> None:
        """Register an already-built model (e.g. a root model) in the resolution cache, to avoid rebuilding it."""
        self._cache[model_name] = symbol_table
        self._requested.add(model_name)

    def availability(self, model_name: str) -> str:
        """Return a model's status: 'builtin' | 'available' | 'indexed_but_failed' | 'missing'.

        Lets a header completeness check run independently of which models a DATASECTION actually references.
        """
        if model_name in _BUILTIN_SOURCES:
            return "builtin"
        if model_name not in self._index and model_name not in self._cache:
            return "missing"
        table = self._get_table(model_name)
        return "available" if table is not None else "indexed_but_failed"

    def bind_builder_factory(self, factory) -> None:
        """Inject the factory building imported files: `factory() -> InterlisModelBuilder` with `repository=self`."""
        self._make_sub_builder = factory

    def resolve_external(self, model_name: str, full_dotted_name: str, kind_hint) -> Any | None:
        table = self._get_table(model_name)
        if table is None:
            return None
        found = table.resolve(full_dotted_name, kind_hint=kind_hint)
        if found is not None:
            return found
        return self._resolve_via_topic_inheritance(table, full_dotted_name, kind_hint)

    def _resolve_via_topic_inheritance(self, table: Any, full_dotted_name: str, kind_hint) -> Any | None:
        """Resolve `<Model>.<Topic>.<Name>` when `<Name>` is only declared in a base topic (`TOPIC EXTENDS`).

        Cross-model counterpart of `ForwardRefResolver._resolve_via_topic_extends`; walks `DataUnit.Super`.
        """
        if "." not in full_dotted_name:
            return None
        topic_qualified, member_name = full_dotted_name.rsplit(".", 1)
        data_unit = getattr(table.resolve(topic_qualified), "_twin", None)
        visited: set[int] = set()
        while data_unit is not None and id(data_unit) not in visited:
            visited.add(id(data_unit))
            super_topic = getattr(getattr(data_unit, "Super", None), "_twin", None)
            if super_topic is None:
                return None
            elements = getattr(super_topic, "Element", None) or []
            if not isinstance(elements, list):
                elements = [elements]
            for element in elements:
                if getattr(element, "Name", None) != member_name:
                    continue
                if kind_hint is None:
                    return element
                hints = kind_hint if isinstance(kind_hint, list) else [kind_hint]
                if getattr(element, "_qualified_class", "").rsplit(".", 1)[-1] in hints:
                    return element
            data_unit = getattr(super_topic, "_twin", None)
        return None

    def symbol_table_for(self, model_name: str):
        """Return a loaded model's complete symbol table (same cache as `resolve_external`)."""
        return self._get_table(model_name)

    def loaded_models(self) -> dict[str, Any]:
        """Return `{model name: SymbolTable}` for every imported model a `build()` actually pulled in."""
        return {name: table for name, table in self._cache.items() if table is not None and name in self._requested}

    def _get_table(self, model_name: str):
        """Build (or return the cached) SymbolTable for `model_name`.

        Passes the file's `!!@Name=Value` meta-attribute comments to the sub-build so an imported model's own
        MetaAttributes (e.g. a domain's `!!@CRS=...`) are captured.
        """
        self._requested.add(model_name)
        if model_name in self._cache:
            return self._cache[model_name]
        sibling_names: list[str] = []
        if model_name in _BUILTIN_SOURCES:
            source = _BUILTIN_SOURCES[model_name]
            tree, syntax_errors = parse_text(source)
            meta_attributes = meta_attribute_comments(source)
        else:
            path = self._index.get(model_name)
            if path is None:
                self._cache[model_name] = None
                return None
            # One build populates every model the file declares, so they must share its table; two builds would
            # yield non-identical instances that an EXTENDS/REFERENCE TO resolved through one cannot find in the other.
            sibling_names = self.declared_model_names(path)
            for sibling in sibling_names:
                if sibling in self._cache:
                    self._cache[model_name] = self._cache[sibling]
                    return self._cache[sibling]
            tree, syntax_errors = parse_file(path)
            meta_attributes = meta_attribute_comments_in_file(path)
        if syntax_errors or self._make_sub_builder is None:
            self._cache[model_name] = None
            return None
        builder = self._make_sub_builder()
        # Anti-cycle guard: cache the still-empty table before `build()` so a circular import finds it instead of
        # reloading the same file endlessly.
        for name in {model_name, *sibling_names}:
            self._cache[name] = builder.symbol_table
        try:
            result = builder.build(tree, meta_attributes=meta_attributes)
        except BuildError:
            # Same policy as a syntax error: an imported model that fails to build degrades to None (as if absent)
            # rather than crashing the root build, and is never retried.
            for name in {model_name, *sibling_names}:
                self._cache[name] = None
            return None
        if model_name in _BUILTIN_SOURCES:
            # Restore the real model name over the internal one (see _PREDEFINED_INTERLIS_SOURCE) in the instance
            # and the table, and alias the bare name so an `Import` finds the Model instead of an unresolved reference.
            result.Name = model_name
            builder.symbol_table.rekey_model_prefix(_PREDEFINED_MODEL_INTERNAL_NAME, model_name)
            builder.symbol_table.register(model_name, result)
        return builder.symbol_table
