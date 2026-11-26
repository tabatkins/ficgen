"""Source Markdown and KDL parsing, story loading, and taxonomy indexing."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path

import kdl

from . import t
from .kdlMeta import (
    StoryParseError,
    coerceStringValue,
    findNodes,
    getBoolField,
    getDateField,
    getIntField,
    getListField,
    getStringField,
    isKdlNull,
    mergeDocuments,
    normalizeKey,
    parseKdlDocument,
    readUtf8File,
)
from .markdownExt import MarkdownRenderer, countWords, makeExcerpt, stripHtmlTags
from .models import (
    RESERVED_OUTPUT_SLUGS,
    TAXONOMY_SPECS,
    Archive,
    Chapter,
    FeedEntry,
    SiteConfig,
    Story,
    TaxonomyBucket,
    TaxonomyItem,
    buildSlugMap,
)

_LOG: t.Final[logging.Logger] = logging.getLogger(__name__)
_TITLE_LINE_RE: t.Final[re.Pattern[str]] = re.compile(r"^#\s+(.+?)\s*$")
_OPEN_FENCE_RE: t.Final[re.Pattern[str]] = re.compile(r"^(?P<marker>`{3,}|~{3,})(?P<info>.*)$")
_METADATA_INFO_RE: t.Final[re.Pattern[str]] = re.compile(r"^metadata(?:[ \t]+kdl)?$", re.IGNORECASE)
_SPECIAL_TAG_RE: t.Final[re.Pattern[str]] = re.compile(
    r"(?is)<(notes|startnotes|beginnotes|endnotes|summary)>(.*?)</\1>",
)
_CHAPTER_FILE_RE: t.Final[re.Pattern[str]] = re.compile(r"^ch(\d+)\.md$", re.IGNORECASE)
_NATURAL_SPLIT_RE: t.Final[re.Pattern[str]] = re.compile(r"(\d+)")
_STORY_KDL_NAMES: t.Final[tuple[str, ...]] = ("story.kdl", "meta.kdl", "index.kdl")

_NOTES_KEYS: t.Final[tuple[str, ...]] = ("notes", "startnotes", "beginnotes")
_DATE_KEYS: t.Final[tuple[str, ...]] = ("date", "published-date")
_UPDATED_KEYS: t.Final[tuple[str, ...]] = ("updated", "updated-date")
_TAXONOMY_FIELD_KEYS: t.Final[dict[t.TaxonomyKind, tuple[str, ...]]] = {
    "fandoms": ("fandoms", "fandom"),
    "categories": ("relationship-categories", "categories", "category"),
    "relationships": ("relationships", "relationship", "ships"),
    "characters": ("characters", "character"),
    "tags": ("tags", "tag", "additional-tags"),
}


class LeadingTitle(t.NamedTuple):
    """Leading `# Heading` extracted from a normalized (LF) Markdown document."""

    title: str
    contentStart: int


class FencedBlock(t.NamedTuple):
    """A CommonMark fenced code block located inside a normalized (LF) Markdown document."""

    info: str
    body: str
    start: int
    end: int
    bodyStart: int
    bodyEnd: int
    isLeading: bool


@dataclass(slots=True)
class ParsedDocument:
    """Intermediate parsed representation of a single Markdown source file."""

    title: str
    metadata: kdl.Document
    summaryMd: str
    notesMd: str
    endnotesMd: str
    bodyMd: str
    cssBlocks: tuple[str, ...] = ()


@dataclass(slots=True)
class _StoryHeader:
    """Whole-work metadata loaded for a multi-chapter story before loading individual chapters."""

    metaSourcePath: Path
    sourceDesc: str
    metadata: kdl.Document
    title: str
    summaryMd: str
    notesMd: str
    endnotesMd: str
    indexCssBlocks: tuple[str, ...] = ()
    preloadedFirstChapter: ParsedDocument | None = None


def normalizeLineEndings(rawText: str) -> str:
    """Normalize CRLF and CR line endings to LF."""
    return rawText.replace("\r\n", "\n").replace("\r", "\n")


def splitLeadingTitle(normalized: str) -> LeadingTitle:
    """Extract an optional leading `# Title` line after any leading blank lines."""
    pos = 0
    for line in normalized.splitlines(keepends=True):
        lineEnd = pos + len(line)
        stripped = line.rstrip("\n")
        if not stripped.strip():
            pos = lineEnd
            continue
        titleMatch = _TITLE_LINE_RE.match(stripped)
        if titleMatch is not None:
            return LeadingTitle(title=titleMatch.group(1).strip(), contentStart=lineEnd)
        return LeadingTitle(title="", contentStart=pos)
    return LeadingTitle(title="", contentStart=len(normalized))


def scanFencedBlocks(normalized: str, *, startPos: int = 0) -> list[FencedBlock]:
    """Scan CommonMark backtick/tilde fenced code blocks starting at startPos."""
    blocks: list[FencedBlock] = []
    pos = startPos
    seenNonEmpty = False

    lines = normalized[startPos:].splitlines(keepends=True)
    idx = 0
    while idx < len(lines):
        line = lines[idx]
        lineEnd = pos + len(line)
        content = line.rstrip("\n")

        openMatch = _OPEN_FENCE_RE.match(content)
        if openMatch is not None:
            marker = openMatch.group("marker")
            info = openMatch.group("info").strip()
            fenceChar = marker[0]
            # Per CommonMark, backtick fence info strings cannot contain backticks.
            if fenceChar == "`" and "`" in info:
                seenNonEmpty = True
                pos = lineEnd
                idx += 1
                continue

            isLeading = not seenNonEmpty
            seenNonEmpty = True
            blockStart = pos
            bodyStart = lineEnd
            fenceLen = len(marker)

            scanPos = lineEnd
            scanIdx = idx + 1
            closed = False
            while scanIdx < len(lines):
                scanLine = lines[scanIdx]
                scanLineEnd = scanPos + len(scanLine)
                trimmed = scanLine.rstrip("\n").rstrip(" \t")
                if len(trimmed) >= fenceLen and trimmed == fenceChar * len(trimmed):
                    blocks.append(
                        FencedBlock(
                            info=info,
                            body=normalized[bodyStart:scanPos],
                            start=blockStart,
                            end=scanLineEnd,
                            bodyStart=bodyStart,
                            bodyEnd=scanPos,
                            isLeading=isLeading,
                        ),
                    )
                    pos = scanLineEnd
                    idx = scanIdx + 1
                    closed = True
                    break
                scanPos = scanLineEnd
                scanIdx += 1

            if not closed:
                # Unclosed fence runs to EOF in CommonMark; stop scanning.
                break
            continue

        if content.strip():
            seenNonEmpty = True
        pos = lineEnd
        idx += 1

    return blocks


def isMetadataFence(block: FencedBlock) -> bool:
    """Return True if a fenced block holds ficgen KDL metadata."""
    if _METADATA_INFO_RE.match(block.info) is not None:
        return True
    return block.isLeading and block.info.lower() == "kdl"


def findMetadataFences(normalized: str) -> tuple[LeadingTitle, list[FencedBlock]]:
    """Locate leading title and all KDL metadata fences in a normalized Markdown document."""
    leading = splitLeadingTitle(normalized)
    blocks = scanFencedBlocks(normalized, startPos=leading.contentStart)
    metaBlocks = [block for block in blocks if isMetadataFence(block)]
    return (leading, metaBlocks)


def _extractSpecialFromSegment(
    segment: str,
    summaries: list[str],
    notes: list[str],
    endnotes: list[str],
) -> str:
    """Extract <summary>, <notes>, and <endnotes> tags from a prose segment outside code fences."""

    def _replaceTag(match: re.Match[str]) -> str:
        tag = match.group(1).lower()
        inner = match.group(2).strip()
        if inner:
            match tag:
                case "summary":
                    summaries.append(inner)
                case "notes" | "startnotes" | "beginnotes":
                    notes.append(inner)
                case "endnotes":
                    endnotes.append(inner)
        return ""

    return _SPECIAL_TAG_RE.sub(_replaceTag, segment)


def parseMarkdownDocument(rawText: str, sourceDesc: str = "<markdown>") -> ParsedDocument:
    """Parse title, KDL metadata blocks, inline CSS blocks, author's notes, summary, and body."""
    normalized = normalizeLineEndings(rawText)
    leading = splitLeadingTitle(normalized)
    blocks = scanFencedBlocks(normalized, startPos=leading.contentStart)

    metaDocs: list[kdl.Document] = []
    cssBlocks: list[str] = []
    summaries: list[str] = []
    notes: list[str] = []
    endnotes: list[str] = []
    proseChunks: list[str] = []

    cursor = leading.contentStart
    for block in blocks:
        if block.start > cursor:
            proseChunks.append(
                _extractSpecialFromSegment(normalized[cursor : block.start], summaries, notes, endnotes),
            )
        if isMetadataFence(block):
            metaDocs.append(parseKdlDocument(block.body, sourceDesc))
        elif block.info.lower() == "css":
            cssText = block.body.strip()
            if cssText:
                cssBlocks.append(cssText)
        else:
            proseChunks.append(normalized[block.start : block.end])
        cursor = block.end

    if cursor < len(normalized):
        proseChunks.append(_extractSpecialFromSegment(normalized[cursor:], summaries, notes, endnotes))

    metadata = mergeDocuments(*metaDocs)
    title = getStringField(metadata, "title", sourceDesc=sourceDesc) or leading.title

    metaSummary = getStringField(metadata, "summary", sourceDesc=sourceDesc)
    if metaSummary:
        summaries.insert(0, metaSummary)

    metaNotes = getStringField(metadata, *_NOTES_KEYS, sourceDesc=sourceDesc)
    if metaNotes:
        notes.insert(0, metaNotes)

    metaEndnotes = getStringField(metadata, "endnotes", sourceDesc=sourceDesc)
    if metaEndnotes:
        endnotes.insert(0, metaEndnotes)

    return ParsedDocument(
        title=title,
        metadata=metadata,
        summaryMd="\n\n".join(summaries).strip(),
        notesMd="\n\n".join(notes).strip(),
        endnotesMd="\n\n".join(endnotes).strip(),
        bodyMd="".join(proseChunks).strip(),
        cssBlocks=tuple(cssBlocks),
    )


def _extractTaxonomyFields(
    meta: kdl.Document,
    sourceDesc: str,
) -> dict[t.TaxonomyKind, t.MetadataList]:
    """Extract all five taxonomy tag lists from a KDL metadata document."""
    return {kind: getListField(meta, *keys, sourceDesc=sourceDesc) for kind, keys in _TAXONOMY_FIELD_KEYS.items()}


def _getNsfwField(doc: kdl.Document, sourceDesc: str) -> tuple[bool, str]:
    """Parse an optional `nsfw` KDL field (bool, string reason, or `reason="..."` prop)."""
    for node in reversed(findNodes(doc, "nsfw")):
        reason = ""
        for propName, propVal in node.props.items():
            if normalizeKey(propName) in {"reason", "label", "warning"}:
                reason = coerceStringValue(propVal, propName, sourceDesc)

        if not node.args:
            return (True, reason)

        first = node.args[0]
        if isKdlNull(first):
            continue
        if isinstance(first, bool):
            return (first, reason if first else "")
        if isinstance(first, kdl.Bool):
            return (first.value, reason if first.value else "")
        if isinstance(first, str):
            cleaned = first.strip()
            lowered = cleaned.lower()
            if lowered in {"true", "yes"}:
                return (True, reason)
            if lowered in {"false", "no"}:
                return (False, "")
            if cleaned:
                return (True, reason or cleaned)
            continue

        msg = f"Invalid value for KDL field {node.name!r} in {sourceDesc}: {first!r} (expected bool or reason string)"
        raise StoryParseError(msg)

    return (False, "")


def _mergeLists(*lists: t.MetadataList) -> t.MetadataList:
    """Merge multiple metadata lists while preserving order and case-insensitive uniqueness."""
    seen: set[str] = set()
    merged: list[str] = []
    for entries in lists:
        for item in entries:
            lowered = item.lower()
            if lowered not in seen:
                seen.add(lowered)
                merged.append(item)
    return tuple(merged)


def _naturalSortKey(path: Path) -> list[int | str]:
    """Return a sort key that orders ch2.md before ch10.md."""
    parts = _NATURAL_SPLIT_RE.split(path.stem.lower())
    return [int(part) if part.isdigit() else part for part in parts]


def _humanizeSlug(slug: str) -> str:
    """Turn a directory slug like 'midnight-rain' into a fallback display title."""
    return " ".join(word.capitalize() for word in slug.replace("_", "-").split("-") if word)


def _readOptionalCssFile(cssPath: Path) -> str | None:
    """Read a UTF-8 CSS file if it exists and is non-empty."""
    if not cssPath.is_file():
        return None
    text = readUtf8File(cssPath, label="stylesheet")
    return text if text.strip() else None


def _loadChapter(
    chapterPath: Path,
    fallbackNumber: int,
    storyTitle: str,
    renderer: MarkdownRenderer,
    workCssPath: Path,
    *,
    preloadedDoc: ParsedDocument | None = None,
) -> Chapter:
    """Load and render a single chapter Markdown file."""
    sourceDesc = str(chapterPath)
    parsed = (
        preloadedDoc
        if preloadedDoc is not None
        else parseMarkdownDocument(readUtf8File(chapterPath, label="chapter file"), sourceDesc=sourceDesc)
    )
    meta = parsed.metadata

    filenameMatch = _CHAPTER_FILE_RE.match(chapterPath.name)
    inferredNum = int(filenameMatch.group(1)) if filenameMatch is not None else fallbackNumber
    number = getIntField(meta, "chapter", sourceDesc=sourceDesc) or inferredNum

    chapterTitle = parsed.title
    if chapterTitle.lower() == storyTitle.lower():
        chapterTitle = ""

    published = getBoolField(meta, "published", default=True, sourceDesc=sourceDesc)
    nsfw, nsfwReason = _getNsfwField(meta, sourceDesc)
    date, hasExplicitDate = getDateField(meta, chapterPath, *_DATE_KEYS)

    summaryHtml = renderer.render(parsed.summaryMd)
    notesHtml = renderer.render(parsed.notesMd)
    endnotesHtml = renderer.render(parsed.endnotesMd)
    bodyHtml, hasNsfwSections = renderer.renderBody(parsed.bodyMd)
    wordCount = countWords(bodyHtml)
    summaryText = stripHtmlTags(summaryHtml) if summaryHtml else makeExcerpt(bodyHtml)

    chapterCssPath = chapterPath.with_suffix(".css")
    chapterCssFile = _readOptionalCssFile(chapterCssPath) if chapterCssPath != workCssPath else None
    chapterCssBlocks = tuple(block for block in (chapterCssFile, *parsed.cssBlocks) if block)
    tax = _extractTaxonomyFields(meta, sourceDesc)

    return Chapter(
        number=number,
        slug=f"ch{number}",
        title=chapterTitle,
        sourcePath=chapterPath,
        published=published,
        date=date,
        hasExplicitDate=hasExplicitDate,
        summaryHtml=summaryHtml,
        summaryText=summaryText,
        notesHtml=notesHtml,
        endnotesHtml=endnotesHtml,
        bodyHtml=bodyHtml,
        wordCount=wordCount,
        extraFandoms=tax["fandoms"],
        extraCategories=tax["categories"],
        extraRelationships=tax["relationships"],
        extraCharacters=tax["characters"],
        extraTags=tax["tags"],
        cssBlocks=chapterCssBlocks,
        nsfw=nsfw,
        nsfwReason=nsfwReason,
        hasNsfwSections=hasNsfwSections,
    )


def discoverStoryDirs(storiesDir: Path) -> list[Path]:
    """Return sorted story subdirectories inside storiesDir."""
    if not storiesDir.is_dir():
        msg = f"Stories directory does not exist: {storiesDir}"
        raise StoryParseError(msg)
    return sorted(
        (
            entry
            for entry in storiesDir.iterdir()
            if entry.is_dir() and not entry.name.startswith(".") and not entry.name.startswith("_")
        ),
        key=lambda path: path.name.lower(),
    )


def discoverStoryFiles(storyDir: Path) -> tuple[Path | None, Path | None, list[Path]]:
    """Return (storyKdlPath, indexMdPath, sortedChapterFiles) for a story directory."""
    foundKdl = [storyDir / name for name in _STORY_KDL_NAMES if (storyDir / name).is_file()]
    if len(foundKdl) > 1:
        names = ", ".join(path.name for path in foundKdl)
        msg = f"Story directory {storyDir} contains multiple whole-work KDL files: {names}"
        raise StoryParseError(msg)
    storyKdlPath = foundKdl[0] if foundKdl else None

    indexCandidate = storyDir / "index.md"
    indexMdPath = indexCandidate if indexCandidate.is_file() else None
    chapterFiles = sorted(
        (path for path in storyDir.iterdir() if path.is_file() and path.suffix == ".md" and path.name != "index.md"),
        key=_naturalSortKey,
    )
    if indexMdPath is None and not chapterFiles:
        msg = f"Story directory {storyDir} contains no Markdown files."
        raise StoryParseError(msg)
    return (storyKdlPath, indexMdPath, chapterFiles)


def _loadSingleDocStory(
    storyDir: Path,
    *,
    storyKdlPath: Path | None,
    indexMdPath: Path,
    workCssFile: str | None,
    config: SiteConfig,
    renderer: MarkdownRenderer,
) -> Story | None:
    """Load a single-document story from index.md (plus optional story.kdl)."""
    slug = storyDir.name
    sourceDesc = str(indexMdPath)
    indexDoc = parseMarkdownDocument(readUtf8File(indexMdPath, label="story file"), sourceDesc=sourceDesc)
    if storyKdlPath is not None:
        kdlDoc = parseKdlDocument(readUtf8File(storyKdlPath, label="story KDL"), sourceDesc=str(storyKdlPath))
        meta = mergeDocuments(kdlDoc, indexDoc.metadata)
    else:
        meta = indexDoc.metadata

    storyPublished = getBoolField(meta, "published", default=True, sourceDesc=sourceDesc)
    if not storyPublished and not config.includeDrafts:
        return None

    title = getStringField(meta, "title", sourceDesc=sourceDesc) or indexDoc.title or _humanizeSlug(slug)
    author = getStringField(meta, "author", sourceDesc=sourceDesc) or config.author
    complete = getBoolField(meta, "complete", "status", default=True, sourceDesc=sourceDesc)
    nsfw, nsfwReason = _getNsfwField(meta, sourceDesc)

    date, hasExplicitDate = getDateField(meta, indexMdPath, *_DATE_KEYS)
    updated, hasUpdated = getDateField(meta, indexMdPath, *_UPDATED_KEYS)
    if not hasUpdated:
        updated = date

    # indexDoc already merged inline metadata from index.md; fall back to story.kdl if present.
    summaryMd = indexDoc.summaryMd or getStringField(meta, "summary", sourceDesc=sourceDesc)
    notesMd = indexDoc.notesMd or getStringField(meta, *_NOTES_KEYS, sourceDesc=sourceDesc)
    endnotesMd = indexDoc.endnotesMd or getStringField(meta, "endnotes", sourceDesc=sourceDesc)

    summaryHtml = renderer.render(summaryMd)
    notesHtml = renderer.render(notesMd)
    endnotesHtml = renderer.render(endnotesMd)
    bodyHtml, hasNsfwSections = renderer.renderBody(indexDoc.bodyMd)
    wordCount = countWords(bodyHtml)
    summaryText = stripHtmlTags(summaryHtml) if summaryHtml else makeExcerpt(bodyHtml)
    workCssBlocks = tuple(block for block in (workCssFile, *indexDoc.cssBlocks) if block)
    tax = _extractTaxonomyFields(meta, sourceDesc)

    return Story(
        slug=slug,
        title=title,
        author=author,
        isMultiChapter=False,
        published=storyPublished,
        complete=complete,
        plannedChapters=1,
        date=date,
        updated=updated,
        hasExplicitDate=hasExplicitDate,
        rating=getStringField(meta, "rating", sourceDesc=sourceDesc),
        warnings=getListField(meta, "warnings", "archive-warnings", sourceDesc=sourceDesc),
        fandoms=tax["fandoms"],
        categories=tax["categories"],
        relationships=tax["relationships"],
        characters=tax["characters"],
        tags=tax["tags"],
        summaryHtml=summaryHtml,
        summaryText=summaryText,
        notesHtml=notesHtml,
        endnotesHtml=endnotesHtml,
        bodyHtml=bodyHtml,
        wordCount=wordCount,
        chapters=(),
        sourceDir=storyDir,
        cssBlocks=workCssBlocks,
        nsfw=nsfw,
        nsfwReason=nsfwReason,
        hasNsfwSections=hasNsfwSections,
    )


def _loadMultiChapterHeader(
    slug: str,
    storyKdlPath: Path | None,
    indexMdPath: Path | None,
    chapterFiles: list[Path],
) -> _StoryHeader:
    """Resolve whole-work metadata from story.kdl, falling back to index.md or the first chapter."""
    if storyKdlPath is not None:
        sourceDesc = str(storyKdlPath)
        storyMeta = parseKdlDocument(readUtf8File(storyKdlPath, label="story KDL"), sourceDesc=sourceDesc)
        return _StoryHeader(
            metaSourcePath=storyKdlPath,
            sourceDesc=sourceDesc,
            metadata=storyMeta,
            title=getStringField(storyMeta, "title", sourceDesc=sourceDesc) or _humanizeSlug(slug),
            summaryMd=getStringField(storyMeta, "summary", sourceDesc=sourceDesc),
            notesMd=getStringField(storyMeta, *_NOTES_KEYS, sourceDesc=sourceDesc),
            endnotesMd=getStringField(storyMeta, "endnotes", sourceDesc=sourceDesc),
        )

    if indexMdPath is not None:
        sourceDesc = str(indexMdPath)
        indexDoc = parseMarkdownDocument(readUtf8File(indexMdPath, label="story index"), sourceDesc=sourceDesc)
        return _StoryHeader(
            metaSourcePath=indexMdPath,
            sourceDesc=sourceDesc,
            metadata=indexDoc.metadata,
            title=indexDoc.title or _humanizeSlug(slug),
            summaryMd=indexDoc.summaryMd or indexDoc.bodyMd,
            notesMd=indexDoc.notesMd,
            endnotesMd=indexDoc.endnotesMd,
            indexCssBlocks=indexDoc.cssBlocks,
        )

    firstChapterPath = chapterFiles[0]
    sourceDesc = str(firstChapterPath)
    firstDoc = parseMarkdownDocument(readUtf8File(firstChapterPath, label="chapter file"), sourceDesc=sourceDesc)
    return _StoryHeader(
        metaSourcePath=firstChapterPath,
        sourceDesc=sourceDesc,
        metadata=firstDoc.metadata,
        title=firstDoc.title or _humanizeSlug(slug),
        summaryMd="",
        notesMd="",
        endnotesMd="",
        preloadedFirstChapter=firstDoc,
    )


def _loadMultiChapterStory(
    storyDir: Path,
    *,
    storyKdlPath: Path | None,
    indexMdPath: Path | None,
    chapterFiles: list[Path],
    workCssPath: Path,
    workCssFile: str | None,
    config: SiteConfig,
    renderer: MarkdownRenderer,
) -> Story | None:
    """Load a multi-chapter story and its included chapters."""
    slug = storyDir.name
    header = _loadMultiChapterHeader(slug, storyKdlPath, indexMdPath, chapterFiles)
    storyMeta = header.metadata
    sourceDesc = header.sourceDesc

    storyPublished = getBoolField(storyMeta, "published", default=True, sourceDesc=sourceDesc)
    if not storyPublished and not config.includeDrafts:
        return None

    loadedChapters = [
        _loadChapter(
            chapterPath,
            idx + 1,
            header.title,
            renderer,
            workCssPath,
            preloadedDoc=header.preloadedFirstChapter if idx == 0 else None,
        )
        for idx, chapterPath in enumerate(chapterFiles)
    ]
    loadedChapters.sort(key=lambda chapter: (chapter.number, chapter.sourcePath.name))

    seenNumbers: set[int] = set()
    for chapter in loadedChapters:
        if chapter.number in seenNumbers:
            msg = f"Duplicate chapter number {chapter.number} in {storyDir}"
            raise StoryParseError(msg)
        seenNumbers.add(chapter.number)

    includedChapters = tuple(chapter for chapter in loadedChapters if chapter.published or config.includeDrafts)
    if not includedChapters:
        return None

    author = getStringField(storyMeta, "author", sourceDesc=sourceDesc) or config.author
    complete = getBoolField(storyMeta, "complete", "status", default=False, sourceDesc=sourceDesc)
    plannedChapters = getIntField(storyMeta, "planned-chapters", "total-chapters", sourceDesc=sourceDesc)
    # Only apply work-level NSFW when whole-work metadata comes from story.kdl/index.md (not chapter 1 fallback).
    if header.preloadedFirstChapter is None:
        nsfw, nsfwReason = _getNsfwField(storyMeta, sourceDesc)
    else:
        nsfw, nsfwReason = (False, "")

    workTax = _extractTaxonomyFields(storyMeta, sourceDesc)
    fandoms = _mergeLists(workTax["fandoms"], *(ch.extraFandoms for ch in includedChapters))
    categories = _mergeLists(workTax["categories"], *(ch.extraCategories for ch in includedChapters))
    relationships = _mergeLists(workTax["relationships"], *(ch.extraRelationships for ch in includedChapters))
    characters = _mergeLists(workTax["characters"], *(ch.extraCharacters for ch in includedChapters))
    tags = _mergeLists(workTax["tags"], *(ch.extraTags for ch in includedChapters))

    storyDate, hasStoryDate = getDateField(storyMeta, header.metaSourcePath, *_DATE_KEYS)
    explicitChapterDates = [chapter.date for chapter in includedChapters if chapter.hasExplicitDate]
    if not hasStoryDate and explicitChapterDates:
        storyDate = min(explicitChapterDates)
        hasStoryDate = True

    updated, hasUpdated = getDateField(storyMeta, header.metaSourcePath, *_UPDATED_KEYS)
    if not hasUpdated:
        chapterDates = explicitChapterDates if explicitChapterDates else [chapter.date for chapter in includedChapters]
        updated = max([storyDate, *chapterDates])

    summaryHtml = renderer.render(header.summaryMd)
    notesHtml = renderer.render(header.notesMd)
    endnotesHtml = renderer.render(header.endnotesMd)
    summaryText = stripHtmlTags(summaryHtml) if summaryHtml else includedChapters[0].summaryText
    totalWords = sum(chapter.wordCount for chapter in includedChapters)
    workCssBlocks = tuple(block for block in (workCssFile, *header.indexCssBlocks) if block)

    return Story(
        slug=slug,
        title=header.title,
        author=author,
        isMultiChapter=True,
        published=storyPublished,
        complete=complete,
        plannedChapters=plannedChapters,
        date=storyDate,
        updated=updated,
        hasExplicitDate=hasStoryDate,
        rating=getStringField(storyMeta, "rating", sourceDesc=sourceDesc),
        warnings=getListField(storyMeta, "warnings", "archive-warnings", sourceDesc=sourceDesc),
        fandoms=fandoms,
        categories=categories,
        relationships=relationships,
        characters=characters,
        tags=tags,
        summaryHtml=summaryHtml,
        summaryText=summaryText,
        notesHtml=notesHtml,
        endnotesHtml=endnotesHtml,
        bodyHtml="",
        wordCount=totalWords,
        chapters=includedChapters,
        sourceDir=storyDir,
        cssBlocks=workCssBlocks,
        nsfw=nsfw,
        nsfwReason=nsfwReason,
    )


def loadStory(storyDir: Path, config: SiteConfig, renderer: MarkdownRenderer) -> Story | None:
    """Load a story directory into a Story model, or return None if unpublished."""
    storyKdlPath, indexMdPath, chapterFiles = discoverStoryFiles(storyDir)
    workCssPath = storyDir / "work.css"
    workCssFile = _readOptionalCssFile(workCssPath)

    if not chapterFiles:
        assert indexMdPath is not None
        return _loadSingleDocStory(
            storyDir,
            storyKdlPath=storyKdlPath,
            indexMdPath=indexMdPath,
            workCssFile=workCssFile,
            config=config,
            renderer=renderer,
        )

    return _loadMultiChapterStory(
        storyDir,
        storyKdlPath=storyKdlPath,
        indexMdPath=indexMdPath,
        chapterFiles=chapterFiles,
        workCssPath=workCssPath,
        workCssFile=workCssFile,
        config=config,
        renderer=renderer,
    )


def _storySortKey(story: Story) -> tuple[int, int, str]:
    """Sort stories by explicit date first, then updated date descending, then title."""
    dateOrdinal = story.updated.date().toordinal()
    return (0 if story.hasExplicitDate else 1, -dateOrdinal, story.title.lower())


def _buildTaxonomies(
    stories: tuple[Story, ...],
) -> tuple[dict[t.TaxonomyKind, tuple[TaxonomyBucket, ...]], dict[t.TaxonomyKind, dict[str, str]]]:
    """Build static taxonomy buckets and slug maps for all stories."""
    taxonomies: dict[t.TaxonomyKind, tuple[TaxonomyBucket, ...]] = {}
    slugMaps: dict[t.TaxonomyKind, dict[str, str]] = {}

    for kind, label, singularLabel in TAXONOMY_SPECS:
        grouped: dict[str, list[Story]] = {}
        canonicalNames: dict[str, str] = {}
        observedByCanonical: dict[str, set[str]] = {}
        for story in stories:
            for name in story.taxonomyNames(kind):
                key = name.lower()
                canonical = canonicalNames.setdefault(key, name)
                grouped.setdefault(canonical, []).append(story)
                observedByCanonical.setdefault(canonical, set()).add(name)

        slugMap = buildSlugMap(grouped.keys())
        # Map alternate casings from individual stories onto their canonical bucket slug.
        for canonical, spellings in observedByCanonical.items():
            canonicalSlug = slugMap[canonical]
            for spelling in spellings:
                slugMap.setdefault(spelling, canonicalSlug)
        slugMaps[kind] = slugMap
        buckets = tuple(
            TaxonomyBucket(
                kind=kind,
                label=label,
                singularLabel=singularLabel,
                item=TaxonomyItem(kind=kind, name=name, slug=slugMap[name]),
                stories=tuple(grouped[name]),
            )
            for name in sorted(grouped.keys(), key=lambda value: (value.lower(), value))
        )
        taxonomies[kind] = buckets

    return (taxonomies, slugMaps)


def _buildFeedEntries(stories: tuple[Story, ...], config: SiteConfig) -> tuple[FeedEntry, ...]:
    """Build sorted Atom feed entries for all published stories and chapters."""
    baseUrl = config.normalizedBaseUrl()
    entries: list[tuple[tuple[int, str, int], FeedEntry]] = []

    for story in stories:
        categories = _mergeLists(story.fandoms, story.categories, story.relationships, story.characters, story.tags)
        if not story.isMultiChapter:
            url = f"{baseUrl}{story.slug}/"
            entry = FeedEntry(
                entryId=url,
                title=story.title,
                url=url,
                updated=story.updated,
                published=story.date,
                author=story.author or config.author,
                summaryText=story.summaryText,
                contentHtml=story.bodyHtml,
                categories=categories,
            )
            entries.append(((-story.updated.date().toordinal(), story.title.lower(), 0), entry))
        else:
            for chapter in story.chapters:
                url = f"{baseUrl}{story.slug}/{chapter.filename}"
                entryTitle = f"{story.title} — {chapter.displayTitle}"
                entry = FeedEntry(
                    entryId=url,
                    title=entryTitle,
                    url=url,
                    updated=chapter.date,
                    published=chapter.date,
                    author=story.author or config.author,
                    summaryText=chapter.summaryText,
                    contentHtml=chapter.bodyHtml,
                    categories=categories,
                )
                entries.append(((-chapter.date.date().toordinal(), story.title.lower(), -chapter.number), entry))

    entries.sort(key=lambda pair: pair[0])
    return tuple(entry for _, entry in entries)


def _warnUndatedStories(stories: tuple[Story, ...]) -> None:
    """Log warnings for published stories or chapters missing an explicit Date."""
    warned = False
    for story in stories:
        if not story.published:
            continue
        if not story.isMultiChapter:
            if not story.hasExplicitDate:
                _LOG.warning("%s is published without a Date: %s", story.title, story.sourceDir / "index.md")
                warned = True
        else:
            for chapter in story.chapters:
                if chapter.published and not chapter.hasExplicitDate:
                    _LOG.warning(
                        "%s (%s) is published without a Date: %s",
                        story.title,
                        chapter.displayTitle,
                        chapter.sourcePath,
                    )
                    warned = True
    if warned:
        _LOG.warning("Run `ficgen stamp` to add date metadata to undated published files.")


def _validateStorySlugs(storyDirs: t.Sequence[Path], config: SiteConfig) -> None:
    """Reject story slugs that collide with generated site folders/files or the stories directory itself."""
    resolvedStoriesDir = config.storiesDir.resolve()
    for storyDir in storyDirs:
        slug = storyDir.name
        if slug.lower() in RESERVED_OUTPUT_SLUGS:
            msg = (
                f"Story slug {slug!r} ({storyDir}) conflicts with a reserved site output path. "
                + "Rename the story directory."
            )
            raise StoryParseError(msg)
        if (config.outputDir / slug).resolve() == resolvedStoriesDir:
            msg = (
                f"Story slug {slug!r} ({storyDir}) conflicts with the stories source directory "
                + f"{config.storiesDir}. Rename the story directory."
            )
            raise StoryParseError(msg)


def loadArchive(config: SiteConfig) -> Archive:
    """Scan storiesDir and construct a fully indexed Archive."""
    storyDirs = discoverStoryDirs(config.storiesDir)
    _validateStorySlugs(storyDirs, config)
    renderer = MarkdownRenderer(hideNsfw=config.hideNsfw)

    allStorySlugs = tuple(storyDir.name for storyDir in storyDirs)
    loaded: list[Story] = []
    for storyDir in storyDirs:
        story = loadStory(storyDir, config, renderer)
        if story is not None:
            loaded.append(story)

    loaded.sort(key=_storySortKey)
    stories = tuple(loaded)
    _warnUndatedStories(stories)
    taxonomies, slugMaps = _buildTaxonomies(stories)
    totalWords = sum(story.wordCount for story in stories)
    feedEntries = _buildFeedEntries(stories, config)

    return Archive(
        config=config,
        stories=stories,
        allStorySlugs=allStorySlugs,
        taxonomies=taxonomies,
        slugMaps=slugMaps,
        totalWords=totalWords,
        feedEntries=feedEntries,
    )
