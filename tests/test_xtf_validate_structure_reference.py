"""A `REFERENCE TO` nested inside a STRUCTURE-typed attribute (the catalogue-reference wrapper pattern).

The REF is checked against the inner `Reference` attribute's declared
target, never against the wrapper STRUCTURE itself.
"""

from pathlib import Path

from interlis.cli import main

_MODEL = """INTERLIS 2.3;
MODEL M AT "http://x" VERSION "1" =
  TOPIC Cat =
    CLASS Item =
      Code : MANDATORY TEXT*5;
    END Item;
    CLASS Other =
      Code : MANDATORY TEXT*5;
    END Other;
    STRUCTURE ItemRef =
      Reference : MANDATORY REFERENCE TO (EXTERNAL) Item;
    END ItemRef;
  END Cat;
  TOPIC Data =
    DEPENDS ON M.Cat;
    CLASS Thing =
      Kind : MANDATORY M.Cat.ItemRef;
    END Thing;
  END Data;
END M.
"""

_TRANSFER = """<?xml version="1.0" encoding="UTF-8"?>
<TRANSFER xmlns="http://www.interlis.ch/INTERLIS2.3">
<HEADERSECTION SENDER="test" VERSION="2.3"><MODELS><MODEL NAME="M" VERSION="1" URI="http://x"/></MODELS></HEADERSECTION>
<DATASECTION>
<M.Cat BID="b1">
<M.Cat.Item TID="i1"><Code>a</Code></M.Cat.Item>
<M.Cat.Other TID="o1"><Code>b</Code></M.Cat.Other>
</M.Cat>
<M.Data BID="b2">
<M.Data.Thing TID="t1"><Kind><M.Cat.ItemRef><Reference REF="{ref}"/></M.Cat.ItemRef></Kind></M.Data.Thing>
</M.Data>
</DATASECTION>
</TRANSFER>
"""


def _validate(tmp_path: Path, ref: str, capsys) -> tuple[int, str]:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    (tmp_path / "data.xtf").write_text(_TRANSFER.format(ref=ref), encoding="utf-8")
    code = main(["validate", str(tmp_path / "data.xtf"), "--model", str(tmp_path / "M.ili"), "--repo", str(tmp_path)])
    captured = capsys.readouterr()
    return code, captured.out + captured.err


def test_ref_to_the_inner_attributes_declared_class_has_no_error(tmp_path: Path, capsys):
    _code, output = _validate(tmp_path, "i1", capsys)
    assert "incompatible" not in output
    assert "0 error(s)" in output


def test_ref_to_another_class_is_flagged_on_the_inner_attribute(tmp_path: Path, capsys):
    _code, output = _validate(tmp_path, "o1", capsys)
    assert "Thing[t1].Kind.Reference" in output
    assert "incompatible with declared class 'Item'" in output
