"""KDL metadata parsing, formatting, and typed field extraction helpers."""

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


def isKdlNull(val: t.Any) -> bool:
    """Return True if a parsed KDL value represents null."""
    return val is None or isinstance(val, kdl.Null)


def readUtf8File(path: Path, label: str = "file") -> str:
    """Read a UTF-8 text file, wrapping I/O and encoding errors in StoryParseError."""
    try:
        return path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        msg = f"Failed to read {label} {path}: {exc}"
        raise StoryParseError(msg) from exc


def parseKdlDocument(text: str, sourceDesc: str = "<kdl>") -> kdl.Document:
    """Parse a KDL string into a kdl.Document, wrapping syntax errors in StoryParseError."""
    if not text.strip():
        return kdl.Document()
    try:
        return kdl.parse(text)
    except kdl.ParseError as exc:
        msg = f"Invalid KDL in {sourceDesc}: {exc}"
        raise StoryParseError(msg) from exc


def parseIsoDateTime(raw: str, errorMessage: str) -> dt.datetime:
    """Parse an ISO-8601 date or datetime string into a UTC-aware datetime."""
    try:
        parsed = dt.datetime.fromisoformat(raw)
    except ValueError as exc:
        raise StoryParseError(errorMessage) from exc
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=dt.UTC)
    return parsed.astimezone(dt.UTC)


def formatKdlString(value: str) -> str:
    """Serialize a Python string as a properly escaped KDL string literal."""
    return kdl.String(value).print()


def formatKdlListLine(
    nodeName: str,
    values: t.Sequence[str],
    *,
    emitEmpty: bool = False,
) -> str | None:
    """Format a KDL list metadata line, optionally emitting an empty-string placeholder."""
    cleaned = [val.strip() for val in values if val.strip()]
    if not cleaned:
        return f'{nodeName} ""' if emitEmpty else None
    args = " ".join(formatKdlString(val) for val in cleaned)
    return f"{nodeName} {args}"


def findNodes(doc: kdl.Document, *keys: str) -> list[kdl.Node]:
    """Return all top-level nodes whose normalized name matches any candidate key."""
    targets = {normalizeKey(key) for key in keys}
    return [node for node in doc.nodes if normalizeKey(node.name) in targets]


def _iterNonNullFirstArgs(doc: kdl.Document, *keys: str) -> t.Iterator[tuple[kdl.Node, t.Any]]:
    """Yield (node, firstArg) in reverse order for matching nodes whose first argument is non-null."""
    for node in reversed(findNodes(doc, *keys)):
        if not node.args:
            continue
        first = node.args[0]
        if isKdlNull(first):
            continue
        yield (node, first)


def coerceStringValue(val: t.Any, fieldName: str, sourceDesc: str = "<kdl>") -> str:
    """Convert a KDL value (str or tagged url) to str, returning empty string for null."""
    if isKdlNull(val):
        return ""
    if isinstance(val, str):
        return val.strip()
    if isinstance(val, urllib.parse.ParseResult):
        return urllib.parse.urlunparse(val).strip()
    if isinstance(val, kdl.Stringish):
        return str(val.value).strip()
    msg = f"Expected string value for KDL field {fieldName!r} in {sourceDesc}, got {val!r}"
    raise StoryParseError(msg)


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
    for node, first in _iterNonNullFirstArgs(doc, *keys):
        value = coerceStringValue(first, node.name, sourceDesc)
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
        cleaned = coerceStringValue(rawVal, fieldName, sourceDesc)
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
                # Allow bare string child nodes without a leading `-` bullet.
                _addItem(child.name, node.name)

    return tuple(items)


def getBoolField(
    doc: kdl.Document,
    *keys: str,
    default: bool,
    sourceDesc: str = "<kdl>",
) -> bool:
    """Parse an optional boolean KDL field (native bool or status string)."""
    for node, raw in _iterNonNullFirstArgs(doc, *keys):
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


def getIntField(
    doc: kdl.Document,
    *keys: str,
    minValue: int = 1,
    sourceDesc: str = "<kdl>",
) -> int | None:
    """Parse an optional integer KDL field bounded below by minValue."""
    for node, raw in _iterNonNullFirstArgs(doc, *keys):
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
        if value < minValue:
            msg = f"KDL field {node.name!r} in {sourceDesc} must be >= {minValue}, got {value}"
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
    for node, raw in _iterNonNullFirstArgs(doc, *keys):
        if isinstance(raw, dt.datetime):
            parsed = raw.replace(tzinfo=dt.UTC) if raw.tzinfo is None else raw.astimezone(dt.UTC)
            return (parsed, True)
        if isinstance(raw, dt.date):
            return (dt.datetime(raw.year, raw.month, raw.day, tzinfo=dt.UTC), True)
        if isinstance(raw, str):
            cleaned = raw.strip()
            if not cleaned:
                continue
            errorMsg = f"Invalid ISO date for KDL field {node.name!r} in {sourceDesc}: {cleaned!r}"
            return (parseIsoDateTime(cleaned, errorMsg), True)
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
