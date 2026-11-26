"""Scaffold new archives, single-document stories, and multi-chapter story directories."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from pathlib import Path

from . import t
from .kdlMeta import StoryParseError, formatKdlListLine, formatKdlString
from .models import (
    DEFAULT_CUSTOM_CSS_NAME,
    DEFAULT_MONO_FONT,
    DEFAULT_SANS_FONT,
    DEFAULT_SERIF_FONT,
    DEFAULT_STORIES_DIR_NAME,
    slugify,
)
from .stamper import defaultTodayDate, validateDateString

_SAFE_SLUG_RE: t.Final[re.Pattern[str]] = re.compile(r"^[a-z0-9][a-z0-9-]*$")
_EXAMPLE_CHAPTER_COMMENTED_META: t.Final[str] = (
    "// published false\n"
    + '// summary "Optional chapter summary (or use a <summary> block)."\n'
    + '// notes "Optional chapter top notes (or use a <notes> block)."\n'
    + '// endnotes "Optional chapter end notes (or use an <endnotes> block)."\n'
    + '// fandoms "Chapter-Specific Fandom"\n'
    + '// categories "Gen"\n'
    + '// characters "Extra Character"\n'
    + '// relationships "Extra Character/Another Character"\n'
    + '// tags "Chapter-Specific Tag"\n'
)


@dataclass(slots=True)
class InitOptions:
    """Options for initializing a new story directory."""

    storiesDir: Path
    title: str
    slug: str = ""
    multiChapter: bool = False
    chapterCount: int = 1
    published: bool = False
    nsfw: bool = False
    dateValue: str = ""
    fandoms: t.MetadataList = ()
    categories: t.MetadataList = ()
    relationships: t.MetadataList = ()
    characters: t.MetadataList = ()
    tags: t.MetadataList = ()


@dataclass(slots=True)
class InitResult:
    """Paths created when scaffolding a new story."""

    storyDir: Path
    slug: str
    createdFiles: tuple[Path, ...]


@dataclass(slots=True)
class SiteInitResult:
    """Paths created when initializing a new ficgen archive directory."""

    siteDir: Path
    configPath: Path
    storiesDir: Path
    createdFiles: tuple[Path, ...]


def resolveStorySlug(title: str, explicitSlug: str) -> str:
    """Validate an explicit slug or derive one cleanly from the story title."""
    cleanedTitle = title.strip()
    if not cleanedTitle:
        msg = "Story title cannot be empty."
        raise StoryParseError(msg)

    slug = explicitSlug.strip() or slugify(cleanedTitle)
    if not _SAFE_SLUG_RE.match(slug):
        msg = (
            f"Invalid story slug {slug!r}: must start with a lowercase letter or digit "
            + "and contain only lowercase letters, digits, and hyphens."
        )
        raise StoryParseError(msg)
    return slug


def _kdlListOrEmpty(nodeName: str, values: t.MetadataList) -> str:
    """Format a KDL list line, always emitting an empty placeholder when unset."""
    line = formatKdlListLine(nodeName, values, emitEmpty=True)
    assert line is not None
    return line


def _buildStoryMetadataLines(options: InitOptions, *, includeTitle: bool, includePublishState: bool) -> list[str]:
    """Build KDL metadata lines shared by single-doc index.md and multi-chapter story.kdl."""
    lines: list[str] = []
    if includeTitle:
        lines.append(f"title {formatKdlString(options.title.strip())}")
    lines.append(_kdlListOrEmpty("fandoms", options.fandoms))
    lines.append(_kdlListOrEmpty("categories", options.categories))
    lines.append(_kdlListOrEmpty("characters", options.characters))
    lines.append(_kdlListOrEmpty("relationships", options.relationships))
    lines.append(_kdlListOrEmpty("tags", options.tags))

    if options.nsfw:
        lines.append("nsfw true")

    if includePublishState:
        if options.dateValue:
            lines.append(f"date {formatKdlString(options.dateValue)}")
        elif options.published:
            lines.append(f"date {formatKdlString(defaultTodayDate())}")
        if not options.published:
            lines.append("published false")

    return lines


def _buildChapterContent(chapterNum: int, *, published: bool, dateValue: str) -> str:
    """Build starter Markdown content for a chapter file."""
    metaLines: list[str] = [f"chapter {chapterNum}"]
    if dateValue:
        metaLines.append(f"date {formatKdlString(dateValue)}")
    elif published:
        metaLines.append(f"date {formatKdlString(defaultTodayDate())}")
    if not published:
        metaLines.append("published false")

    metaBlock = "\n".join(metaLines)
    return f"```metadata\n{metaBlock}\n```\n\n"


def initStory(options: InitOptions) -> InitResult:
    """Create a new story directory with starter KDL and Markdown files."""
    title = options.title.strip()
    slug = resolveStorySlug(title, options.slug)

    if options.chapterCount < 1:
        msg = f"Chapter count must be >= 1, got {options.chapterCount}."
        raise StoryParseError(msg)

    validatedDate = validateDateString(options.dateValue) if options.dateValue.strip() else ""
    effective = replace(
        options,
        title=title,
        slug=slug,
        multiChapter=options.multiChapter or options.chapterCount > 1,
        dateValue=validatedDate,
    )

    storyDir = effective.storiesDir / slug
    if storyDir.exists():
        msg = f"Story directory already exists: {storyDir}"
        raise StoryParseError(msg)

    storyDir.mkdir(parents=True, exist_ok=False)
    created: list[Path] = []

    if not effective.multiChapter:
        metaLines = _buildStoryMetadataLines(effective, includeTitle=False, includePublishState=True)
        indexPath = storyDir / "index.md"
        indexContent = f"# {title}\n\n```metadata\n" + "\n".join(metaLines) + "\n```\n\n"
        indexPath.write_text(indexContent, encoding="utf-8")
        created.append(indexPath)
    else:
        storyMetaLines = _buildStoryMetadataLines(effective, includeTitle=True, includePublishState=False)
        storyMetaLines.append('// summary ""')
        storyKdlPath = storyDir / "story.kdl"
        storyKdlPath.write_text("\n".join(storyMetaLines) + "\n", encoding="utf-8")
        created.append(storyKdlPath)

        for chapterNum in range(1, effective.chapterCount + 1):
            chapterPath = storyDir / f"ch{chapterNum}.md"
            chapterContent = _buildChapterContent(
                chapterNum,
                published=effective.published,
                dateValue=effective.dateValue,
            )
            chapterPath.write_text(chapterContent, encoding="utf-8")
            created.append(chapterPath)

    return InitResult(storyDir=storyDir, slug=slug, createdFiles=tuple(created))


def _buildDefaultSiteKdl() -> str:
    """Build a starter site.kdl with sensible defaults and commented optional settings."""
    return (
        'title "My Fiction Archive"\n'
        + 'subtitle "Original fiction and fanworks."\n'
        + 'author "Your Name"\n'
        + 'base-url "https://example.com/"\n'
        + f'// stories "{DEFAULT_STORIES_DIR_NAME}"\n'
        + "// include-drafts false\n"
        + "// local-browsing true\n"
        + "// hide-nsfw true  // hide NSFW works/chapters/sections behind a confirmation dialog (or false to show normally)\n"
        + "// import-nsfw {\n"
        + '//     ratings "Explicit" "Mature" "Not Rated"\n'
        + '//     warnings "Rape/Non-Con" "Underage Sex" "Graphic Depictions Of Violence"\n'
        + "//     tags true\n"
        + "//     chapters true\n"
        + "// }\n"
        + f'// font-serif "{DEFAULT_SERIF_FONT}"    // presets: old-style, transitional, slab-serif, antique, didone (or custom CSS font stack)\n'
        + f'// font-sans "{DEFAULT_SANS_FONT}"  // presets: neogrotesque, system-ui, humanist, geometric-humanist, classical-humanist, industrial, rounded-sans, handwritten\n'
        + f'// font-mono "{DEFAULT_MONO_FONT}"     // presets: mono-code, mono-slab-serif (or custom CSS font stack)\n'
        + f'// css "{DEFAULT_CUSTOM_CSS_NAME}" mode="append"  // optional custom CSS (auto-loaded if {DEFAULT_CUSTOM_CSS_NAME} exists); mode: "append" or "replace"\n'
    )


def _buildExampleOneShotMd(todayDate: str) -> str:
    """Build an example single-document story with Lorem Ipsum prose and commented optional metadata."""
    dateLit = formatKdlString(todayDate)
    return (
        "# Example One-Shot\n\n"
        + "```metadata\n"
        + 'fandoms "Original Work"\n'
        + 'categories "Gen"\n'
        + 'characters "Character A" "Character B"\n'
        + 'relationships "Character A & Character B"\n'
        + 'tags "Example" "One-Shot"\n'
        + f"date {dateLit}\n"
        + '// title "Example One-Shot"\n'
        + '// author "Your Name"\n'
        + '// rating "General Audiences"\n'
        + '// warnings "No Archive Warnings Apply"\n'
        + '// summary "Optional inline summary (or use a <summary> block below)."\n'
        + '// notes "Optional top notes (or use a <notes> block below)."\n'
        + '// endnotes "Optional end notes (or use an <endnotes> block below)."\n'
        + f"// updated {dateLit}\n"
        + "// published false\n"
        + "// nsfw false\n"
        + "// complete true\n"
        + "```\n\n"
        + "<summary>\n"
        + "Lorem ipsum dolor sit amet, consectetur adipiscing elit. "
        + "Sed do eiusmod tempor incididunt ut labore et dolore magna aliqua.\n"
        + "</summary>\n\n"
        + "<notes>\n"
        + "Ut enim ad minim veniam, quis nostrud exercitation ullamco laboris nisi ut "
        + "aliquip ex ea commodo consequat.\n"
        + "</notes>\n\n"
        + "Lorem ipsum dolor sit amet, consectetur adipiscing elit, sed do eiusmod tempor "
        + "incididunt ut labore et dolore magna aliqua. Ut enim ad minim veniam, quis nostrud "
        + "exercitation ullamco laboris nisi ut aliquip ex ea commodo consequat.\n\n"
        + "|Lorem ipsum?>\n\n"
        + "<Dolor!|\n\n"
        + "|sit amet...>\n\n"
        + "Duis aute irure dolor in reprehenderit in voluptate velit esse cillum dolore eu "
        + "fugiat nulla pariatur. Excepteur sint occaecat cupidatat non proident, sunt in "
        + "culpa qui officia deserunt mollit anim id est laborum.\n\n"
        + "---\n\n"
        + "Curabitur pretium tincidunt lacus. Nulla gravida orci a odio. Nullam varius, "
        + "turpis et commodo pharetra, est eros bibendum elit, nec luctus magna felis "
        + "sollicitudin mauris.\n"
    )


def _buildExampleMultiStoryKdl(todayDate: str) -> str:
    """Build an example multi-chapter story.kdl with defaults and commented optional metadata."""
    dateLit = formatKdlString(todayDate)
    return (
        'title "Example Multi-Chapter Story"\n'
        + 'fandoms "Original Work"\n'
        + 'categories "F/F"\n'
        + 'characters "Character C" "Character D"\n'
        + 'relationships "Character C/Character D"\n'
        + 'tags "Example" "Multi-Chapter"\n'
        + 'summary "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Integer nec odio. Praesent libero."\n'
        + '// author "Your Name"\n'
        + '// rating "Teen And Up Audiences"\n'
        + '// warnings "No Archive Warnings Apply"\n'
        + '// notes "Optional work-level top notes."\n'
        + '// endnotes "Optional work-level end notes."\n'
        + f"// date {dateLit}\n"
        + f"// updated {dateLit}\n"
        + "// published false\n"
        + "// nsfw false\n"
        + "// complete false\n"
        + "// planned-chapters 3\n"
    )


def _buildExampleNsfwStoryKdl(todayDate: str) -> str:
    """Build an example multi-chapter story.kdl showcasing chapter- and section-level NSFW gating."""
    dateLit = formatKdlString(todayDate)
    return (
        'title "Example NSFW Story"\n'
        + 'fandoms "Original Work"\n'
        + 'categories "Gen"\n'
        + 'characters "Character E" "Character F"\n'
        + 'tags "Example" "NSFW"\n'
        + 'summary "Demonstrates inline <nsfw> blocks in Chapter 1 and a full NSFW chapter in Chapter 2 '
        + '(all prose is placeholder Lorem Ipsum)."\n'
        + '// author "Your Name"\n'
        + '// rating "Mature"\n'
        + '// warnings "No Archive Warnings Apply"\n'
        + '// notes "Optional work-level top notes."\n'
        + '// endnotes "Optional work-level end notes."\n'
        + f"// date {dateLit}\n"
        + f"// updated {dateLit}\n"
        + "// published false\n"
        + "// nsfw true  // uncomment to mark the entire work as NSFW\n"
        + "// complete true\n"
        + "// planned-chapters 2\n"
    )


def _buildExampleChapterMd(
    chapterNum: int,
    title: str,
    todayDate: str,
    *,
    noteBlock: str,
    prose: str,
    nsfw: bool = False,
) -> str:
    """Build an example multi-chapter Markdown file with commented optional metadata."""
    dateLit = formatKdlString(todayDate)
    titleLit = formatKdlString(title)
    nsfwLine = "nsfw true\n" if nsfw else "// nsfw false\n"
    return (
        f"# {title}\n\n"
        + "```metadata\n"
        + f"chapter {chapterNum}\n"
        + f"date {dateLit}\n"
        + nsfwLine
        + f"// title {titleLit}\n"
        + _EXAMPLE_CHAPTER_COMMENTED_META
        + "```\n\n"
        + f"{noteBlock}\n\n"
        + f"{prose}\n"
    )


def initSite(targetDir: Path) -> SiteInitResult:
    """Initialize a brand-new ficgen archive in an empty directory with site.kdl and example stories."""
    if targetDir.exists():
        if not targetDir.is_dir():
            msg = f"Cannot initialize ficgen archive: path exists and is not a directory: {targetDir}"
            raise StoryParseError(msg)
        if any(targetDir.iterdir()):
            msg = f"Cannot initialize ficgen archive: directory is not empty: {targetDir}"
            raise StoryParseError(msg)
    else:
        targetDir.mkdir(parents=True, exist_ok=False)

    todayDate = defaultTodayDate()
    created: list[Path] = []

    configPath = targetDir / "site.kdl"
    configPath.write_text(_buildDefaultSiteKdl(), encoding="utf-8")
    created.append(configPath)

    storiesDir = targetDir / DEFAULT_STORIES_DIR_NAME
    oneShotDir = storiesDir / "example-one-shot"
    oneShotDir.mkdir(parents=True, exist_ok=False)
    oneShotPath = oneShotDir / "index.md"
    oneShotPath.write_text(_buildExampleOneShotMd(todayDate), encoding="utf-8")
    created.append(oneShotPath)

    multiDir = storiesDir / "example-multi-chapter"
    multiDir.mkdir(parents=True, exist_ok=False)
    storyKdlPath = multiDir / "story.kdl"
    storyKdlPath.write_text(_buildExampleMultiStoryKdl(todayDate), encoding="utf-8")
    created.append(storyKdlPath)

    ch1Path = multiDir / "ch1.md"
    ch1Path.write_text(
        _buildExampleChapterMd(
            1,
            "Lorem Ipsum",
            todayDate,
            noteBlock=(
                "<notes>\n"
                + "Sed cursus ante dapibus diam. Sed nisi. Nulla quis sem at nibh elementum imperdiet.\n"
                + "</notes>"
            ),
            prose=(
                "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Integer nec odio. "
                + "Praesent libero. Sed cursus ante dapibus diam. Sed nisi. Nulla quis sem at nibh "
                + "elementum imperdiet.\n\n"
                + "Duis sagittis ipsum. Praesent mauris. Fusce nec tellus sed augue semper porta. "
                + "Mauris massa. Vestibulum lacinia arcu eget nulla."
            ),
        ),
        encoding="utf-8",
    )
    created.append(ch1Path)

    ch2Path = multiDir / "ch2.md"
    ch2Path.write_text(
        _buildExampleChapterMd(
            2,
            "Dolor Sit Amet",
            todayDate,
            noteBlock=(
                "<endnotes>\n"
                + "Class aptent taciti sociosqu ad litora torquent per conubia nostra, per inceptos himenaeos.\n"
                + "</endnotes>"
            ),
            prose=(
                "Class aptent taciti sociosqu ad litora torquent per conubia nostra, per inceptos "
                + "himenaeos. Curabitur sodales ligula in libero. Sed dignissim lacinia nunc.\n\n"
                + "Curabitur tortor. Pellentesque nibh. Aenean quam. In scelerisque sem at dolor. "
                + "Maecenas mattis. Sed convallis tristique sem."
            ),
        ),
        encoding="utf-8",
    )
    created.append(ch2Path)

    nsfwDir = storiesDir / "example-nsfw"
    nsfwDir.mkdir(parents=True, exist_ok=False)
    nsfwStoryKdlPath = nsfwDir / "story.kdl"
    nsfwStoryKdlPath.write_text(_buildExampleNsfwStoryKdl(todayDate), encoding="utf-8")
    created.append(nsfwStoryKdlPath)

    nsfwCh1Path = nsfwDir / "ch1.md"
    nsfwCh1Path.write_text(
        _buildExampleChapterMd(
            1,
            "Inline NSFW Sections",
            todayDate,
            noteBlock=(
                "<notes>\n"
                + "This chapter is SFW overall, with two placeholder `<nsfw>` sections inside the prose.\n"
                + "</notes>"
            ),
            prose=(
                "Lorem ipsum dolor sit amet, consectetur adipiscing elit. Integer nec odio. "
                + "Praesent libero. Sed cursus ante dapibus diam.\n\n"
                + "<nsfw>\n"
                + "Sed nisi. Nulla quis sem at nibh elementum imperdiet. Duis sagittis ipsum. "
                + "Praesent mauris. Fusce nec tellus sed augue semper porta.\n"
                + "</nsfw>\n\n"
                + "Mauris massa. Vestibulum lacinia arcu eget nulla. Class aptent taciti sociosqu "
                + "ad litora torquent per conubia nostra.\n\n"
                + '<nsfw reason="Example content warning">\n'
                + "Curabitur sodales ligula in libero. Sed dignissim lacinia nunc. "
                + "Curabitur tortor. Pellentesque nibh.\n"
                + "</nsfw>\n\n"
                + "Aenean quam. In scelerisque sem at dolor. Maecenas mattis. Sed convallis tristique sem."
            ),
        ),
        encoding="utf-8",
    )
    created.append(nsfwCh1Path)

    nsfwCh2Path = nsfwDir / "ch2.md"
    nsfwCh2Path.write_text(
        _buildExampleChapterMd(
            2,
            "Full NSFW Chapter",
            todayDate,
            nsfw=True,
            noteBlock=(
                "<notes>\n" + "This entire chapter is marked `nsfw true` in its KDL metadata block.\n" + "</notes>"
            ),
            prose=(
                "Proin ut ligula vel nunc egestas porttitor. Morbi lectus risus, iaculis vel, "
                + "suscipit quis, luctus non, massa. Fusce ac turpis quis ligula lacinia aliquet.\n\n"
                + "Mauris ipsum. Nulla metus metus, ullamcorper vel, tincidunt sed, euismod in, nibh. "
                + "Quisque volutpat condimentum velit."
            ),
        ),
        encoding="utf-8",
    )
    created.append(nsfwCh2Path)

    return SiteInitResult(
        siteDir=targetDir,
        configPath=configPath,
        storiesDir=storiesDir,
        createdFiles=tuple(created),
    )
