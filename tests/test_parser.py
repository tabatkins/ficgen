"""Tests for KDL/Markdown document parsing, story loading, draft handling, and slugging."""

from __future__ import annotations

from pathlib import Path

import pytest

from ficgen.kdlMeta import getListField, getStringField
from ficgen.models import SiteConfig, buildSlugMap, slugify
from ficgen.parser import StoryParseError, loadArchive, parseMarkdownDocument


def test_parse_document_extracts_title_kdl_metadata_and_notes() -> None:
    """KDL metadata fences, heading titles, and note blocks are extracted from body text."""
    raw = (
        "# Sample Story\n\n"
        + "```metadata\n"
        + 'fandoms "Fandom A" "Fandom B"\n'
        + 'categories "F/F"\n'
        + "tags {\n"
        + '    - "angst"\n'
        + '    - "fluff"\n'
        + "}\n"
        + "```\n\n"
        + "<notes>\nTop note with https://example.com\n</notes>\n\n"
        + "<endnotes>\nBottom note.\n</endnotes>\n\n"
        + "Body paragraph one.\n"
    )
    doc = parseMarkdownDocument(raw)
    assert doc.title == "Sample Story"
    assert getListField(doc.metadata, "fandoms") == ("Fandom A", "Fandom B")
    assert getListField(doc.metadata, "tags") == ("angst", "fluff")
    assert doc.notesMd == "Top note with https://example.com"
    assert doc.endnotesMd == "Bottom note."
    assert doc.bodyMd == "Body paragraph one."


def test_parse_document_supports_leading_kdl_fence_and_typed_dates() -> None:
    """Leading ```kdl fences work as metadata while mid-prose ```kdl fences stay prose."""
    raw = (
        "# Typed Demo\n\n"
        + "```kdl\n"
        + 'author "Writer"\n'
        + 'date (date)"2026-04-05"\n'
        + "```\n\n"
        + "Prose before example.\n\n"
        + "```kdl\n"
        + 'example-node "not metadata"\n'
        + "```\n"
    )
    doc = parseMarkdownDocument(raw)
    assert getStringField(doc.metadata, "author") == "Writer"
    assert 'example-node "not metadata"' in doc.bodyMd


def test_parse_document_ignores_tags_inside_code_fence() -> None:
    """Note tags inside fenced code blocks stay in the body."""
    raw = "# Code Demo\n\n```html\n<endnotes>literal example</endnotes>\n```\n\nReal prose."
    doc = parseMarkdownDocument(raw)
    assert doc.endnotesMd == ""
    assert "<endnotes>literal example</endnotes>" in doc.bodyMd


def test_parse_document_extracts_inline_css_blocks() -> None:
    """Inline ```css fences are extracted into cssBlocks and stripped from story prose."""
    raw = (
        "# Styled Story\n\n"
        + '```metadata\ntags "work-skin"\n```\n\n'
        + "```css\n"
        + "p { color: navy; }\n"
        + "```\n\n"
        + "Prose paragraph.\n\n"
        + "```css\n"
        + "em { letter-spacing: 0.05em; }\n"
        + "```\n"
    )
    doc = parseMarkdownDocument(raw)
    assert doc.cssBlocks == ("p { color: navy; }", "em { letter-spacing: 0.05em; }")
    assert doc.bodyMd == "Prose paragraph."


def test_load_archive_with_story_kdl_and_draft_filtering(tmp_path: Path) -> None:
    """Multi-chapter stories read whole-work metadata from story.kdl and filter drafts."""
    multiDir = tmp_path / "multi"
    multiDir.mkdir()
    (multiDir / "story.kdl").write_text(
        'title "Multi Story"\n'
        + 'fandoms "Alpha"\n'
        + 'categories "F/F"\n'
        + 'tags "slow burn"\n'
        + 'summary "Work-level summary."\n',
        encoding="utf-8",
    )
    (multiDir / "ch1.md").write_text(
        '# First Awakening\n\n```metadata\nchapter 1\ndate (date)"2026-01-10"\n```\n\nChapter one prose.\n',
        encoding="utf-8",
    )
    (multiDir / "ch2.md").write_text(
        '```metadata\nchapter 2\npublished false\ntags "spoiler tag"\n```\n\nChapter two draft.\n',
        encoding="utf-8",
    )

    draftStoryDir = tmp_path / "secret-wip"
    draftStoryDir.mkdir()
    (draftStoryDir / "index.md").write_text(
        "# Secret Draft\n\n```metadata\npublished false\n```\n\nUnpublished text.\n",
        encoding="utf-8",
    )

    pubArchive = loadArchive(SiteConfig(storiesDir=tmp_path, outputDir=tmp_path / "out", includeDrafts=False))
    assert len(pubArchive.stories) == 1
    assert pubArchive.allStorySlugs == ("multi", "secret-wip")
    story = pubArchive.stories[0]
    assert story.title == "Multi Story"
    assert story.isMultiChapter is True
    assert story.summaryText == "Work-level summary."
    assert len(story.chapters) == 1
    assert story.chapters[0].displayTitle == "Chapter 1: First Awakening"
    assert story.date.strftime("%Y-%m-%d") == "2026-01-10"
    assert story.tags == ("slow burn",)
    assert story.chapterCountDisplay == "1/?"

    draftArchive = loadArchive(SiteConfig(storiesDir=tmp_path, outputDir=tmp_path / "out", includeDrafts=True))
    assert len(draftArchive.stories) == 2
    multiWithDraft = next(item for item in draftArchive.stories if item.slug == "multi")
    assert len(multiWithDraft.chapters) == 2
    assert multiWithDraft.chapters[1].published is False
    assert "spoiler tag" in multiWithDraft.tags


def test_load_multichapter_without_story_kdl_synthesizes_metadata(tmp_path: Path) -> None:
    """Multi-chapter directories without story.kdl still load using chapter metadata."""
    storyDir = tmp_path / "legacy"
    storyDir.mkdir()
    (storyDir / "ch1.md").write_text(
        '# Legacy Tale\n\n```metadata\nchapter 1\nfandoms "Classic"\n```\n\nPart one.\n',
        encoding="utf-8",
    )
    (storyDir / "ch2.md").write_text(
        '# Legacy Tale\n\n```metadata\nchapter 2\ntags "sequel"\n```\n\nPart two.\n',
        encoding="utf-8",
    )

    archive = loadArchive(SiteConfig(storiesDir=tmp_path, outputDir=tmp_path / "out"))
    assert len(archive.stories) == 1
    story = archive.stories[0]
    assert story.title == "Legacy Tale"
    assert story.fandoms == ("Classic",)
    assert story.tags == ("sequel",)
    assert [ch.number for ch in story.chapters] == [1, 2]
    assert story.chapters[0].title == ""


def test_duplicate_chapter_number_raises(tmp_path: Path) -> None:
    """Duplicate chapter numbers in the same story raise StoryParseError."""
    storyDir = tmp_path / "dup"
    storyDir.mkdir()
    (storyDir / "ch1.md").write_text("```metadata\nchapter 1\n```\n\nA.\n", encoding="utf-8")
    (storyDir / "ch2.md").write_text("```metadata\nchapter 1\n```\n\nB.\n", encoding="utf-8")

    with pytest.raises(StoryParseError, match="Duplicate chapter number"):
        loadArchive(SiteConfig(storiesDir=tmp_path, outputDir=tmp_path / "out"))


def test_invalid_kdl_metadata_raises() -> None:
    """Malformed KDL syntax in a metadata block raises StoryParseError."""
    with pytest.raises(StoryParseError, match="Invalid KDL"):
        parseMarkdownDocument('# Title\n\n```metadata\ntags "unterminated\n```\n')


def test_slugify_and_collision_disambiguation() -> None:
    """Slugs are URL-safe and colliding names receive deterministic disambiguators."""
    assert slugify("Fandom (group)") == "fandom-group"
    assert slugify("F/F") == "f-f"
    assert slugify("!!!").startswith("item-")

    slugMap = buildSlugMap(["A/B", "A-B", "Unique Tag"])
    assert slugMap["Unique Tag"] == "unique-tag"
    assert slugMap["A/B"] != slugMap["A-B"]
    assert slugMap["A/B"].startswith("a-b-")
    assert slugMap["A-B"].startswith("a-b-")


def test_parse_document_handles_empty_metadata_and_nested_example_fences() -> None:
    """Empty metadata fences do not swallow prose, and 4-backtick fences protect nested examples."""
    emptyMeta = "# Empty Meta\n\n```metadata\n```\n\nProse after empty fence.\n"
    doc = parseMarkdownDocument(emptyMeta)
    assert doc.title == "Empty Meta"
    assert doc.bodyMd == "Prose after empty fence."

    nestedExample = (
        "# Docs Example\n\n"
        + "````markdown\n"
        + "```metadata\n"
        + 'tags "not-a-tag"\n'
        + "```\n\n"
        + "```css\n"
        + "p { color: red; }\n"
        + "```\n\n"
        + "<endnotes>Not an endnote.</endnotes>\n"
        + "````\n\n"
        + "Actual story prose.\n"
    )
    exampleDoc = parseMarkdownDocument(nestedExample)
    assert not getListField(exampleDoc.metadata, "tags")
    assert not exampleDoc.cssBlocks
    assert exampleDoc.endnotesMd == ""
    assert "p { color: red; }" in exampleDoc.bodyMd
    assert "<endnotes>Not an endnote.</endnotes>" in exampleDoc.bodyMd
    assert "Actual story prose." in exampleDoc.bodyMd


def test_case_insensitive_taxonomy_tags_remain_visible_on_all_stories(tmp_path: Path) -> None:
    """Stories sharing a tag with different casing merge into one bucket and both resolve TaxonomyItems."""
    firstDir = tmp_path / "first"
    firstDir.mkdir()
    (firstDir / "index.md").write_text(
        '# First\n\n```metadata\ntags "Slow Burn"\ndate "2026-09-02"\n```\n\nOne.\n',
        encoding="utf-8",
    )

    secondDir = tmp_path / "second"
    secondDir.mkdir()
    (secondDir / "index.md").write_text(
        '# Second\n\n```metadata\ntags "slow burn"\ndate "2026-09-01"\n```\n\nTwo.\n',
        encoding="utf-8",
    )

    archive = loadArchive(SiteConfig(storiesDir=tmp_path, outputDir=tmp_path / "out"))
    tagBuckets = archive.taxonomies["tags"]
    assert len(tagBuckets) == 1
    assert tagBuckets[0].item.name == "Slow Burn"
    assert tagBuckets[0].item.slug == "slow-burn"
    assert len(tagBuckets[0].stories) == 2

    bySlug = {story.slug: story for story in archive.stories}
    firstItems = archive.taxonomyItems(bySlug["first"], "tags")
    secondItems = archive.taxonomyItems(bySlug["second"], "tags")
    assert len(firstItems) == 1 and firstItems[0].slug == "slow-burn"
    assert len(secondItems) == 1 and secondItems[0].slug == "slow-burn"


def test_nsfw_metadata_on_work_chapter_and_section(tmp_path: Path) -> None:
    """Parser reads work-level, chapter-level, and section-level NSFW markers accurately."""
    # Single-doc NSFW with explicit reason string.
    oneShotNsfw = tmp_path / "spicy-shot"
    oneShotNsfw.mkdir()
    (oneShotNsfw / "index.md").write_text(
        '# Spicy Shot\n\n```metadata\nnsfw "Explicit sexual content"\ndate "2026-10-01"\n```\n\nBody.\n',
        encoding="utf-8",
    )

    # Single-doc SFW containing an <nsfw> section.
    oneShotSection = tmp_path / "partial-shot"
    oneShotSection.mkdir()
    (oneShotSection / "index.md").write_text(
        '# Partial Shot\n\n```metadata\ndate "2026-10-02"\n```\n\nSafe.\n\n<nsfw>\nSpicy scene.\n</nsfw>\n',
        encoding="utf-8",
    )

    # Multi-chapter SFW story with one NSFW chapter (using reason property).
    multiChapter = tmp_path / "mixed-multi"
    multiChapter.mkdir()
    (multiChapter / "story.kdl").write_text('title "Mixed Multi"\n', encoding="utf-8")
    (multiChapter / "ch1.md").write_text(
        '```metadata\nchapter 1\ndate "2026-10-03"\n```\n\nSafe ch1.\n',
        encoding="utf-8",
    )
    (multiChapter / "ch2.md").write_text(
        '```metadata\nchapter 2\nnsfw true reason="Violence"\ndate "2026-10-04"\n```\n\nCh2.\n',
        encoding="utf-8",
    )

    # Legacy multi-chapter without story.kdl: nsfw on ch1 stays on ch1, not whole work.
    legacy = tmp_path / "legacy-nsfw"
    legacy.mkdir()
    (legacy / "ch1.md").write_text(
        '```metadata\nchapter 1\nnsfw true\ndate "2026-10-05"\n```\n\nCh1.\n',
        encoding="utf-8",
    )
    (legacy / "ch2.md").write_text('```metadata\nchapter 2\ndate "2026-10-06"\n```\n\nCh2.\n', encoding="utf-8")

    archive = loadArchive(SiteConfig(storiesDir=tmp_path, outputDir=tmp_path / "out"))
    bySlug = {story.slug: story for story in archive.stories}

    assert bySlug["spicy-shot"].nsfw is True
    assert bySlug["spicy-shot"].nsfwReason == "Explicit sexual content"
    assert bySlug["spicy-shot"].hasNsfwContent is True

    assert bySlug["partial-shot"].nsfw is False
    assert bySlug["partial-shot"].hasNsfwSections is True
    assert bySlug["partial-shot"].hasNsfwContent is True

    mixed = bySlug["mixed-multi"]
    assert mixed.nsfw is False
    assert mixed.hasNsfwChapters is True
    assert mixed.hasNsfwContent is True
    assert mixed.chapters[0].nsfw is False
    assert mixed.chapters[1].nsfw is True
    assert mixed.chapters[1].nsfwReason == "Violence"

    leg = bySlug["legacy-nsfw"]
    assert leg.nsfw is False
    assert leg.chapters[0].nsfw is True
    assert leg.chapters[1].nsfw is False

    # Non-bool/non-string nsfw value raises StoryParseError.
    bad = tmp_path / "bad-nsfw"
    bad.mkdir()
    (bad / "index.md").write_text("# Bad\n\n```metadata\nnsfw 123\n```\n\nBody.\n", encoding="utf-8")
    with pytest.raises(StoryParseError, match="nsfw"):
        loadArchive(SiteConfig(storiesDir=tmp_path, outputDir=tmp_path / "out"))
