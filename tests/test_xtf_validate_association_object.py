"""A non-embedded (n:m) association is transferred as its own object whose elements are its role REFs."""

from pathlib import Path

from interlis.cli import main

_MODEL = """INTERLIS 2.3;
MODEL M AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS Line =
      Code : TEXT*5;
    END Line;
    CLASS Area =
      Code : TEXT*5;
    END Area;
    ASSOCIATION Line_Area =
      rArea -- {1..*} Area;
      rLine -- {1..*} Line;
    END Line_Area;
  END T;
END M.
"""

_TRANSFER = """<?xml version="1.0" encoding="UTF-8"?>
<TRANSFER xmlns="http://www.interlis.ch/INTERLIS2.3">
<HEADERSECTION SENDER="test" VERSION="2.3"><MODELS><MODEL NAME="M" VERSION="1" URI="http://x"/></MODELS></HEADERSECTION>
<DATASECTION>
<M.T BID="b1">
<M.T.Line TID="l1"><Code>a</Code></M.T.Line>
<M.T.Area TID="a1"><Code>b</Code></M.T.Area>
<M.T.Line_Area><rArea REF="{area}"/><rLine REF="l1"/></M.T.Line_Area>
</M.T>
</DATASECTION>
</TRANSFER>
"""


def _validate(tmp_path: Path, area_ref: str, capsys) -> str:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    (tmp_path / "data.xtf").write_text(_TRANSFER.format(area=area_ref), encoding="utf-8")
    main(["validate", str(tmp_path / "data.xtf"), "--model", str(tmp_path / "M.ili"), "--repo", str(tmp_path)])
    captured = capsys.readouterr()
    return captured.out + captured.err


def test_association_roles_are_known_members(tmp_path: Path, capsys):
    output = _validate(tmp_path, "a1", capsys)
    assert "attribute absent from schema" not in output
    assert "0 error(s), 0 warning(s)" in output


def test_association_role_ref_to_wrong_class_is_an_error(tmp_path: Path, capsys):
    output = _validate(tmp_path, "l1", capsys)
    assert "Line_Area[None].rArea" in output
    assert "incompatible with declared class 'Area'" in output
