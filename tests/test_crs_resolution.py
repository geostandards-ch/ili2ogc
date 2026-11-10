"""One CRS resolution for every converter: `!!@CRS` on the domain, inherited through EXTENDS, or bound by a CONTEXT."""

from pathlib import Path

import pytest
from conftest import build_from_text

from interlis.builder.repository import ModelRepository
from interlis.cli import main
from interlis.runtime.parse import meta_attribute_comments
from interlis.xtf.crs import resolve_crs
from interlis.xtf.schema import attributes_of, line_coord_type, resolve_attribute

BASE = Path(__file__).parent / "fixtures" / "solid3d"


def _resolution(src: str, attr: str = "G", *, vertex_of_line: bool = False):
    builder = build_from_text(src, repository=ModelRepository([BASE]), meta_attributes=meta_attribute_comments(src))
    cls = next(
        c for c in builder.symbol_table.all_registered() if c._qualified_class.endswith(".Class") and c.Name == "A"
    )
    resolved = resolve_attribute(attributes_of(cls)[attr])
    coord_type = line_coord_type(resolved.type_instance) if vertex_of_line else resolved.type_instance
    return resolve_crs(coord_type, symbol_table=builder.symbol_table, repository=builder.repository)


def _model(imports: str, attr_type: str, domain: str = "") -> str:
    return (
        f'INTERLIS 2.4;\nMODEL M AT "http://x" VERSION "1" =\n  IMPORTS {imports};\n{domain}'
        f"  TOPIC T = CLASS A = G : {attr_type}; END A; END T;\nEND M.\n"
    )


@pytest.mark.parametrize(
    ("model", "epsg"),
    [("GeometryCHLV95_V2", 2056), ("GeometryCHLV03_V2", 21781)],
)
@pytest.mark.parametrize("domain", ["Coord2", "Coord3", "MultiPoint", "MultiPoint3D"])
def test_concrete_domain_carries_its_own_epsg_and_the_two_never_mix(model, epsg, domain):
    found = _resolution(_model(model, f"{model}.{domain}"))
    assert (found.epsg, found.provenance) == (epsg, "annotation")


@pytest.mark.parametrize(
    ("model", "epsg"),
    [("GeometryCHLV95_V2", 2056), ("GeometryCHLV03_V2", 21781)],
)
@pytest.mark.parametrize(
    "line", ["POLYLINE WITH (STRAIGHTS) VERTEX {m}.Coord2", "SURFACE WITH (STRAIGHTS) VERTEX {m}.Coord3"]
)
def test_lines_and_surfaces_take_their_vertex_domain_crs(model, epsg, line):
    found = _resolution(_model(model, line.format(m=model)), vertex_of_line=True)
    assert found.epsg == epsg


def test_domain_extending_a_concrete_one_inherits_its_crs():
    domain = "  DOMAIN\n    Mine EXTENDS GeometryCHLV95_V2.Coord2 = COORD 0.000 .. 10.000, 0.000 .. 10.000;\n"
    found = _resolution(_model("GeometryCHLV95_V2", "Mine", domain))
    assert (found.epsg, found.provenance) == (2056, "extends")


@pytest.mark.parametrize(
    ("context", "epsg"),
    [("ContextCHLV95_V2", 2056), ("ContextCHLV03_V2", 21781)],
)
def test_context_binds_a_generic_domain_to_the_concrete_crs(context, epsg):
    found = _resolution(_model(f"Geometry_V2, {context}", "Geometry_V2.Coord3"))
    assert (found.epsg, found.provenance) == (epsg, "context")


def test_or_ed_context_is_ambiguous_and_never_resolved():
    found = _resolution(_model("Geometry_V2, ContextCH_V2", "Geometry_V2.Coord2"))
    assert (found.epsg, found.reason) == (None, "ambiguous-context")


def test_generic_domain_without_context_is_unbound():
    found = _resolution(_model("Geometry_V2", "Geometry_V2.Coord2"))
    assert (found.epsg, found.reason) == (None, "generic-unbound")


def test_domain_without_annotation_is_undeclared_and_not_inferred_from_its_bounds():
    domain = "  DOMAIN\n    Plain = COORD 2460000.000 .. 2870000.000, 1045000.000 .. 1310000.000;\n"
    found = _resolution(_model("GeometryCHLV95_V2", "Plain", domain))
    assert (found.epsg, found.reason) == (None, "undeclared")


def test_malformed_annotation_is_reported_not_resolved():
    domain = "  DOMAIN\n    !!@CRS=LV95\n    Bad = COORD 0.000 .. 10.000, 0.000 .. 10.000;\n"
    found = _resolution(_model("GeometryCHLV95_V2", "Bad", domain))
    assert (found.epsg, found.reason) == (None, "invalid-annotation")


def test_two_annotations_on_one_domain_are_reported_not_resolved():
    domain = (
        "  DOMAIN\n    !!@CRS=EPSG:2056\n    !!@CRS=EPSG:21781\n    Two = COORD 0.000 .. 10.000, 0.000 .. 10.000;\n"
    )
    found = _resolution(_model("GeometryCHLV95_V2", "Two", domain))
    assert (found.epsg, found.reason) == (None, "duplicate-annotation")


def test_annotation_separated_from_its_domain_resolves_with_a_warning():
    domain = "  DOMAIN\n    !!@CRS=EPSG:2056\n    /** doc */\n    Far = COORD 0.000 .. 10.000, 0.000 .. 10.000;\n"
    found = _resolution(_model("GeometryCHLV95_V2", "Far", domain))
    assert found.epsg == 2056
    assert [rule for rule, _ in found.warnings] == ["CRS-ANNOTATION-DISTANT"]


def test_sql_and_jsonfg_agree_on_a_context_bound_model(tmp_path: Path):
    """The model of the reported divergence: JSON-FG resolved the CONTEXT's CRS, SQL dropped the column."""
    sql = tmp_path / "o.sql"
    main(["convert-sql", str(BASE / "Building3D_V1.ili"), "--repo", str(BASE), "-o", str(sql)])
    text = sql.read_text(encoding="utf-8")
    assert "SQL-GEOM-NO-CRS" not in text and "geometry(PolygonZ, 2056)" in text


def test_sql_tells_a_missing_base_model_from_an_undeclared_crs(tmp_path: Path, capsys):
    (tmp_path / "M.ili").write_text(
        _model("GeometryCHLV95_V2", "Plain", "  DOMAIN\n    Plain = COORD 0.000 .. 10.000, 0.000 .. 10.000;\n"),
        encoding="utf-8",
    )
    main(["convert-sql", str(tmp_path / "M.ili"), "--repo", str(BASE), "-o", str(tmp_path / "o.sql")])
    assert "SQL-GEOM-CRS-UNDECLARED" in capsys.readouterr().err
    (tmp_path / "N.ili").write_text(_model("Geometry_V2", "Geometry_V2.Coord2"), encoding="utf-8")
    main(["convert-sql", str(tmp_path / "N.ili"), "--repo", str(BASE), "-o", str(tmp_path / "o2.sql")])
    err = capsys.readouterr().err
    assert "SQL-GEOM-NO-CRS" in err and "a CONTEXT" in err


_UNDECLARED = """INTERLIS 2.4;
MODEL U AT "http://x" VERSION "1" =
  DOMAIN C2 = COORD 2460000.000 .. 2870000.000, 1045000.000 .. 1310000.000;
  TOPIC T =
    CLASS P =
      Pos : C2;
    END P;
  END T;
END U.
"""


def test_crs_option_fills_only_an_undeclared_domain(tmp_path, capsys):
    from interlis.cli import main

    (tmp_path / "U.ili").write_text(_UNDECLARED, encoding="utf-8")
    plain, given = tmp_path / "plain.sql", tmp_path / "given.sql"
    main(["convert-sql", str(tmp_path / "U.ili"), "-o", str(plain)])
    main(["convert-sql", str(tmp_path / "U.ili"), "--crs", "EPSG:2056", "-o", str(given)])
    assert "geometry(" not in plain.read_text(encoding="utf-8")
    assert "geometry(Point, 2056)" in given.read_text(encoding="utf-8")
    assert "CRS-DEFAULT-APPLIED" in capsys.readouterr().err


def test_crs_option_rejects_a_non_epsg_value(tmp_path):
    import pytest

    from interlis.cli import main

    with pytest.raises(SystemExit):
        main(["convert-sql", str(tmp_path / "U.ili"), "--crs", "LV95"])
