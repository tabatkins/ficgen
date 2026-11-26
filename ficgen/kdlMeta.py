"""KDL metadata parsing and typed field extraction helpers."""

from __future__ import annotations

import datetime as dt
import re
import urllib.parse
from pathlib import Path

import kdl

from . import t

_KEY_SEP_RE: t.Final[re.Pattern[str]] = re.compile(r"[\s_]+")
_TRUTHY_STRINGS: t.Final[frozenset[str]] = frozenset({"true", "yes", "complete", "completed"})
_FALSY_STRINGS: t.Final[frozenset[str]] = frozenset({"false", "no", "incomplete", "wip", "in progress"})


class StoryParseError(ValueError):
    """Raised when a story source directory, KDL metadata block, or document is invalid."""


def normalizeKey(name: str) -> str:
    """Normalize a KDL node name for flexible matching (e.g. 'base_url' -> 'base-url')."""
    return _KEY_SEP_RE.sub("-", name.strip().lower())


def parseKdlDocument(text: str, sourceDesc: str = "<kdl>") -> kdl.Document:
    """Parse a KDL string into a kdl.Document, wrapping syntax errors in StoryParseError."""
    if not text.strip():
        return kdl.Document()
    try:
        return kdl.parse(text)
    except kdl.ParseError as exc:
        msg = f"Invalid KDL in {sourceDesc}: {exc}"
        raise StoryParseError(msg) from exc


def findNodes(doc: kdl.Document, *keys: str) -> list[kdl.Node]:
    """Return all top-level nodes whose normalized name matches any candidate key."""
    targets = {normalizeKey(key) for key in keys}
    return [node for node in doc.nodes if normalizeKey(node.name) in targets]


def _coerceString(val: t.Any, fieldName: str, sourceDesc: str) -> str:
    """Convert a KDL value (str or tagged url) to str, rejecting incompatible types."""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, urllib.parse.ParseResult):
        return urllib.parse.urlunparse(val).strip()
    if isinstance(val, kdl.Stringish):
        return str(val.value).strip()
    msg = f"Expected string value for KDL field {fieldName!r} in {sourceDesc}, got {val!r}"
    raise StoryParseError(msg)


def coerceStringValue(val: t.Any, fieldName: str, sourceDesc: str = "<kdl>") -> str:
    """Convert a KDL value (str or tagged url) to str, returning empty string for null."""
    if val is None or isinstance(val, kdl.Null):
        return ""
    return _coerceString(val, fieldName, sourceDesc)


def getNodeStringArgs(node: kdl.Node, sourceDesc: str = "<kdl>") -> tuple[str, ...]:
    """Return non-empty string arguments from a single KDL node, rejecting non-string values."""
    values: list[str] = []
    for raw in node.args:
        cleaned = coerceStringValue(raw, node.name, sourceDesc)
        if cleaned:
            values.append(cleaned)
    return tuple(values)


def getStringField(doc: kdl.Document, *keys: str, sourceDesc: str = "<kdl>") -> str:
    """Return the string argument of the last matching KDL node, or empty string if absent."""
    nodes = findNodes(doc, *keys)
    for node in reversed(nodes):
        if not node.args:
            continue
        first = node.args[0]
        if first is None or isinstance(first, kdl.Null):
            continue
        value = _coerceString(first, node.name, sourceDesc)
        if value:
            return value
    return ""


def getListField(doc: kdl.Document, *keys: str, sourceDesc: str = "<kdl>") -> t.MetadataList:
    """Collect deduplicated strings from matching KDL nodes' args and child blocks."""
    nodes = findNodes(doc, *keys)
    if not nodes:
        return ()

    seen: set[str] = set()
    items: list[str] = []

    def _addItem(rawVal: t.Any, fieldName: str) -> None:
        if rawVal is None or isinstance(rawVal, kdl.Null):
            return
        cleaned = _coerceString(rawVal, fieldName, sourceDesc)
        lowered = cleaned.lower()
        if cleaned and lowered not in seen:
            seen.add(lowered)
            items.append(cleaned)

    for node in nodes:
        for arg in node.args:
            _addItem(arg, node.name)
        for child in node.nodes:
            if child.args:
                for childArg in child.args:
                    _addItem(childArg, node.name)
            elif child.name != "-":
                _addItem(child.name, node.name)

    return tuple(items)


def getBoolField(
    doc: kdl.Document,
    *keys: str,
    default: bool,
    sourceDesc: str = "<kdl>",
) -> bool:
    """Parse an optional boolean KDL field (native bool or status string)."""
    nodes = findNodes(doc, *keys)
    for node in reversed(nodes):
        if not node.args:
            continue
        raw = node.args[0]
        if raw is None or isinstance(raw, kdl.Null):
            continue
        if isinstance(raw, bool):
            return raw
        if isinstance(raw, kdl.Bool):
            return raw.value
        if isinstance(raw, str):
            lowered = raw.strip().lower()
            if lowered in _TRUTHY_STRINGS:
                return True
            if lowered in _FALSY_STRINGS:
                return False
        msg = f"Invalid boolean value for KDL field {node.name!r} in {sourceDesc}: {raw!r}"
        raise StoryParseError(msg)
    return default


def getIntField(doc: kdl.Document, *keys: str, sourceDesc: str = "<kdl>") -> int | None:
    """Parse an optional positive integer KDL field."""
    nodes = findNodes(doc, *keys)
    for node in reversed(nodes):
        if not node.args:
            continue
        raw = node.args[0]
        if raw is None or isinstance(raw, kdl.Null):
            continue
        if isinstance(raw, bool):
            msg = f"Expected integer for KDL field {node.name!r} in {sourceDesc}, got bool {raw!r}"
            raise StoryParseError(msg)
        if isinstance(raw, int):
            value = raw
        elif isinstance(raw, float) and raw.is_integer():
            value = int(raw)
        elif isinstance(raw, kdl.Decimal) and isinstance(raw.mantissa, int) and raw.exponent == 0:
            value = raw.mantissa
        else:
            msg = f"Invalid integer value for KDL field {node.name!r} in {sourceDesc}: {raw!r}"
            raise StoryParseError(msg)
        if value < 1:
            msg = f"KDL field {node.name!r} in {sourceDesc} must be >= 1, got {value}"
            raise StoryParseError(msg)
        return value
    return None


def getDateField(
    doc: kdl.Document,
    fallbackPath: Path,
    *keys: str,
) -> tuple[dt.datetime, bool]:
    """Parse an ISO-8601 KDL date/date-time field, falling back to file mtime."""
    sourceDesc = str(fallbackPath)
    nodes = findNodes(doc, *keys)
    for node in reversed(nodes):
        if not node.args:
            continue
        raw = node.args[0]
        if raw is None or isinstance(raw, kdl.Null):
            continue
        if isinstance(raw, dt.datetime):
            parsed = raw.replace(tzinfo=dt.UTC) if raw.tzinfo is None else raw.astimezone(dt.UTC)
            return (parsed, True)
        if isinstance(raw, dt.date):
            return (dt.datetime(raw.year, raw.month, raw.day, tzinfo=dt.UTC), True)
        if isinstance(raw, str):
            cleaned = raw.strip()
            if not cleaned:
                continue
            normalized = cleaned.replace("Z", "+00:00")
            try:
                parsed = dt.datetime.fromisoformat(normalized)
            except ValueError as exc:
                msg = f"Invalid ISO date for KDL field {node.name!r} in {sourceDesc}: {cleaned!r}"
                raise StoryParseError(msg) from exc
            parsed = parsed.replace(tzinfo=dt.UTC) if parsed.tzinfo is None else parsed.astimezone(dt.UTC)
            return (parsed, True)
        msg = f"Invalid date value for KDL field {node.name!r} in {sourceDesc}: {raw!r}"
        raise StoryParseError(msg)

    mtime = fallbackPath.stat().st_mtime
    return (dt.datetime.fromtimestamp(mtime, tz=dt.UTC), False)


def mergeDocuments(*docs: kdl.Document) -> kdl.Document:
    """Combine nodes from multiple KDL documents in order into a new Document."""
    merged = kdl.Document()
    for doc in docs:
        merged.nodes.extend(doc.nodes)
    return merged
