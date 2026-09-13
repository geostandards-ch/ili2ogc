"""`ModelRepository` resolving a qualified name through a cross-model `TOPIC EXTENDS` chain.

Real corpus data (`RoadsExgm2ien.ili`'s `GRAPHIC ... BASED ON
RoadsExdm2ien.RoadsExtended.LandCover`) references a class only via the
DERIVED topic's own qualified name - `LandCover` is declared in the BASE
topic (`RoadsExdm2ben.Roads`, a DIFFERENT model file), never redeclared in
`RoadsExtended` (`TOPIC RoadsExtended EXTENDS RoadsExdm2ben.Roads`). Within
one file this already works (one shared symbol table, no per-topic
scoping); cross-model it returned an `UnresolvedNamedReference` before
`ModelRepository._resolve_via_topic_inheritance` walked `DataUnit.Super`
across the model boundary.
"""

from pathlib import Path

from conftest import build_from_file

from interlis.builder.errors import UnresolvedNamedReference
from interlis.builder.repository import ModelRepository
from interlis.metamodel.instance import MetaInstance

_FIXTURES = Path(__file__).parent / "fixtures" / "cartosym"
_MODEL = _FIXTURES / "roadsexgm2ien" / "RoadsExgm2ien.ili"
_REPO_DIR = _FIXTURES / "roadsexgm2ien_repo"


def _graphics(builder):
    return {
        i.Name: i
        for i in builder.symbol_table.all_registered()
        if isinstance(i, MetaInstance) and i._qualified_class.rsplit(".", 1)[-1] == "Graphic"
    }


def test_every_real_graphic_base_resolves_through_the_topic_extends_chain():
    repository = ModelRepository([_REPO_DIR])
    builder = build_from_file(_MODEL, repository=repository)
    graphics = _graphics(builder)
    assert set(graphics) == {
        "Surface_Graphics",
        "SurfaceBoundary_Graphics",
        "Polyline_Graphics",
        "Text_Graphics",
        "Point_Graphics",
    }
    for graphic in graphics.values():
        base = graphic.Base
        assert not isinstance(base, UnresolvedNamedReference), f"{graphic.Name}.Base: {base!r}"


def test_landcover_resolves_to_the_class_declared_in_the_base_topics_own_file():
    repository = ModelRepository([_REPO_DIR])
    builder = build_from_file(_MODEL, repository=repository)
    base = _graphics(builder)["Surface_Graphics"].Base
    assert base.Name == "LandCover"
    assert base._qualified_class.rsplit(".", 1)[-1] == "Class"
