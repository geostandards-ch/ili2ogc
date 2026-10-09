"""`MinScaleDenominator`/`MaxScaleDenominator` PARAMETERs of a drawing rule -> `viz.sd` bounds -> SLD scale range.

`tests/fixtures/cartosym/scale_example.ili` sets the parameters declared by the proposed
`StandardSymbologyScale` extension (`scale_repo/`) on a `PolylineSign`; the sign library is synthetic.
"""

from pathlib import Path

import pytest
from conftest import build_from_file
from lxml import etree
from pycartosym.codecs import get_codec

from interlis.builder.repository import ModelRepository
from interlis.cli import main
from interlis.cli_style import ExitCode
from interlis.convert.cartosym import ScaleRangeError, SignLibrary, graphic_to_style, scale_selector, write_sld
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.parse import parse_xtf

_FIXTURES = Path(__file__).parent / "fixtures" / "cartosym"
_MODEL = _FIXTURES / "scale_example.ili"
_SIGN_XTF = _FIXTURES / "scale_example.xtf"
_REPO_DIR = _FIXTURES / "scale_repo"
_SE = {"se": "http://www.opengis.net/se"}
_SD = {"sysId": "viz.sd"}
_FEATURE = {"op": "=", "args": [{"sysId": "dataLayer.id"}, "Road"]}


@pytest.fixture(scope="module")
def graphics():
    builder = build_from_file(_MODEL, repository=ModelRepository([_REPO_DIR]))
    return {
        i.Name: i
        for i in builder.symbol_table.all_registered()
        if isinstance(i, MetaInstance) and i._qualified_class.rsplit(".", 1)[-1] == "Graphic"
    }


@pytest.fixture(scope="module")
def library() -> SignLibrary:
    return SignLibrary(parse_xtf(_SIGN_XTF).baskets[0])


def _sld(graphics, library, name: str) -> etree._Element:
    return etree.fromstring(write_sld(graphic_to_style(graphics[name], library)).encode("utf-8"))


def _bounds(root: etree._Element) -> list[tuple[str | None, str | None]]:
    """(min, max) text of every `se:Rule`, in document order."""
    return [
        (
            rule.findtext("se:MinScaleDenominator", namespaces=_SE),
            rule.findtext("se:MaxScaleDenominator", namespaces=_SE),
        )
        for rule in root.iterfind(".//se:Rule", _SE)
    ]


def test_min_only_sets_a_lower_bound(graphics, library):
    (rule,) = graphic_to_style(graphics["Road_MinOnly"], library).styling_rules
    assert rule.selector == {"op": "and", "args": [_FEATURE, {"op": ">=", "args": [_SD, 25000]}]}
    assert _bounds(_sld(graphics, library, "Road_MinOnly")) == [("25000", None)]


def test_max_only_sets_an_upper_bound(graphics, library):
    (rule,) = graphic_to_style(graphics["Road_MaxOnly"], library).styling_rules
    assert rule.selector == {"op": "and", "args": [_FEATURE, {"op": "<", "args": [_SD, 100000]}]}
    assert _bounds(_sld(graphics, library, "Road_MaxOnly")) == [(None, "100000")]


def test_both_bounds_follow_the_schema_element_order(graphics, library):
    root = _sld(graphics, library, "Road_Both")
    assert _bounds(root) == [("1000", "100000")]
    rule = root.find(".//se:Rule", _SE)
    names = [etree.QName(child).localname for child in rule]
    assert names.index("MinScaleDenominator") < names.index("MaxScaleDenominator") < names.index("LineSymbolizer")


def test_bounds_combine_with_the_where_clause_on_the_same_rule(graphics, library):
    root = _sld(graphics, library, "Road_Where")
    assert _bounds(root) == [("5000", "200000")]
    rule = root.find(".//se:Rule", _SE)
    assert rule.find("{http://www.opengis.net/ogc}Filter") is not None
    assert len(list(root.iterfind(".//se:Rule", _SE))) == 1


def test_bounds_stay_on_their_own_rule_across_drawing_passes(graphics, library):
    root = _sld(graphics, library, "Road_Passes")
    styles = root.findall(".//se:FeatureTypeStyle", _SE)
    assert len(styles) == 2
    per_pass = [
        (
            rule.findtext("se:MinScaleDenominator", namespaces=_SE),
            rule.findtext("se:MaxScaleDenominator", namespaces=_SE),
        )
        for style in styles
        for rule in style.iterfind("se:Rule", _SE)
    ]
    assert per_pass == [(None, "50000"), ("10000", None)]


def test_absent_or_zero_bounds_leave_the_rule_unrestricted(graphics, library):
    assert _bounds(_sld(graphics, library, "Road_NoScale")) == [(None, None)]
    assert write_sld(graphic_to_style(graphics["Road_Zero"], library)) == write_sld(
        graphic_to_style(graphics["Road_NoScale"], library)
    ).replace("Road_NoScale", "Road_Zero")


def test_bounds_survive_a_round_trip_through_the_sld_reader(graphics, library):
    sld = write_sld(graphic_to_style(graphics["Road_Both"], library))
    style = get_codec("sld").read(sld)
    selector = style.styling_rules[0].selector
    conjuncts = selector["args"] if selector["op"] == "and" else [selector]
    flat = [c for part in conjuncts for c in (part["args"] if part.get("op") == "and" else [part])]
    assert {"op": ">=", "args": [_SD, 1000]} in flat
    assert {"op": "<", "args": [_SD, 100000]} in flat


def test_a_range_that_selects_no_scale_is_refused(graphics, library):
    with pytest.raises(ScaleRangeError, match="not below"):
        graphic_to_style(graphics["Road_Inverted"], library)


def test_scale_selector_rules():
    assert scale_selector({}) is None
    assert scale_selector({"MinScaleDenominator": 0, "MaxScaleDenominator": 0}) is None
    assert scale_selector({"MaxScaleDenominator": 2500.5}) == {"op": "<", "args": [_SD, 2500.5]}
    with pytest.raises(ScaleRangeError):
        scale_selector({"MinScaleDenominator": 5000, "MaxScaleDenominator": 5000})
    with pytest.raises(NotImplementedError, match="literal number"):
        scale_selector({"MinScaleDenominator": {"property": "Scale"}})


def test_cli_skips_only_the_refused_graphic(tmp_path, capsys):
    out_dir = tmp_path / "sld"
    exit_code = main(
        [
            "convert-sld",
            str(_MODEL),
            "--repo",
            str(_REPO_DIR),
            "--sign-xtf",
            str(_SIGN_XTF),
            "-o",
            str(out_dir),
        ]
    )
    assert exit_code == ExitCode.OK
    written = {p.stem for p in out_dir.glob("*.sld")}
    assert "Road_Both" in written and "Road_Inverted" not in written
    assert "Road_Inverted" in capsys.readouterr().err
