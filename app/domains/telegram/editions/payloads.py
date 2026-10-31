"""Telegram payload primitives — the caption budget and its escaping rules.

The editions contract fixes one shape per post: **title + one teaser paragraph
+ canonical site link + image + tags**, rendered as a media caption, so the
limit that matters is Telegram's 1024-character photo-caption limit
(``sendMessage`` would allow 4096, but one shape is one fewer branch to get
wrong). When the body does not fit, the teaser is cut at a sentence boundary —
title, link and tags are never truncated away.

Rendering is pure: no client, no network, no clock. Every story-supplied
string is HTML-escaped because the transport sends ``parse_mode="html"``.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from typing import Any

CAPTION_LIMIT = 1024
MAX_TAGS = 5
MIN_TEASER_CHARS = 60
MIN_TAGS_CHARS = 24

_TAG_STRIP_RE = re.compile(r"[^a-z0-9]+")
_WS_RE = re.compile(r"\s+")
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+")


@dataclass(frozen=True, slots=True)
class TelegramPayload:
    """One rendered post. ``image_url`` switches the send path to media."""

    text: str
    image_url: str | None = None
    parse_mode: str = "html"
    disable_web_page_preview: bool = True

    @property
    def chars(self) -> int:
        return len(self.text)

    @property
    def needs_media(self) -> bool:
        return bool(self.image_url)

    def as_dict(self) -> dict[str, Any]:
        return {
            "text": self.text,
            "image_url": self.image_url,
            "parse_mode": self.parse_mode,
            "disable_web_page_preview": self.disable_web_page_preview,
            "chars": self.chars,
            "needs_media": self.needs_media,
        }


def esc(value: object) -> str:
    """HTML-escape text content (quotes left intact)."""
    return html.escape(str(value), quote=False)


def esc_attr(value: object) -> str:
    """HTML-escape an attribute value (href)."""
    return html.escape(str(value), quote=True)


def normalise_tags(tags: object) -> tuple[str, ...]:
    """Lowercase, ``#``-free, deduplicated, order-preserving, max ``MAX_TAGS``."""
    if isinstance(tags, str):
        tags = tags.replace("|", ",").split(",")
    seen: list[str] = []
    for tag in tags or ():  # type: ignore[union-attr]
        cleaned = _TAG_STRIP_RE.sub("_", str(tag).strip().lower()).strip("_")
        if cleaned and cleaned not in seen:
            seen.append(cleaned)
        if len(seen) == MAX_TAGS:
            break
    return tuple(seen)


def collapse(text: object) -> str:
    """One paragraph: all whitespace runs collapsed to a single space."""
    return _WS_RE.sub(" ", str(text or "")).strip()


def fit_paragraph(text: object, budget: int) -> str:
    """Collapse *text* and cut it to *budget* at the last sentence/word edge."""
    paragraph = collapse(text)
    if budget <= 0:
        return ""
    if len(paragraph) <= budget:
        return paragraph
    window = paragraph[:budget]
    sentences = _SENTENCE_RE.split(window)
    if len(sentences) > 1:
        kept = " ".join(sentences[:-1]).strip()
        if len(kept) >= MIN_TEASER_CHARS:
            return kept
    cut = window.rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{cut}…"


__all__ = [
    "CAPTION_LIMIT",
    "MAX_TAGS",
    "MIN_TAGS_CHARS",
    "MIN_TEASER_CHARS",
    "TelegramPayload",
    "collapse",
    "esc",
    "esc_attr",
    "fit_paragraph",
    "normalise_tags",
]
