"""Tests for the `ficgen new` / `ficgen init` story scaffolder."""

from __future__ import annotations

from pathlib import Path

import pytest

from ficgen.cli import main
from ficgen.generator import generateSite
from ficgen.kdlMeta import StoryParseError
from ficgen.models import SiteConfig
from ficgen.parser import loadArchive
from ficgen.scaffolder import InitOptions, initStory


def test_init_single_doc_story_defaults_to_draft_and_builds_cleanly(tmp_path: Path) -> None:
    """Single-doc scaffolding creates index.md with valid KDL draft metadata."""
    storiesDir = tmp_path / "stories"
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
    storiesDir = tmp_path / "stories"
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
    storiesDir = tmp_path / "stories"
    initStory(InitOptions(storiesDir=storiesDir, title="Unique Story"))

    with pytest.raises(StoryParseError, match="already exists"):
        initStory(InitOptions(storiesDir=storiesDir, title="Unique Story"))

    with pytest.raises(StoryParseError, match="Invalid story slug"):
        initStory(InitOptions(storiesDir=storiesDir, title="Bad", slug="../escape"))

    with pytest.raises(StoryParseError, match="Chapter count"):
        initStory(InitOptions(storiesDir=storiesDir, title="Zero", chapterCount=0))


def test_cli_new_and_init_alias(tmp_path: Path) -> None:
    """`ficgen new` and `ficgen init` work via CLI and surface errors cleanly."""
    storiesDir = tmp_path / "stories"
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
                "init",
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

    # Duplicate returns exit code 1.
    assert main(["new", "CLI One Shot", "--stories", str(storiesDir)]) == 1
