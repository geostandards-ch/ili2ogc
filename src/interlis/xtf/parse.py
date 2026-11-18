"""Streaming parser for INTERLIS transfer files (.xtf), structure only.

Each attribute is kept as a raw XML subtree (`RawNode`); its meaning depends on the declared type, known elsewhere.
Parsing is incremental so large files fit in memory. XTF 2.3 and 2.4 envelopes are both accepted.
"""

from collections.abc import Iterator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET


def _strip_ns(tag: str) -> str:
    return tag.rsplit("}", 1)[-1] if tag.startswith("{") else tag


_DELETE_OBJECT_TAG = "{http://www.interlis.ch/xtf/2.4/INTERLIS}delete"


def _model_name_from_tag(tag: str) -> str | None:
    """Return the MODEL name a namespaced XTF 2.4 tag belongs to, or `None` for a bare XTF 2.3 tag.

    The namespace URI ends in the model name, which is needed to build `Model.Topic.Class`.
    """
    if not tag.startswith("{"):
        return None
    uri = tag[1:].split("}", 1)[0]
    prefix = "http://www.interlis.ch/xtf/2.4/"
    if not uri.startswith(prefix):
        return None
    name = uri[len(prefix) :]
    return name if name and name != "INTERLIS" else None


def _get_attr_ci(elem: ET.Element, name: str) -> str | None:
    """Look up an attribute ignoring case and namespace; `name` is UPPERCASE.

    XTF 2.3 attributes are bare UPPERCASE and XTF 2.4 ones are namespaced lowercase.
    """
    if name in elem.attrib:
        return elem.attrib[name]
    for key, value in elem.attrib.items():
        if _strip_ns(key).upper() == name:
            return value
    return None


@dataclass
class RawNode:
    """Raw XML subtree for one attribute (or one of its descendants).

    The namespace is stripped from the tag and the text is trimmed (`None` if empty).
    """

    tag: str
    text: str | None
    attrib: dict[str, str]
    children: list["RawNode"] = field(default_factory=list)
    blank: str = ""


@dataclass
class XtfObject:
    """An instance: a class or a non-embedded relation, both encoded the same way.

    `qualified_class` is the tag as written, not yet resolved; empty for a `DeleteObject`.
    """

    tid: str | None
    qualified_class: str
    attributes: dict[str, list[RawNode]]  # attribute name -> occurrences (several for LIST/BAG)
    operation: str | None = None
    """The upper-cased `OPERATION` (`INSERT`/`UPDATE`/`DELETE`) of an incremental transfer, `None` when absent
    (an `INSERT`)."""


@dataclass
class XtfBasket:
    bid: str
    qualified_topic: str
    kind: str | None
    endstate: str | None
    objects: list[XtfObject] = field(default_factory=list)


@dataclass
class XtfModelRef:
    name: str
    version: str | None
    uri: str | None


@dataclass
class XtfTransfer:
    sender: str | None
    ili_version: str | None
    models: list[XtfModelRef]
    baskets: list[XtfBasket]


def _to_raw_node(elem: ET.Element) -> RawNode:
    text = elem.text.strip() if elem.text and elem.text.strip() else None
    if text is None and len(elem) == 0 and elem.get("OID"):
        text = elem.get("OID")
    return RawNode(
        tag=_strip_ns(elem.tag),
        text=text,
        attrib=dict(elem.attrib),
        children=[_to_raw_node(c) for c in elem],
        blank=elem.text if elem.text and not elem.text.strip() and len(elem) == 0 else "",
    )


def _walk(path: Path) -> Iterator[tuple[str, Any]]:
    """Walk an .xtf once, yielding the header, each basket as it opens, and each object as it closes.

    Nothing is retained, so memory stays flat whatever the file size.
    """
    sender: str | None = None
    ili_version: str | None = None
    models: list[XtfModelRef] = []

    # Stack of (role, tag_without_namespace); the role comes from structural position,
    # since tags are named after the model.
    stack: list[tuple[str, str]] = []
    current_basket: XtfBasket | None = None
    current_object: XtfObject | None = None

    for event, elem in ET.iterparse(str(path), events=("start", "end")):
        tag = _strip_ns(elem.tag)
        if event == "start":
            parent_role = stack[-1][0] if stack else None
            # Section keywords are compared case-insensitively (XTF 2.3 UPPERCASE, XTF 2.4 lowercase).
            upper_tag = tag.upper()
            if parent_role is None:
                role = "transfer"
            elif parent_role == "transfer":
                role = (
                    "headersection"
                    if upper_tag == "HEADERSECTION"
                    else ("datasection" if upper_tag == "DATASECTION" else "other")
                )
            elif parent_role == "headersection":
                # SENDER is a child element only in XTF 2.4 (an attribute of HEADERSECTION in XTF 2.3,
                # handled at its end event).
                if upper_tag == "MODELS":
                    role = "models"
                elif upper_tag == "SENDER":
                    role = "sender"
                else:
                    role = "other"
            elif parent_role == "models":
                role = "model"
            elif parent_role == "datasection":
                role = "basket"
                model_name = _model_name_from_tag(elem.tag)
                qualified_topic = f"{model_name}.{tag}" if model_name else tag
                current_basket = XtfBasket(
                    bid=_get_attr_ci(elem, "BID") or "",
                    qualified_topic=qualified_topic,
                    kind=_get_attr_ci(elem, "KIND"),
                    endstate=_get_attr_ci(elem, "ENDSTATE"),
                )
                yield ("basket", current_basket)
            elif parent_role == "basket":
                role = "object"
                # XTF 2.4: the namespace names the object's model and the tag is the bare class name, so rebuild
                # `Model.Topic.Class` from the basket TOPIC. XTF 2.3: the tag is already complete.
                model_name = _model_name_from_tag(elem.tag)
                basket_topic = current_basket.qualified_topic.rsplit(".", 1)[-1]
                qualified_class = f"{model_name}.{basket_topic}.{tag}" if model_name else tag
                operation = (_get_attr_ci(elem, "OPERATION") or "").upper() or None
                if elem.tag == _DELETE_OBJECT_TAG:  # `DeleteObject`: names a TID only, never its class
                    qualified_class, operation = "", "DELETE"
                current_object = XtfObject(
                    tid=_get_attr_ci(elem, "TID"),
                    qualified_class=qualified_class,
                    attributes={},
                    operation=operation,
                )
            elif parent_role == "object":
                role = "attribute"
            else:
                role = "raw"  # descendant of an attribute already being captured
            stack.append((role, tag))
            continue

        # event == "end"
        role, _tag = stack.pop()
        if role == "attribute":
            current_object.attributes.setdefault(tag, []).append(_to_raw_node(elem))
            elem.clear()
        elif role == "object":
            yield ("object", (current_basket, current_object))
            current_object = None
            elem.clear()
        elif role == "basket":
            current_basket = None
            elem.clear()
        elif role == "model":
            # XTF 2.3: NAME is an attribute (VERSION/URI alongside).
            # XTF 2.4: the model name is the element's text, with no version/uri.
            name = _get_attr_ci(elem, "NAME")
            if name is None:
                name = elem.text.strip() if elem.text else ""
            models.append(XtfModelRef(name=name, version=_get_attr_ci(elem, "VERSION"), uri=_get_attr_ci(elem, "URI")))
            elem.clear()
        elif role == "sender":
            sender = elem.text.strip() if elem.text else None
        elif role == "headersection":
            if sender is None:  # not already set via the XTF 2.4 "sender" child role above
                sender = _get_attr_ci(elem, "SENDER")
            ili_version = _get_attr_ci(elem, "VERSION")
            yield ("header", (sender, ili_version, models))


def parse_xtf(path: Path) -> XtfTransfer:
    """Parse an .xtf into `(sender, version, models, baskets)`, holding every object in memory.

    Structure only: no reference is resolved. Use `iter_objects` to stream instead.
    """
    transfer = XtfTransfer(sender=None, ili_version=None, models=[], baskets=[])
    for kind, payload in _walk(path):
        if kind == "header":
            transfer.sender, transfer.ili_version, transfer.models = payload
        elif kind == "basket":
            transfer.baskets.append(payload)
        else:
            payload[0].objects.append(payload[1])
    return transfer


def read_skeleton(path: Path) -> XtfTransfer:
    """The header and the baskets (topic, BID, kind) of an .xtf, with no object kept - flat memory."""
    transfer = XtfTransfer(sender=None, ili_version=None, models=[], baskets=[])
    for kind, payload in _walk(path):
        if kind == "header":
            transfer.sender, transfer.ili_version, transfer.models = payload
        elif kind == "basket":
            transfer.baskets.append(payload)
    return transfer


def iter_objects(path: Path) -> Iterator[tuple[XtfBasket, XtfObject]]:
    """Yield every object of an .xtf with its basket, one at a time.

    The basket's `objects` stays empty, so checks needing all objects (references, VIEWs) need `parse_xtf`.
    """
    for kind, payload in _walk(path):
        if kind == "object":
            yield payload


def iter_events(path: Path) -> Iterator[tuple[str, Any]]:
    """Yield each basket as it opens, then each of its objects; empty baskets are still reported."""
    for kind, payload in _walk(path):
        if kind != "header":
            yield kind, payload


def _is_incremental_basket(basket: XtfBasket) -> bool:
    return (basket.kind or "").upper() in ("UPDATE", "INCREMENTAL")


def is_incremental(source: XtfTransfer | Path) -> bool:
    """Whether a transfer is incremental: a basket of KIND UPDATE/INCREMENTAL or an UPDATE/DELETE operation."""
    if isinstance(source, XtfTransfer):
        return any(
            _is_incremental_basket(b) or any(o.operation in ("UPDATE", "DELETE") for o in b.objects)
            for b in source.baskets
        )
    for kind, payload in _walk(source):
        if kind == "basket" and _is_incremental_basket(payload):
            return True
        if kind == "object" and payload[1].operation in ("UPDATE", "DELETE"):
            return True
    return False
