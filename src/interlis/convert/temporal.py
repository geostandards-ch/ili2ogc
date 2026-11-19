"""The temporal attributes of a class and the JSON-FG `time` member they give.

An attribute is designated with `!!@primaryInstant=true` or `!!@primaryInterval=start|end` (the tag names of OGC
24-017r1, in eCH-0117 meta-attribute syntax). A class that designates none and has exactly one XMLDate/XMLDateTime
attribute gets that one as its primary instant, as OGC API Features Part 5 does for a single temporal property.
"""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import datetime, timezone
from typing import Any
from zoneinfo import ZoneInfo

from interlis.xtf.schema import ResolvedAttribute

PRIMARY_INSTANT = "primary-instant"
PRIMARY_INTERVAL_START = "primary-interval-start"
PRIMARY_INTERVAL_END = "primary-interval-end"
_TEMPORAL_FORMATS = ("XMLDate", "XMLDateTime")
_DATE_TIME = re.compile(r"^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2}):(\d{2})(?:\.(\d+))?$")
_ZONE = ZoneInfo("Europe/Zurich")  # INTERLIS date-times carry no time zone; JSON-FG timestamps must be UTC


def temporal_roles(attributes: Mapping[str, ResolvedAttribute]) -> dict[str, str]:
    """`attribute name -> Part 5 role` for a class's direct attributes; empty when the designation is inconsistent."""
    temporal = [name for name, r in attributes.items() if _is_temporal(r)]
    designated: dict[str, str] = {}
    for name in temporal:
        marks = {m.Name: m.Value for m in getattr(attributes[name].attr, "MetaAttribute", None) or []}
        if str(marks.get("primaryInstant")).lower() == "true":
            designated[name] = PRIMARY_INSTANT
        elif marks.get("primaryInterval") in ("start", "end"):
            designated[name] = f"primary-interval-{marks['primaryInterval']}"
    if not designated:
        return {temporal[0]: PRIMARY_INSTANT} if len(temporal) == 1 else {}
    roles = list(designated.values())
    consistent = len(set(roles)) == len(roles) and not (PRIMARY_INSTANT in roles and len(roles) > 1)
    return designated if consistent else {}


def time_member(roles: Mapping[str, str], properties: Mapping[str, Any]) -> dict[str, Any] | None:
    """The JSON-FG `time` object built from the designated attributes' values, `None` when they are all absent."""
    by_role = {role: properties.get(name) for name, role in roles.items()}
    instant = by_role.get(PRIMARY_INSTANT)
    if instant is not None:
        return _single(str(instant))
    start, end = by_role.get(PRIMARY_INTERVAL_START), by_role.get(PRIMARY_INTERVAL_END)
    if start is None and end is None:
        return None
    return {"interval": [_bound(start), _bound(end)]}


def _is_temporal(resolved: ResolvedAttribute) -> bool:
    return (
        resolved.type_kind == "FormattedType" and getattr(resolved.type_instance, "Format", None) in _TEMPORAL_FORMATS
    )


def _single(text: str) -> dict[str, str]:
    value = _bound(text)
    return {"date": value} if "T" not in value else {"timestamp": value}


def _bound(value: Any) -> str:
    """A JSON-FG date or timestamp; `..` for an absent end.

    A date-time at midnight is a date (the data are date-granular); any other is read as Swiss local time and
    written in UTC, as JSON-FG requires.
    """
    if value is None:
        return ".."
    text = str(value)
    if "T" not in text:
        return text
    match = _DATE_TIME.match(text)
    if match is None:
        return text
    year, month, day, hour, minute, second, fraction = match.groups()
    microsecond = int((fraction or "0").ljust(6, "0")[:6])
    moment = datetime(int(year), int(month), int(day), int(hour), int(minute), int(second), microsecond)
    if moment.hour == moment.minute == moment.second == moment.microsecond == 0:
        return text[:10]
    utc = moment.replace(tzinfo=_ZONE).astimezone(timezone.utc)
    return utc.strftime("%Y-%m-%dT%H:%M:%S") + (f".{utc.microsecond:06d}".rstrip("0") if utc.microsecond else "") + "Z"
