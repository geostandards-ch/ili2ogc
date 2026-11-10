"""`TextSign (EXTENDED)` with Separator / Suffix: a two-part number label, in two rules."""

from pathlib import Path

import pytest
from pycartosym import get_codec
from pycartosym.parser import CartoSymParser

from interlis.cli import main
from interlis.cli_style import ExitCode
from interlis.convert.cartosym import _text_with_suffix

_FIXTURES = Path(__file__).parent / "fixtures" / "cartosym"
_MODEL = _FIXTURES / "text_suffix" / "TextSuffixExample.ili"
_SIGNS = _FIXTURES / "text_suffix" / "TextSuffixExample_Signs.xtf"
_REPO = _FIXTURES / "text_suffix_repo"


def test_text_alone_without_a_suffix():
    assert _text_with_suffix({"Txt": {"property": "Number"}}) == {"property": "Number"}


def test_text_separator_suffix_is_a_concatenation():
    params = {"Txt": {"property": "Number"}, "Separator": ".", "Suffix": {"property": "Part"}}
    expected = {"op": "concatenate", "args": [{"property": "Number"}, ".", {"property": "Part"}]}
    assert _text_with_suffix(params) == expected


def test_convert_sld_writes_both_labels(capsys):
    css = "L { label: { elements: [ Text { text: concatenate(a, '.', b) } ] }; }"
    probe = CartoSymParser().parse_string_to_pydantic(css)
    try:
        get_codec("sld").write(probe)
    except NotImplementedError:
        pytest.skip("this pycartosym's SLD writer cannot write a concatenated label yet")
    args = ["convert-sld", str(_MODEL), "--repo", str(_REPO), "--sign-xtf", str(_SIGNS)]
    assert main(args) == ExitCode.OK
    out = capsys.readouterr().out
    assert "<ogc:PropertyName>Number</ogc:PropertyName>.<ogc:PropertyName>Part</ogc:PropertyName>" in out
    assert out.count("<se:Rule>") == 2
    assert "PropertyIsNull" in out
