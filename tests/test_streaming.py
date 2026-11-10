"""Streaming entry points: same objects and Features as the whole-transfer ones, without holding them all."""

import json
import tracemalloc
from pathlib import Path

from interlis.cli import main
from interlis.xtf.parse import iter_objects, parse_xtf, read_skeleton

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS A =
      Name : MANDATORY TEXT*20;
    END A;
  END T;
END M.
"""


def _xtf(path: Path, count: int) -> Path:
    objects = "".join(f'<M.T.A TID="o{i}"><Name>n{i}</Name></M.T.A>\n' for i in range(count))
    path.write_text(
        '<?xml version="1.0"?><TRANSFER xmlns="http://www.interlis.ch/INTERLIS2.3">'
        '<HEADERSECTION SENDER="x" VERSION="2.3"><MODELS><MODEL NAME="M" VERSION="1" URI="http://x"></MODEL></MODELS>'
        f'</HEADERSECTION><DATASECTION><M.T BID="b1">{objects}</M.T></DATASECTION></TRANSFER>',
        encoding="utf-8",
    )
    return path


def test_iter_objects_yields_what_parse_xtf_holds(tmp_path: Path):
    xtf = _xtf(tmp_path / "a.xtf", 5)
    streamed = [(b.bid, o.tid, o.qualified_class) for b, o in iter_objects(xtf)]
    held = [(b.bid, o.tid, o.qualified_class) for b in parse_xtf(xtf).baskets for o in b.objects]
    assert streamed == held and len(held) == 5


def test_read_skeleton_has_header_and_baskets_but_no_object(tmp_path: Path):
    skeleton = read_skeleton(_xtf(tmp_path / "a.xtf", 5))
    assert [m.name for m in skeleton.models] == ["M"] and skeleton.sender == "x"
    assert [(b.bid, b.qualified_topic, b.objects) for b in skeleton.baskets] == [("b1", "M.T", [])]


def test_iterating_does_not_grow_with_the_transfer(tmp_path: Path):
    xtf = _xtf(tmp_path / "big.xtf", 20000)
    tracemalloc.start()
    sum(1 for _ in iter_objects(xtf))
    streamed = tracemalloc.get_traced_memory()[1]
    tracemalloc.reset_peak()
    parse_xtf(xtf)
    held = tracemalloc.get_traced_memory()[1]
    tracemalloc.stop()
    assert streamed * 5 < held


def test_stream_option_writes_the_same_features(tmp_path: Path):
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    xtf = _xtf(tmp_path / "a.xtf", 4)
    whole, streamed = tmp_path / "w.json", tmp_path / "s.json"
    base = ["convert-jsonfg", str(xtf), "--model", str(tmp_path / "M.ili")]
    assert main([*base, "-o", str(whole)]) == 0
    assert main([*base, "--stream", "-o", str(streamed)]) == 0
    expected = json.loads(whole.read_text(encoding="utf-8"))
    got = json.loads(streamed.read_text(encoding="utf-8"))
    assert got == expected  # featureType / coordRefSys hoisted exactly as in the whole-collection output
    assert got["featureType"] == "A" and len(got["features"]) == 4


def test_stream_option_refuses_what_needs_the_whole_collection(tmp_path: Path):
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    xtf = _xtf(tmp_path / "a.xtf", 1)
    args = ["convert-jsonfg", str(xtf), "--model", str(tmp_path / "M.ili"), "--stream"]
    assert main([*args, "--feature-schema-url", "x.json"]) != 0


def test_validate_stream_reports_what_validate_reports(tmp_path: Path, capsys):
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    xtf = _xtf(tmp_path / "a.xtf", 3)
    xtf.write_text(
        xtf.read_text(encoding="utf-8").replace("<Name>n1</Name>", "").replace('TID="o2"', 'TID="o0"'), encoding="utf-8"
    )
    base = ["validate-xtf", str(xtf), "--model", str(tmp_path / "M.ili"), "--no-constraints"]
    main(base)
    whole = capsys.readouterr().out
    main([*base, "--stream"])
    streamed = capsys.readouterr().out
    assert whole == streamed and "already used by" in whole and "MANDATORY" in whole.upper()


def test_validate_stream_says_constraints_are_skipped(tmp_path: Path, capsys):
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    main(["validate-xtf", str(_xtf(tmp_path / "a.xtf", 1)), "--model", str(tmp_path / "M.ili"), "--stream"])
    assert "CONSTRAINTs" in capsys.readouterr().err


def test_import_stream_writes_the_same_sql(tmp_path: Path):
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    xtf = _xtf(tmp_path / "a.xtf", 6)
    whole, streamed = tmp_path / "w.sql", tmp_path / "s.sql"
    base = ["import-xtf", str(xtf), "--model", str(tmp_path / "M.ili"), "--dataset", "ds"]
    assert main([*base, "-o", str(whole)]) == 0
    assert main([*base, "--stream", "-o", str(streamed)]) == 0
    assert streamed.read_text(encoding="utf-8") == whole.read_text(encoding="utf-8")


def test_import_stream_replays_an_incremental_transfer_identically(tmp_path: Path):
    (tmp_path / "M.ili").write_text(
        _MODEL.replace("END A;", "END A;\n    CLASS B =\n      Of : MANDATORY REFERENCE TO A;\n    END B;"),
        encoding="utf-8",
    )
    xtf = tmp_path / "i.xtf"
    xtf.write_text(
        '<?xml version="1.0"?><TRANSFER xmlns="http://www.interlis.ch/INTERLIS2.3">'
        '<HEADERSECTION SENDER="x" VERSION="2.3"><MODELS><MODEL NAME="M" VERSION="1" URI="http://x"></MODEL></MODELS></HEADERSECTION><DATASECTION>'
        '<M.T BID="b1" KIND="UPDATE"><M.T.B TID="b9"><Of REF="a1"></Of></M.T.B><M.T.A TID="a2" OPERATION="UPDATE">'
        '<Name>x</Name></M.T.A><M.T.A TID="a3" OPERATION="DELETE"></M.T.A></M.T><M.T BID="empty"></M.T>'
        "</DATASECTION></TRANSFER>",
        encoding="utf-8",
    )
    whole, streamed = tmp_path / "w.sql", tmp_path / "s.sql"
    base = ["import-xtf", str(xtf), "--model", str(tmp_path / "M.ili"), "--dataset", "ds"]
    main([*base, "-o", str(whole)])
    main([*base, "--stream", "-o", str(streamed)])
    assert streamed.read_text(encoding="utf-8") == whole.read_text(encoding="utf-8")
    assert "409 Conflict: INSERT" in whole.read_text(encoding="utf-8")
