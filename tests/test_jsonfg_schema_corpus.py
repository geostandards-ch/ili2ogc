"""Corpus invariant: a transfer's JSON-FG feature properties match the JSON Schema of its model.

Runs on `xtf_corpus/` + `ili_corpus/` (scratch directories, absent in a fresh checkout: the test is then skipped) for
transfers up to 3 MB. Value facets (ranges, lengths, item counts, integer-ness, empty values) are left to
`interlis validate`; this checks the SHAPE: objects, arrays, references as OID strings. Geometry
(`format: geometry-*`) is not a property of the document and is left to `validate-jsonfg`.
"""

import json
import re
import warnings
from pathlib import Path

import jsonschema
import pytest

from interlis.builder.model_builder import InterlisModelBuilder
from interlis.builder.repository import ModelRepository
from interlis.convert.jsonfg import transfer_to_feature_collection
from interlis.convert.jsonschema import convertible_roots, is_spatial_schema, model_to_json_schema
from interlis.runtime.parse import parse_file
from interlis.xtf.model_resolution import root_model_names
from interlis.xtf.parse import parse_xtf

ROOT = Path(__file__).resolve().parent.parent
XTF_FILES = sorted(p for p in (ROOT / "xtf_corpus").rglob("*.xtf") if p.stat().st_size <= 3_000_000)

pytestmark = pytest.mark.skipif(
    not XTF_FILES or not (ROOT / "ili_corpus").is_dir(), reason="xtf_corpus/ and ili_corpus/ are not present"
)

_VALUE_FACETS = {"minimum", "maximum", "maxLength", "minItems", "maxItems", "format", "pattern"}


def _shape_only(node):
    """The schema without its value facets; a scalar may be `null` (an empty element) and integers any number."""
    if isinstance(node, list):
        return [_shape_only(item) for item in node]
    if not isinstance(node, dict):
        return node
    shaped = {key: _shape_only(value) for key, value in node.items() if key not in _VALUE_FACETS}
    kind = shaped.get("type")
    if kind == "integer":
        kind = shaped["type"] = "number"
    if isinstance(kind, str) and kind != "object" and "prefixItems" not in shaped:
        shaped["type"] = [kind, "null"]
    return shaped


def _schema_and_features(xtf: Path):
    repository = ModelRepository([ROOT / "ili_corpus"])
    transfer = parse_xtf(xtf)
    model_path = next((repository.path_for(n) for n in root_model_names(transfer) if repository.path_for(n)), None)
    if model_path is None:
        pytest.skip("root model not in ili_corpus/")
    tree, _ = parse_file(model_path)
    builder = InterlisModelBuilder(ROOT / "mappings", ROOT / "spec/grammar/mapping", repository=repository)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        builder.build(tree)
    roots = convertible_roots(builder.symbol_table)
    schema = model_to_json_schema(roots, symbol_table=builder.symbol_table)
    collection = transfer_to_feature_collection(transfer, symbol_table=builder.symbol_table, repository=repository)
    return schema, collection


@pytest.mark.parametrize("xtf", XTF_FILES, ids=[p.name for p in XTF_FILES])
def test_feature_properties_match_the_model_schema(xtf):
    schema, collection = _schema_and_features(xtf)
    defs = _shape_only(schema["$defs"])
    problems = []
    for feature in collection["features"]:
        feature_type = feature.get("featureType") or collection.get("featureType")
        # Two models of one file may share a class name: the later one is keyed `Name_2`, `Name_3`...
        keys = [k for k in defs if k == feature_type or re.fullmatch(re.escape(str(feature_type)) + r"_?\d+", k)]
        if not keys:
            continue  # a class of an imported model is not part of this model's schema
        properties = feature["properties"]
        attempts = []
        for key in keys:
            definition = dict(defs[key])
            spatial = {n for n, prop in schema["$defs"][key].get("properties", {}).items() if is_spatial_schema(prop)}
            definition["required"] = [name for name in definition.get("required", []) if name not in spatial]
            root = {"$schema": schema["$schema"], "$defs": defs, **definition}
            attempts.append([e.message for e in jsonschema.Draft202012Validator(root).iter_errors(properties)])
        if all(attempts):
            problems.append((feature_type, feature.get("id"), min(attempts, key=len)[:2]))
    assert not problems, json.dumps(problems[:3], default=str)[:600]
