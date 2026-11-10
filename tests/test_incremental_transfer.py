"""Incremental transfers (INSERT/UPDATE/DELETE objects): replayed like REST calls by `import-xtf`."""

import json
from pathlib import Path

from interlis.cli import main
from interlis.xtf.parse import parse_xtf

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS A =
      Name : MANDATORY TEXT*20;
    END A;
  END T;
END M.
"""

_XTF = """<?xml version="1.0" encoding="UTF-8"?>
<TRANSFER xmlns="http://www.interlis.ch/INTERLIS2.3">
<HEADERSECTION SENDER="x" VERSION="2.3"><MODELS><MODEL NAME="M" VERSION="1" URI="http://x"></MODEL></MODELS></HEADERSECTION>
<DATASECTION>
<M.T BID="b1" KIND="UPDATE">
<M.T.A TID="new"><Name>n</Name></M.T.A>
<M.T.A TID="old" OPERATION="UPDATE"><Name>renamed</Name></M.T.A>
<M.T.A TID="gone" OPERATION="DELETE"></M.T.A>
</M.T>
</DATASECTION>
</TRANSFER>
"""


def _setup(tmp_path: Path) -> Path:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    xtf = tmp_path / "incr.xtf"
    xtf.write_text(_XTF, encoding="utf-8")
    return xtf


def test_parser_keeps_the_operation_per_object(tmp_path: Path):
    objects = parse_xtf(_setup(tmp_path)).baskets[0].objects
    assert [o.operation for o in objects] == [None, "UPDATE", "DELETE"]


def test_import_replays_post_put_delete(tmp_path: Path):
    xtf = _setup(tmp_path)
    out = tmp_path / "o.sql"
    main(["import-xtf", str(xtf), "--model", str(tmp_path / "M.ili"), "--dataset", "ds", "-o", str(out)])
    sql = out.read_text(encoding="utf-8")
    assert "INSERT INTO T_ILI2DB_DATASET" not in sql  # the dataset must already exist
    assert "404 Not Found: dataset ds" in sql
    assert "409 Conflict: INSERT a new" in sql
    assert 'INSERT INTO "a"' in sql and "'new'" in sql
    assert """UPDATE "a" SET "name" = 'renamed' WHERE "t_ili_tid" = 'old'""" in sql
    assert "404 Not Found: UPDATE a old" in sql
    assert 'DELETE FROM "a" WHERE "t_ili_tid" = \'gone\'' in sql
    assert "404 Not Found: DELETE a gone" in sql


def test_insert_only_transfer_still_creates_its_dataset(tmp_path: Path):
    xtf = _setup(tmp_path)
    xtf.write_text(
        _XTF.replace(' OPERATION="UPDATE"', "").replace(' OPERATION="DELETE"', "").replace(' KIND="UPDATE"', ""),
        encoding="utf-8",
    )
    out = tmp_path / "o.sql"
    main(["import-xtf", str(xtf), "--model", str(tmp_path / "M.ili"), "-o", str(out)])
    sql = out.read_text(encoding="utf-8")
    assert "INSERT INTO T_ILI2DB_DATASET" in sql
    assert "409 Conflict: dataset" in sql and "409 Conflict: INSERT" not in sql


def test_update_basket_with_only_inserts_is_incremental(tmp_path: Path):
    """A basket of KIND UPDATE is a POST on an existing dataset even when every object is an INSERT."""
    xtf = _setup(tmp_path)
    xtf.write_text(
        _XTF.replace(' OPERATION="UPDATE"', "").replace(' OPERATION="DELETE"', ""),
        encoding="utf-8",
    )
    out = tmp_path / "o.sql"
    main(["import-xtf", str(xtf), "--model", str(tmp_path / "M.ili"), "--dataset", "ds", "-o", str(out)])
    sql = out.read_text(encoding="utf-8")
    assert "INSERT INTO T_ILI2DB_DATASET" not in sql and "404 Not Found: dataset ds" in sql


def test_validate_skips_attribute_checks_of_a_delete(tmp_path: Path):
    """A DELETE carries only its TID: the MANDATORY `Name` must not be reported missing."""
    xtf = _setup(tmp_path)
    out = tmp_path / "r.txt"
    code = main(["validate-xtf", str(xtf), "--model", str(tmp_path / "M.ili"), "--report", str(out)])
    assert code == 0, out.read_text(encoding="utf-8") if out.exists() else ""


def test_convert_jsonfg_writes_part4_requests(tmp_path: Path):
    xtf = _setup(tmp_path)
    out = tmp_path / "o.json"
    main(["convert-jsonfg", str(xtf), "--model", str(tmp_path / "M.ili"), "-o", str(out)])
    requests = json.loads(out.read_text(encoding="utf-8"))["requests"]
    assert [(r["method"], r["href"]) for r in requests] == [
        ("POST", "/collections/A/items"),
        ("PUT", "/collections/A/items/old"),
        ("DELETE", "/collections/A/items/gone"),
    ]
    assert requests[0]["body"]["id"] == "new" and requests[0]["body"]["properties"] == {"Name": "n"}
    assert requests[1]["body"]["properties"] == {"Name": "renamed"} and "body" not in requests[2]
    assert requests[0]["headers"]["Content-Type"] == "application/vnd.ogc.fg+json"


def test_convert_jsonfg_stream_writes_the_same_requests(tmp_path: Path):
    xtf = _setup(tmp_path)
    whole, streamed = tmp_path / "w.json", tmp_path / "s.json"
    base = ["convert-jsonfg", str(xtf), "--model", str(tmp_path / "M.ili")]
    main([*base, "-o", str(whole)])
    main([*base, "--stream", "-o", str(streamed)])
    assert streamed.read_text(encoding="utf-8") == whole.read_text(encoding="utf-8")


def test_a_complete_transfer_is_still_a_feature_collection(tmp_path: Path):
    xtf = _setup(tmp_path)
    xtf.write_text(
        _XTF.replace(' OPERATION="UPDATE"', "").replace(' OPERATION="DELETE"', "").replace(' KIND="UPDATE"', ""),
        encoding="utf-8",
    )
    out = tmp_path / "o.json"
    main(["convert-jsonfg", str(xtf), "--model", str(tmp_path / "M.ili"), "-o", str(out)])
    assert json.loads(out.read_text(encoding="utf-8"))["type"] == "FeatureCollection"


_XTF24 = """<?xml version="1.0" encoding="UTF-8"?>
<ili:transfer xmlns:ili="http://www.interlis.ch/xtf/2.4/INTERLIS" xmlns="http://www.interlis.ch/xtf/2.4/M">
<ili:headersection sender="x" version="2.4"><ili:models><ili:model>M</ili:model></ili:models></ili:headersection>
<ili:datasection>
<T ili:bid="b1" ili:kind="UPDATE">
<A ili:tid="new"><Name>n</Name></A>
<ili:delete ili:tid="gone"/>
</T>
</ili:datasection>
</ili:transfer>
"""


def test_delete_object_names_a_tid_but_no_class(tmp_path: Path):
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    xtf = tmp_path / "d.xtf"
    xtf.write_text(_XTF24, encoding="utf-8")
    objects = parse_xtf(xtf).baskets[0].objects
    assert [(o.qualified_class, o.operation, o.tid) for o in objects] == [
        ("M.T.A", None, "new"),
        ("", "DELETE", "gone"),
    ]


def test_import_deletes_a_delete_object_from_whichever_table_holds_it(tmp_path: Path):
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    xtf = tmp_path / "d.xtf"
    xtf.write_text(_XTF24, encoding="utf-8")
    out = tmp_path / "o.sql"
    main(["import-xtf", str(xtf), "--model", str(tmp_path / "M.ili"), "--dataset", "ds", "-o", str(out)])
    sql = out.read_text(encoding="utf-8")
    assert """DELETE FROM "a" WHERE "t_ili_tid" = 'gone'""" in sql
    assert "404 Not Found: DeleteObject gone" in sql


def test_convert_jsonfg_leaves_out_a_delete_object_with_a_note(tmp_path: Path, capsys):
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    xtf = tmp_path / "d.xtf"
    xtf.write_text(_XTF24, encoding="utf-8")
    out = tmp_path / "o.json"
    main(["convert-jsonfg", str(xtf), "--model", str(tmp_path / "M.ili"), "-o", str(out)])
    requests = json.loads(out.read_text(encoding="utf-8"))["requests"]
    assert [r["method"] for r in requests] == ["POST"]
    assert "JSONFG-DELETEOBJECT-NO-COLLECTION" in capsys.readouterr().err


def test_convert_jsonfg_geojson_request_bodies(tmp_path: Path):
    (tmp_path / "M.ili").write_text(
        _MODEL.replace("Name : MANDATORY TEXT*20;", "Name : MANDATORY TEXT*20;\n      Pos : C2;").replace(
            "TOPIC T",
            "!!@CRS=EPSG:2056\n  DOMAIN C2 = COORD 2460000.000 .. 2870000.000, 1045000.000 .. 1310000.000;\n  TOPIC T",
        ),
        encoding="utf-8",
    )
    xtf = tmp_path / "g.xtf"
    xtf.write_text(
        _XTF.replace(
            "<Name>n</Name>", "<Name>n</Name><Pos><COORD><C1>2600000.000</C1><C2>1200000.000</C2></COORD></Pos>"
        ),
        encoding="utf-8",
    )
    out = tmp_path / "o.json"
    main(["convert-jsonfg", str(xtf), "--model", str(tmp_path / "M.ili"), "--request-body", "geojson", "-o", str(out)])
    post = json.loads(out.read_text(encoding="utf-8"))["requests"][0]
    assert post["headers"] == {
        "Content-Type": "application/geo+json",
        "Content-Crs": "<http://www.opengis.net/def/crs/EPSG/0/2056>",
    }
    assert post["body"] == {
        "type": "Feature",
        "id": "new",
        "geometry": {"type": "Point", "coordinates": [2600000.0, 1200000.0]},
        "properties": {"Name": "n"},
    }
