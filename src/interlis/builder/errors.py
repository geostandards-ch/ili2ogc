"""Errors and diagnostic objects for the ModelBuilder."""

from typing import Any


class BuildError(Exception):
    """Unrecoverable build error, carrying the grammar rule and the ctx's start line/column when available."""

    def __init__(self, message: str, *, rule: str | None = None, ctx: Any = None):
        self.rule = rule
        self.ctx = ctx
        location = ""
        start = getattr(ctx, "start", None)
        if start is not None:
            location = f" (line {start.line}, column {start.column})"
        prefix = f"[{rule}]" if rule else ""
        super().__init__(f"{prefix}{location} {message}".strip())


class UnresolvedNamedReference:
    """A reference into an imported model that could not be resolved, kept as an explicit object (not None).

    Used when no ModelRepository is configured, or the model is missing, unparsable or in an unresolved cycle.
    """

    __slots__ = ("name", "reason")

    def __init__(self, name: str, reason: str = "external_import"):
        self.name = name
        self.reason = reason

    def __repr__(self) -> str:
        return f"<UnresolvedNamedReference {self.name!r} reason={self.reason!r}>"

    def __eq__(self, other):
        return isinstance(other, UnresolvedNamedReference) and (self.name, self.reason) == (other.name, other.reason)
