"""Command-line interface for building, initializing, importing, and stamping fiction archives."""

from __future__ import annotations

import argparse
import contextlib
import logging
import sys
from pathlib import Path

import ao3fetch

from . import t
from .config import loadSiteConfig, resolveDefaultPaths, resolveImportSettings, resolveStoriesDir
from .generator import BuildResult, generateSite
from .importer import ImportOptions, importFromSource
from .kdlMeta import StoryParseError
from .models import SiteConfig
from .parser import loadArchive
from .scaffolder import InitOptions, initSite, initStory
from .stamper import StampEntry, StampResult, defaultTodayDate, stampStories


def _addStoriesAndConfigArgs(
    parser: argparse.ArgumentParser,
    defaultStories: Path,
    *,
    storiesHelpPrefix: str = "Directory containing story folders",
) -> None:
    """Attach shared `--stories` and `--config` options to a subcommand parser."""
    parser.add_argument(
        "--stories",
        type=Path,
        default=None,
        help=f"{storiesHelpPrefix} (default: {defaultStories}, or site.kdl stories)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional KDL configuration file (auto-loads site.kdl if present).",
    )


def _buildArgParser(defaultStories: Path, defaultOutput: Path) -> argparse.ArgumentParser:
    """Create the CLI argument parser for building the site."""
    parser = argparse.ArgumentParser(
        prog="ficgen",
        description="Build a static HTML fiction archive from Markdown story documents.",
        epilog=(
            "Subcommands:\n"
            + "  ficgen [build] [options]        Build the static HTML archive (default)\n"
            + "  ficgen init [DIR]               Initialize a new archive with site.kdl and example stories\n"
            + "  ficgen new TITLE [options]      Initialize a new story directory\n"
            + "  ficgen import SOURCE [options]  Import stories from AO3-compatible archives or EPUBs\n"
            + "  ficgen stamp [options]          Stamp date and implied characters into KDL metadata blocks"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    _addStoriesAndConfigArgs(parser, defaultStories)
    parser.add_argument(
        "--output",
        type=Path,
        default=defaultOutput,
        help=f"Output directory for generated HTML/CSS/feed (default: {defaultOutput})",
    )
    parser.add_argument(
        "--title",
        type=str,
        default=None,
        help="Archive title (overrides config file).",
    )
    parser.add_argument(
        "--subtitle",
        type=str,
        default=None,
        help="Archive subtitle shown on the index page.",
    )
    parser.add_argument(
        "--author",
        type=str,
        default=None,
        help="Default author name for stories and Atom feed.",
    )
    parser.add_argument(
        "--base-url",
        type=str,
        default=None,
        help="Canonical base URL for the Atom feed (e.g. https://example.com/fic/).",
    )
    parser.add_argument(
        "--drafts",
        action="store_true",
        default=False,
        help="Include unpublished (published false) stories and chapters.",
    )
    return parser


def _stampArgParser() -> argparse.ArgumentParser:
    """Create the CLI argument parser for the `stamp` subcommand."""
    defaultStories, _ = resolveDefaultPaths()
    parser = argparse.ArgumentParser(
        prog="ficgen stamp",
        description=(
            'Stamp `date "YYYY-MM-DD"` and relationship-implied `characters` '
            + "into KDL metadata blocks of story and chapter files."
        ),
    )
    parser.add_argument(
        "targets",
        nargs="*",
        type=Path,
        help="Optional file paths, story directories, or story slugs to stamp (defaults to all stories).",
    )
    _addStoriesAndConfigArgs(parser, defaultStories)
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="ISO-8601 date to stamp (default: today's date, YYYY-MM-DD).",
    )
    parser.add_argument(
        "--characters",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Also add characters implied by relationship tags (default: enabled; pass --no-characters to disable).",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite existing date values.",
    )
    parser.add_argument(
        "--drafts",
        action="store_true",
        default=False,
        help="Also stamp files marked published false.",
    )
    parser.add_argument(
        "-n",
        "--dry-run",
        action="store_true",
        default=False,
        help="Show which files would be stamped without modifying them.",
    )
    return parser


def _initArgParser() -> argparse.ArgumentParser:
    """Create the CLI argument parser for the `init` subcommand."""
    parser = argparse.ArgumentParser(
        prog="ficgen init",
        description="Initialize a brand-new ficgen archive in an empty directory with site.kdl and example stories.",
    )
    parser.add_argument(
        "directory",
        nargs="?",
        type=Path,
        default=None,
        help="Target empty directory to initialize (default: current working directory).",
    )
    return parser


def _newArgParser() -> argparse.ArgumentParser:
    """Create the CLI argument parser for the `new` subcommand."""
    defaultStories, _ = resolveDefaultPaths()
    parser = argparse.ArgumentParser(
        prog="ficgen new",
        description="Initialize a new story directory with KDL metadata and starter Markdown files.",
    )
    parser.add_argument("title", type=str, help="Title of the new story.")
    parser.add_argument(
        "--slug",
        type=str,
        default="",
        help="Directory / URL slug (default: slugified from title).",
    )
    parser.add_argument(
        "-m",
        "--multi",
        action="store_true",
        default=False,
        help="Create a multi-chapter story (story.kdl + ch1.md) instead of a one-shot index.md.",
    )
    parser.add_argument(
        "--chapters",
        type=int,
        default=1,
        help="Number of starter chapter files to create (implies --multi if > 1).",
    )
    parser.add_argument(
        "--published",
        action="store_true",
        default=False,
        help="Mark starter file(s) as published (default creates them as `published false` drafts).",
    )
    parser.add_argument(
        "--nsfw",
        action="store_true",
        default=False,
        help="Mark the new story as NSFW (`nsfw true`).",
    )
    parser.add_argument(
        "--date",
        type=str,
        default="",
        help="Optional ISO-8601 publication date (YYYY-MM-DD).",
    )
    parser.add_argument("--fandom", action="append", default=[], help="Fandom tag (repeatable).")
    parser.add_argument(
        "--category",
        action="append",
        default=[],
        help="Relationship category such as F/F (repeatable).",
    )
    parser.add_argument("--relationship", action="append", default=[], help="Relationship / ship tag (repeatable).")
    parser.add_argument("--character", action="append", default=[], help="Character tag (repeatable).")
    parser.add_argument("--tag", action="append", default=[], help="Additional freeform tag (repeatable).")
    _addStoriesAndConfigArgs(parser, defaultStories)
    return parser


def _importArgParser() -> argparse.ArgumentParser:
    """Create the CLI argument parser for the `import` subcommand."""
    defaultStories, _ = resolveDefaultPaths()
    parser = argparse.ArgumentParser(
        prog="ficgen import",
        description="Import one or more works from AO3, AO3-compatible archives, or downloaded AO3 EPUBs.",
    )
    parser.add_argument(
        "sources",
        nargs="+",
        type=str,
        help="AO3/mirror work URL(s), numeric work ID(s), or local .epub file path(s).",
    )
    parser.add_argument(
        "--archive",
        type=str,
        default=ao3fetch.DEFAULT_BASE_URL,
        help=f"Archive base URL for bare numeric work IDs (default: {ao3fetch.DEFAULT_BASE_URL}).",
    )
    parser.add_argument(
        "--slug",
        type=str,
        default="",
        help="Custom story directory/URL slug (only valid when importing a single work).",
    )
    _addStoriesAndConfigArgs(parser, defaultStories, storiesHelpPrefix="Destination stories directory")
    parser.add_argument(
        "--draft",
        action="store_true",
        default=False,
        help="Mark imported story/chapters as `published false`.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        default=False,
        help="Overwrite existing story directory if it already exists.",
    )
    parser.add_argument(
        "--cookie",
        type=str,
        default="",
        help="Optional Cookie header value (e.g. session cookie for restricted works).",
    )
    return parser


def buildConfigFromArgs(argv: t.Sequence[str] | None = None) -> SiteConfig:
    """Parse CLI arguments and optional KDL site config into a SiteConfig."""
    defaultStories, defaultOutput = resolveDefaultPaths()
    parser = _buildArgParser(defaultStories, defaultOutput)
    args = parser.parse_args(argv)
    return loadSiteConfig(
        storiesDir=args.stories,
        outputDir=args.output,
        configPath=args.config,
        title=args.title,
        subtitle=args.subtitle,
        author=args.author,
        baseUrl=args.base_url,
        includeDrafts=bool(args.drafts),
    )


def buildArchive(config: SiteConfig) -> BuildResult:
    """Load stories from disk and generate the static archive."""
    archive = loadArchive(config)
    return generateSite(archive)


def _formatStampDetails(entry: StampEntry) -> str:
    """Format the stamped date and/or added characters for CLI display."""
    parts: list[str] = []
    if entry.stampedDate:
        parts.append(entry.dateValue)
    if entry.addedCharacters:
        chars = ", ".join(entry.addedCharacters)
        parts.append(f"characters ({chars})")
    return ", ".join(parts) if parts else entry.dateValue


def _printStampSummary(result: StampResult) -> None:
    """Print per-file actions and a concise summary for `ficgen stamp`."""
    for entry in result.entries:
        match entry.action:
            case "stamped":
                print(f"Stamped {_formatStampDetails(entry)}: {entry.path}")
            case "would_stamp":
                print(f"Would stamp {_formatStampDetails(entry)}: {entry.path}")
            case "skipped_existing":
                print(f"Skipped (up to date): {entry.path}")
            case "skipped_draft":
                print(f"Skipped (draft): {entry.path}")

    verb = "Would stamp" if result.dryRun else "Stamped"
    print(
        f"{verb} {result.stampedCount} file(s)"
        + f" ({result.skippedExistingCount} up to date, {result.skippedDraftCount} draft(s) skipped).",
    )


def _runStamp(argv: t.Sequence[str]) -> int:
    """Execute the `ficgen stamp` subcommand."""
    args = _stampArgParser().parse_args(argv)
    storiesDir = resolveStoriesDir(args.stories, args.config)
    dateValue: str = args.date if args.date is not None else defaultTodayDate()
    result = stampStories(
        storiesDir=storiesDir,
        dateValue=dateValue,
        targets=args.targets,
        force=bool(args.force),
        includeDrafts=bool(args.drafts),
        dryRun=bool(args.dry_run),
        stampCharacters=bool(args.characters),
    )
    _printStampSummary(result)
    return 0


def _runInit(argv: t.Sequence[str]) -> int:
    """Execute the `ficgen init` subcommand."""
    args = _initArgParser().parse_args(argv)
    targetDir: Path = args.directory if args.directory is not None else Path.cwd()
    result = initSite(targetDir)
    print(f"Initialized a new ficgen archive in {result.siteDir}:")
    for createdPath in result.createdFiles:
        print(f"  {createdPath.relative_to(result.siteDir)}")
    print("")
    print("Next steps:")
    print("  - Edit site.kdl with your archive's title, author, and base URL.")
    print("  - Run `ficgen build` to create the HTML for the example stories.")
    print("  - Look at the example source and output, and delete the sources when you're ready.")
    print('  - Run `ficgen new "Your Story Title"` to start your own new story.')
    return 0


def _runNew(argv: t.Sequence[str]) -> int:
    """Execute the `ficgen new` subcommand."""
    args = _newArgParser().parse_args(argv)
    storiesDir = resolveStoriesDir(args.stories, args.config)
    options = InitOptions(
        storiesDir=storiesDir,
        title=args.title,
        slug=args.slug,
        multiChapter=bool(args.multi),
        chapterCount=int(args.chapters),
        published=bool(args.published),
        nsfw=bool(args.nsfw),
        dateValue=args.date,
        fandoms=tuple(args.fandom),
        categories=tuple(args.category),
        relationships=tuple(args.relationship),
        characters=tuple(args.character),
        tags=tuple(args.tag),
    )
    result = initStory(options)
    print(f"Initialized story '{options.title.strip()}' in {result.storyDir}:")
    for createdPath in result.createdFiles:
        print(f"  {createdPath}")
    return 0


def mergeCookieHeader(existing: str, extraCookie: str) -> str:
    """Combine an existing HTTP Cookie header value with an additional cookie string."""
    cleanedExisting = existing.strip().rstrip(";").strip()
    cleanedExtra = extraCookie.strip().lstrip(";").strip()
    if cleanedExisting and cleanedExtra:
        return f"{cleanedExisting}; {cleanedExtra}"
    return cleanedExtra or cleanedExisting


def _makeCookieFetcher(baseFetcher: ao3fetch.Fetcher, extraCookie: str) -> ao3fetch.Fetcher:
    """Return a fetcher that appends an extra Cookie header to every request."""

    def _wrapped(url: str, headers: t.Mapping[str, str] | None = None) -> ao3fetch.FetchResponse:
        merged = dict(headers) if headers else {}
        combined = mergeCookieHeader(merged.get("Cookie", ""), extraCookie)
        if combined:
            merged["Cookie"] = combined
        return baseFetcher(url, merged)

    return _wrapped


@contextlib.contextmanager
def _configuredAo3Fetcher(cookie: str) -> t.Iterator[None]:
    """Temporarily wrap ao3fetch's default fetcher with caching, retries, and optional cookies."""
    logger = logging.getLogger("ficgen.importer")
    previousFetcher = ao3fetch.getFetcher()
    if previousFetcher is not ao3fetch.defaultFetch and not cookie:
        yield
        return

    base: ao3fetch.Fetcher = _makeCookieFetcher(previousFetcher, cookie) if cookie else previousFetcher

    def _logRetry(event: ao3fetch.RetryEvent) -> None:
        statusDesc = f"HTTP {event.response.status}" if event.response is not None else str(event.error)
        logger.warning(
            "Retry %d in %.1fs after %s",
            event.attempt,
            event.delaySeconds,
            statusDesc,
        )

    ao3fetch.setFetcher(ao3fetch.withCache(ao3fetch.withRetry(base, onRetry=_logRetry)))
    try:
        yield
    finally:
        if previousFetcher is ao3fetch.defaultFetch:
            ao3fetch.resetFetcher()
        else:
            ao3fetch.setFetcher(previousFetcher)


def _runImport(argv: t.Sequence[str]) -> int:
    """Execute the `ficgen import` subcommand."""
    args = _importArgParser().parse_args(argv)
    if args.slug and len(args.sources) > 1:
        msg = "--slug can only be used when importing a single work."
        raise StoryParseError(msg)

    storiesDir, importNsfw = resolveImportSettings(args.stories, args.config)
    with _configuredAo3Fetcher(args.cookie):
        for source in args.sources:
            options = ImportOptions(
                storiesDir=storiesDir,
                archiveUrl=args.archive,
                slug=args.slug,
                asDraft=bool(args.draft),
                force=bool(args.force),
                nsfwConfig=importNsfw,
            )
            result = importFromSource(source, options)
            chapterNote = f" ({result.chapterCount} chapters)" if result.chapterCount > 1 else ""
            print(f"Imported '{result.title}'{chapterNote} -> {result.storyDir}")
            for createdPath in result.createdFiles:
                print(f"  {createdPath}")

    return 0


def _runBuild(argv: t.Sequence[str]) -> int:
    """Execute the default `ficgen [build]` subcommand."""
    config = buildConfigFromArgs(argv)
    result = buildArchive(config)
    draftNote = " (including drafts)" if config.includeDrafts else ""
    print(
        f"Built {result.storyCount} stories"
        + f" ({result.chapterPageCount} chapter pages,"
        + f" {result.taxonomyPageCount} tag pages,"
        + f" {result.feedEntryCount} feed entries){draftNote}"
        + f" -> {result.outputDir}",
    )
    return 0


def _configureLogging() -> None:
    """Attach a stderr warning handler to the ficgen logger if none is configured."""
    pkgLogger = logging.getLogger("ficgen")
    if not pkgLogger.handlers:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s: %(message)s"))
        pkgLogger.addHandler(handler)
    if pkgLogger.level == logging.NOTSET:
        pkgLogger.setLevel(logging.WARNING)


def main(argv: t.Sequence[str] | None = None) -> int:
    """Entry point for the ficgen CLI."""
    _configureLogging()
    rawArgs = list(sys.argv[1:] if argv is None else argv)
    try:
        match rawArgs:
            case ["stamp", *rest]:
                return _runStamp(rest)
            case ["init", *rest]:
                return _runInit(rest)
            case ["new", *rest]:
                return _runNew(rest)
            case ["import", *rest]:
                return _runImport(rest)
            case ["build", *rest]:
                return _runBuild(rest)
            case _:
                return _runBuild(rawArgs)
    except StoryParseError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
