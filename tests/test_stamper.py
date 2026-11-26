"""Tests for the `ficgen stamp` subcommand and KDL metadata date stamping."""

from __future__ import annotations

from pathlib import Path

import pytest

from ficgen.cli import main
from ficgen.models import SiteConfig
from ficgen.parser import StoryParseError, loadArchive
from ficgen.stamper import stampDocumentText, stampStories


def test_stamp_document_inserts_kdl_date_into_existing_metadata_block() -> None:
    """Stamping appends a KDL date node inside an existing fenced metadata block."""
    raw = '# My Story\n\n```metadata\nfandoms "Alpha"\ntags "fluff"\n```\n\nOnce upon a time.\n'
    updated, changed = stampDocumentText(raw, "2026-11-24")
    assert changed is True
    assert updated == (
        '# My Story\n\n```metadata\nfandoms "Alpha"\ntags "fluff"\ndate "2026-11-24"\n```\n\nOnce upon a time.\n'
    )


def test_stamp_document_is_idempotent_unless_forced() -> None:
    """Existing KDL date nodes are preserved by default and overwritten only with force=True."""
    raw = '# Dated\n\n```metadata\ndate "2026-01-05"\n```\n\nBody.\n'
    unchanged, changed = stampDocumentText(raw, "2026-11-24")
    assert changed is False
    assert unchanged == raw

    forced, forceChanged = stampDocumentText(raw, "2026-11-24", force=True)
    assert forceChanged is True
    assert 'date "2026-11-24"' in forced
    assert "2026-01-05" not in forced


def test_stamp_document_handles_commented_and_null_dates_and_crlf() -> None:
    """Null date nodes are replaced and commented-out date lines do not block stamping."""
    rawNull = '# Win\r\n\r\n```metadata\r\ndate null\r\ntags "x"\r\n```\r\n\r\nText.'
    updatedNull, changedNull = stampDocumentText(rawNull, "2026-11-24")
    assert changedNull is True
    assert updatedNull == '# Win\r\n\r\n```metadata\r\ndate "2026-11-24"\r\ntags "x"\r\n```\r\n\r\nText.'

    rawCommented = '# Commented\n\n```metadata\n// date "2025-01-01"\n/- date "2025-02-02"\ntags "x"\n```\n\nBody.\n'
    updatedCommented, changedCommented = stampDocumentText(rawCommented, "2026-11-24")
    assert changedCommented is True
    assert '\ndate "2026-11-24"\n' in updatedCommented


def test_stamp_document_creates_metadata_block_when_missing() -> None:
    """Documents without a metadata fence get a KDL fence inserted after title or at top."""
    withTitle = "# Bare Title\n\nSome prose.\n"
    updatedTitle, changedTitle = stampDocumentText(withTitle, "2026-11-24")
    assert changedTitle is True
    assert updatedTitle == '# Bare Title\n\n```metadata\ndate "2026-11-24"\n```\n\nSome prose.\n'

    bare = "Just prose.\n"
    updatedBare, changedBare = stampDocumentText(bare, "2026-11-24")
    assert changedBare is True
    assert updatedBare == '```metadata\ndate "2026-11-24"\n```\n\nJust prose.\n'


def test_stamp_stories_targets_chapters_and_skips_drafts(tmp_path: Path) -> None:
    """Stamping targets single-doc index.md and multi-chapter chN.md while skipping drafts."""
    oneShot = tmp_path / "one-shot"
    oneShot.mkdir()
    (oneShot / "index.md").write_text('# One Shot\n\n```metadata\ntags "short"\n```\n\nHello.\n', encoding="utf-8")

    multi = tmp_path / "multi"
    multi.mkdir()
    (multi / "story.kdl").write_text('title "Multi"\nfandoms "Test"\n', encoding="utf-8")
    (multi / "ch1.md").write_text("```metadata\nchapter 1\n```\n\nCh 1.\n", encoding="utf-8")
    (multi / "ch2.md").write_text("```metadata\nchapter 2\npublished false\n```\n\nCh 2.\n", encoding="utf-8")

    # Dry run leaves files untouched.
    dryResult = stampStories(tmp_path, "2026-11-24", dryRun=True)
    assert dryResult.stampedCount == 2
    assert dryResult.skippedDraftCount == 1
    assert "date" not in (oneShot / "index.md").read_text(encoding="utf-8")
    assert "date" not in (multi / "ch1.md").read_text(encoding="utf-8")

    # Real stamp updates published files only, leaves story.kdl and ch2.md untouched.
    result = stampStories(tmp_path, "2026-11-24")
    assert result.stampedCount == 2
    assert result.skippedDraftCount == 1
    assert 'date "2026-11-24"' in (oneShot / "index.md").read_text(encoding="utf-8")
    assert 'date "2026-11-24"' in (multi / "ch1.md").read_text(encoding="utf-8")
    assert "date" not in (multi / "story.kdl").read_text(encoding="utf-8")
    assert "date" not in (multi / "ch2.md").read_text(encoding="utf-8")

    # Loading the archive picks up explicit dates for both stories.
    archive = loadArchive(SiteConfig(storiesDir=tmp_path, outputDir=tmp_path / "out"))
    assert all(story.hasExplicitDate for story in archive.stories)
    assert all(story.date.strftime("%Y-%m-%d") == "2026-11-24" for story in archive.stories)

    # Second run is a no-op for already-dated files.
    secondResult = stampStories(tmp_path, "2026-12-01")
    assert secondResult.stampedCount == 0
    assert secondResult.skippedExistingCount == 2
    assert secondResult.skippedDraftCount == 1


def test_stamp_cli_subcommand_and_slug_targeting(tmp_path: Path) -> None:
    """`ficgen stamp` supports story slug targets, --date, --drafts, and rejects bad dates."""
    storiesDir = tmp_path / "_stories"
    storyDir = storiesDir / "alpha"
    storyDir.mkdir(parents=True)
    storyFile = storyDir / "index.md"
    storyFile.write_text("# Alpha\n\n```metadata\npublished false\n```\n\nBody.\n", encoding="utf-8")

    assert main(["stamp", "alpha", "--stories", str(storiesDir), "--date", "2026-05-01"]) == 0
    assert "date" not in storyFile.read_text(encoding="utf-8")

    assert main(["stamp", "alpha", "--stories", str(storiesDir), "--date", "2026-05-01", "--drafts"]) == 0
    assert 'date "2026-05-01"' in storyFile.read_text(encoding="utf-8")

    # Invalid ISO date returns exit code 1.
    assert main(["stamp", "--stories", str(storiesDir), "--date", "not-a-date"]) == 1

    # Explicit `build` subcommand still works.
    outDir = tmp_path / "out"
    assert main(["build", "--stories", str(storiesDir), "--output", str(outDir), "--drafts"]) == 0
    assert "2026-05-01" in (outDir / "alpha" / "index.html").read_text(encoding="utf-8")


def test_stamp_invalid_date_raises_directly(tmp_path: Path) -> None:
    """Invalid date values raise StoryParseError."""
    with pytest.raises(StoryParseError, match="Invalid ISO-8601 date"):
        stampStories(tmp_path, "2026-99-99")


def test_stamp_document_handles_leading_blanks_empty_fence_and_duplicate_dates() -> None:
    """Stamping handles leading blanks before `# Title` + ```kdl, empty fences, and duplicate date nodes."""
    leadingKdl = '\n# Leading Blanks\n\n```kdl\nauthor "Writer"\n```\n\nBody.\n'
    stampedKdl, changedKdl = stampDocumentText(leadingKdl, "2026-11-24")
    assert changedKdl is True
    assert stampedKdl.count("```") == 2
    assert 'date "2026-11-24"' in stampedKdl

    emptyFence = "# Empty\n\n```metadata\n```\n\nBody.\n"
    stampedEmpty, changedEmpty = stampDocumentText(emptyFence, "2026-11-24")
    assert changedEmpty is True
    assert stampedEmpty == '# Empty\n\n```metadata\ndate "2026-11-24"\n```\n\nBody.\n'

    dupDates = '# Dups\n\n```metadata\ndate "2025-01-01"\ndate "2025-06-01"\n```\n\nBody.\n'
    forcedDup, changedDup = stampDocumentText(dupDates, "2026-11-24", force=True)
    assert changedDup is True
    assert "2025-01-01" not in forcedDup
    assert "2025-06-01" not in forcedDup
    assert 'date "2026-11-24"' in forcedDup
