"""`paper_as_pixels` / `convert-sld --dpi`: paper metres written as pixels (an SLD number has no unit)."""

from pathlib import Path

import pytest
from pycartosym.models.types import UnitType

from interlis.cli import main
from interlis.cli_style import ExitCode
from interlis.convert.cartosym import meters, paper_as_millimetres, paper_as_pixels, polyline_sign_to_stroke

_FIXTURES = Path(__file__).parent / "fixtures" / "cartosym"


def test_meters_stay_metres_by_default():
    value = meters(0.0002)
    assert value.unit == UnitType.METERS and value.value == 0.0002


def test_paper_metres_become_pixels_at_the_dpi():
    with paper_as_pixels(96):
        value = meters(0.0002)  # a 0.2 mm line
    assert value.unit == UnitType.PIXELS
    assert value.value == pytest.approx(0.2 / 25.4 * 96)
    assert meters(0.0002).unit == UnitType.METERS  # restored after the block


def test_dash_lengths_are_relative_to_the_line_width():
    with paper_as_pixels(96):
        stroke = polyline_sign_to_stroke(width=0.0002, dash_pattern=[7.5, 2.5])  # 1.5 / 0.5 mm
    assert stroke.dash_pattern == [6, 2]
    plain = polyline_sign_to_stroke(width=0.0002, dash_pattern=[7.5, 2.5])
    assert plain.dash_pattern == [8, 2]  # unchanged without the option


def test_convert_sld_dpi_writes_pixel_widths(tmp_path, capsys):
    model = _FIXTURES / "surface_border_example.ili"
    signs = str(_FIXTURES / "surface_border_example.xtf")
    args = ["convert-sld", str(model), "--repo", str(_FIXTURES / "scale_repo"), "--sign-xtf", signs]
    assert main(args + ["--graphic", "Zone_Outline", "--dpi", "96"]) == ExitCode.OK
    assert f'name="stroke-width">{4 / 0.0254 * 96}' in capsys.readouterr().out
    assert main(args + ["--graphic", "Zone_Outline"]) == ExitCode.OK
    assert 'name="stroke-width">4<' in capsys.readouterr().out


def test_paper_metres_become_millimetres():
    with paper_as_millimetres():
        value = meters(0.0002)
        stroke = polyline_sign_to_stroke(width=0.0002, dash_pattern=[7.5, 2.5])  # 1.5 / 0.5 mm
    assert value.unit == UnitType.MILLIMETERS and value.value == pytest.approx(0.2)
    assert stroke.dash_pattern == [5, 2]  # 1.5 mm and 0.5 mm at 0.28 mm per pixel
    assert meters(0.0002).unit == UnitType.METERS


def test_convert_sld_paper_units_writes_pixels_at_the_writers_pixel_size(capsys):
    model = _FIXTURES / "surface_border_example.ili"
    args = ["convert-sld", str(model), "--repo", str(_FIXTURES / "scale_repo")]
    args += ["--sign-xtf", str(_FIXTURES / "surface_border_example.xtf"), "--graphic", "Zone_Outline"]
    assert main(args + ["--paper-units"]) == ExitCode.OK
    out = capsys.readouterr().out
    assert f'name="stroke-width">{round(4000 / 0.28, 4)}' in out and "units/pixel" in out
    assert main(args + ["--paper-units", "--dpi", "96"]) == ExitCode.USAGE
