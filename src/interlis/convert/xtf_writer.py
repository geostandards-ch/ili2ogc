"""Write an `XtfTransfer` as XTF 2.3 text, the reverse of `xtf/parse.py`.

`render_xtf` serializes a transfer as is; `write_view_basket` evaluates a VIEW TOPIC's views into one new basket.
"""

from collections.abc import Sequence
from xml.etree import ElementTree as ET

from interlis.builder.forward_refs import SymbolTable
from interlis.builder.repository import ModelRepository
from interlis.convert.jsonfg import UnmappedValue, evaluate_view_objects
from interlis.metamodel.instance import MetaInstance
from interlis.xtf.parse import RawNode, XtfBasket, XtfModelRef, XtfObject, XtfTransfer

_XTF23_NAMESPACE = "http://www.interlis.ch/INTERLIS2.3"


def _raw_node_to_element(node: RawNode, *, tag: str) -> ET.Element:
    """`RawNode` -> its element, with `tag` overriding `node.tag` at THIS level only.

    Needed for VIEW-renamed attributes: the re-keyed `RawNode` still carries the source attribute's tag.
    Nested children keep their own tags.
    """
    elem = ET.Element(tag, dict(node.attrib))
    if node.text is not None:
        elem.text = node.text
    for child in node.children:
        elem.append(_raw_node_to_element(child, tag=child.tag))
    return elem


def _object_element(obj: XtfObject, attr_order: list[str] | None) -> ET.Element:
    """One `XtfObject` -> its `<Model.Topic.Class TID="...">` element.

    `attr_order` overrides the dict order (the XSD sequence for VIEW classes); attributes with no value are skipped.
    """
    attrib = {"TID": obj.tid} if obj.tid is not None else {}
    elem = ET.Element(obj.qualified_class, attrib)
    for name in attr_order if attr_order is not None else list(obj.attributes):
        for node in obj.attributes.get(name, []):
            elem.append(_raw_node_to_element(node, tag=name))
    return elem


def _basket_element(basket: XtfBasket, attr_order_by_class: dict[str, list[str]] | None) -> ET.Element:
    attrib = {"BID": basket.bid}
    if basket.kind is not None:
        attrib["KIND"] = basket.kind
    if basket.endstate is not None:
        attrib["ENDSTATE"] = basket.endstate
    elem = ET.Element(basket.qualified_topic, attrib)
    for obj in basket.objects:
        order = (attr_order_by_class or {}).get(obj.qualified_class)
        elem.append(_object_element(obj, order))
    return elem


def render_xtf(transfer: XtfTransfer, *, attr_order_by_class: dict[str, list[str]] | None = None) -> str:
    """Serialize `transfer` to XTF 2.3 wire text - the bare, UPPERCASE-attribute convention `xtf/parse.py` reads.

    `attr_order_by_class` (keyed by `XtfObject.qualified_class`) overrides attribute order for VIEW-derived classes.
    """
    root = ET.Element("TRANSFER", {"xmlns": _XTF23_NAMESPACE})
    header = ET.SubElement(root, "HEADERSECTION", {"VERSION": "2.3"})
    if transfer.sender is not None:
        header.set("SENDER", transfer.sender)
    models_elem = ET.SubElement(header, "MODELS")
    for model in transfer.models:
        model_attrib = {"NAME": model.name}
        if model.version is not None:
            model_attrib["VERSION"] = model.version
        if model.uri is not None:
            model_attrib["URI"] = model.uri
        ET.SubElement(models_elem, "MODEL", model_attrib)
    data = ET.SubElement(root, "DATASECTION")
    for basket in transfer.baskets:
        data.append(_basket_element(basket, attr_order_by_class))
    return '<?xml version="1.0" encoding="UTF-8"?>' + ET.tostring(root, encoding="unicode")


def _view_attr_order(view: MetaInstance) -> list[str]:
    """The VIEW's own `ClassAttribute` declaration order - refman SS4.3.7's XSD-sequence order."""
    return [name for attr in getattr(view, "ClassAttribute", None) or [] if (name := getattr(attr, "Name", None))]


def write_view_basket(
    view: MetaInstance,
    transfer: XtfTransfer,
    *,
    bid: str,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
    kind: str | None = None,
    catalogs: Sequence[XtfTransfer] = (),
    unmapped: list[UnmappedValue] | None = None,
) -> XtfBasket:
    """Evaluate `view` against `transfer` and assemble its VIEW TOPIC `XtfBasket`.

    `kind=None` omits `KIND` (FULL is assumed; an ili2c-compiled XSD may not declare it at all). `bid` is the caller's
    choice. Objects are retagged with the VIEW's qualified `Model.Topic.ViewName`, as the wire format requires.
    """
    objects = evaluate_view_objects(
        view, transfer, symbol_table=symbol_table, repository=repository, catalogs=catalogs, unmapped=unmapped
    )
    qualified_view = symbol_table.qualified_name_of(view)
    if qualified_view is None or "." not in qualified_view:
        view_name = getattr(view, "Name", None)
        raise ValueError(f"write_view_basket: {view_name!r} isn't registered under a Model.Topic.Name path")
    qualified_topic = qualified_view.rsplit(".", 1)[0]
    objects = [XtfObject(tid=obj.tid, qualified_class=qualified_view, attributes=obj.attributes) for obj in objects]
    return XtfBasket(bid=bid, qualified_topic=qualified_topic, kind=kind, endstate=None, objects=objects)


def _unquote(value: str | None) -> str | None:
    """Strip one matching pair of surrounding `"` - `Model.Version`/`Model.At` keep their source quoting."""
    if value is not None and len(value) >= 2 and value[0] == value[-1] == '"':
        return value[1:-1]
    return value


def _model_ref_for(qualified_name: str, symbol_table: SymbolTable) -> XtfModelRef:
    """`XtfModelRef` for the MODEL owning `qualified_name`; `version`/`uri` stay `None` when unresolvable or absent."""
    model_name = qualified_name.split(".", 1)[0]
    model_instance = symbol_table.resolve(model_name)
    version = _unquote(getattr(model_instance, "Version", None)) if model_instance is not None else None
    uri = _unquote(getattr(model_instance, "At", None)) if model_instance is not None else None
    return XtfModelRef(name=model_name, version=version, uri=uri)


def write_xtf(
    view: MetaInstance,
    transfer: XtfTransfer,
    *,
    bid: str,
    symbol_table: SymbolTable,
    repository: ModelRepository | None = None,
    sender: str | None = None,
    merge_with_source: bool = False,
    catalogs: Sequence[XtfTransfer] = (),
    unmapped: list[UnmappedValue] | None = None,
) -> str:
    """Compose one VIEW TOPIC's materialized data into a complete .xtf document.

    By default a standalone transfer holds only the VIEW basket and its own model; `merge_with_source=True` appends
    it to a copy of the source transfer's baskets/models instead.
    """
    basket = write_view_basket(
        view,
        transfer,
        bid=bid,
        symbol_table=symbol_table,
        repository=repository,
        catalogs=catalogs,
        unmapped=unmapped,
    )
    qualified_view = symbol_table.qualified_name_of(view)
    assert qualified_view is not None  # write_view_basket already raised ValueError otherwise
    view_model = _model_ref_for(qualified_view, symbol_table)
    models = list(transfer.models) if merge_with_source else []
    if not any(m.name == view_model.name for m in models):
        models.append(view_model)
    baskets = [*transfer.baskets, basket] if merge_with_source else [basket]
    out = XtfTransfer(sender=sender, ili_version=transfer.ili_version, models=models, baskets=baskets)
    return render_xtf(out, attr_order_by_class={qualified_view: _view_attr_order(view)})


__all__ = [
    "XtfBasket",
    "XtfModelRef",
    "XtfTransfer",
    "render_xtf",
    "write_view_basket",
    "write_xtf",
]
