"""Static site generator for a personal fiction archive."""

from __future__ import annotations

from .cli import buildArchive as buildArchive, main as main
from .css import FONT_PRESETS as FONT_PRESETS, resolveFontStack as resolveFontStack
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
    ImportNsfwConfig as ImportNsfwConfig,
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
    SiteInitResult as SiteInitResult,
    initSite as initSite,
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
    "FONT_PRESETS",
    "Archive",
    "BuildResult",
    "Chapter",
    "FeedEntry",
    "ImportNsfwConfig",
    "ImportOptions",
    "ImportResult",
    "InitOptions",
    "InitResult",
    "MarkdownRenderer",
    "SiteConfig",
    "SiteInitResult",
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
    "initSite",
    "initStory",
    "loadArchive",
    "main",
    "parseKdlDocument",
    "parseMarkdownDocument",
    "resolveFontStack",
    "slugify",
    "stampDocumentText",
    "stampFile",
    "stampStories",
    "writeImportedWork",
]
