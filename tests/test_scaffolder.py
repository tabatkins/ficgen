"""Tests for the `ficgen init` archive scaffolder and `ficgen new` story scaffolder."""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

from ficgen.cli import buildConfigFromArgs, main
from ficgen.generator import generateSite
from ficgen.kdlMeta import StoryParseError
from ficgen.models import SiteConfig
from ficgen.parser import loadArchive
from ficgen.scaffolder import InitOptions, initSite, initStory


def test_init_single_doc_story_defaults_to_draft_and_builds_cleanly(tmp_path: Path) -> None:
    """Single-doc scaffolding creates index.md with valid KDL draft metadata."""
    storiesDir = tmp_path / "_stories"
    result = initStory(
        InitOptions(
            storiesDir=storiesDir,
            title='Midnight "Rain"',
            fandoms=("Fandom One",),
            categories=("F/F",),
            tags=("fluff", "rain"),
        ),
    )

    assert result.slug == "midnight-rain"
    assert result.storyDir == storiesDir / "midnight-rain"
    assert result.createdFiles == (storiesDir / "midnight-rain" / "index.md",)

    text = (result.storyDir / "index.md").read_text(encoding="utf-8")
    assert '# Midnight "Rain"' in text
    assert 'fandoms "Fandom One"' in text
    assert 'tags "fluff" "rain"' in text
    assert "published false" in text

    # Excluded by default, loads and builds cleanly with includeDrafts=True.
    pubArchive = loadArchive(SiteConfig(storiesDir=storiesDir, outputDir=tmp_path / "out-pub"))
    assert not pubArchive.stories

    draftArchive = loadArchive(
        SiteConfig(storiesDir=storiesDir, outputDir=tmp_path / "out-draft", includeDrafts=True),
    )
    assert len(draftArchive.stories) == 1
    story = draftArchive.stories[0]
    assert story.title == 'Midnight "Rain"'
    assert story.fandoms == ("Fandom One",)
    assert story.tags == ("fluff", "rain")
    assert story.published is False
    generateSite(draftArchive)
    assert (tmp_path / "out-draft" / "midnight-rain" / "index.html").is_file()


def test_init_multi_chapter_story_creates_story_kdl_and_chapters(tmp_path: Path) -> None:
    """Multi-chapter scaffolding creates story.kdl and ch1.md..chN.md without index.md."""
    storiesDir = tmp_path / "_stories"
    result = initStory(
        InitOptions(
            storiesDir=storiesDir,
            title="Starlight Drift",
            multiChapter=True,
            chapterCount=2,
            published=True,
            dateValue="2026-11-25",
            fandoms=("Alpha", "Beta"),
            relationships=("A/B",),
        ),
    )

    assert result.slug == "starlight-drift"
    assert not (result.storyDir / "index.md").exists()
    assert (result.storyDir / "story.kdl").is_file()
    assert (result.storyDir / "ch1.md").is_file()
    assert (result.storyDir / "ch2.md").is_file()

    archive = loadArchive(SiteConfig(storiesDir=storiesDir, outputDir=tmp_path / "out"))
    assert len(archive.stories) == 1
    story = archive.stories[0]
    assert story.isMultiChapter is True
    assert story.title == "Starlight Drift"
    assert story.fandoms == ("Alpha", "Beta")
    assert story.relationships == ("A/B",)
    assert [ch.number for ch in story.chapters] == [1, 2]
    assert all(ch.published for ch in story.chapters)
    assert all(ch.date.strftime("%Y-%m-%d") == "2026-11-25" for ch in story.chapters)
    generateSite(archive)
    assert (tmp_path / "out" / "starlight-drift" / "ch2.html").is_file()


def test_init_rejects_duplicate_dir_and_bad_inputs(tmp_path: Path) -> None:
    """Scaffolding refuses to overwrite existing story directories or accept invalid slugs/counts."""
    storiesDir = tmp_path / "_stories"
    initStory(InitOptions(storiesDir=storiesDir, title="Unique Story"))

    with pytest.raises(StoryParseError, match="already exists"):
        initStory(InitOptions(storiesDir=storiesDir, title="Unique Story"))

    with pytest.raises(StoryParseError, match="Invalid story slug"):
        initStory(InitOptions(storiesDir=storiesDir, title="Bad", slug="../escape"))

    with pytest.raises(StoryParseError, match="Chapter count"):
        initStory(InitOptions(storiesDir=storiesDir, title="Zero", chapterCount=0))


def test_cli_new(tmp_path: Path) -> None:
    """`ficgen new` works via CLI and surfaces errors cleanly."""
    storiesDir = tmp_path / "_stories"
    assert (
        main(
            [
                "new",
                "CLI One Shot",
                "--stories",
                str(storiesDir),
                "--fandom",
                "Test Fandom",
                "--category",
                "F/F",
                "--published",
            ],
        )
        == 0
    )
    assert (storiesDir / "cli-one-shot" / "index.md").is_file()

    assert (
        main(
            [
                "new",
                "CLI Multi",
                "--slug",
                "custom-multi",
                "--multi",
                "--stories",
                str(storiesDir),
            ],
        )
        == 0
    )
    assert (storiesDir / "custom-multi" / "story.kdl").is_file()
    assert (storiesDir / "custom-multi" / "ch1.md").is_file()

    assert main(["new", "Spicy Draft", "--nsfw", "--stories", str(storiesDir)]) == 0
    assert "nsfw true" in (storiesDir / "spicy-draft" / "index.md").read_text(encoding="utf-8")

    # Duplicate returns exit code 1.
    assert main(["new", "CLI One Shot", "--stories", str(storiesDir)]) == 1


def test_cli_init_scaffolds_empty_folder_and_builds_cleanly(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`ficgen init` populates an empty folder with site.kdl and example stories that build cleanly."""
    archiveDir = tmp_path / "my-archive"
    archiveDir.mkdir()
    monkeypatch.chdir(archiveDir)

    assert main(["init"]) == 0
    captured = capsys.readouterr()
    assert "site.kdl" in captured.out
    assert "ficgen build" in captured.out
    assert "ficgen new" in captured.out

    assert (archiveDir / "site.kdl").is_file()
    oneShotMd = archiveDir / "_stories" / "example-one-shot" / "index.md"
    storyKdl = archiveDir / "_stories" / "example-multi-chapter" / "story.kdl"
    ch1Md = archiveDir / "_stories" / "example-multi-chapter" / "ch1.md"
    ch2Md = archiveDir / "_stories" / "example-multi-chapter" / "ch2.md"
    nsfwStoryKdl = archiveDir / "_stories" / "example-nsfw" / "story.kdl"
    nsfwCh1Md = archiveDir / "_stories" / "example-nsfw" / "ch1.md"
    nsfwCh2Md = archiveDir / "_stories" / "example-nsfw" / "ch2.md"
    assert oneShotMd.is_file()
    assert storyKdl.is_file()
    assert ch1Md.is_file()
    assert ch2Md.is_file()
    assert nsfwStoryKdl.is_file()
    assert nsfwCh1Md.is_file()
    assert nsfwCh2Md.is_file()

    # Example stories use placeholder Lorem Ipsum prose and include commented optional metadata.
    assert "Lorem ipsum" in oneShotMd.read_text(encoding="utf-8")
    assert "Lorem ipsum" in ch1Md.read_text(encoding="utf-8")
    assert "<nsfw>" in nsfwCh1Md.read_text(encoding="utf-8")
    assert '<nsfw reason="' in nsfwCh1Md.read_text(encoding="utf-8")
    assert "nsfw true" in nsfwCh2Md.read_text(encoding="utf-8")
    siteKdlText = (archiveDir / "site.kdl").read_text(encoding="utf-8")
    assert "// include-drafts false" in siteKdlText
    assert "// local-browsing true" in siteKdlText
    assert "// hide-nsfw true" in siteKdlText
    assert "// import-nsfw {" in siteKdlText
    assert '// stories "_stories"' in siteKdlText
    assert '// font-serif "old-style"' in siteKdlText
    assert '// font-sans "neogrotesque"' in siteKdlText
    assert '// font-mono "mono-code"' in siteKdlText
    assert '// css "site.css" mode="append"' in siteKdlText
    assert "// planned-chapters" in storyKdl.read_text(encoding="utf-8")

    # Running `ficgen build` right away succeeds with zero warnings and generates all example stories.
    with caplog.at_level(logging.WARNING, logger="ficgen"):
        assert main(["build"]) == 0
    assert caplog.records == []
    assert (archiveDir / "index.html").is_file()
    assert (archiveDir / "example-one-shot" / "index.html").is_file()
    assert (archiveDir / "example-multi-chapter" / "index.html").is_file()
    assert (archiveDir / "example-multi-chapter" / "ch1.html").is_file()
    assert (archiveDir / "example-multi-chapter" / "ch2.html").is_file()
    assert (archiveDir / "example-multi-chapter" / "all.html").is_file()
    assert (archiveDir / "example-nsfw" / "index.html").is_file()
    assert 'data-nsfw-gate="section"' in (archiveDir / "example-nsfw" / "ch1.html").read_text(encoding="utf-8")
    assert 'data-nsfw-gate="chapter"' in (archiveDir / "example-nsfw" / "ch2.html").read_text(encoding="utf-8")
    assert (archiveDir / "example-nsfw" / "all.html").is_file()


def test_init_site_rejects_non_empty_directory_and_files(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`ficgen init` refuses to run in a non-empty directory or on a file path."""
    nonEmptyDir = tmp_path / "occupied"
    nonEmptyDir.mkdir()
    (nonEmptyDir / "existing.txt").write_text("hello", encoding="utf-8")

    monkeypatch.chdir(nonEmptyDir)
    assert main(["init"]) == 1
    captured = capsys.readouterr()
    assert "not empty" in captured.err
    assert not (nonEmptyDir / "site.kdl").exists()
    assert not (nonEmptyDir / "_stories").exists()

    # Hidden files also count as non-empty.
    hiddenDir = tmp_path / "hidden"
    hiddenDir.mkdir()
    (hiddenDir / ".keep").write_text("", encoding="utf-8")
    with pytest.raises(StoryParseError, match="not empty"):
        initSite(hiddenDir)

    # A path pointing to a regular file fails cleanly.
    filePath = tmp_path / "not-a-dir"
    filePath.write_text("x", encoding="utf-8")
    with pytest.raises(StoryParseError, match="not a directory"):
        initSite(filePath)


def test_init_site_commented_metadata_is_valid_when_uncommented(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every commented-out KDL metadata line in `ficgen init` templates parses cleanly if uncommented."""
    archiveDir = tmp_path / "uncommented"
    result = initSite(archiveDir)

    commentRe = re.compile(r"(?m)^// ")
    for createdPath in result.createdFiles:
        raw = createdPath.read_text(encoding="utf-8")
        createdPath.write_text(commentRe.sub("", raw), encoding="utf-8")
    (archiveDir / "site.css").write_text(":root { --accent: rebeccapurple; }\n", encoding="utf-8")

    monkeypatch.chdir(archiveDir)
    config = buildConfigFromArgs(["--drafts"])
    archive = loadArchive(config)
    assert len(archive.stories) == 3
    generateSite(archive)
    assert (archiveDir / "example-one-shot" / "index.html").is_file()
    assert (archiveDir / "example-multi-chapter" / "ch2.html").is_file()
    assert (archiveDir / "example-nsfw" / "ch2.html").is_file()
    assert "--accent: rebeccapurple;" in (archiveDir / "style.css").read_text(encoding="utf-8")
