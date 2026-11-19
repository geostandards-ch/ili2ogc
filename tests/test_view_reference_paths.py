"""VIEW attributes whose path has three or more elements agree between JSON-FG and the SQL translation.

A path may follow an embedded association role, a REFERENCE TO attribute, a STRUCTURE member, or a STRUCTURE that
wraps a REFERENCE TO. A target missing from the transfer is a NULL (the SQL LEFT JOIN) until a catalogue supplies it.
Fixtures: `tests/fixtures/views/projection_of_paths.ili`, `union_of_reference.ili`.
"""

import json
import sqlite3

from view_fixtures import FIXTURES, build, features, json_schema, run_ddl, sql_views, view_named

from interlis.cli import main
from interlis.cli_style import ExitCode
from interlis.convert.jsonfg import collect_diagnostics, evaluate_view, transfer_to_feature_collection
from interlis.xtf.parse import parse_xtf
from interlis.xtf.validate import extract_reference

PATHS = "projection_of_paths"
FLAT = ["FeatureName", "OwnerName", "ValidFrom", "ValidUntil", "KindLabel", "CategoryCode"]


def _flatten(prefix: str, node, ids: dict[str, int], row: dict) -> None:
    """One XTF attribute node as SQL columns: a STRUCTURE member is `<attribute>_<member>`, a reference its row id."""
    if node.children:
        for wrapper in node.children:
            for member in wrapper.children:
                _flatten(f"{prefix}_{member.tag.lower()}", member, ids, row)
        return
    ref = extract_reference(node)
    row[prefix] = node.text if ref is None else ids.get(ref, -1)  # -1: a reference to nothing


def _load(con: sqlite3.Connection, transfer) -> None:
    objects = [obj for basket in transfer.baskets for obj in basket.objects]
    ids = {obj.tid: i for i, obj in enumerate(objects, 1)}
    for obj in objects:
        row: dict = {"t_id": ids[obj.tid], "t_basket": 1, "t_ili_tid": obj.tid}
        for name, nodes in obj.attributes.items():
            _flatten(name.lower(), nodes[0], ids, row)
        table = obj.qualified_class.rsplit(".", 1)[-1].lower()
        columns = ", ".join(f'"{c}"' for c in row)
        con.execute(f'INSERT INTO "{table}" ({columns}) VALUES ({", ".join("?" for _ in row)})', list(row.values()))


def _sql_rows(builder, xtf_name, view_sql_name, columns):
    tables, views = sql_views(builder)
    con = run_ddl(tables, views)
    _load(con, parse_xtf(FIXTURES / "xtf" / f"{xtf_name}.xtf"))
    quoted = ", ".join(f'"{c.lower()}"' for c in columns)
    return sorted(con.execute(f'SELECT {quoted} FROM "{view_sql_name}"'), key=repr)


def _json_rows(feats, names):
    return sorted((tuple(f["properties"].get(n) for n in names) for f in feats), key=repr)


def test_projection_follows_role_reference_structure_and_structure_reference_like_sql():
    builder = build(PATHS)
    feats = features(builder, PATHS, "Flat")
    rows = _json_rows(feats, FLAT)
    assert rows == sorted(
        [
            ("one", "Lausanne", "2020-01-01", "2030-01-01", "Beta", "A"),
            ("two", "Lausanne", "2021-05-05", None, "Alpha", "B"),
            ("three", "Lausanne", None, None, None, None),
        ],
        key=repr,
    )
    assert rows == _sql_rows(builder, PATHS, "flat", FLAT)


def test_join_path_starts_from_the_object_its_alias_names_like_sql():
    builder = build(PATHS)
    columns = ["FeatureName", "CategoryCode", "OwnerName"]
    rows = _json_rows(features(builder, PATHS, "Pair"), columns)
    assert rows == [("one", "A", "Lausanne"), ("three", None, "Lausanne"), ("two", "B", "Lausanne")]
    assert rows == _sql_rows(builder, PATHS, "pair", columns)


def test_union_branch_follows_a_reference_like_sql():
    builder = build("union_of_reference")
    rows = _json_rows(features(builder, "union_of_reference"), ["Attr1"])
    assert rows == sorted([(None,), ("delta",), ("gamma",)], key=repr)
    tables, views = sql_views(builder)
    con = run_ddl(tables, views)
    con.execute('INSERT INTO "d" ("t_id", "t_basket", "name") VALUES (1, 1, ?)', ("delta",))
    con.execute('INSERT INTO "c1" ("t_id", "t_basket", "refd") VALUES (1, 1, 1)')
    con.execute('INSERT INTO "c1" ("t_id", "t_basket", "refd") VALUES (2, 1, 99)')
    con.execute('INSERT INTO "c2" ("t_id", "t_basket", "attr2") VALUES (1, 1, ?)', ("gamma",))
    assert sorted(con.execute('SELECT "attr1" FROM "cc"'), key=repr) == rows


def test_path_attribute_types_resolve_for_json_schema():
    schema = json_schema(build(PATHS))
    for view in ("Flat", "Pair"):
        properties = schema["$defs"][view]["properties"]
        assert properties, view
        assert all("x-unsupported" not in p for p in properties.values()), properties
    assert schema["$defs"]["Flat"]["properties"]["CategoryCode"] == {"type": "string", "maxLength": 10}
    assert schema["$defs"]["Flat"]["properties"]["ValidFrom"] == {"type": "string", "maxLength": 20}
    assert schema["$defs"]["Flat"]["properties"]["KindLabel"] == {"type": "string", "maxLength": 40}


def _collection(builder, catalogs=()):
    unmapped: list = []
    transfer = parse_xtf(FIXTURES / "xtf" / f"{PATHS}.xtf")
    collection = transfer_to_feature_collection(
        transfer,
        symbol_table=builder.symbol_table,
        views=[view_named(builder, "Flat")],
        unmapped=unmapped,
        catalogs=catalogs,
    )
    return collection, collect_diagnostics(unmapped)


def test_unresolved_reference_target_is_reported_once_per_attribute_and_only_that():
    _collection_, diagnostics = _collection(build(PATHS))
    assert sorted((d.rule, d.severity) for d in diagnostics) == [("JSONFG-VIEW-PATH-UNRESOLVED", "note")] * 2
    by_attribute = {d.location.element_path: d.message for d in diagnostics}
    assert set(by_attribute) == {"Flat.KindLabel", "Flat.CategoryCode"}
    assert "'missing-type' is not in the transfer (1 object(s))" in by_attribute["Flat.KindLabel"]
    assert "--catalog" in by_attribute["Flat.CategoryCode"]


def test_catalogue_transfer_resolves_the_missing_targets():
    catalog = parse_xtf(FIXTURES / "xtf" / f"{PATHS}_catalog.xtf")
    collection, diagnostics = _collection(build(PATHS), catalogs=[catalog])
    assert diagnostics == []
    three = next(f for f in collection["features"] if f["properties"].get("FeatureName") == "three")
    assert (three["properties"]["KindLabel"], three["properties"]["CategoryCode"]) == ("Gamma", "D")


def test_convert_jsonfg_catalog_option_feeds_reference_resolution(tmp_path, capsys):
    out = tmp_path / "out.json"
    base = ["convert-jsonfg", str(FIXTURES / "xtf" / f"{PATHS}.xtf"), "--model", str(FIXTURES / f"{PATHS}.ili")]
    assert main([*base, "-o", str(out)]) == 2
    assert "JSONFG-VIEW-PATH-UNRESOLVED" in capsys.readouterr().err
    catalog = str(FIXTURES / "xtf" / f"{PATHS}_catalog.xtf")
    assert main([*base, "--catalog", catalog, "-o", str(out)]) == 0
    flat = [f for f in json.loads(out.read_text())["features"] if f["featureType"] == "Flat"]
    assert {f["properties"]["CategoryCode"] for f in flat} == {"A", "B", "D"}
    assert main([*base, "--catalog", str(tmp_path / "nope.xtf")]) == ExitCode.NOT_FOUND
    assert "--catalog file not found" in capsys.readouterr().err


def test_evaluate_view_without_catalogs_leaves_the_value_out_instead_of_failing():
    builder = build(PATHS)
    transfer = parse_xtf(FIXTURES / "xtf" / f"{PATHS}.xtf")
    three = next(
        f
        for f in evaluate_view(view_named(builder, "Flat"), transfer, symbol_table=builder.symbol_table)
        if f["properties"]["FeatureName"] == "three"
    )
    assert "KindLabel" not in three["properties"] and "CategoryCode" not in three["properties"]


def test_write_xtf_catalog_option_resolves_the_missing_targets(tmp_path, capsys):
    out = tmp_path / "out.xtf"
    model = tmp_path / "model.ili"
    model.write_text((FIXTURES / f"{PATHS}.ili").read_text().replace("  TOPIC Views =", "  VIEW TOPIC Views ="))
    base = [
        "write-xtf",
        str(model),
        str(FIXTURES / "xtf" / f"{PATHS}.xtf"),
        "--view",
        "Flat",
        "-o",
        str(out),
    ]
    assert main(base) == 0
    assert "JSONFG-VIEW-PATH-UNRESOLVED" in capsys.readouterr().err
    assert out.read_text().count("<CategoryCode>") < 3
    catalog = str(FIXTURES / "xtf" / f"{PATHS}_catalog.xtf")
    assert main([*base, "--catalog", catalog]) == 0
    assert "JSONFG-VIEW-PATH-UNRESOLVED" not in capsys.readouterr().err
    assert out.read_text().count("<CategoryCode>") == 3


def test_convert_cql2_says_a_view_has_no_constraints_of_its_own(capsys):
    assert main(["convert-cql2", str(FIXTURES / f"{PATHS}.ili")]) == 0
    assert "Flat: a VIEW has no constraints of its own" in capsys.readouterr().err


def test_output_option_creates_the_parent_folder_and_version_is_printed(tmp_path, capsys):
    out = tmp_path / "new" / "folder" / "schema.sql"
    assert main(["convert-sql", str(FIXTURES / f"{PATHS}.ili"), "-o", str(out)]) in (0, 2)
    assert out.is_file()
    try:
        main(["--version"])
    except SystemExit as stop:
        assert stop.code == 0
    assert capsys.readouterr().out.startswith("ili2ogc ")
