"""A TEXT value carried by an `OID` XML attribute or made of whitespace is a value; a truly empty element is not."""

import pytest
from conftest import build_from_file

from interlis.convert.jsonfg import transfer_to_feature_collection
from interlis.xtf.parse import parse_xtf
from interlis.xtf.validate import validate_transfer

MODEL = """INTERLIS 2.4;
MODEL TextVal (en) AT "https://example.org/" VERSION "2026-01-01" =
  TOPIC T =
    CLASS Item =
      Note: TEXT;
      Ident: INTERLIS.STANDARDOID;
    END Item;
  END T;
END TextVal.
"""

_XTF = """<?xml version="1.0" encoding="UTF-8"?>
<TRANSFER xmlns="http://www.interlis.ch/INTERLIS2.3"><HEADERSECTION SENDER="t" VERSION="2.3"><MODELS>
<MODEL NAME="TextVal" VERSION="2026-01-01" URI="https://example.org/"></MODEL></MODELS></HEADERSECTION>
<DATASECTION><TextVal.T BID="b">
<TextVal.T.Item TID="i1">{note}{ident}</TextVal.T.Item>
</TextVal.T></DATASECTION></TRANSFER>
"""


@pytest.fixture(scope="module")
def builder(tmp_path_factory):
    path = tmp_path_factory.mktemp("textval") / "TextVal.ili"
    path.write_text(MODEL, encoding="utf-8")
    return build_from_file(path)


def _transfer(tmp_path, note, ident):
    path = tmp_path / "t.xtf"
    path.write_text(_XTF.format(note=note, ident=ident), encoding="utf-8")
    return parse_xtf(path)


def _errors(builder, transfer):
    return [
        i.attribute for i in validate_transfer(transfer, symbol_table=builder.symbol_table) if i.severity == "error"
    ]


def test_an_oid_attribute_is_a_value(builder, tmp_path):
    assert _errors(builder, _transfer(tmp_path, "<Note>x</Note>", '<Ident OID="CH123"></Ident>')) == []


def test_a_blank_text_is_a_value(builder, tmp_path):
    assert _errors(builder, _transfer(tmp_path, "<Note> </Note>", "")) == []


def test_an_empty_text_is_still_an_error(builder, tmp_path):
    assert _errors(builder, _transfer(tmp_path, "<Note></Note>", "")) == ["Note"]


def test_the_values_reach_the_json_fg(builder, tmp_path):
    transfer = _transfer(tmp_path, "<Note> </Note>", '<Ident OID="CH123"/>')
    [feature] = transfer_to_feature_collection(transfer, symbol_table=builder.symbol_table)["features"]
    assert feature["properties"] == {"Note": " ", "Ident": "CH123"}
