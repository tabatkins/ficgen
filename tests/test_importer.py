"""Tests for HTML-to-Markdown integration and the `ficgen import` command."""

from __future__ import annotations

import io
import zipfile
from collections.abc import Mapping
from pathlib import Path

import ao3fetch
import pytest

from ficgen.cli import main, mergeCookieHeader
from ficgen.generator import generateSite
from ficgen.importer import htmlToMarkdown
from ficgen.markdownExt import MarkdownRenderer
from ficgen.models import SiteConfig
from ficgen.parser import loadArchive

_WORK_SINGLE_HTML = """<!DOCTYPE html>
<html><body>
<div id="workskin">
  <div class="preface group">
    <h2 class="title heading">and she burned the sky</h2>
    <h3 class="byline heading"><a rel="author" href="/users/melo/pseuds/melo">melo</a></h3>
    <div class="summary module"><blockquote class="userstuff"><p>Life after the wolves.</p></blockquote></div>
    <div class="notes module"><blockquote class="userstuff"><p>Work start note.</p></blockquote></div>
  </div>
  <dl class="work meta group">
    <dd class="rating tags"><a class="tag">Teen And Up Audiences</a></dd>
    <dd class="warning tags"><a class="tag">Creator Chose Not To Use Archive Warnings</a></dd>
    <dd class="category tags"><a class="tag">F/F</a></dd>
    <dd class="fandom tags"><a class="tag">Batgirl</a><a class="tag">Teen Titans</a></dd>
    <dd class="relationship tags"><a class="tag">Cassandra Cain/Rose Wilson</a></dd>
    <dd class="character tags"><a class="tag">Cassandra Cain</a><a class="tag">Rose Wilson</a></dd>
    <dd class="freeform tags"><ul class="commas"><li><a class="tag">Moving On</a></li></ul></dd>
    <dd class="language">English</dd>
    <dd class="stats"><dl class="stats">
      <dd class="published">2025-08-23</dd>
      <dd class="words">500</dd>
      <dd class="chapters">1/1</dd>
    </dl></dd>
  </dl>
  <div id="chapters">
    <div class="userstuff module" role="article">
      <h3 class="landmark heading">Work Text</h3>
      <p>The sky was <em>bright</em>.</p>
      <hr />
      <p>Then it faded.</p>
    </div>
  </div>
  <div id="work_endnotes"><div class="userstuff"><p>Work end note.</p></div></div>
</div>
</body></html>
"""

_WORK_MULTI_FULL_HTML = """<!DOCTYPE html>
<html><body>
<div id="workskin">
  <div class="preface group">
    <h2 class="title heading">Two Parts</h2>
    <h3 class="byline heading"><a rel="author" href="/users/alice/pseuds/ally">ally</a></h3>
    <div class="summary module"><blockquote class="userstuff"><p>Main summary.</p></blockquote></div>
    <div class="notes module"><blockquote class="userstuff"><p>Global start note.</p></blockquote></div>
  </div>
  <dl class="work meta group">
    <dd class="rating tags"><a class="tag">General Audiences</a></dd>
    <dd class="warning tags"><a class="tag">No Archive Warnings Apply</a></dd>
    <dd class="category tags"><a class="tag">F/F</a></dd>
    <dd class="fandom tags"><a class="tag">Original Work</a></dd>
    <dd class="language">English</dd>
    <dd class="stats"><dl class="stats">
      <dd class="published">2026-01-10</dd>
      <dd class="status">2026-02-15</dd>
      <dd class="words">2,100</dd>
      <dd class="chapters">2/?</dd>
    </dl></dd>
  </dl>
  <div id="chapters">
    <div class="chapter" id="chapter-1">
      <div class="chapter preface group">
        <h3 class="title"><a href="/works/555/chapters/1001">Chapter 1</a>: Dawn</h3>
      </div>
      <div class="userstuff module" role="article">
        <h3 class="landmark heading">Chapter Text</h3>
        <p>First chapter body.</p>
      </div>
    </div>
    <div class="chapter" id="chapter-2">
      <div class="chapter preface group">
        <h3 class="title"><a href="/works/555/chapters/1002">Chapter 2</a></h3>
      </div>
      <div class="userstuff module" role="article">
        <h3 class="landmark heading">Chapter Text</h3>
        <p>Second chapter body.</p>
      </div>
    </div>
  </div>
  <div id="work_endnotes"><div class="userstuff"><p>Global end note.</p></div></div>
</div>
</body></html>
"""

_WORK_NAVIGATE_HTML = """<!DOCTYPE html>
<html><body>
<div class="works-navigate">
  <h2><a href="/works/555">Two Parts</a> by <a rel="author" href="/users/alice/pseuds/ally">ally</a></h2>
  <ol class="chapter index group">
    <li><a href="/works/555/chapters/1001">1. Dawn</a> <span class="datetime">(2026-01-10)</span></li>
    <li><a href="/works/555/chapters/1002">2. Chapter 2</a> <span class="datetime">(2026-02-15)</span></li>
  </ol>
</div>
</body></html>
"""

_WORK_LOCKED_HTML = '<html><body><div id="signin"><h3 class="heading">Log In</h3></div></body></html>'


def _makeRouteFetcher(routes: dict[str, tuple[int, str]]) -> ao3fetch.Fetcher:
    def _fetch(url: str, _headers: Mapping[str, str] | None = None) -> ao3fetch.FetchResponse:
        if url in routes:
            status, body = routes[url]
            return ao3fetch.FetchResponse(status=status, headers={}, body=body.encode("utf-8"), url=url)
        return ao3fetch.FetchResponse(status=404, headers={}, body=b"Not Found", url=url)

    return _fetch


def _buildTestEpub() -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr(
            "META-INF/container.xml",
            '<?xml version="1.0"?><container><rootfiles><rootfile full-path="OEBPS/content.opf"/></rootfiles></container>',
        )
        zf.writestr(
            "OEBPS/content.opf",
            '<?xml version="1.0"?><package><metadata><dc:language>en</dc:language></metadata>'
            + '<manifest><item id="c1" href="text/ch.xhtml" media-type="application/xhtml+xml"/></manifest>'
            + '<spine><itemref idref="c1"/></spine></package>',
        )
        zf.writestr(
            "OEBPS/text/ch.xhtml",
            "<html><body>"
            + '<div id="preface">'
            + '<p class="message">Posted originally on the <a href="https://archiveofourown.org/works/999">AO3</a></p>'
            + "<h1>EPUB Story</h1>"
            + '<div class="byline">by <a rel="author" href="https://archiveofourown.org/users/u1/pseuds/p1">p1</a></div>'
            + '<dl class="tags">'
            + "<dt>Rating:</dt><dd><a>General Audiences</a></dd>"
            + "<dt>Archive Warning:</dt><dd><a>Choose Not To Use Archive Warnings</a></dd>"
            + "<dt>Category:</dt><dd><a>F/F</a></dd>"
            + "<dt>Fandom:</dt><dd><a>Test Fandom</a></dd>"
            + "<dt>Stats:</dt><dd>Published: 2026-05-01 Words: 42</dd>"
            + "</dl>"
            + "</div>"
            + '<div id="chapters"><div><h2>EPUB Story</h2></div><div><p>Hello world.</p></div></div>'
            + "</body></html>",
        )
    return buf.getvalue()


def test_html_to_markdown_roundtrips_with_ficgen_renderer() -> None:
    """htmlToMarkdown delegates to ao3fetch.toMarkdown and round-trips headings cleanly with MarkdownRenderer."""
    renderer = MarkdownRenderer()
    h2Md = htmlToMarkdown("<p>Waking<br />\n------</p>\n<p>Ithari wakes.</p>")
    assert h2Md == "Waking\n------\n\nIthari wakes."
    h2Html = renderer.render(h2Md)
    assert "<h2>Waking</h2>" in h2Html
    assert "<hr" not in h2Html


def test_import_single_doc_from_superlove_and_build(tmp_path: Path) -> None:
    """`ficgen import` imports a one-shot from an AO3-compatible archive and builds cleanly."""
    routes = {
        "https://superlove.sayitditto.net/works/1269?view_full_work=true&view_adult=true": (200, _WORK_SINGLE_HTML),
    }
    storiesDir = tmp_path / "_stories"
    outDir = tmp_path / "out"

    ao3fetch.setFetcher(_makeRouteFetcher(routes))
    try:
        code = main(["import", "https://superlove.sayitditto.net/works/1269", "--stories", str(storiesDir)])
    finally:
        ao3fetch.resetFetcher()
        ao3fetch.resetArchiveBaseUrl()

    assert code == 0
    indexMd = storiesDir / "and-she-burned-the-sky" / "index.md"
    assert indexMd.is_file()
    raw = indexMd.read_text(encoding="utf-8")
    assert "# and she burned the sky" in raw
    assert 'fandoms "Batgirl" "Teen Titans"' in raw
    assert 'date "2025-08-23"' in raw
    assert "<summary>" in raw
    assert "<notes>" in raw
    assert "<endnotes>" in raw
    assert "The sky was _bright_." in raw

    # Round-trip through ficgen parser and static generator.
    archive = loadArchive(SiteConfig(storiesDir=storiesDir, outputDir=outDir))
    assert len(archive.stories) == 1
    assert archive.stories[0].title == "and she burned the sky"
    assert archive.stories[0].date.strftime("%Y-%m-%d") == "2025-08-23"
    generateSite(archive)
    assert (outDir / "and-she-burned-the-sky" / "index.html").is_file()


def test_import_multichapter_work_creates_story_kdl_and_chapter_dates(tmp_path: Path) -> None:
    """`ficgen import` imports multi-chapter works into story.kdl + chN.md with /navigate dates."""
    routes = {
        "https://archiveofourown.org/works/555?view_full_work=true&view_adult=true": (200, _WORK_MULTI_FULL_HTML),
        "https://archiveofourown.org/works/555/navigate": (200, _WORK_NAVIGATE_HTML),
    }
    storiesDir = tmp_path / "_stories"
    outDir = tmp_path / "out"

    ao3fetch.setFetcher(_makeRouteFetcher(routes))
    try:
        code = main(["import", "https://archiveofourown.org/works/555", "--stories", str(storiesDir)])
    finally:
        ao3fetch.resetFetcher()
        ao3fetch.resetArchiveBaseUrl()

    assert code == 0
    storyDir = storiesDir / "two-parts"
    assert (storyDir / "story.kdl").is_file()
    assert (storyDir / "ch1.md").is_file()
    assert (storyDir / "ch2.md").is_file()
    assert not (storyDir / "index.md").exists()

    ch1Text = (storyDir / "ch1.md").read_text(encoding="utf-8")
    ch2Text = (storyDir / "ch2.md").read_text(encoding="utf-8")
    assert "# Dawn" in ch1Text
    assert 'date "2026-01-10"' in ch1Text
    assert 'date "2026-02-15"' in ch2Text

    archive = loadArchive(SiteConfig(storiesDir=storiesDir, outputDir=outDir))
    assert len(archive.stories) == 1
    story = archive.stories[0]
    assert story.isMultiChapter is True
    assert story.date.strftime("%Y-%m-%d") == "2026-01-10"
    assert story.updated.strftime("%Y-%m-%d") == "2026-02-15"
    assert story.chapters[0].title == "Dawn"
    assert story.chapters[1].title == ""
    generateSite(archive)
    assert (outDir / "two-parts" / "ch2.html").is_file()
    assert (outDir / "two-parts" / "all.html").is_file()


def test_import_local_epub_and_error_cases(tmp_path: Path) -> None:
    """`ficgen import` supports local EPUBs, rejects overwrites without --force, and fails on locked works."""
    epubPath = tmp_path / "work.epub"
    epubPath.write_bytes(_buildTestEpub())
    storiesDir = tmp_path / "_stories"

    assert main(["import", str(epubPath), "--stories", str(storiesDir), "--draft"]) == 0
    assert (storiesDir / "epub-story" / "index.md").is_file()
    assert "published false" in (storiesDir / "epub-story" / "index.md").read_text(encoding="utf-8")

    # Refuses overwrite without --force, succeeds with --force.
    assert main(["import", str(epubPath), "--stories", str(storiesDir)]) == 1
    assert main(["import", str(epubPath), "--stories", str(storiesDir), "--force"]) == 0

    # Locked work returns exit code 1.
    ao3fetch.setFetcher(
        _makeRouteFetcher(
            {"https://archiveofourown.org/works/9999?view_full_work=true&view_adult=true": (200, _WORK_LOCKED_HTML)},
        ),
    )
    try:
        assert main(["import", "9999", "--stories", str(storiesDir)]) == 1
    finally:
        ao3fetch.resetFetcher()
        ao3fetch.resetArchiveBaseUrl()


def test_import_defaults_to_underscore_stories_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ficgen import` writes into `./_stories` when `--stories` is omitted."""
    epubPath = tmp_path / "work.epub"
    epubPath.write_bytes(_buildTestEpub())
    monkeypatch.chdir(tmp_path)

    assert main(["import", str(epubPath)]) == 0
    assert (tmp_path / "_stories" / "epub-story" / "index.md").is_file()


def test_import_respects_site_kdl_stories_dir(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`ficgen import` uses the `stories` directory configured in `site.kdl`."""
    epubPath = tmp_path / "work.epub"
    epubPath.write_bytes(_buildTestEpub())
    (tmp_path / "site.kdl").write_text('stories "custom-fics"\n', encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert main(["import", str(epubPath)]) == 0
    assert (tmp_path / "custom-fics" / "epub-story" / "index.md").is_file()
    assert not (tmp_path / "_stories").exists()


def test_import_handles_missing_and_corrupt_epub_and_cookie_header_join(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """Missing or corrupt EPUBs fail cleanly and --cookie joins existing Cookie headers with '; '."""
    storiesDir = tmp_path / "_stories"

    assert main(["import", str(tmp_path / "missing.epub"), "--stories", str(storiesDir)]) == 1
    assert "does not exist" in capsys.readouterr().err

    badEpub = tmp_path / "corrupt.epub"
    badEpub.write_bytes(b"not a zip file")
    assert main(["import", str(badEpub), "--stories", str(storiesDir)]) == 1
    assert "Failed to parse EPUB" in capsys.readouterr().err

    seenCookies: list[str] = []

    def _recordingFetch(url: str, headers: Mapping[str, str] | None = None) -> ao3fetch.FetchResponse:
        seenCookies.append((headers or {}).get("Cookie", ""))
        return ao3fetch.FetchResponse(status=200, headers={}, body=_WORK_SINGLE_HTML.encode("utf-8"), url=url)

    ao3fetch.setFetcher(_recordingFetch)
    try:
        assert (
            main(
                [
                    "import",
                    "https://superlove.sayitditto.net/works/1269",
                    "--stories",
                    str(storiesDir),
                    "--cookie",
                    "session=abc123",
                ],
            )
            == 0
        )
    finally:
        ao3fetch.resetFetcher()
        ao3fetch.resetArchiveBaseUrl()
    assert seenCookies == ["view_adult=true; session=abc123"]

    assert mergeCookieHeader("view_adult=true", "session=abc123") == "view_adult=true; session=abc123"
    assert mergeCookieHeader("view_adult=true;", "session=abc123") == "view_adult=true; session=abc123"
    assert mergeCookieHeader("", "session=abc123") == "session=abc123"


def _makeWorkHtml(
    *,
    title: str,
    rating: str,
    warning: str = "No Archive Warnings Apply",
    freeformTags: tuple[str, ...] = (),
) -> str:
    tagItems = "".join(f'<li><a class="tag">{tag}</a></li>' for tag in freeformTags)
    freeformBlock = f'<dd class="freeform tags"><ul class="commas">{tagItems}</ul></dd>' if freeformTags else ""
    return (
        "<!DOCTYPE html><html><body><div id='workskin'>"
        + f'<div class="preface group"><h2 class="title heading">{title}</h2>'
        + '<h3 class="byline heading"><a rel="author" href="/users/a/pseuds/a">a</a></h3></div>'
        + '<dl class="work meta group">'
        + f'<dd class="rating tags"><a class="tag">{rating}</a></dd>'
        + f'<dd class="warning tags"><a class="tag">{warning}</a></dd>'
        + '<dd class="category tags"><a class="tag">F/F</a></dd>'
        + '<dd class="fandom tags"><a class="tag">Original Work</a></dd>'
        + freeformBlock
        + '<dd class="language">English</dd>'
        + '<dd class="stats"><dl class="stats"><dd class="published">2026-03-01</dd>'
        + '<dd class="words">100</dd><dd class="chapters">1/1</dd></dl></dd></dl>'
        + '<div id="chapters"><div class="userstuff module" role="article"><p>Prose.</p></div></div>'
        + "</div></body></html>"
    )


_WORK_CHAPTER_NSFW_HTML = """<!DOCTYPE html>
<html><body>
<div id="workskin">
  <div class="preface group">
    <h2 class="title heading">Mixed Chapters</h2>
    <h3 class="byline heading"><a rel="author" href="/users/a/pseuds/a">a</a></h3>
  </div>
  <dl class="work meta group">
    <dd class="rating tags"><a class="tag">Teen And Up Audiences</a></dd>
    <dd class="warning tags"><a class="tag">No Archive Warnings Apply</a></dd>
    <dd class="category tags"><a class="tag">F/F</a></dd>
    <dd class="fandom tags"><a class="tag">Original Work</a></dd>
    <dd class="language">English</dd>
    <dd class="stats"><dl class="stats">
      <dd class="published">2026-04-01</dd>
      <dd class="words">500</dd>
      <dd class="chapters">2/2</dd>
    </dl></dd>
  </dl>
  <div id="chapters">
    <div class="chapter" id="chapter-1">
      <div class="chapter preface group">
        <h3 class="title"><a href="/works/800/chapters/1">Chapter 1</a>: Calm</h3>
        <div id="notes" class="notes module"><blockquote class="userstuff"><p>No NSFW in this chapter.</p></blockquote></div>
      </div>
      <div class="userstuff module" role="article"><p>Safe chapter.</p></div>
    </div>
    <div class="chapter" id="chapter-2">
      <div class="chapter preface group">
        <h3 class="title"><a href="/works/800/chapters/2">Chapter 2</a>: Heat</h3>
        <div id="notes" class="notes module"><blockquote class="userstuff"><p>Warning: NSFW scene ahead.</p></blockquote></div>
      </div>
      <div class="userstuff module" role="article"><p>Spicy chapter.</p></div>
    </div>
  </div>
</div>
</body></html>
"""


def test_import_nsfw_detection_defaults_and_site_kdl_customization(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`ficgen import` detects NSFW ratings, warnings, tags, and chapters by default and honors `import-nsfw` in site.kdl."""
    routes = {
        "https://archiveofourown.org/works/101?view_full_work=true&view_adult=true": (
            200,
            _makeWorkHtml(title="Explicit Work", rating="Explicit"),
        ),
        "https://archiveofourown.org/works/102?view_full_work=true&view_adult=true": (
            200,
            _makeWorkHtml(title="Mature Work", rating="Mature"),
        ),
        "https://archiveofourown.org/works/103?view_full_work=true&view_adult=true": (
            200,
            _makeWorkHtml(title="Unrated Work", rating="Not Rated"),
        ),
        "https://archiveofourown.org/works/104?view_full_work=true&view_adult=true": (
            200,
            _makeWorkHtml(
                title="Violent Teen Work",
                rating="Teen And Up Audiences",
                warning="Graphic Depictions Of Violence",
            ),
        ),
        "https://archiveofourown.org/works/105?view_full_work=true&view_adult=true": (
            200,
            _makeWorkHtml(title="Tagged Smut", rating="General Audiences", freeformTags=("Fluff and Smut",)),
        ),
        "https://archiveofourown.org/works/106?view_full_work=true&view_adult=true": (
            200,
            _makeWorkHtml(title="Negated Tag", rating="General Audiences", freeformTags=("No Smut", "Not NSFW")),
        ),
        "https://archiveofourown.org/works/800?view_full_work=true&view_adult=true": (200, _WORK_CHAPTER_NSFW_HTML),
        "https://archiveofourown.org/works/800/navigate": (404, "Not Found"),
    }

    defaultStories = tmp_path / "default" / "_stories"
    ao3fetch.setFetcher(_makeRouteFetcher(routes))
    try:
        for workId in ("101", "102", "103", "104", "105", "106", "800"):
            assert main(["import", workId, "--stories", str(defaultStories)]) == 0
    finally:
        ao3fetch.resetFetcher()
        ao3fetch.resetArchiveBaseUrl()

    assert "nsfw true" in (defaultStories / "explicit-work" / "index.md").read_text(encoding="utf-8")
    assert "nsfw true" in (defaultStories / "mature-work" / "index.md").read_text(encoding="utf-8")
    assert "nsfw true" in (defaultStories / "unrated-work" / "index.md").read_text(encoding="utf-8")
    assert "nsfw true" in (defaultStories / "violent-teen-work" / "index.md").read_text(encoding="utf-8")
    assert "nsfw true" in (defaultStories / "tagged-smut" / "index.md").read_text(encoding="utf-8")
    assert "nsfw true" not in (defaultStories / "negated-tag" / "index.md").read_text(encoding="utf-8")

    # Multi-chapter SFW work only marks chapter 2 (not story.kdl and not chapter 1 with "No NSFW").
    assert "nsfw true" not in (defaultStories / "mixed-chapters" / "story.kdl").read_text(encoding="utf-8")
    assert "nsfw true" not in (defaultStories / "mixed-chapters" / "ch1.md").read_text(encoding="utf-8")
    assert "nsfw true" in (defaultStories / "mixed-chapters" / "ch2.md").read_text(encoding="utf-8")

    # Narrow `import-nsfw` in site.kdl: only Explicit ratings trigger NSFW.
    customSite = tmp_path / "custom"
    customSite.mkdir()
    cfgPath = customSite / "site.kdl"
    cfgPath.write_text(
        "import-nsfw {\n"
        + '    ratings "Explicit"\n'
        + "    warnings false\n"
        + "    tags false\n"
        + "    chapters false\n"
        + "}\n",
        encoding="utf-8",
    )
    customStories = customSite / "_stories"
    ao3fetch.setFetcher(_makeRouteFetcher(routes))
    try:
        for workId in ("101", "102", "103", "104", "105", "800"):
            assert main(["import", workId, "--stories", str(customStories), "--config", str(cfgPath)]) == 0
    finally:
        ao3fetch.resetFetcher()
        ao3fetch.resetArchiveBaseUrl()

    assert "nsfw true" in (customStories / "explicit-work" / "index.md").read_text(encoding="utf-8")
    assert "nsfw true" not in (customStories / "mature-work" / "index.md").read_text(encoding="utf-8")
    assert "nsfw true" not in (customStories / "unrated-work" / "index.md").read_text(encoding="utf-8")
    assert "nsfw true" not in (customStories / "violent-teen-work" / "index.md").read_text(encoding="utf-8")
    assert "nsfw true" not in (customStories / "tagged-smut" / "index.md").read_text(encoding="utf-8")
    assert "nsfw true" not in (customStories / "mixed-chapters" / "ch2.md").read_text(encoding="utf-8")

    # `import-nsfw false` disables all automatic NSFW marking.
    cfgPath.write_text("import-nsfw false\n", encoding="utf-8")
    ao3fetch.setFetcher(_makeRouteFetcher(routes))
    try:
        assert main(["import", "101", "--stories", str(customStories), "--config", str(cfgPath), "--force"]) == 0
    finally:
        ao3fetch.resetFetcher()
        ao3fetch.resetArchiveBaseUrl()
    assert "nsfw true" not in (customStories / "explicit-work" / "index.md").read_text(encoding="utf-8")

    # Invalid rating name in import-nsfw fails cleanly.
    cfgPath.write_text('import-nsfw {\n    ratings "Bogus Rating"\n}\n', encoding="utf-8")
    assert main(["import", "101", "--stories", str(customStories), "--config", str(cfgPath)]) == 1
    assert "Unknown AO3 ratings value" in capsys.readouterr().err
