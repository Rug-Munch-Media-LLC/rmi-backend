"""Escape free-form alert text for Telegram's LEGACY Markdown parse mode.

``sendMessage`` with ``parse_mode="Markdown"`` treats ``* _ [ ] ( ) ~`` and a
backtick as entity delimiters. One unbalanced one makes Telegram reject the
whole message with HTTP 400, and the alert is never delivered — observed 4x in
24h on production, e.g. ``ValkeyHeadroom: rmi-valkey``.

Two things this module exists to get right, both learned the hard way:

1. **Only the legacy set is escapable.** Escaping ``+ - = | { } . ! #`` looks
   safer but is *invalid* in legacy Markdown and is itself a 400. A union of
   the legacy and MarkdownV2 sets is not a superset here — it is a bug.
2. **Code-span content must not be escaped.** Inside `` ` `` nothing is
   parsed, so a backslash renders literally. That is how a timestamp arrived
   on screen as ``2026\\-09\\-27T10\\:00\\:00Z``.
"""

from __future__ import annotations

# Exactly what Telegram's legacy Markdown parser accepts as ``\x``. Nothing
# else: an escape it does not recognise is a 400, so adding "harmless looking"
# characters here makes delivery worse, not safer.
_SPECIALS = frozenset("\\`*_[]()~")

__all__ = ["code", "esc", "unesc"]


def esc(value: object) -> str:
    """Return ``value`` with every legacy-Markdown delimiter escaped."""
    text = str(value)
    return "".join(f"\\{char}" if char in _SPECIALS else char for char in text)


def unesc(value: str) -> str:
    """Strip escapes added by :func:`esc`, for the plain-text fallback.

    The Markdown attempt and the plain-text retry must not send the same bytes:
    a fallback that keeps the backslashes shows the operator ``\\(15m\\)``
    instead of ``(15m)``, which is a confusing way to learn an alert arrived.
    """
    out: list[str] = []
    skip = False
    for char in value:
        if skip:
            out.append(char)
            skip = False
        elif char == "\\":
            skip = True
        else:
            out.append(char)
    return "".join(out)


def code(value: object) -> str:
    """Wrap ``value`` in a code span, unescaped, as its content requires.

    A literal backtick inside the value would close the span early, so the
    span is dropped for those values rather than producing broken markup.
    """
    text = str(value)
    if "`" in text:
        return text
    return f"`{text}`"
