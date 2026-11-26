"""Data models for the fiction archive static site generator."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from . import t

DEFAULT_STORIES_DIR_NAME: t.Final[str] = "_stories"

TAXONOMY_SPECS: t.Final[tuple[tuple[t.TaxonomyKind, str, str], ...]] = (
    ("fandoms", "Fandoms", "Fandom"),
    ("categories", "Categories", "Category"),
    ("relationships", "Relationships", "Relationship"),
    ("characters", "Characters", "Character"),
    ("tags", "Tags", "Tag"),
)

_SLUG_UNSAFE_RE: t.Final[re.Pattern[str]] = re.compile(r"[^a-z0-9]+")


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
