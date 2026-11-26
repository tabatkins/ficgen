"""CSS font stack resolution, cascade layering, @import hoisting, and @scope wrapping."""

from __future__ import annotations

import re

from . import t
from .kdlMeta import StoryParseError, normalizeKey

DEFAULT_CUSTOM_CSS_NAME: t.Final[str] = "site.css"
DEFAULT_SERIF_FONT: t.Final[str] = "old-style"
DEFAULT_SANS_FONT: t.Final[str] = "neogrotesque"
DEFAULT_MONO_FONT: t.Final[str] = "mono-code"

FONT_PRESETS: t.Final[tuple[str, ...]] = (
    "system-ui",
    "transitional",
    "old-style",
    "humanist",
    "geometric-humanist",
    "classical-humanist",
    "neogrotesque",
    "mono-slab-serif",
    "mono-code",
    "industrial",
    "rounded-sans",
    "slab-serif",
    "antique",
    "didone",
    "handwritten",
)
_FONT_PRESET_SET: t.Final[frozenset[str]] = frozenset(FONT_PRESETS)
_FONT_ROLE_DEFAULTS: t.Final[dict[t.FontRole, tuple[str, str]]] = {
    "serif": (DEFAULT_SERIF_FONT, "serif"),
    "sans": (DEFAULT_SANS_FONT, "sans-serif"),
    "mono": (DEFAULT_MONO_FONT, "monospace"),
}
_GENERIC_FALLBACK_FONTS: t.Final[frozenset[str]] = frozenset(
    {"serif", "sans-serif", "monospace", "cursive", "fantasy"},
)
_PRESET_VAR_RE: t.Final[re.Pattern[str]] = re.compile(r"^var\(\s*--font-([a-z0-9-]+)\s*\)$", re.IGNORECASE)
_SIMPLE_CSS_IDENT_RE: t.Final[re.Pattern[str]] = re.compile(r"^[A-Za-z0-9_-]+$")
_INVALID_CSS_FONT_RE: t.Final[re.Pattern[str]] = re.compile(r"[\r\n;{}]")

_FONT_VAR_PATTERNS: t.Final[dict[t.FontRole, re.Pattern[str]]] = {
    "serif": re.compile(r"(?m)^([ \t]*--font-serif:[ \t]*)([^;\n]+)(;)"),
    "sans": re.compile(r"(?m)^([ \t]*--font-sans:[ \t]*)([^;\n]+)(;)"),
    "mono": re.compile(r"(?m)^([ \t]*--font-mono:[ \t]*)([^;\n]+)(;)"),
}
_LAYER_ORDER_FULL: t.Final[str] = "@layer ficgen, site, work, chapter;"
_LAYER_ORDER_REPLACE: t.Final[str] = "@layer site, work, chapter;"
_LAYER_ORDER_LINE_RE: t.Final[re.Pattern[str]] = re.compile(r"(?m)^@layer ficgen, site, work, chapter;\n+")
_IMPORT_LAYER_NAMED_RE: t.Final[re.Pattern[str]] = re.compile(r"^layer\(\s*([^)]+?)\s*\)", re.IGNORECASE)
_IMPORT_LAYER_ANON_RE: t.Final[re.Pattern[str]] = re.compile(r"^layer(?=$|[\s;])", re.IGNORECASE)
_STYLE_CLOSE_RE: t.Final[re.Pattern[str]] = re.compile(r"(?i)</(style)(?=[\s>/])")


def _matchFontPreset(value: str) -> str | None:
    """Return the canonical preset name if value refers to a built-in font preset."""
    normalized = normalizeKey(value).removeprefix("--font-")
    if normalized in _FONT_PRESET_SET:
        return normalized
    return None


def _formatFontStackItem(item: str) -> str:
    """Format one item from a multi-argument font list into a CSS font-family entry."""
    preset = _matchFontPreset(item)
    if preset is not None:
        return f"var(--font-{preset})"
    if (
        (len(item) >= 2 and item[0] == item[-1] and item[0] in {"'", '"'})
        or "," in item
        or "(" in item
        or _SIMPLE_CSS_IDENT_RE.match(item) is not None
    ):
        return item
    if "'" not in item:
        return f"'{item}'"
    if '"' not in item:
        return f'"{item}"'
    escaped = item.replace("\\", "\\\\").replace("'", "\\'")
    return f"'{escaped}'"


def formatFontArgs(args: t.Sequence[str]) -> str:
    """Join one or more KDL font arguments into a single preset name or CSS font stack."""
    cleaned = [item.strip() for item in args if item.strip()]
    if not cleaned:
        return ""
    if len(cleaned) == 1:
        return cleaned[0]
    return ", ".join(_formatFontStackItem(item) for item in cleaned)


def _splitCssFontList(stack: str, originalValue: str) -> list[str]:
    """Split a CSS font-family list on top-level commas, respecting quotes and parentheses."""
    parts: list[str] = []
    current: list[str] = []
    quote: str | None = None
    parenDepth = 0
    escaped = False

    for ch in stack:
        if escaped:
            current.append(ch)
            escaped = False
            continue
        if quote is not None:
            current.append(ch)
            if ch == "\\":
                escaped = True
            elif ch == quote:
                quote = None
            continue
        if ch in {"'", '"'}:
            quote = ch
            current.append(ch)
        elif ch == "(":
            parenDepth += 1
            current.append(ch)
        elif ch == ")":
            if parenDepth == 0:
                msg = f"Invalid CSS font stack {originalValue!r}: unmatched ')'."
                raise StoryParseError(msg)
            parenDepth -= 1
            current.append(ch)
        elif ch == "," and parenDepth == 0:
            parts.append("".join(current).strip())
            current.clear()
        else:
            current.append(ch)

    if quote is not None:
        msg = f"Invalid CSS font stack {originalValue!r}: unterminated quote."
        raise StoryParseError(msg)
    if parenDepth != 0:
        msg = f"Invalid CSS font stack {originalValue!r}: unterminated '('."
        raise StoryParseError(msg)

    parts.append("".join(current).strip())
    return parts


def _isUnfailingFontEntry(entry: str) -> bool:
    """Return True if a CSS font-family entry is a generic fallback or built-in preset that cannot fail."""
    if entry.lower() in _GENERIC_FALLBACK_FONTS:
        return True
    presetMatch = _PRESET_VAR_RE.match(entry)
    return presetMatch is not None and presetMatch.group(1).lower() in _FONT_PRESET_SET


def resolveFontStack(value: str, *, role: t.FontRole) -> str:
    """Resolve a font preset name or custom CSS font stack into a CSS custom property value."""
    defaultPreset, fallbackGeneric = _FONT_ROLE_DEFAULTS[role]
    cleaned = value.strip().rstrip(";").strip() or defaultPreset
    if _INVALID_CSS_FONT_RE.search(cleaned) is not None:
        msg = f"Invalid CSS font stack {value!r}: must not contain newlines, ';', '{{', or '}}'."
        raise StoryParseError(msg)
    preset = _matchFontPreset(cleaned)
    if preset is not None:
        return f"var(--font-{preset})"

    parts = _splitCssFontList(cleaned, value)
    if any(not part for part in parts):
        msg = f"Invalid CSS font stack {value!r}: contains an empty font family entry."
        raise StoryParseError(msg)

    for part in parts[:-1]:
        if _isUnfailingFontEntry(part):
            msg = (
                f"Invalid CSS font stack for {role} ({value!r}): "
                + f"generic fallback {part!r} must be at the end of the font list "
                + "because fonts listed after it can never be selected."
            )
            raise StoryParseError(msg)

    if _isUnfailingFontEntry(parts[-1]):
        return cleaned
    return f"{cleaned}, {fallbackGeneric}"


def _replaceCssVar(cssText: str, pattern: re.Pattern[str], cssValue: str) -> str:
    """Replace the first matching CSS custom property value in cssText."""

    def _sub(match: re.Match[str]) -> str:
        return f"{match.group(1)}{cssValue}{match.group(3)}"

    return pattern.sub(_sub, cssText, count=1)


def applyFontConfig(
    cssText: str,
    *,
    fontSerif: str,
    fontSans: str,
    fontMono: str,
) -> str:
    """Substitute configured serif, sans, and mono font stacks into style.css."""
    roleValues: tuple[tuple[t.FontRole, str], ...] = (
        ("serif", fontSerif),
        ("sans", fontSans),
        ("mono", fontMono),
    )
    updated = cssText
    for role, rawValue in roleValues:
        resolved = resolveFontStack(rawValue, role=role)
        updated = _replaceCssVar(updated, _FONT_VAR_PATTERNS[role], resolved)
    return updated


def _isCssIdentChar(ch: str) -> bool:
    """Return True if ch can continue a CSS identifier/at-keyword."""
    return ch.isalnum() or ch in {"-", "_"}


def _skipCssQuoted(cssText: str, start: int) -> int:
    """Return index immediately after the closing quote of a CSS string starting at start."""
    quote = cssText[start]
    pos = start + 1
    length = len(cssText)
    while pos < length:
        ch = cssText[pos]
        if ch == "\\":
            pos += 2
            continue
        pos += 1
        if ch == quote:
            return pos
    return length


def _skipCssComment(cssText: str, start: int) -> int:
    """Return index immediately after the closing '*/' of a CSS comment starting at start."""
    endComment = cssText.find("*/", start + 2)
    return len(cssText) if endComment == -1 else endComment + 2


def _scanCssRuleEnd(cssText: str, start: int) -> int:
    """Find the index immediately past the terminating ';' of an @-rule, respecting quotes, comments, and parens."""
    pos = start
    length = len(cssText)
    parenDepth = 0
    while pos < length:
        ch = cssText[pos]
        if ch in {"'", '"'}:
            pos = _skipCssQuoted(cssText, pos)
            continue
        if cssText.startswith("/*", pos):
            pos = _skipCssComment(cssText, pos)
            continue
        pos += 1
        if ch == "(":
            parenDepth += 1
        elif ch == ")" and parenDepth > 0:
            parenDepth -= 1
        elif ch == ";" and parenDepth == 0:
            return pos
    return length


def _indentCss(cssText: str, spaces: int = 2) -> str:
    """Indent non-empty lines of CSS text by the given number of spaces."""
    prefix = " " * spaces
    return "\n".join(prefix + line if line.strip() else "" for line in cssText.strip().splitlines())


def _wrapLayerBlock(layerName: str, cssBody: str) -> str:
    """Wrap CSS rules inside `@layer <layerName> { ... }`."""
    return f"@layer {layerName} {{\n{_indentCss(cssBody, 2)}\n}}"


def _wrapScopedBlock(scopeSelector: str, cssBody: str) -> str:
    """Wrap CSS rules inside `@scope (<scopeSelector>) { ... }`."""
    return f"@scope ({scopeSelector}) {{\n{_indentCss(cssBody, 2)}\n}}"


def _formatLayerHeader(layerDecl: str, imports: t.Sequence[str]) -> str:
    """Combine a `@layer ...;` declaration with any hoisted `@import` rules."""
    if not imports:
        return layerDecl
    return layerDecl + "\n" + "\n".join(imports)


def _skipImportTarget(importRule: str, start: int) -> int:
    """Return the index immediately past the <url> or <string> target of an @import rule."""
    pos = start
    length = len(importRule)
    while pos < length:
        if importRule[pos].isspace():
            pos += 1
        elif importRule.startswith("/*", pos):
            pos = _skipCssComment(importRule, pos)
        else:
            break
    if pos >= length:
        return pos
    if importRule[pos] in {"'", '"'}:
        return _skipCssQuoted(importRule, pos)
    if importRule[pos : pos + 4].lower() == "url(":
        pos += 4
        while pos < length:
            ch = importRule[pos]
            if ch in {"'", '"'}:
                pos = _skipCssQuoted(importRule, pos)
                continue
            pos += 1
            if ch == ")":
                return pos
    return pos


def _scopeImportToLayer(importRule: str, layerName: str) -> str:
    """Insert or nest a `layer(...)` clause on an `@import` rule for the given layer."""
    trimmed = importRule.strip()
    if not trimmed.endswith(";"):
        trimmed += ";"
    afterTarget = _skipImportTarget(trimmed, len("@import"))
    prefix = trimmed[:afterTarget]
    rest = trimmed[afterTarget:].lstrip()

    namedMatch = _IMPORT_LAYER_NAMED_RE.match(rest)
    if namedMatch is not None:
        existingLayer = namedMatch.group(1).strip()
        tail = rest[namedMatch.end() :]
        return f"{prefix} layer({layerName}.{existingLayer}){tail}"

    anonMatch = _IMPORT_LAYER_ANON_RE.match(rest)
    if anonMatch is not None:
        tail = rest[anonMatch.end() :]
        return f"{prefix} layer({layerName}){tail}"

    if rest == ";":
        return f"{prefix} layer({layerName});"
    return f"{prefix} layer({layerName}) {rest}"


def _matchesAtKeyword(cssText: str, pos: int, keyword: str) -> bool:
    """Return True if cssText has the exact CSS at-keyword (e.g. '@import') at pos."""
    end = pos + len(keyword)
    if cssText[pos:end].lower() != keyword:
        return False
    return end == len(cssText) or not _isCssIdentChar(cssText[end])


def _extractLeadingImports(cssText: str) -> tuple[list[str], str]:
    """Hoist any leading @import rules from CSS so they remain valid before layer blocks."""
    imports: list[str] = []
    pos = 0
    lastImportEnd = 0
    length = len(cssText)

    while pos < length:
        if cssText[pos].isspace():
            pos += 1
            continue
        if cssText.startswith("/*", pos):
            endPos = _skipCssComment(cssText, pos)
            if endPos == length and not cssText.endswith("*/"):
                break
            pos = endPos
            continue
        if _matchesAtKeyword(cssText, pos, "@charset"):
            pos = _scanCssRuleEnd(cssText, pos)
            lastImportEnd = pos
            continue
        if _matchesAtKeyword(cssText, pos, "@import"):
            ruleEnd = _scanCssRuleEnd(cssText, pos)
            imports.append(cssText[pos:ruleEnd].strip())
            pos = ruleEnd
            lastImportEnd = pos
            continue
        break

    remaining = cssText[lastImportEnd:].lstrip("\r\n") if lastImportEnd > 0 else cssText
    return (imports, remaining)


def _extractLayerBlocks(
    blocks: t.Sequence[str],
    layerName: str,
) -> tuple[list[str], str]:
    """Extract layer-scoped @imports and combined CSS body from a sequence of CSS blocks."""
    scopedImports: list[str] = []
    bodies: list[str] = []
    for block in blocks:
        imports, body = _extractLeadingImports(block)
        scopedImports.extend(_scopeImportToLayer(rule, layerName) for rule in imports)
        if body.strip():
            bodies.append(body.strip())
    return (scopedImports, "\n\n".join(bodies))


def buildSiteStylesheet(
    packagedCss: str,
    *,
    fontSerif: str,
    fontSans: str,
    fontMono: str,
    customCss: str | None = None,
    customCssMode: t.CssMode = "append",
) -> str:
    """Assemble the final style.css from packaged styles, font settings, and optional custom CSS."""
    if customCss is None:
        if customCssMode == "replace":
            msg = "Custom CSS mode is 'replace', but no custom CSS file was provided."
            raise StoryParseError(msg)
        return applyFontConfig(packagedCss, fontSerif=fontSerif, fontSans=fontSans, fontMono=fontMono)

    siteImports, siteBody = _extractLayerBlocks((customCss,), "site")
    if customCssMode == "replace":
        chunks = [_formatLayerHeader(_LAYER_ORDER_REPLACE, siteImports)]
        if siteBody:
            chunks.append(_wrapLayerBlock("site", siteBody))
        return "\n\n".join(chunks) + "\n"

    baseCss = applyFontConfig(packagedCss, fontSerif=fontSerif, fontSans=fontSans, fontMono=fontMono).rstrip()
    chunks = []
    if siteImports:
        chunks.append(_formatLayerHeader(_LAYER_ORDER_FULL, siteImports))
        baseCss = _LAYER_ORDER_LINE_RE.sub("", baseCss, count=1)
    chunks.append(baseCss)
    if siteBody:
        chunks.append(_wrapLayerBlock("site", siteBody))
    return "\n\n".join(chunks) + "\n"


def buildStoryPageCss(
    workBlocks: t.Sequence[str],
    chapterScopes: t.Sequence[tuple[str, t.Sequence[str]]] = (),
    *,
    customCssMode: t.CssMode = "append",
) -> str:
    """Assemble layered, @scope-wrapped CSS for a story or chapter page, or empty string if none."""
    allImports: list[str] = []
    layerSections: list[str] = []

    workImports, workBody = _extractLayerBlocks(workBlocks, "work")
    allImports.extend(workImports)
    if workBody:
        layerSections.append(_wrapLayerBlock("work", _wrapScopedBlock("article.work", workBody)))

    chapterScopeBlocks: list[str] = []
    for scopeSelector, blocks in chapterScopes:
        chImports, chBody = _extractLayerBlocks(blocks, "chapter")
        allImports.extend(chImports)
        if chBody:
            chapterScopeBlocks.append(_wrapScopedBlock(scopeSelector, chBody))
    if chapterScopeBlocks:
        layerSections.append(_wrapLayerBlock("chapter", "\n\n".join(chapterScopeBlocks)))

    if not allImports and not layerSections:
        return ""

    layerDecl = _LAYER_ORDER_REPLACE if customCssMode == "replace" else _LAYER_ORDER_FULL
    header = _formatLayerHeader(layerDecl, allImports)
    assembled = "\n\n".join([header, *layerSections])
    return _STYLE_CLOSE_RE.sub(r"<\/\1", assembled)
