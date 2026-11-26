"""Import works from AO3-compatible archives or EPUB downloads into _stories/."""

from __future__ import annotations

import logging
import shutil
from dataclasses import dataclass
from pathlib import Path

import ao3fetch
import kdl

from . import t
from .kdlMeta import StoryParseError
from .scaffolder import resolveStorySlug

_LOG: t.Final[logging.Logger] = logging.getLogger(__name__)


@dataclass(slots=True)
class ImportOptions:
    """Configuration for importing a work into a _stories/ directory."""

    storiesDir: Path
    archiveUrl: str = ao3fetch.DEFAULT_BASE_URL
    slug: str = ""
    asDraft: bool = False
    force: bool = False


@dataclass(slots=True)
class ImportResult:
    """Outcome of importing one AO3 work into _stories/."""

    title: str
    slug: str
    sourceUrl: str
    storyDir: Path
    createdFiles: tuple[Path, ...]
    chapterCount: int


def htmlToMarkdown(html: str) -> str:
    """Convert AO3 HTML content (prose, summary, or notes) into clean Markdown."""
    return ao3fetch.toMarkdown(html)


def _formatKdlStr(value: str) -> str:
    return kdl.String(value).print()


def _formatKdlList(nodeName: str, values: t.Sequence[str]) -> str | None:
    cleaned = [val.strip() for val in values if val.strip()]
    if not cleaned:
        return None
    args = " ".join(_formatKdlStr(val) for val in cleaned)
    return f"{nodeName} {args}"


def _formatAuthors(authors: tuple[ao3fetch.Author, ...]) -> str:
    formatted: list[str] = []
    for author in authors:
        if author.anonymous:
            formatted.append("Anonymous")
        elif author.pseud and author.pseud != author.username:
            formatted.append(f"{author.pseud} ({author.username})")
        else:
            formatted.append(author.username)
    return ", ".join(formatted)


def _buildSharedMetaLines(work: ao3fetch.WorkWithText | ao3fetch.DownloadedWork) -> list[str]:
    """Build KDL metadata lines common to one-shot index.md and multi-chapter story.kdl."""
    lines: list[str] = []
    authorStr = _formatAuthors(work.authors)
    if authorStr:
        lines.append(f"author {_formatKdlStr(authorStr)}")

    for nodeName, values in (
        ("fandoms", work.fandoms),
        ("categories", tuple(cat.value for cat in work.category) if work.category else ()),
        ("characters", work.tags.characters),
        ("relationships", work.tags.relationships),
        ("tags", work.tags.additional),
    ):
        line = _formatKdlList(nodeName, values)
        if line is not None:
            lines.append(line)

    if work.rating:
        lines.append(f"rating {_formatKdlStr(work.rating.value)}")

    warningsLine = _formatKdlList("warnings", tuple(warn.value for warn in work.tags.warnings))
    if warningsLine is not None:
        lines.append(warningsLine)

    return lines


def _joinNoteHtml(*parts: str | None) -> str:
    rendered = [htmlToMarkdown(part) for part in parts if part and part.strip()]
    return "\n\n".join(text for text in rendered if text)


def _buildMarkdownBlocks(
    *,
    title: str | None,
    metaLines: list[str],
    summaryMd: str,
    notesMd: str,
    endnotesMd: str,
    bodyMd: str,
) -> str:
    sections: list[str] = []
    if title:
        sections.append(f"# {title}")
    sections.append("```metadata\n" + "\n".join(metaLines) + "\n```")
    if summaryMd:
        sections.append(f"<summary>\n{summaryMd}\n</summary>")
    if notesMd:
        sections.append(f"<notes>\n{notesMd}\n</notes>")
    if endnotesMd:
        sections.append(f"<endnotes>\n{endnotesMd}\n</endnotes>")
    if bodyMd:
        sections.append(bodyMd)
    return "\n\n".join(sections) + "\n"


def writeImportedWork(
    work: ao3fetch.WorkWithText | ao3fetch.DownloadedWork,
    options: ImportOptions,
    *,
    chapterDates: t.Mapping[int, str] | None = None,
) -> ImportResult:
    """Write a fetched or downloaded AO3 work into _stories/<slug>/ in KDL + Markdown format."""
    slug = resolveStorySlug(work.title, options.slug)
    storyDir = options.storiesDir / slug
    if storyDir.exists():
        if not options.force:
            msg = f"Story directory already exists: {storyDir} (pass --force to overwrite)"
            raise StoryParseError(msg)
        shutil.rmtree(storyDir)

    storyDir.mkdir(parents=True, exist_ok=False)
    created: list[Path] = []
    chapters = work.content.chapters
    isSingleDoc = len(chapters) == 1 and work.chapters.total == 1

    if isSingleDoc:
        chapter = chapters[0]
        metaLines = _buildSharedMetaLines(work)
        if work.publishedAt:
            metaLines.append(f"date {_formatKdlStr(work.publishedAt)}")
        if work.updatedAt and work.updatedAt != work.publishedAt:
            metaLines.append(f"updated {_formatKdlStr(work.updatedAt)}")
        if not work.complete:
            metaLines.append("complete false")
        if options.asDraft:
            metaLines.append("published false")

        content = _buildMarkdownBlocks(
            title=work.title,
            metaLines=metaLines,
            summaryMd=_joinNoteHtml(work.summary, chapter.summary),
            notesMd=_joinNoteHtml(work.content.notes.start, chapter.notes.start),
            endnotesMd=_joinNoteHtml(chapter.notes.end, work.content.notes.end),
            bodyMd=htmlToMarkdown(chapter.html),
        )
        indexPath = storyDir / "index.md"
        indexPath.write_text(content, encoding="utf-8")
        created.append(indexPath)
    else:
        dateMap = chapterDates or {}
        storyMetaLines = [f"title {_formatKdlStr(work.title)}", *_buildSharedMetaLines(work)]
        storyMetaLines.append(f"complete {'true' if work.complete else 'false'}")
        if work.chapters.total is not None:
            storyMetaLines.append(f"planned-chapters {work.chapters.total}")
        if not dateMap and work.publishedAt:
            storyMetaLines.append(f"date {_formatKdlStr(work.publishedAt)}")
        if not dateMap and work.updatedAt and work.updatedAt != work.publishedAt:
            storyMetaLines.append(f"updated {_formatKdlStr(work.updatedAt)}")
        if options.asDraft:
            storyMetaLines.append("published false")

        summaryMd = _joinNoteHtml(work.summary)
        if summaryMd:
            storyMetaLines.append(f"summary {_formatKdlStr(summaryMd)}")
        startNotesMd = _joinNoteHtml(work.content.notes.start)
        if startNotesMd:
            storyMetaLines.append(f"notes {_formatKdlStr(startNotesMd)}")
        endNotesMd = _joinNoteHtml(work.content.notes.end)
        if endNotesMd:
            storyMetaLines.append(f"endnotes {_formatKdlStr(endNotesMd)}")

        storyKdlPath = storyDir / "story.kdl"
        storyKdlPath.write_text("\n".join(storyMetaLines) + "\n", encoding="utf-8")
        created.append(storyKdlPath)

        for chapter in chapters:
            chMeta = [f"chapter {chapter.index}"]
            chDate = dateMap.get(chapter.index)
            if not chDate and chapter.index == 1 and len(chapters) == 1 and work.publishedAt:
                chDate = work.publishedAt
            if chDate:
                chMeta.append(f"date {_formatKdlStr(chDate)}")
            if options.asDraft:
                chMeta.append("published false")

            chContent = _buildMarkdownBlocks(
                title=chapter.title,
                metaLines=chMeta,
                summaryMd=_joinNoteHtml(chapter.summary),
                notesMd=_joinNoteHtml(chapter.notes.start),
                endnotesMd=_joinNoteHtml(chapter.notes.end),
                bodyMd=htmlToMarkdown(chapter.html),
            )
            chPath = storyDir / f"ch{chapter.index}.md"
            chPath.write_text(chContent, encoding="utf-8")
            created.append(chPath)

    return ImportResult(
        title=work.title,
        slug=slug,
        sourceUrl=work.url,
        storyDir=storyDir,
        createdFiles=tuple(created),
        chapterCount=len(chapters),
    )


def _fetchChapterDatesSafe(workId: ao3fetch.ArchiveId) -> dict[int, str]:
    """Best-effort fetch of per-chapter publication dates from /works/<id>/navigate."""
    try:
        indexData = ao3fetch.getWorkWithChapters(workId)
    except (ao3fetch.ArchivePageRequestError, ValueError, OSError) as exc:
        _LOG.warning("Could not load chapter dates from /navigate for work %s: %s", workId, exc)
        return {}
    return {chapter.index: chapter.publishedAt for chapter in indexData.chapters if chapter.publishedAt}


def importFromSource(source: str, options: ImportOptions) -> ImportResult:
    """Import a work from an AO3/compatible URL, numeric work ID, or local .epub file."""
    localPath = Path(source)
    if localPath.is_file() and localPath.suffix.lower() == ".epub":
        try:
            downloaded = ao3fetch.parseEpubWork(localPath.read_bytes(), filePath=str(localPath))
        except ValueError as exc:
            msg = f"Failed to parse EPUB {localPath}: {exc}"
            raise StoryParseError(msg) from exc
        return writeImportedWork(downloaded, options)

    targetBaseUrl = options.archiveUrl
    if ao3fetch.isValidArchiveId(source):
        workId = ao3fetch.parseArchiveId(source)
    elif "/works/" in source or source.startswith(("http://", "https://")):
        try:
            parsed = ao3fetch.getWorkDetailsFromUrl(source)
        except (ValueError, ao3fetch.InvalidIDError) as exc:
            msg = f"Could not parse archive work URL {source!r}: {exc}"
            raise StoryParseError(msg) from exc
        workId = parsed.workId
        if parsed.baseUrl:
            targetBaseUrl = parsed.baseUrl
    else:
        msg = f"Expected an AO3 work URL, numeric work ID, or .epub file path, got: {source!r}"
        raise StoryParseError(msg)

    previousBaseUrl = ao3fetch.getArchiveBaseUrl()
    ao3fetch.setArchiveBaseUrl(targetBaseUrl)
    try:
        try:
            work = ao3fetch.getWorkContent(workId, fullWork=True)
        except (ao3fetch.ArchivePageRequestError, ao3fetch.InvalidIDError, ValueError, OSError) as exc:
            msg = f"Failed to fetch work {workId} from {targetBaseUrl}: {exc}"
            raise StoryParseError(msg) from exc

        if isinstance(work, ao3fetch.LockedWorkSummary):
            msg = (
                f"Work {work.url} is restricted to logged-in users. "
                + "Pass --cookie with a session cookie or download its EPUB and run `ficgen import <file.epub>`."
            )
            raise StoryParseError(msg)

        chapterDates: dict[int, str] = {}
        if len(work.content.chapters) > 1 or work.chapters.total != 1:
            chapterDates = _fetchChapterDatesSafe(work.id)

        return writeImportedWork(work, options, chapterDates=chapterDates)
    finally:
        ao3fetch.setArchiveBaseUrl(previousBaseUrl)
