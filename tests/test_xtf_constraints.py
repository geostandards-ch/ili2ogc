"""Constraint checks of an XTF transfer (`src/interlis/xtf/constraints.py`), on synthetic transfers.

The model covers each constraint form the checker evaluates (MANDATORY with a
reference hop, inherited MANDATORY, plausibility, UNIQUE global/BASKET/WHERE/
LOCAL) and the forms it reports as not evaluated (SET, EXISTENCE, a function
call).
"""

import pytest
from conftest import build_from_text

from interlis.xtf.constraints import check_transfer_constraints
from interlis.xtf.parse import RawNode, XtfBasket, XtfObject, XtfTransfer
from interlis.xtf.validate import validate_transfer

MODEL = """INTERLIS 2.3;
MODEL CT AT "http://x" VERSION "1" =
  TOPIC T =
    STRUCTURE Entry =
      Sub: TEXT*5;
      Other: TEXT*5;
    END Entry;

    CLASS Owner =
      Code: TEXT*10;
    END Owner;

    CLASS Base =
      Name: TEXT*10;
      Num: 0 .. 100;
      MANDATORY CONSTRAINT Num >= 10;
      UNIQUE Name;
    END Base;

    CLASS Derived EXTENDS Base =
      Entries: BAG OF Entry;
      UNIQUE (LOCAL) Entries: Sub;
    END Derived;

    CLASS Linked =
      Ref: REFERENCE TO Owner;
      Key: TEXT*10;
      Kind: TEXT*10;
      Mark: TEXT*10;
      MANDATORY CONSTRAINT Ref->Code == "A";
      UNIQUE WHERE Kind == "live": Key;
      UNIQUE (BASKET) Mark;
    END Linked;

    CLASS Rated =
      Flag: TEXT*5;
      CONSTRAINT >= 50% Flag == "yes";
    END Rated;

    CLASS Site =
      Kind: (k1, k2);
      Active: BOOLEAN;
      Dep: TEXT*5;
      MANDATORY CONSTRAINT NOT (Kind == #k1 AND Active AND NOT (DEFINED (Dep)));
    END Site;

    CLASS Consumer =
      Own: TEXT*10;
      EXISTENCE CONSTRAINT Own REQUIRED IN Owner: Code OR Base: Name;
    END Consumer;

    CLASS Counted =
      Name: TEXT*10;
      SET CONSTRAINT INTERLIS.objectCount(ALL) < 5;
      MANDATORY CONSTRAINT INTERLIS.len(Name) > 2;
    END Counted;
  END T;
END CT.
"""


@pytest.fixture(scope="module")
def builder():
    return build_from_text(MODEL)


def _node(tag: str, text: str | None = None, *, ref: str | None = None, children=()):
    return RawNode(tag=tag, text=text, attrib={"REF": ref} if ref else {}, children=list(children))


def _obj(cls: str, tid: str, **attrs) -> XtfObject:
    nodes = {}
    for name, value in attrs.items():
        nodes[name] = [value] if isinstance(value, RawNode) else [_node(name, value)]
    return XtfObject(tid=tid, qualified_class=f"CT.T.{cls}", attributes=nodes)


def _check(builder, *baskets: list[XtfObject]):
    transfer = XtfTransfer(
        sender=None,
        ili_version=None,
        models=[],
        baskets=[
            XtfBasket(bid=f"b{i}", qualified_topic="CT.T", kind=None, endstate=None, objects=list(objs))
            for i, objs in enumerate(baskets)
        ],
    )
    return check_transfer_constraints(transfer, symbol_table=builder.symbol_table)


def _by_severity(issues, severity):
    return [i for i in issues if i.severity == severity]


def test_mandatory_constraint_is_checked_per_object(builder):
    issues = _check(builder, [_obj("Base", "o1", Num="5"), _obj("Base", "o2", Num="50")])
    errors = _by_severity(issues, "error")
    assert [(i.object_tid, "Num >= 10" in i.message) for i in errors] == [("o1", True)]


def test_undefined_value_counts_as_satisfied(builder):
    assert _check(builder, [_obj("Base", "o1", Name="x")]) == []


def test_mandatory_follows_a_reference(builder):
    owners = [_obj("Owner", "w1", Code="A"), _obj("Owner", "w2", Code="B")]
    linked = [_obj("Linked", "l1", Ref=_node("Ref", ref="w1")), _obj("Linked", "l2", Ref=_node("Ref", ref="w2"))]
    errors = _by_severity(_check(builder, owners, linked), "error")
    assert [i.object_tid for i in errors] == ["l2"]


def test_inherited_constraint_applies_to_the_subclass(builder):
    errors = _by_severity(_check(builder, [_obj("Derived", "d1", Num="3")]), "error")
    assert [i.object_tid for i in errors] == ["d1"]


def test_unique_spans_all_baskets_and_subclasses(builder):
    issues = _check(builder, [_obj("Base", "o1", Name="x", Num="20")], [_obj("Derived", "o2", Name="x", Num="20")])
    errors = _by_severity(issues, "error")
    assert [i.object_tid for i in errors] == ["o2"]
    assert "'o1'" in errors[0].message


def test_unique_ignores_objects_with_an_undefined_key(builder):
    assert _check(builder, [_obj("Base", "o1", Num="20"), _obj("Base", "o2", Num="20")]) == []


def test_unique_basket_scope_allows_the_same_value_in_two_baskets(builder):
    owner = [_obj("Owner", "w1", Code="A")]
    first = [_obj("Linked", "l1", Ref=_node("Ref", ref="w1"), Mark="m")]
    second = [_obj("Linked", "l2", Ref=_node("Ref", ref="w1"), Mark="m")]
    assert _check(builder, owner, first, second) == []
    twice = [
        _obj("Linked", "l3", Ref=_node("Ref", ref="w1"), Mark="m"),
        _obj("Linked", "l4", Ref=_node("Ref", ref="w1"), Mark="m"),
    ]
    assert [i.object_tid for i in _by_severity(_check(builder, owner, twice), "error")] == ["l4"]


def test_unique_where_restricts_the_checked_objects(builder):
    owner = [_obj("Owner", "w1", Code="A")]
    ref = _node("Ref", ref="w1")
    dead = [_obj("Linked", "l1", Ref=ref, Key="k", Kind="dead"), _obj("Linked", "l2", Ref=ref, Key="k", Kind="dead")]
    assert _check(builder, owner, dead) == []
    live = [_obj("Linked", "l3", Ref=ref, Key="k", Kind="live"), _obj("Linked", "l4", Ref=ref, Key="k", Kind="live")]
    assert [i.object_tid for i in _by_severity(_check(builder, owner, live), "error")] == ["l4"]


def _entries(*subs: str) -> RawNode:
    return _node("Entries", children=[_node("CT.T.Entry", children=[_node("Sub", sub)]) for sub in subs])


def test_local_unique_is_checked_within_one_object(builder):
    ok = _obj("Derived", "d1", Num="20", Entries=_entries("a", "b"))
    bad = _obj("Derived", "d2", Num="20", Entries=_entries("a", "a"))
    errors = _by_severity(_check(builder, [ok, bad]), "error")
    assert [i.object_tid for i in errors] == ["d2"]
    assert errors[0].attribute == "Entries"


def test_plausibility_constraint_warns_below_its_percentage(builder):
    flags = ["yes"] * 4 + ["no"] * 6
    warnings = _by_severity(_check(builder, [_obj("Rated", f"r{i}", Flag=f) for i, f in enumerate(flags)]), "warning")
    assert len(warnings) == 1 and "40.0%" in warnings[0].message
    flags = ["yes"] * 6 + ["no"] * 4
    assert _by_severity(_check(builder, [_obj("Rated", f"r{i}", Flag=f) for i, f in enumerate(flags)]), "warning") == []


def test_existence_constraint_requires_the_value_in_one_of_the_classes(builder):
    owners = [_obj("Owner", "w1", Code="A")]
    bases = [_obj("Base", "b1", Name="B", Num="20")]
    consumers = [_obj("Consumer", f"c{i}", Own=own) for i, own in enumerate(["A", "B", "Z"])]
    errors = _by_severity(_check(builder, owners, bases, consumers), "error")
    assert [(i.object_tid, "'Z'" in i.message) for i in errors] == [("c2", True)]


def test_existence_constraint_is_not_evaluated_without_the_required_objects(builder):
    issues = _check(builder, [_obj("Consumer", "c0", Own="Z")])
    assert _by_severity(issues, "error") == []
    assert any("EXISTENCE CONSTRAINT" in i.message and "not evaluated" in i.message for i in issues)


def test_boolean_attributes_read_from_a_transfer_are_booleans(builder):
    active = _obj("Site", "s1", Kind="k1", Active="true")
    inactive = _obj("Site", "s2", Kind="k1", Active="false")
    errors = _by_severity(_check(builder, [active, inactive]), "error")
    assert [i.object_tid for i in errors] == ["s1"]


def test_unsupported_constraints_are_reported_once_as_info(builder):
    infos = _by_severity(_check(builder, [_obj("Counted", f"c{i}", Name="abc") for i in range(8)]), "info")
    assert sorted(i.message.split(" not evaluated")[0] for i in infos) == ["SET CONSTRAINT", "constraint"]
    assert all(i.object_tid is None for i in infos)


def test_validate_transfer_includes_constraints_unless_disabled(builder):
    transfer = XtfTransfer(
        sender=None,
        ili_version=None,
        models=[],
        baskets=[
            XtfBasket(bid="b0", qualified_topic="CT.T", kind=None, endstate=None, objects=[_obj("Base", "o1", Num="5")])
        ],
    )
    with_constraints = validate_transfer(transfer, symbol_table=builder.symbol_table)
    without = validate_transfer(transfer, symbol_table=builder.symbol_table, check_constraints=False)
    assert any("Num >= 10" in i.message for i in with_constraints)
    assert not any("Num >= 10" in i.message for i in without)
