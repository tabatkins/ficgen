"""Site configuration loading, `site.kdl` parsing, and archive path resolution."""

from __future__ import annotations

from pathlib import Path

import kdl

from . import t
from .css import (
    DEFAULT_CUSTOM_CSS_NAME,
    DEFAULT_MONO_FONT,
    DEFAULT_SANS_FONT,
    DEFAULT_SERIF_FONT,
    formatFontArgs,
    resolveFontStack,
)
from .kdlMeta import (
    StoryParseError,
    coerceStringValue,
    getBoolField,
    getNodeStringArgs,
    getStringField,
    isKdlNull,
    normalizeKey,
    parseKdlDocument,
    readUtf8File,
)
from .models import DEFAULT_STORIES_DIR_NAME, SiteConfig

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
_CSS_PATH_KEYS: t.Final[frozenset[str]] = frozenset({"css", "custom-css", "stylesheet"})
_CSS_MODE_KEYS: t.Final[frozenset[str]] = frozenset({"css-mode", "custom-css-mode", "stylesheet-mode"})


def _parseCssMode(rawMode: str, sourceDesc: str) -> t.CssMode:
    """Validate a KDL CSS mode string ('append' or 'replace')."""
    lowered = rawMode.strip().lower()
    if lowered == "append":
        return "append"
    if lowered == "replace":
        return "replace"
    msg = f"Invalid CSS mode {rawMode!r} in {sourceDesc} (expected 'append' or 'replace')."
    raise StoryParseError(msg)


def _isKdlFalseOrNull(val: t.Any) -> bool:
    """Return True if a KDL argument explicitly disables a setting (null or false)."""
    return isKdlNull(val) or (isinstance(val, bool) and not val) or (isinstance(val, kdl.Bool) and not val.value)


def _extractCustomCssConfig(
    configDoc: kdl.Document,
    configPath: Path | None,
    defaultCssDir: Path,
    sourceDesc: str,
) -> tuple[Path | None, t.CssMode]:
    """Extract optional custom CSS file path and append/replace mode from site.kdl or site.css."""
    explicitPathStr: str | None = None
    explicitDisabled = False
    cssMode: t.CssMode = "append"

    for node in configDoc.nodes:
        key = normalizeKey(node.name)
        if key in _CSS_PATH_KEYS:
            if node.args:
                first = node.args[0]
                if _isKdlFalseOrNull(first):
                    explicitPathStr = None
                    explicitDisabled = True
                else:
                    val = coerceStringValue(first, node.name, sourceDesc)
                    explicitPathStr = val or None
                    explicitDisabled = not val
            for propName, propVal in node.props.items():
                if normalizeKey(propName) in {"mode", "css-mode"}:
                    rawMode = coerceStringValue(propVal, propName, sourceDesc)
                    if rawMode:
                        cssMode = _parseCssMode(rawMode, sourceDesc)
        elif key in _CSS_MODE_KEYS and node.args:
            rawMode = coerceStringValue(node.args[0], node.name, sourceDesc)
            if rawMode:
                cssMode = _parseCssMode(rawMode, sourceDesc)

    if explicitPathStr is not None:
        rawPath = Path(explicitPathStr).expanduser()
        baseDir = configPath.parent if configPath is not None else defaultCssDir
        resolvedPath = rawPath if rawPath.is_absolute() else baseDir / rawPath
        if not resolvedPath.is_file():
            msg = f"Custom CSS file does not exist: {resolvedPath}"
            raise StoryParseError(msg)
        return (resolvedPath, cssMode)

    if explicitDisabled:
        if cssMode == "replace":
            msg = f"CSS mode is 'replace' in {sourceDesc}, but custom CSS is disabled."
            raise StoryParseError(msg)
        return (None, cssMode)

    candidate = defaultCssDir / DEFAULT_CUSTOM_CSS_NAME
    if candidate.is_file():
        return (candidate, cssMode)

    if cssMode == "replace":
        msg = (
            f"CSS mode is 'replace' in {sourceDesc}, but no custom CSS file was found "
            + f"(expected {candidate} or an explicit `css` path)."
        )
        raise StoryParseError(msg)
    return (None, cssMode)


def _extractFontConfig(configDoc: kdl.Document, sourceDesc: str) -> tuple[str, str, str]:
    """Extract and eagerly validate optional serif, sans, and mono font settings from site.kdl."""
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
    # Validate eagerly so bad font stacks fail at config-load time.
    resolveFontStack(fontSerif, role="serif")
    resolveFontStack(fontSans, role="sans")
    resolveFontStack(fontMono, role="mono")
    return (fontSerif, fontSans, fontMono)


def _loadKdlConfig(configPath: Path) -> kdl.Document:
    """Read and parse an optional KDL site configuration file."""
    rawText = readUtf8File(configPath, label="config file")
    return parseKdlDocument(rawText, sourceDesc=str(configPath))


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
    return rawPath if rawPath.is_absolute() else configPath.parent / rawPath


def resolveSiteRoot() -> Path:
    """Resolve the default site root from cwd or its parent directory."""
    cwd = Path.cwd()
    if (cwd / "site.kdl").is_file() or (cwd / DEFAULT_STORIES_DIR_NAME).is_dir():
        return cwd
    parent = cwd.parent
    if (parent / "site.kdl").is_file() or (parent / DEFAULT_STORIES_DIR_NAME).is_dir():
        return parent
    return cwd


def resolveDefaultPaths() -> tuple[Path, Path]:
    """Resolve default _stories/ and output directories from cwd or parent layout."""
    siteRoot = resolveSiteRoot()
    return (siteRoot / DEFAULT_STORIES_DIR_NAME, siteRoot)


def resolveStoriesDir(cliStories: Path | None, cliConfig: Path | None) -> Path:
    """Resolve storiesDir from explicit CLI flag, site.kdl, or default _stories/."""
    if cliStories is not None:
        return cliStories
    defaultStories, defaultOutput = resolveDefaultPaths()
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


def loadSiteConfig(
    *,
    storiesDir: Path | None = None,
    outputDir: Path | None = None,
    configPath: Path | None = None,
    title: str | None = None,
    subtitle: str | None = None,
    author: str | None = None,
    baseUrl: str | None = None,
    includeDrafts: bool = False,
) -> SiteConfig:
    """Build a SiteConfig by combining optional CLI overrides with auto-discovered site.kdl."""
    defaultStories, defaultOutput = resolveDefaultPaths()
    effectiveOutput = defaultOutput if outputDir is None else outputDir
    candidateRoot = storiesDir.parent if storiesDir is not None else defaultOutput

    resolvedConfigPath = configPath
    if resolvedConfigPath is None:
        candidate = candidateRoot / "site.kdl"
        if candidate.is_file():
            resolvedConfigPath = candidate

    configDoc = kdl.Document()
    configSource = "<config>"
    configuredStories: Path | None = None
    if resolvedConfigPath is not None:
        configDoc = _loadKdlConfig(resolvedConfigPath)
        configSource = str(resolvedConfigPath)
        configuredStories = _resolveStoriesPathFromConfig(configDoc, resolvedConfigPath)

    effectiveStories = storiesDir if storiesDir is not None else (configuredStories or defaultStories)

    def _strOpt(cliVal: str | None, kdlKey: str, fallback: str) -> str:
        if cliVal is not None:
            return cliVal
        return getStringField(configDoc, kdlKey, sourceDesc=configSource) or fallback

    kdlDrafts = getBoolField(configDoc, "include-drafts", default=False, sourceDesc=configSource)
    localBrowsing = getBoolField(configDoc, "local-browsing", default=True, sourceDesc=configSource)
    fontSerif, fontSans, fontMono = _extractFontConfig(configDoc, configSource)
    defaultCssDir = resolvedConfigPath.parent if resolvedConfigPath is not None else candidateRoot
    customCssPath, customCssMode = _extractCustomCssConfig(configDoc, resolvedConfigPath, defaultCssDir, configSource)

    return SiteConfig(
        storiesDir=effectiveStories,
        outputDir=effectiveOutput,
        title=_strOpt(title, "title", "Fiction Archive"),
        subtitle=_strOpt(subtitle, "subtitle", ""),
        author=_strOpt(author, "author", ""),
        baseUrl=_strOpt(baseUrl, "base-url", "https://example.com/"),
        includeDrafts=includeDrafts or kdlDrafts,
        localBrowsing=localBrowsing,
        fontSerif=fontSerif,
        fontSans=fontSans,
        fontMono=fontMono,
        customCssPath=customCssPath,
        customCssMode=customCssMode,
    )
