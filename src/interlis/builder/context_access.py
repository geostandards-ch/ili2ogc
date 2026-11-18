"""Generic reader for an ANTLR4-Python context's accessors, driven by signature introspection.

A repeatable accessor takes an optional index parameter (None -> full list); a unique accessor takes
none. Inspecting the bound signature distinguishes the two without an external cache.
"""

import inspect
from typing import Any


def has_accessor(ctx: Any, field: str) -> bool:
    return callable(getattr(ctx, field, None))


def call(ctx: Any, field: str, index: int | None = None) -> Any:
    """Call ctx.<field>(...), respecting its real signature.

    Raises AttributeError if the accessor is missing, TypeError for a nonzero index on a unique
    accessor (index=0 is tolerated as "the only match").
    """
    method = getattr(ctx, field, None)
    if method is None or not callable(method):
        raise AttributeError(f"{type(ctx).__name__} has no accessor {field!r}")
    accepts_index = len(inspect.signature(method).parameters) > 0
    if index is not None:
        if not accepts_index:
            if index == 0:
                return method()
            raise TypeError(f"{field} on {type(ctx).__name__} does not accept an index (single accessor)")
        if index < 0:
            # Generated accessors silently return None for negative indices; go through the full list
            # so Python-style indexing (-1 = last) works.
            values = method()
            values = list(values) if values is not None else []
            return values[index] if -len(values) <= index < len(values) else None
        return method(index)
    return method()


def call_list(ctx: Any, field: str) -> list:
    """Return a list for both repeatable (0..N) and unique (0..1) accessors."""
    method = getattr(ctx, field, None)
    if method is None or not callable(method):
        raise AttributeError(f"{type(ctx).__name__} n'a pas d'accesseur {field!r}")
    accepts_index = len(inspect.signature(method).parameters) > 0
    if accepts_index:
        result = method()
        return list(result) if result is not None else []
    result = method()
    return [] if result is None else [result]


def is_present(ctx: Any, field: str, index: int | None = None) -> bool:
    """Check whether the accessor matched anything.

    A repeatable accessor returns a (possibly empty) list, never None, so a plain `is not None`
    test would report it present unconditionally; an empty list counts as absent.
    """
    result = call(ctx, field, index)
    if isinstance(result, list):
        return len(result) > 0
    return result is not None


def text(ctx: Any, field: str, index: int | None = None) -> str | None:
    """Return an accessor's raw text, or None if absent (STRING literals keep their quotes)."""
    node = call(ctx, field, index)
    return None if node is None else node.getText()
