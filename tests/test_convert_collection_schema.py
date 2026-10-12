"""`collection_schema` and `interlis convert --collection`: one class as the standalone OGC API Schemas document."""

import json

import pytest
from conftest import build_from_text

from interlis.cli import main
from interlis.cli_style import ExitCode
from interlis.convert.jsonschema import collection_schema, convertible_roots, model_to_json_schema

_MODEL = """INTERLIS 2.4;
MODEL Col AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS Owner =
      Name : MANDATORY TEXT*10;
    END Owner;
    STRUCTURE Share =
      Percent : 0 .. 100;
      Holder : REFERENCE TO Owner;
    END Share;
    CLASS Parcel =
      Holder : REFERENCE TO Owner;
      Main : Share;
    END Parcel;
  END T;
END Col.
"""


@pytest.fixture(scope="module")
def schema():
    table = build_from_text(_MODEL).symbol_table
    return model_to_json_schema(convertible_roots(table), symbol_table=table)


def test_the_standalone_schema_is_an_object_with_its_id(schema):
    document = collection_schema(schema, "Parcel", schema_id="http://x/collections/Parcel/schema")
    assert document["$id"] == "http://x/collections/Parcel/schema"
    assert document["type"] == "object" and "$defs" not in document


def test_a_structure_is_inlined(schema):
    main_property = collection_schema(schema, "Parcel")["properties"]["Main"]
    assert main_property["type"] == "object" and "$ref" not in main_property
    assert main_property["properties"]["Percent"]["maximum"] == 100


def test_a_reference_names_its_target_collection(schema):
    holder = collection_schema(schema, "Parcel")["properties"]["Holder"]
    assert (holder["x-ogc-role"], holder["x-ogc-collectionId"]) == ("reference", "Owner")


def test_without_an_id_there_is_none(schema):
    assert "$id" not in collection_schema(schema, "Owner")


def test_an_unknown_class_is_a_key_error(schema):
    with pytest.raises(KeyError):
        collection_schema(schema, "Nope")


def test_the_cli_writes_one_class(tmp_path, capsys):
    model = tmp_path / "col.ili"
    model.write_text(_MODEL, encoding="utf-8")
    assert main(["convert", str(model), "--collection", "Owner", "--schema-id", "http://x/s"]) == ExitCode.OK
    assert json.loads(capsys.readouterr().out)["$id"] == "http://x/s"


def test_the_cli_refuses_an_unknown_class_and_a_lone_schema_id(tmp_path, capsys):
    model = tmp_path / "col.ili"
    model.write_text(_MODEL, encoding="utf-8")
    assert main(["convert", str(model), "--collection", "Nope"]) == ExitCode.NOT_FOUND
    assert main(["convert", str(model), "--schema-id", "http://x/s"]) == ExitCode.INVALID
    assert "available: Owner, Share, Parcel" in capsys.readouterr().err
