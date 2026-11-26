"""Static HTML, CSS, and Atom feed generation for the fiction archive."""

from __future__ import annotations

import contextlib
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from . import t
from .css import buildSiteStylesheet, buildStoryPageCss
from .kdlMeta import StoryParseError, readUtf8File
from .models import TAXONOMY_SPECS, Archive, SiteConfig, Story, TaxonomyBucket


@dataclass(slots=True)
class BuildResult:
    """Summary of files written during a static site build."""

    outputDir: Path
    storyCount: int
    chapterPageCount: int
    taxonomyPageCount: int
    feedEntryCount: int
    writtenFiles: tuple[Path, ...]


def _formatNumber(value: int) -> str:
    """Format an integer with thousands separators."""
    return f"{value:,}"


def _formatAtomTime(dt: datetime) -> str:
    """Format a timezone-aware datetime as RFC 3339 UTC for Atom 1.0."""
    return dt.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _createJinjaEnv() -> Environment:
    """Construct the Jinja2 template environment with strict autoescaping."""
    env = Environment(
        loader=PackageLoader("ficgen", "templates"),
        autoescape=select_autoescape(["html", "xml"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    env.filters["format_num"] = _formatNumber
    env.filters["atom_time"] = _formatAtomTime
    return env


def _writeTextFile(path: Path, content: str, written: list[Path]) -> None:
    """Write UTF-8 text to disk, creating parent directories as needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    written.append(path)


def _cleanStoryDir(storyOutDir: Path) -> None:
    """Remove previously generated HTML files inside a story's output directory."""
    if not storyOutDir.is_dir():
        return
    for item in storyOutDir.iterdir():
        if item.is_file() and item.suffix == ".html":
            item.unlink()
    with contextlib.suppress(OSError):
        # Leave directory intact if it still contains non-HTML assets.
        storyOutDir.rmdir()


def _writeStylesheet(outputDir: Path, config: SiteConfig, written: list[Path]) -> None:
    """Write the archive's style.css, applying font settings and any custom site stylesheet."""
    outCssPath = outputDir / "style.css"
    customCss: str | None = None

    if config.customCssPath is not None:
        if not config.customCssPath.is_file():
            msg = f"Custom CSS file does not exist: {config.customCssPath}"
            raise StoryParseError(msg)
        if config.customCssPath.resolve() == outCssPath.resolve():
            msg = (
                f"Custom CSS path cannot be the same as the generated output stylesheet: {outCssPath} "
                + "(use a separate source file such as site.css)."
            )
            raise StoryParseError(msg)
        customCss = readUtf8File(config.customCssPath, label="custom CSS file")

    packagedCss = resources.files("ficgen").joinpath("static/style.css").read_text(encoding="utf-8")
    renderedCss = buildSiteStylesheet(
        packagedCss,
        fontSerif=config.fontSerif,
        fontSans=config.fontSans,
        fontMono=config.fontMono,
        customCss=customCss,
        customCssMode=config.customCssMode,
    )
    _writeTextFile(outCssPath, renderedCss, written)


def _renderSingleStory(
    env: Environment,
    archive: Archive,
    story: Story,
    written: list[Path],
) -> None:
    """Generate the single-page HTML for a one-shot story."""
    template = env.get_template("story_single.html")
    html = template.render(
        archive=archive,
        story=story,
        rootPrefix="../",
        pageTitle=f"{story.title} — {archive.config.title}",
        metaDescription=story.summaryText,
        activeNav="",
        bodyClass="page-story page-story-single",
        pageCss=buildStoryPageCss(story.cssBlocks, customCssMode=archive.config.customCssMode),
    )
    _writeTextFile(archive.config.outputDir / story.slug / "index.html", html, written)


def _renderMultiChapterStory(
    env: Environment,
    archive: Archive,
    story: Story,
    written: list[Path],
) -> int:
    """Generate TOC, per-chapter pages, and Entire Work view for a multi-chapter story."""
    storyOutDir = archive.config.outputDir / story.slug
    cssMode = archive.config.customCssMode

    tocTemplate = env.get_template("story_toc.html")
    tocHtml = tocTemplate.render(
        archive=archive,
        story=story,
        rootPrefix="../",
        pageTitle=f"{story.title} — {archive.config.title}",
        metaDescription=story.summaryText,
        activeNav="",
        bodyClass="page-story page-story-toc",
        pageCss=buildStoryPageCss(story.cssBlocks, customCssMode=cssMode),
    )
    _writeTextFile(storyOutDir / "index.html", tocHtml, written)

    chapterTemplate = env.get_template("story_chapter.html")
    chapters = story.chapters
    for idx, chapter in enumerate(chapters):
        prevChapter = chapters[idx - 1] if idx > 0 else None
        nextChapter = chapters[idx + 1] if idx + 1 < len(chapters) else None
        chapterHtml = chapterTemplate.render(
            archive=archive,
            story=story,
            chapter=chapter,
            chapterIndex=idx,
            prevChapter=prevChapter,
            nextChapter=nextChapter,
            rootPrefix="../",
            pageTitle=f"{chapter.displayTitle} — {story.title} — {archive.config.title}",
            metaDescription=chapter.summaryText,
            activeNav="",
            bodyClass="page-story page-story-chapter",
            pageCss=buildStoryPageCss(
                story.cssBlocks,
                (("article.work", chapter.cssBlocks),),
                customCssMode=cssMode,
            ),
        )
        _writeTextFile(storyOutDir / chapter.filename, chapterHtml, written)

    allTemplate = env.get_template("story_all.html")
    allHtml = allTemplate.render(
        archive=archive,
        story=story,
        rootPrefix="../",
        pageTitle=f"{story.title} (Entire Work) — {archive.config.title}",
        metaDescription=story.summaryText,
        activeNav="",
        bodyClass="page-story page-story-all",
        pageCss=buildStoryPageCss(
            story.cssBlocks,
            tuple((f"#ch-{chapter.number}", chapter.cssBlocks) for chapter in chapters),
            customCssMode=cssMode,
        ),
    )
    _writeTextFile(storyOutDir / "all.html", allHtml, written)
    return len(chapters)


def _renderBrowsePages(
    env: Environment,
    archive: Archive,
    written: list[Path],
) -> int:
    """Generate static taxonomy browse hub and per-tag index pages."""
    browseDir = archive.config.outputDir / "browse"
    if browseDir.exists():
        shutil.rmtree(browseDir)

    sections: list[tuple[t.TaxonomyKind, str, tuple[TaxonomyBucket, ...]]] = [
        (kind, label, archive.taxonomies[kind]) for kind, label, _ in TAXONOMY_SPECS if archive.taxonomies[kind]
    ]

    hubTemplate = env.get_template("browse_index.html")
    hubHtml = hubTemplate.render(
        archive=archive,
        sections=sections,
        rootPrefix="../",
        pageTitle=f"Browse Tags — {archive.config.title}",
        metaDescription=f"Browse fandoms, relationships, characters, and tags in {archive.config.title}.",
        activeNav="browse",
        bodyClass="page-browse page-browse-index",
    )
    _writeTextFile(browseDir / "index.html", hubHtml, written)

    detailTemplate = env.get_template("browse_detail.html")
    taxonomyPageCount = 0
    for kind, _, buckets in sections:
        for bucket in buckets:
            workCount = len(bucket.stories)
            workWord = "work" if workCount == 1 else "works"
            detailHtml = detailTemplate.render(
                archive=archive,
                bucket=bucket,
                rootPrefix="../../",
                pageTitle=f"{bucket.singularLabel}: {bucket.item.name} — {archive.config.title}",
                metaDescription=f"{workCount} {workWord} tagged {bucket.item.name} in {archive.config.title}.",
                activeNav="browse",
                bodyClass="page-browse page-browse-detail",
            )
            _writeTextFile(browseDir / kind / f"{bucket.item.slug}.html", detailHtml, written)
            taxonomyPageCount += 1

    return taxonomyPageCount


def _renderAtomFeed(
    env: Environment,
    archive: Archive,
    written: list[Path],
) -> None:
    """Generate the Atom 1.0 feed (feed.xml) for published stories and chapters."""
    feedUpdated = max(entry.updated for entry in archive.feedEntries) if archive.feedEntries else datetime.now(tz=UTC)
    template = env.get_template("feed.xml")
    xml = template.render(
        archive=archive,
        baseUrl=archive.config.normalizedBaseUrl(),
        feedUpdated=feedUpdated,
        defaultAuthor=archive.config.author or archive.config.title,
    )
    _writeTextFile(archive.config.outputDir / "feed.xml", xml, written)


def generateSite(archive: Archive) -> BuildResult:
    """Render the complete static fiction archive into archive.config.outputDir."""
    outputDir = archive.config.outputDir
    outputDir.mkdir(parents=True, exist_ok=True)
    env = _createJinjaEnv()
    written: list[Path] = []

    # Clean generated HTML in known story directories (including newly unpublished ones).
    for slug in archive.allStorySlugs:
        _cleanStoryDir(outputDir / slug)

    _writeStylesheet(outputDir, archive.config, written)

    indexTemplate = env.get_template("index.html")
    storyCount = len(archive.stories)
    storyWord = "story" if storyCount == 1 else "stories"
    indexDescription = (
        archive.config.subtitle
        or f"{archive.config.title}: {storyCount} {storyWord}, {_formatNumber(archive.totalWords)} words."
    )
    indexHtml = indexTemplate.render(
        archive=archive,
        rootPrefix="",
        pageTitle=archive.config.title,
        metaDescription=indexDescription,
        activeNav="stories",
        bodyClass="page-index",
    )
    _writeTextFile(outputDir / "index.html", indexHtml, written)

    chapterPageCount = 0
    for story in archive.stories:
        if story.isMultiChapter:
            chapterPageCount += _renderMultiChapterStory(env, archive, story, written)
        else:
            _renderSingleStory(env, archive, story, written)

    taxonomyPageCount = _renderBrowsePages(env, archive, written)
    _renderAtomFeed(env, archive, written)

    return BuildResult(
        outputDir=outputDir,
        storyCount=storyCount,
        chapterPageCount=chapterPageCount,
        taxonomyPageCount=taxonomyPageCount,
        feedEntryCount=len(archive.feedEntries),
        writtenFiles=tuple(written),
    )
