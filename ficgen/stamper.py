"""Stamp KDL publication `date` metadata into story and chapter Markdown files."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

import kdl

from . import t
from .kdlMeta import (
    StoryParseError,
    findNodes,
    formatKdlString,
    getBoolField,
    isKdlNull,
    mergeDocuments,
    parseIsoDateTime,
    parseKdlDocument,
    readUtf8File,
)
from .parser import (
    discoverStoryDirs,
    discoverStoryFiles,
    findMetadataFences,
    normalizeLineEndings,
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
    parseIsoDateTime(cleaned, f"Invalid ISO-8601 date {cleaned!r} (expected e.g. YYYY-MM-DD).")
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
        if isKdlNull(val):
            continue
        if isinstance(val, str) and not val.strip():
            continue
        return (True, True)
    return (True, False)


def _isKdlUnpublished(rawText: str, sourceDesc: str = "<kdl>") -> bool:
    """Return True if a KDL metadata file explicitly declares published false."""
    doc = parseKdlDocument(rawText, sourceDesc=sourceDesc)
    return not getBoolField(doc, "published", default=True, sourceDesc=sourceDesc)


def _isMarkdownUnpublished(normalized: str, sourceDesc: str = "<markdown>") -> bool:
    """Return True if a normalized Markdown document explicitly declares published false."""
    _, metaBlocks = findMetadataFences(normalized)
    merged = mergeDocuments(*(parseKdlDocument(block.body, sourceDesc) for block in metaBlocks))
    return not getBoolField(merged, "published", default=True, sourceDesc=sourceDesc)


def _updateKdlBlockDate(
    body: str,
    doc: kdl.Document,
    dateValue: str,
    *,
    force: bool,
) -> tuple[str, bool]:
    """Insert or update a KDL `date "..."` node inside a metadata fence body."""
    hasNode, hasValue = _hasActiveDateValue(doc)
    if hasValue and not force:
        return (body, False)

    dateLit = formatKdlString(dateValue)
    if hasNode:
        dateNodes = findNodes(doc, "date", "published-date")
        lineMatches = list(_KDL_DATE_LINE_RE.finditer(body))
        # Prefer in-place single-line replacement when unambiguous, to keep comments and formatting intact.
        if len(dateNodes) == 1 and len(lineMatches) == 1:
            lineMatch = lineMatches[0]
            replacement = f'{lineMatch.group("indent")}{lineMatch.group("key")} {dateLit}'
            newBody = body[: lineMatch.start()] + replacement + body[lineMatch.end() :]
            return (newBody, newBody != body)

        # Fallback (multiple date nodes, semicolons, or block comments): update KDL AST and reprint.
        for node in dateNodes:
            node.args = [dateValue]
        reprinted = doc.print()
        if reprinted and not reprinted.endswith("\n"):
            reprinted += "\n"
        return (reprinted, reprinted != body)

    if body and not body.endswith("\n"):
        return (body + f"\ndate {dateLit}\n", True)
    return (body + f"date {dateLit}\n", True)


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
    normalized = normalizeLineEndings(rawText)
    leading, metaBlocks = findMetadataFences(normalized)

    if metaBlocks:
        parsedBlocks = [(block, parseKdlDocument(block.body, sourceDesc)) for block in metaBlocks]
        merged = mergeDocuments(*(doc for _, doc in parsedBlocks))
        _, hasAnyDateValue = _hasActiveDateValue(merged)
        if hasAnyDateValue and not force:
            return (rawText, False)

        # Pick the last fence containing a date node (matching parser's last-wins semantics),
        # or the first metadata fence if none currently declares a date node.
        targetBlock, targetDoc = parsedBlocks[0]
        for block, doc in reversed(parsedBlocks):
            hasNode, _ = _hasActiveDateValue(doc)
            if hasNode:
                targetBlock, targetDoc = block, doc
                break

        newBody, changed = _updateKdlBlockDate(targetBlock.body, targetDoc, dateValue, force=force)
        if not changed:
            return (rawText, False)
        updatedNormalized = normalized[: targetBlock.bodyStart] + newBody + normalized[targetBlock.bodyEnd :]
        updated = updatedNormalized.replace("\n", lineEnding)
        return (updated, updated != rawText)

    # No metadata fence exists yet: insert one after a leading '# Title', or at the top.
    dateLit = formatKdlString(dateValue)
    metaBlock = f"```metadata\ndate {dateLit}\n```"
    if leading.title:
        prefix = normalized[: leading.contentStart].rstrip("\n")
        rest = normalized[leading.contentStart :].lstrip("\n")
        updatedNormalized = f"{prefix}\n\n{metaBlock}\n\n{rest}" if rest else f"{prefix}\n\n{metaBlock}\n"
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
        storyPublished = not _isKdlUnpublished(
            readUtf8File(storyKdlPath, label="story KDL"),
            sourceDesc=str(storyKdlPath),
        )
    elif indexMdPath is not None:
        storyPublished = not _isMarkdownUnpublished(
            normalizeLineEndings(readUtf8File(indexMdPath, label="story file")),
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
    rawText = readUtf8File(path, label="story file")
    filePublished = storyPublished and not _isMarkdownUnpublished(
        normalizeLineEndings(rawText),
        sourceDesc=sourceDesc,
    )
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
