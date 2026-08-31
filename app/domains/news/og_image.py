"""News thumbnail extraction — RSS media tags + og:image fallback.

Shared by the RSS ingest (``app.domains.news.ingest``) and the Redis news
aggregate (``app.databus.news_provider``) so every news item — regardless of
source — carries a usable thumbnail:

  1. RSS media: ``media:content`` → ``media:thumbnail`` → enclosure → first
     ``<img>`` in the summary HTML (the existing ``_extract_image`` order).
  2. Fallback: fetch the article page and pull ``og:image`` /
     ``twitter:image`` (bounded, fail-open → ``""``).

No new dependencies — stdlib regex over httpx-fetched HTML, mirroring the
bot's proven ``_fetch_og_image`` pattern.
"""

from __future__ import annotations

import re
from typing import Any

import httpx

from app.core.logging import get_logger

logger = get_logger("news_og_image")

_OG_IMAGE_TIMEOUT = 6.0
_MAX_OG_FETCHES = 40  # per ingest batch — bounded so a slow feed can't stall

_USER_AGENT = "Mozilla/5.0 (compatible; RugMunchNewsBot/1.0)"

_OG_IMAGE_RES = tuple(
    re.compile(pattern, re.IGNORECASE)
    for pattern in (
        r'<meta[^>]+property=["\']og:image(?::secure_url)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image(?::secure_url)?["\']',
        r'<meta[^>]+name=["\']twitter:image(?::src)?["\'][^>]+content=["\']([^"\']+)["\']',
        r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+name=["\']twitter:image(?::src)?["\']',
    )
)


def _acceptable(url: str) -> bool:
    """Reject widget/embed URLs — they render as broken images in cards."""
    lowered = url.lower()
    return not any(marker in lowered for marker in ("/embed", "embed?", "&embed", "/widget", "/chart/"))


def extract_entry_image(entry: Any) -> str:
    """Best image URL from a feedparser RSS entry ("" when none).

    Order: media:content → media:thumbnail → image enclosure → first
    ``<img src>`` in summary/description HTML. Mirrors
    ``app.databus.news_provider._extract_image`` so both ingest paths agree.
    """
    for media in getattr(entry, "media_content", None) or []:
        if isinstance(media, dict) and media.get("url"):
            mtype = str(media.get("type") or "image")
            url = str(media["url"])
            if _acceptable(url) and ("image" in mtype or mtype.startswith("http")):
                return url
    for thumb in getattr(entry, "media_thumbnail", None) or []:
        if isinstance(thumb, dict) and thumb.get("url") and _acceptable(str(thumb["url"])):
            return str(thumb["url"])
    for link in getattr(entry, "links", None) or []:
        if (
            isinstance(link, dict)
            and link.get("rel") == "enclosure"
            and "image" in str(link.get("type") or "")
            and _acceptable(str(link.get("href") or ""))
        ):
            return str(link["href"])
    for html_field in ("summary", "description", "content"):
        html = ""
        value = getattr(entry, html_field, None)
        if isinstance(value, str):
            html = value
        elif isinstance(value, list):
            html = " ".join(str(v.get("value", "")) for v in value if isinstance(v, dict))
        match = re.search(r'<img[^>]+src=["\']([^"\']+)["\']', html or "", re.IGNORECASE)
        if match and _acceptable(match.group(1)):
            return match.group(1)
    return ""


async def fetch_og_image(url: str, timeout: float = 6.0) -> str:
    """Best-effort og:image/twitter:image for an article URL (fail-open → "").

    Skips non-http(s) URLs; never raises. Bound to *timeout* seconds.
    """
    url = (url or "").strip()
    if not url.startswith(("http://", "https://")):
        return ""
    try:
        async with httpx.AsyncClient(follow_redirects=True, timeout=timeout) as client:
            resp = await client.get(
                url,
                headers={"User-Agent": _USER_AGENT, "Accept": "text/html"},
            )
            if resp.status_code != 200:
                return ""
            # Meta tags live in <head> — cap the scan to keep regex cheap.
            html = resp.text[:200_000]
    except Exception as exc:
        logger.debug("news_og_image:fetch_failed url=%s err=%s", url[:120], str(exc)[:80])
        return ""
    for pattern in _OG_IMAGE_RES:
        match = pattern.search(html)
        if match:
            candidate = match.group(1).strip()
            if candidate.startswith(("http://", "https://")) and _acceptable(candidate):
                return candidate
            # Protocol-relative or site-relative URLs
            if candidate.startswith("//"):
                return f"https:{candidate}"
    return ""


async def resolve_thumbnails(
    entries: list[tuple[Any, str]],
    *,
    max_fetches: int = _MAX_OG_FETCHES,
) -> list[str]:
    """Resolve a thumbnail per ``(entry, article_url)`` pair, in order.

    RSS media first; og:image fetch fallback (bounded to *max_fetches*
    network calls per batch, gathered concurrently). Never raises.
    """
    direct = [extract_entry_image(entry) for entry, _url in entries]
    missing = [i for i, (img, (_e, url)) in enumerate(zip(direct, entries, strict=True)) if not img and url]
    og_urls: dict[int, str] = {}
    if missing:
        bounded = missing[:max_fetches]
        results = await gather_og_images([entries[i][1] for i in bounded])
        og_urls = dict(zip(bounded, results, strict=True))
    return [direct[i] or og_urls.get(i, "") for i in range(len(entries))]


async def gather_og_images(urls: list[str]) -> list[str]:
    """Concurrently fetch og:images for *urls* (order-preserving, fail-open)."""
    if not urls:
        return []
    import asyncio

    return list(await asyncio.gather(*(fetch_og_image(u) for u in urls)))
