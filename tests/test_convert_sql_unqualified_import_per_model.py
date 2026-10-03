"""`IMPORTS UNQUALIFIED` is scoped to the MODEL that declares it, not to the whole file.

A multi-MODEL file's LV03 and LV95 variants each import their own geometry
model unqualified, and both declare the same short domain name: each
variant's geometry must resolve to its own import's CRS.
"""

import subprocess
import sys
from pathlib import Path

_GEOMETRY = """INTERLIS 2.4;
MODEL GeoLV03 AT "http://x" VERSION "1" =
  DOMAIN
    !!@CRS=EPSG:21781
    Coord2 = COORD 480000.000 .. 850000.000, 60000.000 .. 320000.000;
END GeoLV03.

MODEL GeoLV95 AT "http://x" VERSION "1" =
  DOMAIN
    !!@CRS=EPSG:2056
    Coord2 = COORD 2480000.000 .. 2850000.000, 1060000.000 .. 1320000.000;
END GeoLV95.
"""

_DATA = """INTERLIS 2.4;
MODEL DataLV03 AT "http://x" VERSION "1" =
  IMPORTS UNQUALIFIED GeoLV03;
  TOPIC T =
    CLASS PointLV03 =
      Pos : Coord2;
    END PointLV03;
  END T;
END DataLV03.

MODEL DataLV95 AT "http://x" VERSION "1" =
  IMPORTS UNQUALIFIED GeoLV95;
  TOPIC T =
    CLASS PointLV95 =
      Pos : Coord2;
    END PointLV95;
  END T;
END DataLV95.
"""


def test_each_model_resolves_its_own_unqualified_import(tmp_path: Path):
    (tmp_path / "Geo.ili").write_text(_GEOMETRY, encoding="utf-8")
    (tmp_path / "Data.ili").write_text(_DATA, encoding="utf-8")
    out = tmp_path / "out.sql"
    # Several hash seeds: a file-wide set of imports made the winner depend on set iteration order.
    for seed in ("1", "2", "3"):
        subprocess.run(
            [
                sys.executable,
                "-c",
                "import sys; from interlis.cli import main; sys.exit(main(sys.argv[1:]))",
                "convert-sql",
                str(tmp_path / "Data.ili"),
                "--repo",
                str(tmp_path),
                "-o",
                str(out),
            ],
            env={"PYTHONHASHSEED": seed, "PATH": ""},
            check=False,
        )
        sql = out.read_text(encoding="utf-8")
        lv03 = sql.split('CREATE TABLE "pointlv03"', 1)[1].split(");", 1)[0]
        lv95 = sql.split('CREATE TABLE "pointlv95"', 1)[1].split(");", 1)[0]
        assert "geometry(Point, 21781)" in lv03, seed
        assert "geometry(Point, 2056)" in lv95, seed
