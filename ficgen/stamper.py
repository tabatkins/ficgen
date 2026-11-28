"""Stamp KDL publication `date` and relationship-implied `characters` metadata into story files."""

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
    formatKdlListLine,
    formatKdlString,
    getBoolField,
    getListField,
    isKdlNull,
    mergeDocuments,
    parseIsoDateTime,
    parseKdlDocument,
    readUtf8File,
)
from .parser import (
    discoverStoryDirs,
    discoverStoryFiles,
    extractRelationshipCharacters,
    findMetadataFences,
    normalizeLineEndings,
)

_KDL_DATE_LINE_RE: t.Final[re.Pattern[str]] = re.compile(
    r"(?im)^(?P<indent>[ \t]*)(?P<key>date|published[-_]date)(?:[ \t]+.*)?$",
)
_KDL_CHARACTERS_LINE_RE: t.Final[re.Pattern[str]] = re.compile(
    r"(?im)^(?P<indent>[ \t]*)(?P<key>characters?)(?:[ \t]+(?P<rest>.*))?$",
)
_STORY_KDL_NAMES: t.Final[tuple[str, ...]] = ("story.kdl", "meta.kdl", "index.kdl")


@dataclass(slots=True)
class StampEntry:
    """Outcome of attempting to stamp a single story or chapter file."""

    path: Path
    action: t.StampAction
    dateValue: str
    stampedDate: bool = False
    addedCharacters: t.MetadataList = ()


@dataclass(slots=True)
class _StampTarget:
    """Resolved file target for `ficgen stamp` with date/character stamping settings."""

    path: Path
    storyPublished: bool
    stampDate: bool
    stampCharacters: bool
    reportIfUnchanged: bool = True
    existingCharacters: t.MetadataList = ()


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
        """Number of files skipped because their metadata was already up to date."""
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


def _parseMarkdownMetaDoc(normalized: str, sourceDesc: str = "<markdown>") -> kdl.Document:
    """Parse and merge all KDL metadata fences from a normalized Markdown document."""
    _, metaBlocks = findMetadataFences(normalized)
    return mergeDocuments(*(parseKdlDocument(block.body, sourceDesc) for block in metaBlocks))


def _isKdlUnpublished(rawText: str, sourceDesc: str = "<kdl>") -> bool:
    """Return True if a KDL metadata file explicitly declares published false."""
    doc = parseKdlDocument(rawText, sourceDesc=sourceDesc)
    return not getBoolField(doc, "published", default=True, sourceDesc=sourceDesc)


def _isMarkdownUnpublished(normalized: str, sourceDesc: str = "<markdown>") -> bool:
    """Return True if a normalized Markdown document explicitly declares published false."""
    merged = _parseMarkdownMetaDoc(normalized, sourceDesc=sourceDesc)
    return not getBoolField(merged, "published", default=True, sourceDesc=sourceDesc)


def _docEffectiveCharacters(doc: kdl.Document, sourceDesc: str) -> t.MetadataList:
    """Return explicit and relationship-implied characters from a parsed KDL metadata document."""
    explicit = getListField(doc, "characters", "character", sourceDesc=sourceDesc)
    relationships = getListField(doc, "relationships", "relationship", "ships", sourceDesc=sourceDesc)
    implied = extractRelationshipCharacters(relationships)
    seen: set[str] = set()
    merged: list[str] = []
    for name in (*explicit, *implied):
        lowered = name.lower()
        if lowered not in seen:
            seen.add(lowered)
            merged.append(name)
    return tuple(merged)


def _missingImpliedCharacters(
    doc: kdl.Document,
    sourceDesc: str,
    *,
    existingCharacters: t.Iterable[str] = (),
) -> t.MetadataList:
    """Return relationship-implied characters not already present in doc or existingCharacters."""
    relationships = getListField(doc, "relationships", "relationship", "ships", sourceDesc=sourceDesc)
    if not relationships:
        return ()
    implied = extractRelationshipCharacters(relationships)
    if not implied:
        return ()
    present = getListField(doc, "characters", "character", sourceDesc=sourceDesc)
    seen = {name.lower() for name in (*existingCharacters, *present)}
    return tuple(name for name in implied if name.lower() not in seen)


def _splitKdlLineComment(rest: str) -> tuple[str, str] | None:
    """Split a single-line KDL argument tail into (argsPart, trailingComment), or None if complex/multiline."""
    inQuotes = False
    escaped = False
    idx = 0
    length = len(rest)

    while idx < length:
        ch = rest[idx]
        if inQuotes:
            if escaped:
                escaped = False
            elif ch == "\\":
                escaped = True
            elif ch == '"':
                inQuotes = False
            idx += 1
            continue

        if ch == '"':
            inQuotes = True
            idx += 1
            continue
        if ch == "/" and idx + 1 < length:
            nxt = rest[idx + 1]
            if nxt == "/":
                codePart = rest[:idx].rstrip(" \t")
                commentPart = rest[len(codePart) :]
                return (codePart, commentPart)
            if nxt in {"*", "-"}:
                return None
        if ch in {";", "{", "}", "\\", "#"}:
            return None
        idx += 1

    if inQuotes:
        return None
    return (rest.rstrip(" \t"), "")


def _appendKdlLines(body: str, lines: t.Sequence[str]) -> str:
    """Append one or more KDL lines to a metadata block body, ensuring newline boundaries."""
    addition = "\n".join(lines) + "\n"
    if body and not body.endswith("\n"):
        return body + "\n" + addition
    return body + addition


def _insertIntoKdlChildBlock(
    body: str,
    openLineMatch: re.Match[str],
    missing: t.MetadataList,
) -> str | None:
    """Insert `- "..."` child entries before the closing `}` of a simple multiline KDL child block."""
    rest = openLineMatch.group("rest") or ""
    if not rest.lstrip().startswith("{"):
        return None
    split = _splitKdlLineComment(rest.replace("{", "", 1))
    if split is None or split[0].strip():
        return None

    lines = body[openLineMatch.end() :].splitlines(keepends=True)
    offset = openLineMatch.end()
    childIndent = openLineMatch.group("indent") + "    "
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("}"):
            if stripped != "}" and not stripped[1:].lstrip().startswith("//"):
                return None
            inserted = "".join(f"{childIndent}- {formatKdlString(name)}\n" for name in missing)
            prefix = body[:offset]
            if prefix and not prefix.endswith("\n"):
                prefix += "\n"
            return prefix + inserted + body[offset:]
        if "{" in stripped or "}" in stripped:
            return None
        if stripped and not stripped.startswith(("//", "/-", "/*")):
            leadingWs = line[: len(line) - len(line.lstrip(" \t"))]
            if leadingWs:
                childIndent = leadingWs
        offset += len(line)
    return None


def _updateKdlBlockCharacters(
    body: str,
    doc: kdl.Document,
    missing: t.MetadataList,
    sourceDesc: str,
) -> tuple[str, bool]:
    """Insert or append missing character tags into a KDL metadata block body."""
    if not missing:
        return (body, False)

    charNodes = findNodes(doc, "characters", "character")
    lineMatches = list(_KDL_CHARACTERS_LINE_RE.finditer(body))

    if len(charNodes) == 1 and len(lineMatches) == 1:
        node = charNodes[0]
        lineMatch = lineMatches[0]
        indent = lineMatch.group("indent")
        key = lineMatch.group("key")
        rest = lineMatch.group("rest") or ""

        if node.nodes and not node.args and not node.props:
            updatedBlock = _insertIntoKdlChildBlock(body, lineMatch, missing)
            if updatedBlock is not None:
                return (updatedBlock, updatedBlock != body)
        elif not node.nodes and not node.props:
            split = _splitKdlLineComment(rest)
            if split is not None:
                _, commentPart = split
                existing = getListField(kdl.Document([node]), "characters", "character", sourceDesc=sourceDesc)
                if key.lower() == "character" and len(existing) == 1:
                    extraLines = "".join(f"\n{indent}{key} {formatKdlString(name)}" for name in missing)
                    newBody = body[: lineMatch.end()] + extraLines + body[lineMatch.end() :]
                    return (newBody, newBody != body)
                combined = (*existing, *missing)
                formatted = " ".join(formatKdlString(name) for name in combined)
                replacement = f"{indent}{key} {formatted}{commentPart}"
                newBody = body[: lineMatch.start()] + replacement + body[lineMatch.end() :]
                return (newBody, newBody != body)

    if (
        charNodes
        and len(charNodes) == len(lineMatches)
        and all(
            not node.nodes and not node.props and len(node.args) == 1 and lineMatch.group("key").lower() == "character"
            for node, lineMatch in zip(charNodes, lineMatches, strict=True)
        )
    ):
        lastMatch = lineMatches[-1]
        indent = lastMatch.group("indent")
        key = lastMatch.group("key")
        extraLines = "".join(f"\n{indent}{key} {formatKdlString(name)}" for name in missing)
        newBody = body[: lastMatch.end()] + extraLines + body[lastMatch.end() :]
        return (newBody, newBody != body)

    line = formatKdlListLine("characters", missing)
    assert line is not None
    newBody = _appendKdlLines(body, (line,))
    return (newBody, newBody != body)


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

    return (_appendKdlLines(body, (f"date {dateLit}",)), True)


def _stampKdlTextDetailed(
    rawText: str,
    *,
    existingCharacters: t.Iterable[str] = (),
    sourceDesc: str = "<kdl>",
) -> tuple[str, t.MetadataList]:
    """Insert missing relationship-implied characters into a standalone KDL metadata file."""
    lineEnding = _detectLineEnding(rawText)
    normalized = normalizeLineEndings(rawText)
    doc = parseKdlDocument(normalized, sourceDesc=sourceDesc)
    missing = _missingImpliedCharacters(doc, sourceDesc, existingCharacters=existingCharacters)
    if not missing:
        return (rawText, ())

    newBody, changed = _updateKdlBlockCharacters(normalized, doc, missing, sourceDesc=sourceDesc)
    if not changed:
        return (rawText, ())
    updated = newBody.replace("\n", lineEnding)
    return (updated, missing if updated != rawText else ())


def stampKdlText(
    rawText: str,
    *,
    existingCharacters: t.Iterable[str] = (),
    sourceDesc: str = "<kdl>",
) -> tuple[str, bool]:
    """Insert missing relationship-implied `characters` into a standalone KDL metadata document.

    Returns `(updatedText, changed)`. Preserves original line endings and comments untouched.
    """
    updated, added = _stampKdlTextDetailed(rawText, existingCharacters=existingCharacters, sourceDesc=sourceDesc)
    return (updated, bool(added))


def _stampMarkdownTextDetailed(
    rawText: str,
    dateValue: str,
    *,
    force: bool = False,
    stampDate: bool = True,
    stampCharacters: bool = True,
    existingCharacters: t.Iterable[str] = (),
    sourceDesc: str = "<markdown>",
) -> tuple[str, bool, t.MetadataList]:
    """Stamp missing implied characters and/or `date` into a Markdown document's KDL metadata block."""
    lineEnding = _detectLineEnding(rawText)
    normalized = normalizeLineEndings(rawText)
    addedCharacters: t.MetadataList = ()
    stampedDate = False

    if stampCharacters:
        _, metaBlocks = findMetadataFences(normalized)
        if metaBlocks:
            parsedBlocks = [(block, parseKdlDocument(block.body, sourceDesc)) for block in metaBlocks]
            merged = mergeDocuments(*(doc for _, doc in parsedBlocks))
            missing = _missingImpliedCharacters(merged, sourceDesc, existingCharacters=existingCharacters)
            if missing:
                targetBlock, targetDoc = parsedBlocks[0]
                foundCharBlock = False
                for block, doc in reversed(parsedBlocks):
                    if findNodes(doc, "characters", "character"):
                        targetBlock, targetDoc = block, doc
                        foundCharBlock = True
                        break
                if not foundCharBlock:
                    for block, doc in reversed(parsedBlocks):
                        if findNodes(doc, "relationships", "relationship", "ships"):
                            targetBlock, targetDoc = block, doc
                            break
                newBody, charChanged = _updateKdlBlockCharacters(targetBlock.body, targetDoc, missing, sourceDesc)
                if charChanged:
                    normalized = normalized[: targetBlock.bodyStart] + newBody + normalized[targetBlock.bodyEnd :]
                    addedCharacters = missing

    if stampDate:
        leading, metaBlocks = findMetadataFences(normalized)
        if metaBlocks:
            parsedBlocks = [(block, parseKdlDocument(block.body, sourceDesc)) for block in metaBlocks]
            merged = mergeDocuments(*(doc for _, doc in parsedBlocks))
            _, hasAnyDateValue = _hasActiveDateValue(merged)
            if not hasAnyDateValue or force:
                # Pick the last fence containing a date node (matching parser's last-wins semantics),
                # or the first metadata fence if none currently declares a date node.
                targetBlock, targetDoc = parsedBlocks[0]
                for block, doc in reversed(parsedBlocks):
                    hasNode, _ = _hasActiveDateValue(doc)
                    if hasNode:
                        targetBlock, targetDoc = block, doc
                        break

                newBody, dateChanged = _updateKdlBlockDate(targetBlock.body, targetDoc, dateValue, force=force)
                if dateChanged:
                    normalized = normalized[: targetBlock.bodyStart] + newBody + normalized[targetBlock.bodyEnd :]
                    stampedDate = True
        else:
            # No metadata fence exists yet: insert one after a leading '# Title', or at the top.
            dateLit = formatKdlString(dateValue)
            metaBlock = f"```metadata\ndate {dateLit}\n```"
            if leading.title:
                prefix = normalized[: leading.contentStart].rstrip("\n")
                rest = normalized[leading.contentStart :].lstrip("\n")
                normalized = f"{prefix}\n\n{metaBlock}\n\n{rest}" if rest else f"{prefix}\n\n{metaBlock}\n"
            else:
                rest = normalized.lstrip("\n")
                normalized = f"{metaBlock}\n\n{rest}" if rest else f"{metaBlock}\n"
            stampedDate = True

    if not stampedDate and not addedCharacters:
        return (rawText, False, ())

    updated = normalized.replace("\n", lineEnding)
    if updated == rawText:
        return (rawText, False, ())
    return (updated, stampedDate, addedCharacters)


def stampDocumentText(
    rawText: str,
    dateValue: str,
    *,
    force: bool = False,
    stampCharacters: bool = True,
    existingCharacters: t.Iterable[str] = (),
    sourceDesc: str = "<markdown>",
) -> tuple[str, bool]:
    """Insert or update `date "..."` and implied `characters` inside a Markdown KDL metadata block.

    Returns `(updatedText, changed)`. Preserves original line endings and prose untouched.
    """
    updated, stampedDate, addedCharacters = _stampMarkdownTextDetailed(
        rawText,
        dateValue,
        force=force,
        stampDate=True,
        stampCharacters=stampCharacters,
        existingCharacters=existingCharacters,
        sourceDesc=sourceDesc,
    )
    return (updated, stampedDate or bool(addedCharacters))


def _loadSiblingWorkCharacters(storyDir: Path) -> t.MetadataList:
    """Return effective characters from a directory's whole-work KDL file, if present."""
    for name in _STORY_KDL_NAMES:
        candidate = storyDir / name
        if candidate.is_file():
            doc = parseKdlDocument(readUtf8File(candidate, label="story KDL"), sourceDesc=str(candidate))
            return _docEffectiveCharacters(doc, str(candidate))
    return ()


def _collectStoryTargets(storyDir: Path, *, stampCharacters: bool) -> list[_StampTarget]:
    """Collect stampable story/chapter files from a story directory along with metadata context."""
    storyKdlPath, indexMdPath, chapterFiles = discoverStoryFiles(storyDir)

    storyPublished = True
    workCharacters: t.MetadataList = ()
    if storyKdlPath is not None:
        storyKdlDoc = parseKdlDocument(readUtf8File(storyKdlPath, label="story KDL"), sourceDesc=str(storyKdlPath))
        storyPublished = getBoolField(storyKdlDoc, "published", default=True, sourceDesc=str(storyKdlPath))
        workCharacters = _docEffectiveCharacters(storyKdlDoc, str(storyKdlPath))
    elif indexMdPath is not None:
        indexMeta = _parseMarkdownMetaDoc(
            normalizeLineEndings(readUtf8File(indexMdPath, label="story file")),
            sourceDesc=str(indexMdPath),
        )
        storyPublished = getBoolField(indexMeta, "published", default=True, sourceDesc=str(indexMdPath))
        workCharacters = _docEffectiveCharacters(indexMeta, str(indexMdPath))

    targets: list[_StampTarget] = []
    if chapterFiles:
        if stampCharacters:
            if storyKdlPath is not None:
                targets.append(
                    _StampTarget(
                        path=storyKdlPath,
                        storyPublished=storyPublished,
                        stampDate=False,
                        stampCharacters=True,
                        reportIfUnchanged=False,
                    ),
                )
            elif indexMdPath is not None:
                targets.append(
                    _StampTarget(
                        path=indexMdPath,
                        storyPublished=storyPublished,
                        stampDate=False,
                        stampCharacters=True,
                        reportIfUnchanged=False,
                    ),
                )
        for chapterPath in chapterFiles:
            targets.append(
                _StampTarget(
                    path=chapterPath,
                    storyPublished=storyPublished,
                    stampDate=True,
                    stampCharacters=stampCharacters,
                    reportIfUnchanged=True,
                    existingCharacters=workCharacters,
                ),
            )
        return targets

    assert indexMdPath is not None
    if storyKdlPath is not None:
        indexMeta = _parseMarkdownMetaDoc(
            normalizeLineEndings(readUtf8File(indexMdPath, label="story file")),
            sourceDesc=str(indexMdPath),
        )
        indexExplicitChars = getListField(indexMeta, "characters", "character", sourceDesc=str(indexMdPath))
        effectivePublished = getBoolField(indexMeta, "published", default=storyPublished, sourceDesc=str(indexMdPath))
        if stampCharacters:
            targets.append(
                _StampTarget(
                    path=storyKdlPath,
                    storyPublished=effectivePublished,
                    stampDate=False,
                    stampCharacters=True,
                    reportIfUnchanged=False,
                    existingCharacters=indexExplicitChars,
                ),
            )
        targets.append(
            _StampTarget(
                path=indexMdPath,
                storyPublished=storyPublished,
                stampDate=True,
                stampCharacters=stampCharacters,
                reportIfUnchanged=True,
                existingCharacters=workCharacters,
            ),
        )
        return targets

    targets.append(
        _StampTarget(
            path=indexMdPath,
            storyPublished=storyPublished,
            stampDate=True,
            stampCharacters=stampCharacters,
            reportIfUnchanged=True,
        ),
    )
    return targets


def _resolveTargets(
    storiesDir: Path,
    targets: t.Sequence[Path],
    *,
    stampCharacters: bool,
) -> list[_StampTarget]:
    """Resolve CLI target arguments (files, directories, or story slugs) into stamp targets."""
    if not targets:
        resolved: list[_StampTarget] = []
        for storyDir in discoverStoryDirs(storiesDir):
            resolved.extend(_collectStoryTargets(storyDir, stampCharacters=stampCharacters))
        return resolved

    resolvedTargets: list[_StampTarget] = []
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
            if candidate.suffix not in {".md", ".kdl"}:
                msg = f"Expected a Markdown (.md) or KDL (.kdl) file, got: {candidate}"
                raise StoryParseError(msg)
            key = candidate.resolve()
            if key not in seen:
                seen.add(key)
                isKdl = candidate.suffix == ".kdl"
                siblingWorkChars = () if isKdl else _loadSiblingWorkCharacters(candidate.parent)
                resolvedTargets.append(
                    _StampTarget(
                        path=candidate,
                        storyPublished=True,
                        stampDate=not isKdl,
                        stampCharacters=stampCharacters,
                        reportIfUnchanged=True,
                        existingCharacters=siblingWorkChars,
                    ),
                )
        elif candidate.is_dir():
            for item in _collectStoryTargets(candidate, stampCharacters=stampCharacters):
                key = item.path.resolve()
                if key not in seen:
                    seen.add(key)
                    resolvedTargets.append(item)
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
    stampDate: bool = True,
    stampCharacters: bool = True,
    existingCharacters: t.Iterable[str] = (),
) -> StampEntry:
    """Stamp `date "..."` and/or implied `characters` into a single Markdown or KDL file."""
    sourceDesc = str(path)
    isKdl = path.suffix == ".kdl"
    rawText = readUtf8File(path, label="story KDL" if isKdl else "story file")
    effectiveStampDate = stampDate and not isKdl

    if isKdl:
        filePublished = storyPublished and not _isKdlUnpublished(rawText, sourceDesc=sourceDesc)
    else:
        filePublished = storyPublished and not _isMarkdownUnpublished(
            normalizeLineEndings(rawText),
            sourceDesc=sourceDesc,
        )

    if isKdl:
        if stampCharacters:
            updatedText, addedCharacters = _stampKdlTextDetailed(
                rawText,
                existingCharacters=existingCharacters,
                sourceDesc=sourceDesc,
            )
        else:
            updatedText, addedCharacters = (rawText, ())
        stampedDate = False
    else:
        updatedText, stampedDate, addedCharacters = _stampMarkdownTextDetailed(
            rawText,
            dateValue,
            force=force,
            stampDate=effectiveStampDate,
            stampCharacters=stampCharacters,
            existingCharacters=existingCharacters,
            sourceDesc=sourceDesc,
        )

    if not filePublished and not includeDrafts:
        if not effectiveStampDate and not addedCharacters:
            return StampEntry(path=path, action="skipped_existing", dateValue=dateValue)
        return StampEntry(path=path, action="skipped_draft", dateValue=dateValue)

    if not stampedDate and not addedCharacters:
        return StampEntry(path=path, action="skipped_existing", dateValue=dateValue)

    if dryRun:
        return StampEntry(
            path=path,
            action="would_stamp",
            dateValue=dateValue,
            stampedDate=stampedDate,
            addedCharacters=addedCharacters,
        )

    path.write_text(updatedText, encoding="utf-8")
    return StampEntry(
        path=path,
        action="stamped",
        dateValue=dateValue,
        stampedDate=stampedDate,
        addedCharacters=addedCharacters,
    )


def stampStories(
    storiesDir: Path,
    dateValue: str,
    *,
    targets: t.Sequence[Path] = (),
    force: bool = False,
    includeDrafts: bool = False,
    dryRun: bool = False,
    stampCharacters: bool = True,
) -> StampResult:
    """Stamp `date` and/or implied `characters` into eligible story/chapter files."""
    validatedDate = validateDateString(dateValue)
    targetFiles = _resolveTargets(storiesDir, targets, stampCharacters=stampCharacters)
    entries: list[StampEntry] = []
    for target in targetFiles:
        entry = stampFile(
            target.path,
            validatedDate,
            force=force,
            includeDrafts=includeDrafts,
            dryRun=dryRun,
            storyPublished=target.storyPublished,
            stampDate=target.stampDate,
            stampCharacters=target.stampCharacters,
            existingCharacters=target.existingCharacters,
        )
        if not target.reportIfUnchanged and entry.action == "skipped_existing":
            continue
        entries.append(entry)
    return StampResult(entries=tuple(entries), dryRun=dryRun)
