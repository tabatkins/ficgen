"""Tests for the `ficgen stamp` subcommand and KDL metadata date stamping."""

from __future__ import annotations

from pathlib import Path

import pytest

from ficgen.cli import main
from ficgen.models import SiteConfig
from ficgen.parser import StoryParseError, loadArchive
from ficgen.stamper import stampDocumentText, stampKdlText, stampStories


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


def test_stamp_document_and_kdl_add_relationship_implied_characters() -> None:
    """Stamping adds missing relationship-implied characters across KDL styles while preserving comments."""
    # Empty placeholder `characters ""` (e.g. from `ficgen new`) is filled in place; existing date stays intact.
    placeholderMd = (
        "# Rain\n\n"
        + "```metadata\n"
        + 'characters ""\n'
        + 'relationships "Elena/Sora" "Sora & Maya"\n'
        + 'date "2026-08-01"\n'
        + "```\n\n"
        + "Prose.\n"
    )
    updatedMd, changedMd = stampDocumentText(placeholderMd, "2026-11-28")
    assert changedMd is True
    assert updatedMd == (
        "# Rain\n\n"
        + "```metadata\n"
        + 'characters "Elena" "Sora" "Maya"\n'
        + 'relationships "Elena/Sora" "Sora & Maya"\n'
        + 'date "2026-08-01"\n'
        + "```\n\n"
        + "Prose.\n"
    )
    # Second pass is idempotent.
    assert stampDocumentText(updatedMd, "2026-11-28") == (updatedMd, False)

    # Disabling `stampCharacters` leaves characters untouched and skips already-dated files.
    assert stampDocumentText(placeholderMd, "2026-11-28", stampCharacters=False) == (placeholderMd, False)

    # Existing characters line preserves explicit casing, trailing comments, and CRLF endings.
    existingKdl = (
        'title "Drift"\r\n'
        + 'characters "Zara" "nadia"  // core cast\r\n'
        + 'relationships "Nadia/Kira"\r\n'
        + '// summary ""\r\n'
    )
    updatedKdl, changedKdl = stampKdlText(existingKdl)
    assert changedKdl is True
    assert updatedKdl == (
        'title "Drift"\r\n'
        + 'characters "Zara" "nadia" "Kira"  // core cast\r\n'
        + 'relationships "Nadia/Kira"\r\n'
        + '// summary ""\r\n'
    )

    # Child block syntax and repeated singular `character` lines keep their formatting style.
    childBlockKdl = 'characters {\n    - "Zara"\n}\nrelationships "Nadia/Kira"\n'
    updatedChild, changedChild = stampKdlText(childBlockKdl)
    assert changedChild is True
    assert updatedChild == 'characters {\n    - "Zara"\n    - "Nadia"\n    - "Kira"\n}\nrelationships "Nadia/Kira"\n'

    singularKdl = 'character "Zara"\nrelationships "Nadia/Kira"\n'
    updatedSingular, changedSingular = stampKdlText(singularKdl)
    assert changedSingular is True
    assert updatedSingular == 'character "Zara"\ncharacter "Nadia"\ncharacter "Kira"\nrelationships "Nadia/Kira"\n'

    # No characters line yet appends a new `characters` line; non-relationships do nothing.
    noCharsKdl = 'title "Solo"\nrelationships "Alice/Bob" "Gen"\n'
    updatedNoChars, changedNoChars = stampKdlText(noCharsKdl)
    assert changedNoChars is True
    assert updatedNoChars == 'title "Solo"\nrelationships "Alice/Bob" "Gen"\ncharacters "Alice" "Bob"\n'
    assert stampKdlText('title "Gen"\nrelationships "Gen"\n') == ('title "Gen"\nrelationships "Gen"\n', False)


def test_stamp_stories_and_cli_characters_option(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`ficgen stamp` stamps implied characters into index.md, story.kdl, and chapters, toggleable via --no-characters."""
    archiveStories = tmp_path / "_stories"

    shotDir = archiveStories / "midnight-rain"
    shotDir.mkdir(parents=True)
    (shotDir / "index.md").write_text(
        '# Midnight Rain\n\n```metadata\nrelationships "Elena/Sora"\n```\n\nRain prose.\n',
        encoding="utf-8",
    )

    seriesDir = archiveStories / "starlight-drift"
    seriesDir.mkdir(parents=True)
    (seriesDir / "story.kdl").write_text(
        'title "Starlight Drift"\ncharacters ""\nrelationships "Nadia/Kira"\n',
        encoding="utf-8",
    )
    (seriesDir / "ch1.md").write_text(
        '```metadata\nchapter 1\nrelationships "Kira & Echo"\n```\n\nCh 1.\n',
        encoding="utf-8",
    )
    (seriesDir / "ch2.md").write_text(
        '```metadata\nchapter 2\npublished false\nrelationships "Secret/Spoiler"\n```\n\nCh 2.\n',
        encoding="utf-8",
    )

    # `--no-characters` stamps dates only and leaves story.kdl and characters untouched.
    assert main(["stamp", "--stories", str(archiveStories), "--date", "2026-11-28", "--no-characters"]) == 0
    capsys.readouterr()
    assert 'date "2026-11-28"' in (shotDir / "index.md").read_text(encoding="utf-8")
    assert "characters" not in (shotDir / "index.md").read_text(encoding="utf-8")
    assert 'characters ""' in (seriesDir / "story.kdl").read_text(encoding="utf-8")
    assert "date" not in (seriesDir / "story.kdl").read_text(encoding="utf-8")
    assert 'date "2026-11-28"' in (seriesDir / "ch1.md").read_text(encoding="utf-8")
    assert "characters" not in (seriesDir / "ch1.md").read_text(encoding="utf-8")

    # Running with default `--characters` now adds implied characters even on already-dated files,
    # never adds `date` to story.kdl, avoids duplicating story.kdl's Kira in ch1.md, and skips draft ch2.md.
    assert main(["stamp", "--stories", str(archiveStories), "--date", "2026-12-01", "--dry-run"]) == 0
    dryOut = capsys.readouterr().out
    assert "Would stamp characters (Elena, Sora):" in dryOut
    assert "Would stamp characters (Nadia, Kira):" in dryOut
    assert "Would stamp characters (Echo):" in dryOut
    assert "characters" not in (shotDir / "index.md").read_text(encoding="utf-8")

    assert main(["stamp", "--stories", str(archiveStories), "--date", "2026-12-01"]) == 0
    stampOut = capsys.readouterr().out
    assert "Stamped characters (Elena, Sora):" in stampOut
    assert "Stamped characters (Nadia, Kira):" in stampOut
    assert "Stamped characters (Echo):" in stampOut
    assert "Skipped (draft):" in stampOut

    oneShotText = (shotDir / "index.md").read_text(encoding="utf-8")
    assert 'characters "Elena" "Sora"' in oneShotText
    assert 'date "2026-11-28"' in oneShotText

    storyKdlText = (seriesDir / "story.kdl").read_text(encoding="utf-8")
    assert 'characters "Nadia" "Kira"' in storyKdlText
    assert "date" not in storyKdlText

    ch1Text = (seriesDir / "ch1.md").read_text(encoding="utf-8")
    assert 'characters "Echo"' in ch1Text
    assert 'characters "Kira"' not in ch1Text

    ch2Text = (seriesDir / "ch2.md").read_text(encoding="utf-8")
    assert "characters" not in ch2Text
    assert "date" not in ch2Text

    # Explicit .kdl file target is accepted and idempotent.
    assert main(["stamp", str(seriesDir / "story.kdl"), "--date", "2026-12-01"]) == 0
    assert "Skipped (up to date):" in capsys.readouterr().out

    # `--drafts` stamps both date and implied characters on ch2.md.
    assert main(["stamp", "--stories", str(archiveStories), "--date", "2026-12-02", "--drafts", "--characters"]) == 0
    draftOut = capsys.readouterr().out
    assert "Stamped 2026-12-02, characters (Secret, Spoiler):" in draftOut
    ch2Stamped = (seriesDir / "ch2.md").read_text(encoding="utf-8")
    assert 'date "2026-12-02"' in ch2Stamped
    assert 'characters "Secret" "Spoiler"' in ch2Stamped
