# ficgen

An easy-to-use static site generator for personal fiction archives, both online and offline. (No comments or kudos, just text.) Reads Markdown story documents and KDL metadata (`kdl-py`) from a `_stories/` directory and outputs clean, literary static HTML, CSS (with light/dark mode), static taxonomy browse pages, and an Atom 1.0 feed.

See [an example archive](https://tabatkins.github.io/ficgen/) (created by `ficgen init && ficgen build`) to explore how it works! Or check the [docs/ folder](docs/) to see the full project structure, source and built artifacts alike.

> [!NOTE]
> In addition to using this to host your *own* fics (its original purpose),
> `ficgen import` makes it easy to create your own personal backup archive,
> so your favorite fics can be easily available even when AO3 is down. ^_^

## Installation

Requires Python 3.12+.

```sh
pip install ficgen
```

Or install globally as a CLI tool with [`pipx`](https://pipx.pypa.io/latest/how-to/install-pipx.html):

```sh
pipx install ficgen
```

For local development inside a virtual environment:

```sh
pip install -r requirements-dev.txt
```

---

## 1. The `ficgen` Command

`ficgen` (also runnable as `python -m ficgen`) has five subcommands: `build` (the default when no subcommand is given), `init`, `new`, `import`, and `stamp`.

### Initializing a New Archive (`ficgen init`)

Scaffolds a brand-new archive in an empty directory (defaulting to the current directory) with a starter `site.kdl` and example one-shot, multi-chapter, and NSFW stories under `_stories/`:

```sh
ficgen init                     # initialize the current empty directory
ficgen init path/to/my-archive  # create/initialize a new archive directory
```

`ficgen init` refuses to run if the target directory is not empty. Both `site.kdl` and the example stories come with sensible defaults filled in and optional metadata fields documented as commented-out KDL lines. Once initialized, run `ficgen build` to generate HTML for the examples, and `ficgen new "Your Story Title"` to start your own story.

### Building the Archive (`ficgen` / `ficgen build`)

```sh
ficgen                              # build published stories from ./_stories into .
ficgen build                        # explicit equivalent
ficgen --drafts                     # include published false stories and chapters
ficgen --stories path/to/_stories --output path/to/out
```

#### Build Options

| Flag | Default | Description |
| --- | --- | --- |
| `--stories PATH` | `site.kdl` `stories`, or `./_stories` (or `../_stories`) | Source directory containing story subdirectories. |
| `--output PATH` | `.` (or `..`) | Output directory for generated HTML, CSS, and feed. |
| `--config PATH` | `./site.kdl` (or `../site.kdl`, or `<stories>/../site.kdl`) if present | Optional KDL site configuration file. |
| `--title TEXT` | `"Fiction Archive"` | Archive title (overrides `site.kdl`). |
| `--subtitle TEXT` | `""` | Subtitle shown on the archive index page. |
| `--author TEXT` | `""` | Default author name for stories and Atom feed entries. |
| `--base-url URL` | `"https://example.com/"` | Canonical base URL used for Atom `<id>` and `<link>` elements. |
| `--drafts` | `false` | Include stories and chapters marked `published false`, with `[Draft]` badges. |

#### Site Configuration (`site.kdl`)

If `site.kdl` exists in the site root (`.` or `..`, or next to `--stories`), `ficgen` loads it automatically across all subcommands. CLI flags override values in `site.kdl`.

```kdl
title "My Fiction Archive"
subtitle "Original fiction and fanworks."
author "Alex Rivera"
base-url "https://example.com/fic/"
// stories "_stories"
// include-drafts false
// local-browsing true
// hide-nsfw true
// import-nsfw {
//     ratings "Explicit" "Mature" "Not Rated"
//     warnings "Rape/Non-Con" "Underage Sex" "Graphic Depictions Of Violence"
//     tags true
//     chapters true
// }
// font-serif "old-style"
// font-sans "neogrotesque"
// font-mono "mono-code"
// css "site.css" mode="append"
```

Relative `stories` and `css` paths (aliases: `stories-dir`, `stories-directory`; `custom-css`, `stylesheet`) are resolved relative to the directory containing `site.kdl`. When `local-browsing` is `true` (the default), each HTML page includes a tiny script that rewrites relative directory links to `index.html` only when viewed over `file://`; set `local-browsing false` to omit it.

**NSFW Display & Import Rules:** By default (`hide-nsfw true`, or `nsfw "hide"`), works, chapters, and `<nsfw>` sections marked NSFW still appear normally in archive listings (with an `NSFW` / `Contains NSFW` badge), while their content is placed behind an inline confirmation dialog offering to show it **once**, **permanently for that work/chapter**, or **permanently for the site** (persisted via cookies, with a footer `Re-hide NSFW` control to clear saved preferences). With JavaScript disabled (`<noscript>`), NSFW content is shown by default beneath a clear NSFW warning banner. Set `hide-nsfw false` (or `nsfw "show"`) to display all NSFW content normally without gating. On `ficgen import`, `import-nsfw` controls which AO3 ratings, archive warnings, additional tags, and chapter title/summary/start-note markers automatically add `nsfw true`; narrow any list, set a category or `import-nsfw false` to disable it, or specify custom tag keywords.

**Fonts:** Set `font-serif`, `font-sans`, and `font-mono` (or group them in a `fonts { serif "..."; sans "..."; mono "..." }` block) to any built-in preset name—`system-ui`, `transitional`, `old-style`, `humanist`, `geometric-humanist`, `classical-humanist`, `neogrotesque`, `mono-slab-serif`, `mono-code`, `industrial`, `rounded-sans`, `slab-serif`, `antique`, `didone`, `handwritten`—or supply a custom CSS font stack (either as a single CSS string like `"'Literata', Georgia, serif"` or as multiple KDL string arguments like `font-serif "Literata" "Georgia"`). Custom stacks without an ultimate generic fallback automatically have `serif`, `sans-serif`, or `monospace` appended; placing a generic fallback keyword before another font fails the build with an error.

**Custom CSS:** Stylesheets use explicit CSS cascade layers declared via `@layer ficgen, site, work, chapter;`. Ficgen's built-in CSS lives in `@layer ficgen`. If a `site.css` file exists in the site root (next to `site.kdl` or `_stories/`), it is automatically wrapped in `@layer site` and appended to `style.css` (with any leading `@import` rules hoisted and scoped via `layer(site)`). Set `css "path/to/custom.css"` to use a different file, `css false` to ignore `site.css`, and `mode="replace"` (or `css-mode "replace"`) to replace `@layer ficgen` with your own stylesheet.

#### Generated Output

```text
output/
├── index.html                          # Main Table of Contents listing all stories
├── style.css                           # Shared stylesheet (light/dark mode)
├── feed.xml                            # Atom 1.0 feed of published stories & chapters
├── browse/
│   ├── index.html                      # Hub listing all fandoms, categories, ships, characters, tags
│   └── <kind>/<slug>.html              # Static filtered listing for each taxonomy value
├── <single-doc-slug>/
│   └── index.html                      # Complete one-shot reading page
└── <multi-chapter-slug>/
    ├── index.html                      # Story overview, metadata, and chapter list
    ├── ch1.html, ch2.html, ...         # Individual chapter pages with prev/next/picker nav
    └── all.html                        # "Entire Work" combined view
```

Rebuilding cleans stale `.html` files inside known story folders and regenerates `browse/` from scratch, while leaving `_stories/` and non-HTML files untouched. If any published story or chapter is missing an explicit `date` metadata node, `ficgen` logs a `WARNING` to `stderr` via Python's `logging` module suggesting `ficgen stamp`.

### Creating a New Story (`ficgen new`)

Creates a new story folder under `_stories/<slug>/` with valid KDL metadata and starter Markdown files:

```sh
ficgen new "Midnight Rain"                                      # one-shot: _stories/midnight-rain/index.md (draft)
ficgen new --multi "Starlight Drift"                            # multi-chapter: story.kdl + ch1.md (draft)
ficgen new --chapters 3 "Long Arc"                              # multi-chapter: story.kdl + ch1.md..ch3.md
ficgen new "Quick Shot" --published --fandom "Original Work" --category "F/F" --tag "fluff"
```

#### New Story Options

| Flag | Default | Description |
| --- | --- | --- |
| `--slug SLUG` | slugified from title | Explicit folder/URL slug (lowercase letters, digits, hyphens). |
| `-m`, `--multi` | `false` | Create a multi-chapter layout (`story.kdl` + `ch1.md`) instead of `index.md`. |
| `--chapters N` | `1` | Number of starter chapter files (`ch1.md`..`chN.md`); implies `--multi` when `> 1`. |
| `--published` | `false` | Omit `published false` and stamp today's `date` (default starts stories as drafts). |
| `--nsfw` | `false` | Mark the starter story with `nsfw true`. |
| `--date DATE` | `""` | Explicit ISO-8601 `date` to include in starter metadata. |
| `--fandom`, `--category`, `--relationship`, `--character`, `--tag` | `""` | Repeatable metadata tags to pre-populate. |
| `--stories PATH` | `site.kdl` `stories`, or `./_stories` (or `../_stories`) | Stories directory where the new folder is created. |
| `--config PATH` | `./site.kdl` (or `../site.kdl`) if present | Optional KDL site configuration file. |

### Importing from AO3-Compatible Archives (`ficgen import`)

Imports stories directly from AO3, any AO3-compatible archive (such as [superlove](https://superlove.sayitditto.net/) or [SquidgeWorld](https://squidgeworld.org/)), or local AO3 `.epub` downloads into `_stories/<slug>/` using [`ao3fetch`](https://github.com/tabatkins/ao3fetch):

```sh
ficgen import https://archiveofourown.org/works/123456
ficgen import https://superlove.sayitditto.net/works/1269
ficgen import 123456 --archive https://archiveofourown.org
ficgen import path/to/downloaded-work.epub --draft
```

#### Import Options

| Flag | Default | Description |
| --- | --- | --- |
| `--archive URL` | `https://archiveofourown.org` | Archive base URL used for bare numeric work IDs (full URLs auto-detect their host). |
| `--slug SLUG` | slugified from title | Custom directory/URL slug (when importing a single work). |
| `--stories PATH` | `site.kdl` `stories`, or `./_stories` (or `../_stories`) | Destination stories directory. |
| `--config PATH` | `./site.kdl` (or `../site.kdl`) if present | Optional KDL site configuration file. |
| `--draft` | `false` | Mark imported story/chapters as `published false`. |
| `--force` | `false` | Overwrite an existing `_stories/<slug>/` directory. |
| `--cookie COOKIE` | `""` | Optional `Cookie` header for importing restricted/logged-in works. |

Import behavior:
- Automatically wraps network fetches in `withCache(withRetry(...))` to respect `429 Retry-After` rate limits and transient `5xx` errors.
- Converts work/chapter prose, summaries, and start/end notes from HTML into clean Markdown.
- Writes single-chapter works to `index.md` and multi-chapter works to `story.kdl` + `ch1.md`, `ch2.md`, ..., pulling per-chapter publication dates from `/works/<id>/navigate` when available.
- Automatically detects NSFW-indicating AO3 ratings (`Explicit`, `Mature`, `Not Rated`), archive warnings (`Rape/Non-Con`, `Underage Sex`, `Graphic Depictions Of Violence`), additional tags, and per-chapter NSFW markers in chapter titles/summaries/start notes, adding `nsfw true` accordingly (customizable via `import-nsfw` in `site.kdl`).

### Stamping Dates & Implied Characters (`ficgen stamp`)

Inserts `date "YYYY-MM-DD"` into undated story/chapter source files and adds any `characters` implied by `relationships` tags into story/chapter KDL metadata blocks:

```sh
ficgen stamp --dry-run                          # preview which files would be modified
ficgen stamp                                    # stamp dates and implied characters on all published stories
ficgen stamp --no-characters                    # stamp dates only (skip adding relationship-implied characters)
ficgen stamp starlight-drift --date 2026-10-12  # stamp a specific story slug, directory, or file
ficgen stamp _stories/midnight-rain/index.md --date 2026-08-03
ficgen stamp --force --date 2026-10-12          # overwrite existing date entries
ficgen stamp --drafts                           # also stamp files marked published false
```

#### Stamp Behavior

- **Targets:** With no positional arguments, scans every story folder in `--stories` (or `site.kdl`'s `stories` directory). Positional arguments accept story slugs (resolved inside the stories directory), directory paths, or individual `.md` / `.kdl` files.
- **Single-doc vs. multi-chapter:** Stamps `date` into `index.md` for single-doc stories and into chapter files (`chN.md`) for multi-chapter stories (never stamping `date` into `story.kdl`, since story `Published` and `Updated` dates derive automatically from chapter dates).
- **Implied characters (`--characters` / `--no-characters`):** Enabled by default; parses `relationships` tags split on `/` and `&` and adds any missing characters into `characters` in `index.md`, `story.kdl`, and chapter files (skipping characters already listed in `story.kdl` when stamping chapters). Pass `--no-characters` to stamp dates only.
- **Idempotent & draft-safe:** Parses KDL with `kdl-py` (so `//` comments and `/-` slashdashed nodes are ignored), skips files that already have up-to-date dates and characters (unless `--force` is set for dates), fills `date null` / empty `characters ""` nodes in place, and skips `published false` files (unless `--drafts` is set).
- **Formatting preservation:** Only modifies the KDL metadata block (or inserts one after `# Title` / at the top if absent), preserving prose, KDL comments, and line endings.

---

## 2. Story Structure & KDL Metadata

Each story lives in its own subdirectory under `_stories/<slug>/`. The directory name becomes the story's URL slug (`/<slug>/`). Directories starting with `.` or `_` are ignored.

### Single-Document Stories (One-Shots)

A folder containing `index.md` with an inline KDL metadata block (` ```metadata ` or a leading ` ```kdl ` fence):

```text
_stories/midnight-rain/
└── index.md
```

````markdown
# Midnight Rain

```metadata
fandoms "Original Work"
categories "F/F"
characters "Elena" "Sora"
relationships "Elena/Sora"
tags "rain" "slice of life" "yearning"
date "2026-08-02"
```

<endnotes>
Inspiration track: https://example.com/playlist

Author's endnote here.
</endnotes>

Story prose begins here...
````

### Multi-Chapter Stories

A folder containing a whole-work KDL file (`story.kdl`) plus chapter Markdown files (`ch1.md`, `ch2.md`, ...):

```text
_stories/starlight-drift/
├── story.kdl   # Whole-work title, shared metadata, optional work-level summary/notes
├── ch1.md      # Chapter 1 metadata, optional chapter notes, prose
└── ch2.md      # Chapter 2 (can be marked published false while in progress)
```

**`story.kdl`:**

```kdl
title "Starlight Drift"
fandoms "Original Work"
categories "F/F"
characters "Nadia" "Kira"
relationships "Nadia/Kira"
tags "Sci-Fi" "Slow Burn" "Space Station"
// summary "Optional work-level summary in Markdown."
// notes "Optional work-level top notes."
// endnotes "Optional work-level endnotes."
```

**`ch1.md`:**

````markdown
# Docking Bay Nine

```metadata
chapter 1
date "2026-03-11"
```

<notes>
Welcome to chapter one!
</notes>

Chapter prose begins here...
````

Notes on multi-chapter stories:
- Whole-work metadata lives in `story.kdl` (also accepts `meta.kdl` or `index.kdl` if `story.kdl` isn't present).
- Chapter headings (`# ...`) are optional; if omitted (or identical to the story title), the chapter is titled `Chapter N`. Otherwise it displays as `Chapter N: <Chapter Title>`.
- Chapter order uses the `chapter` integer first, falling back to natural numeric filename order (`ch2.md` before `ch10.md`). Duplicate chapter numbers raise an error.
- Any `fandoms`, `categories`, `relationships`, `characters`, or `tags` placed on an included chapter are merged (deduplicated) into the story's metadata.

### KDL Metadata Reference

All metadata uses KDL syntax parsed with `kdl-py`. Node names are matched case-insensitively with spaces/underscores normalized to hyphens (so `planned-chapters`, `planned_chapters`, and `"Planned Chapters"` all match).

List fields accept multiple positional arguments, repeated nodes, or child blocks:

```kdl
// Positional arguments
tags "Slow Burn" "Space Station"

// Repeated nodes
tag "Slow Burn"
tag "Space Station"

// Child block
tags {
    - "Slow Burn"
    - "Space Station"
}
```

| Node | Aliases | KDL Value / Default | Description |
| --- | --- | --- | --- |
| `title` | *(or leading `# Heading`)* | string | Overrides `# Heading` (or humanized folder slug). Required in `story.kdl` if different from slug. |
| `author` | — | string (default: `site.kdl` `author`) | Per-story author override. |
| `fandoms` | `fandom` | list of strings | Fandom tags (indexed under `browse/fandoms/`). |
| `categories` | `relationship-categories`, `category` | list of strings | e.g. `"F/F"`, `"M/M"`, `"F/M"`, `"Gen"`, `"Multi"`, `"Other"` (`browse/categories/`). |
| `relationships` | `relationship`, `ships` | list of strings | Ship tags (`browse/relationships/`). Tags split on `/` or `&` (e.g. `"A/B"`, `"A & B"`, `"A/B/C"`) automatically imply `characters` tags for each character. |
| `characters` | `character` | list of strings | Character tags (`browse/characters/`). |
| `tags` | `tag`, `additional-tags` | list of strings | Freeform tags (`browse/tags/`). |
| `rating` | — | string | Optional rating label shown in the story metadata block. |
| `warnings` | `archive-warnings` | list of strings | Highlighted warning pills shown in cards and headers. |
| `summary` | *(or `<summary>...</summary>`)* | Markdown string | Story or chapter summary (shown on index cards, TOC, and story headers). |
| `notes` | `startnotes`, `beginnotes` *(or `<notes>`)* | Markdown string | Work-level or chapter-level top notes. |
| `endnotes` | *(or `<endnotes>`)* | Markdown string | Work-level or chapter-level end notes. |
| `date` | `published-date` | `"YYYY-MM-DD"`, `(date)"YYYY-MM-DD"`, or `(date-time)"..."` | Publication date. Multi-chapter stories default to the earliest explicit chapter `date`. Falls back to file `mtime` for Atom feeds only (HTML hides dates unless explicitly set). |
| `updated` | `updated-date` | ISO-8601 string or `(date)` / `(date-time)` | Last updated date. Defaults to `date` for one-shots, or latest chapter `date` for multi-chapter works. Shown in HTML only when different from `Published`. |
| `published` | — | boolean (`true` / `false`, default `true`) | Set `false` to mark a story or chapter as a draft. |
| `nsfw` | — | boolean (`true` / `false`, default `false`) or reason string | Mark a whole work (`index.md`/`story.kdl`) or single chapter (`chN.md`) as NSFW, optionally with a reason (e.g. `nsfw "Graphic violence"` or `nsfw true reason="..."`). |
| `complete` | `status` | boolean (`true` / `false`) or `"Complete"` / `"In Progress"` | Defaults to `true` for one-shots and `false` for multi-chapter stories. |
| `planned-chapters` | `total-chapters` | integer ≥ 1 | Sets the denominator in chapter counts (e.g. `2/5` instead of `2/?`). |
| `chapter` | — | integer ≥ 1 | Explicit chapter number for chapter files (inferred from `chN.md` if omitted). |

---

## 3. Special Features

### Text-Message Chat Bubbles

Lines wrapped in `\<...|` (or unescaped `<...|`) and `|...>` render as styled SMS/chat bubbles:

```markdown
\<Hey, on my way back from the station now.|

|Sounds good! Dinner is almost ready.>

\<Be there in ten.|
```

- Consecutive chat lines—even when separated by blank lines—are grouped into a single `<div class="chat" role="region" aria-label="Text messages">` container.
- `\<...|` / `<...|` renders as `.chat-msg.chat-left` (left-aligned bubble); `|...>` renders as `.chat-msg.chat-right` (right-aligned accent bubble).
- Full inline Markdown (`_italics_`, `**bold**`, smart quotes, emoji, auto-linked URLs) works inside bubbles.
- Chat syntax inside fenced code blocks is ignored.

### Author's Notes, Summary & NSFW Section Blocks

Place `<notes>`, `<endnotes>`, `<summary>`, or `<nsfw>` tags anywhere in a story or chapter Markdown file (outside fenced code blocks), or specify `notes`, `endnotes`, or `summary` strings in `story.kdl`:

- **`<notes>...</notes>`** (aliases: `<startnotes>`, `<beginnotes>`): Rendered in a styled **Notes** box above the prose.
- **`<endnotes>...</endnotes>`**: Extracted and rendered in an **End Notes** box below the prose (`id="endnotes"`), with an automatic jump link (`See the end of the story/chapter for notes.`) placed in the header.
- **`<summary>...</summary>`**: Multi-paragraph Markdown summary (combined with any `summary` KDL node).
- **`<nsfw>...</nsfw>`** (optional `reason="..."` / `label="..."` attribute): Marks a prose section inside a work or chapter as NSFW while supporting full Markdown formatting inside. Counts toward prose word count, hides behind an inline confirmation dialog when `hide-nsfw` is enabled, and displays with an NSFW warning banner under `<noscript>`.

All four support full Markdown formatting and auto-linked URLs.

### Smart Typography & Auto-Linking

- **Curly quotes & apostrophes:** Straight `"..."` and `'...'` convert to typographic `“...”`, `‘...’`, and `’`, including opening quotes immediately preceding ellipses (`"...Wait` → `“…Wait`).
- **Dashes & ellipses:** Prose `--` and `---` convert to em-dashes (`—`), numeric ranges like `1990--1995` convert to en-dashes (`–`), and `...` converts to `…`.
- **Auto-linked URLs:** Bare `http://` and `https://` URLs are automatically turned into hyperlinks in prose and notes.
- **Scene breaks:** Standard Markdown horizontal rules (`---`, `* * *`, etc.) render as centered `✦ ✦ ✦` ornaments.

### Work Skins & Per-Chapter CSS

Style an individual story (or a single chapter of a multi-chapter story) without affecting site chrome or other works:

- **External files:** Place `work.css` inside `_stories/<slug>/` for work-wide styles, and `<chapter-stem>.css` (e.g. `ch1.css` next to `ch1.md`) for chapter-specific styles.
- **Inline CSS blocks:** Include one or more ```` ```css ... ``` ```` fenced blocks directly inside `index.md` or `chN.md`; they are stripped from story prose and appended after `work.css` / `chN.css`.
- **Automatic layering & scoping:** Work CSS is wrapped in `@layer work { @scope (article.work) { ... } }` and chapter CSS in `@layer chapter { @scope (article.work) { ... } }` (or `@scope (#ch-N)` per chapter on `all.html`), so simple selectors only target story content while cleanly overriding `@layer ficgen` and `@layer site`. Any leading `@import` rules are hoisted in order with `layer(work)` / `layer(chapter)`.

### Word Counts, Taxonomies, Atom Feed & Theming

- **Word counts:** Calculated from rendered story/chapter prose only (excluding metadata and author's notes), ignoring standalone punctuation tokens.
- **Static taxonomy browsing:** Every fandom, category, relationship, character, and tag gets a collision-safe static page under `browse/<kind>/<slug>.html` linked from every story card and metadata header.
- **Atom 1.0 feed:** `feed.xml` publishes one entry per one-shot story and one entry per published chapter of multi-chapter works, ordered newest-first, and is linked via `<link rel="alternate" type="application/atom+xml">` on every page.
- **Light & dark mode:** Styled with modern CSS `color-scheme: light dark` and `light-dark()`, following OS preference by default with a progressive-enhancement Light/Dark toggle persisted in `localStorage`.

---

## Development

```sh
pip install -r requirements-dev.txt
./lint          # runs ruff, black, mypy, pylint (10/10), check-manifest, and pytest
```

See [CONTRIBUTING.md](CONTRIBUTING.md) for coding conventions.

## Credits & License

Created by Tab Atkins-Bittner (originally for [tabatkins.com/fic](https://tabatkins.com/fic/)).
MIT License — see [LICENSE](LICENSE).
