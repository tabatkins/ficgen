"""Markdown rendering with chat bubbles, auto-linking, smart typography, and word counting."""

from __future__ import annotations

import re
from html.parser import HTMLParser

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.rules_core import StateCore

from . import t

_CHAT_LEFT_RE: t.Final[re.Pattern[str]] = re.compile(r"^\\?<(.+)\|$")
_CHAT_RIGHT_RE: t.Final[re.Pattern[str]] = re.compile(r"^\\?\|(.+)>$")
_EM_DASH_RE: t.Final[re.Pattern[str]] = re.compile(r"(?<!-)(?<!\d)--(?!-)(?!\d)")
_ELLIPSIS_OPEN_QUOTE_RE: t.Final[re.Pattern[str]] = re.compile(r"(^|[\s(\[{\u2014\u2013])\u201d(?=\u2026)")
_WORD_CHAR_RE: t.Final[re.Pattern[str]] = re.compile(r"\w")
_WHITESPACE_RE: t.Final[re.Pattern[str]] = re.compile(r"\s+")


def _parseChatLine(lineText: str) -> tuple[t.ChatSide, str] | None:
    """Parse a single line of text-message bubble syntax, if it matches."""
    stripped = lineText.strip()
    if not stripped:
        return None
    leftMatch = _CHAT_LEFT_RE.match(stripped)
    if leftMatch is not None:
        inner = leftMatch.group(1).strip()
        if inner:
            return ("left", inner)
    rightMatch = _CHAT_RIGHT_RE.match(stripped)
    if rightMatch is not None:
        inner = rightMatch.group(1).strip()
        if inner:
            return ("right", inner)
    return None


def _lineContent(state: StateBlock, line: int) -> str:
    """Extract the raw text of a line from a markdown-it block state."""
    pos = state.bMarks[line] + state.tShift[line]
    maximum = state.eMarks[line]
    return state.src[pos:maximum]


def _chatBlockRule(state: StateBlock, startLine: int, endLine: int, silent: bool) -> bool:
    """Markdown-it block rule grouping consecutive text-message lines into a chat block."""
    if state.sCount[startLine] - state.blkIndent >= 4:
        return False

    first = _parseChatLine(_lineContent(state, startLine))
    if first is None:
        return False

    if silent:
        return True

    messages: list[tuple[t.ChatSide, str, int]] = [(first[0], first[1], startLine)]
    nextLine = startLine + 1

    while nextLine < endLine:
        scanLine = nextLine
        while scanLine < endLine and state.isEmpty(scanLine):
            scanLine += 1
        if scanLine >= endLine:
            break
        if state.sCount[scanLine] - state.blkIndent >= 4:
            break
        parsed = _parseChatLine(_lineContent(state, scanLine))
        if parsed is None:
            break
        messages.append((parsed[0], parsed[1], scanLine))
        nextLine = scanLine + 1

    openToken = state.push("chat_open", "div", 1)
    openToken.attrSet("class", "chat")
    openToken.attrSet("role", "region")
    openToken.attrSet("aria-label", "Text messages")
    openToken.map = [startLine, nextLine]

    for side, content, lineNum in messages:
        msgOpen = state.push("chat_msg_open", "p", 1)
        msgOpen.attrSet("class", f"chat-msg chat-{side}")
        msgOpen.map = [lineNum, lineNum + 1]

        inlineToken = state.push("inline", "", 0)
        inlineToken.content = content
        inlineToken.map = [lineNum, lineNum + 1]
        inlineToken.children = []

        state.push("chat_msg_close", "p", -1)

    state.push("chat_close", "div", -1)
    state.line = nextLine
    return True


def _emDashCoreRule(state: StateCore) -> None:
    """Convert prose double-hyphens (--) into em-dashes before markdown-it's en-dash rule."""
    for token in state.tokens:
        if token.type != "inline" or not token.children:
            continue
        for child in token.children:
            if child.type == "text" and "--" in child.content:
                child.content = _EM_DASH_RE.sub("—", child.content)


def _ellipsisQuoteCoreRule(state: StateCore) -> None:
    """Fix opening double quotes immediately before an ellipsis (e.g. '...We')."""
    for token in state.tokens:
        if token.type != "inline" or not token.children:
            continue
        for child in token.children:
            if child.type == "text" and "”…" in child.content:
                child.content = _ELLIPSIS_OPEN_QUOTE_RE.sub(r"\1“", child.content)


class _TextExtractor(HTMLParser):
    """HTML parser that collects plain text content while preserving word boundaries."""

    _BLOCK_TAGS: t.Final[frozenset[str]] = frozenset(
        {
            "address",
            "article",
            "aside",
            "blockquote",
            "br",
            "dd",
            "div",
            "dl",
            "dt",
            "figcaption",
            "figure",
            "footer",
            "h1",
            "h2",
            "h3",
            "h4",
            "h5",
            "h6",
            "header",
            "hr",
            "li",
            "main",
            "nav",
            "ol",
            "p",
            "pre",
            "section",
            "table",
            "td",
            "th",
            "tr",
            "ul",
        },
    )

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []

    @t.override
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self._BLOCK_TAGS:
            self._parts.append(" ")

    @t.override
    def handle_endtag(self, tag: str) -> None:
        if tag in self._BLOCK_TAGS:
            self._parts.append(" ")

    @t.override
    def handle_data(self, data: str) -> None:
        self._parts.append(data)

    def getText(self) -> str:
        """Return normalized plain text extracted from the parsed HTML."""
        return _WHITESPACE_RE.sub(" ", "".join(self._parts)).strip()


def stripHtmlTags(htmlText: str) -> str:
    """Convert an HTML fragment into normalized plain text."""
    if not htmlText:
        return ""
    extractor = _TextExtractor()
    extractor.feed(htmlText)
    extractor.close()
    return extractor.getText()


def countWords(htmlText: str) -> int:
    """Count prose words in an HTML fragment, ignoring standalone punctuation tokens."""
    plain = stripHtmlTags(htmlText)
    if not plain:
        return 0
    return sum(1 for token in plain.split() if _WORD_CHAR_RE.search(token) is not None)


def makeExcerpt(htmlText: str, maxChars: int = 240) -> str:
    """Build a concise plain-text excerpt suitable for meta descriptions and feeds."""
    plain = stripHtmlTags(htmlText)
    if len(plain) <= maxChars:
        return plain
    truncated = plain[:maxChars].rsplit(" ", 1)[0].rstrip(" ,;:")
    return truncated + "…"


class MarkdownRenderer:
    """Configured CommonMark renderer for fiction prose and author's notes."""

    __slots__ = ("_md",)

    def __init__(self) -> None:
        md = MarkdownIt(
            "commonmark",
            {
                "html": True,
                "linkify": True,
                "typographer": True,
            },
        )
        md.enable(["linkify", "replacements", "smartquotes"])
        md.block.ruler.before("html_block", "chat_bubbles", _chatBlockRule, {"alt": ["paragraph"]})
        md.core.ruler.before("replacements", "fiction_em_dash", _emDashCoreRule)
        md.core.ruler.after("smartquotes", "fiction_ellipsis_quote", _ellipsisQuoteCoreRule)
        self._md = md

    def render(self, markdownText: str) -> str:
        """Render block-level Markdown into HTML."""
        stripped = markdownText.strip()
        if not stripped:
            return ""
        return str(self._md.render(stripped)).strip()

    def renderInline(self, markdownText: str) -> str:
        """Render inline Markdown into HTML without a wrapping paragraph tag."""
        stripped = markdownText.strip()
        if not stripped:
            return ""
        return str(self._md.renderInline(stripped)).strip()
