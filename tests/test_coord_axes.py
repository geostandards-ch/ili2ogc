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


def test_jsonfg_place_keeps_the_transfer_axis_order(tmp_path):
    """C1 then C2 (easting, northing for a Swiss CRS) go to `place` unswapped, ROTATION or not."""
    import json

    from interlis.cli import main

    ili = tmp_path / "R.ili"
    ili.write_text(
        'INTERLIS 2.3;\nMODEL R AT "http://x" VERSION "1" =\n  !!@CRS=EPSG:2056\n'
        "  DOMAIN C2 = COORD 2460000.000 .. 2870000.000, 1045000.000 .. 1310000.000, ROTATION 2 -> 1;\n"
        "  TOPIC T =\n    CLASS P =\n      Pos : C2;\n    END P;\n  END T;\nEND R.\n",
        encoding="utf-8",
    )
    xtf = tmp_path / "r.xtf"
    xtf.write_text(
        '<?xml version="1.0" encoding="UTF-8"?><TRANSFER xmlns="http://www.interlis.ch/INTERLIS2.3"><HEADERSECTION '
        'SENDER="x" VERSION="2.3"><MODELS><MODEL NAME="R" VERSION="1" URI="http://x"></MODEL></MODELS></HEADERSECTION>'
        '<DATASECTION><R.T BID="b"><R.T.P TID="p"><Pos><COORD><C1>2600000.000</C1><C2>1200000.000</C2></COORD></Pos>'
        "</R.T.P></R.T></DATASECTION></TRANSFER>",
        encoding="utf-8",
    )
    out = tmp_path / "o.json"
    main(["convert-jsonfg", str(xtf), "--model", str(ili), "-o", str(out)])
    place = json.loads(out.read_text(encoding="utf-8"))["features"][0]["place"]
    assert place["coordinates"] == [2600000.0, 1200000.0]
