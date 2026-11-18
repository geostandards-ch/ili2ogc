"""Find the root model(s) needed to validate an XTF transfer, using only the file and a local `.ili` repository.

`HEADERSECTION/MODELS` says which model versions to fetch; DATASECTION baskets name the root (business) models.
Core models listed in the header never produce a basket, so they cannot be a build entry point.
"""

from dataclasses import dataclass

from interlis.xtf.parse import XtfTransfer


def root_model_names(transfer: XtfTransfer) -> list[str]:
    """Return the model names that have a DATASECTION basket, in order of first appearance.

    One of them is enough as the `build()` root; the others resolve through the repository.
    """
    seen: dict[str, None] = {}
    for basket in transfer.baskets:
        name = basket.qualified_topic.split(".", 1)[0]
        seen.setdefault(name, None)
    return list(seen)


def header_model_lookup(transfer: XtfTransfer) -> dict[str, "list[str]"]:
    """Map model name to `[version, uri]` as declared in HEADERSECTION/MODELS, so an error can say what to fetch."""
    return {m.name: [m.version or "?", m.uri or "?"] for m in transfer.models}


@dataclass
class HeaderModelStatus:
    name: str
    version: str | None
    uri: str | None
    status: str  # "builtin" | "available" | "indexed_but_failed" | "missing" | "no_repo"


def header_completeness(transfer: XtfTransfer, repository) -> list[HeaderModelStatus]:
    """Return the resolvability status of every HEADERSECTION/MODELS entry.

    With `repository=None` every entry is "no_repo", which differs from "missing" (searched but not found).
    """
    if repository is None:
        return [HeaderModelStatus(name=m.name, version=m.version, uri=m.uri, status="no_repo") for m in transfer.models]
    return [
        HeaderModelStatus(name=m.name, version=m.version, uri=m.uri, status=repository.availability(m.name))
        for m in transfer.models
    ]
