"""ili2db-style technical columns, datasets/baskets and T_ILI2DB_* metadata, exercised on a live SQLite engine."""

import sqlite3
from pathlib import Path

import pytest

from interlis.cli import main

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS Owner =
      Name : MANDATORY TEXT*20;
    END Owner;
    CLASS Parcel =
      Nr : MANDATORY 1 .. 9999;
      owner : MANDATORY REFERENCE TO Owner;
    END Parcel;
  END T;
END M.
"""


def _ddl(tmp_path: Path, dialect: str) -> str:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    out = tmp_path / f"out.{dialect}.sql"
    main(["convert-sql", str(tmp_path / "M.ili"), "--repo", str(tmp_path), "--dialect", dialect, "-o", str(out)])
    return out.read_text(encoding="utf-8")


def test_tables_carry_the_technical_columns_and_bigint_references(tmp_path: Path):
    ddl = _ddl(tmp_path, "postgresql")
    parcel = ddl.split('CREATE TABLE "parcel" (', 1)[1].split("\n);", 1)[0]
    assert "\"t_id\" bigint PRIMARY KEY DEFAULT nextval('t_ili2db_seq')" in parcel
    assert '"t_basket" bigint NOT NULL' in parcel
    assert '"t_ili_tid" varchar(200)' in parcel
    assert '"owner" bigint NOT NULL' in parcel
    assert 'REFERENCES "owner" ("t_id")' in ddl
    assert 'FOREIGN KEY ("t_basket") REFERENCES T_ILI2DB_BASKET DEFERRABLE INITIALLY DEFERRED;' in ddl


def test_mapping_metadata_names_classes_and_attributes(tmp_path: Path):
    ddl = _ddl(tmp_path, "postgresql")
    assert "INSERT INTO T_ILI2DB_CLASSNAME (IliName, SqlName) VALUES ('M.T.Parcel', 'parcel');" in ddl
    assert (
        "INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('M.T.Parcel.owner', 'owner', "
        "'parcel', 'owner');" in ddl
    )
    assert (
        "INSERT INTO T_ILI2DB_MODEL (filename, iliversion, modelName, content, importDate) VALUES ('M.ili', '2.4', 'M'"
        in ddl
    )


def _gpkg_connection(tmp_path: Path) -> sqlite3.Connection:
    ddl = _ddl(tmp_path, "gpkg")
    con = sqlite3.connect(":memory:")
    con.execute("PRAGMA foreign_keys=ON")
    con.executescript(
        ddl.split("INSERT INTO gpkg_contents")[0] if "gpkg_contents" in ddl else ddl.split("-- spatial")[0]
    )
    return con


def test_two_datasets_share_the_schema_through_baskets(tmp_path: Path):
    con = _gpkg_connection(tmp_path)
    for dataset in (1, 2):
        con.execute("INSERT INTO T_ILI2DB_DATASET (T_Id, datasetName) VALUES (?, ?)", (dataset, f"d{dataset}"))
        con.execute(
            "INSERT INTO T_ILI2DB_BASKET (T_Id, dataset, topic, attachmentKey) VALUES (?, ?, 'M.T', 'x')",
            (dataset, dataset),
        )
        # The same TID in both datasets: rows are told apart by t_id/t_basket, never by the transferred TID.
        con.execute("INSERT INTO owner (t_basket, t_ili_tid, name) VALUES (?, 'o1', 'A')", (dataset,))
        owner = con.execute("SELECT t_id FROM owner WHERE t_basket = ? AND t_ili_tid = 'o1'", (dataset,)).fetchone()[0]
        con.execute("INSERT INTO parcel (t_basket, t_ili_tid, nr, owner) VALUES (?, 'p1', 1, ?)", (dataset, owner))
    con.commit()
    assert con.execute("SELECT count(*) FROM parcel").fetchone()[0] == 2


def test_a_row_of_an_unknown_basket_is_rejected_at_commit(tmp_path: Path):
    con = _gpkg_connection(tmp_path)
    con.execute("INSERT INTO owner (t_basket, t_ili_tid, name) VALUES (99, 'o1', 'A')")
    with pytest.raises(sqlite3.IntegrityError):
        con.commit()


_CHILD_MODEL = """INTERLIS 2.4;
MODEL C AT "http://x" VERSION "1" =
  STRUCTURE Label =
    Language : (de, fr, en);
    Text : MANDATORY TEXT*20;
    MANDATORY CONSTRAINT Language == #de OR Language == #fr;
  END Label;
  TOPIC T =
    CLASS Item =
      Labels : BAG {1..*} OF Label;
    END Item;
  END T;
END C.
"""


def _child_ddl(tmp_path: Path) -> str:
    (tmp_path / "C.ili").write_text(_CHILD_MODEL, encoding="utf-8")
    out = tmp_path / "c.sql"
    main(["convert-sql", str(tmp_path / "C.ili"), "--repo", str(tmp_path), "-o", str(out)])
    return out.read_text(encoding="utf-8")


def test_element_structure_constraint_binds_each_child_row(tmp_path: Path):
    child = _child_ddl(tmp_path).split('CREATE TABLE "item_labels" (', 1)[1].split("\n);", 1)[0]
    assert """CHECK ((("language" = 'de') OR ("language" = 'fr')))""" in child


def test_child_table_link_is_named_after_its_attribute(tmp_path: Path):
    ddl = _child_ddl(tmp_path)
    assert (
        "INSERT INTO T_ILI2DB_ATTRNAME (IliName, SqlName, ColOwner, Target) VALUES ('C.T.Item.Labels', 'item_fk', "
        "'item_labels', 'item');" in ddl
    )
    assert "VALUES ('C.Label.Text', 'text', 'item_labels', NULL);" in ddl
