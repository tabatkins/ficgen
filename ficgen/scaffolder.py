"""Scaffold new single-document and multi-chapter story directories."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path

import kdl

from . import t
from .kdlMeta import StoryParseError
from .models import slugify
from .stamper import defaultTodayDate, validateDateString

_SAFE_SLUG_RE: t.Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9-]*$")


@dataclass(slots=True)
class InitOptions:
    """Options for initializing a new story directory."""

    storiesDir: Path
    title: str
    slug: str = ""
    multiChapter: bool = False
    chapterCount: int = 1
    published: bool = False
    dateValue: str = ""
    fandoms: t.MetadataList = ()
    categories: t.MetadataList = ()
    relationships: t.MetadataList = ()
    characters: t.MetadataList = ()
    tags: t.MetadataList = ()


@dataclass(slots=True)
class InitResult:
    """Paths created when scaffolding a new story."""

    storyDir: Path
    slug: str
    createdFiles: tuple[Path, ...]


def _formatKdlString(value: str) -> str:
    """Serialize a Python string as a properly escaped KDL string literal."""
    return kdl.String(value).print()


def _formatKdlListLine(nodeName: str, values: t.MetadataList) -> str:
    """Format a KDL list metadata line, using an empty string placeholder when unset."""
    if not values:
        return f'{nodeName} ""'
    args = " ".join(_formatKdlString(item) for item in values)
    return f"{nodeName} {args}"


def resolveStorySlug(title: str, explicitSlug: str) -> str:
    """Validate an explicit slug or derive one cleanly from the story title."""
    cleanedTitle = title.strip()
    if not cleanedTitle:
        msg = "Story title cannot be empty."
        raise StoryParseError(msg)

    slug = explicitSlug.strip() if explicitSlug.strip() else slugify(cleanedTitle)
    if not _SAFE_SLUG_RE.match(slug):
        msg = (
            f"Invalid story slug {slug!r}: must start with a lowercase letter or digit "
            + "and contain only lowercase letters, digits, and hyphens."
        )
        raise StoryParseError(msg)
    return slug


def _buildStoryMetadataLines(options: InitOptions, *, includeTitle: bool, includePublishState: bool) -> list[str]:
    """Build KDL metadata lines shared by single-doc index.md and multi-chapter story.kdl."""
    lines: list[str] = []
    if includeTitle:
        lines.append(f"title {_formatKdlString(options.title.strip())}")
    lines.append(_formatKdlListLine("fandoms", options.fandoms))
    lines.append(_formatKdlListLine("categories", options.categories))
    lines.append(_formatKdlListLine("characters", options.characters))
    lines.append(_formatKdlListLine("relationships", options.relationships))
    lines.append(_formatKdlListLine("tags", options.tags))

    if includePublishState:
        if options.dateValue:
            lines.append(f"date {_formatKdlString(options.dateValue)}")
        elif options.published:
            lines.append(f"date {_formatKdlString(defaultTodayDate())}")
        if not options.published:
            lines.append("published false")

    return lines


def _buildChapterContent(chapterNum: int, *, published: bool, dateValue: str) -> str:
    """Build starter Markdown content for a chapter file."""
    metaLines: list[str] = [f"chapter {chapterNum}"]
    if dateValue:
        metaLines.append(f"date {_formatKdlString(dateValue)}")
    elif published:
        metaLines.append(f"date {_formatKdlString(defaultTodayDate())}")
    if not published:
        metaLines.append("published false")

    metaBlock = "\n".join(metaLines)
    return f"```metadata\n{metaBlock}\n```\n\n"


def initStory(options: InitOptions) -> InitResult:
    """Create a new story directory with starter KDL and Markdown files."""
    title = options.title.strip()
    slug = resolveStorySlug(title, options.slug)

    if options.chapterCount < 1:
        msg = f"Chapter count must be >= 1, got {options.chapterCount}."
        raise StoryParseError(msg)

    validatedDate = validateDateString(options.dateValue) if options.dateValue.strip() else ""
    effectiveOptions = InitOptions(
        storiesDir=options.storiesDir,
        title=title,
        slug=slug,
        multiChapter=options.multiChapter or options.chapterCount > 1,
        chapterCount=options.chapterCount,
        published=options.published,
        dateValue=validatedDate,
        fandoms=options.fandoms,
        categories=options.categories,
        relationships=options.relationships,
        characters=options.characters,
        tags=options.tags,
    )

    storyDir = effectiveOptions.storiesDir / slug
    if storyDir.exists():
        msg = f"Story directory already exists: {storyDir}"
        raise StoryParseError(msg)

    storyDir.mkdir(parents=True, exist_ok=False)
    created: list[Path] = []

    if not effectiveOptions.multiChapter:
        metaLines = _buildStoryMetadataLines(effectiveOptions, includeTitle=False, includePublishState=True)
        indexPath = storyDir / "index.md"
        indexContent = f"# {title}\n\n```metadata\n" + "\n".join(metaLines) + "\n```\n\n"
        indexPath.write_text(indexContent, encoding="utf-8")
        created.append(indexPath)
    else:
        storyMetaLines = _buildStoryMetadataLines(effectiveOptions, includeTitle=True, includePublishState=False)
        storyMetaLines.append('// summary ""')
        storyKdlPath = storyDir / "story.kdl"
        storyKdlPath.write_text("\n".join(storyMetaLines) + "\n", encoding="utf-8")
        created.append(storyKdlPath)

        for chapterNum in range(1, effectiveOptions.chapterCount + 1):
            chapterPath = storyDir / f"ch{chapterNum}.md"
            chapterContent = _buildChapterContent(
                chapterNum,
                published=effectiveOptions.published,
                dateValue=effectiveOptions.dateValue,
            )
            chapterPath.write_text(chapterContent, encoding="utf-8")
            created.append(chapterPath)

    return InitResult(storyDir=storyDir, slug=slug, createdFiles=tuple(created))
