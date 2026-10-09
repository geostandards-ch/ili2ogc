"""A COORD domain keeps one axis per declared component, whether it is a range or a bare NUMERIC."""

import pytest
from conftest import build_from_text

from interlis.xtf.schema import attributes_of, coord_axes, resolve_attribute

_MODEL = """INTERLIS 2.4;
MODEL Axes AT "http://x" VERSION "1" =
  DOMAIN
    Bare2 = COORD NUMERIC, NUMERIC;
    Bare3 = COORD NUMERIC, NUMERIC, NUMERIC;
    Unit3 = COORD NUMERIC [INTERLIS.m], NUMERIC [INTERLIS.m], NUMERIC [INTERLIS.m];
    Range3 = COORD 0.000 .. 10.000 [INTERLIS.m], 0.000 .. 20.000 [INTERLIS.m], 0.000 .. 30.000 [INTERLIS.m];
  TOPIC T =
    CLASS A =
      B2: Bare2;
      B3: Bare3;
      U3: Unit3;
      R3: Range3;
    END A;
  END T;
END Axes.
"""


@pytest.fixture(scope="module")
def axes():
    builder = build_from_text(_MODEL)
    members = attributes_of(builder.symbol_table.resolve("A"))
    return {name: coord_axes(resolve_attribute(attr).type_instance) for name, attr in members.items()}


@pytest.mark.parametrize("name, count", [("B2", 2), ("B3", 3), ("U3", 3), ("R3", 3)])
def test_every_declared_axis_is_kept(axes, name, count):
    assert len(axes[name]) == count


def test_range_axes_keep_their_own_bounds(axes):
    assert [axis.Max for axis in axes["R3"]] == ["10.000", "20.000", "30.000"]
