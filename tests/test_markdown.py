"""Tests for Markdown extensions: chat bubbles, smart typography, auto-links, and word counting."""

from __future__ import annotations

from ficgen.markdownExt import MarkdownRenderer, countWords, makeExcerpt, stripHtmlTags


def test_chat_bubbles_grouped_across_blank_lines() -> None:
    """Consecutive chat lines separated by blank lines group into one chat container."""
    renderer = MarkdownRenderer()
    source = (
        "Before the texts.\n\n"
        + "\\<Hey, just leaving the station now. Gonna swing by the bakery first.|\n\n"
        + "|Sounds good. I've already put the kettle on.>\n\n"
        + "\\<Gotcha. Shouldn't be too long.|\n\n"
        + "After the texts."
    )
    html = renderer.render(source)
    assert html.count('<div class="chat"') == 1
    assert (
        '<p class="chat-msg chat-left">Hey, just leaving the station now. Gonna swing by the bakery first.</p>' in html
    )
    assert '<p class="chat-msg chat-right">Sounds good. I\u2019ve already put the kettle on.</p>' in html
    assert '<p class="chat-msg chat-left">Gotcha. Shouldn\u2019t be too long.</p>' in html
    assert "<p>Before the texts.</p>" in html
    assert "<p>After the texts.</p>" in html


def test_chat_bubbles_unescaped_and_single_message() -> None:
    """Unescaped chat syntax supports inline formatting and emoji."""
    renderer = MarkdownRenderer()
    source = "<First message _with emphasis_|\n|Did you make it home safe? Let me know. \u2728>"
    html = renderer.render(source)
    assert html.count('<div class="chat"') == 1
    assert '<p class="chat-msg chat-left">First message <em>with emphasis</em></p>' in html
    assert '<p class="chat-msg chat-right">Did you make it home safe? Let me know. \u2728</p>' in html


def test_chat_syntax_inside_code_fence_is_ignored() -> None:
    """Fenced code blocks are not parsed as text-message bubbles."""
    renderer = MarkdownRenderer()
    source = "```\n<not a chat|\n|also not a chat>\n```"
    html = renderer.render(source)
    assert 'class="chat"' not in html
    assert "<pre><code>" in html


def test_smart_typography_quotes_dashes_and_ellipses() -> None:
    """Quotes, apostrophes, prose em-dashes, numeric en-dashes, and ellipses render cleanly."""
    renderer = MarkdownRenderer()
    source = (
        '"During all the quiet hours--"\n\n'
        + '"...We never finished that song", she said.\n\n'
        + '"\u2026Yes." And 1990--1995 was a range.\n\n'
        + '"I, uh" _didn\'t mean to_ "drop the keys"'
    )
    html = renderer.render(source)
    assert "\u201cDuring all the quiet hours\u2014\u201d" in html
    assert "\u201c\u2026We never finished that song\u201d, she said." in html
    assert "\u201c\u2026Yes.\u201d And 1990\u20131995 was a range." in html
    assert "\u201cI, uh\u201d <em>didn\u2019t mean to</em> \u201cdrop the keys\u201d" in html


def test_bare_urls_are_autolinked() -> None:
    """Bare HTTP(S) URLs are automatically converted to hyperlinks."""
    renderer = MarkdownRenderer()
    html = renderer.render("Playlist: https://example.com/playlist")
    assert '<a href="https://example.com/playlist">https://example.com/playlist</a>' in html


def test_word_count_and_excerpt() -> None:
    """Word counting ignores standalone punctuation tokens and excerpts truncate cleanly."""
    html = "<p>Bright \u2014. Even the \u2014 is quiet now.</p><p>Don\u2019t stop!</p>"
    assert stripHtmlTags(html) == "Bright \u2014. Even the \u2014 is quiet now. Don\u2019t stop!"
    assert countWords(html) == 8
    excerpt = makeExcerpt("<p>" + "word " * 60 + "</p>", maxChars=30)
    assert excerpt.endswith("\u2026")
    assert len(excerpt) <= 31
