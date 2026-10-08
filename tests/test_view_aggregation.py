"""`AGGREGATION OF`: JSON-FG evaluation agrees with the SQL translation on the same data.

eCH-0031 V2.1.0 SS3.15 (Zusammenfassung): all base instances (`ALL`) or
those with an identical attribute combination (`EQUAL(...)`) collapse into
one; `INTERLIS.objectCount/elementCount(AGGREGATES)` count the collapsed
set. A user `FUNCTION` is declared without a body, so it is not evaluable
by any converter. Fixtures: `tests/fixtures/views/aggregation_*.ili`.
"""

from view_fixtures import FIXTURES, build, features, load_transfer, registered, run_ddl, sql_views

from interlis.convert.jsonfg import evaluate_view, evaluate_view_objects, unsupported_view_reason, view_skip_diagnostic
from interlis.xtf.parse import parse_xtf


def _sql_rows(builder, xtf_name, view_sql_name, columns):
    tables, views = sql_views(builder)
    con = run_ddl(tables, views)
    load_transfer(con, parse_xtf(FIXTURES / "xtf" / f"{xtf_name}.xtf"))
    quoted = ", ".join(f'"{c}"' for c in columns)
    return sorted(con.execute(f'SELECT {quoted} FROM "{view_sql_name}"'), key=repr)


def _json_rows(feats, names):
    return sorted((tuple(f["properties"].get(n) for n in names) for f in feats), key=repr)


def _as_sql_numbers(rows):
    return [tuple(int(v) if isinstance(v, (int, float)) else v for v in row) for row in rows]


def test_equal_key_with_count_is_one_feature_per_group_and_equals_sql():
    builder = build("aggregation_count")
    feats = features(builder, "aggregation_count")
    json_rows = _json_rows(feats, ["Municipality", "ParcelCount"])
    assert json_rows == sorted([(None, 2), ("Lausanne", 2), ("Renens", 1)], key=repr)
    assert json_rows == _as_sql_numbers(
        _sql_rows(builder, "aggregation_count", "municipalitystats", ["municipality", "parcelcount"])
    )
    assert all("id" not in f for f in feats)


def test_all_with_count_is_a_single_group_and_equals_sql():
    builder = build("aggregation_count_all")
    feats = features(builder, "aggregation_count_all")
    assert [f["properties"]["Total"] for f in feats] == [5]
    assert _as_sql_numbers(_sql_rows(builder, "aggregation_count_all", "parcelstats", ["total"])) == [(5,)]


def test_all_with_count_over_no_object_is_still_one_row_in_both_targets():
    builder = build("aggregation_count_all")
    view = registered(builder, "View")[0]
    empty = parse_xtf(FIXTURES / "xtf" / "aggregation_count_all.xtf")
    empty.baskets[0].objects.clear()
    (feat,) = evaluate_view(view, empty, symbol_table=builder.symbol_table)
    assert feat["properties"]["Total"] == 0
    tables, views = sql_views(builder)
    con = run_ddl(tables, views)
    assert list(con.execute('SELECT "total" FROM "parcelstats"')) == [(0,)]


def test_where_filters_before_grouping_and_plain_attributes_join_the_group_key():
    builder = build("aggregation_where")
    feats = features(builder, "aggregation_where")
    names = ["Municipality", "Zone", "ParcelCount"]
    expected = sorted([("Lausanne", "residential", 2), ("Renens", "mixed", 1), ("Renens", "residential", 1)], key=repr)
    assert _json_rows(feats, names) == expected
    columns = ["municipality", "zone", "parcelcount"]
    assert _json_rows(feats, names) == _as_sql_numbers(_sql_rows(builder, "aggregation_where", "zonestats", columns))


def test_group_on_a_reference_hop_key_equals_sql():
    builder = build("aggregation_key_reference")
    feats = features(builder, "aggregation_key_reference")
    assert _json_rows(feats, ["MunicipalityName"]) == [("Lausanne",), ("Renens",)]
    assert _json_rows(feats, ["MunicipalityName"]) == _sql_rows(
        builder, "aggregation_key_reference", "municipalitylist", ["municipalityname"]
    )


def test_evaluate_view_objects_carries_the_counts_for_the_xtf_writer():
    builder = build("aggregation_count")
    view = registered(builder, "View")[0]
    objects = evaluate_view_objects(
        view, parse_xtf(FIXTURES / "xtf" / "aggregation_count.xtf"), symbol_table=builder.symbol_table
    )
    counts = sorted(o.attributes["ParcelCount"][0].text for o in objects)
    assert counts == ["1", "2", "2"]


def test_user_function_column_is_explicitly_not_evaluable_and_says_why():
    builder = build("aggregation_of")
    view = registered(builder, "View")[0]
    reason = unsupported_view_reason(view)
    assert reason is not None and "'countB'" in reason and "without a body" in reason
    diag = view_skip_diagnostic(view)
    assert diag is not None and diag.rule == "JSONFG-VIEW-FORMATION-UNSUPPORTED"
