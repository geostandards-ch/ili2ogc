"""CLI wiring for `interlis convert-sld`, against the real `RoadsExgm2ien.ili`/`RoadsExgm2ien_Symbols.xtf` fixture."""

from pathlib import Path

from interlis.cli import main
from interlis.cli_style import ExitCode

_FIXTURES = Path(__file__).parent / "fixtures" / "cartosym"
_MODEL = _FIXTURES / "roadsexgm2ien" / "RoadsExgm2ien.ili"
_SIGN_XTF = _FIXTURES / "roadsexgm2ien" / "RoadsExgm2ien_Symbols.xtf"
_REPO = _FIXTURES / "roadsexgm2ien_repo"


def test_convert_sld_single_graphic_prints_to_stdout(capsys):
    exit_code = main(
        [
            "convert-sld",
            str(_MODEL),
            "--repo",
            str(_REPO),
            "--sign-xtf",
            str(_SIGN_XTF),
            "--graphic",
            "Text_Graphics",
        ]
    )
    assert exit_code == ExitCode.OK
    out = capsys.readouterr().out
    assert "<se:Name>Text_Graphics</se:Name>" in out
    assert "<se:FeatureTypeName>StreetNamePosition</se:FeatureTypeName>" in out
    assert "<ogc:PropertyName>Street.Name</ogc:PropertyName>" in out


def test_convert_sld_multi_graphic_requires_output_dir(capsys):
    exit_code = main(["convert-sld", str(_MODEL), "--repo", str(_REPO)])
    assert exit_code == ExitCode.USAGE
    err = capsys.readouterr().err
    assert "5 GRAPHICs" in err
    assert "--graphic" in err and "-o DIR" in err


def test_convert_sld_multi_graphic_writes_one_file_per_graphic(tmp_path):
    out_dir = tmp_path / "sld"
    exit_code = main(
        [
            "convert-sld",
            str(_MODEL),
            "--repo",
            str(_REPO),
            "--sign-xtf",
            str(_SIGN_XTF),
            "-o",
            str(out_dir),
        ]
    )
    assert exit_code == ExitCode.OK
    written = {p.stem for p in out_dir.glob("*.sld")}
    assert written == {
        "Surface_Graphics",
        "SurfaceBoundary_Graphics",
        "Polyline_Graphics",
        "Text_Graphics",
        "Point_Graphics",
    }


_NO_CONTENT_MODEL = """INTERLIS 2.4;
MODEL Foo AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS SurfaceSign = Dummy: TEXT*1; END SurfaceSign;
    CLASS LandCover = Type: (building, other); Geometry: TEXT*1; END LandCover;
    GRAPHIC G BASED ON LandCover =
      Building OF SurfaceSign:
        WHERE Type == #building (
          Geometry := Geometry;
          Priority := 1
        );
    END G;
  END T;
END Foo.
"""


def test_convert_sld_skips_a_graphic_with_no_representable_content(tmp_path, capsys):
    """No `Sign := {...}` at all - every rule ends up symbolizer-less (only `z_order`, `Priority`)."""
    model_path = tmp_path / "Foo.ili"
    model_path.write_text(_NO_CONTENT_MODEL, encoding="utf-8")

    exit_code = main(["convert-sld", str(model_path), "--graphic", "G"])
    assert exit_code == ExitCode.INVALID
    err = capsys.readouterr().err
    assert "GRAPHIC 'G'" in err
    assert "no GRAPHIC produced any SLD-representable content" in err


def test_convert_sld_unknown_graphic_name_exits_not_found(capsys):
    exit_code = main(["convert-sld", str(_MODEL), "--repo", str(_REPO), "--graphic", "Nope"])
    assert exit_code == ExitCode.NOT_FOUND
    assert "no GRAPHIC named 'Nope'" in capsys.readouterr().err


def test_convert_sld_missing_model_file_exits_not_found(tmp_path, capsys):
    exit_code = main(["convert-sld", str(tmp_path / "nope.ili")])
    assert exit_code == ExitCode.NOT_FOUND
    assert capsys.readouterr().err.startswith("error: .ili file not found:")


def test_convert_sld_missing_sign_xtf_exits_not_found(tmp_path, capsys):
    exit_code = main(["convert-sld", str(_MODEL), "--repo", str(_REPO), "--sign-xtf", str(tmp_path / "nope.xtf")])
    assert exit_code == ExitCode.NOT_FOUND
    assert capsys.readouterr().err.startswith("error: .xtf file not found:")


def test_convert_sld_priority_sorts_rules_ascending_last_drawn_on_top(capsys):
    """`Surface_Graphics`'s 4 rules share the same real Priority (100) - a stable sort keeps declaration order."""
    exit_code = main(
        [
            "convert-sld",
            str(_MODEL),
            "--repo",
            str(_REPO),
            "--sign-xtf",
            str(_SIGN_XTF),
            "--graphic",
            "Surface_Graphics",
        ]
    )
    assert exit_code == ExitCode.OK
    out = capsys.readouterr().out
    assert out.index("<se:Title>Building</se:Title>") < out.index("<se:Title>Street</se:Title>")
    assert out.index("<se:Title>Street</se:Title>") < out.index("<se:Title>Water</se:Title>")
    assert out.index("<se:Title>Water</se:Title>") < out.index("<se:Title>Other</se:Title>")
