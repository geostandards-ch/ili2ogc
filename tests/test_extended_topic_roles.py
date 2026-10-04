"""Roles a base model's association embeds stay members of the subclasses an extending topic declares."""

import json
from pathlib import Path

from interlis.cli import main

_BASE = """INTERLIS 2.3;
MODEL Base AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS Plan =
      Code : TEXT*5;
    END Plan;
    CLASS Item =
      Label : TEXT*10;
    END Item;
    ASSOCIATION PlanItem =
      Plan -<#> {1} Plan;
      Item -- {0..*} Item;
    END PlanItem;
  END T;
END Base.
"""

_EXT = """INTERLIS 2.3;
MODEL Ext AT "http://x" VERSION "1" =
  IMPORTS Base;
  TOPIC T EXTENDS Base.T =
    CLASS ItemX EXTENDS Base.T.Item =
      Number : 1 .. 99;
    END ItemX;
  END T;
END Ext.
"""

_XTF = """<?xml version="1.0" encoding="UTF-8"?>
<TRANSFER xmlns="http://www.interlis.ch/INTERLIS2.3">
<HEADERSECTION SENDER="test" VERSION="2.3"><MODELS><MODEL NAME="Base" VERSION="1" URI="http://x"/>
<MODEL NAME="Ext" VERSION="1" URI="http://x"/></MODELS></HEADERSECTION>
<DATASECTION><Ext.T BID="b1">
<Base.T.Plan TID="p1"><Code>A</Code></Base.T.Plan>
<Ext.T.ItemX TID="i1"><Label>x</Label><Plan REF="p1"></Plan><Number>3</Number></Ext.T.ItemX>
</Ext.T></DATASECTION></TRANSFER>
"""


def _files(tmp_path: Path) -> tuple[str, str]:
    (tmp_path / "Base.ili").write_text(_BASE, encoding="utf-8")
    (tmp_path / "Ext.ili").write_text(_EXT, encoding="utf-8")
    (tmp_path / "data.xtf").write_text(_XTF, encoding="utf-8")
    return str(tmp_path / "data.xtf"), str(tmp_path / "Ext.ili")


def test_validate_knows_the_inherited_role(tmp_path: Path, capsys):
    xtf, model = _files(tmp_path)
    main(["validate", xtf, "--model", model, "--repo", str(tmp_path), "--verbose"])
    assert "absent from schema" not in capsys.readouterr().out


def test_jsonfg_keeps_the_inherited_role(tmp_path: Path):
    xtf, model = _files(tmp_path)
    out = tmp_path / "out.json"
    main(["convert-jsonfg", xtf, "--model", model, "--repo", str(tmp_path), "-o", str(out)])
    item = next(f for f in json.loads(out.read_text())["features"] if f["featureType"] == "ItemX")
    assert item["properties"]["Plan"] == "p1"
