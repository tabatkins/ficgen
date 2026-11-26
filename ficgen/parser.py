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
    getBoolField,
    getDateField,
    getIntField,
    getListField,
    getStringField,
    mergeDocuments,
    parseKdlDocument,
)
from .markdownExt import MarkdownRenderer, countWords, makeExcerpt, stripHtmlTags
from .models import (
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
_METADATA_FENCE_RE: t.Final[re.Pattern[str]] = re.compile(
    r"(?ms)^```metadata(?:[ \t]+kdl)?[ \t]*\n(.*?)\n```[ \t]*\n?",
)
_CSS_FENCE_RE: t.Final[re.Pattern[str]] = re.compile(
    r"(?ms)^```css[ \t]*\n(.*?)^```[ \t]*\n?",
)
_LEADING_KDL_FENCE_RE: t.Final[re.Pattern[str]] = re.compile(
    r"(?ms)\A[ \t\n]*```kdl[ \t]*\n(.*?)\n```[ \t]*\n?",
)
_CODE_FENCE_SPLIT_RE: t.Final[re.Pattern[str]] = re.compile(
    r"(?ms)(^```[^\n]*\n.*?^```[ \t]*$)",
)
_SPECIAL_TAG_RE: t.Final[re.Pattern[str]] = re.compile(
    r"(?is)<(notes|startnotes|beginnotes|endnotes|summary)>(.*?)</\1>",
)
_CHAPTER_FILE_RE: t.Final[re.Pattern[str]] = re.compile(r"^ch(\d+)\.md$", re.IGNORECASE)
_NATURAL_SPLIT_RE: t.Final[re.Pattern[str]] = re.compile(r"(\d+)")
_STORY_KDL_NAMES: t.Final[tuple[str, ...]] = ("story.kdl", "meta.kdl", "index.kdl")


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


def _extractSpecialBlocks(text: str) -> tuple[str, list[str], list[str], list[str]]:
    """Extract <summary>, <notes>, and <endnotes> blocks outside fenced code blocks."""
    summaries: list[str] = []
    notes: list[str] = []
    endnotes: list[str] = []

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

    segments = _CODE_FENCE_SPLIT_RE.split(text)
    cleanedSegments: list[str] = []
    for index, segment in enumerate(segments):
        if index % 2 == 1:
            cleanedSegments.append(segment)
        else:
            cleanedSegments.append(_SPECIAL_TAG_RE.sub(_replaceTag, segment))

    return "".join(cleanedSegments), summaries, notes, endnotes


def parseMarkdownDocument(rawText: str, sourceDesc: str = "<markdown>") -> ParsedDocument:
    """Parse title, KDL metadata block, author's notes, summary, and body from Markdown text."""
    normalized = rawText.replace("\r\n", "\n").replace("\r", "\n")
    lines = normalized.split("\n")

    title = ""
    firstNonEmpty = 0
    while firstNonEmpty < len(lines) and not lines[firstNonEmpty].strip():
        firstNonEmpty += 1

    if firstNonEmpty < len(lines):
        titleMatch = _TITLE_LINE_RE.match(lines[firstNonEmpty])
        if titleMatch is not None:
            title = titleMatch.group(1).strip()
            remaining = "\n".join(lines[firstNonEmpty + 1 :])
        else:
            remaining = "\n".join(lines[firstNonEmpty:])
    else:
        remaining = ""

    metaDocs: list[kdl.Document] = []

    leadingMatch = _LEADING_KDL_FENCE_RE.match(remaining)
    if leadingMatch is not None:
        metaDocs.append(parseKdlDocument(leadingMatch.group(1), sourceDesc))
        remaining = remaining[leadingMatch.end() :]

    def _consumeMetadata(match: re.Match[str]) -> str:
        metaDocs.append(parseKdlDocument(match.group(1), sourceDesc))
        return ""

    withoutMeta = _METADATA_FENCE_RE.sub(_consumeMetadata, remaining)

    cssBlocks: list[str] = []

    def _consumeCss(match: re.Match[str]) -> str:
        cssText = match.group(1).strip()
        if cssText:
            cssBlocks.append(cssText)
        return ""

    withoutCss = _CSS_FENCE_RE.sub(_consumeCss, withoutMeta)
    metadata = mergeDocuments(*metaDocs)
    bodyRaw, summaries, notes, endnotes = _extractSpecialBlocks(withoutCss)

    metaTitle = getStringField(metadata, "title", sourceDesc=sourceDesc)
    if metaTitle:
        title = metaTitle

    metaSummary = getStringField(metadata, "summary", sourceDesc=sourceDesc)
    if metaSummary:
        summaries.insert(0, metaSummary)

    metaNotes = getStringField(metadata, "notes", "startnotes", "beginnotes", sourceDesc=sourceDesc)
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
        bodyMd=bodyRaw.strip(),
        cssBlocks=tuple(cssBlocks),
    )


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
    try:
        text = cssPath.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Failed to read stylesheet {cssPath}: {exc}"
        raise StoryParseError(msg) from exc
    return text if text.strip() else None


def _loadChapter(
    chapterPath: Path,
    fallbackNumber: int,
    storyTitle: str,
    renderer: MarkdownRenderer,
    workCssPath: Path,
) -> Chapter:
    """Load and render a single chapter Markdown file."""
    sourceDesc = str(chapterPath)
    rawText = chapterPath.read_text(encoding="utf-8")
    parsed = parseMarkdownDocument(rawText, sourceDesc=sourceDesc)
    meta = parsed.metadata

    filenameMatch = _CHAPTER_FILE_RE.match(chapterPath.name)
    inferredNum = int(filenameMatch.group(1)) if filenameMatch is not None else fallbackNumber
    number = getIntField(meta, "chapter", sourceDesc=sourceDesc) or inferredNum

    chapterTitle = parsed.title
    if chapterTitle.lower() == storyTitle.lower():
        chapterTitle = ""

    published = getBoolField(meta, "published", default=True, sourceDesc=sourceDesc)
    date, hasExplicitDate = getDateField(meta, chapterPath, "date", "published-date")

    summaryHtml = renderer.render(parsed.summaryMd)
    notesHtml = renderer.render(parsed.notesMd)
    endnotesHtml = renderer.render(parsed.endnotesMd)
    bodyHtml = renderer.render(parsed.bodyMd)
    wordCount = countWords(bodyHtml)
    summaryText = stripHtmlTags(summaryHtml) if summaryHtml else makeExcerpt(bodyHtml)

    chapterCssPath = chapterPath.with_suffix(".css")
    chapterCssFile = _readOptionalCssFile(chapterCssPath) if chapterCssPath != workCssPath else None
    chapterCssBlocks = tuple(block for block in (chapterCssFile, *parsed.cssBlocks) if block)

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
        extraFandoms=getListField(meta, "fandoms", "fandom", sourceDesc=sourceDesc),
        extraCategories=getListField(meta, "relationship-categories", "categories", "category", sourceDesc=sourceDesc),
        extraRelationships=getListField(meta, "relationships", "relationship", "ships", sourceDesc=sourceDesc),
        extraCharacters=getListField(meta, "characters", "character", sourceDesc=sourceDesc),
        extraTags=getListField(meta, "tags", "tag", "additional-tags", sourceDesc=sourceDesc),
        cssBlocks=chapterCssBlocks,
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


def loadStory(storyDir: Path, config: SiteConfig, renderer: MarkdownRenderer) -> Story | None:
    """Load a story directory into a Story model, or return None if unpublished."""
    slug = storyDir.name
    storyKdlPath, indexMdPath, chapterFiles = discoverStoryFiles(storyDir)
    workCssPath = storyDir / "work.css"
    workCssFile = _readOptionalCssFile(workCssPath)

    isMultiChapter = bool(chapterFiles)
    if not isMultiChapter:
        assert indexMdPath is not None
        sourceDesc = str(indexMdPath)
        indexDoc = parseMarkdownDocument(indexMdPath.read_text(encoding="utf-8"), sourceDesc=sourceDesc)
        if storyKdlPath is not None:
            kdlDoc = parseKdlDocument(storyKdlPath.read_text(encoding="utf-8"), sourceDesc=str(storyKdlPath))
            meta = mergeDocuments(kdlDoc, indexDoc.metadata)
        else:
            meta = indexDoc.metadata

        storyPublished = getBoolField(meta, "published", default=True, sourceDesc=sourceDesc)
        if not storyPublished and not config.includeDrafts:
            return None

        title = getStringField(meta, "title", sourceDesc=sourceDesc) or indexDoc.title or _humanizeSlug(slug)
        author = getStringField(meta, "author", sourceDesc=sourceDesc) or config.author
        complete = getBoolField(
            meta,
            "complete",
            default=getBoolField(meta, "status", default=True, sourceDesc=sourceDesc),
            sourceDesc=sourceDesc,
        )
        date, hasExplicitDate = getDateField(meta, indexMdPath, "date", "published-date")
        updated, hasUpdated = getDateField(meta, indexMdPath, "updated", "updated-date")
        if not hasUpdated:
            updated = date

        summaryMd = indexDoc.summaryMd or getStringField(meta, "summary", sourceDesc=sourceDesc)
        notesMd = indexDoc.notesMd or getStringField(meta, "notes", "startnotes", "beginnotes", sourceDesc=sourceDesc)
        endnotesMd = indexDoc.endnotesMd or getStringField(meta, "endnotes", sourceDesc=sourceDesc)

        summaryHtml = renderer.render(summaryMd)
        notesHtml = renderer.render(notesMd)
        endnotesHtml = renderer.render(endnotesMd)
        bodyHtml = renderer.render(indexDoc.bodyMd)
        wordCount = countWords(bodyHtml)
        summaryText = stripHtmlTags(summaryHtml) if summaryHtml else makeExcerpt(bodyHtml)
        workCssBlocks = tuple(block for block in (workCssFile, *indexDoc.cssBlocks) if block)

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
            fandoms=getListField(meta, "fandoms", "fandom", sourceDesc=sourceDesc),
            categories=getListField(meta, "relationship-categories", "categories", "category", sourceDesc=sourceDesc),
            relationships=getListField(meta, "relationships", "relationship", "ships", sourceDesc=sourceDesc),
            characters=getListField(meta, "characters", "character", sourceDesc=sourceDesc),
            tags=getListField(meta, "tags", "tag", "additional-tags", sourceDesc=sourceDesc),
            summaryHtml=summaryHtml,
            summaryText=summaryText,
            notesHtml=notesHtml,
            endnotesHtml=endnotesHtml,
            bodyHtml=bodyHtml,
            wordCount=wordCount,
            chapters=(),
            sourceDir=storyDir,
            cssBlocks=workCssBlocks,
        )

    # Multi-chapter story: prefer whole-work story.kdl, falling back to index.md or first chapter.
    indexCssBlocks: tuple[str, ...] = ()
    if storyKdlPath is not None:
        metaSourcePath = storyKdlPath
        sourceDesc = str(storyKdlPath)
        storyMeta = parseKdlDocument(storyKdlPath.read_text(encoding="utf-8"), sourceDesc=sourceDesc)
        storyTitle = getStringField(storyMeta, "title", sourceDesc=sourceDesc) or _humanizeSlug(slug)
        storySummaryMd = getStringField(storyMeta, "summary", sourceDesc=sourceDesc)
        storyNotesMd = getStringField(storyMeta, "notes", "startnotes", "beginnotes", sourceDesc=sourceDesc)
        storyEndnotesMd = getStringField(storyMeta, "endnotes", sourceDesc=sourceDesc)
    elif indexMdPath is not None:
        metaSourcePath = indexMdPath
        sourceDesc = str(indexMdPath)
        indexDoc = parseMarkdownDocument(indexMdPath.read_text(encoding="utf-8"), sourceDesc=sourceDesc)
        storyMeta = indexDoc.metadata
        storyTitle = indexDoc.title or _humanizeSlug(slug)
        storySummaryMd = indexDoc.summaryMd or indexDoc.bodyMd
        storyNotesMd = indexDoc.notesMd
        storyEndnotesMd = indexDoc.endnotesMd
        indexCssBlocks = indexDoc.cssBlocks
    else:
        metaSourcePath = chapterFiles[0]
        sourceDesc = str(chapterFiles[0])
        firstDoc = parseMarkdownDocument(chapterFiles[0].read_text(encoding="utf-8"), sourceDesc=sourceDesc)
        storyMeta = firstDoc.metadata
        storyTitle = firstDoc.title or _humanizeSlug(slug)
        storySummaryMd = ""
        storyNotesMd = ""
        storyEndnotesMd = ""

    storyPublished = getBoolField(storyMeta, "published", default=True, sourceDesc=sourceDesc)
    if not storyPublished and not config.includeDrafts:
        return None

    loadedChapters = [
        _loadChapter(chapterPath, idx + 1, storyTitle, renderer, workCssPath)
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
    complete = getBoolField(
        storyMeta,
        "complete",
        default=getBoolField(storyMeta, "status", default=False, sourceDesc=sourceDesc),
        sourceDesc=sourceDesc,
    )
    plannedChapters = getIntField(storyMeta, "planned-chapters", "total-chapters", sourceDesc=sourceDesc)

    fandoms = _mergeLists(
        getListField(storyMeta, "fandoms", "fandom", sourceDesc=sourceDesc),
        *(chapter.extraFandoms for chapter in includedChapters),
    )
    categories = _mergeLists(
        getListField(storyMeta, "relationship-categories", "categories", "category", sourceDesc=sourceDesc),
        *(chapter.extraCategories for chapter in includedChapters),
    )
    relationships = _mergeLists(
        getListField(storyMeta, "relationships", "relationship", "ships", sourceDesc=sourceDesc),
        *(chapter.extraRelationships for chapter in includedChapters),
    )
    characters = _mergeLists(
        getListField(storyMeta, "characters", "character", sourceDesc=sourceDesc),
        *(chapter.extraCharacters for chapter in includedChapters),
    )
    tags = _mergeLists(
        getListField(storyMeta, "tags", "tag", "additional-tags", sourceDesc=sourceDesc),
        *(chapter.extraTags for chapter in includedChapters),
    )

    storyDate, hasStoryDate = getDateField(storyMeta, metaSourcePath, "date", "published-date")
    explicitChapterDates = [chapter.date for chapter in includedChapters if chapter.hasExplicitDate]
    if not hasStoryDate and explicitChapterDates:
        storyDate = min(explicitChapterDates)
        hasStoryDate = True

    updated, hasUpdated = getDateField(storyMeta, metaSourcePath, "updated", "updated-date")
    if not hasUpdated:
        chapterDates = explicitChapterDates if explicitChapterDates else [chapter.date for chapter in includedChapters]
        updated = max([storyDate, *chapterDates])

    summaryHtml = renderer.render(storySummaryMd)
    notesHtml = renderer.render(storyNotesMd)
    endnotesHtml = renderer.render(storyEndnotesMd)
    summaryText = stripHtmlTags(summaryHtml) if summaryHtml else includedChapters[0].summaryText
    totalWords = sum(chapter.wordCount for chapter in includedChapters)
    workCssBlocks = tuple(block for block in (workCssFile, *indexCssBlocks) if block)

    return Story(
        slug=slug,
        title=storyTitle,
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
        for story in stories:
            for name in story.taxonomyNames(kind):
                key = name.lower()
                canonical = canonicalNames.setdefault(key, name)
                grouped.setdefault(canonical, []).append(story)

        slugMap = buildSlugMap(grouped.keys())
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


def loadArchive(config: SiteConfig) -> Archive:
    """Scan storiesDir and construct a fully indexed Archive."""
    storyDirs = discoverStoryDirs(config.storiesDir)
    renderer = MarkdownRenderer()

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
