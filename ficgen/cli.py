"""Command-line interface for building the fiction archive and stamping dates."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

import ao3fetch
import kdl

from . import t
from .generator import BuildResult, generateSite
from .importer import ImportOptions, importFromSource
from .kdlMeta import (
    StoryParseError,
    coerceStringValue,
    getBoolField,
    getNodeStringArgs,
    getStringField,
    normalizeKey,
    parseKdlDocument,
)
from .models import (
    DEFAULT_MONO_FONT,
    DEFAULT_SANS_FONT,
    DEFAULT_SERIF_FONT,
    DEFAULT_STORIES_DIR_NAME,
    SiteConfig,
    formatFontArgs,
    resolveFontStack,
)
from .parser import loadArchive
from .scaffolder import InitOptions, initSite, initStory
from .stamper import StampResult, defaultTodayDate, stampStories

_FONT_TOP_LEVEL_SLOTS: t.Final[dict[str, str]] = {
    "font-serif": "serif",
    "serif-font": "serif",
    "font-sans": "sans",
    "sans-font": "sans",
    "font-mono": "mono",
    "mono-font": "mono",
}
_FONT_BLOCK_SLOTS: t.Final[dict[str, str]] = {
    "serif": "serif",
    "sans": "sans",
    "mono": "mono",
    **_FONT_TOP_LEVEL_SLOTS,
}


def _extractFontConfig(configDoc: kdl.Document, sourceDesc: str) -> tuple[str, str, str]:
    """Extract optional serif, sans, and mono font settings from site.kdl."""
    slots: dict[str, str] = {"serif": "", "sans": "", "mono": ""}
    for node in configDoc.nodes:
        key = normalizeKey(node.name)
        if key in {"fonts", "font"}:
            for propName, propVal in node.props.items():
                slot = _FONT_BLOCK_SLOTS.get(normalizeKey(propName))
                if slot is not None:
                    val = coerceStringValue(propVal, propName, sourceDesc)
                    if val:
                        slots[slot] = val
            for child in node.nodes:
                slot = _FONT_BLOCK_SLOTS.get(normalizeKey(child.name))
                if slot is not None:
                    val = formatFontArgs(getNodeStringArgs(child, sourceDesc))
                    if val:
                        slots[slot] = val
        elif key in _FONT_TOP_LEVEL_SLOTS:
            val = formatFontArgs(getNodeStringArgs(node, sourceDesc))
            if val:
                slots[_FONT_TOP_LEVEL_SLOTS[key]] = val

    fontSerif = slots["serif"] or DEFAULT_SERIF_FONT
    fontSans = slots["sans"] or DEFAULT_SANS_FONT
    fontMono = slots["mono"] or DEFAULT_MONO_FONT
    resolveFontStack(fontSerif, role="serif")
    resolveFontStack(fontSans, role="sans")
    resolveFontStack(fontMono, role="mono")
    return (fontSerif, fontSans, fontMono)


def _loadKdlConfig(configPath: Path) -> kdl.Document:
    """Read an optional KDL site configuration file."""
    try:
        rawText = configPath.read_text(encoding="utf-8")
    except OSError as exc:
        msg = f"Failed to read config file {configPath}: {exc}"
        raise StoryParseError(msg) from exc
    return parseKdlDocument(rawText, sourceDesc=str(configPath))


def _resolveSiteRoot() -> Path:
    """Resolve the default site root from cwd or its parent directory."""
    cwd = Path.cwd()
    if (cwd / "site.kdl").is_file() or (cwd / DEFAULT_STORIES_DIR_NAME).is_dir():
        return cwd
    parent = cwd.parent
    if (parent / "site.kdl").is_file() or (parent / DEFAULT_STORIES_DIR_NAME).is_dir():
        return parent
    return cwd


def _resolveDefaultPaths() -> tuple[Path, Path]:
    """Resolve default _stories/ and output directories from cwd or package layout."""
    siteRoot = _resolveSiteRoot()
    return (siteRoot / DEFAULT_STORIES_DIR_NAME, siteRoot)


def _resolveStoriesPathFromConfig(configDoc: kdl.Document, configPath: Path) -> Path | None:
    """Read an optional stories directory from a KDL site config, resolved relative to configPath."""
    kdlStories = getStringField(
        configDoc,
        "stories",
        "stories-dir",
        "stories-directory",
        sourceDesc=str(configPath),
    )
    if not kdlStories:
        return None
    rawPath = Path(kdlStories).expanduser()
    if rawPath.is_absolute():
        return rawPath
    return configPath.parent / rawPath


def _resolveCliStoriesDir(cliStories: Path | None, cliConfig: Path | None) -> Path:
    """Resolve storiesDir from explicit CLI flag, site.kdl, or default _stories/."""
    if cliStories is not None:
        return cliStories
    defaultStories, defaultOutput = _resolveDefaultPaths()
    configPath = cliConfig
    if configPath is None:
        candidate = defaultOutput / "site.kdl"
        if candidate.is_file():
            configPath = candidate
    if configPath is not None:
        configDoc = _loadKdlConfig(configPath)
        configured = _resolveStoriesPathFromConfig(configDoc, configPath)
        if configured is not None:
            return configured
    return defaultStories


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
            + "  ficgen stamp [options]          Stamp date into undated story/chapter KDL metadata blocks"
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--stories",
        type=Path,
        default=None,
        help=f"Directory containing story folders (default: {defaultStories}, or site.kdl stories)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=defaultOutput,
        help=f"Output directory for generated HTML/CSS/feed (default: {defaultOutput})",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional KDL configuration file (auto-loads site.kdl if present).",
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
    defaultStories, _ = _resolveDefaultPaths()
    parser = argparse.ArgumentParser(
        prog="ficgen stamp",
        description='Stamp `date "YYYY-MM-DD"` into KDL metadata blocks of undated story and chapter files.',
    )
    parser.add_argument(
        "targets",
        nargs="*",
        type=Path,
        help="Optional file paths, story directories, or story slugs to stamp (defaults to all stories).",
    )
    parser.add_argument(
        "--stories",
        type=Path,
        default=None,
        help=f"Directory containing story folders (default: {defaultStories}, or site.kdl stories)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional KDL configuration file (auto-loads site.kdl if present).",
    )
    parser.add_argument(
        "--date",
        type=str,
        default=None,
        help="ISO-8601 date to stamp (default: today's date, YYYY-MM-DD).",
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
    defaultStories, _ = _resolveDefaultPaths()
    parser = argparse.ArgumentParser(
        prog="ficgen new",
        description="Initialize a new story directory with KDL metadata and starter Markdown files.",
    )
    parser.add_argument(
        "title",
        type=str,
        help="Title of the new story.",
    )
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
        "--date",
        type=str,
        default="",
        help="Optional ISO-8601 publication date (YYYY-MM-DD).",
    )
    parser.add_argument(
        "--fandom",
        action="append",
        default=[],
        help="Fandom tag (repeatable).",
    )
    parser.add_argument(
        "--category",
        action="append",
        default=[],
        help="Relationship category such as F/F (repeatable).",
    )
    parser.add_argument(
        "--relationship",
        action="append",
        default=[],
        help="Relationship / ship tag (repeatable).",
    )
    parser.add_argument(
        "--character",
        action="append",
        default=[],
        help="Character tag (repeatable).",
    )
    parser.add_argument(
        "--tag",
        action="append",
        default=[],
        help="Additional freeform tag (repeatable).",
    )
    parser.add_argument(
        "--stories",
        type=Path,
        default=None,
        help=f"Directory containing story folders (default: {defaultStories}, or site.kdl stories)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional KDL configuration file (auto-loads site.kdl if present).",
    )
    return parser


def _importArgParser() -> argparse.ArgumentParser:
    """Create the CLI argument parser for the `import` subcommand."""
    defaultStories, _ = _resolveDefaultPaths()
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
    parser.add_argument(
        "--stories",
        type=Path,
        default=None,
        help=f"Destination stories directory (default: {defaultStories}, or site.kdl stories)",
    )
    parser.add_argument(
        "--config",
        type=Path,
        default=None,
        help="Optional KDL configuration file (auto-loads site.kdl if present).",
    )
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
    defaultStories, defaultOutput = _resolveDefaultPaths()
    parser = _buildArgParser(defaultStories, defaultOutput)
    args = parser.parse_args(argv)

    cliStories: Path | None = args.stories
    outputDir: Path = args.output
    configPath: Path | None = args.config
    if configPath is None:
        candidateRoot = cliStories.parent if cliStories is not None else defaultOutput
        candidate = candidateRoot / "site.kdl"
        if candidate.is_file():
            configPath = candidate

    configDoc = kdl.Document()
    configSource = "<config>"
    configuredStories: Path | None = None
    if configPath is not None:
        configDoc = _loadKdlConfig(configPath)
        configSource = str(configPath)
        configuredStories = _resolveStoriesPathFromConfig(configDoc, configPath)

    storiesDir = cliStories if cliStories is not None else (configuredStories or defaultStories)

    def _strOpt(cliVal: str | None, kdlKey: str, fallback: str) -> str:
        if cliVal is not None:
            return cliVal
        kdlVal = getStringField(configDoc, kdlKey, sourceDesc=configSource)
        return kdlVal or fallback

    kdlDrafts = getBoolField(configDoc, "include-drafts", default=False, sourceDesc=configSource)
    includeDrafts = bool(args.drafts) or kdlDrafts
    localBrowsing = getBoolField(configDoc, "local-browsing", default=True, sourceDesc=configSource)
    fontSerif, fontSans, fontMono = _extractFontConfig(configDoc, configSource)

    return SiteConfig(
        storiesDir=storiesDir,
        outputDir=outputDir,
        title=_strOpt(args.title, "title", "Fiction Archive"),
        subtitle=_strOpt(args.subtitle, "subtitle", ""),
        author=_strOpt(args.author, "author", ""),
        baseUrl=_strOpt(args.base_url, "base-url", "https://example.com/"),
        includeDrafts=includeDrafts,
        localBrowsing=localBrowsing,
        fontSerif=fontSerif,
        fontSans=fontSans,
        fontMono=fontMono,
    )


def buildArchive(config: SiteConfig) -> BuildResult:
    """Load stories from disk and generate the static archive."""
    archive = loadArchive(config)
    return generateSite(archive)


def _printStampSummary(result: StampResult) -> None:
    """Print per-file actions and a concise summary for `ficgen stamp`."""
    for entry in result.entries:
        match entry.action:
            case "stamped":
                print(f"Stamped {entry.dateValue}: {entry.path}")
            case "would_stamp":
                print(f"Would stamp {entry.dateValue}: {entry.path}")
            case "skipped_existing":
                print(f"Skipped (already dated): {entry.path}")
            case "skipped_draft":
                print(f"Skipped (draft): {entry.path}")

    verb = "Would stamp" if result.dryRun else "Stamped"
    print(
        f"{verb} {result.stampedCount} file(s)"
        + f" ({result.skippedExistingCount} already dated, {result.skippedDraftCount} draft(s) skipped).",
    )


def _runStamp(argv: t.Sequence[str]) -> int:
    """Execute the `ficgen stamp` subcommand."""
    parser = _stampArgParser()
    args = parser.parse_args(argv)
    storiesDir = _resolveCliStoriesDir(args.stories, args.config)
    dateValue: str = args.date if args.date is not None else defaultTodayDate()
    result = stampStories(
        storiesDir=storiesDir,
        dateValue=dateValue,
        targets=args.targets,
        force=bool(args.force),
        includeDrafts=bool(args.drafts),
        dryRun=bool(args.dry_run),
    )
    _printStampSummary(result)
    return 0


def _runInit(argv: t.Sequence[str]) -> int:
    """Execute the `ficgen init` subcommand."""
    parser = _initArgParser()
    args = parser.parse_args(argv)
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
    parser = _newArgParser()
    args = parser.parse_args(argv)
    storiesDir = _resolveCliStoriesDir(args.stories, args.config)
    options = InitOptions(
        storiesDir=storiesDir,
        title=args.title,
        slug=args.slug,
        multiChapter=bool(args.multi),
        chapterCount=int(args.chapters),
        published=bool(args.published),
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


def _makeCookieFetcher(baseFetcher: ao3fetch.Fetcher, extraCookie: str) -> ao3fetch.Fetcher:
    """Return a fetcher that appends an extra Cookie header to every request."""

    def _wrapped(url: str, headers: t.Mapping[str, str] | None = None) -> ao3fetch.FetchResponse:
        merged = dict(headers) if headers else {}
        existing = merged.get("Cookie", "").strip()
        merged["Cookie"] = f"{existing} {extraCookie}".strip() if existing else extraCookie
        return baseFetcher(url, merged)

    return _wrapped


def _runImport(argv: t.Sequence[str]) -> int:
    """Execute the `ficgen import` subcommand."""
    parser = _importArgParser()
    args = parser.parse_args(argv)
    if args.slug and len(args.sources) > 1:
        msg = "--slug can only be used when importing a single work."
        raise StoryParseError(msg)

    storiesDir = _resolveCliStoriesDir(args.stories, args.config)
    logger = logging.getLogger("ficgen.importer")
    previousFetcher = ao3fetch.getFetcher()
    installedCustom = False
    if previousFetcher is ao3fetch.defaultFetch or args.cookie:
        base: ao3fetch.Fetcher = previousFetcher
        if args.cookie:
            base = _makeCookieFetcher(base, args.cookie)

        def _logRetry(event: ao3fetch.RetryEvent) -> None:
            statusDesc = f"HTTP {event.response.status}" if event.response is not None else str(event.error)
            logger.warning(
                "Retry %d in %.1fs after %s",
                event.attempt,
                event.delaySeconds,
                statusDesc,
            )

        ao3fetch.setFetcher(ao3fetch.withCache(ao3fetch.withRetry(base, onRetry=_logRetry)))
        installedCustom = True

    try:
        for source in args.sources:
            options = ImportOptions(
                storiesDir=storiesDir,
                archiveUrl=args.archive,
                slug=args.slug,
                asDraft=bool(args.draft),
                force=bool(args.force),
            )
            result = importFromSource(source, options)
            chapterNote = f" ({result.chapterCount} chapters)" if result.chapterCount > 1 else ""
            print(f"Imported '{result.title}'{chapterNote} -> {result.storyDir}")
            for createdPath in result.createdFiles:
                print(f"  {createdPath}")
    finally:
        if installedCustom:
            if previousFetcher is ao3fetch.defaultFetch:
                ao3fetch.resetFetcher()
            else:
                ao3fetch.setFetcher(previousFetcher)

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
        if rawArgs and rawArgs[0] == "stamp":
            return _runStamp(rawArgs[1:])
        if rawArgs and rawArgs[0] == "init":
            return _runInit(rawArgs[1:])
        if rawArgs and rawArgs[0] == "new":
            return _runNew(rawArgs[1:])
        if rawArgs and rawArgs[0] == "import":
            return _runImport(rawArgs[1:])
        buildArgs = rawArgs[1:] if rawArgs and rawArgs[0] == "build" else rawArgs
        config = buildConfigFromArgs(buildArgs)
        result = buildArchive(config)
    except StoryParseError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1

    draftNote = " (including drafts)" if config.includeDrafts else ""
    print(
        f"Built {result.storyCount} stories"
        + f" ({result.chapterPageCount} chapter pages,"
        + f" {result.taxonomyPageCount} tag pages,"
        + f" {result.feedEntryCount} feed entries){draftNote}"
        + f" -> {result.outputDir}",
    )
    return 0
