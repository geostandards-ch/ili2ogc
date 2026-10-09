"""`TEXT*N` / `MTEXT*N` maximum length is enforced by the XTF validator; unbounded text and NAME/URI are not
subject to it."""

import pytest
from conftest import build_from_file

from interlis.xtf.parse import RawNode, XtfBasket, XtfObject, XtfTransfer
from interlis.xtf.validate import validate_transfer

MODEL = """INTERLIS 2.4;
MODEL TextLen (en) AT "https://example.org/" VERSION "2026-01-01" =
  TOPIC T =
    CLASS Item =
      Short: TEXT*5;
      Multi: MTEXT*5;
      Free: TEXT;
      Ident: NAME;
    END Item;
  END T;
END TextLen.
"""


@pytest.fixture(scope="module")
def builder(tmp_path_factory):
    path = tmp_path_factory.mktemp("textlen") / "TextLen.ili"
    path.write_text(MODEL, encoding="utf-8")
    return build_from_file(path)


def _issues(builder, **values):
    attributes = {name: [RawNode(tag=name, text=text, attrib={}, children=[])] for name, text in values.items()}
    obj = XtfObject(tid="t1", qualified_class="TextLen.T.Item", attributes=attributes)
    basket = XtfBasket(bid="b1", qualified_topic="TextLen.T", kind=None, endstate=None, objects=[obj])
    transfer = XtfTransfer(sender=None, ili_version=None, models=[], baskets=[basket])
    return validate_transfer(transfer, symbol_table=builder.symbol_table)


@pytest.mark.parametrize("attribute", ["Short", "Multi"])
def test_text_at_the_limit_has_no_issue(builder, attribute):
    assert _issues(builder, **{attribute: "abcde"}) == []


@pytest.mark.parametrize("attribute", ["Short", "Multi"])
def test_text_over_the_limit_is_an_error(builder, attribute):
    issues = _issues(builder, **{attribute: "abcdef"})
    assert [(i.attribute, i.severity) for i in issues] == [(attribute, "error")]
    assert "exceeds" in issues[0].message


def test_length_counts_characters_not_bytes(builder):
    assert _issues(builder, Short="äöüéè") == []


def test_unbounded_text_has_no_limit(builder):
    assert _issues(builder, Free="x" * 5000) == []


def test_name_type_is_not_checked_against_a_text_limit(builder):
    assert _issues(builder, Ident="x" * 200) == []
