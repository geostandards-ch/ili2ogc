"""`interlis validate-jsonfg`: exit codes, the issue listing and the SARIF report."""

import json

import pytest
from conftest import ROOT

from interlis.cli import main
from interlis.cli_style import ExitCode

_DIR = ROOT / "tests/fixtures/solid3d"
_MODEL = str(_DIR / "Building3D_V1.ili")
_JSONFG = _DIR / "building3d.jsonfg.json"


def _run(capsys, path, *extra):
    code = main(["validate-jsonfg", str(path), "--model", _MODEL, "--repo", str(_DIR), *extra])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def _mutated(tmp_path, change):
    document = json.loads(_JSONFG.read_text(encoding="utf-8"))
    change(document)
    path = tmp_path / "mutated.jsonfg.json"
    path.write_text(json.dumps(document), encoding="utf-8")
    return path


def test_a_conforming_file_exits_ok(capsys):
    code, out, _err = _run(capsys, _JSONFG)
    assert code == ExitCode.OK and "0 issue(s)" in out


def test_a_defect_exits_with_an_error_and_names_the_layer(tmp_path, capsys):
    def change(document):
        document["features"][0]["properties"]["Name"] = "x" * 200

    code, out, _err = _run(capsys, _mutated(tmp_path, change))
    assert code == ExitCode.ERROR
    assert "[error  ] " in out and "[properties]" in out and ".Name:" in out


def test_a_geometry_defect_is_reported(tmp_path, capsys):
    def change(document):
        document["features"][0]["coordRefSys"] = "http://www.opengis.net/def/crs/EPSG/0/21781"

    code, out, _err = _run(capsys, _mutated(tmp_path, change))
    assert code == ExitCode.ERROR and "[geometry]" in out and "coordRefSys" in out


def test_the_sarif_report_carries_the_issues(tmp_path, capsys):
    def change(document):
        document["features"][0]["featureType"] = "Lot"

    report = tmp_path / "report.sarif"
    code, _out, _err = _run(capsys, _mutated(tmp_path, change), "--report", str(report))
    assert code == ExitCode.ERROR
    results = json.loads(report.read_text(encoding="utf-8"))["runs"][0]["results"]
    assert any("featureType" in r["message"]["text"] for r in results)


def test_a_missing_json_file_exits_not_found(tmp_path, capsys):
    code, _out, err = _run(capsys, tmp_path / "nope.json")
    assert code == ExitCode.NOT_FOUND and "JSON-FG file not found" in err


def test_a_file_that_is_not_json_exits_invalid(tmp_path, capsys):
    bad = tmp_path / "bad.json"
    bad.write_text("{not json", encoding="utf-8")
    code, _out, err = _run(capsys, bad)
    assert code == ExitCode.INVALID and "is not valid JSON" in err


def test_the_model_is_required():
    with pytest.raises(SystemExit):
        main(["validate-jsonfg", str(_JSONFG)])
