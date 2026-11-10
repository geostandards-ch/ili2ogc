"""Coordinate reference system of a coordinate domain, shared by every converter and validator.

The EPSG code comes from ONE source only: the eCH-0117 `!!@CRS=EPSG:<digits>` annotation written directly above a
concrete domain (`Coord2 EXTENDS Geometry_V2.Coord2 = COORD ...`). A generic domain (`Coord2 (GENERIC) = COORD ...`)
has none; its concrete one reaches it through `EXTENDS` or a `CONTEXT` rebinding. Nothing is ever inferred from the
bounds of a domain or from the name of its axes' reference system.
"""

from __future__ import annotations

import contextlib
from collections.abc import Iterator
from contextvars import ContextVar
from dataclasses import dataclass

from interlis.builder.forward_refs import SymbolTable
from interlis.builder.repository import ModelRepository
from interlis.metamodel.instance import MetaInstance

CRS_URI_PREFIX = "http://www.opengis.net/def/crs/EPSG/0/"

_FALLBACK: ContextVar[int | None] = ContextVar("_FALLBACK", default=None)

_CONTEXT_CLASS = "IlisMeta16.ModelData.Context"
_MODEL_CLASS = "IlisMeta16.ModelData.Model"


@dataclass(frozen=True)
class CrsResolution:
    """The CRS of a coordinate domain, or why it has none.

    `provenance` says where the code came from: `"annotation"` (on the domain itself), `"extends"` (on an ancestor
    in its `EXTENDS` chain), `"context"` (on the concrete domain a `CONTEXT` binds the generic one to) or `"cli"`
    (the explicit `--crs` fallback for a domain that declares none).
    `reason` (when unresolved) is one of `unresolved-type`, `unresolved-super` (a base model is missing),
    `generic-unbound` (a generic domain no imported `CONTEXT` binds), `undeclared` (no annotation anywhere on the
    chain), `ambiguous-context`, `invalid-annotation` or `duplicate-annotation`.
    `warnings` are `(rule, detail)` findings that did not prevent the resolution.
    """

    epsg: int | None = None
    provenance: str | None = None
    domain: MetaInstance | None = None
    reason: str | None = None
    detail: str = ""
    warnings: tuple[tuple[str, str], ...] = ()

    @property
    def uri(self) -> str | None:
        """The `http://www.opengis.net/def/crs/EPSG/0/<code>` URI, or `None` when unresolved."""
        return None if self.epsg is None else f"{CRS_URI_PREFIX}{self.epsg}"


@contextlib.contextmanager
def fallback_crs(epsg: int | None) -> Iterator[None]:
    """Inside the block, a domain that declares NO `!!@CRS` anywhere gets `epsg` (the user's explicit `--crs`).

    Only the `undeclared` case: an ambiguous `CONTEXT`, an invalid or duplicated annotation and an unbound
    generic domain stay unresolved, since the user's value would silence a real model problem.
    """
    token = _FALLBACK.set(epsg)
    try:
        yield
    finally:
        _FALLBACK.reset(token)


def _name_of(domain: MetaInstance) -> str:
    return getattr(domain, "Name", None) or "<anonymous>"


def _own_annotation(domain: MetaInstance) -> CrsResolution | None:
    """The CRS written on `domain` itself, a failed resolution if that annotation is unusable, `None` if absent."""
    annotations = [m for m in getattr(domain, "MetaAttribute", None) or [] if getattr(m, "Name", None) == "CRS"]
    if not annotations:
        return None
    if len(annotations) > 1:
        values = ", ".join(str(a.Value) for a in annotations)
        return CrsResolution(
            reason="duplicate-annotation",
            domain=domain,
            detail=f"domain {_name_of(domain)} carries {len(annotations)} !!@CRS annotations ({values})",
        )
    raw = annotations[0].Value
    scheme, _, code = (raw or "").partition(":")
    if scheme.strip().upper() != "EPSG" or not code.strip().isdigit():
        return CrsResolution(
            reason="invalid-annotation",
            domain=domain,
            detail=f"domain {_name_of(domain)}: !!@CRS={raw} is not EPSG:<digits>",
        )
    gap = getattr(annotations[0], "_line_gap", None)
    warnings = ()
    if gap is not None and gap > 1:
        warnings = (
            (
                "CRS-ANNOTATION-DISTANT",
                f"domain {_name_of(domain)}: !!@CRS={raw} sits {gap} lines above it (comments or blank lines between)",
            ),
        )
    return CrsResolution(epsg=int(code.strip()), provenance="annotation", domain=domain, warnings=warnings)


def _along_extends(domain: MetaInstance) -> CrsResolution | None:
    """The first annotation on `domain` or an ancestor of its `EXTENDS` chain, `None` if the chain has none."""
    seen: set[int] = set()
    current: MetaInstance | None = domain
    while current is not None and id(current) not in seen:
        seen.add(id(current))
        found = _own_annotation(current)
        if found is not None:
            if found.epsg is not None and current is not domain:
                return CrsResolution(
                    epsg=found.epsg,
                    provenance="extends",
                    domain=current,
                    warnings=found.warnings,
                )
            return found
        parent = getattr(current, "Super", None)
        if parent is None:
            return None
        if not isinstance(parent, MetaInstance):
            return CrsResolution(
                reason="unresolved-super",
                detail=f"domain {_name_of(current)} extends a domain whose model is not available",
            )
        current = parent
    return None


def _chain(domain: MetaInstance) -> list[MetaInstance]:
    """`domain` then each ancestor of its `EXTENDS` chain that resolved (cycle-safe)."""
    out: list[MetaInstance] = []
    current: MetaInstance | None = domain
    while isinstance(current, MetaInstance) and all(current is not seen for seen in out):
        out.append(current)
        current = getattr(current, "Super", None)
    return out


def context_instances(symbol_table: SymbolTable | None, repository: ModelRepository | None) -> list[MetaInstance]:
    """Every `Context` (`CONTEXT <Name> = ...;`) declared directly in a MODEL actually IMPORTed, deduplicated.

    Scoped to `Model.Element` of the SPECIFIC model each name in `repository.loaded_models()` was resolved
    through: a file can declare several sibling `MODEL`s sharing one symbol table (swisstopo's
    `CHBase_Part7_CONTEXT_V2.ili`: `ContextCH_V2`/`ContextCHLV03_V2`/`ContextCHLV95_V2`), and only the model named in
    an `IMPORTS` clause may contribute its rebindings - an unrelated sibling's (possibly ambiguous, `OR`-joined)
    rebinding of the SAME generic domain must never shadow it.
    """
    models: list[MetaInstance] = []
    if symbol_table is not None:
        models.extend(
            inst
            for inst in symbol_table.all_registered()
            if isinstance(inst, MetaInstance) and inst._qualified_class == _MODEL_CLASS
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
                and element._qualified_class == _CONTEXT_CLASS
                and id(element) not in seen
            ):
                seen.add(id(element))
                contexts.append(element)
    return contexts


def context_binding(
    domain: MetaInstance | None, symbol_table: SymbolTable | None, repository: ModelRepository | None
) -> list[MetaInstance] | None:
    """The concrete domains a `CONTEXT` binds the generic `domain` (or an ancestor of it) to, `None` if none does.

    Several entries mean OR-ed alternatives (`ContextCH_V2`'s `Coord2=...LV03 OR ...LV95`): ambiguous.
    """
    if domain is None:
        return None
    candidates = _chain(domain)
    for context in context_instances(symbol_table, repository):
        for generic_def in getattr(context, "GenericDef", None) or []:
            domains = getattr(generic_def, "GenericDomain", None) or []
            if not domains or not any(domains[0] is c for c in candidates):
                continue
            links = getattr(generic_def, "ConcreteForGeneric", None) or []
            return [
                c
                for link in links
                for c in (getattr(link, "ConcreteDomain", None) or [])
                if isinstance(c, MetaInstance)
            ]
    return None


def context_concrete_domain(
    domain: MetaInstance | None, symbol_table: SymbolTable | None, repository: ModelRepository | None
) -> MetaInstance | None:
    """The single concrete domain a `CONTEXT` binds `domain` to, `None` when unbound or ambiguous."""
    binding = context_binding(domain, symbol_table, repository)
    return binding[0] if binding is not None and len(binding) == 1 else None


def resolve_crs(
    coord_type: MetaInstance | None,
    *,
    symbol_table: SymbolTable | None = None,
    repository: ModelRepository | None = None,
) -> CrsResolution:
    """`_resolve`, then the `--crs` fallback for a domain declaring none."""
    found = _resolve(coord_type, symbol_table=symbol_table, repository=repository)
    fallback = _FALLBACK.get()
    if fallback is None or found.reason != "undeclared":
        return found
    return CrsResolution(
        epsg=fallback,
        provenance="cli",
        domain=found.domain,
        warnings=(("CRS-DEFAULT-APPLIED", f"{found.detail}; --crs EPSG:{fallback} used"),),
    )


def _resolve(
    coord_type: MetaInstance | None,
    *,
    symbol_table: SymbolTable | None = None,
    repository: ModelRepository | None = None,
) -> CrsResolution:
    """Resolve the CRS of a coordinate domain: its own `!!@CRS`, else its `EXTENDS` chain's, else its `CONTEXT`'s.

    Never guessed: an absent, duplicated or malformed annotation, or an ambiguous `CONTEXT`, gives an unresolved
    `CrsResolution` carrying the reason.
    """
    if coord_type is None:
        return CrsResolution(reason="unresolved-type", detail="vertex CoordType not resolved")
    direct = _along_extends(coord_type)
    if direct is not None:
        return direct
    binding = context_binding(coord_type, symbol_table, repository)
    if binding is not None:
        if len(binding) != 1:
            names = ", ".join(_name_of(c) for c in binding) or "no domain"
            return CrsResolution(
                reason="ambiguous-context",
                domain=coord_type,
                detail=f"a CONTEXT binds {_name_of(coord_type)} to {names}: choosing one would invent a CRS",
            )
        concrete = _along_extends(binding[0])
        if concrete is None:
            return CrsResolution(
                reason="undeclared",
                domain=binding[0],
                detail=f"{_name_of(binding[0])}, the CONTEXT's concrete domain, has no !!@CRS",
            )
        if concrete.epsg is None:
            return concrete
        return CrsResolution(
            epsg=concrete.epsg, provenance="context", domain=concrete.domain, warnings=concrete.warnings
        )
    if getattr(coord_type, "Generic", None):
        return CrsResolution(
            reason="generic-unbound",
            domain=coord_type,
            detail=f"generic domain {_name_of(coord_type)} is bound by no CONTEXT in scope",
        )
    return CrsResolution(
        reason="undeclared",
        domain=coord_type,
        detail=f"domain {_name_of(coord_type)} and its ancestors carry no !!@CRS",
    )


def reason_rule(reason: str, converter: str) -> str:
    """The diagnostic id of an unresolved `CrsResolution.reason` for `converter` (`"SQL"` or `"JSONFG"`)."""
    if reason in ("unresolved-type", "unresolved-super", "generic-unbound"):
        return f"{converter}-GEOM-NO-CRS"
    if reason == "undeclared":
        return f"{converter}-GEOM-CRS-UNDECLARED"
    return {
        "ambiguous-context": "CRS-CONTEXT-AMBIGUOUS",
        "invalid-annotation": "CRS-ANNOTATION-INVALID",
        "duplicate-annotation": "CRS-ANNOTATION-DUPLICATE",
    }[reason]


def crs_uri(
    coord_type: MetaInstance | None,
    *,
    symbol_table: SymbolTable | None = None,
    repository: ModelRepository | None = None,
) -> str | None:
    """`resolve_crs(...).uri`: the JSON-FG `coordRefSys` URI, `None` when unresolved."""
    return resolve_crs(coord_type, symbol_table=symbol_table, repository=repository).uri


__all__ = [
    "CRS_URI_PREFIX",
    "CrsResolution",
    "context_binding",
    "context_concrete_domain",
    "context_instances",
    "crs_uri",
    "fallback_crs",
    "reason_rule",
    "resolve_crs",
]
