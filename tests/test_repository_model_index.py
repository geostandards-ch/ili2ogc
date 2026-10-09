"""`ModelRepository` indexes the MODEL names a file really declares, not words in its comments or strings."""

from interlis.builder.repository import ModelRepository

_SOURCE = """INTERLIS 2.3;

/* DATA MODEL HAZARD MAPPING, version 1 */
!!  MINIMUM DATA MODEL
MODEL Real (en) AT "http://example.org/ MODEL InString" VERSION "1" =
  !! this MODEL InLineComment is not a declaration
  TOPIC T =
  END T;
END Real.

SYMBOLOGY MODEL Second (en) AT "http://example.org/" VERSION "1" =
END Second.
"""


def test_comment_and_string_words_are_not_indexed(tmp_path):
    (tmp_path / "a.ili").write_text(_SOURCE, encoding="utf-8")
    repository = ModelRepository([tmp_path])
    assert set(repository._index) == {"Real", "Second"}


def test_first_declaration_wins(tmp_path):
    (tmp_path / "a.ili").write_text("MODEL Same (en) =\nEND Same.\n", encoding="utf-8")
    (tmp_path / "b.ili").write_text("MODEL Same (en) =\nEND Same.\n", encoding="utf-8")
    assert ModelRepository([tmp_path])._index["Same"].name == "a.ili"
