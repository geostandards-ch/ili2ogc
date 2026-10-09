"""A model whose topics only extend a base topic still gets a JSON Schema entry for every inherited class."""

from conftest import build_from_file

from interlis.builder.repository import ModelRepository
from interlis.convert.jsonschema import model_to_json_schema, topic_inherited_classes

_BASE = """INTERLIS 2.4;
MODEL Base AT "https://example.org/" VERSION "1" =
  TOPIC Core =
    CLASS Item =
      Name: TEXT*20;
    END Item;
  END Core;
END Base.
"""

_EXTENSION = """INTERLIS 2.4;
MODEL Ext AT "https://example.org/" VERSION "1" =
  IMPORTS Base;
  TOPIC Wider EXTENDS Base.Core =
  END Wider;
END Ext.
"""


def test_extension_only_model_exposes_inherited_classes(tmp_path):
    (tmp_path / "Base.ili").write_text(_BASE, encoding="utf-8")
    path = tmp_path / "Ext.ili"
    path.write_text(_EXTENSION, encoding="utf-8")
    builder = build_from_file(path, repository=ModelRepository([tmp_path]))
    inherited = topic_inherited_classes(builder.symbol_table)
    assert [c.Name for c in inherited] == ["Item"]
    schema = model_to_json_schema(inherited, symbol_table=builder.symbol_table)
    assert set(schema["$defs"]) == {"Item"}
