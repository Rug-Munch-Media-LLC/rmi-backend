"""Escape free-form alert text for Telegram's legacy Markdown parse mode.

``sendMessage`` with ``parse_mode="Markdown"`` treats ``* _ [ ] ( ) ` ~`` and a
leading ``>`` as entity delimiters. One unbalanced one makes Telegram reject the
whole message with HTTP 400 "Bad Request", so the alert is never delivered and
the operator never learns about it. Alert text is free-form — metric names carry
``(``, ``)``, ``>``, ``/`` and ``:`` — so every interpolated value must be escaped.

Kept separate from the alerts feed so the escaping rule has one home and one
test, and so any other Markdown send path can import the same function.
"""

from __future__ import annotations

# Legacy Markdown delimiters, backslash, plus the MarkdownV2 additions that a
# later migration to parse_mode="MarkdownV2" would need. Escaping the union is
# safe under both parse modes: a backslash escape of a non-delimiter renders
# as the literal character.
_SPECIALS = r"\`*_[]()~>#+-=|{}.!"


def esc(value: object) -> str:
    """Return ``value`` with every Markdown delimiter backslash-escaped.

    The first character gets a leading backslash too, so a value starting with
    ``>`` or ``-`` cannot open a quote or a list in MarkdownV2.
    """
    text = str(value)
    return "".join(f"\\{char}" if char in _SPECIALS else char for char in text)
