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
from .models import (
    DEFAULT_NSFW_RATINGS,
    DEFAULT_NSFW_TAGS,
    DEFAULT_NSFW_WARNINGS,
    DEFAULT_STORIES_DIR_NAME,
    ImportNsfwConfig,
    SiteConfig,
)

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
_NSFW_HIDE_MODES: t.Final[frozenset[str]] = frozenset({"hide", "hidden"})
_NSFW_SHOW_MODES: t.Final[frozenset[str]] = frozenset({"show", "shown", "normal", "visible"})
_CANONICAL_RATINGS: t.Final[dict[str, str]] = {
    "general audiences": "General Audiences",
    "general": "General Audiences",
    "g": "General Audiences",
    "teen and up audiences": "Teen And Up Audiences",
    "teen": "Teen And Up Audiences",
    "t": "Teen And Up Audiences",
    "mature": "Mature",
    "m": "Mature",
    "explicit": "Explicit",
    "e": "Explicit",
    "not rated": "Not Rated",
    "unrated": "Not Rated",
    "nr": "Not Rated",
}
_CANONICAL_WARNINGS: t.Final[dict[str, str]] = {
    "graphic depictions of violence": "Graphic Depictions Of Violence",
    "graphic violence": "Graphic Depictions Of Violence",
    "major character death": "Major Character Death",
    "no archive warnings apply": "No Archive Warnings Apply",
    "rape/non-con": "Rape/Non-Con",
    "non-con": "Rape/Non-Con",
    "underage sex": "Underage Sex",
    "underage": "Underage Sex",
    "creator chose not to use archive warnings": "Creator Chose Not To Use Archive Warnings",
    "choose not to use archive warnings": "Creator Chose Not To Use Archive Warnings",
}


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


def _asKdlBool(val: t.Any) -> bool | None:
    """Return Python bool if val is a KDL boolean, otherwise None."""
    if isinstance(val, bool):
        return val
    if isinstance(val, kdl.Bool):
        return val.value
    return None


def _parseHideNsfwMode(rawMode: str, sourceDesc: str) -> bool:
    """Validate a KDL NSFW display mode string ('hide' or 'show')."""
    lowered = rawMode.strip().lower()
    if lowered in _NSFW_HIDE_MODES:
        return True
    if lowered in _NSFW_SHOW_MODES:
        return False
    msg = f"Invalid NSFW display mode {rawMode!r} in {sourceDesc} (expected 'hide' or 'show')."
    raise StoryParseError(msg)


def _extractHideNsfwConfig(configDoc: kdl.Document, sourceDesc: str) -> bool:
    """Extract whether NSFW works/chapters/sections should be hidden behind a confirmation dialog."""
    hideNsfw = True
    for node in configDoc.nodes:
        key = normalizeKey(node.name)
        if key == "hide-nsfw":
            hideNsfw = getBoolField(kdl.Document([node]), "hide-nsfw", default=hideNsfw, sourceDesc=sourceDesc)
        elif key in {"nsfw", "nsfw-mode"}:
            if node.args:
                first = node.args[0]
                if not isKdlNull(first):
                    rawMode = coerceStringValue(first, node.name, sourceDesc)
                    if rawMode:
                        hideNsfw = _parseHideNsfwMode(rawMode, sourceDesc)
            for propName, propVal in node.props.items():
                propKey = normalizeKey(propName)
                if propKey in {"mode", "display"}:
                    rawMode = coerceStringValue(propVal, propName, sourceDesc)
                    if rawMode:
                        hideNsfw = _parseHideNsfwMode(rawMode, sourceDesc)
                elif propKey == "hide":
                    boolVal = _asKdlBool(propVal)
                    if boolVal is None:
                        msg = f"Expected boolean for property {propName!r} in {sourceDesc}, got {propVal!r}"
                        raise StoryParseError(msg)
                    hideNsfw = boolVal
            for child in node.nodes:
                childKey = normalizeKey(child.name)
                if childKey == "hide":
                    hideNsfw = getBoolField(kdl.Document([child]), "hide", default=hideNsfw, sourceDesc=sourceDesc)
                elif childKey in {"mode", "display"} and child.args:
                    rawMode = coerceStringValue(child.args[0], child.name, sourceDesc)
                    if rawMode:
                        hideNsfw = _parseHideNsfwMode(rawMode, sourceDesc)
    return hideNsfw


def _collectChildListValues(node: kdl.Node, sourceDesc: str) -> list[str]:
    """Collect string arguments and child-block strings from a KDL node."""
    values: list[str] = []
    for arg in node.args:
        cleaned = coerceStringValue(arg, node.name, sourceDesc)
        if cleaned:
            values.append(cleaned)
    for child in node.nodes:
        if child.args:
            for childArg in child.args:
                cleaned = coerceStringValue(childArg, node.name, sourceDesc)
                if cleaned:
                    values.append(cleaned)
        elif child.name != "-":
            cleaned = coerceStringValue(child.name, node.name, sourceDesc)
            if cleaned:
                values.append(cleaned)
    return values


def _parseImportCategoryList(
    node: kdl.Node,
    defaults: tuple[str, ...],
    canonicalMap: dict[str, str] | None,
    sourceDesc: str,
) -> tuple[str, ...]:
    """Parse an import-nsfw category setting (`true`, `false`/`null`/empty, or list of strings)."""
    if not node.args and not node.nodes:
        return ()
    if len(node.args) == 1 and not node.nodes:
        first = node.args[0]
        if _isKdlFalseOrNull(first):
            return ()
        boolVal = _asKdlBool(first)
        if boolVal is True:
            return defaults

    rawItems = _collectChildListValues(node, sourceDesc)
    seen: set[str] = set()
    resolved: list[str] = []
    for item in rawItems:
        lowered = item.lower()
        if canonicalMap is not None:
            canonical = canonicalMap.get(lowered)
            if canonical is None:
                valid = ", ".join(sorted(set(canonicalMap.values())))
                msg = f"Unknown AO3 {node.name} value {item!r} in {sourceDesc} (valid: {valid})."
                raise StoryParseError(msg)
            value = canonical
        else:
            value = lowered
        key = value.lower()
        if key not in seen:
            seen.add(key)
            resolved.append(value)
    return tuple(resolved)


def _applyImportNsfwChildren(
    nodes: t.Sequence[kdl.Node],
    current: ImportNsfwConfig,
    sourceDesc: str,
) -> ImportNsfwConfig:
    """Apply `ratings`, `warnings`, `tags`, and `chapters` child nodes to ImportNsfwConfig."""
    ratings = current.ratings
    warnings = current.warnings
    tags = current.tags
    detectChapters = current.detectChapters

    for child in nodes:
        childKey = normalizeKey(child.name)
        if childKey in {"ratings", "rating"}:
            ratings = _parseImportCategoryList(child, DEFAULT_NSFW_RATINGS, _CANONICAL_RATINGS, sourceDesc)
        elif childKey in {"warnings", "warning", "archive-warnings"}:
            warnings = _parseImportCategoryList(child, DEFAULT_NSFW_WARNINGS, _CANONICAL_WARNINGS, sourceDesc)
        elif childKey in {"tags", "tag", "additional-tags"}:
            tags = _parseImportCategoryList(child, DEFAULT_NSFW_TAGS, None, sourceDesc)
        elif childKey in {"chapters", "chapter", "detect-chapters"}:
            detectChapters = getBoolField(
                kdl.Document([child]),
                childKey,
                default=detectChapters,
                sourceDesc=sourceDesc,
            )

    return ImportNsfwConfig(
        ratings=ratings,
        warnings=warnings,
        tags=tags,
        detectChapters=detectChapters,
    )


def _extractImportNsfwConfig(configDoc: kdl.Document, sourceDesc: str) -> ImportNsfwConfig:
    """Extract optional `import-nsfw` settings from site.kdl."""
    config = ImportNsfwConfig()
    for node in configDoc.nodes:
        key = normalizeKey(node.name)
        if key == "import-nsfw":
            if node.args:
                first = node.args[0]
                if _isKdlFalseOrNull(first):
                    config = ImportNsfwConfig(ratings=(), warnings=(), tags=(), detectChapters=False)
                elif _asKdlBool(first) is True:
                    config = ImportNsfwConfig()
                else:
                    msg = (
                        f"Invalid value for KDL field {node.name!r} in {sourceDesc}: {first!r} (expected bool or block)"
                    )
                    raise StoryParseError(msg)
            if node.nodes:
                config = _applyImportNsfwChildren(node.nodes, config, sourceDesc)
        elif key == "nsfw" and node.nodes:
            for child in node.nodes:
                childKey = normalizeKey(child.name)
                if childKey == "import":
                    if child.args and _isKdlFalseOrNull(child.args[0]):
                        config = ImportNsfwConfig(ratings=(), warnings=(), tags=(), detectChapters=False)
                    elif child.args and _asKdlBool(child.args[0]) is True:
                        config = ImportNsfwConfig()
                    if child.nodes:
                        config = _applyImportNsfwChildren(child.nodes, config, sourceDesc)
            config = _applyImportNsfwChildren(node.nodes, config, sourceDesc)
    return config


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


def _findConfigPath(cliStories: Path | None, cliConfig: Path | None, defaultOutput: Path) -> Path | None:
    """Locate site.kdl from explicit --config or auto-discovery relative to stories/site root."""
    if cliConfig is not None:
        return cliConfig
    candidateRoot = cliStories.parent if cliStories is not None else defaultOutput
    candidate = candidateRoot / "site.kdl"
    return candidate if candidate.is_file() else None


def resolveStoriesDir(cliStories: Path | None, cliConfig: Path | None) -> Path:
    """Resolve storiesDir from explicit CLI flag, site.kdl, or default _stories/."""
    if cliStories is not None:
        return cliStories
    defaultStories, defaultOutput = resolveDefaultPaths()
    configPath = _findConfigPath(None, cliConfig, defaultOutput)
    if configPath is not None:
        configDoc = _loadKdlConfig(configPath)
        configured = _resolveStoriesPathFromConfig(configDoc, configPath)
        if configured is not None:
            return configured
    return defaultStories


def resolveImportSettings(cliStories: Path | None, cliConfig: Path | None) -> tuple[Path, ImportNsfwConfig]:
    """Resolve storiesDir and ImportNsfwConfig from CLI flags and optional site.kdl."""
    defaultStories, defaultOutput = resolveDefaultPaths()
    configPath = _findConfigPath(cliStories, cliConfig, defaultOutput)
    configuredStories: Path | None = None
    importNsfw = ImportNsfwConfig()
    if configPath is not None:
        configDoc = _loadKdlConfig(configPath)
        configuredStories = _resolveStoriesPathFromConfig(configDoc, configPath)
        importNsfw = _extractImportNsfwConfig(configDoc, str(configPath))

    storiesDir = cliStories if cliStories is not None else (configuredStories or defaultStories)
    return (storiesDir, importNsfw)


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
    resolvedConfigPath = _findConfigPath(storiesDir, configPath, defaultOutput)

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
    hideNsfw = _extractHideNsfwConfig(configDoc, configSource)
    importNsfw = _extractImportNsfwConfig(configDoc, configSource)
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
        hideNsfw=hideNsfw,
        importNsfw=importNsfw,
        fontSerif=fontSerif,
        fontSans=fontSans,
        fontMono=fontMono,
        customCssPath=customCssPath,
        customCssMode=customCssMode,
    )
