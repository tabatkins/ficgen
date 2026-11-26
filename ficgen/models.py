"""Data models for the fiction archive static site generator."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import t
from .kdlMeta import StoryParseError, normalizeKey

DEFAULT_STORIES_DIR_NAME: t.Final[str] = "_stories"
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

TAXONOMY_SPECS: t.Final[tuple[tuple[t.TaxonomyKind, str, str], ...]] = (
    ("fandoms", "Fandoms", "Fandom"),
    ("categories", "Categories", "Category"),
    ("relationships", "Relationships", "Relationship"),
    ("characters", "Characters", "Character"),
    ("tags", "Tags", "Tag"),
)

_SLUG_UNSAFE_RE: t.Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")


def _matchFontPreset(value: str) -> str | None:
    """Return the canonical preset name if value refers to a built-in font preset."""
    normalized = normalizeKey(value)
    if normalized.startswith("--font-"):
        normalized = normalized.removeprefix("--font-")
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
        elif ch == ")" and parenDepth > 0:
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
    cleaned = value.strip().rstrip(";").strip()
    if not cleaned:
        cleaned = defaultPreset
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

    for idx, part in enumerate(parts):
        if _isUnfailingFontEntry(part) and idx < len(parts) - 1:
            msg = (
                f"Invalid CSS font stack for {role} ({value!r}): "
                + f"generic fallback {part!r} must be at the end of the font list "
                + "because fonts listed after it can never be selected."
            )
            raise StoryParseError(msg)

    if _isUnfailingFontEntry(parts[-1]):
        return cleaned
    return f"{cleaned}, {fallbackGeneric}"


def slugify(text: str) -> str:
    """Convert a display label into a clean URL-safe ASCII slug."""
    normalized = text.strip().lower()
    slug = _SLUG_UNSAFE_RE.sub("-", normalized).strip("-")
    if slug:
        return slug
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()[:8]
    return f"item-{digest}"


def buildSlugMap(names: t.Iterable[str]) -> dict[str, str]:
    """Assign collision-free URL slugs for a collection of display names."""
    uniqueNames = sorted(set(names), key=lambda value: (value.lower(), value))
    counts: dict[str, int] = {}
    baseSlugs: dict[str, str] = {}
    for name in uniqueNames:
        base = slugify(name)
        baseSlugs[name] = base
        counts[base] = counts.get(base, 0) + 1

    usedSlugs: set[str] = set()
    slugMap: dict[str, str] = {}
    for name in uniqueNames:
        base = baseSlugs[name]
        if counts[base] == 1 and base not in usedSlugs:
            candidate = base
        else:
            digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:6]
            candidate = f"{base}-{digest}"
            suffix = 2
            while candidate in usedSlugs:
                candidate = f"{base}-{digest}-{suffix}"
                suffix += 1
        usedSlugs.add(candidate)
        slugMap[name] = candidate
    return slugMap


class TaxonomyItem(t.NamedTuple):
    """A single tag, fandom, relationship, character, or category."""

    kind: t.TaxonomyKind
    name: str
    slug: str

    @property
    def relUrl(self) -> str:
        """Relative path from the archive root to this taxonomy page."""
        return f"browse/{self.kind}/{self.slug}.html"


@dataclass(slots=True)
class SiteConfig:
    """Configuration for building the static fiction archive."""

    storiesDir: Path = Path(DEFAULT_STORIES_DIR_NAME)
    outputDir: Path = Path(".")
    title: str = "Fiction Archive"
    subtitle: str = ""
    author: str = ""
    baseUrl: str = "https://example.com/"
    includeDrafts: bool = False
    localBrowsing: bool = True
    fontSerif: str = DEFAULT_SERIF_FONT
    fontSans: str = DEFAULT_SANS_FONT
    fontMono: str = DEFAULT_MONO_FONT
    customCssPath: Path | None = None
    customCssMode: t.CssMode = "append"

    def normalizedBaseUrl(self) -> str:
        """Return baseUrl guaranteed to end with a single slash."""
        return self.baseUrl.rstrip("/") + "/"


@dataclass(slots=True)
class Chapter:
    """A single chapter within a multi-chapter story."""

    number: int
    slug: str
    title: str
    sourcePath: Path
    published: bool
    date: datetime
    hasExplicitDate: bool
    summaryHtml: str
    summaryText: str
    notesHtml: str
    endnotesHtml: str
    bodyHtml: str
    wordCount: int
    extraFandoms: t.MetadataList = ()
    extraCategories: t.MetadataList = ()
    extraRelationships: t.MetadataList = ()
    extraCharacters: t.MetadataList = ()
    extraTags: t.MetadataList = ()

    @property
    def displayTitle(self) -> str:
        """Human-readable chapter title."""
        if self.title:
            return f"Chapter {self.number}: {self.title}"
        return f"Chapter {self.number}"

    @property
    def filename(self) -> str:
        """Output HTML filename for this chapter within the story folder."""
        return f"{self.slug}.html"


@dataclass(slots=True)
class Story:
    """A single-document or multi-chapter story."""

    slug: str
    title: str
    author: str
    isMultiChapter: bool
    published: bool
    complete: bool
    plannedChapters: int | None
    date: datetime
    updated: datetime
    hasExplicitDate: bool
    rating: str
    warnings: t.MetadataList
    fandoms: t.MetadataList
    categories: t.MetadataList
    relationships: t.MetadataList
    characters: t.MetadataList
    tags: t.MetadataList
    summaryHtml: str
    summaryText: str
    notesHtml: str
    endnotesHtml: str
    bodyHtml: str
    wordCount: int
    chapters: tuple[Chapter, ...]
    sourceDir: Path

    @property
    def chapterCountDisplay(self) -> str:
        """AO3-style chapter count string (e.g. '1/1', '2/?', '3/5')."""
        if not self.isMultiChapter:
            return "1/1"
        count = len(self.chapters)
        if self.complete:
            total = self.plannedChapters if self.plannedChapters and self.plannedChapters >= count else count
            return f"{count}/{total}"
        if self.plannedChapters is not None:
            return f"{count}/{self.plannedChapters}"
        return f"{count}/?"

    @property
    def statusLabel(self) -> str:
        """Human-readable completion status."""
        if not self.published:
            return "Draft"
        if self.complete:
            return "Complete"
        return "In Progress"

    @property
    def hasDraftChapters(self) -> bool:
        """Return True if any included chapter is marked unpublished."""
        return any(not chapter.published for chapter in self.chapters)

    def taxonomyNames(self, kind: t.TaxonomyKind) -> t.MetadataList:
        """Return the tuple of display names for a given taxonomy kind."""
        match kind:
            case "fandoms":
                return self.fandoms
            case "categories":
                return self.categories
            case "relationships":
                return self.relationships
            case "characters":
                return self.characters
            case "tags":
                return self.tags


@dataclass(slots=True)
class TaxonomyBucket:
    """All published stories belonging to a specific taxonomy item."""

    kind: t.TaxonomyKind
    label: str
    singularLabel: str
    item: TaxonomyItem
    stories: tuple[Story, ...]


@dataclass(slots=True)
class FeedEntry:
    """An entry in the Atom 1.0 feed."""

    entryId: str
    title: str
    url: str
    updated: datetime
    published: datetime
    author: str
    summaryText: str
    contentHtml: str
    categories: t.MetadataList


@dataclass(slots=True)
class Archive:
    """Fully loaded archive ready for static site generation."""

    config: SiteConfig
    stories: tuple[Story, ...]
    allStorySlugs: tuple[str, ...]
    taxonomies: dict[t.TaxonomyKind, tuple[TaxonomyBucket, ...]]
    slugMaps: dict[t.TaxonomyKind, dict[str, str]]
    totalWords: int
    feedEntries: tuple[FeedEntry, ...]

    def taxonomyItems(self, story: Story, kind: t.TaxonomyKind) -> tuple[TaxonomyItem, ...]:
        """Resolve a story's taxonomy labels into TaxonomyItem objects with slugs."""
        kindSlugs = self.slugMaps[kind]
        return tuple(
            TaxonomyItem(kind=kind, name=name, slug=kindSlugs[name])
            for name in story.taxonomyNames(kind)
            if name in kindSlugs
        )
