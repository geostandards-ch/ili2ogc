"""IlisMeta16 attributes the metamodel defines by derivation, filled once a model is completely built."""

from interlis.metamodel.instance import MetaInstance


class _DerivedAttributesMixin:
    """Role.Mandatory, Role.EmbeddedTransfer and Class.EmbeddedRoleTransfer, set from the built associations."""

    symbol_table: "object"

    def _apply_derived_role_flags(self) -> None:
        """Fill the derived flags of every association registered in this model's symbol table.

        A role is always `Mandatory` (the reference exists whatever its cardinality, which `Multiplicity` carries);
        `EmbeddedTransfer` marks the role transferred as a pseudo-attribute of the opposite class and
        `EmbeddedRoleTransfer` the association carrying it (eCH-0031 V2.1.0 §4.3.9, the rule
        `xtf.schema.association_embedding` applies).
        """
        from interlis.xtf.schema import association_embedding

        for instance in self.symbol_table.all_registered():  # type: ignore[attr-defined]
            if not isinstance(instance, MetaInstance) or not instance._qualified_class.endswith("ModelData.Class"):
                continue
            if getattr(instance, "Kind", None) != "Association":
                continue
            for role in getattr(instance, "Role", None) or []:
                if isinstance(role, MetaInstance):
                    role.Mandatory = True
            embedding = association_embedding(instance)
            if embedding is not None:
                instance.EmbeddedRoleTransfer = True
                embedding[1].EmbeddedTransfer = True
