"""Telegram LEGACY Markdown escaping rules.

Two production defects this pins, both of which shipped once:

- Escaping the union of the legacy and MarkdownV2 sets. Only the legacy set is
  escapable; ``\\+``, ``\\=``, ``\\.`` and friends are themselves HTTP 400.
- Escaping inside a code span. Nothing is parsed inside `` ` ``, so a
  timestamp rendered as ``2026\\-09\\-27T10\\:00\\:00Z``.
"""

from __future__ import annotations

from app.domains.telegram.adminbot.tg_markdown import code, esc, unesc

LEGACY = "\\`*_[]()~"


def test_esc_escapes_exactly_the_legacy_delimiters() -> None:
    for char in LEGACY:
        assert esc(char) == f"\\{char}", char


def test_esc_leaves_non_delimiters_alone() -> None:
    """These are the 400s: an escape the legacy parser does not recognise."""
    for value in ("+", "-", "=", "|", "{", "}", ".", "!", "#", ">", "load15 > 32"):
        assert esc(value) == value, value
    assert esc("plain text 85%") == "plain text 85%"


def test_unesc_reverses_esc() -> None:
    for value in ("a\\b", "x_y", "> quoted", "(>= 80) %", "a*b_c`d"):
        assert unesc(esc(value)) == value


def test_code_span_content_is_not_escaped() -> None:
    """A backslash inside a code span is shown to the operator verbatim."""
    started = "2026-09-27T10:00:00+00:00"
    assert code(started) == f"`{started}`"
    assert "\\" not in code(started)


def test_code_drops_the_span_when_the_value_has_a_backtick() -> None:
    """A literal backtick would close the span early."""
    assert code("weird`value") == "weird`value"
