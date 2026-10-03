"""Identity across a multi-MODEL `.ili`: one built table per FILE, and FKs retargeted by it.

A single file can declare several models that reuse the same class names
(`BaseModel_SectoralPlans_V1_4.ili`'s LV03 and LV95 variants). Building it
once per requested model name produced two disjoint object graphs, and a
`REFERENCE TO` then reached whichever same-named class happened to take
the unsuffixed table name.
"""

from pathlib import Path

import pytest

from interlis.builder.repository import ModelRepository
from interlis.cli import _open_builder, main
from interlis.runtime.parse import meta_attribute_comments_in_file, parse_file

_TWO_MODEL_BASE = """INTERLIS 2.4;
MODEL BaseA AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS Thing =
      Code : MANDATORY TEXT*10;
    END Thing;
    CLASS Holder =
      thing : REFERENCE TO Thing;
    END Holder;
  END T;
END BaseA.

MODEL BaseB AT "http://x" VERSION "1" =
  TOPIC T =
    CLASS Thing =
      Code : MANDATORY TEXT*10;
    END Thing;
    CLASS Holder =
      thing : REFERENCE TO Thing;
    END Holder;
  END T;
END BaseB.
"""

_DERIVED = """INTERLIS 2.4;
MODEL Derived AT "http://x" VERSION "1" =
  IMPORTS BaseB;
  TOPIC DT EXTENDS BaseB.T =
    CLASS DerivedThing EXTENDS BaseB.T.Thing =
      Extra : TEXT*10;
    END DerivedThing;
  END DT;
END Derived.
"""


@pytest.fixture
def repo_dir(tmp_path: Path) -> Path:
    (tmp_path / "Base.ili").write_text(_TWO_MODEL_BASE, encoding="utf-8")
    (tmp_path / "Derived.ili").write_text(_DERIVED, encoding="utf-8")
    return tmp_path


def test_sibling_models_of_one_file_share_its_symbol_table(repo_dir: Path):
    repository = ModelRepository([repo_dir])
    builder = _open_builder(repository)
    derived = repo_dir / "Derived.ili"
    tree, _ = parse_file(derived)
    builder.build(tree, meta_attributes=meta_attribute_comments_in_file(derived))

    table_b = repository.symbol_table_for("BaseB")
    table_a = repository.symbol_table_for("BaseA")
    assert table_a is table_b

    derived_thing = builder.symbol_table.resolve("Derived.DT.DerivedThing")
    assert derived_thing.Super is table_a.resolve("BaseB.T.Thing")


def test_declared_model_names_lists_every_model_of_the_file(repo_dir: Path):
    repository = ModelRepository([repo_dir])
    assert repository.declared_model_names(repo_dir / "Base.ili") == ["BaseA", "BaseB"]


def test_catalog_promotion_keeps_each_models_reference_on_its_own_class(repo_dir: Path, capsys):
    assert (
        main(
            [
                "convert-sql",
                str(repo_dir / "Derived.ili"),
                "--repo",
                str(repo_dir),
                "--catalog",
                str(repo_dir / "Base.ili"),
                "--dialect",
                "postgresql",
            ]
        )
        == 0
    )
    ddl = capsys.readouterr().out
    # Same-named classes are told apart by what differs in their model names.
    assert 'CREATE TABLE "thing_basea" (' in ddl
    assert 'CREATE TABLE "thing_baseb" (' in ddl
    assert (
        'ALTER TABLE "holder_basea" ADD CONSTRAINT fk_holder_thing FOREIGN KEY ("thing") '
        'REFERENCES "thing_basea" ("id") DEFERRABLE INITIALLY DEFERRED;' in ddl
    )
    assert (
        'ALTER TABLE "holder_baseb" ADD CONSTRAINT fk_holder_thing FOREIGN KEY ("thing") '
        'REFERENCES "thing_baseb" ("id") DEFERRABLE INITIALLY DEFERRED;' in ddl
    )
