"""Static site generator for a personal fiction archive."""

from __future__ import annotations

from .cli import buildArchive as buildArchive, main as main
from .generator import BuildResult as BuildResult, generateSite as generateSite
from .importer import (
    ImportOptions as ImportOptions,
    ImportResult as ImportResult,
    htmlToMarkdown as htmlToMarkdown,
    importFromSource as importFromSource,
    writeImportedWork as writeImportedWork,
)
from .kdlMeta import (
    StoryParseError as StoryParseError,
    parseKdlDocument as parseKdlDocument,
)
from .markdownExt import MarkdownRenderer as MarkdownRenderer
from .models import (
    Archive as Archive,
    Chapter as Chapter,
    FeedEntry as FeedEntry,
    SiteConfig as SiteConfig,
    Story as Story,
    TaxonomyBucket as TaxonomyBucket,
    TaxonomyItem as TaxonomyItem,
    slugify as slugify,
)
from .parser import (
    loadArchive as loadArchive,
    parseMarkdownDocument as parseMarkdownDocument,
)
from .scaffolder import (
    InitOptions as InitOptions,
    InitResult as InitResult,
    initStory as initStory,
)
from .stamper import (
    StampEntry as StampEntry,
    StampResult as StampResult,
    stampDocumentText as stampDocumentText,
    stampFile as stampFile,
    stampStories as stampStories,
)

__all__ = [
    "Archive",
    "BuildResult",
    "Chapter",
    "FeedEntry",
    "ImportOptions",
    "ImportResult",
    "InitOptions",
    "InitResult",
    "MarkdownRenderer",
    "SiteConfig",
    "StampEntry",
    "StampResult",
    "Story",
    "StoryParseError",
    "TaxonomyBucket",
    "TaxonomyItem",
    "buildArchive",
    "generateSite",
    "htmlToMarkdown",
    "importFromSource",
    "initStory",
    "loadArchive",
    "main",
    "parseKdlDocument",
    "parseMarkdownDocument",
    "slugify",
    "stampDocumentText",
    "stampFile",
    "stampStories",
    "writeImportedWork",
]
