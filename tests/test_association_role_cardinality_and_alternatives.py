"""Role defaults (no cardinality = {0..*}, a composition {0..1}) and alternative role targets (`-- A OR B`)."""

from pathlib import Path

from interlis.cli import main

_MODEL = """INTERLIS 2.3;
MODEL M AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS A =
      Code : TEXT*5;
    END A;
    CLASS B =
      Code : TEXT*5;
    END B;
    CLASS C =
      Code : TEXT*5;
    END C;
    ASSOCIATION A_B =
      a -- A;
      b -- B;
    END A_B;
    ASSOCIATION Owner =
      owner -- {1} A OR B;
      owned -- {0..*} C;
    END Owner;
  END T;
END M.
"""

_TRANSFER = """<?xml version="1.0" encoding="UTF-8"?>
<TRANSFER xmlns="http://www.interlis.ch/INTERLIS2.3">
<HEADERSECTION SENDER="test" VERSION="2.3"><MODELS><MODEL NAME="M" VERSION="1" URI="http://x"/></MODELS></HEADERSECTION>
<DATASECTION>
<M.T BID="b1">
<M.T.A TID="a1"><Code>a</Code></M.T.A>
<M.T.B TID="b1"><Code>b</Code></M.T.B>
<M.T.C TID="c1"><Code>c</Code><owner REF="{ref}"/></M.T.C>
</M.T>
</DATASECTION>
</TRANSFER>
"""


def _sql(tmp_path: Path) -> str:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    out = tmp_path / "out.sql"
    main(["convert-sql", str(tmp_path / "M.ili"), "--repo", str(tmp_path), "-o", str(out)])
    return out.read_text(encoding="utf-8")


def test_roles_without_cardinality_are_n_to_m(tmp_path: Path):
    link = _sql(tmp_path).split('CREATE TABLE "a_b" (', 1)[1].split("\n);", 1)[0]
    assert '"a" bigint NOT NULL' in link
    assert '"b" bigint NOT NULL' in link


def test_alternative_targets_get_one_column_each(tmp_path: Path):
    c = _sql(tmp_path).split('CREATE TABLE "c" (', 1)[1].split("\n);", 1)[0]
    assert '"owner" bigint,' in c
    assert '"owner_b" bigint,' in c
    assert "ELSE 0 END) = 1)" in c


def _validate(tmp_path: Path, ref: str, capsys) -> str:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    (tmp_path / "data.xtf").write_text(_TRANSFER.format(ref=ref), encoding="utf-8")
    main(["validate", str(tmp_path / "data.xtf"), "--model", str(tmp_path / "M.ili"), "--repo", str(tmp_path)])
    captured = capsys.readouterr()
    return captured.out + captured.err


def test_ref_to_either_alternative_is_valid(tmp_path: Path, capsys):
    assert "incompatible" not in _validate(tmp_path, "b1", capsys)


def test_ref_to_neither_alternative_is_an_error(tmp_path: Path, capsys):
    assert "incompatible with declared class 'A'" in _validate(tmp_path, "c1", capsys)
