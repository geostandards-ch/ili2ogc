"""`interlis fetch-models`: the models a file needs, transitively, from ilimodels.xml catalogues (served offline)."""

import io
from pathlib import Path

from interlis.builder.fetch import fetch_models

_CATALOG = """<?xml version="1.0" encoding="UTF-8"?>
<TRANSFER xmlns="http://www.interlis.ch/INTERLIS2.3"><DATASECTION>
<IliRepository20.RepositoryIndex BID="b">
  <IliRepository20.RepositoryIndex.ModelMetadata TID="1">
    <Name>Base</Name><SchemaLanguage>ili2_3</SchemaLanguage><File>old/Base.ili</File><Version>2010-01-01</Version>
  </IliRepository20.RepositoryIndex.ModelMetadata>
  <IliRepository20.RepositoryIndex.ModelMetadata TID="2">
    <Name>Base</Name><SchemaLanguage>ili2_3</SchemaLanguage><File>Base.ili</File><Version>2020-01-01</Version>
    <precursorVersion>2010-01-01</precursorVersion>
  </IliRepository20.RepositoryIndex.ModelMetadata>
  <IliRepository20.RepositoryIndex.ModelMetadata TID="3">
    <Name>Units</Name><SchemaLanguage>ili2_3</SchemaLanguage><File>Units.ili</File><Version>2012-02-20</Version>
  </IliRepository20.RepositoryIndex.ModelMetadata>
</IliRepository20.RepositoryIndex>
</DATASECTION></TRANSFER>
"""

_FILES = {
    "https://cat.example/ilimodels.xml": _CATALOG,
    "https://cat.example/Base.ili": (
        'INTERLIS 2.3;\nMODEL Base AT "x" VERSION "2020-01-01" =\n  IMPORTS Units;\nEND Base.\n'
    ),
    "https://cat.example/Units.ili": 'INTERLIS 2.3;\nTYPE MODEL Units AT "x" VERSION "2012-02-20" =\nEND Units.\n',
}


def _opener(url: str):
    if url not in _FILES:
        raise OSError(f"404 {url}")
    return io.BytesIO(_FILES[url].encode("utf-8"))


def test_imports_are_fetched_transitively_newest_version_first(tmp_path: Path):
    model = tmp_path / "M.ili"
    model.write_text("INTERLIS 2.3;\nMODEL M =\n  IMPORTS UNQUALIFIED Base; !! IMPORTS Ignored;\nEND M.\n")
    out = tmp_path / "models"
    downloaded, missing = fetch_models([model], out, catalogs=("https://cat.example/ilimodels.xml",), opener=_opener)
    assert sorted(p.name for p in out.iterdir()) == ["Base.ili", "Units.ili"]
    assert "Base 2020-01-01" in downloaded[0] and not missing


def test_a_model_already_there_is_kept_and_an_unknown_one_reported(tmp_path: Path):
    out = tmp_path / "models"
    out.mkdir()
    (out / "Base.ili").write_text('INTERLIS 2.3;\nMODEL Base AT "x" VERSION "1" =\nEND Base.\n')
    model = tmp_path / "M.ili"
    model.write_text("INTERLIS 2.3;\nMODEL M =\n  IMPORTS Base, Nowhere;\nEND M.\n")
    downloaded, missing = fetch_models([model], out, catalogs=("https://cat.example/ilimodels.xml",), opener=_opener)
    assert downloaded == []
    assert len(missing) == 1 and missing[0].startswith("Nowhere")
