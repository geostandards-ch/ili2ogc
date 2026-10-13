"""An object linked to too few or too many objects through an association role is an error of the XTF validator."""

import pytest
from conftest import build_from_file

from interlis.xtf.parse import parse_xtf
from interlis.xtf.validate import validate_transfer

MODEL = """INTERLIS 2.4;
MODEL Card (en) AT "https://example.org/" VERSION "2026-01-01" =
  TOPIC T =
    CLASS Plan = Year: 1990 .. 2100; END Plan;
    CLASS Cost = Amount: 0 .. 1000; END Cost;
    CLASS Tag = Label: TEXT*10; END Tag;
    CLASS Item = Name: TEXT*10; END Item;
    ASSOCIATION PlanCost =
      rPlan -<#> {1} Plan;
      rCosts -- {1..*} Cost;
    END PlanCost;
    ASSOCIATION ItemTag =
      rItem -- {0..2} Item;
      rTag -- {1..*} Tag;
    END ItemTag;
  END T;
END Card.
"""

_XTF = """<?xml version="1.0" encoding="UTF-8"?>
<TRANSFER xmlns="http://www.interlis.ch/INTERLIS2.3"><HEADERSECTION SENDER="t" VERSION="2.3"><MODELS>
<MODEL NAME="Card" VERSION="2026-01-01" URI="https://example.org/"></MODEL></MODELS></HEADERSECTION>
<DATASECTION><Card.T BID="b">{objects}</Card.T></DATASECTION></TRANSFER>
"""


@pytest.fixture(scope="module")
def builder(tmp_path_factory):
    path = tmp_path_factory.mktemp("card") / "Card.ili"
    path.write_text(MODEL, encoding="utf-8")
    return build_from_file(path)


def _errors(builder, tmp_path, objects):
    path = tmp_path / "t.xtf"
    path.write_text(_XTF.format(objects=objects), encoding="utf-8")
    issues = validate_transfer(parse_xtf(path), symbol_table=builder.symbol_table)
    return [(i.object_tid, i.attribute, i.message) for i in issues if i.severity == "error"]


_PLAN = '<Card.T.Plan TID="p1"><Year>2000</Year></Card.T.Plan>'
_COST = '<Card.T.Cost TID="c1"><Amount>5</Amount><rPlan REF="p1"/></Card.T.Cost>'


def test_an_object_with_the_required_links_has_no_issue(builder, tmp_path):
    assert _errors(builder, tmp_path, _PLAN + _COST) == []


def test_an_object_without_a_required_link_is_an_error(builder, tmp_path):
    assert _errors(builder, tmp_path, _PLAN) == [
        ("p1", "rCosts", "rCosts should associate 1 to * target objects (instead of 0)")
    ]


_ITEMS = "".join(f'<Card.T.Item TID="i{n}"><Name>a</Name></Card.T.Item>' for n in (1, 2, 3))
_TAG = '<Card.T.Tag TID="t1"><Label>x</Label></Card.T.Tag>'


def _link(item):
    return f'<Card.T.ItemTag><rItem REF="{item}"/><rTag REF="t1"/></Card.T.ItemTag>'


def test_a_link_object_counts_the_targets_of_each_end(builder, tmp_path):
    objects = _ITEMS + _TAG + _link("i1") + _link("i2")
    assert _errors(builder, tmp_path, objects) == [
        ("i3", "rTag", "rTag should associate 1 to * target objects (instead of 0)")
    ]


def test_a_role_with_too_many_targets_is_an_error(builder, tmp_path):
    objects = _ITEMS + _TAG + _link("i1") + _link("i2") + _link("i3")
    assert _errors(builder, tmp_path, objects) == [
        ("t1", "rItem", "rItem should associate 0 to 2 target objects (instead of 3)")
    ]
