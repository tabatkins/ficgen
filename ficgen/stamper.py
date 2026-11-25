"""Stamp KDL publication `date` metadata into story and chapter Markdown files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import kdl

from . import t
from .kdlMeta import StoryParseError, findNodes, getBoolField, parseKdlDocument
from .parser import discoverStoryDirs, discoverStoryFiles, parseMarkdownDocument

_TITLE_LINE_RE: t.Final[re.Pattern[str]] = re.compile(r"^#\s+.+$")
_METADATA_BLOCK_RE: t.Final[re.Pattern[str]] = re.compile(
    r"(?ms)^(?P<open>```metadata(?:[ \t]+kdl)?[ \t]*\n)(?P<body>.*?)(?P<close>^```[ \t]*)(?P<tail>\n?)",
)
_LEADING_KDL_BLOCK_RE: t.Final[re.Pattern[str]] = re.compile(
    r"(?ms)\A(?:(?:#[^\n]*\n)?[ \t\n]*)(?P<open>```kdl[ \t]*\n)(?P<body>.*?)(?P<close>^```[ \t]*)(?P<tail>\n?)",
)
_KDL_DATE_LINE_RE: t.Final[re.Pattern[str]] = re.compile(
    r"(?im)^(?P<indent>[ \t]*)(?P<key>date|published[-_]date)(?:[ \t]+.*)?$",
)


@dataclass(slots=True)
class StampEntry:
    """Outcome of attempting to stamp a single Markdown file."""

    path: Path
    action: t.StampAction
    dateValue: str


@dataclass(slots=True)
class StampResult:
    """Aggregate result of a `ficgen stamp` invocation."""

    entries: tuple[StampEntry, ...]
    dryRun: bool

    @property
    def stampedCount(self) -> int:
        """Number of files stamped (or that would be stamped under --dry-run)."""
        return sum(1 for entry in self.entries if entry.action in {"stamped", "would_stamp"})

    @property
    def skippedExistingCount(self) -> int:
        """Number of files skipped because they already had a date node."""
        return sum(1 for entry in self.entries if entry.action == "skipped_existing")

    @property
    def skippedDraftCount(self) -> int:
        """Number of files skipped because they are marked published false."""
        return sum(1 for entry in self.entries if entry.action == "skipped_draft")


def validateDateString(rawDate: str) -> str:
    """Validate an ISO-8601 date or datetime string and return it trimmed."""
    cleaned = rawDate.strip()
    if not cleaned:
        msg = "Date value cannot be empty."
        raise StoryParseError(msg)
    normalized = cleaned.replace("Z", "+00:00")
    try:
        datetime.fromisoformat(normalized)
    except ValueError as exc:
        msg = f"Invalid ISO-8601 date {cleaned!r} (expected e.g. YYYY-MM-DD)."
        raise StoryParseError(msg) from exc
    return cleaned


def defaultTodayDate() -> str:
    """Return today's local calendar date as YYYY-MM-DD."""
    return datetime.now().astimezone().date().isoformat()


def _detectLineEnding(rawText: str) -> str:
    """Detect whether a file uses CRLF or LF line endings."""
    if "\r\n" in rawText:
        return "\r\n"
    return "\n"


def _hasActiveDateValue(doc: kdl.Document) -> tuple[bool, bool]:
    """Return (hasDateNode, hasNonEmptyDateValue) for a KDL metadata document."""
    nodes = findNodes(doc, "date", "published-date")
    if not nodes:
        return (False, False)
    for node in reversed(nodes):
        if not node.args:
            continue
        val = node.args[0]
        if val is None or isinstance(val, kdl.Null):
            continue
        if isinstance(val, str) and not val.strip():
            continue
        return (True, True)
    return (True, False)


def _isMarkdownUnpublished(rawText: str, sourceDesc: str = "<markdown>") -> bool:
    """Return True if a Markdown document explicitly declares published false."""
    parsed = parseMarkdownDocument(rawText, sourceDesc=sourceDesc)
    return not getBoolField(parsed.metadata, "published", default=True, sourceDesc=sourceDesc)


def _isKdlUnpublished(rawText: str, sourceDesc: str = "<kdl>") -> bool:
    """Return True if a KDL metadata file explicitly declares published false."""
    doc = parseKdlDocument(rawText, sourceDesc=sourceDesc)
    return not getBoolField(doc, "published", default=True, sourceDesc=sourceDesc)


def _updateKdlBlockDate(body: str, dateValue: str, *, force: bool, sourceDesc: str) -> tuple[str, bool]:
    """Insert or update a KDL `date "..."` node inside a metadata fence body."""
    doc = parseKdlDocument(body, sourceDesc=sourceDesc)
    hasNode, hasValue = _hasActiveDateValue(doc)
    if hasValue and not force:
        return (body, False)

    if hasNode:
        # Prefer in-place single-line replacement to keep comments and formatting intact.
        lineMatch = _KDL_DATE_LINE_RE.search(body)
        if lineMatch is not None:
            replacement = f'{lineMatch.group("indent")}{lineMatch.group("key")} "{dateValue}"'
            newBody = body[: lineMatch.start()] + replacement + body[lineMatch.end() :]
            return (newBody, newBody != body)

        # Fallback: update KDL AST directly and reprint.
        for node in findNodes(doc, "date", "published-date"):
            node.args = [dateValue]
        return (doc.print(), True)

    if body and not body.endswith("\n"):
        return (body + f'\ndate "{dateValue}"\n', True)
    return (body + f'date "{dateValue}"\n', True)


def stampDocumentText(
    rawText: str,
    dateValue: str,
    *,
    force: bool = False,
    sourceDesc: str = "<markdown>",
) -> tuple[str, bool]:
    """Insert or update `date "..."` inside a Markdown document's KDL metadata block.

    Returns `(updatedText, changed)`. Preserves original line endings and prose untouched.
    """
    lineEnding = _detectLineEnding(rawText)
    normalized = rawText.replace("\r\n", "\n")

    blockMatch = _METADATA_BLOCK_RE.search(normalized) or _LEADING_KDL_BLOCK_RE.match(normalized)
    if blockMatch is not None:
        newBody, changed = _updateKdlBlockDate(
            blockMatch.group("body"),
            dateValue,
            force=force,
            sourceDesc=sourceDesc,
        )
        if not changed:
            return (rawText, False)
        updatedNormalized = normalized[: blockMatch.start("body")] + newBody + normalized[blockMatch.end("body") :]
        updated = updatedNormalized.replace("\n", lineEnding)
        return (updated, updated != rawText)

    # No metadata fence exists yet: insert one after a leading '# Title', or at the top.
    metaBlock = f'```metadata\ndate "{dateValue}"\n```'
    lines = normalized.split("\n")
    firstNonEmpty = 0
    while firstNonEmpty < len(lines) and not lines[firstNonEmpty].strip():
        firstNonEmpty += 1

    if firstNonEmpty < len(lines) and _TITLE_LINE_RE.match(lines[firstNonEmpty]) is not None:
        prefix = "\n".join(lines[: firstNonEmpty + 1])
        rest = "\n".join(lines[firstNonEmpty + 1 :]).lstrip("\n")
        if rest:
            updatedNormalized = f"{prefix}\n\n{metaBlock}\n\n{rest}"
        else:
            updatedNormalized = f"{prefix}\n\n{metaBlock}\n"
    else:
        rest = normalized.lstrip("\n")
        updatedNormalized = f"{metaBlock}\n\n{rest}" if rest else f"{metaBlock}\n"

    updated = updatedNormalized.replace("\n", lineEnding)
    return (updated, True)


def _collectStoryFiles(storyDir: Path) -> list[tuple[Path, bool]]:
    """Collect stampable Markdown files from a story directory along with story published status."""
    storyKdlPath, indexMdPath, chapterFiles = discoverStoryFiles(storyDir)

    storyPublished = True
    if storyKdlPath is not None:
        storyPublished = not _isKdlUnpublished(storyKdlPath.read_text(encoding="utf-8"), sourceDesc=str(storyKdlPath))
    elif indexMdPath is not None:
        storyPublished = not _isMarkdownUnpublished(
            indexMdPath.read_text(encoding="utf-8"),
            sourceDesc=str(indexMdPath),
        )

    if chapterFiles:
        return [(chapterPath, storyPublished) for chapterPath in chapterFiles]
    assert indexMdPath is not None
    return [(indexMdPath, storyPublished)]


def _resolveTargets(storiesDir: Path, targets: t.Sequence[Path]) -> list[tuple[Path, bool]]:
    """Resolve CLI target arguments (files, directories, or story slugs) into file paths."""
    if not targets:
        resolved: list[tuple[Path, bool]] = []
        for storyDir in discoverStoryDirs(storiesDir):
            resolved.extend(_collectStoryFiles(storyDir))
        return resolved

    resolvedTargets: list[tuple[Path, bool]] = []
    seen: set[Path] = set()
    for target in targets:
        candidate = target
        if not candidate.exists():
            slugCandidate = storiesDir / target
            if slugCandidate.exists():
                candidate = slugCandidate
            else:
                msg = f"Target not found: {target}"
                raise StoryParseError(msg)

        if candidate.is_file():
            if candidate.suffix != ".md":
                msg = f"Expected a Markdown (.md) file, got: {candidate}"
                raise StoryParseError(msg)
            key = candidate.resolve()
            if key not in seen:
                seen.add(key)
                resolvedTargets.append((candidate, True))
        elif candidate.is_dir():
            for filePath, storyPublished in _collectStoryFiles(candidate):
                key = filePath.resolve()
                if key not in seen:
                    seen.add(key)
                    resolvedTargets.append((filePath, storyPublished))
        else:
            msg = f"Unsupported target path: {candidate}"
            raise StoryParseError(msg)

    return resolvedTargets


def stampFile(
    path: Path,
    dateValue: str,
    *,
    force: bool = False,
    includeDrafts: bool = False,
    dryRun: bool = False,
    storyPublished: bool = True,
) -> StampEntry:
    """Stamp `date "..."` into a single Markdown file if eligible."""
    sourceDesc = str(path)
    rawText = path.read_text(encoding="utf-8")
    filePublished = storyPublished and not _isMarkdownUnpublished(rawText, sourceDesc=sourceDesc)
    if not filePublished and not includeDrafts:
        return StampEntry(path=path, action="skipped_draft", dateValue=dateValue)

    updatedText, changed = stampDocumentText(rawText, dateValue, force=force, sourceDesc=sourceDesc)
    if not changed:
        return StampEntry(path=path, action="skipped_existing", dateValue=dateValue)

    if dryRun:
        return StampEntry(path=path, action="would_stamp", dateValue=dateValue)

    path.write_text(updatedText, encoding="utf-8")
    return StampEntry(path=path, action="stamped", dateValue=dateValue)


def stampStories(
    storiesDir: Path,
    dateValue: str,
    *,
    targets: t.Sequence[Path] = (),
    force: bool = False,
    includeDrafts: bool = False,
    dryRun: bool = False,
) -> StampResult:
    """Stamp `date "..."` into eligible story/chapter files under storiesDir or explicit targets."""
    validatedDate = validateDateString(dateValue)
    targetFiles = _resolveTargets(storiesDir, targets)
    entries = tuple(
        stampFile(
            filePath,
            validatedDate,
            force=force,
            includeDrafts=includeDrafts,
            dryRun=dryRun,
            storyPublished=storyPublished,
        )
        for filePath, storyPublished in targetFiles
    )
    return StampResult(entries=entries, dryRun=dryRun)
