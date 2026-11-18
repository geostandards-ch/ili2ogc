"""Combined view of the IlisMeta16 metamodel: UmlIndex plus the values of the anonymous enumerations.

Only EnumerationType is resolved into `Literal[...]`; the registry types the other anonymous primitives as `Any`.
"""

from pathlib import Path

import yaml

from interlis.spec.uml_index import UmlIndex

GENERIC_ENUM_TYPE_UUID = "279A049B-2BCC-4fb5-9C8F-3B22EF3EE0ED"


class MetamodelSchema:
    def __init__(self, uml: UmlIndex, kind_values: dict[str, dict]):
        self.uml = uml
        self.kind_values = kind_values  # {"Qualified.Attr": {"mandatory": bool, "values": [...]}}

    @classmethod
    def load(cls, mappings_dir: Path) -> "MetamodelSchema":
        uml = UmlIndex.load(mappings_dir)
        kv_path = mappings_dir / "ilismeta16-kind-values.yml"
        kv_data = yaml.safe_load(kv_path.read_text(encoding="utf-8")) or {}
        return cls(uml, kv_data.get("kind_values", {}))

    def instantiable_classes(self) -> dict[str, dict]:
        """Return classes and datatypes - what the registry must generate.

        Selects kind Class or DataType. Excludes Association and
        Enumeration (separate structures, not generated as instances).
        """
        return {qn: el for qn, el in self.uml.qualified.items() if el.get("kind") in ("Class", "DataType")}

    def associations(self) -> dict[str, dict]:
        return {qn: el for qn, el in self.uml.qualified.items() if el.get("kind") == "Association"}

    def enum_values_for(self, qualified_class: str, attribute: str) -> list[str] | None:
        return (self.kind_values.get(f"{qualified_class}.{attribute}") or {}).get("values")
