"""`interlis fetch-models`: download the models a file needs from the INTERLIS model repositories.

The only network access of this package, and an explicit step of its own: every other command resolves IMPORTS
from local `--repo` directories, so a build is reproducible and offline. The files land in one directory - a cache
reused across runs (a model already there is not downloaded again) - that later commands take as `--repo`.
"""

from __future__ import annotations

import re
import urllib.request
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urljoin

from interlis.runtime.parse import read_ili_text

from .repository import ModelRepository

CATALOGS = (
    "https://models.interlis.ch/ilimodels.xml",
    "https://models.geo.admin.ch/ilimodels.xml",
    "https://models.kgk-cgc.ch/ilimodels.xml",
)
_BUILTIN = {"INTERLIS"}
_COMMENTS = re.compile(r"!!.*?$|/\*.*?\*/", re.M | re.S)


@dataclass
class _Wanted:
    name: str
    version: str | None  # from a transfer header; IMPORTS never name one
    language: str | None  # "ili2_3"/"ili2_4" of the importing file: the version to prefer
    required_by: str


def _ili_language(text: str) -> str | None:
    match = re.search(r"^\s*INTERLIS\s+2\.(\d)\s*;", text, re.M)
    return f"ili2_{match.group(1)}" if match else None


def _imports(text: str) -> list[str]:
    names: list[str] = []
    for group in re.findall(r"\bIMPORTS\s+([^;]+);", _COMMENTS.sub("", text)):
        names += [n for n in re.findall(r"[A-Za-z_][A-Za-z0-9_]*", group) if n != "UNQUALIFIED"]
    return names


def _transfer_models(path: Path) -> list[tuple[str, str | None]]:
    """`(name, version)` of each model in a transfer's HEADERSECTION (XTF 2.3 attributes, or 2.4 element text)."""
    models = []
    for _event, elem in ET.iterparse(str(path), events=("end",)):
        tag = elem.tag.rsplit("}", 1)[-1].upper()
        if tag == "MODEL":
            attrs = {k.upper(): v for k, v in elem.attrib.items()}
            name = attrs.get("NAME") or (elem.text or "").strip()
            if name:
                models.append((name, attrs.get("VERSION")))
        elif tag == "MODELS":
            break
    return models


def _catalog_entries(url: str, opener: Callable) -> list[dict]:
    """Every `ModelMetadata` of every `RepositoryIndex` of an `ilimodels.xml` (one index lists several)."""
    with opener(url) as response:
        root = ET.fromstring(response.read())
    entries = []
    for metadata in root.iter():
        if not metadata.tag.rsplit("}", 1)[-1].endswith(".ModelMetadata"):
            continue
        entry: dict = {"catalog": url}
        for child in metadata:
            tag = child.tag.rsplit("}", 1)[-1]
            if tag == "dependsOnModel":
                entry[tag] = [v.text.strip() for v in child.iter() if v.tag.rsplit("}", 1)[-1] == "value" and v.text]
            else:
                entry[tag] = child.text
        entries.append(entry)
    return entries


def _pick(entries: list[dict], wanted: _Wanted) -> dict | None:
    """The exact header version, else the newest (no other entry names it `precursorVersion`), in the importing
    file's INTERLIS version when there is one.
    """
    pool = [e for e in entries if e.get("SchemaLanguage") != "ili1"]
    exact = [e for e in pool if wanted.version and e.get("Version") == wanted.version]
    if exact:
        return exact[0]
    precursors = {e.get("precursorVersion") for e in pool}
    tips = [e for e in pool if e.get("Version") not in precursors] or pool
    same_language = [e for e in tips if wanted.language and e.get("SchemaLanguage") == wanted.language]
    return (same_language or tips or [None])[0]


def fetch_models(
    files: list[Path],
    out_dir: Path,
    *,
    repo_dirs: list[Path] = (),  # type: ignore[assignment]
    catalogs: tuple[str, ...] = CATALOGS,
    opener: Callable = urllib.request.urlopen,
) -> tuple[list[str], list[str]]:
    """Download into `out_dir` every model `files` need, transitively, that `out_dir`/`repo_dirs` don't hold yet.

    `files` are `.ili` models (their IMPORTS) or transfers (their header models). Returns `(downloaded, missing)`,
    each line naming the model.
    """
    out_dir.mkdir(parents=True, exist_ok=True)
    queue: list[_Wanted] = []
    for path in files:
        if path.suffix.lower() == ".ili":
            text = read_ili_text(path)
            queue += [_Wanted(n, None, _ili_language(text), path.name) for n in _imports(text)]
        else:
            queue += [_Wanted(n, v, None, path.name) for n, v in _transfer_models(path)]
    catalog_cache: dict[str, list[dict]] = {}
    seen: set[str] = set()
    downloaded: list[str] = []
    missing: list[str] = []
    while queue:
        wanted = queue.pop(0)
        if wanted.name in seen or wanted.name in _BUILTIN:
            continue
        seen.add(wanted.name)
        local = ModelRepository([out_dir, *repo_dirs]).path_for(wanted.name)
        if local is not None:
            text = read_ili_text(local)
            queue += [_Wanted(n, None, _ili_language(text), wanted.name) for n in _imports(text)]
            continue
        entry = None
        for url in catalogs:
            if url not in catalog_cache:
                try:
                    catalog_cache[url] = _catalog_entries(url, opener)
                except OSError:
                    catalog_cache[url] = []
            entry = _pick([e for e in catalog_cache[url] if e.get("Name") == wanted.name], wanted)
            if entry is not None:
                break
        if entry is None or not entry.get("File"):
            missing.append(f"{wanted.name} (required by {wanted.required_by}): in none of {', '.join(catalogs)}")
            continue
        url = urljoin(entry["catalog"], entry["File"])
        target = out_dir / Path(entry["File"]).name
        try:
            with opener(url) as response:
                target.write_bytes(response.read())
        except OSError as exc:
            missing.append(f"{wanted.name} (required by {wanted.required_by}): {url}: {exc}")
            continue
        downloaded.append(f"{wanted.name} {entry.get('Version') or ''} <- {url}")
        text = read_ili_text(target)
        dependencies = _imports(text) + list(entry.get("dependsOnModel") or [])
        queue += [_Wanted(n, None, _ili_language(text), wanted.name) for n in dependencies]
    return downloaded, missing
