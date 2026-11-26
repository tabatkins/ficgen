"""Static HTML, CSS, and Atom feed generation for the fiction archive."""

from __future__ import annotations

import contextlib
import re
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from importlib import resources
from pathlib import Path

from jinja2 import Environment, PackageLoader, select_autoescape

from . import t
from .kdlMeta import StoryParseError
from .models import (
    TAXONOMY_SPECS,
    Archive,
    SiteConfig,
    Story,
    TaxonomyBucket,
    resolveFontStack,
)

_FONT_SERIF_RE: t.Final[re.Pattern[str]] = re.compile(r"(?m)^([ \t]*--font-serif:[ \t]*)([^;\n]+)(;)")
_FONT_SANS_RE: t.Final[re.Pattern[str]] = re.compile(r"(?m)^([ \t]*--font-sans:[ \t]*)([^;\n]+)(;)")
_FONT_MONO_RE: t.Final[re.Pattern[str]] = re.compile(r"(?m)^([ \t]*--font-mono:[ \t]*)([^;\n]+)(;)")
_LAYER_ORDER_FULL: t.Final[str] = "@layer ficgen, site, work, chapter;"
_LAYER_ORDER_REPLACE: t.Final[str] = "@layer site, work, chapter;"
_LAYER_ORDER_LINE_RE: t.Final[re.Pattern[str]] = re.compile(r"(?m)^@layer ficgen, site, work, chapter;\n+")
_IMPORT_LAYER_NAMED_RE: t.Final[re.Pattern[str]] = re.compile(r"^layer\(\s*([^)]+?)\s*\)", re.IGNORECASE)
_IMPORT_LAYER_ANON_RE: t.Final[re.Pattern[str]] = re.compile(r"^layer(?=$|[\s;])", re.IGNORECASE)
_STYLE_CLOSE_RE: t.Final[re.Pattern[str]] = re.compile(r"(?i)</(style)(?=[\s>/])")


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
    utc = dt.astimezone(UTC)
    return utc.strftime("%Y-%m-%dT%H:%M:%SZ")


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


def _replaceCssVar(cssText: str, pattern: re.Pattern[str], cssValue: str) -> str:
    """Replace the first matching CSS custom property value in cssText."""

    def _sub(match: re.Match[str]) -> str:
        return f"{match.group(1)}{cssValue}{match.group(3)}"

    return pattern.sub(_sub, cssText, count=1)


def _applyFontConfig(cssText: str, config: SiteConfig) -> str:
    """Substitute configured serif, sans, and mono font stacks into style.css."""
    updated = _replaceCssVar(cssText, _FONT_SERIF_RE, resolveFontStack(config.fontSerif, role="serif"))
    updated = _replaceCssVar(updated, _FONT_SANS_RE, resolveFontStack(config.fontSans, role="sans"))
    return _replaceCssVar(updated, _FONT_MONO_RE, resolveFontStack(config.fontMono, role="mono"))


def _skipCssQuoted(cssText: str, start: int) -> int:
    """Return index immediately after the closing quote of a CSS string starting at start."""
    quote = cssText[start]
    pos = start + 1
    length = len(cssText)
    while pos < length:
        ch = cssText[pos]
        if ch == "\\":
            pos += 2
            continue
        pos += 1
        if ch == quote:
            return pos
    return length


def _scanCssRuleEnd(cssText: str, start: int) -> int:
    """Find the index immediately past the terminating ';' of an @-rule, respecting quotes and parens."""
    pos = start
    length = len(cssText)
    parenDepth = 0
    while pos < length:
        ch = cssText[pos]
        if ch in {"'", '"'}:
            pos = _skipCssQuoted(cssText, pos)
            continue
        pos += 1
        if ch == "(":
            parenDepth += 1
        elif ch == ")" and parenDepth > 0:
            parenDepth -= 1
        elif ch == ";" and parenDepth == 0:
            return pos
    return length


def _indentCss(cssText: str, spaces: int = 2) -> str:
    """Indent non-empty lines of CSS text by the given number of spaces."""
    prefix = " " * spaces
    return "\n".join(prefix + line if line.strip() else "" for line in cssText.strip().splitlines())


def _wrapLayerBlock(layerName: str, cssBody: str) -> str:
    """Wrap CSS rules inside `@layer <layerName> { ... }`."""
    return f"@layer {layerName} {{\n{_indentCss(cssBody, 2)}\n}}"


def _wrapScopedBlock(scopeSelector: str, cssBody: str) -> str:
    """Wrap CSS rules inside `@scope (<scopeSelector>) { ... }`."""
    return f"@scope ({scopeSelector}) {{\n{_indentCss(cssBody, 2)}\n}}"


def _skipImportTarget(importRule: str, start: int) -> int:
    """Return the index immediately past the <url> or <string> target of an @import rule."""
    pos = start
    length = len(importRule)
    while pos < length and importRule[pos].isspace():
        pos += 1
    if pos >= length:
        return pos
    if importRule[pos] in {"'", '"'}:
        return _skipCssQuoted(importRule, pos)
    if importRule[pos : pos + 4].lower() == "url(":
        pos += 4
        while pos < length:
            ch = importRule[pos]
            if ch in {"'", '"'}:
                pos = _skipCssQuoted(importRule, pos)
                continue
            pos += 1
            if ch == ")":
                return pos
    return pos


def _scopeImportToLayer(importRule: str, layerName: str) -> str:
    """Insert or nest a `layer(...)` clause on an `@import` rule for the given layer."""
    trimmed = importRule.strip()
    if not trimmed.endswith(";"):
        trimmed += ";"
    afterTarget = _skipImportTarget(trimmed, len("@import"))
    prefix = trimmed[:afterTarget]
    rest = trimmed[afterTarget:].lstrip()

    namedMatch = _IMPORT_LAYER_NAMED_RE.match(rest)
    if namedMatch is not None:
        existingLayer = namedMatch.group(1).strip()
        tail = rest[namedMatch.end() :]
        return f"{prefix} layer({layerName}.{existingLayer}){tail}"

    anonMatch = _IMPORT_LAYER_ANON_RE.match(rest)
    if anonMatch is not None:
        tail = rest[anonMatch.end() :]
        return f"{prefix} layer({layerName}){tail}"

    if rest == ";":
        return f"{prefix} layer({layerName});"
    return f"{prefix} layer({layerName}) {rest}"


def _extractLeadingImports(cssText: str) -> tuple[list[str], str]:
    """Hoist any leading @import rules from CSS so they remain valid before layer blocks."""
    imports: list[str] = []
    pos = 0
    lastImportEnd = 0
    length = len(cssText)

    while pos < length:
        if cssText[pos].isspace():
            pos += 1
            continue
        if cssText.startswith("/*", pos):
            endComment = cssText.find("*/", pos + 2)
            if endComment == -1:
                break
            pos = endComment + 2
            continue
        lowered = cssText[pos : pos + 9].lower()
        if lowered.startswith("@charset"):
            pos = _scanCssRuleEnd(cssText, pos)
            lastImportEnd = pos
            continue
        if lowered.startswith("@import") and (pos + 7 == length or not cssText[pos + 7].isalnum()):
            ruleEnd = _scanCssRuleEnd(cssText, pos)
            imports.append(cssText[pos:ruleEnd].strip())
            pos = ruleEnd
            lastImportEnd = pos
            continue
        break

    remaining = cssText[lastImportEnd:].lstrip("\r\n") if lastImportEnd > 0 else cssText
    return (imports, remaining)


def _extractLayerBlocks(
    blocks: t.Sequence[str],
    layerName: str,
) -> tuple[list[str], str]:
    """Extract layer-scoped @imports and combined CSS body from a sequence of CSS blocks."""
    scopedImports: list[str] = []
    bodies: list[str] = []
    for block in blocks:
        imports, body = _extractLeadingImports(block)
        scopedImports.extend(_scopeImportToLayer(rule, layerName) for rule in imports)
        if body.strip():
            bodies.append(body.strip())
    return (scopedImports, "\n\n".join(bodies))


def _buildStoryPageCss(
    archive: Archive,
    workBlocks: t.Sequence[str],
    chapterScopes: t.Sequence[tuple[str, t.Sequence[str]]] = (),
) -> str:
    """Assemble layered, @scope-wrapped CSS for a story or chapter page, or empty string if none."""
    allImports: list[str] = []
    layerSections: list[str] = []

    workImports, workBody = _extractLayerBlocks(workBlocks, "work")
    allImports.extend(workImports)
    if workBody:
        layerSections.append(_wrapLayerBlock("work", _wrapScopedBlock("article.work", workBody)))

    chapterScopeBlocks: list[str] = []
    for scopeSelector, blocks in chapterScopes:
        chImports, chBody = _extractLayerBlocks(blocks, "chapter")
        allImports.extend(chImports)
        if chBody:
            chapterScopeBlocks.append(_wrapScopedBlock(scopeSelector, chBody))
    if chapterScopeBlocks:
        layerSections.append(_wrapLayerBlock("chapter", "\n\n".join(chapterScopeBlocks)))

    if not allImports and not layerSections:
        return ""

    layerDecl = _LAYER_ORDER_REPLACE if archive.config.customCssMode == "replace" else _LAYER_ORDER_FULL
    header = layerDecl if not allImports else layerDecl + "\n" + "\n".join(allImports)
    assembled = "\n\n".join([header, *layerSections])
    return _STYLE_CLOSE_RE.sub(r"<\/\1", assembled)


def _copyStylesheet(outputDir: Path, config: SiteConfig, written: list[Path]) -> None:
    """Write the archive's style.css, applying font settings and any custom site stylesheet."""
    outCssPath = outputDir / "style.css"
    customCssPath = config.customCssPath

    if customCssPath is not None:
        if not customCssPath.is_file():
            msg = f"Custom CSS file does not exist: {customCssPath}"
            raise StoryParseError(msg)
        if customCssPath.resolve() == outCssPath.resolve():
            msg = (
                f"Custom CSS path cannot be the same as the generated output stylesheet: {outCssPath} "
                + "(use a separate source file such as site.css)."
            )
            raise StoryParseError(msg)
        try:
            customCss = customCssPath.read_text(encoding="utf-8")
        except OSError as exc:
            msg = f"Failed to read custom CSS file {customCssPath}: {exc}"
            raise StoryParseError(msg) from exc

        siteImports, siteBody = _extractLayerBlocks((customCss,), "site")
        if config.customCssMode == "replace":
            header = _LAYER_ORDER_REPLACE if not siteImports else _LAYER_ORDER_REPLACE + "\n" + "\n".join(siteImports)
            chunks = [header]
            if siteBody:
                chunks.append(_wrapLayerBlock("site", siteBody))
            _writeTextFile(outCssPath, "\n\n".join(chunks) + "\n", written)
            return

        packagedCss = resources.files("ficgen").joinpath("static/style.css").read_text(encoding="utf-8")
        baseCss = _applyFontConfig(packagedCss, config).rstrip()
        chunks = []
        if siteImports:
            chunks.append(_LAYER_ORDER_FULL + "\n" + "\n".join(siteImports))
            baseCss = _LAYER_ORDER_LINE_RE.sub("", baseCss, count=1)
        chunks.append(baseCss)
        if siteBody:
            chunks.append(_wrapLayerBlock("site", siteBody))
        _writeTextFile(outCssPath, "\n\n".join(chunks) + "\n", written)
        return

    if config.customCssMode == "replace":
        msg = "Custom CSS mode is 'replace', but no custom CSS file was provided."
        raise StoryParseError(msg)

    cssText = resources.files("ficgen").joinpath("static/style.css").read_text(encoding="utf-8")
    renderedCss = _applyFontConfig(cssText, config)
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
        pageCss=_buildStoryPageCss(archive, story.cssBlocks),
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

    tocTemplate = env.get_template("story_toc.html")
    tocHtml = tocTemplate.render(
        archive=archive,
        story=story,
        rootPrefix="../",
        pageTitle=f"{story.title} — {archive.config.title}",
        metaDescription=story.summaryText,
        activeNav="",
        bodyClass="page-story page-story-toc",
        pageCss=_buildStoryPageCss(archive, story.cssBlocks),
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
            pageCss=_buildStoryPageCss(archive, story.cssBlocks, (("article.work", chapter.cssBlocks),)),
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
        pageCss=_buildStoryPageCss(
            archive,
            story.cssBlocks,
            tuple((f"#ch-{chapter.number}", chapter.cssBlocks) for chapter in chapters),
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

    _copyStylesheet(outputDir, archive.config, written)

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
