"""Integration tests for static site generation, link integrity, Atom feed, and CLI."""

from __future__ import annotations

import logging
from html.parser import HTMLParser
from pathlib import Path
from xml.parsers import expat

import pytest

from ficgen import t
from ficgen.cli import buildConfigFromArgs, main
from ficgen.generator import generateSite
from ficgen.models import SiteConfig
from ficgen.parser import loadArchive

_ATOM_NS: t.Final[str] = "http://www.w3.org/2005/Atom"


class _LinkCollector(HTMLParser):
    """Collect relative href values from <a> and <link> elements."""

    def __init__(self) -> None:
        super().__init__()
        self.hrefs: list[str] = []

    @t.override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in {"a", "link"}:
            for name, value in attrs:
                if name == "href" and value is not None:
                    self.hrefs.append(value)


def _parseAtomFeed(xmlText: str) -> tuple[str, list[str]]:
    """Validate XML well-formedness via expat and return (rootTag, entryTitles)."""
    rootTag = ""
    stack: list[str] = []
    entryTitles: list[str] = []
    titleChunks: list[str] = []
    inEntryTitle = False

    def _onStart(name: str, _attrs: dict[str, str]) -> None:
        nonlocal rootTag, inEntryTitle
        if not rootTag:
            rootTag = name
        stack.append(name)
        if len(stack) >= 2 and stack[-2] == f"{_ATOM_NS} entry" and name == f"{_ATOM_NS} title":
            inEntryTitle = True
            titleChunks.clear()

    def _onData(data: str) -> None:
        if inEntryTitle:
            titleChunks.append(data)

    def _onEnd(_name: str) -> None:
        nonlocal inEntryTitle
        if inEntryTitle:
            entryTitles.append("".join(titleChunks).strip())
            inEntryTitle = False
        stack.pop()

    parser = expat.ParserCreate(namespace_separator=" ")
    parser.StartElementHandler = _onStart
    parser.CharacterDataHandler = _onData
    parser.EndElementHandler = _onEnd
    parser.Parse(xmlText, True)
    return (rootTag, entryTitles)


def _assertAllInternalLinksResolve(outputDir: Path) -> None:
    """Verify every relative href in every generated HTML file resolves to a real file."""
    htmlFiles = list(outputDir.rglob("*.html"))
    assert htmlFiles, "Expected generated HTML files"

    for htmlFile in htmlFiles:
        collector = _LinkCollector()
        collector.feed(htmlFile.read_text(encoding="utf-8"))
        for href in collector.hrefs:
            if href.startswith(("http://", "https://", "mailto:", "#")):
                continue
            targetPart = href.split("#", 1)[0]
            if not targetPart:
                continue
            resolved = (htmlFile.parent / targetPart).resolve()
            if resolved.is_dir():
                resolved = resolved / "index.html"
            assert resolved.is_file(), f"Broken link {href!r} in {htmlFile.relative_to(outputDir)}"


def _createSampleStories(storiesDir: Path) -> Path:
    """Create a self-contained set of sample single-doc and multi-chapter stories."""
    # Multi-chapter story with chat bubbles, notes, and a draft ch2.
    driftDir = storiesDir / "starlight-drift"
    driftDir.mkdir(parents=True)
    (driftDir / "story.kdl").write_text(
        'title "Starlight Drift"\n'
        + 'fandoms "Original Work"\n'
        + 'categories "F/F"\n'
        + 'characters "Nadia" "Kira"\n'
        + 'relationships "Nadia/Kira"\n'
        + 'tags "Sci-Fi" "Slow Burn"\n'
        + 'summary "A quiet drift between stations."\n',
        encoding="utf-8",
    )
    (driftDir / "ch1.md").write_text(
        "```metadata\n"
        + "chapter 1\n"
        + 'date "2026-03-11"\n'
        + "```\n\n"
        + "<notes>\nSoundtrack: https://example.com/track1\n</notes>\n\n"
        + "Docking bay lights hummed.\n\n"
        + "\\<On my way back now.|\n\n"
        + "|See you soon.>\n",
        encoding="utf-8",
    )
    (driftDir / "ch2.md").write_text(
        "```metadata\n" + "chapter 2\n" + "published false\n" + "```\n\n" + "|Did you make it back safe?>\n",
        encoding="utf-8",
    )

    # Single-doc story with endnotes, smart typography, and an auto-linked URL.
    rainDir = storiesDir / "midnight-rain"
    rainDir.mkdir(parents=True)
    (rainDir / "index.md").write_text(
        "# Midnight Rain\n\n"
        + "```metadata\n"
        + 'fandoms "Original Work"\n'
        + 'categories "F/F"\n'
        + 'tags "Rain" "Yearning"\n'
        + 'date "2026-08-02"\n'
        + "```\n\n"
        + "<endnotes>\nPlaylist: https://example.com/rain\n</endnotes>\n\n"
        + '"During all the quiet hours--"\n\n'
        + '"...We never finished that song."\n',
        encoding="utf-8",
    )

    # Second single-doc story.
    lanternDir = storiesDir / "paper-lanterns"
    lanternDir.mkdir(parents=True)
    (lanternDir / "index.md").write_text(
        "# Paper Lanterns\n\n"
        + "```metadata\n"
        + 'fandoms "Original Work"\n'
        + 'categories "F/F"\n'
        + 'tags "Festival"\n'
        + 'date "2026-06-15"\n'
        + "```\n\n"
        + "Warm paper glowed along the river.\n",
        encoding="utf-8",
    )

    return storiesDir


def test_generate_site_from_sample_stories(tmp_path: Path) -> None:
    """End-to-end build creates valid HTML, taxonomy pages, chat bubbles, and Atom feed."""
    storiesDir = _createSampleStories(tmp_path / "_stories")
    outDir = tmp_path / "out"
    config = SiteConfig(
        storiesDir=storiesDir,
        outputDir=outDir,
        title="Test Archive",
        author="Alex Rivera",
        baseUrl="https://example.com/fic/",
        includeDrafts=False,
    )
    archive = loadArchive(config)
    result = generateSite(archive)

    assert result.storyCount == 3
    assert result.chapterPageCount == 1
    assert result.feedEntryCount == 3
    assert (outDir / "index.html").is_file()
    assert (outDir / "style.css").is_file()
    assert (outDir / "feed.xml").is_file()
    assert (outDir / "browse" / "index.html").is_file()

    # Multi-chapter outputs (ch2 is published false).
    assert (outDir / "starlight-drift" / "index.html").is_file()
    assert (outDir / "starlight-drift" / "ch1.html").is_file()
    assert (outDir / "starlight-drift" / "all.html").is_file()
    assert not (outDir / "starlight-drift" / "ch2.html").exists()

    # Single-doc outputs.
    assert (outDir / "midnight-rain" / "index.html").is_file()
    assert (outDir / "paper-lanterns" / "index.html").is_file()

    # Verify chat bubbles & notes rendered in ch1.
    ch1Html = (outDir / "starlight-drift" / "ch1.html").read_text(encoding="utf-8")
    assert 'class="chat"' in ch1Html
    assert 'class="chat-msg chat-left"' in ch1Html
    assert 'class="chat-msg chat-right"' in ch1Html
    assert "https://example.com/track1" in ch1Html

    # Verify endnotes & smart typography in midnight-rain.
    rainHtml = (outDir / "midnight-rain" / "index.html").read_text(encoding="utf-8")
    assert 'id="endnotes"' in rainHtml
    assert '<a href="https://example.com/rain">https://example.com/rain</a>' in rainHtml
    assert "hours\u2014" in rainHtml
    assert "\u201c\u2026We never" in rainHtml

    # Verify static taxonomy pages exist and list matching stories.
    ffPage = outDir / "browse" / "categories" / "f-f.html"
    assert ffPage.is_file()
    ffHtml = ffPage.read_text(encoding="utf-8")
    assert "Starlight Drift" in ffHtml
    assert "Midnight Rain" in ffHtml
    assert "Paper Lanterns" in ffHtml

    # Verify Atom feed parses cleanly and contains all 3 entries.
    rootTag, titles = _parseAtomFeed((outDir / "feed.xml").read_text(encoding="utf-8"))
    assert rootTag == f"{_ATOM_NS} feed"
    assert set(titles) == {
        "Starlight Drift \u2014 Chapter 1",
        "Midnight Rain",
        "Paper Lanterns",
    }

    _assertAllInternalLinksResolve(outDir)


def test_draft_toggle_cleans_stale_chapter_pages(tmp_path: Path) -> None:
    """Rebuilding without --drafts removes stale unpublished chapter HTML files."""
    storiesDir = _createSampleStories(tmp_path / "_stories")
    outDir = tmp_path / "out"

    draftExit = main(
        [
            "--stories",
            str(storiesDir),
            "--output",
            str(outDir),
            "--drafts",
        ],
    )
    assert draftExit == 0
    assert (outDir / "starlight-drift" / "ch2.html").is_file()
    ch2Html = (outDir / "starlight-drift" / "ch2.html").read_text(encoding="utf-8")
    assert "Draft" in ch2Html
    assert "Did you make it back safe?" in ch2Html
    _assertAllInternalLinksResolve(outDir)

    pubExit = main(
        [
            "--stories",
            str(storiesDir),
            "--output",
            str(outDir),
        ],
    )
    assert pubExit == 0
    assert not (outDir / "starlight-drift" / "ch2.html").exists()
    _assertAllInternalLinksResolve(outDir)


def test_cli_reads_kdl_config_and_handles_missing_stories_dir(tmp_path: Path) -> None:
    """CLI applies KDL site settings and returns exit code 1 for missing story paths."""
    storiesDir = _createSampleStories(tmp_path / "_stories")
    cfgPath = tmp_path / "site.kdl"
    cfgPath.write_text(
        'title "Custom Archive"\nsubtitle "Short stories"\nauthor "Writer"\n',
        encoding="utf-8",
    )
    outDir = tmp_path / "out"
    code = main(
        [
            "--stories",
            str(storiesDir),
            "--output",
            str(outDir),
            "--config",
            str(cfgPath),
        ],
    )
    assert code == 0
    indexHtml = (outDir / "index.html").read_text(encoding="utf-8")
    assert "Custom Archive" in indexHtml
    assert "Short stories" in indexHtml

    errCode = main(["--stories", str(tmp_path / "does-not-exist"), "--output", str(outDir)])
    assert errCode == 1


def test_published_and_updated_date_labels(tmp_path: Path) -> None:
    """Single-date stories show Published only; multi-date stories show both Published and Updated."""
    storiesDir = tmp_path / "_stories"

    oneShot = storiesDir / "one-shot"
    oneShot.mkdir(parents=True)
    (oneShot / "index.md").write_text(
        '# Single Date\n\n```metadata\ndate "2026-09-01"\n```\n\nBody.\n',
        encoding="utf-8",
    )

    multi = storiesDir / "multi"
    multi.mkdir(parents=True)
    (multi / "story.kdl").write_text('title "Multi Date"\nfandoms "Test"\n', encoding="utf-8")
    (multi / "ch1.md").write_text('```metadata\nchapter 1\ndate "2026-09-10"\n```\n\nOne.\n', encoding="utf-8")
    (multi / "ch2.md").write_text('```metadata\nchapter 2\ndate "2026-10-15"\n```\n\nTwo.\n', encoding="utf-8")

    outDir = tmp_path / "out"
    config = SiteConfig(storiesDir=storiesDir, outputDir=outDir)
    generateSite(loadArchive(config))

    oneShotHtml = (outDir / "one-shot" / "index.html").read_text(encoding="utf-8")
    assert "<strong>Published:</strong>" in oneShotHtml
    assert "2026-09-01" in oneShotHtml
    assert "<strong>Updated:</strong>" not in oneShotHtml

    multiHtml = (outDir / "multi" / "index.html").read_text(encoding="utf-8")
    assert "<strong>Published:</strong>" in multiHtml
    assert "2026-09-10" in multiHtml
    assert "<strong>Updated:</strong>" in multiHtml
    assert "2026-10-15" in multiHtml

    indexHtml = (outDir / "index.html").read_text(encoding="utf-8")
    assert 'Published <time datetime="2026-09-01">2026-09-01</time>' in indexHtml
    assert 'Published <time datetime="2026-09-10">2026-09-10</time>' in indexHtml
    assert 'Updated <time datetime="2026-10-15">2026-10-15</time>' in indexHtml


def test_build_warns_on_undated_published_stories_and_chapters(
    tmp_path: Path,
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Published stories/chapters missing Date: log warnings, while drafts do not."""
    storiesDir = tmp_path / "_stories"

    dated = storiesDir / "dated"
    dated.mkdir(parents=True)
    (dated / "index.md").write_text('# Dated\n\n```metadata\ndate "2026-09-01"\n```\n\nBody.\n', encoding="utf-8")

    undated = storiesDir / "undated"
    undated.mkdir(parents=True)
    (undated / "index.md").write_text('# Undated\n\n```metadata\ntags "test"\n```\n\nBody.\n', encoding="utf-8")

    multi = storiesDir / "multi"
    multi.mkdir(parents=True)
    (multi / "story.kdl").write_text('title "Multi"\nfandoms "Test"\n', encoding="utf-8")
    (multi / "ch1.md").write_text("```metadata\nchapter 1\n```\n\nPublished undated.\n", encoding="utf-8")
    (multi / "ch2.md").write_text(
        "```metadata\nchapter 2\npublished false\n```\n\nDraft undated.\n",
        encoding="utf-8",
    )

    outDir = tmp_path / "out"
    with caplog.at_level(logging.WARNING, logger="ficgen"):
        exitCode = main(["--stories", str(storiesDir), "--output", str(outDir), "--drafts"])
    assert exitCode == 0

    messages = [record.getMessage() for record in caplog.records if record.levelno == logging.WARNING]
    assert any("Undated is published without a Date:" in msg for msg in messages)
    assert any("Multi (Chapter 1) is published without a Date:" in msg for msg in messages)
    assert not any("Chapter 2" in msg for msg in messages)
    assert not any("Dated is published" in msg for msg in messages)
    assert any("ficgen stamp" in msg for msg in messages)

    # Once all published stories/chapters have Date:, no warnings are logged.
    assert main(["stamp", "--stories", str(storiesDir), "--date", "2026-11-24"]) == 0
    caplog.clear()
    with caplog.at_level(logging.WARNING, logger="ficgen"):
        assert main(["--stories", str(storiesDir), "--output", str(outDir), "--drafts"]) == 0
    assert caplog.records == []


def test_default_stories_dir_resolution_and_cli_workflow(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Default source directory is `_stories` for SiteConfig and all CLI subcommands."""
    assert SiteConfig().storiesDir == Path("_stories")

    # Even if a legacy/output `stories/` directory exists, defaults still resolve to `_stories/`.
    (tmp_path / "stories").mkdir()
    monkeypatch.chdir(tmp_path)
    initialConfig = buildConfigFromArgs([])
    assert initialConfig.storiesDir == tmp_path / "_stories"
    assert initialConfig.outputDir == tmp_path

    # `ficgen new`, `ficgen stamp`, and `ficgen` (build) use `./_stories` by default.
    assert main(["new", "Default Folder Tale", "--published"]) == 0
    storyFile = tmp_path / "_stories" / "default-folder-tale" / "index.md"
    assert storyFile.is_file()

    assert main(["stamp", "--force", "--date", "2026-11-26"]) == 0
    assert 'date "2026-11-26"' in storyFile.read_text(encoding="utf-8")

    # Resolving from a subdirectory finds `../_stories` and outputs to parent.
    subDir = tmp_path / "subdir"
    subDir.mkdir()
    monkeypatch.chdir(subDir)
    subConfig = buildConfigFromArgs([])
    assert subConfig.storiesDir == tmp_path / "_stories"
    assert subConfig.outputDir == tmp_path

    assert main([]) == 0
    assert (tmp_path / "index.html").is_file()
    assert (tmp_path / "default-folder-tale" / "index.html").is_file()


def test_site_kdl_configures_stories_dir_across_cli_commands(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """`site.kdl` can configure the stories directory for build, new, and stamp, overridable via CLI."""
    siteDir = tmp_path / "site"
    siteDir.mkdir()
    cfgPath = siteDir / "site.kdl"
    cfgPath.write_text(
        'title "Configured Archive"\nstories "fics"\n',
        encoding="utf-8",
    )

    monkeypatch.chdir(siteDir)
    cfg = buildConfigFromArgs([])
    assert cfg.storiesDir == siteDir / "fics"
    assert cfg.outputDir == siteDir

    # `new`, `stamp`, and `build` all use the `site.kdl` stories directory.
    assert main(["new", "Configured Tale", "--published"]) == 0
    storyFile = siteDir / "fics" / "configured-tale" / "index.md"
    assert storyFile.is_file()
    assert not (siteDir / "_stories").exists()

    assert main(["stamp", "--force", "--date", "2026-11-26"]) == 0
    assert 'date "2026-11-26"' in storyFile.read_text(encoding="utf-8")

    # Running from a subdirectory finds ../site.kdl and resolves `stories` relative to site.kdl.
    monkeypatch.chdir(siteDir / "fics")
    subCfg = buildConfigFromArgs([])
    assert subCfg.storiesDir == siteDir / "fics"
    assert subCfg.outputDir == siteDir

    assert main([]) == 0
    assert "Configured Archive" in (siteDir / "index.html").read_text(encoding="utf-8")
    assert (siteDir / "configured-tale" / "index.html").is_file()

    # Explicit --config from an unrelated directory resolves relative `stories` against site.kdl.
    otherDir = tmp_path / "elsewhere"
    otherDir.mkdir()
    monkeypatch.chdir(otherDir)
    explicitCfg = buildConfigFromArgs(["--config", str(cfgPath)])
    assert explicitCfg.storiesDir == siteDir / "fics"

    # Explicit --stories overrides site.kdl while retaining other site.kdl values.
    overrideDir = tmp_path / "override"
    overrideCfg = buildConfigFromArgs(["--config", str(cfgPath), "--stories", str(overrideDir)])
    assert overrideCfg.storiesDir == overrideDir
    assert overrideCfg.title == "Configured Archive"

    # `stories-dir` alias and absolute paths are supported.
    absStoriesDir = tmp_path / "abs-stories"
    altCfgPath = tmp_path / "alt.kdl"
    altCfgPath.write_text(f'stories-dir "{absStoriesDir.as_posix()}"\n', encoding="utf-8")
    altCfg = buildConfigFromArgs(["--config", str(altCfgPath)])
    assert altCfg.storiesDir == absStoriesDir

    # Non-string stories value in site.kdl fails cleanly.
    badCfgPath = tmp_path / "bad.kdl"
    badCfgPath.write_text("stories 123\n", encoding="utf-8")
    assert main(["--config", str(badCfgPath)]) == 1
