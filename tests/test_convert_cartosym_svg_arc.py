"""`convert/cartosym.py::_svg_arc_command` - SVG arc flags/radius derived from 3 points, checked by hand.

`_svg_arc_command` never sees a target angle/flag directly - it derives
everything from 3 raw points (INTERLIS's own arc encoding: start,
mid/through-point, end). These cases are worked out by hand (unit circle,
easy angles) rather than round-tripped through the function itself, so a
sign error in the sweep/large-arc derivation would actually be caught.
"""

import math
import re

from interlis.convert.cartosym import _svg_arc_command

_PATTERN = re.compile(r"A ([\d.]+) ([\d.]+) 0 (\d) (\d) ([\d.eE+-]+) ([\d.eE+-]+)")


def _parse(command: str) -> tuple[float, int, int, float, float]:
    m = _PATTERN.match(command)
    assert m, command
    rx, ry, large_arc, sweep, x, y = m.groups()
    assert rx == ry
    return float(rx), int(large_arc), int(sweep), float(x), float(y)


def test_quarter_circle_counterclockwise_in_svg_angle_terms():
    """Unit circle, 0 deg -> 90 deg through 45 deg - minor arc, increasing angle (sweep=1)."""
    start = (1.0, 0.0)
    mid = (math.cos(math.radians(45)), math.sin(math.radians(45)))
    end = (0.0, 1.0)
    radius, large_arc, sweep, x, y = _parse(_svg_arc_command(start, mid, end))
    assert radius == 1.0
    assert large_arc == 0
    assert sweep == 1
    assert (x, y) == (0.0, 1.0)


def test_quarter_circle_reversed_is_the_other_sweep_direction():
    """Same 3 points, start/end swapped - same minor arc, now the decreasing-angle direction (sweep=0)."""
    start = (0.0, 1.0)
    mid = (math.cos(math.radians(45)), math.sin(math.radians(45)))
    end = (1.0, 0.0)
    radius, large_arc, sweep, x, y = _parse(_svg_arc_command(start, mid, end))
    assert radius == 1.0
    assert large_arc == 0
    assert sweep == 0
    assert (x, y) == (1.0, 0.0)


def test_major_arc_sets_the_large_arc_flag():
    """0 deg -> 270 deg through 180 deg - a 270 deg major arc, increasing angle (sweep=1), large_arc=1."""
    start = (1.0, 0.0)
    mid = (-1.0, 0.0)
    end = (0.0, -1.0)
    radius, large_arc, sweep, x, y = _parse(_svg_arc_command(start, mid, end))
    assert radius == 1.0
    assert large_arc == 1
    assert sweep == 1
    assert abs(x) < 1e-9
    assert y == -1.0


def test_collinear_points_return_none():
    assert _svg_arc_command((0.0, 0.0), (1.0, 0.0), (2.0, 0.0)) is None
