"""Render one post: edition header or lane badge, teaser, link, tags, image.

Everything here is pure and synchronous, which is what makes the dry run a
faithful preview: the CLI and the publisher call the same two functions.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from app.domains.telegram.editions.payloads import (
    CAPTION_LIMIT,
    MIN_TEASER_CHARS,
    TelegramPayload,
    collapse,
    esc,
    esc_attr,
    fit_paragraph,
    normalise_tags,
)

if TYPE_CHECKING:
    from collections.abc import Mapping

LANE_BADGE: dict[str, str] = {
    "alerts": "🚨 ",
    "alpha": "🐋 ",
    "whales": "🐋 ",
    "news": "📰 ",
}


def canonical_link(lead: Mapping[str, Any], site_url: str) -> str | None:
    """The story's canonical URL: explicit ``url`` first, else slug on site."""
    explicit = lead.get("url") or lead.get("canonical_url") or lead.get("link")
    if explicit and str(explicit).startswith(("http://", "https://")):
        return str(explicit)
    slug = lead.get("slug") or lead.get("story_slug")
    if slug:
        return f"{site_url.rstrip('/')}/story/{slug}"
    return None


def story_title(lead: Mapping[str, Any], fallback: str = "Untitled") -> str:
    return collapse(lead.get("title") or lead.get("headline") or fallback)


def story_teaser(lead: Mapping[str, Any]) -> str:
    return collapse(lead.get("teaser") or lead.get("dek") or lead.get("summary") or "")


def lead_image(lead: Mapping[str, Any]) -> str | None:
    image = lead.get("image_url") or lead.get("image") or lead.get("og_image")
    if image and str(image).startswith(("http://", "https://")):
        return str(image)
    return None


def _link_line(lead: Mapping[str, Any], site_url: str) -> str:
    link = canonical_link(lead, site_url)
    return f'🔗 <a href="{esc_attr(link)}">{esc(link)}</a>' if link else ""


def _tags_line(lead: Mapping[str, Any]) -> str:
    return " ".join(f"#{tag}" for tag in normalise_tags(lead.get("tags")))


def _teaser_and_tags(teaser: str, link_line: str, tags_line: str, head_len: int) -> tuple[str, str]:
    """Fit the teaser into the caption budget; shed tags before the teaser."""
    reserve = head_len + 2 + (len(link_line) + 2 if link_line else 0)
    budget = CAPTION_LIMIT - reserve - (len(tags_line) + 2 if tags_line else 0) - 2
    if budget < MIN_TEASER_CHARS and tags_line:
        tags_line = ""
        budget = CAPTION_LIMIT - reserve - 2
    if budget < MIN_TEASER_CHARS:
        return "", ""
    return fit_paragraph(teaser, budget), tags_line


def _assemble(head: str, teaser: str, link_line: str, tags_line: str) -> str:
    parts = [head]
    for extra in (teaser, link_line, tags_line):
        if extra:
            parts.append(extra)
    text = "\n\n".join(parts)
    return text if len(text) <= CAPTION_LIMIT else f"{text[: CAPTION_LIMIT - 1]}…"


def _build(head: str, lead: Mapping[str, Any], site_url: str) -> TelegramPayload:
    link_line = _link_line(lead, site_url)
    teaser, tags_line = _teaser_and_tags(esc(story_teaser(lead)), link_line, _tags_line(lead), len(head))
    return TelegramPayload(text=_assemble(head, teaser, link_line, tags_line), image_url=lead_image(lead))


def edition_payload(
    *,
    title: str,
    emoji: str,
    lead: Mapping[str, Any],
    site_url: str,
    date: str | None = None,
) -> TelegramPayload:
    """Render one daily edition post (title, teaser, link, image, tags)."""
    day = date or datetime.now(UTC).strftime("%Y-%m-%d")
    prefix = f"{emoji} " if emoji else ""
    return _build(f"{prefix}<b>{esc(title)}</b> · {day}", lead, site_url)


def story_payload(*, lead: Mapping[str, Any], site_url: str, lane: str) -> TelegramPayload:
    """Render one routed story post for *lane* (same shape, no edition line)."""
    badge = LANE_BADGE.get(lane, "")
    return _build(f"{badge}<b>{esc(story_title(lead))}</b>", lead, site_url)


__all__ = [
    "LANE_BADGE",
    "canonical_link",
    "edition_payload",
    "lead_image",
    "story_payload",
    "story_teaser",
    "story_title",
]
