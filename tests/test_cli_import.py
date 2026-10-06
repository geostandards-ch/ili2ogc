"""`interlis import`: a transfer's objects as INSERTs into the schema `convert-sql` creates for the same model."""

import re
from pathlib import Path

from interlis.cli import main

_MODEL = """INTERLIS 2.4;
MODEL M AT "http://x" VERSION "1" =
  DOMAIN
    !!@CRS=EPSG:2056
    Coord2 = COORD 2460000.000 .. 2870000.000, 1045000.000 .. 1310000.000;
  STRUCTURE LocalisedText =
    Language : (de, fr);
    Text : MANDATORY TEXT*50;
  END LocalisedText;
  STRUCTURE MultilingualText =
    LocalisedText : BAG {1..*} OF LocalisedText;
  END MultilingualText;
  STRUCTURE Info =
    Since : TEXT*10;
  END Info;
  TOPIC T =
    CLASS Facility =
      Label : TEXT*20;
    END Facility;
    CLASS SpecialFacility EXTENDS Facility =
      Number : 1 .. 99;
    END SpecialFacility;
    CLASS Site =
      Pos : MANDATORY Coord2;
      Name : MultilingualText;
      Info : Info;
      Tags : BAG {0..*} OF Info;
      Fac : REFERENCE TO Facility;
    END Site;
  END T;
END M.
"""

_XTF = """<?xml version="1.0" encoding="UTF-8"?>
<ili:transfer xmlns:ili="http://www.interlis.ch/xtf/2.4/INTERLIS" xmlns="http://www.interlis.ch/xtf/2.4/M">
  <ili:headersection><ili:models><ili:model>M</ili:model></ili:models><ili:sender>t</ili:sender></ili:headersection>
  <ili:datasection>
    <T ili:bid="b1">
      <SpecialFacility ili:tid="f1"><Label>special</Label><Number>7</Number></SpecialFacility>
      <Site ili:tid="s1">
        <Pos><ili:coord><ili:c1>2600000.000</ili:c1><ili:c2>1200000.000</ili:c2></ili:coord></Pos>
        <Name><MultilingualText><LocalisedText>
          <LocalisedText><Language>de</Language><Text>Nord</Text></LocalisedText>
          <LocalisedText><Language>fr</Language><Text>Nord'est</Text></LocalisedText>
        </LocalisedText></MultilingualText></Name>
        <Info><Info><Since>2020</Since></Info></Info>
        <Tags><Info><Since>a</Since></Info><Info><Since>b</Since></Info></Tags>
        <Fac ili:ref="f1"/>
      </Site>
    </T>
  </ili:datasection>
</ili:transfer>
"""


def _import(tmp_path: Path, capsys) -> str:
    (tmp_path / "M.ili").write_text(_MODEL, encoding="utf-8")
    (tmp_path / "data.xtf").write_text(_XTF, encoding="utf-8")
    capsys.readouterr()
    assert main(["import", str(tmp_path / "data.xtf"), "--model", str(tmp_path / "M.ili")]) == 0
    return capsys.readouterr().out


def _insert(sql: str, table: str) -> list[str]:
    return re.findall(rf'INSERT INTO "{table}" \((.*?)\) VALUES \((.*?)\);', sql)


def test_dataset_basket_and_ids_come_from_one_sequence_block(tmp_path: Path, capsys):
    sql = _import(tmp_path, capsys)
    assert sql.startswith("BEGIN;") and sql.rstrip().endswith("COMMIT;")
    assert "nextval('t_ili2db_seq')" in sql and "setval('t_ili2db_seq'" in sql
    assert "INSERT INTO T_ILI2DB_DATASET" in sql and "'data'" in sql
    assert re.search(r"INSERT INTO T_ILI2DB_BASKET .*'M\.T', 'b1', 'data\.xtf'", sql)


def test_object_columns_follow_the_model(tmp_path: Path, capsys):
    sql = _import(tmp_path, capsys)
    ((columns, values),) = _insert(sql, "site")
    assert '"info_since"' in columns and "'2020'" in values
    assert "ST_GeomFromText('POINT (2600000.0 1200000.0)', 2056)" in values
    # The facility is a SpecialFacility: its subclass reference column is set, the base one is left out.
    assert '"fac_specialfacility"' in columns and '"fac"' not in columns.replace('"fac_specialfacility"', "")


def test_bag_elements_and_multilingual_texts_fill_child_tables(tmp_path: Path, capsys):
    sql = _import(tmp_path, capsys)
    texts = _insert(sql, "site_name_localisedtext")
    assert sorted(v.split(", ")[-1] for _, v in texts) == ["'Nord'", "'Nord''est'"]
    tags = _insert(sql, "site_tags")
    assert len(tags) == 2 and all('"site_fk"' in c for c, _ in tags)
