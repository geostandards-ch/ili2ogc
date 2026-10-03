"""JSON-FG keeps an embedded role whose association is declared in the base model of an extended topic."""

import json
from pathlib import Path

from interlis.cli import main

_BASE = """INTERLIS 2.3;
MODEL Base AT "http://x" VERSION "1" =
  TOPIC Plans =
    CLASS Plan =
      Code : TEXT*5;
    END Plan;
    CLASS Facility =
      Code : TEXT*5;
    END Facility;
    ASSOCIATION Plan_Facility =
      Plan -- {1} Plan;
      Facility -- {0..*} Facility;
    END Plan_Facility;
  END Plans;
END Base.
"""

_EXT = """INTERLIS 2.3;
MODEL Ext AT "http://x" VERSION "1" =
  IMPORTS Base;
  TOPIC Plans EXTENDS Base.Plans =
  END Plans;
END Ext.
"""

_TRANSFER = """<?xml version="1.0" encoding="UTF-8"?>
<TRANSFER xmlns="http://www.interlis.ch/INTERLIS2.3">
<HEADERSECTION SENDER="test" VERSION="2.3"><MODELS><MODEL NAME="Ext" VERSION="1" URI="http://x"/></MODELS></HEADERSECTION>
<DATASECTION>
<Ext.Plans BID="b1">
<Base.Plans.Plan TID="p1"><Code>a</Code></Base.Plans.Plan>
<Base.Plans.Facility TID="f1"><Code>b</Code><Plan REF="p1"/></Base.Plans.Facility>
</Ext.Plans>
</DATASECTION>
</TRANSFER>
"""


def test_role_from_the_base_model_is_a_feature_property(tmp_path: Path):
    (tmp_path / "Base.ili").write_text(_BASE, encoding="utf-8")
    (tmp_path / "Ext.ili").write_text(_EXT, encoding="utf-8")
    (tmp_path / "data.xtf").write_text(_TRANSFER, encoding="utf-8")
    out = tmp_path / "out.json"
    main(
        [
            "convert-jsonfg",
            str(tmp_path / "data.xtf"),
            "--model",
            str(tmp_path / "Ext.ili"),
            "--repo",
            str(tmp_path),
            "-o",
            str(out),
        ]
    )
    features = json.loads(out.read_text(encoding="utf-8"))["features"]
    facility = next(f for f in features if f["id"] == "f1")
    assert facility["properties"]["Plan"] == "p1"
